"""Le métré confronté à ses fixtures de référence.

Mêmes règles que `test_scene_graph.py` : les valeurs attendues des fixtures 07 à 10 ont été
calculées à la main, à partir du plan, et font foi (`CLAUDE.md`). Le champ `reasoning` de chaque
fixture rejoue le calcul sans relire le code — c'est lui qui distingue une valeur dérivée d'une
valeur recopiée depuis une sortie de programme.

L'entrée de ces fixtures est un **plan**, pas un scene graph : le test construit d'abord le scene
graph, puis le métré. Un métré juste sur un scene graph inventé ne prouverait rien.
"""

import json
import math
from pathlib import Path
from typing import Any

import pytest

from app.geometry.quantities import WASTE_RATIO_BY_PATTERN, build_takeoff
from app.geometry.scene import build_scene_graph

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return payload


def takeoff_of(fixture: dict[str, Any]) -> dict[str, Any]:
    return build_takeoff(build_scene_graph(fixture["input"]))


def assert_matches(actual: Any, expected: Any, path: str = "", *, strict_keys: bool = True) -> None:
    """Comparaison récursive avec un chemin lisible en cas d'écart.

    `strict_keys` compare aussi l'ensemble des clés : sans ça, un champ ajouté ou renommé par le
    code passerait inaperçu et la fixture ne décrirait plus qu'un sous-ensemble du contrat.
    """
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path} : dict attendu, reçu {type(actual).__name__}"
        if strict_keys:
            unexpected = sorted(set(actual) - set(expected))
            assert not unexpected, f"{path} : champs non décrits par la fixture : {unexpected}"
        for key, value in expected.items():
            assert key in actual, f"{path}.{key} absent du résultat"
            assert_matches(actual[key], value, f"{path}.{key}", strict_keys=strict_keys)
    elif isinstance(expected, list):
        assert isinstance(actual, list), f"{path} : liste attendue, reçu {type(actual).__name__}"
        assert len(actual) == len(expected), (
            f"{path} : {len(actual)} éléments, {len(expected)} attendus"
        )
        for index, value in enumerate(expected):
            assert_matches(actual[index], value, f"{path}[{index}]", strict_keys=strict_keys)
    elif isinstance(expected, float):
        assert actual is not None, f"{path} : valeur absente, {expected} attendu"
        assert actual == pytest.approx(expected, abs=1e-6), f"{path} : {actual} ≠ {expected}"
    else:
        assert actual == expected, f"{path} : {actual!r} ≠ {expected!r}"


# --- Fixture 07 : pièce rectangulaire simple -----------------------------------------------------


def test_a_rectangular_room_matches_its_reference_takeoff() -> None:
    fixture = load("07_metre_piece_rectangulaire.json")

    assert_matches(takeoff_of(fixture), fixture["expected"], "metre")


def test_the_takeoff_never_measures_the_wall_centre_line_area() -> None:
    """`floor_area_cm2` est l'aire de la ligne médiane des murs : la facturer, c'est un litige.

    Sur la pièce de référence, l'écart est de 6.1 % — 120000 cm² annoncés pour 113100 réels. Le
    métré n'expose aucune aire de ligne médiane, pour qu'aucun appelant ne puisse s'y tromper.
    """
    fixture = load("07_metre_piece_rectangulaire.json")
    scene = build_scene_graph(fixture["input"])
    room = scene["rooms"][0]

    assert room["floor_area_cm2"] == fixture["expected_median_floor_area_cm2"]
    overestimate = room["floor_area_cm2"] / room["net_floor_area_cm2"] - 1.0
    assert overestimate == pytest.approx(fixture["expected_median_overestimate_ratio"], abs=1e-4)

    measured = build_takeoff(scene)["rooms"][0]
    assert measured["floor_area_m2"] == pytest.approx(room["net_floor_area_cm2"] / 10_000)
    assert measured["floor_area_m2"] != pytest.approx(room["floor_area_cm2"] / 10_000)


