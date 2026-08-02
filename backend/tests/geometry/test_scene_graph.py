"""Ticket P6 — le scene graph confronté aux fixtures de référence.

Ces fixtures ont été calculées à la main **avant** l'implémentation et font foi (`CLAUDE.md`) :
en cas de désaccord, c'est le code qui est corrigé, jamais la fixture ajustée.

C'est la contre-mesure de `docs/plan-generation-ia.md` §6 contre « le calcul géométrique a l'air
correct mais est subtilement faux » : un test dont les valeurs attendues seraient issues du code
lui-même ne prouverait rien.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from app.geometry.furniture import expand_recipe, requires_csg
from app.geometry.scene import build_scene_graph
from app.geometry.vectors import ensure_counter_clockwise, outward_normal, signed_area

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return payload


def assert_matches(actual: Any, expected: Any, path: str = "") -> None:
    """Comparaison récursive avec un chemin lisible en cas d'écart.

    Un `assert actual == expected` sur des arbres de cette taille produit un diff illisible ;
    ici l'échec pointe directement le champ fautif.
    """
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path} : dict attendu, reçu {type(actual).__name__}"
        for key, value in expected.items():
            assert key in actual, f"{path}.{key} absent du résultat"
            assert_matches(actual[key], value, f"{path}.{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list), f"{path} : liste attendue, reçu {type(actual).__name__}"
        assert len(actual) == len(expected), (
            f"{path} : {len(actual)} éléments, {len(expected)} attendus"
        )
        for index, value in enumerate(expected):
            assert_matches(actual[index], value, f"{path}[{index}]")
    elif isinstance(expected, float):
        assert actual == pytest.approx(expected, abs=1e-4), f"{path} : {actual} ≠ {expected}"
    else:
        assert actual == expected, f"{path} : {actual!r} ≠ {expected!r}"


# --- Fixture 01 : pièce carrée nue ---------------------------------------------------------------


def test_a_bare_room_matches_its_reference_fixture() -> None:
    fixture = load("01_piece_carree.json")

    scene = build_scene_graph(fixture["input"])

    assert_matches(scene, fixture["expected"], "scene")


def test_the_bare_room_produces_exactly_six_nodes() -> None:
    fixture = load("01_piece_carree.json")

    scene = build_scene_graph(fixture["input"])
    nodes = scene["rooms"][0]["nodes"]

    assert [node["face_label"] for node in nodes] == ["A", "B", "C", "D", "SOL", "PLAFOND"]
    assert [node["kind"] for node in nodes] == [
        "wall",
        "wall",
        "wall",
        "wall",
        "floor",
        "ceiling",
    ]


def test_every_face_has_its_own_camera_preset() -> None:
    """Spec §3.3 : une vue orthographique par face, en plus des trois vues d'ensemble."""
    fixture = load("01_piece_carree.json")

    cameras = build_scene_graph(fixture["input"])["rooms"][0]["cameras"]

    names = [camera["name"] for camera in cameras]
    assert names[:3] == ["dessus", "isometrique", "orbite"]
    assert sorted(name for name in names if name.startswith("face-")) == [
        "face-A",
        "face-B",
        "face-C",
        "face-D",
    ]


def test_the_top_view_reads_like_the_2d_plan() -> None:
    """Le vecteur « haut » doit faire descendre l'axe y du plan, comme dans l'éditeur Konva."""
    fixture = load("01_piece_carree.json")

    cameras = build_scene_graph(fixture["input"])["rooms"][0]["cameras"]
    top = next(camera for camera in cameras if camera["name"] == "dessus")

    assert top["kind"] == "orthographic"
    assert top["up"] == [0.0, 0.0, -1.0]
    assert top["position"][1] > fixture["input"]["rooms"][0]["ceiling_height_cm"]


def test_face_cameras_look_at_the_wall_from_inside_the_room() -> None:
    """Une élévation montre la face intérieure : la caméra est dans la pièce, pas dehors."""
    fixture = load("01_piece_carree.json")

    scene = build_scene_graph(fixture["input"])
    room = scene["rooms"][0]
    cameras = {camera["face_label"]: camera for camera in room["cameras"] if camera["face_label"]}
    walls = {node["face_label"]: node for node in room["nodes"] if node["kind"] == "wall"}

    for label, camera in cameras.items():
        outward = walls[label]["outward_normal"]
        # Le vecteur caméra → mur doit être orienté comme la normale sortante.
        direction = [
            camera["target"][axis] - camera["position"][axis] for axis in range(3)
        ]
        dot = sum(direction[axis] * outward[axis] for axis in range(3))
        assert dot > 0, f"la caméra de la face {label} regarde le mur depuis l'extérieur"


