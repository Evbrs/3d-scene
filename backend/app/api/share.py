"""Partage de vue par lien permalien (`docs/spec-complete.md` §3.5, phase P8).

L'endpoint public est le point délicat : il sert des données **sans authentification**. Quatre
propriétés sont donc traitées comme faisant partie de la fonctionnalité, pas comme des options :

1. **Jeton imprévisible** — `secrets.token_urlsafe(32)`, soit 256 bits d'entropie. Un
   identifiant séquentiel rendrait tous les projets partagés énumérables.
2. **Aucune information sensible** — la réponse publique ne contient ni propriétaire, ni
   identifiants internes de projet, ni horodatage d'édition.
3. **Rien de plus que la vue partagée** — l'état ne vise qu'une pièce, c'est donc une pièce qui
   est servie. Renvoyer le graphe complet livrait à un lien « salle de bain » le plan intégral du
   logement : surfaces, revêtements, mobilier et dimensions de toutes les autres pièces.
4. **Limitation de débit** — sans elle, l'endpoint public est un amplificateur : chaque appel
   déclenche un calcul de scene graph complet. Elle passe par le **compteur partagé**
   (`app/core/rate_limit.py`) et non par un seau en mémoire de processus : la production tourne
   avec quatre workers, un compteur local y multiplie le plafond par quatre et le remet à zéro à
   chaque redémarrage. Le défaut avait été corrigé pour l'authentification et laissé en place ici,
   c'est-à-dire sur la seule route que personne n'a besoin de compte pour atteindre.
"""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import col, select

from app.api.conflicts import ConflictAwareRoute
from app.api.deps import CurrentUser, SessionDep
from app.api.permissions import get_owned_project, require_role
from app.api.scene import scene_for_project
from app.core.rate_limit import costly
from app.models.base import utcnow
from app.models.organization import OrganizationRole
from app.models.plan import Project, SharedView
from app.schemas.share import PublicSceneResponse, SharedViewCreate, SharedViewRead
from app.services.quotas import resolve_entitlement
from app.services.seed_plans import LIMIT_SHARE_LINK_DAYS

router = APIRouter(tags=["partage"], route_class=ConflictAwareRoute)

# Un jeton de 32 octets encodés en URL-safe base64 : imprévisible, et court à copier-coller.
TOKEN_BYTES = 32

# Ancienne clé d'expiration, rangée dans `state` avant que la colonne `expires_at` n'existe. Elle
# n'est plus jamais écrite : elle n'est lue que pour ne pas rouvrir un partage volontairement
# fermé sur les lignes que la migration n'aurait pas su convertir.
LEGACY_EXPIRY_KEY = "__expires_at"

# Titre servi quand le propriétaire n'a pas posé de `public_label`. Neutre par défaut : le nom
# d'un projet de rénovation porte couramment le nom et l'adresse du client (spec §3.5, « pas
# d'info sensible exposée »), et le lien se transfère.
DEFAULT_PUBLIC_LABEL = "Vue partagée"

# Durée retenue quand le palier ne déclare aucune limite de partage. A11 pose qu'une limite absente
# vaut « illimité, jamais zéro », et ce sens de défaillance est le bon pour un **plafond** — il ne
# doit jamais bloquer un client payant. Mais une durée de **conservation** absente ne vaut pas
# « éternel » : elle vaut « pas de politique », ce que le RGPD ne permet pas. Le plafond reste donc
# permissif, et c'est la valeur par défaut qui retombe ici — 30 jours, ce que `docs/rgpd.md` et la
# grille de `docs/strategie-produit.md` §4 annoncent au palier gratuit.
FALLBACK_SHARE_LINK_DAYS = 30