def test_a_room_without_a_net_floor_area_is_reported_and_not_guessed() -> None:
    """Se replier sur `floor_area_cm2` surfacturerait de 6 à 20 % sans que rien ne le signale."""
    fixture = load("07_metre_piece_rectangulaire.json")
    scene = build_scene_graph(fixture["input"])
    del scene["rooms"][0]["net_floor_area_cm2"]

    measured = build_takeoff(scene)["rooms"][0]

    assert measured["floor_area_m2"] is None
    assert measured["ceiling_area_m2"] is None
    assert measured["volume_m3"] is None
    assert [face["net_area_m2"] for face in measured["faces"] if face["kind"] == "floor"] == [None]
    assert any("net_floor_area_cm2" in message for message in measured["warnings"])
    # Les murs, eux, restent chiffrables : ils ne dépendent pas de la surface au sol.
    assert measured["wall_net_area_m2"] == 35.0


def test_the_skirting_is_measured_on_the_inner_face_and_not_on_the_centre_line() -> None:
    """1400 cm sur l'axe des murs, 1360 au nu intérieur : 2.9 % de plinthe facturée en trop."""
    fixture = load("07_metre_piece_rectangulaire.json")

    measured = takeoff_of(fixture)["rooms"][0]

    assert measured["perimeter_ml"] == 14.0
    assert measured["net_perimeter_ml"] == 13.6
    assert measured["skirting_ml"] == 13.6
    assert measured["cornice_ml"] == 13.6


def test_a_painted_face_has_no_tiling_and_that_is_not_an_anomaly() -> None:
    fixture = load("07_metre_piece_rectangulaire.json")

    measured = takeoff_of(fixture)["rooms"][0]

    assert [face["tiling"] for face in measured["faces"] if face["material"] == "peinture"] == [
        None
    ] * 5
    assert measured["warnings"] == []


# --- Fixture 08 : pièce en L ---------------------------------------------------------------------


def test_an_l_shaped_room_matches_its_reference_takeoff() -> None:
    fixture = load("08_metre_piece_en_L.json")

    measured = takeoff_of(fixture)["rooms"][0]

    assert_matches(
        {key: measured[key] for key in fixture["expected_room"]},
        fixture["expected_room"],
        "piece",
    )
    assert_matches(measured["faces"], fixture["expected_faces"], "faces")
    assert_matches(measured["coverings"], fixture["expected_coverings"], "revetements")


def test_a_door_shortens_the_skirting_and_a_window_does_not() -> None:
    """Le critère est la hauteur du bas du percement, pas la nature déclarée de l'ouverture.

    Cette nature n'apparaît dans le scene graph que si le catalogue de menuiseries a été fourni,
    alors que le trou, lui, y est toujours : s'y fier ferait facturer la plinthe sur toute la
    largeur des portes dès qu'un appelant construit la scène sans catalogue.
    """
    fixture = load("08_metre_piece_en_L.json")

    measured = takeoff_of(fixture)["rooms"][0]
    faces = {face["face_label"]: face for face in measured["faces"]}

    assert faces["A"]["door_count"] == 1
    assert faces["A"]["skirting_deduction_ml"] == 0.9
    assert faces["B"]["window_count"] == 1
    assert faces["B"]["skirting_deduction_ml"] == 0.0
    assert measured["net_perimeter_ml"] == 21.6
    assert measured["skirting_ml"] == 20.7
    assert measured["cornice_ml"] == 21.6


def test_a_non_rectangular_floor_declines_to_count_whole_units_and_cuts() -> None:
    """Zéro coupe sur un sol en L serait un mensonge : l'inconnu se dit, il ne s'invente pas."""
    fixture = load("08_metre_piece_en_L.json")

    floor = next(
        face for face in takeoff_of(fixture)["rooms"][0]["faces"] if face["kind"] == "floor"
    )

    assert floor["tiling"]["full_units"] is None
    assert floor["tiling"]["cut_units"] is None
    # La commande, elle, reste chiffrable : elle ne dépend que de l'aire et du taux de chute.
    assert floor["tiling"]["units_total"] == 51


# --- Fixture 09 : mur portant deux ouvertures ----------------------------------------------------