# --- Fixture 02 : ouvertures ---------------------------------------------------------------------


def test_openings_become_holes_in_the_wall() -> None:
    fixture = load("02_mur_avec_ouvertures.json")

    scene = build_scene_graph(fixture["input"])
    walls = [node for node in scene["rooms"][0]["nodes"] if node["kind"] == "wall"]

    assert len(walls) == 1
    assert_matches(walls[0], fixture["expected_wall_node"], "mur")


def test_openings_do_not_produce_furniture_nodes() -> None:
    """Spec §3.1 : une ouverture est un trou dans le mur, pas un objet posé dessus."""
    fixture = load("02_mur_avec_ouvertures.json")

    scene = build_scene_graph(fixture["input"])
    furniture = [node for node in scene["rooms"][0]["nodes"] if node["kind"] == "furniture"]

    assert len(furniture) == fixture["expected_furniture_node_count"]


# --- Fixture 03 : recette paramétrique -----------------------------------------------------------


def test_a_parametric_recipe_expands_to_its_reference_primitives() -> None:
    fixture = load("03_commode_parametrique.json")
    expected = fixture["expected"]
    element = fixture["input"]["element"]
    wall = fixture["input"]["wall"]
    furniture_type = fixture["input"]["furniture_type"]

    scene = build_scene_graph(
        {
            "project_id": 3,
            "rooms": [
                {
                    "id": 30,
                    "name": "Chambre",
                    "wall_thickness_cm": wall["wall_thickness_cm"],
                    "ceiling_height_cm": wall["ceiling_height_cm"],
                    "polygon": [[0, 0], [400, 0], [400, 300], [0, 300]],
                    "faces": [
                        {
                            "id": 300,
                            "label": "A",
                            "kind": "wall",
                            "start_x_cm": wall["start_x_cm"],
                            "start_y_cm": wall["start_y_cm"],
                            "end_x_cm": wall["end_x_cm"],
                            "end_y_cm": wall["end_y_cm"],
                            "covering": {},
                            "elements": [element],
                        }
                    ],
                }
            ],
        },
        {furniture_type["id"]: furniture_type},
    )

    furniture = [node for node in scene["rooms"][0]["nodes"] if node["kind"] == "furniture"]
    assert len(furniture) == 1
    for key in (
        "position",
        "rotation_y",
        "size_cm",
        "primitives",
        "requires_csg",
        "furniture_type_slug",
        "face_label",
    ):
        assert_matches(furniture[0][key], expected[key], f"meuble.{key}")


def test_the_drawer_count_is_a_parameter_not_a_hardcoded_geometry() -> None:
    """Spec §4.1 : `repeat_y` doit produire N copies, sans nouvelle primitive écrite à la main."""
    fixture = load("03_commode_parametrique.json")
    parts = fixture["input"]["furniture_type"]["parts"]

    for drawers in (1, 2, 4, 8):
        adjusted = [
            {**part, "repeat_y": drawers} if part.get("repeat_y") else part for part in parts
        ]
        primitives = expand_recipe(adjusted, (100.0, 85.0, 45.0), {})
        facades = [p for p in primitives if p.color_slot == "facade"]
        assert len(facades) == drawers


def test_repeated_primitives_are_centred_on_the_furniture() -> None:
    """Les copies doivent être réparties symétriquement : sinon le meuble paraît décentré."""
    fixture = load("03_commode_parametrique.json")
    parts = [
        part
        for part in fixture["input"]["furniture_type"]["parts"]
        if part["color_slot"] == "facade"
    ]

    offsets = [p.offset[1] for p in expand_recipe(parts, (100.0, 85.0, 45.0), {})]

    assert offsets == sorted(offsets)
    assert sum(offsets) == pytest.approx(0.0, abs=1e-9)


def test_a_recipe_with_a_subtraction_is_flagged_for_csg() -> None:
    """Spec §4.2 : le viewer n'active `three-bvh-csg` que là où c'est nécessaire."""
    plain = expand_recipe(
        [{"type": "box", "rel_position": [0.5, 0.5, 0.5], "rel_size": [1, 1, 1],
          "color_slot": "corps"}],
        (10.0, 10.0, 10.0),
        {},
    )
    hollow = expand_recipe(
        [
            {"type": "box", "rel_position": [0.5, 0.5, 0.5], "rel_size": [1, 1, 1],
             "color_slot": "corps"},
            {"type": "sphere", "rel_position": [0.5, 0.9, 0.5], "rel_size": [0.8, 0.8, 0.8],
             "color_slot": "corps", "operation": "subtract"},
        ],
        (10.0, 10.0, 10.0),
        {},
    )

    assert requires_csg(plain) is False
    assert requires_csg(hollow) is True


