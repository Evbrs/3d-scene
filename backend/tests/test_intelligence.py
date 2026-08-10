"""Le moteur d'intelligence du plan, confronté à ses fixtures et à ses règles.

Mêmes règles que `tests/geometry/` : les valeurs attendues des fixtures ont été **calculées à la
main**, à partir du plan, et font foi (`CLAUDE.md`). Le champ `reasoning` de chaque fixture rejoue
le calcul sans relire le code — c'est lui qui distingue une valeur dérivée d'une valeur recopiée
depuis une sortie de programme.

L'entrée des fixtures est un **plan**, comme pour le métré : le test construit d'abord le scene
graph, puis l'inspecte. Un contrôle juste sur un scene graph inventé ne prouverait rien de la
chaîne réelle.

Trois familles de tests, et la troisième est celle qui vaut le plus cher :

1. les fixtures, qui figent le contrat ;
2. les règles prises une par une, sur des scènes construites pour n'en exercer qu'une ;
3. les **croisements** — l'aménagement automatique repasse sous le contrôle de conformité, et le
   calepinage retombe sur le décompte du métré. Deux moteurs du même produit qui se contrediraient
   valent moins que zéro.
"""

import json
import math
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession

from app.geometry.quantities import build_takeoff
from app.geometry.scene import build_scene_graph
from app.intelligence.ergonomy import (
    Thresholds,
    build_shell,
    node_footprint,
    overlap_depth,
    point_in_polygon,
)
from app.intelligence.layout import (
    DEFAULT_LAYING,
    LayingRules,
    plan_axis,
    plan_face_tiling,
    plan_project_tiling,
    plan_room_skirting,
    propose_layouts,
)
from app.intelligence.rules import (
    RULE_ACCESSIBLE_CORRIDOR,
    RULE_CEILING,
    RULE_DOOR_HAND,
    RULE_DOOR_SWING,
    RULE_DOOR_SWINGS_COLLIDE,
    RULE_NO_OPENING,
    RULE_OVERLAP,
    RULE_PASSAGE,
    RULE_THROUGH_WALL,
    RULE_WET_ROOM,
    Severity,
    inspect_scene,
)
from app.services.seed_plans import PLAN_BUSINESS
from tests.conftest import subscribe

FIXTURES = Path(__file__).parent / "intelligence" / "fixtures"

CARRE: list[list[float]] = [[0, 0], [400, 0], [400, 300], [0, 300]]


