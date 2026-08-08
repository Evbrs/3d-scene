"""API du métré (`docs/strategie-produit.md` §3.1).

Deux représentations d'un même calcul, et la seconde n'est pas un gadget :

- `GET /api/projects/{id}/takeoff` rend le métré en JSON, tel que le moteur de devis le consomme ;
- `GET /api/projects/{id}/takeoff.csv` rend le même métré en tableur. C'est le pont le moins cher
  vers le classeur de prix que l'artisan utilise déjà, il rend le devis **vérifiable ligne à
  ligne** contre les élévations cotées, et il tient lieu d'export de portabilité au sens du RGPD.

Le calcul lui-même est une fonction pure (`app.geometry.quantities.build_takeoff`) branchée sur le
scene graph déjà mis en cache par `app/api/scene.py` : le métré ne recalcule pas la géométrie, il
la lit. Deux chemins qui construiraient la scène séparément finiraient par en servir deux versions
différentes — c'est exactement la raison d'être de `scene_for_project`.
"""

import csv
import io
from typing import Any

from fastapi import APIRouter, Response
from fastapi.concurrency import run_in_threadpool

from app.api.deps import CurrentUser, SessionDep
from app.api.permissions import get_owned_project
from app.api.scene import scene_for_project
from app.geometry.quantities import build_takeoff
from app.schemas.quote import TakeoffRead

router = APIRouter(prefix="/api", tags=["metre"])

# Point-virgule et non virgule : c'est le séparateur qu'attend un tableur configuré en français,
# et une virgule y collerait toutes les colonnes dans la première cellule. La virgule décimale des
# nombres impose de toute façon ce choix.
CSV_DELIMITER = ";"
# BOM UTF-8 : sans elle, Excel lit le fichier en ANSI et « Salle d'eau » devient « Salle dâ€™eau ».
UTF8_BOM = "﻿"

CSV_COLUMNS = (
    "piece",
    "hauteur_sous_plafond_m",
    "surface_sol_m2",
    "plinthe_ml",
    "corniche_ml",
    "face",
    "nature",
    "longueur_m",
    "hauteur_m",
    "surface_brute_m2",
    "surface_percements_m2",
    "surface_nette_m2",
    "nb_ouvertures",
    "nb_portes",
    "nb_fenetres",
    "deduction_plinthe_ml",
    "materiau",
    "motif_de_pose",
    "unite_largeur_cm",
    "unite_hauteur_cm",
    "taux_de_chute",
    "surface_a_commander_m2",
    "unites_a_commander",
    "unites_entieres",
    "unites_coupees",
)


async def compute_takeoff(
    session: SessionDep, project_id: int, version: int
) -> dict[str, Any]:
    """Métré du projet, calculé depuis la scène (cache compris).

    `build_takeoff` est du calcul numérique pur, donc bloquant : rendu sur la boucle d'événements,
    il gèle toutes les autres requêtes le temps du calcul, exactement comme `build_scene_graph`.
    """
    scene, _ = await scene_for_project(session, project_id, version)
    return await run_in_threadpool(build_takeoff, scene)


@router.get("/projects/{project_id}/takeoff", response_model=TakeoffRead)
async def read_takeoff(
    project_id: int, session: SessionDep, current_user: CurrentUser
) -> dict[str, Any]:
    """Métré complet : par face, par pièce, puis par projet.

    Les valeurs que le métré n'a pas su établir sortent à `null` et **jamais à zéro**, chacune
    accompagnée d'une entrée dans `warnings`. Un total est donc partiel dès que `warnings` n'est
    pas vide : un client qui produit un devis doit le lire.
    """
    project = await get_owned_project(session, project_id, current_user)
    return await compute_takeoff(session, project_id, project.version)


def _decimal_fr(value: float | int | None) -> str:
    """Nombre à la française : virgule décimale, cellule vide pour une valeur non établie.

    Une inconnue ne devient pas zéro dans le tableur non plus. Une cellule vide se voit dans une
    somme ; un zéro s'y confond avec une mesure réellement nulle.
    """
    if value is None:
        return ""
    if isinstance(value, int):
        return str(value)
    return f"{value:g}".replace(".", ",")