async def share_link_days(
    session: AsyncSession, organization_id: int, requested: int | None
) -> int:
    """Durée de vie effective d'un lien : celle du palier par défaut, et bornée par elle.

    Deux usages de la même limite, et ils ne se comportent pas pareil quand elle est absente du
    catalogue : la **valeur par défaut** retombe sur `FALLBACK_SHARE_LINK_DAYS`, le **plafond** ne
    s'applique pas. Voir le commentaire de cette constante.

    Le rabotage est silencieux, et c'est délibéré : `SharedViewRead` rend `expires_at`, donc le
    propriétaire lit la date réellement retenue. Refuser en 402 la création d'un lien parce qu'on a
    demandé 365 jours au lieu de 90 arrêterait un geste légitime pour une question de réglage.
    """
    limit = (await resolve_entitlement(session, organization_id)).limit(LIMIT_SHARE_LINK_DAYS)
    if requested is None:
        return limit if limit is not None else FALLBACK_SHARE_LINK_DAYS
    return requested if limit is None else min(requested, limit)


def _is_closed(shared: SharedView) -> bool:
    """Vrai si le lien ne doit plus rien servir.

    Une expiration **illisible** ferme l'accès. C'est le sens de la règle sur un endpoint public :
    une date qu'on ne sait pas interpréter est un doute, et un doute ne s'arbitre pas en faveur de
    l'ouverture. La traiter comme « pas d'expiration » — ce que faisait la version précédente,
    puisqu'un `isoformat` invalide levait au milieu de la lecture — revenait à publier
    indéfiniment un lien que son propriétaire croyait périmé.
    """
    if shared.revoked_at is not None:
        return True
    if shared.expires_at is not None and _as_utc(shared.expires_at) <= utcnow():
        return True

    if LEGACY_EXPIRY_KEY not in shared.state:
        return False
    legacy = shared.state[LEGACY_EXPIRY_KEY]
    if not isinstance(legacy, str):
        return True
    try:
        return _as_utc(datetime.fromisoformat(legacy)) <= utcnow()
    except ValueError:
        return True


def _as_utc(moment: datetime) -> datetime:
    """Relit un horodatage comme UTC quand il revient naïf de la base.

    SQLite ne stocke aucun fuseau : la colonne est pourtant déclarée `timezone=True`, mais la
    valeur relue est naïve et toute comparaison avec `utcnow()` lèverait. Les dates d'expiration
    sont écrites en UTC (`utcnow()`), l'hypothèse est donc exacte, pas commode.
    """
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _room_visible_in(state: dict[str, Any], scene: dict[str, Any]) -> list[dict[str, Any]]:
    """Pièces du scene graph que l'état partagé désigne réellement.

    `room_index` est le rang de la pièce dans le plan, tel que le viewer l'utilise. Un rang hors
    borne renvoie une scène vide : la pièce visée a été supprimée depuis, et « je n'ai pas trouvé
    ce que tu demandes » est la seule réponse honnête — servir les autres pièces à la place serait
    exactement la fuite qu'on ferme ici.
    """
    rooms = scene.get("rooms") or []
    index = state.get("room_index", 0)
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(rooms):
        return []
    return [rooms[index]]


