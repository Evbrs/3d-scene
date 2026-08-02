"""Partage de vue par lien permalien (`docs/spec-complete.md` §3.5, phase P8).

L'endpoint public est le point délicat : il sert des données **sans authentification**. Trois
propriétés sont donc traitées comme faisant partie de la fonctionnalité, pas comme des options :

1. **Jeton imprévisible** — `secrets.token_urlsafe(32)`, soit 256 bits d'entropie. Un
   identifiant séquentiel rendrait tous les projets partagés énumérables.
2. **Aucune information sensible** — la réponse publique ne contient ni propriétaire, ni
   identifiants internes de projet, ni horodatage d'édition.
3. **Limitation de débit** — sans elle, l'endpoint public est un amplificateur : chaque appel
   déclenche un calcul de scene graph complet.
"""

import secrets
import time
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request, Response, status
from sqlalchemy.orm import selectinload
from sqlmodel import col, select

from app.api.deps import CurrentUser, SessionDep
from app.api.permissions import get_owned_project
from app.api.scene import load_scene_inputs, project_to_plain_dict
from app.core.rate_limit import SlidingWindowRateLimiter
from app.geometry.scene import build_scene_graph
from app.models.base import utcnow
from app.models.plan import Project, SharedView
from app.schemas.share import PublicSceneResponse, SharedViewCreate, SharedViewRead

router = APIRouter(tags=["partage"])

# Un jeton de 32 octets encodés en URL-safe base64 : imprévisible, et court à copier-coller.
TOKEN_BYTES = 32

# Débit du lecteur public. Généreux pour un usage normal (rechargements, plusieurs onglets),
# assez bas pour rendre l'énumération de jetons et l'abus de calcul inintéressants.
public_rate_limiter = SlidingWindowRateLimiter(max_attempts=60, window_seconds=60)

# Clé d'expiration stockée dans `state`, pour ne pas ajouter de colonne à ce stade.
EXPIRY_KEY = "__expires_at"


def _client_key(request: Request) -> str:
    client = request.client
    return client.host if client else "inconnu"


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
    """Crée un lien de partage sur un projet dont on est propriétaire."""
    await get_owned_project(session, project_id, current_user)

    state = payload.state.model_dump(mode="json")
    if payload.expires_in_days is not None:
        expiry = utcnow() + timedelta(days=payload.expires_in_days)
        state[EXPIRY_KEY] = expiry.isoformat()
    if payload.label:
        state["label"] = payload.label

    shared = SharedView(
        project_id=project_id, token=secrets.token_urlsafe(TOKEN_BYTES), state=state
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
    """Révoque un lien. Un partage qu'on ne peut pas retirer n'est pas un partage maîtrisé."""
    shared = (
        await session.execute(
            select(SharedView)
            .where(col(SharedView.id) == shared_view_id)
            .options(selectinload(SharedView.project))  # type: ignore[arg-type]
        )
    ).scalar_one_or_none()

    # 404 et non 403 sur le partage d'autrui : même règle que le reste de l'API.
    if shared is None or shared.project.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Partage introuvable")

    await session.delete(shared)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/api/public/views/{token}", response_model=PublicSceneResponse)
async def read_public_view(
    request: Request,
    session: SessionDep,
    token: Annotated[str, Path(min_length=16, max_length=64)],
) -> PublicSceneResponse:
    """Lecture publique d'une vue partagée. **Aucune authentification.**

    Le message d'erreur est le même pour un jeton inexistant et pour un jeton expiré : les
    distinguer permettrait de confirmer qu'un lien a existé.
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
    if shared is None:
        raise not_found

    expires_at = shared.state.get(EXPIRY_KEY)
    if isinstance(expires_at, str):
        from datetime import datetime

        if datetime.fromisoformat(expires_at) <= utcnow():
            raise not_found

    project = (
        await session.execute(select(Project).where(col(Project.id) == shared.project_id))
    ).scalar_one_or_none()
    if project is None:
        raise not_found

    _project, catalog = await load_scene_inputs(session, shared.project_id)
    scene = build_scene_graph(project_to_plain_dict(_project), catalog)

    # L'identifiant interne du projet n'a rien à faire dans une réponse publique.
    scene.pop("project_id", None)

    public_state = {
        key: value for key, value in shared.state.items() if not key.startswith("__")
    }

    return PublicSceneResponse(
        project_name=project.name, state=public_state, scene=scene
    )