def _face_rows(takeoff: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for room in takeoff.get("rooms") or []:
        for face in room.get("faces") or []:
            tiling = face.get("tiling") or {}
            rows.append(
                {
                    "piece": str(room.get("name") or ""),
                    "hauteur_sous_plafond_m": _decimal_fr(room.get("ceiling_height_m")),
                    "surface_sol_m2": _decimal_fr(room.get("floor_area_m2")),
                    "plinthe_ml": _decimal_fr(room.get("skirting_ml")),
                    "corniche_ml": _decimal_fr(room.get("cornice_ml")),
                    "face": str(face.get("face_label") or ""),
                    "nature": str(face.get("kind") or ""),
                    "longueur_m": _decimal_fr(face.get("length_m")),
                    "hauteur_m": _decimal_fr(face.get("height_m")),
                    "surface_brute_m2": _decimal_fr(face.get("gross_area_m2")),
                    "surface_percements_m2": _decimal_fr(face.get("openings_area_m2")),
                    "surface_nette_m2": _decimal_fr(face.get("net_area_m2")),
                    "nb_ouvertures": _decimal_fr(face.get("opening_count")),
                    "nb_portes": _decimal_fr(face.get("door_count")),
                    "nb_fenetres": _decimal_fr(face.get("window_count")),
                    "deduction_plinthe_ml": _decimal_fr(face.get("skirting_deduction_ml")),
                    "materiau": str(face.get("material") or ""),
                    "motif_de_pose": str(tiling.get("pattern") or ""),
                    "unite_largeur_cm": _decimal_fr(tiling.get("unit_width_cm")),
                    "unite_hauteur_cm": _decimal_fr(tiling.get("unit_height_cm")),
                    "taux_de_chute": _decimal_fr(tiling.get("waste_ratio")),
                    "surface_a_commander_m2": _decimal_fr(tiling.get("ordered_area_m2")),
                    "unites_a_commander": _decimal_fr(tiling.get("units_total")),
                    "unites_entieres": _decimal_fr(tiling.get("full_units")),
                    "unites_coupees": _decimal_fr(tiling.get("cut_units")),
                }
            )
    return rows


def takeoff_to_csv(takeoff: dict[str, Any]) -> str:
    """Métré au format tableur : une ligne par face, avec les données de pièce répétées.

    La répétition des colonnes de pièce sur chaque ligne est délibérée : c'est ce qui rend le
    fichier exploitable en tableau croisé dynamique, là où des sections imbriquées obligeraient à
    le retravailler à la main.

    Les avertissements sont écrits **en fin de fichier**, préfixés `# AVERTISSEMENT`, plutôt que
    perdus. Un métré incomplet exporté sans ses réserves donne un devis trop bas, et personne ne
    va relire l'API pour les retrouver.
    """
    buffer = io.StringIO()
    # `\r\n` : c'est ce qu'attend le RFC 4180 et ce que produisent les tableurs. `lineterminator`
    # doit être explicite, `csv` écrit sinon un `\r\n` dépendant de la plateforme.
    writer = csv.DictWriter(
        buffer, fieldnames=CSV_COLUMNS, delimiter=CSV_DELIMITER, lineterminator="\r\n"
    )
    writer.writeheader()
    writer.writerows(_face_rows(takeoff))

    totals = takeoff.get("totals") or {}
    plain = csv.writer(buffer, delimiter=CSV_DELIMITER, lineterminator="\r\n")
    plain.writerow([])
    plain.writerow(["# TOTAUX DU PROJET"])
    for label, key in (
        ("surface de sol (m2)", "floor_area_m2"),
        ("surface de plafond (m2)", "ceiling_area_m2"),
        ("surface de murs brute (m2)", "wall_gross_area_m2"),
        ("surface de murs nette (m2)", "wall_net_area_m2"),
        ("volume (m3)", "volume_m3"),
        ("plinthe (ml)", "skirting_ml"),
        ("corniche (ml)", "cornice_ml"),
        ("nombre de portes", "door_count"),
        ("nombre de fenetres", "window_count"),
    ):
        plain.writerow([f"# {label}", _decimal_fr(totals.get(key))])

    for message in takeoff.get("warnings") or []:
        plain.writerow(["# AVERTISSEMENT", message])

    return UTF8_BOM + buffer.getvalue()


@router.get(
    "/projects/{project_id}/takeoff.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}, "description": "Métré au format tableur"}},
)
async def download_takeoff_csv(
    project_id: int, session: SessionDep, current_user: CurrentUser
) -> Response:
    """Même métré, au format d'un classeur de prix — et export de portabilité RGPD."""
    project = await get_owned_project(session, project_id, current_user)
    takeoff = await compute_takeoff(session, project_id, project.version)

    return Response(
        content=takeoff_to_csv(takeoff),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="metre-projet-{project_id}.csv"'
        },
    )