@router.post(
    "/api/projects/{project_id}/shared-views",
    response_model=SharedViewRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_shared_view(
    project_id: int,
    payload: SharedViewCreate,
    session: SessionDep,
    current_user: CurrentUser,
) -> SharedView:
    """Crée un lien de partage sur un projet de son organisation.

    Réservé aux `editor` : publier un lien fait sortir la géométrie du plan hors du service, ce
    qu'un rôle de simple lecture n'a pas à pouvoir décider.

    **Le lien a toujours une échéance** (spec §10, amendement A13). Sans durée demandée, c'est
    celle du palier qui s'applique ; une durée demandée y est rabotée. Le chemin par défaut
    fabriquait jusqu'ici un lien **permanent** sur la géométrie d'un logement, alors que
    `docs/rgpd.md` annonce « jusqu'à révocation ou échéance » : une durée de conservation qu'aucun
    code n'applique n'est pas une politique, c'est une phrase.
    """
    project = await get_owned_project(
        session, project_id, current_user, OrganizationRole.EDITOR
    )

    expires_at = utcnow() + timedelta(
        days=await share_link_days(session, project.organization_id, payload.expires_in_days)
    )
    shared = SharedView(
        project_id=project_id,
        token=secrets.token_urlsafe(TOKEN_BYTES),
        state=payload.state.model_dump(mode="json"),
        expires_at=expires_at,
        label=payload.label,
        public_label=payload.public_label,
    )
    session.add(shared)
    await session.commit()
    await session.refresh(shared)
    return shared


@router.get("/api/projects/{project_id}/shared-views", response_model=list[SharedViewRead])
async def list_shared_views(
    project_id: int, session: SessionDep, current_user: CurrentUser
) -> list[SharedView]:
    await get_owned_project(session, project_id, current_user)
    return list(
        (
            await session.execute(
                select(SharedView)
                .where(col(SharedView.project_id) == project_id)
                .order_by(col(SharedView.created_at).desc())
            )
        )
        .scalars()
        .all()
    )


@router.delete("/api/shared-views/{shared_view_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_shared_view(
    shared_view_id: int, session: SessionDep, current_user: CurrentUser
) -> Response:
    """Révoque un lien. Un partage qu'on ne peut pas retirer n'est pas un partage maîtrisé.

    La ligne est **conservée**, `revoked_at` renseigné : elle garde la trace du partage et empêche
    la réattribution du jeton, là où un `DELETE` effaçait aussi la preuve qu'il avait existé.
    Rejouer la révocation ne déplace pas la date : c'est la première qui fait foi.
    """
    shared = (
        await session.execute(
            select(SharedView)
            .where(col(SharedView.id) == shared_view_id)
            .options(selectinload(SharedView.project))  # type: ignore[arg-type]
        )
    ).scalar_one_or_none()

    # 404 et non 403 sur le partage d'une autre organisation : même règle que le reste de
    # l'API. `owner_id` ne décide plus rien — un lien ouvert par un collègue doit pouvoir être
    # refermé par n'importe quel éditeur de la même organisation.
    if shared is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Partage introuvable")
    await require_role(session, shared.project, current_user, OrganizationRole.EDITOR)

    if shared.revoked_at is None:
        shared.revoked_at = utcnow()
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/api/public/views/{token}",
    response_model=PublicSceneResponse,
    dependencies=[Depends(costly("public_view"))],
)
async def read_public_view(
    session: SessionDep,
    token: Annotated[str, Path(min_length=16, max_length=64)],
) -> PublicSceneResponse:
    """Lecture publique d'une vue partagée. **Aucune authentification.**

    Le message d'erreur est le même pour un jeton inexistant, révoqué ou expiré : les distinguer
    permettrait de confirmer qu'un lien a existé.

    Le plafond de débit est une dépendance et non un test en tête de corps : posé au niveau de la
    route, il est évalué avant que quoi que ce soit ne soit lu en base, et il répond avec
    `Retry-After` — ce que la vérification manuelle qu'il remplace ne faisait pas.
    """
    not_found = HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="Vue partagée introuvable"
    )

    shared = (
        await session.execute(select(SharedView).where(col(SharedView.token) == token))
    ).scalar_one_or_none()
    if shared is None or _is_closed(shared):
        raise not_found

    project = (
        await session.execute(select(Project).where(col(Project.id) == shared.project_id))
    ).scalar_one_or_none()
    if project is None:
        raise not_found

    # Même point d'entrée que la lecture authentifiée : sans ça, le propriétaire et le visiteur
    # public voient deux géométries différentes du même projet, et le calcul public — non
    # authentifié — n'est jamais amorti.
    scene, _from_cache = await scene_for_project(session, shared.project_id, project.version)

    public_state = {
        key: value for key, value in shared.state.items() if not key.startswith("__")
    }
    # Copie et non modification sur place : `scene` peut être l'objet que le cache vient de
    # renvoyer, et le filtrer en place servirait ensuite une seule pièce au propriétaire.
    # L'identifiant interne du projet n'a par ailleurs rien à faire dans une réponse publique.
    public_scene = {
        key: value for key, value in scene.items() if key != "project_id"
    } | {"rooms": _room_visible_in(shared.state, scene)}

    return PublicSceneResponse(
        # `project` reste chargé pour sa version (clé du cache) : c'est bien le nom qu'on refuse
        # de publier, pas le projet qu'on renonce à lire.
        project_name=shared.public_label or DEFAULT_PUBLIC_LABEL,
        public_label=shared.public_label,
        state=public_state,
        scene=public_scene,
    )