def test_a_wall_with_two_openings_matches_its_reference_takeoff() -> None:
    fixture = load("09_metre_mur_deux_ouvertures.json")

    measured = takeoff_of(fixture)["rooms"][0]

    assert_matches(
        {key: measured[key] for key in fixture["expected_room"]},
        fixture["expected_room"],
        "piece",
    )
    assert_matches(measured["faces"], fixture["expected_faces"], "faces")


def test_the_openings_swallow_whole_units_and_turn_others_into_cuts() -> None:
    """Une trame de 40 positions dont 6 disparaissent dans les percements et 13 deviennent des
    coupes : compter les 40 comme des carreaux entiers surfacturerait la pose."""
    fixture = load("09_metre_mur_deux_ouvertures.json")

    tiling = takeoff_of(fixture)["rooms"][0]["faces"][0]["tiling"]

    positions = fixture["expected_trame_positions"]
    assert tiling["full_units"] + tiling["cut_units"] == (
        positions - fixture["expected_positions_swallowed_by_openings"]
    )


def test_an_open_wall_run_declines_to_measure_a_skirting() -> None:
    """Un mur isolé ne referme aucun contour : lui inventer un périmètre ferait facturer une
    plinthe qui n'existe pas."""
    fixture = load("09_metre_mur_deux_ouvertures.json")

    measured = takeoff_of(fixture)["rooms"][0]

    assert measured["net_perimeter_ml"] is None
    assert measured["skirting_ml"] is None
    assert measured["cornice_ml"] is None
    assert len(measured["warnings"]) == fixture["expected_warning_count"]
    assert "ne se referment pas" in measured["warnings"][0]


# --- Fixture 10 : calepinage par motif de pose ---------------------------------------------------


def _patterned(fixture: dict[str, Any], pattern: str) -> dict[str, Any]:
    """Le plan de la fixture, dont le seul revêtement change de motif de pose."""
    source: dict[str, Any] = json.loads(json.dumps(fixture["input"]))
    source["rooms"][0]["faces"][0]["covering"]["pattern"] = pattern
    return source


def _with_pattern(fixture: dict[str, Any], pattern: str) -> dict[str, Any]:
    scene = build_scene_graph(_patterned(fixture, pattern))
    face: dict[str, Any] = build_takeoff(scene)["rooms"][0]["faces"][0]
    return face


def test_each_laying_pattern_matches_its_reference_tiling() -> None:
    fixture = load("10_metre_calepinage_motifs.json")

    for pattern, expected in fixture["expected_by_pattern"].items():
        face = _with_pattern(fixture, pattern)

        assert face["net_area_m2"] == fixture["expected_net_area_m2"]
        assert_matches(face["tiling"], expected, f"calepinage[{pattern}]")


def test_the_waste_ratios_keep_the_orders_of_magnitude_of_the_trade() -> None:
    """Pose droite autour de 8 %, diagonale autour de 12 %, chevron et bâton rompu autour de 15 %.

    C'est le chiffre qui rend le devis crédible auprès d'un homme de métier
    (`docs/strategie-produit.md` §3.8) : s'il dérive, la fonctionnalité perd sa raison d'être.
    """
    fixture = load("10_metre_calepinage_motifs.json")

    assert fixture["expected_waste_ratio_orders_of_magnitude"] == WASTE_RATIO_BY_PATTERN
    assert WASTE_RATIO_BY_PATTERN["chevron"] > WASTE_RATIO_BY_PATTERN["straight"]
    assert WASTE_RATIO_BY_PATTERN["herringbone"] == WASTE_RATIO_BY_PATTERN["chevron"]


def test_an_unknown_pattern_falls_back_to_the_straight_provision_and_says_so() -> None:
    fixture = load("10_metre_calepinage_motifs.json")

    measured = build_takeoff(build_scene_graph(_patterned(fixture, "spirale")))["rooms"][0]

    assert measured["faces"][0]["tiling"]["waste_ratio"] == WASTE_RATIO_BY_PATTERN["straight"]
    assert measured["faces"][0]["tiling"]["pattern"] == "spirale"
    assert any("spirale" in message for message in measured["warnings"])