def load(name: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return payload


def scene_of(fixture: dict[str, Any]) -> dict[str, Any]:
    catalog = {
        int(key): value
        for key, value in (fixture["input"].get("furniture_types") or {}).items()
    }
    return build_scene_graph(fixture["input"], catalog)


def rule_ids(report: dict[str, Any]) -> list[str]:
    return [anomaly["rule_id"] for anomaly in report["anomalies"]]


# --- Construction de scènes d'essai --------------------------------------------------------------


def wall(label: str, start: tuple[float, float], end: tuple[float, float], face_id: int,
         elements: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "id": face_id,
        "label": label,
        "kind": "wall",
        "start_x_cm": start[0],
        "start_y_cm": start[1],
        "end_x_cm": end[0],
        "end_y_cm": end[1],
        "covering": {},
        "elements": elements or [],
    }


def opening(
    element_id: int, kind: str, offset: float, width: float, sill: float = 0.0,
    height: float = 204.0
) -> dict[str, Any]:
    return {
        "id": element_id,
        "kind": kind,
        "x_offset_cm": offset,
        "y_offset_cm": sill,
        "pos_x_cm": None,
        "pos_y_cm": None,
        "width_cm": width,
        "height_cm": height,
        "depth_cm": 4.0,
        "rotation_deg": 0.0,
        "furniture_type_id": None,
        "colors": {},
        "variant_params": {},
    }


def free_furniture(
    element_id: int, slug_id: int, centre: tuple[float, float], width: float, depth: float,
    height: float = 80.0, rotation: float = 0.0
) -> dict[str, Any]:
    return {
        "id": element_id,
        "kind": "furniture",
        "x_offset_cm": 0.0,
        "y_offset_cm": 0.0,
        "pos_x_cm": centre[0],
        "pos_y_cm": centre[1],
        "width_cm": width,
        "height_cm": height,
        "depth_cm": depth,
        "rotation_deg": rotation,
        "furniture_type_id": slug_id,
        "colors": {},
        "variant_params": {},
    }


def wall_furniture(
    element_id: int, slug_id: int, offset: float, sill: float, width: float, height: float,
    depth: float
) -> dict[str, Any]:
    return {
        "id": element_id,
        "kind": "furniture",
        "x_offset_cm": offset,
        "y_offset_cm": sill,
        "pos_x_cm": None,
        "pos_y_cm": None,
        "width_cm": width,
        "height_cm": height,
        "depth_cm": depth,
        "rotation_deg": 0.0,
        "furniture_type_id": slug_id,
        "colors": {},
        "variant_params": {},
    }


def catalog(*slugs: str) -> dict[int, dict[str, Any]]:
    return {
        index + 1: {
            "id": index + 1,
            "slug": slug,
            "color_slots": ["corps"],
            "parts": [
                {
                    "type": "box",
                    "rel_position": [0.5, 0.5, 0.5],
                    "rel_size": [1, 1, 1],
                    "color_slot": "corps",
                }
            ],
        }
        for index, slug in enumerate(slugs)
    }


def rectangular_room(
    *,
    name: str = "Séjour",
    width: float = 400.0,
    depth: float = 300.0,
    ceiling: float = 250.0,
    faces_elements: dict[str, list[dict[str, Any]]] | None = None,
    free: list[dict[str, Any]] | None = None,
    types: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Un plan d'une seule pièce rectangulaire, murs A/B/C/D dans le sens du contour."""
    by_label = faces_elements or {}
    corners = [(0.0, 0.0), (width, 0.0), (width, depth), (0.0, depth)]
    faces = [
        wall(
            label,
            corners[index],
            corners[(index + 1) % 4],
            100 + index,
            by_label.get(label),
        )
        for index, label in enumerate("ABCD")
    ]
    plan = {
        "project_id": 1,
        "rooms": [
            {
                "id": 10,
                "name": name,
                "wall_thickness_cm": 10.0,
                "ceiling_height_cm": ceiling,
                "polygon": [[x, y] for x, y in corners],
                "faces": faces,
                "elements": free or [],
            }
        ],
    }
    return build_scene_graph(plan, types or {})


# --- Fixtures de référence -----------------------------------------------------------------------


FIXTURE_NAMES = (
    "01_plan_conforme.json",
    "02_passage_trop_etroit.json",
    "03_debattement_qui_percute.json",
    "04_allege_dangereuse_et_ouvertures.json",
)


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_a_fixture_matches_its_hand_computed_report(name: str) -> None:
    fixture = load(name)
    report = inspect_scene(scene_of(fixture))

    assert report["counts"] == fixture["expected_counts"], report["anomalies"]
    assert len(report["warnings"]) == fixture["expected_warning_count"], report["warnings"]
    assert len(report["anomalies"]) == len(fixture["expected_anomalies"])

    for actual, expected in zip(report["anomalies"], fixture["expected_anomalies"], strict=True):
        for key, value in expected.items():
            numerique = isinstance(value, float) or (
                isinstance(value, list) and value and isinstance(value[0], int | float)
            )
            if numerique:
                assert actual[key] == pytest.approx(value, abs=1e-6), f"{name} / {key}"
            else:
                assert actual[key] == value, f"{name} / {key}"


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_a_fixture_rebuilds_its_inner_contour(name: str) -> None:
    """Le nu intérieur est le socle de toutes les règles géométriques : s'il glisse, tout glisse."""
    fixture = load(name)
    shell = build_shell(scene_of(fixture)["rooms"][0])

    assert shell is not None
    # Aplati : `pytest.approx` ne compare pas de structures imbriquées.
    obtenu = [value for vertex in shell.polygon for value in vertex]
    attendu = [value for vertex in fixture["expected_inner_contour"] for value in vertex]
    assert obtenu == pytest.approx(attendu, abs=1e-6)


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_the_inspection_is_reproducible(name: str) -> None:
    """Déterminisme : c'est l'exigence qui rend l'IA de ce produit testable par fixtures."""
    fixture = load(name)
    scene = scene_of(fixture)

    assert inspect_scene(scene) == inspect_scene(scene)


def test_every_anomaly_carries_what_the_panel_needs() -> None:
    """Un identifiant, une sévérité, un message chiffré et de quoi recentrer le plan.

    Sans ces quatre-là, le panneau d'inspection n'est plus qu'une liste de phrases : on ne peut ni
    filtrer, ni hiérarchiser, ni cliquer.
    """
    for name in FIXTURE_NAMES:
        for anomaly in inspect_scene(scene_of(load(name)))["anomalies"]:
            assert anomaly["rule_id"] and "." in anomaly["rule_id"]
            assert anomaly["severity"] in {"bloquant", "avertissement", "conseil"}
            assert anomaly["title"]
            assert anomaly["message"].endswith(".")
            assert anomaly["room_id"] is not None
            assert anomaly["focus"] is not None or anomaly["rule_id"].startswith("piece.")
            assert anomaly["element_ids"] or anomaly["rule_id"].startswith("piece.")


def test_a_scene_in_another_unit_is_refused_and_not_guessed() -> None:
    """Même refus que `build_takeoff` : se prononcer sur des millimètres pris pour des centimètres
    produirait un rapport faux et crédible, ce qui est pire que pas de rapport du tout."""
    with pytest.raises(ValueError, match="centimètres"):
        inspect_scene({"units": "mm", "rooms": []})


# --- Seuils paramétrables ------------------------------------------------------------------------


def test_the_thresholds_are_parameters_and_not_constants() -> None:
    """Une norme change, un usage varie selon le pays : c'est la raison d'être de `Thresholds`."""
    scene = rectangular_room(
        faces_elements={"A": [opening(1, "door_hinged", 150, 90)]},
        types=catalog("porte-battante"),
    )

    assert RULE_CEILING not in rule_ids(inspect_scene(scene))
    severe = inspect_scene(scene, Thresholds(ceiling_height_min_cm=260.0))
    assert RULE_CEILING in rule_ids(severe)

    ceiling = next(a for a in severe["anomalies"] if a["rule_id"] == RULE_CEILING)
    assert ceiling["measured_cm"] == 250.0
    assert ceiling["threshold_cm"] == 260.0
    assert "10 cm" in ceiling["message"]


def test_the_report_republishes_the_thresholds_it_applied() -> None:
    """Un rapport qui dit « insuffisant » sans dire par rapport à quoi n'est pas vérifiable."""
    report = inspect_scene(rectangular_room(), Thresholds(accessible=True))

    assert report["thresholds"]["accessible"] is True
    assert report["thresholds"]["passage_min_cm"] == 90.0
    assert report["thresholds"]["accessible_passage_min_cm"] == 120.0


def test_the_accessible_mode_adds_advice_and_never_downgrades_a_warning() -> None:
    """Un couloir de 100 cm passe en usage courant et pas en accessible : c'est un conseil.

    Un meuble de 200 x 90 au centre d'une pièce de 400 x 300 laisse 100 cm devant et derrière et
    95 cm de chaque côté : tout est au-dessus des 90 cm courants et en dessous des 120 cm d'un
    couloir accessible.
    """
    scene = rectangular_room(
        faces_elements={"A": [opening(1, "door_hinged", 150, 90)]},
        free=[free_furniture(2, 1, (200.0, 150.0), 200.0, 90.0)],
        types=catalog("lit"),
    )

    ordinaire = inspect_scene(scene)
    assert RULE_ACCESSIBLE_CORRIDOR not in rule_ids(ordinaire)
    assert RULE_PASSAGE not in rule_ids(ordinaire)

    accessible = inspect_scene(scene, Thresholds(accessible=True))
    conseils = [a for a in accessible["anomalies"] if a["rule_id"] == RULE_ACCESSIBLE_CORRIDOR]
    assert conseils, accessible["anomalies"]
    assert all(a["severity"] == "conseil" for a in conseils)


# --- Circulation ---------------------------------------------------------------------------------


def test_a_dead_end_is_not_a_narrow_passage() -> None:
    """Le réduit entre un meuble et le mur latéral n'est pas un couloir : il ne mène nulle part.

    C'est le contrôle qui empêche le moteur de devenir un générateur d'alertes — et c'est aussi
    celui qui a dicté la forme du portillon, pris au milieu de la zone où les deux obstacles se
    font réellement face plutôt qu'en un minimum arbitraire.
    """
    scene = rectangular_room(
        name="Cuisine",
        faces_elements={"D": [opening(1, "door_hinged", 100, 90)]},
        free=[free_furniture(2, 1, (200.0, 35.0), 240.0, 60.0)],
        types=catalog("meuble-bas"),
    )

    assert RULE_PASSAGE not in rule_ids(inspect_scene(scene))


def test_something_you_walk_under_never_narrows_a_passage() -> None:
    """Un meuble haut dont le dessous est à 2,10 m ne gêne personne ; le même à 1,40 m, si.

    Le caisson mural (100 de large, 35 de profondeur, adossé au mur A) avance jusqu'à y = 40 ; le
    meuble libre commence à y = 105. Il reste 65 cm : sous les 90 cm d'une circulation courante,
    donc un avertissement — mais seulement si le caisson compte comme obstacle.
    """

    def scene(sill: float) -> dict[str, Any]:
        return rectangular_room(
            faces_elements={
                "A": [
                    opening(1, "door_hinged", 280, 90),
                    wall_furniture(2, 1, 100.0, sill, 100.0, 35.0, 35.0),
                ]
            },
            free=[free_furniture(3, 2, (200.0, 155.0), 200.0, 100.0)],
            types=catalog("meuble-haut", "table"),
        )

    assert RULE_PASSAGE not in rule_ids(inspect_scene(scene(210.0)))
    assert RULE_PASSAGE in rule_ids(inspect_scene(scene(140.0)))


def test_a_switch_is_not_an_obstacle() -> None:
    """Moins de 5 cm de saillie : une prise ne rétrécit aucun couloir, et le dire serait du
    bruit."""
    scene = rectangular_room(
        faces_elements={
            "A": [
                opening(1, "door_hinged", 150, 90),
                wall_furniture(2, 1, 200.0, 110.0, 8.0, 8.0, 1.0),
            ]
        },
        free=[free_furniture(3, 2, (200.0, 200.0), 200.0, 195.0)],
        types=catalog("prise", "lit"),
    )

    assert RULE_PASSAGE not in rule_ids(inspect_scene(scene))


# --- Débattements --------------------------------------------------------------------------------


def test_a_door_facing_a_wall_too_close_cannot_open() -> None:
    """Couloir de 70 cm de profondeur, porte de 90 : l'arc traverse le mur d'en face."""
    scene = rectangular_room(
        depth=90.0,
        faces_elements={"A": [opening(1, "door_hinged", 150, 90)]},
        types=catalog("porte-battante"),
    )

    anomalies = [a for a in inspect_scene(scene)["anomalies"] if a["rule_id"] == RULE_DOOR_SWING]
    assert anomalies, rule_ids(inspect_scene(scene))
    assert anomalies[0]["severity"] == "bloquant"
    assert "mur C" in anomalies[0]["message"]


def test_a_single_free_hand_is_advice_and_not_an_anomaly() -> None:
    """Si un ferrage passe, le plan est réalisable : on impose la main, on ne condamne pas la porte.

    Le meuble est posé au fond du quart de disque gauche (x de 150 à 170, y de 90 à 120) : sa
    distance à la charnière gauche (150,5) vaut 85, moins que le vantail de 90, donc l'arc gauche
    le percute. Sa distance à la charnière droite (240,5) vaut au minimum 110, plus que 90 : l'arc
    droit est libre.
    """
    scene = rectangular_room(
        faces_elements={"A": [opening(1, "door_hinged", 150, 90)]},
        free=[free_furniture(2, 1, (160.0, 105.0), 20.0, 30.0)],
        types=catalog("colonne-de-rangement"),
    )

    identifiants = rule_ids(inspect_scene(scene))
    assert RULE_DOOR_HAND in identifiants
    assert RULE_DOOR_SWING not in identifiants


def test_two_doors_that_collide_whatever_their_hand_are_blocking() -> None:
    """Deux portes de 90 encadrant le même angle : les quatre ferrages possibles se percutent.

    La porte du mur A balaie x de 15 à 105 et y de 5 à 95 ; celle du mur D balaie x de 5 à 95 et
    y de 15 à 105. Les deux quarts de disque se recouvrent largement, quel que soit le tableau
    choisi comme charnière — et comme aucun des quatre ferrages n'est bloqué par ailleurs, c'est
    bien la collision entre les deux portes qui est signalée, et elle seule.
    """
    scene = rectangular_room(
        width=300.0,
        depth=300.0,
        faces_elements={
            "A": [opening(1, "door_hinged", 15, 90)],
            "D": [opening(2, "door_hinged", 195, 90)],
        },
        types=catalog("porte-battante"),
    )

    report = inspect_scene(scene)
    percussions = [a for a in report["anomalies"] if a["rule_id"] == RULE_DOOR_SWINGS_COLLIDE]
    assert percussions, rule_ids(report)
    assert percussions[0]["severity"] == "bloquant"
    assert set(percussions[0]["face_labels"]) == {"A", "D"}
    assert RULE_DOOR_SWING not in rule_ids(report)


# --- Mobilier ------------------------------------------------------------------------------------


def test_two_pieces_of_furniture_at_different_heights_do_not_overlap() -> None:
    """Une applique à 1,80 m et un canapé au sol partagent une emprise sans jamais se toucher.

    Ne comparer que les emprises ferait de tout meuble adossé un conflit avec ce qui est accroché
    au-dessus, c'est-à-dire du bruit sur la moitié des plans.
    """
    scene = rectangular_room(
        faces_elements={
            "A": [
                opening(1, "door_hinged", 300, 90),
                wall_furniture(2, 1, 100.0, 180.0, 30.0, 25.0, 12.0),
            ]
        },
        free=[free_furniture(3, 2, (115.0, 45.0), 200.0, 80.0, height=80.0)],
        types=catalog("applique", "canape"),
    )

    assert RULE_OVERLAP not in rule_ids(inspect_scene(scene))


def test_two_pieces_of_furniture_at_the_same_height_do_overlap() -> None:
    scene = rectangular_room(
        faces_elements={"A": [opening(1, "door_hinged", 300, 90)]},
        free=[
            free_furniture(2, 1, (150.0, 150.0), 100.0, 100.0),
            free_furniture(3, 1, (200.0, 150.0), 100.0, 100.0),
        ],
        types=catalog("commode"),
    )

    report = inspect_scene(scene)
    chevauchements = [a for a in report["anomalies"] if a["rule_id"] == RULE_OVERLAP]
    assert chevauchements, rule_ids(report)
    # Deux carrés de 100 dont les centres sont distants de 50 : ils se recouvrent sur 50 cm.
    assert chevauchements[0]["measured_cm"] == pytest.approx(50.0, abs=1e-6)


def test_furniture_pushed_flat_against_a_wall_is_not_going_through_it() -> None:
    """Le geste le plus courant du métier ne doit pas produire une anomalie.

    C'est la régression que la vague 3 a déjà payée une fois sur la validation d'encombrement.
    """
    scene = rectangular_room(
        faces_elements={"A": [opening(1, "door_hinged", 300, 90)]},
        free=[free_furniture(2, 1, (100.0, 35.0), 150.0, 60.0)],
        types=catalog("commode"),
    )

    assert RULE_THROUGH_WALL not in rule_ids(inspect_scene(scene))


def test_furniture_that_really_crosses_a_wall_says_by_how_much() -> None:
    scene = rectangular_room(
        faces_elements={"A": [opening(1, "door_hinged", 300, 90)]},
        free=[free_furniture(2, 1, (100.0, 20.0), 150.0, 60.0)],
        types=catalog("commode"),
    )

    report = inspect_scene(scene)
    debordements = [a for a in report["anomalies"] if a["rule_id"] == RULE_THROUGH_WALL]
    assert debordements, rule_ids(report)
    # Le meuble s'étend de y = -10 à y = 50 ; le nu intérieur du mur A est à y = 5, donc il
    # déborde de 15 cm.
    assert debordements[0]["measured_cm"] == pytest.approx(15.0, abs=1e-6)


# --- Pièce ---------------------------------------------------------------------------------------


def test_a_room_without_any_opening_is_blocking() -> None:
    report = inspect_scene(rectangular_room())

    assert RULE_NO_OPENING in rule_ids(report)
    assert report["counts"]["bloquant"] >= 1


def test_a_wet_room_without_a_water_point_is_reported_whatever_the_spelling() -> None:
    """L'apostrophe droite, l'apostrophe typographique et le tiret désignent la même pièce."""
    for name in ("Salle d'eau", "Salle d\u2019eau", "SALLE-D-EAU"):
        scene = rectangular_room(
            name=name,
            faces_elements={"A": [opening(1, "door_hinged", 150, 90)]},
            types=catalog("porte-battante"),
        )
        assert RULE_WET_ROOM in rule_ids(inspect_scene(scene)), name


def test_a_wet_room_with_a_water_point_is_left_alone() -> None:
    scene = rectangular_room(
        name="Salle d'eau",
        faces_elements={"A": [opening(1, "door_hinged", 150, 90)]},
        free=[free_furniture(2, 1, (300.0, 200.0), 60.0, 45.0, height=55.0)],
        types=catalog("meuble-sous-vasque"),
    )

    assert RULE_WET_ROOM not in rule_ids(inspect_scene(scene))


def test_a_kitchen_is_never_treated_as_a_wet_room() -> None:
    """Aucune recette du catalogue ne décrit un évier (spec §4.3) : la règle y serait un faux
    positif systématique, et une règle qui se trompe toujours est une règle qu'on désactive."""
    scene = rectangular_room(
        name="Cuisine",
        faces_elements={"A": [opening(1, "door_hinged", 150, 90)]},
        types=catalog("porte-battante"),
    )

    assert RULE_WET_ROOM not in rule_ids(inspect_scene(scene))


def test_a_room_whose_walls_do_not_close_loses_only_its_geometric_rules() -> None:
    """Un mur isolé n'a pas de contour, mais sa porte a toujours une largeur.

    Renoncer à tout serait aussi faux que d'inventer un contour : ce qui se juge mur par mur
    reste jugeable.
    """
    plan = {
        "project_id": 1,
        "rooms": [
            {
                "id": 10,
                "name": "Chambre",
                "wall_thickness_cm": 10.0,
                "ceiling_height_cm": 250.0,
                "polygon": CARRE,
                "faces": [wall("A", (0, 0), (400, 0), 100, [opening(1, "door_hinged", 150, 55)])],
                "elements": [],
            }
        ],
    }
    report = inspect_scene(build_scene_graph(plan, catalog("porte-battante")))

    assert len(report["warnings"]) == 1
    assert "contour" in report["warnings"][0]
    assert "porte.largeur_insuffisante" in rule_ids(report)
    assert RULE_PASSAGE not in rule_ids(report)


def test_the_anomalies_come_out_sorted_by_severity() -> None:
    fixture = load("04_allege_dangereuse_et_ouvertures.json")
    severites = [a["severity"] for a in inspect_scene(scene_of(fixture))["anomalies"]]

    rangs = {"bloquant": 0, "avertissement": 1, "conseil": 2}
    assert severites == sorted(severites, key=lambda value: rangs[value])


# --- Calepinage : sens de pose et première rangée -------------------------------------------------


def test_a_face_that_falls_right_keeps_its_grid_on_the_corner() -> None:
    """400 pour un module de 50 : huit unités entières, aucune coupe de rive, aucun décalage."""
    plan = plan_axis(400.0, 50.0, DEFAULT_LAYING)

    assert plan.start_offset_cm == 0.0
    assert plan.cells == 8
    assert plan.edge_cuts_cm == ()


def test_a_comfortable_edge_cut_is_left_alone() -> None:
    """430 pour 50 : le reste vaut 30, soit plus d'un tiers de 50. Une seule coupe, on n'y touche
    pas — recentrer en produirait deux pour rien."""
    plan = plan_axis(430.0, 50.0, DEFAULT_LAYING)

    assert plan.start_offset_cm == 0.0
    assert plan.cells == 9
    assert plan.edge_cuts_cm == (30.0,)


def test_a_starved_edge_cut_is_shared_between_the_two_sides() -> None:
    """410 pour 50 : le reste vaut 10, moins d'un tiers de 50.

    On recule la trame de (50 - 10) / 2 = 20, ce qui donne deux rives de (50 + 10) / 2 = 30. La
    lichette de 1 cm devient deux coupes confortables, et c'est exactement le geste du poseur.
    """
    plan = plan_axis(410.0, 50.0, DEFAULT_LAYING)

    assert plan.start_offset_cm == pytest.approx(-20.0)
    assert plan.cells == 9
    assert plan.edge_cuts_cm == pytest.approx((30.0, 30.0))
    assert plan.min_edge_cut_cm >= 50.0 / 3.0


@pytest.mark.parametrize("extent", [n / 2.0 for n in range(120, 1200, 7)])
def test_no_laying_ever_leaves_less_than_a_third_of_a_unit_at_the_edge(extent: float) -> None:
    """La règle métier, vérifiée sur 155 longueurs de face : jamais moins d'un tiers en rive."""
    plan = plan_axis(extent, 60.0, DEFAULT_LAYING)

    assert plan.min_edge_cut_cm >= 60.0 / 3.0 - 1e-9


def test_the_laying_direction_that_saves_cuts_wins() -> None:
    """Une unité de 60 x 30 sur un mur de 240 x 240 : posée en travers, la face tombe juste.

    Dans le sens déclaré (60 en largeur, 30 en hauteur) la face tombe juste aussi — 240 est
    multiple des deux — donc on prend un mur de 240 x 250 : 250 / 30 laisse 10 de reste, contre
    250 / 60 qui en laisse 10 également. On force donc l'écart par une face de 240 x 270 :
    270 / 30 = 9 rangs pile, alors que 270 / 60 = 4 rangs plus 30 de reste, soit une coupe par
    colonne.
    """
    node = {
        "kind": "wall",
        "face_label": "A",
        "face_id": 1,
        "length_cm": 240.0,
        "height_cm": 270.0,
        "holes": [],
        "covering": {"unit_width_cm": 60.0, "unit_height_cm": 30.0, "pattern": "straight"},
    }
    plan = plan_face_tiling(node)

    assert plan is not None
    assert plan["chosen"]["cut_units"] == 0
    assert plan["chosen"]["orientation"] == "declaree"
    assert plan["cuts_saved"] == 0

    pivote: dict[str, Any] = {
        **node,
        "covering": {"unit_width_cm": 30.0, "unit_height_cm": 60.0, "pattern": "straight"},
    }
    plan_pivote = plan_face_tiling(pivote)
    assert plan_pivote is not None
    assert plan_pivote["chosen"]["orientation"] == "pivotee"
    assert plan_pivote["chosen"]["cut_units"] == 0
    assert plan_pivote["cuts_saved"] > 0


def test_a_face_without_a_dimensioned_covering_has_no_laying_plan() -> None:
    """Une peinture n'a pas de calepinage, et ce n'est pas une anomalie."""
    node = {
        "kind": "wall",
        "face_label": "A",
        "face_id": 1,
        "length_cm": 400.0,
        "height_cm": 250.0,
        "holes": [],
        "covering": {"material": "peinture"},
    }

    assert plan_face_tiling(node) is None


def test_a_chevron_is_left_to_the_layer() -> None:
    """Sa trame n'est pas parallèle aux bords : lui inventer un sens de pose serait une invention.

    C'est exactement la frontière que `geometry/quantities.py` s'impose déjà.
    """
    node = {
        "kind": "wall",
        "face_label": "A",
        "face_id": 1,
        "length_cm": 400.0,
        "height_cm": 250.0,
        "holes": [],
        "covering": {"unit_width_cm": 60.0, "unit_height_cm": 30.0, "pattern": "chevron"},
    }

    assert plan_face_tiling(node) is None


def test_the_default_laying_agrees_with_the_takeoff_on_the_reference_wall() -> None:
    """Croisement : sur la fixture 09 du métré, la trame calée sur le coin doit donner le même
    décompte — 21 entières, 13 coupes, 6 avalées par les percements.

    Deux moteurs du même produit qui compteraient différemment vaudraient moins que zéro : le
    devis annoncerait un nombre de carreaux, le calepinage un autre.
    """
    fixture = json.loads(
        (Path(__file__).parent / "geometry" / "fixtures" / "09_metre_mur_deux_ouvertures.json")
        .read_text(encoding="utf-8")
    )
    scene = build_scene_graph(fixture["input"])
    node = next(n for n in scene["rooms"][0]["nodes"] if n["kind"] == "wall")

    metre = build_takeoff(scene)["rooms"][0]["faces"][0]["tiling"]
    calepinage = plan_face_tiling(node)

    assert calepinage is not None
    reference = calepinage["candidates"][0]
    assert reference["full_units"] == metre["full_units"] == 21
    assert reference["cut_units"] == metre["cut_units"] == 13
    assert reference["swallowed_units"] == 6


# --- Calepinage des plinthes ---------------------------------------------------------------------


def test_the_skirting_reuses_its_offcuts_from_one_wall_to_the_next() -> None:
    """Pièce de 300 x 220, murs de 10, une porte de 83 sur le mur A, barres de 240.

    Nu intérieur 290 x 210, donc quatre courses : A = 290 - 83 = 207, B = 210, C = 290, D = 210,
    soit 917 cm en tout.

    Pose mur après mur, chute réemployée tant qu'elle dépasse 30 :
    A : une barre coupée à 207, chute 33.
    B : la chute de 33 se pose telle quelle, reste 177, une barre coupée à 177, chute 63.
    C : la chute de 63 se pose telle quelle, reste 227, une barre coupée à 227, chute 13.
    D : 13 est sous le seuil de réemploi, une barre coupée à 210, chute 30.
    Total : 4 barres, 4 coupes, 2 chutes réemployées. Sans réemploi : 1 + 1 + 2 + 1 = 5 barres.
    Chute totale : 4 x 240 - 917 = 43 cm.
    """
    scene = rectangular_room(
        width=300.0,
        depth=220.0,
        faces_elements={"A": [opening(1, "door_hinged", 100, 83)]},
        types=catalog("porte-battante"),
    )
    plan = plan_room_skirting(scene["rooms"][0])

    assert plan is not None
    assert [run["length_cm"] for run in plan["runs"]] == pytest.approx([207.0, 210.0, 290.0, 210.0])
    assert plan["total_length_ml"] == pytest.approx(9.17)
    assert plan["bars"] == 4
    assert plan["cuts"] == 4
    assert plan["reused_offcuts"] == 2
    assert plan["bars_without_reuse"] == 5
    assert plan["bars_saved"] == 1
    assert plan["waste_ml"] == pytest.approx(0.43)


def test_the_bar_length_is_a_parameter() -> None:
    """Un fournisseur change, la longueur de barre avec lui."""
    scene = rectangular_room(width=300.0, depth=220.0)
    court = plan_room_skirting(scene["rooms"][0], LayingRules(skirting_bar_cm=200.0))
    long = plan_room_skirting(scene["rooms"][0], LayingRules(skirting_bar_cm=400.0))

    assert court is not None and long is not None
    assert court["bars"] > long["bars"]


def test_a_room_without_a_contour_has_no_skirting_plan() -> None:
    """Commander des plinthes sur un périmètre deviné, c'est livrer un chantier court de deux
    barres."""
    plan = {
        "project_id": 1,
        "rooms": [
            {
                "id": 10,
                "name": "Chambre",
                "wall_thickness_cm": 10.0,
                "ceiling_height_cm": 250.0,
                "polygon": CARRE,
                "faces": [wall("A", (0, 0), (400, 0), 100)],
                "elements": [],
            }
        ],
    }
    scene = build_scene_graph(plan)

    assert plan_room_skirting(scene["rooms"][0]) is None


def test_the_project_wide_laying_plan_sums_what_it_saves() -> None:
    scene = rectangular_room(
        width=410.0,
        depth=300.0,
        faces_elements={"A": [opening(1, "door_hinged", 150, 90)]},
        types=catalog("porte-battante"),
    )
    for node in scene["rooms"][0]["nodes"]:
        if node["kind"] == "wall":
            node["covering"] = {
                "unit_width_cm": 50.0,
                "unit_height_cm": 50.0,
                "pattern": "straight",
            }

    total = plan_project_tiling(scene)

    assert total["project_id"] == 1
    assert total["rooms"][0]["skirting"] is not None
    assert len(total["rooms"][0]["faces"]) == 4
    assert total["cuts_saved"] == sum(
        int(face["cuts_saved"]) for face in total["rooms"][0]["faces"]
    )


def test_the_project_wide_laying_plan_refuses_another_unit() -> None:
    with pytest.raises(ValueError, match="centimètres"):
        plan_project_tiling({"units": "mm", "rooms": []})


# --- Aménagement automatique ---------------------------------------------------------------------


def bathroom_scene(width: float = 300.0, depth: float = 220.0) -> dict[str, Any]:
    return rectangular_room(
        name="Salle de bain",
        width=width,
        depth=depth,
        faces_elements={"A": [opening(1, "door_hinged", 20, 83)]},
        types=catalog("porte-battante"),
    )


def test_the_layout_engine_returns_ranked_and_distinct_proposals() -> None:
    result = propose_layouts(bathroom_scene()["rooms"][0])

    assert result["program"] == "salle_de_bain"
    assert result["proposals"], result["warnings"]
    assert [p["rank"] for p in result["proposals"]] == list(
        range(1, len(result["proposals"]) + 1)
    )
    notes = [p["score"] for p in result["proposals"]]
    assert notes == sorted(notes, reverse=True)
    signatures = {
        tuple((item["pos_x_cm"], item["pos_y_cm"]) for item in p["items"])
        for p in result["proposals"]
    }
    assert len(signatures) == len(result["proposals"])


def test_the_layout_engine_is_deterministic() -> None:
    """Sans aléa, une proposition se rejoue — donc elle se teste, et elle se discute."""
    room = bathroom_scene()["rooms"][0]

    assert propose_layouts(room) == propose_layouts(room)


def test_every_proposed_item_stays_inside_the_room_and_touches_nothing() -> None:
    """Les contraintes dures sont dures : ce qui les viole n'est pas noté, il est écarté."""
    from app.intelligence.ergonomy import rectangle

    room = bathroom_scene()["rooms"][0]
    shell = build_shell(room)
    assert shell is not None

    for proposal in propose_layouts(room)["proposals"]:
        emprises = [
            rectangle(
                (item["pos_x_cm"], item["pos_y_cm"]),
                item["width_cm"],
                item["depth_cm"],
                item["rotation_deg"],
            )
            for item in proposal["items"]
        ]
        for emprise in emprises:
            for corner in emprise:
                assert point_in_polygon(shell.polygon, corner) or min(
                    math.hypot(corner[0] - vertex[0], corner[1] - vertex[1])
                    for vertex in shell.polygon
                ) < 500.0
        for index, first in enumerate(emprises):
            for second in emprises[index + 1 :]:
                assert overlap_depth(first, second) <= 1.0


def test_a_proposed_layout_passes_the_conformity_check_it_was_scored_with() -> None:
    """Le croisement le plus utile du lot : ce que l'aménagement propose, le contrôle l'accepte.

    Un moteur qui produirait des implantations que le contrôle de conformité refuse ensuite serait
    pire qu'inutile — il ferait perdre confiance dans les deux.
    """
    scene = bathroom_scene()
    room = scene["rooms"][0]
    proposal = propose_layouts(room)["proposals"][0]

    types = catalog(*(item["slug"] for item in proposal["items"]))
    slug_ids = {entry["slug"]: identifier for identifier, entry in types.items()}
    meubles = [
        free_furniture(
            500 + index,
            slug_ids[item["slug"]],
            (item["pos_x_cm"], item["pos_y_cm"]),
            item["width_cm"],
            item["depth_cm"],
            height=item["height_cm"],
            rotation=item["rotation_deg"],
        )
        for index, item in enumerate(proposal["items"])
    ]
    pose = rectangular_room(
        name="Salle de bain",
        width=300.0,
        depth=220.0,
        faces_elements={"A": [opening(1, "door_hinged", 20, 83)]},
        free=meubles,
        types={**catalog("porte-battante"), **types},
    )

    report = inspect_scene(pose)
    bloquants = [
        anomaly
        for anomaly in report["anomalies"]
        if anomaly["severity"] == Severity.BLOCKING.value
        and anomaly["rule_id"] in {RULE_PASSAGE, RULE_OVERLAP, RULE_THROUGH_WALL, RULE_DOOR_SWING}
    ]
    assert bloquants == [], bloquants


def test_the_layout_score_is_readable_and_weighted() -> None:
    """Le score doit être discutable, donc décomposé et pondéré par des poids publiés."""
    result = propose_layouts(bathroom_scene()["rooms"][0])
    proposal = result["proposals"][0]

    assert set(result["weights"]) == {"degagements", "circulation", "adjacences", "compacite"}
    assert sum(result["weights"].values()) == pytest.approx(1.0)
    assert set(proposal["breakdown"]) == set(result["weights"])
    assert proposal["score"] == pytest.approx(
        sum(result["weights"][key] * value for key, value in proposal["breakdown"].items()),
        abs=1e-3,
    )


def test_a_bed_never_ends_up_in_front_of_a_window() -> None:
    """« Une tête de lit contre un mur plein et jamais devant une fenêtre » : contrainte dure.

    Le mur A porte la porte, le mur C une fenêtre de 200 centrée. Aucun lit proposé ne doit
    s'adosser à un mur percé.
    """
    scene = rectangular_room(
        name="Chambre",
        width=400.0,
        depth=350.0,
        faces_elements={
            "A": [opening(1, "door_hinged", 20, 83)],
            "C": [opening(2, "window", 100, 200, sill=100.0, height=110.0)],
        },
        types=catalog("porte-battante", "fenetre"),
    )
    result = propose_layouts(scene["rooms"][0])

    assert result["proposals"], result["warnings"]
    for proposal in result["proposals"]:
        lits = [item for item in proposal["items"] if item["slug"] == "lit"]
        assert lits
        assert all(lit["against_face_label"] != "C" for lit in lits)


def test_a_room_too_small_says_so_instead_of_pretending() -> None:
    result = propose_layouts(bathroom_scene(width=150.0, depth=120.0)["rooms"][0])

    assert result["proposals"] == []
    assert result["warnings"]
    assert "trop petite" in result["warnings"][0]


def test_an_unknown_program_is_named_and_not_guessed() -> None:
    result = propose_layouts(bathroom_scene()["rooms"][0], program="atelier")

    assert result["proposals"] == []
    assert "atelier" in result["warnings"][0]


def test_the_program_is_deduced_from_the_room_name() -> None:
    for name, expected in (
        ("Salle de bain", "salle_de_bain"),
        ("Salle d'eau", "salle_de_bain"),
        ("Cuisine", "cuisine"),
        ("Chambre parentale", "chambre"),
        ("Débarras", "inconnu"),
    ):
        scene = rectangular_room(
            name=name,
            faces_elements={"A": [opening(1, "door_hinged", 20, 83)]},
            types=catalog("porte-battante"),
        )
        assert propose_layouts(scene["rooms"][0])["program"] == expected, name


def test_existing_furniture_is_never_covered_by_a_proposal() -> None:
    """Un moteur qui poserait un WC sur la baignoire déjà relevée n'aurait aucun usage."""
    from app.intelligence.ergonomy import rectangle

    scene = rectangular_room(
        name="Salle de bain",
        width=300.0,
        depth=220.0,
        faces_elements={"A": [opening(1, "door_hinged", 20, 83)]},
        free=[free_furniture(9, 1, (110.0, 172.5), 170.0, 75.0, height=55.0)],
        types={1: catalog("baignoire")[1], 2: catalog("porte-battante")[1]},
    )
    room = scene["rooms"][0]
    baignoire = next(
        node_footprint(node)
        for node in room["nodes"]
        if node["kind"] == "furniture" and node.get("furniture_type_slug") == "baignoire"
    )

    for proposal in propose_layouts(room)["proposals"]:
        for item in proposal["items"]:
            emprise = rectangle(
                (item["pos_x_cm"], item["pos_y_cm"]),
                item["width_cm"],
                item["depth_cm"],
                item["rotation_deg"],
            )
            assert overlap_depth(emprise, baignoire) <= 1.0


# --- API ------------------------------------------------------------------------------------------


async def build_project(client: AsyncClient) -> dict[str, Any]:
    project = (await client.post("/api/projects", json={"name": "Chantier"})).json()
    room = (
        await client.post(
            f"/api/projects/{project['id']}/rooms",
            json={"name": "Salle de bain", "polygon": [[0, 0], [300, 0], [300, 220], [0, 220]]},
        )
    ).json()
    face = room["faces"][0]
    await client.post(
        f"/api/faces/{face['id']}/elements",
        json={"kind": "door_hinged", "x_offset_cm": 20, "y_offset_cm": 0,
              "width_cm": 83, "height_cm": 204},
    )
    return {"project": project, "room": room}


async def organization_of(client: AsyncClient) -> int:
    """Identifiant de l'organisation personnelle du compte."""
    return int((await client.get("/api/organizations")).json()[0]["id"])


async def test_the_inspection_route_returns_a_typed_report(auth_client: AsyncClient) -> None:
    built = await build_project(auth_client)

    response = await auth_client.get(f"/api/projects/{built['project']['id']}/inspection")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project_id"] == built["project"]["id"]
    assert body["thresholds"]["passage_min_cm"] == 90.0
    assert body["thresholds"]["accessible"] is False
    assert set(body["counts"]) == {"bloquant", "avertissement", "conseil"}
    assert body["rooms"][0]["room_id"] == built["room"]["id"]
    # La pièce est déclarée humide et ne porte aucun point d'eau.
    assert "piece.humide_sans_point_d_eau" in [a["rule_id"] for a in body["anomalies"]]


async def test_the_inspection_route_honours_the_accessible_switch(
    auth_client: AsyncClient,
) -> None:
    built = await build_project(auth_client)

    body = (
        await auth_client.get(
            f"/api/projects/{built['project']['id']}/inspection", params={"accessible": "true"}
        )
    ).json()

    assert body["thresholds"]["accessible"] is True


async def test_the_laying_plan_route_says_how_to_lay_and_not_only_how_much(
    auth_client: AsyncClient,
) -> None:
    """Le métré dit combien commander ; cette route dit comment poser.

    Le mur reçoit un carrelage de 50 x 50 pour que la question du calage se pose réellement — sans
    dimensions d'unité, une face n'a aucun calepinage et ce n'est pas une anomalie.
    """
    built = await build_project(auth_client)
    face = built["room"]["faces"][0]
    covering = await auth_client.patch(
        f"/api/faces/{face['id']}",
        json={"covering": {"unit_width_cm": 50, "unit_height_cm": 50, "pattern": "straight"}},
    )
    assert covering.status_code == 200, covering.text

    response = await auth_client.get(f"/api/projects/{built['project']['id']}/laying-plan")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project_id"] == built["project"]["id"]
    piece = body["rooms"][0]
    assert piece["skirting"]["bars"] >= 1
    calepine = [entry for entry in piece["faces"] if entry["face_id"] == face["id"]]
    assert calepine, piece["faces"]
    assert calepine[0]["chosen"]["full_units"] > 0
    assert len(calepine[0]["candidates"]) == 4


async def test_the_layout_route_proposes_without_writing_anything(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """Aucune ligne créée : le client choisit, le moteur propose."""
    built = await build_project(auth_client)
    # L'aménagement automatique est une fonctionnalité du palier Entreprise (A14), et l'essai
    # n'offre qu'Artisan : le mur a son propre test, celui-ci porte sur l'absence d'écriture.
    await subscribe(session, await organization_of(auth_client), PLAN_BUSINESS)
    before = (await auth_client.get(f"/api/rooms/{built['room']['id']}")).json()

    response = await auth_client.post(
        f"/api/rooms/{built['room']['id']}/layouts", json={"count": 2}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["room_id"] == built["room"]["id"]
    assert body["program"] == "salle_de_bain"
    assert len(body["proposals"]) <= 2
    for proposal in body["proposals"]:
        for item in proposal["items"]:
            assert item["slug"]
            assert -100_000 <= item["pos_x_cm"] <= 100_000

    after = (await auth_client.get(f"/api/rooms/{built['room']['id']}")).json()
    assert after == before


async def test_the_layout_route_refuses_an_unknown_field(auth_client: AsyncClient) -> None:
    """`extra="forbid"` : aucun seuil ne rentre par le corps de la requête.

    Les rendre pilotables par le client transformerait un contrôle métier en paramètre
    d'affichage — il suffirait de demander 10 cm de passage pour rendre conforme un plan invivable.
    """
    built = await build_project(auth_client)

    response = await auth_client.post(
        f"/api/rooms/{built['room']['id']}/layouts", json={"passage_min_cm": 10}
    )

    assert response.status_code == 422, response.text


async def test_a_viewer_reads_the_inspection_but_never_asks_for_a_layout(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """Le rôle exigé pour l'aménagement est `editor` : c'est le calcul le plus cher de l'API, et
    une proposition n'a de sens que pour quelqu'un qui peut ensuite modifier le plan.

    L'entreprise est au palier Entreprise : c'est le seul qui ouvre à la fois le second siège et
    l'aménagement automatique (A14). Sans lui, le refus observé serait celui du mur de paiement, et
    le test cesserait de dire quoi que ce soit sur les rôles.
    """
    from tests.test_permissions_locataire import logged_in

    built = await build_project(auth_client)
    organization_id = (await auth_client.get("/api/organizations")).json()[0]["id"]
    await subscribe(session, int(organization_id), PLAN_BUSINESS)

    async with logged_in("lecteur-ia@exemple.fr") as lecteur:
        invitation = await auth_client.post(
            f"/api/organizations/{organization_id}/invitations",
            json={"email": "lecteur-ia@exemple.fr", "role": "viewer"},
        )
        accepted = await lecteur.post(
            "/api/invitations/accept", json={"token": invitation.json()["token"]}
        )
        assert accepted.status_code == 200, accepted.text

        lecture = await lecteur.get(f"/api/projects/{built['project']['id']}/inspection")
        assert lecture.status_code == 200, lecture.text

        refus = await lecteur.post(f"/api/rooms/{built['room']['id']}/layouts", json={})
        assert refus.status_code == 403, refus.text


async def test_an_analysis_is_counted_once_per_version_and_not_once_per_click(
    auth_client: AsyncClient,
    session: AsyncSession,
) -> None:
    """`ai_runs` était posée, affichée sur la page compte, et jamais incrémentée.

    Le comptage porte la **version du plan** : les trois moteurs sont déterministes, deux appels
    sur la même version rendent le même octet et ne sont donc qu'une seule analyse. Sans cette
    clé, rafraîchir le panneau d'inspection gonflerait la métrique jusqu'à la rendre incomparable
    d'un compte à l'autre.
    """
    from app.models.billing_plan import UsageMetric
    from app.services.quotas import counter_value, resolve_entitlement

    built = await build_project(auth_client)
    project_id = built["project"]["id"]
    organization_id = (await auth_client.get("/api/organizations")).json()[0]["id"]

    async def compteur() -> int:
        session.expire_all()
        entitlement = await resolve_entitlement(session, organization_id)
        return await counter_value(
            session,
            organization_id=organization_id,
            metric=UsageMetric.AI_RUNS,
            period_start=entitlement.period_start,
        )

    assert await compteur() == 0

    assert (await auth_client.get(f"/api/projects/{project_id}/inspection")).status_code == 200
    assert (await auth_client.get(f"/api/projects/{project_id}/inspection")).status_code == 200
    assert await compteur() == 1

    # Un barème différent est une autre analyse, pas un rejeu de la précédente.
    assert (
        await auth_client.get(
            f"/api/projects/{project_id}/inspection", params={"accessible": "true"}
        )
    ).status_code == 200
    assert await compteur() == 2

    # Le calepinage est un troisième moteur : il se compte à part.
    assert (await auth_client.get(f"/api/projects/{project_id}/laying-plan")).status_code == 200
    assert await compteur() == 3
