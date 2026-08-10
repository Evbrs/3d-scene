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

Les déporter dans un fil les empêche de figer le service, il ne les rend pas gratuits : mesuré,
l'aménagement d'une cuisine accessible en cinq variantes coûte **633 ms** de processeur. Les trois
routes portent donc un plafond de débit calibré sur ce coût (`app/core/rate_limit.py`), et celui de
l'aménagement est vingt fois plus serré que celui des deux lectures.

Les trois sont enfin des **fonctionnalités payantes**, et le sont depuis l'amendement A14. Elles
figuraient dans la grille tarifaire depuis leur écriture — « contrôle de conformité » et
« calepinage » au palier Artisan, « aménagement automatique » au palier Entreprise — sans qu'une
seule ligne ne le vérifie : un compte gratuit obtenait les trois en 200. Le mur est ici, à
l'entrée de chaque route, et jamais dans le moteur : `rules.py` et `layout.py` restent des
fonctions pures, testables par fixtures sans base ni abonnement.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from sqlmodel import col, select

from app.api.deps import CurrentUser, RequireFeature, SessionDep
from app.api.permissions import get_owned_project, get_owned_room
from app.api.scene import scene_for_project
from app.core.rate_limit import costly
from app.intelligence.ergonomy import Thresholds, thresholds_from
from app.intelligence.layout import plan_project_tiling, propose_layouts
from app.intelligence.rules import inspect_scene
from app.models.organization import Organization, OrganizationRole
from app.models.plan import Project
from app.schemas.intelligence import (
    InspectionRead,
    LayingPlanRead,
    LayoutProposalsRead,
    LayoutRequest,
)
from app.services.quotas import register_ai_run
from app.services.seed_plans import (
    FEATURE_AUTO_LAYOUT,
    FEATURE_COMPLIANCE_CHECK,
    FEATURE_TILING_WASTE,
)

router = APIRouter(prefix="/api", tags=["intelligence"])

# Les trois murs de ce module. `docs/strategie-produit.md` §4 les place à deux paliers différents :
# la conformité et le calepinage arrivent avec Artisan, l'aménagement automatique avec Entreprise.
# Comme partout, le premier geste refusé ouvre l'essai sans carte et aboutit.
REQUIRE_COMPLIANCE_CHECK = RequireFeature(FEATURE_COMPLIANCE_CHECK)
REQUIRE_TILING_WASTE = RequireFeature(FEATURE_TILING_WASTE)
REQUIRE_AUTO_LAYOUT = RequireFeature(FEATURE_AUTO_LAYOUT)


async def _thresholds_for(
    session: SessionDep, organization_id: int, *, accessible: bool
) -> Thresholds:
    """Seuils d'usage de l'organisation : les défauts du produit, corrigés par ses surcharges.

    A12 refuse tout seuil venu du corps d'une requête — « il suffirait de demander 10 cm de passage
    pour rendre conforme un plan invivable » — en s'accordant une porte de sortie : « un réglage par
    organisation est une ligne SQL ». Cette lecture est cette porte. Sans elle, la règle interdisait
    un abus sans offrir la moindre alternative à l'entreprise qui travaille sous une norme
    différente, ce qui n'est pas tenable (amendement A14).

    Le seul paramètre que le client garde est le **mode** accessible, et il ne relâche aucun seuil :
    il en resserre.
    """
    overrides = (
        await session.execute(
            select(col(Organization.inspection_thresholds)).where(
                col(Organization.id) == organization_id
            )
        )
    ).scalar_one_or_none()
    return thresholds_from(overrides, accessible=accessible)


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


@router.get(
    "/projects/{project_id}/inspection",
    response_model=InspectionRead,
    dependencies=[Depends(costly("inspection"))],
)
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
    await REQUIRE_COMPLIANCE_CHECK(session, project.organization_id)
    scene, _ = await scene_for_project(session, project_id, project.version)
    thresholds = await _thresholds_for(
        session, project.organization_id, accessible=accessible
    )
    report = await run_in_threadpool(inspect_scene, scene, thresholds)
    await _count_run(
        session,
        project,
        current_user,
        kind="inspection",
        variant="accessible" if accessible else "",
    )
    return report


@router.get(
    "/projects/{project_id}/laying-plan",
    response_model=LayingPlanRead,
    dependencies=[Depends(costly("laying_plan"))],
)
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
    await REQUIRE_TILING_WASTE(session, project.organization_id)
    scene, _ = await scene_for_project(session, project_id, project.version)
    plan = await run_in_threadpool(plan_project_tiling, scene)
    await _count_run(session, project, current_user, kind="laying_plan")
    return plan


@router.post(
    "/rooms/{room_id}/layouts",
    response_model=LayoutProposalsRead,
    dependencies=[Depends(costly("layout"))],
)
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
    l'ouvrir en lecture seule en ferait le levier de charge le moins cher du produit. Le rôle ne
    suffisait pourtant pas : un `editor` est ce qu'on devient en s'inscrivant, donc le plafond de
    débit est ici la vraie protection, pas le contrôle d'accès.
    """
    room = await get_owned_room(session, room_id, current_user, OrganizationRole.EDITOR)
    project = await get_owned_project(
        session, room.project_id, current_user, OrganizationRole.EDITOR
    )
    await REQUIRE_AUTO_LAYOUT(session, project.organization_id)
    scene, _ = await scene_for_project(session, room.project_id, project.version)

    room_scene = next(
        (candidate for candidate in scene["rooms"] if candidate.get("id") == room_id), None
    )
    if room_scene is None:
        # La pièce a disparu entre la vérification d'appartenance et le calcul, ou la scène a été
        # servie depuis une entrée de cache d'une version antérieure. Un 500 ferait passer une
        # course pour une panne.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pièce introuvable")

    thresholds = await _thresholds_for(
        session, project.organization_id, accessible=payload.accessible
    )
    proposals = await run_in_threadpool(
        lambda: propose_layouts(
            room_scene,
            program=payload.program,
            thresholds=thresholds,
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