def test_a_staggered_pattern_creates_cuts_where_a_straight_one_creates_none() -> None:
    """La face tombe juste (8 colonnes, 5 rangs) : les 4 coupes viennent du décalage, pas d'elle."""
    fixture = load("10_metre_calepinage_motifs.json")

    straight = _with_pattern(fixture, "straight")["tiling"]
    staggered = _with_pattern(fixture, "staggered")["tiling"]

    assert (straight["full_units"], straight["cut_units"]) == (40, 0)
    assert (staggered["full_units"], staggered["cut_units"]) == (38, 4)


# --- Contrat de sortie ---------------------------------------------------------------------------


def test_the_ordered_quantity_never_falls_below_what_is_actually_laid() -> None:
    """Commander moins d'unités qu'on n'en pose entières serait un devis intenable."""
    for name in (
        "07_metre_piece_rectangulaire.json",
        "08_metre_piece_en_L.json",
        "09_metre_mur_deux_ouvertures.json",
    ):
        measured = takeoff_of(load(name))
        for room in measured["rooms"]:
            for face in room["faces"]:
                tiling = face["tiling"]
                if tiling is None or tiling["full_units"] is None:
                    continue
                assert tiling["units_total"] >= tiling["full_units"], (
                    f"{name} / face {face['face_label']}"
                )


def test_the_takeoff_is_expressed_in_quote_units_and_never_in_square_centimetres() -> None:
    """La conversion se fait ici, une fois : un appelant qui reçoit des cm² finira par les
    facturer tels quels."""
    fixture = load("07_metre_piece_rectangulaire.json")

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                assert not key.endswith("_cm2"), f"{path}.{key} est en cm²"
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    measured = takeoff_of(fixture)
    walk(measured, "metre")
    assert measured["units"] == {"area": "m2", "length": "ml", "volume": "m3"}


def test_a_scene_graph_that_is_not_in_centimetres_is_refused() -> None:
    """Toutes les conversions en dépendent : livrer des m² faux serait pire que ne rien dire."""
    fixture = load("07_metre_piece_rectangulaire.json")
    scene = build_scene_graph(fixture["input"])
    scene["units"] = "mm"

    with pytest.raises(ValueError, match="centimètres"):
        build_takeoff(scene)


def test_a_covering_that_declares_only_one_unit_dimension_is_reported() -> None:
    """Une saisie inachevée : on la signale plutôt que de deviner la seconde dimension."""
    fixture = load("07_metre_piece_rectangulaire.json")
    source = json.loads(json.dumps(fixture["input"]))
    del source["rooms"][0]["faces"][4]["covering"]["unit_height_cm"]

    measured = build_takeoff(build_scene_graph(source))["rooms"][0]

    assert next(face for face in measured["faces"] if face["kind"] == "floor")["tiling"] is None
    assert any("unit_height_cm" in message for message in measured["warnings"])


def test_identical_covering_references_are_grouped_into_a_single_order_line() -> None:
    """Une commande de matériaux se lit par référence, pas par face."""
    fixture = load("07_metre_piece_rectangulaire.json")
    source = json.loads(json.dumps(fixture["input"]))
    tiles = {
        "material": "faience",
        "unit_width_cm": 50,
        "unit_height_cm": 50,
        "pattern": "straight",
    }
    for index in (0, 2):  # murs A et C, 400 x 250 chacun
        source["rooms"][0]["faces"][index]["covering"] = dict(tiles)

    coverings = build_takeoff(build_scene_graph(source))["rooms"][0]["coverings"]

    faience = next(group for group in coverings if group["material"] == "faience")
    assert faience["net_area_m2"] == 20.0
    # 10 m² par mur, 8 % de chute, unité de 0.25 m² : ceil(10.8 / 0.25) = 44 par mur.
    assert faience["units_total"] == 88
    assert faience["full_units"] == 80
    assert len(coverings) == 2