def test_unchosen_colour_slots_stay_null() -> None:
    """Inventer une couleur la rendrait indiscernable d'un choix de l'utilisateur."""
    fixture = load("03_commode_parametrique.json")
    parts = fixture["input"]["furniture_type"]["parts"]

    primitives = expand_recipe(parts, (100.0, 85.0, 45.0), {"corps": "#8b5a2b"})

    assert {p.color for p in primitives if p.color_slot == "corps"} == {"#8b5a2b"}
    assert {p.color for p in primitives if p.color_slot == "facade"} == {None}


# --- Fixture 04 : orientation du polygone --------------------------------------------------------


def test_a_clockwise_polygon_is_normalised_before_any_computation() -> None:
    fixture = load("04_polygone_horaire.json")
    clockwise = fixture["input_polygon_clockwise"]

    assert signed_area(clockwise) == pytest.approx(fixture["expected_signed_area_before"])

    normalised = ensure_counter_clockwise(clockwise)
    assert signed_area(normalised) == pytest.approx(fixture["expected_signed_area_after"])


def test_the_scene_is_identical_whichever_way_the_room_was_drawn() -> None:
    """Le sens de saisie de l'utilisateur ne doit avoir aucune conséquence en 3D."""
    counter_clockwise = load("01_piece_carree.json")["input"]
    clockwise = json.loads(json.dumps(counter_clockwise))
    clockwise["rooms"][0]["polygon"] = load("04_polygone_horaire.json")[
        "input_polygon_clockwise"
    ]

    assert build_scene_graph(clockwise)["rooms"][0]["cameras"] == (
        build_scene_graph(counter_clockwise)["rooms"][0]["cameras"]
    )


def test_outward_normals_point_away_from_the_room() -> None:
    fixture = load("04_polygone_horaire.json")

    segments = {
        "(0,0)->(400,0)": ([0.0, 0.0], [400.0, 0.0]),
        "(400,0)->(400,300)": ([400.0, 0.0], [400.0, 300.0]),
        "(400,300)->(0,300)": ([400.0, 300.0], [0.0, 300.0]),
        "(0,300)->(0,0)": ([0.0, 300.0], [0.0, 0.0]),
    }
    for key, (start, end) in segments.items():
        expected = fixture["expected_outward_normals"][key]
        assert list(outward_normal(start, end)) == pytest.approx(expected, abs=1e-9)


# --- Robustesse ----------------------------------------------------------------------------------


def test_a_room_without_polygon_produces_an_empty_scene() -> None:
    scene = build_scene_graph(
        {
            "project_id": 9,
            "rooms": [
                {
                    "id": 90,
                    "name": "Esquisse",
                    "wall_thickness_cm": 10.0,
                    "ceiling_height_cm": 250.0,
                    "polygon": [],
                    "faces": [],
                }
            ],
        }
    )

    assert scene["rooms"][0]["nodes"] == []
    assert scene["rooms"][0]["cameras"] == []
    assert scene["rooms"][0]["floor_area_cm2"] == 0.0


def test_furniture_referencing_a_missing_recipe_is_skipped() -> None:
    """Un meuble sans recette n'a rien à afficher ; une boîte grise masquerait le problème."""
    scene = build_scene_graph(
        {
            "project_id": 9,
            "rooms": [
                {
                    "id": 91,
                    "name": "Pièce",
                    "wall_thickness_cm": 10.0,
                    "ceiling_height_cm": 250.0,
                    "polygon": [[0, 0], [400, 0], [400, 300], [0, 300]],
                    "faces": [
                        {
                            "id": 910,
                            "label": "A",
                            "kind": "wall",
                            "start_x_cm": 0,
                            "start_y_cm": 0,
                            "end_x_cm": 400,
                            "end_y_cm": 0,
                            "covering": {},
                            "elements": [
                                {
                                    "id": 9100, "kind": "furniture",
                                    "x_offset_cm": 0, "y_offset_cm": 0,
                                    "width_cm": 50, "height_cm": 50, "depth_cm": 50,
                                    "rotation_deg": 0, "furniture_type_id": 12345,
                                    "colors": {}, "variant_params": {},
                                }
                            ],
                        }
                    ],
                }
            ],
        },
        {},
    )

    assert [node["kind"] for node in scene["rooms"][0]["nodes"]] == ["wall"]


def test_the_scene_graph_is_json_serialisable_and_stable() -> None:
    """Stable d'un appel à l'autre : c'est ce qui rendra le cache de P10 efficace."""
    fixture = load("01_piece_carree.json")

    first = json.dumps(build_scene_graph(fixture["input"]), sort_keys=True)
    second = json.dumps(build_scene_graph(fixture["input"]), sort_keys=True)

    assert first == second
