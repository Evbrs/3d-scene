"""API du scene graph 3D (`docs/spec-complete.md` §3.1, phase P6).

Le calcul reste **synchrone** ici : c'est l'arbitrage de la spec §8 (cas 2), « construire en
synchrone (P6), migrer vers Celery (P9) en mesurant le gain avant/après ». Passer directement en
asynchrone contredirait la décision et priverait P9 de sa mesure de référence.
"""

from typing import Any

from fastapi import APIRouter, Response
from sqlalchemy.orm import selectinload
from sqlmodel import col, select

from app.api.deps import CurrentUser, SessionDep
from app.api.permissions import get_owned_project
from app.core.cache import scene_cache
from app.geometry.scene import build_scene_graph
from app.models.plan import Element, Face, FurnitureType, Project, Room

router = APIRouter(prefix="/api", tags=["scene"])


def _element_to_dict(element: Element) -> dict[str, Any]:
    return {
        "id": element.id,
        "kind": element.kind.value,
        "x_offset_cm": element.x_offset_cm,
        "y_offset_cm": element.y_offset_cm,
        "width_cm": element.width_cm,
        "height_cm": element.height_cm,
        "depth_cm": element.depth_cm,
        "rotation_deg": element.rotation_deg,
        "furniture_type_id": element.furniture_type_id,
        "colors": dict(element.colors),
        "variant_params": dict(element.variant_params),
    }


def _face_to_dict(face: Face) -> dict[str, Any]:
    return {
        "id": face.id,
        "label": face.label,
        "kind": face.kind.value,
        "start_x_cm": face.start_x_cm,
        "start_y_cm": face.start_y_cm,
        "end_x_cm": face.end_x_cm,
        "end_y_cm": face.end_y_cm,
        "covering": dict(face.covering),
        # Ordre stable : le scene graph doit être identique d'un appel à l'autre pour que le
        # cache de P10 fasse mouche.
        "elements": [
            _element_to_dict(element) for element in sorted(face.elements, key=lambda e: e.id or 0)
        ],
    }


def project_to_plain_dict(project: Project) -> dict[str, Any]:
    """Traduit les modèles ORM en dictionnaires simples.

    Le module `app.geometry` ne connaît volontairement rien de SQLModel : c'est ce qui permet aux
    fixtures de référence de l'alimenter directement, sans base de données.
    """
    return {
        "project_id": project.id,
        "rooms": [
            {
                "id": room.id,
                "name": room.name,
                "wall_thickness_cm": room.wall_thickness_cm,
                "ceiling_height_cm": room.ceiling_height_cm,
                "polygon": [list(vertex) for vertex in room.polygon],
                "faces": [
                    _face_to_dict(face) for face in sorted(room.faces, key=lambda f: f.id or 0)
                ],
            }
            for room in sorted(project.rooms, key=lambda r: r.id or 0)
        ],
    }


async def load_scene_inputs(
    session: SessionDep, project_id: int
) -> tuple[Project, dict[int, dict[str, Any]]]:
    """Charge le projet complet et le catalogue nécessaire, en un nombre fixe de requêtes."""
    project = (
        await session.execute(
            select(Project)
            .where(col(Project.id) == project_id)
            .options(
                selectinload(Project.rooms)  # type: ignore[arg-type]
                .selectinload(Room.faces)  # type: ignore[arg-type]
                .selectinload(Face.elements)  # type: ignore[arg-type]
            )
        )
    ).scalar_one()

    referenced = {
        element.furniture_type_id
        for room in project.rooms
        for face in room.faces
        for element in face.elements
        if element.furniture_type_id is not None
    }

    catalog: dict[int, dict[str, Any]] = {}
    if referenced:
        rows = (
            (
                await session.execute(
                    select(FurnitureType).where(col(FurnitureType.id).in_(referenced))
                )
            )
            .scalars()
            .all()
        )
        catalog = {
            row.id or 0: {
                "id": row.id,
                "slug": row.slug,
                "parts": list(row.parts),
                "color_slots": list(row.color_slots),
            }
            for row in rows
        }

    return project, catalog


@router.get("/projects/{project_id}/scene")
async def read_scene_graph(
    project_id: int,
    session: SessionDep,
    current_user: CurrentUser,
    response: Response,
) -> dict[str, Any]:
    """Scene graph complet du projet, prêt à être traduit en objets Three.js.

    Le type de retour est un dictionnaire libre et non un modèle Pydantic : la forme des nœuds
    dépend de leur nature (mur, sol, meuble), et un `Union` discriminé de six variantes
    alourdirait le schéma OpenAPI sans rien apporter au client, qui aiguille sur `kind`.

    Le résultat est mis en cache sous une clé portant la version du projet (spec §8, cas 6) :
    toute écriture du plan incrémente cette version, donc rend l'ancienne entrée inatteignable.
    L'en-tête `X-Cache` expose le résultat, pour que la mesure soit faisable depuis un client.
    """
    owned = await get_owned_project(session, project_id, current_user)

    cached = await scene_cache.get(project_id, owned.version)
    if cached is not None:
        response.headers["X-Cache"] = "hit"
        return cached

    project, catalog = await load_scene_inputs(session, project_id)
    scene = build_scene_graph(project_to_plain_dict(project), catalog)
    await scene_cache.set(project_id, owned.version, scene)
    response.headers["X-Cache"] = "miss"
    return scene