def test_the_project_totals_add_up_the_rooms() -> None:
    fixture = load("07_metre_piece_rectangulaire.json")
    source = json.loads(json.dumps(fixture["input"]))
    second = json.loads(json.dumps(source["rooms"][0]))
    second["id"] = 71
    second["name"] = "Chambre"
    source["rooms"].append(second)

    totals = build_takeoff(build_scene_graph(source))["totals"]

    assert totals["room_count"] == 2
    assert totals["floor_area_m2"] == 22.62
    assert totals["volume_m3"] == 56.55
    assert totals["wall_net_area_m2"] == 70.0
    assert totals["skirting_ml"] == 27.2
    assert totals["coverings"][0]["units_total"] == 98


def test_the_takeoff_is_json_serialisable_and_stable() -> None:
    """Un métré part dans un PDF et dans une ligne de devis : il doit être sérialisable tel quel."""
    fixture = load("08_metre_piece_en_L.json")
    scene = build_scene_graph(fixture["input"])

    first = json.dumps(build_takeoff(scene), sort_keys=True, allow_nan=False)
    second = json.dumps(build_takeoff(scene), sort_keys=True, allow_nan=False)

    assert first == second
    assert not any(math.isnan(value) for value in _numbers(json.loads(first)))


def _numbers(node: Any) -> list[float]:
    if isinstance(node, dict):
        return [value for child in node.values() for value in _numbers(child)]
    if isinstance(node, list):
        return [value for child in node for value in _numbers(child)]
    if isinstance(node, bool):
        return []
    return [float(node)] if isinstance(node, int | float) else []


def test_a_unit_far_too_small_for_its_face_is_reported_rather_than_enumerated() -> None:
    """Une mosaïque de 1 cm sur un mur de 10 m fait 300000 positions : les dénombrer coûterait
    plus cher que tout le reste du métré, et le décompte n'apprendrait rien à personne."""
    measured = build_takeoff(
        build_scene_graph(
            {
                "project_id": 11,
                "rooms": [
                    {
                        "id": 110,
                        "name": "Piscine",
                        "wall_thickness_cm": 10.0,
                        "ceiling_height_cm": 300.0,
                        "polygon": [[0, 0], [1000, 0], [1000, 500], [0, 500]],
                        "faces": [
                            {
                                "id": 1100,
                                "label": "A",
                                "kind": "wall",
                                "start_x_cm": 0,
                                "start_y_cm": 0,
                                "end_x_cm": 1000,
                                "end_y_cm": 0,
                                "covering": {
                                    "material": "mosaique",
                                    "unit_width_cm": 1,
                                    "unit_height_cm": 1,
                                    "pattern": "straight",
                                },
                                "elements": [],
                            }
                        ],
                    }
                ],
            }
        )
    )["rooms"][0]

    tiling = measured["faces"][0]["tiling"]
    assert tiling["full_units"] is None
    assert tiling["cut_units"] is None
    # 30 m² à 8 % de chute, unité de 0.0001 m² : la commande reste chiffrée.
    assert tiling["units_total"] == 324_000
    assert any("positions" in message for message in measured["warnings"])


def test_a_room_narrower_than_its_own_walls_is_reported_and_not_measured() -> None:
    """`net_floor_area_cm2` vaut alors 0 : le scene graph a déjà refusé d'inventer une aire, et
    le métré refuse à son tour d'en tirer un linéaire."""
    fixture = load("07_metre_piece_rectangulaire.json")
    source = json.loads(json.dumps(fixture["input"]))
    source["rooms"][0]["wall_thickness_cm"] = 400.0

    measured = build_takeoff(build_scene_graph(source))["rooms"][0]

    assert measured["floor_area_m2"] == 0.0
    assert measured["skirting_ml"] is None
    assert any("plus étroite que ses propres murs" in message for message in measured["warnings"])


def test_a_project_without_rooms_produces_an_empty_takeoff() -> None:
    measured = build_takeoff({"units": "cm", "project_id": 1, "rooms": []})

    assert measured["rooms"] == []
    assert measured["totals"]["room_count"] == 0
    assert measured["totals"]["floor_area_m2"] == 0.0
    assert measured["warnings"] == []
