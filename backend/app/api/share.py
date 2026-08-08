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
   déclenche un calcul de scene graph complet.
"""

import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, Request, Response, status
from sqlalchemy.orm import selectinload
from sqlmodel import col, select

from app.api.conflicts import ConflictAwareRoute
from app.api.deps import CurrentUser, SessionDep
from app.api.permissions import get_owned_project, require_role
from app.api.scene import scene_for_project
from app.core.rate_limit import SlidingWindowRateLimiter
from app.models.base import utcnow
from app.models.organization import OrganizationRole
from app.models.plan import Project, SharedView
from app.schemas.share import PublicSceneResponse, SharedViewCreate, SharedViewRead

router = APIRouter(tags=["partage"], route_class=ConflictAwareRoute)

# Un jeton de 32 octets encodés en URL-safe base64 : imprévisible, et court à copier-coller.
TOKEN_BYTES = 32

# Débit du lecteur public. Généreux pour un usage normal (rechargements, plusieurs onglets),
# assez bas pour rendre l'énumération de jetons et l'abus de calcul inintéressants.
public_rate_limiter = SlidingWindowRateLimiter(max_attempts=60, window_seconds=60)

# Ancienne clé d'expiration, rangée dans `state` avant que la colonne `expires_at` n'existe. Elle
# n'est plus jamais écrite : elle n'est lue que pour ne pas rouvrir un partage volontairement
# fermé sur les lignes que la migration n'aurait pas su convertir.
LEGACY_EXPIRY_KEY = "__expires_at"

# Titre servi quand le propriétaire n'a pas posé de `public_label`. Neutre par défaut : le nom
# d'un projet de rénovation porte couramment le nom et l'adresse du client (spec §3.5, « pas
# d'info sensible exposée »), et le lien se transfère.
DEFAULT_PUBLIC_LABEL = "Vue partagée"


def _client_key(request: Request) -> str:
    client = request.client
    return client.host if client else "inconnu"


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
    """
    await get_owned_project(session, project_id, current_user, OrganizationRole.EDITOR)

    expires_at = (
        utcnow() + timedelta(days=payload.expires_in_days)
        if payload.expires_in_days is not None
        else None
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


@router.get("/api/public/views/{token}", response_model=PublicSceneResponse)
async def read_public_view(
    request: Request,
    session: SessionDep,
    token: Annotated[str, Path(min_length=16, max_length=64)],
) -> PublicSceneResponse:
    """Lecture publique d'une vue partagée. **Aucune authentification.**

    Le message d'erreur est le même pour un jeton inexistant, révoqué ou expiré : les distinguer
    permettrait de confirmer qu'un lien a existé.
    """
    if not public_rate_limiter.hit(_client_key(request), time.monotonic()):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Trop de requêtes, réessayez plus tard",
        )

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
