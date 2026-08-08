"""API du moteur d'intelligence du plan (`docs/strategie-produit.md` §3.8).

Trois routes — le contrôle de conformité, le calepinage, l'aménagement — et une seule source de
géométrie : le scene graph déjà mis en cache par `app/api/scene.py`. Aucune des trois ne
reconstruit la scène pour son compte. C'est la règle que le métré a posée (`app/api/takeoff.py`),
et la raison est la même : deux chemins qui construiraient la géométrie séparément finiraient par
en servir deux versions différentes, et le contrôle de conformité refuserait alors des plans que le
viewer affiche.

Les trois calculs sont du calcul numérique pur, donc bloquants. Rendus sur la boucle d'événements
ils gèleraient **toutes** les requêtes en cours pendant leur durée — c'est déjà l'arbitrage retenu
pour `build_scene_graph` et `build_takeoff`, et l'aménagement automatique est de loin le plus cher
des trois.
"""

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool

from app.api.deps import CurrentUser, SessionDep
from app.api.permissions import get_owned_project, get_owned_room
from app.api.scene import scene_for_project
from app.intelligence.ergonomy import Thresholds
from app.intelligence.layout import plan_project_tiling, propose_layouts
from app.intelligence.rules import inspect_scene
from app.models.organization import OrganizationRole
from app.models.plan import Project
from app.schemas.intelligence import (
    InspectionRead,
    LayingPlanRead,
    LayoutProposalsRead,
    LayoutRequest,
)
from app.services.quotas import register_ai_run

router = APIRouter(prefix="/api", tags=["intelligence"])


async def _count_run(
    session: SessionDep,
    project: Project,
    current_user: CurrentUser,
    *,
    kind: str,
    subject_id: int | None = None,
    variant: str = "",
) -> None:
    """Enregistre l'analyse dans les compteurs d'usage, une fois le résultat obtenu.

    Après le calcul et jamais avant : une analyse qui échoue n'a pas eu lieu. Le commit est
    explicite — une lecture qui écrit doit le faire savoir, et laisser la session ouverte
    reporterait l'écriture sur le prochain appelant.
    """
    if project.id is None or project.organization_id is None:
        return
    await register_ai_run(
        session,
        organization_id=project.organization_id,
        kind=kind,
        subject_id=subject_id if subject_id is not None else project.id,
        version=project.version,
        variant=variant,
        user_id=current_user.id,
    )
    await session.commit()


@router.get("/projects/{project_id}/inspection", response_model=InspectionRead)
async def read_inspection(
    project_id: int,
    session: SessionDep,
    current_user: CurrentUser,
    accessible: Annotated[
        bool,
        Query(
            description=(
                "Applique les seuils d'un logement accessible (couloir de 120 cm) en plus des "
                "seuils d'usage courant."
            )
        ),
    ] = False,
) -> dict[str, Any]:
    """Toutes les anomalies de conformité du projet, triées par sévérité puis par pièce.

    Chacune porte un identifiant de règle stable, une mesure, le seuil qu'elle enfreint et les
    entités concernées : c'est ce qui permet au panneau d'inspection de recentrer le plan sur le
    problème plutôt que de laisser l'utilisateur le chercher.

    `warnings` recense ce qui **n'a pas pu** être contrôlé — une pièce dont les murs ne se
    referment pas n'a pas de contour, donc pas de circulation mesurable. Un rapport vide
    accompagné d'un avertissement ne veut pas dire « conforme ».
    """
    project = await get_owned_project(session, project_id, current_user)
    scene, _ = await scene_for_project(session, project_id, project.version)
    thresholds = Thresholds(accessible=accessible)
    report = await run_in_threadpool(inspect_scene, scene, thresholds)
    await _count_run(
        session,
        project,
        current_user,
        kind="inspection",
        variant="accessible" if accessible else "",
    )
    return report


@router.get("/projects/{project_id}/laying-plan", response_model=LayingPlanRead)
async def read_laying_plan(
    project_id: int, session: SessionDep, current_user: CurrentUser
) -> dict[str, Any]:
    """Calepinage optimisé : sens de pose, position de la première rangée, et plinthes.

    Le métré (`GET /takeoff`) donne déjà entières, coupes et chutes pour la pose de référence —
    trame calée sur le coin, unité dans le sens déclaré. Cette route dit **comment poser** : quel
    sens minimise les coupes, où démarrer la trame pour ne jamais laisser moins d'un tiers d'unité
    en rive, et combien de barres de plinthe commander en réemployant les chutes.

    Elle n'entre pas dans le devis et ne le contredit pas : `cuts_saved` mesure l'écart avec la
    pose de référence du métré, il ne réécrit pas la quantité à commander, qui reste établie sur la
    surface et le taux de chute.
    """
    project = await get_owned_project(session, project_id, current_user)
    scene, _ = await scene_for_project(session, project_id, project.version)
    plan = await run_in_threadpool(plan_project_tiling, scene)
    await _count_run(session, project, current_user, kind="laying_plan")
    return plan


@router.post("/rooms/{room_id}/layouts", response_model=LayoutProposalsRead)
async def propose_room_layouts(
    room_id: int,
    payload: LayoutRequest,
    session: SessionDep,
    current_user: CurrentUser,
) -> dict[str, Any]:
    """Deux ou trois implantations de mobilier **valides** et classées, pour la pièce demandée.

    Un `POST` alors que rien n'est écrit : le verbe est celui du corps de requête, pas celui d'un
    effet de bord. Aucune ligne n'est créée — le client choisit une proposition et crée lui-même
    les éléments, parce qu'un moteur qui poserait d'autorité quinze meubles dans le plan d'un
    artisan serait un moteur qu'on désactive au premier essai.

    Le rôle exigé est `editor` et non `viewer`, pour deux raisons : la proposition n'a de sens que
    pour quelqu'un qui peut ensuite modifier le plan, et c'est le calcul le plus coûteux de l'API —
    l'ouvrir en lecture seule en ferait le levier de charge le moins cher du produit.
    """
    room = await get_owned_room(session, room_id, current_user, OrganizationRole.EDITOR)
    project = await get_owned_project(
        session, room.project_id, current_user, OrganizationRole.EDITOR
    )
    scene, _ = await scene_for_project(session, room.project_id, project.version)

    room_scene = next(
        (candidate for candidate in scene["rooms"] if candidate.get("id") == room_id), None
    )
    if room_scene is None:
        # La pièce a disparu entre la vérification d'appartenance et le calcul, ou la scène a été
        # servie depuis une entrée de cache d'une version antérieure. Un 500 ferait passer une
        # course pour une panne.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pièce introuvable")

    proposals = await run_in_threadpool(
        lambda: propose_layouts(
            room_scene,
            program=payload.program,
            thresholds=Thresholds(accessible=payload.accessible),
            count=payload.count,
        )
    )
    await _count_run(
        session,
        project,
        current_user,
        kind="layout",
        subject_id=room_id,
        variant=f"{payload.program or ''}:{payload.count}:{int(payload.accessible)}",
    )
    return proposals
