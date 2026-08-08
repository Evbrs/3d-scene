"""API du scene graph 3D (`docs/spec-complete.md` §3.1, phase P6).

Le calcul reste **synchrone** ici : c'est l'arbitrage de la spec §8 (cas 2), « construire en
synchrone (P6), migrer vers Celery (P9) en mesurant le gain avant/après ». Passer directement en
asynchrone contredirait la décision et priverait P9 de sa mesure de référence.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Response, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import selectinload
from sqlmodel import col, select

from app.api.conflicts import ConflictAwareRoute
from app.api.deps import CurrentUser, SessionDep
from app.api.permissions import get_owned_project
from app.core.cache import catalog_fingerprint, scene_cache
from app.geometry.scene import OPENING_SLUGS, build_scene_graph
from app.models.plan import Element, Face, FurnitureType, Project, Room

router = APIRouter(prefix="/api", tags=["scene"], route_class=ConflictAwareRoute)


def _element_to_dict(element: Element) -> dict[str, Any]:
    return {
        "id": element.id,
        "kind": element.kind.value,
        "x_offset_cm": element.x_offset_cm,
        "y_offset_cm": element.y_offset_cm,
        # Centre de l'emprise d'un meuble libre, dans le repère du plan (spec §10, A4). Nuls pour
        # tout ce qui est adossé à une face, où ce sont les décalages ci-dessus qui font foi.
        "pos_x_cm": element.pos_x_cm,
        "pos_y_cm": element.pos_y_cm,
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
                # Mobilier libre : ancré à la pièce, donc absent de toutes les faces (spec §10,
                # amendement A4). Sans cette clé, un lit ou un îlot n'atteint jamais la 3D.
                "elements": [
                    _element_to_dict(element)
                    for element in sorted(room.free_elements, key=lambda e: e.id or 0)
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
                .selectinload(Face.elements),  # type: ignore[arg-type]
                selectinload(Project.rooms).selectinload(Room.free_elements),  # type: ignore[arg-type]
            )
        )
    ).scalar_one_or_none()
    # `scalar_one` remontait en 500 quand le projet disparaissait entre la vérification de
    # propriété et le calcul — et sur le chemin **public** (P8), où l'identifiant vient d'un
    # partage qui peut survivre à son projet, la 500 était le cas nominal.
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Projet introuvable")

    referenced = {
        element.furniture_type_id
        for room in project.rooms
        for element in [
            *(item for face in room.faces for item in face.elements),
            *room.free_elements,
        ]
        if element.furniture_type_id is not None
    }

    # Les recettes de menuiserie sont chargées d'office : une ouverture n'a pas de
    # `furniture_type_id` (spec §5, ce champ n'est renseigné que pour `kind == FURNITURE`), c'est
    # sa nature qui désigne sa recette. Sans ce chargement, aucune porte ni fenêtre n'est jamais
    # rendue — le percement resterait un rectangle traversant.
    rows = (
        (
            await session.execute(
                select(FurnitureType).where(
                    col(FurnitureType.id).in_(referenced)
                    | col(FurnitureType.slug).in_(OPENING_SLUGS.values())
                )
            )
        )
        .scalars()
        .all()
    )
    catalog: dict[int, dict[str, Any]] = {
        row.id or 0: {
            "id": row.id,
            "slug": row.slug,
            "parts": list(row.parts),
            "color_slots": list(row.color_slots),
            "variants": list(row.variants),
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

    scene, from_cache = await scene_for_project(session, project_id, owned.version)
    response.headers["X-Cache"] = "hit" if from_cache else "miss"
    return scene


async def scene_for_project(
    session: SessionDep, project_id: int, version: int
) -> tuple[dict[str, Any], bool]:
    """Scène du projet, servie depuis le cache si possible.

    Point de passage unique, partagé avec la lecture publique (P8) : deux chemins calculant la
    scène séparément finiraient par en servir deux versions différentes — c'est exactement ce
    qui se produisait quand seul l'endpoint authentifié utilisait le cache.

    Le catalogue est chargé avant toute lecture de cache : c'est lui qui donne l'empreinte
    entrant dans la clé, sans laquelle une recette modifiée resterait servie depuis l'ancienne
    entrée.
    """
    project, catalog = await load_scene_inputs(session, project_id)
    fingerprint = catalog_fingerprint(catalog)

    cached = await scene_cache.get(project_id, version, fingerprint)
    if cached is not None:
        return cached, True

    # `build_scene_graph` est du calcul numpy pur, donc bloquant : sur un plan un peu fourni il
    # gèle la boucle d'événements — et donc *toutes* les requêtes en cours — pendant des centaines
    # de millisecondes. Le chemin public (P8) est atteignable sans authentification, ce qui en
    # ferait un levier de déni de service à un appel par seconde.
    scene = await run_in_threadpool(
        build_scene_graph, project_to_plain_dict(project), catalog
    )
    await scene_cache.set(project_id, version, scene, fingerprint)
    return scene, False
