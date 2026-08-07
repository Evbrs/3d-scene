"""Ticket P6 — le scene graph confronté aux fixtures de référence.

Ces fixtures ont été calculées à la main **avant** l'implémentation et font foi (`CLAUDE.md`) :
en cas de désaccord, c'est le code qui est corrigé, jamais la fixture ajustée.

C'est la contre-mesure de `docs/plan-generation-ia.md` §6 contre « le calcul géométrique a l'air
correct mais est subtilement faux » : un test dont les valeurs attendues seraient issues du code
lui-même ne prouverait rien.
"""

import json
import math
from pathlib import Path
from typing import Any

import pytest

from app.geometry.furniture import expand_recipe, requires_csg, resolve_variants
from app.geometry.scene import build_scene_graph
from app.geometry.vectors import (
    ensure_counter_clockwise,
    first_hit_distance,
    miter_extension,
    offset_polygon,
    outward_normal,
    signed_area,
    wall_direction,
)

FIXTURES = Path(__file__).parent / "fixtures"

# Champs ajoutés au contrat APRÈS l'écriture des fixtures 01 à 04. Les décrire dans ces fixtures
# reviendrait à les réécrire, ce que `CLAUDE.md` interdit ; ils sont donc exclus du seul contrôle
# d'exhaustivité et figés par les fixtures 05 et 06, qui les décrivent en entier. La liste est
# volontairement explicite : un champ ajouté sans être inscrit ici fait toujours échouer les
# fixtures d'origine, et le contrôle des champs *manquants* ou renommés reste entier.
FIELDS_ADDED_AFTER_P6 = frozenset({"axis", "net_floor_area_cm2"})


def load(name: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return payload


def assert_matches(actual: Any, expected: Any, path: str = "", *, strict_keys: bool = True) -> None:
    """Comparaison récursive avec un chemin lisible en cas d'écart.

    Un `assert actual == expected` sur des arbres de cette taille produit un diff illisible ;
    ici l'échec pointe directement le champ fautif.

    `strict_keys` compare aussi l'**ensemble** des clés : sans ça, un champ ajouté ou renommé par
    le code passerait inaperçu, et la fixture ne décrirait plus qu'un sous-ensemble du contrat.
    """
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path} : dict attendu, reçu {type(actual).__name__}"
        if strict_keys:
            unexpected = sorted(set(actual) - set(expected) - FIELDS_ADDED_AFTER_P6)
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


def _commode(variants: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """La recette de la fixture 03, éventuellement dotée d'une déclaration de variation."""
    recipe = json.loads(json.dumps(load("03_commode_parametrique.json")["input"]["furniture_type"]))
    if variants is not None:
        recipe["variants"] = variants
    return dict(recipe)


DRAWER_VARIANT = [{"name": "nb_tiroirs", "axis": "y", "applies_to": ["facade", "poignee"],
                   "min": 1, "max": 5}]


def test_the_drawer_count_comes_from_the_instance_not_from_the_recipe() -> None:
    """Spec §4.1 et §4.4 : le nombre de tiroirs est un paramètre d'**instance**.

    La version précédente de ce test réécrivait `repeat_y` dans la RECETTE, puis vérifiait que la
    recette modifiée produisait N copies. Elle ne testait donc que `_axis_centers`, et passait
    alors même que `variant_params` n'était lu par personne — ce qui était exactement le cas.
    Ici c'est `variant_params`, et lui seul, qui varie.
    """
    recipe = _commode(DRAWER_VARIANT)

    for drawers in (1, 2, 4, 5):
        primitives = expand_recipe(
            recipe["parts"], (100.0, 85.0, 45.0), {}, recipe["variants"], {"nb_tiroirs": drawers}
        )
        facades = [p for p in primitives if p.color_slot == "facade"]
        poignees = [p for p in primitives if p.color_slot == "poignee"]
        assert len(facades) == drawers
        assert len(poignees) == drawers


def test_a_recipe_without_variants_ignores_the_instance_parameters() -> None:
    """Sans déclaration, rien ne dit ce que `nb_tiroirs` pilote : la recette fait foi."""
    recipe = _commode()

    primitives = expand_recipe(
        recipe["parts"], (100.0, 85.0, 45.0), {}, None, {"nb_tiroirs": 2}
    )

    assert len([p for p in primitives if p.color_slot == "facade"]) == 4


def test_a_variant_value_is_clamped_to_the_bounds_of_the_recipe() -> None:
    """`variant_params` est un JSON libre : c'est ici que sa valeur rencontre les bornes.

    Borner plutôt que refuser : un refus ferait disparaître le meuble du plan pour une saisie
    hors bornes, alors qu'un meuble borné reste visible et corrigeable.
    """
    recipe = _commode(DRAWER_VARIANT)

    def drawers(value: Any) -> int:
        primitives = expand_recipe(
            recipe["parts"], (100.0, 85.0, 45.0), {}, recipe["variants"], {"nb_tiroirs": value}
        )
        return len([p for p in primitives if p.color_slot == "facade"])

    assert drawers(12) == 5  # borné par max
    assert drawers(0) == 1  # borné par min
    assert drawers(-3) == 1
    # `isinstance(True, int)` vaut True en Python : sans exclusion explicite des booléens, un
    # `true` saisi produirait un tiroir unique au lieu d'être ignoré.
    assert drawers(True) == 4
    assert drawers("quatre") == 4
    assert drawers(None) == 4


def test_a_variant_only_touches_the_slots_it_declares() -> None:
    """Les emplacements visés sont désignés par leur `color_slot`, pas par leur rang."""
    recipe = _commode(
        [{"name": "nb_tiroirs", "axis": "y", "applies_to": ["facade"], "min": 1, "max": 5}]
    )

    primitives = expand_recipe(
        recipe["parts"], (100.0, 85.0, 45.0), {}, recipe["variants"], {"nb_tiroirs": 2}
    )

    assert len([p for p in primitives if p.color_slot == "facade"]) == 2
    assert len([p for p in primitives if p.color_slot == "poignee"]) == 4
    assert len([p for p in primitives if p.color_slot == "corps"]) == 1


def test_variant_resolution_indexes_by_slot_and_axis() -> None:
    resolved = resolve_variants(DRAWER_VARIANT, {"nb_tiroirs": 3})

    assert resolved == {("facade", "y"): 3, ("poignee", "y"): 3}
    assert resolve_variants(DRAWER_VARIANT, {}) == {}
    assert resolve_variants(None, {"nb_tiroirs": 3}) == {}
    # Un axe fantaisiste ne pilote rien : `parts` et `variants` sont du JSON libre.
    assert resolve_variants([{**DRAWER_VARIANT[0], "axis": "w"}], {"nb_tiroirs": 3}) == {}


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
    assert normalised == fixture["expected_normalized_polygon"]


def _drawn_clockwise(source: dict[str, Any]) -> dict[str, Any]:
    """La MÊME pièce, décrite dans l'autre sens : polygone **et** segments de mur inversés.

    Ne retourner que le polygone en laissant les faces intactes produirait une entrée
    incohérente, que personne ne peut saisir — et un test qui passerait quoi qu'il arrive.
    C'est exactement le défaut qu'avait la première version de ce test.
    """
    room = json.loads(json.dumps(source))["rooms"][0]
    room["polygon"] = list(reversed(room["polygon"]))

    walls = [face for face in room["faces"] if face["kind"] == "wall"]
    others = [face for face in room["faces"] if face["kind"] != "wall"]
    reversed_walls = []
    for index, face in enumerate(reversed(walls)):
        flipped = dict(face)
        flipped["start_x_cm"], flipped["end_x_cm"] = face["end_x_cm"], face["start_x_cm"]
        flipped["start_y_cm"], flipped["end_y_cm"] = face["end_y_cm"], face["start_y_cm"]
        flipped["label"] = "ABCDEFGH"[index]
        reversed_walls.append(flipped)
    room["faces"] = reversed_walls + others
    return {"project_id": source["project_id"], "rooms": [room]}


def test_outward_normals_stay_outward_when_the_room_is_drawn_clockwise() -> None:
    """Le sens de saisie de l'utilisateur ne doit avoir aucune conséquence sur la 3D.

    Sans cette garantie, une pièce dessinée dans l'autre sens sort avec toutes ses normales
    retournées vers l'intérieur : les matériaux à une seule face disparaissent et les élévations
    regardent les murs depuis l'extérieur du logement.
    """
    source = load("01_piece_carree.json")["input"]
    scene = build_scene_graph(_drawn_clockwise(source))

    room = scene["rooms"][0]
    centroid = (200.0, 150.0)  # centre du rectangle de référence

    for node in room["nodes"]:
        if node["kind"] != "wall":
            continue
        # Un vecteur allant du centre de la pièce vers le mur doit pointer dans le même sens que
        # la normale sortante.
        wall_center_x = node["origin"][0] + 0.0
        wall_center_z = node["origin"][2] + 0.0
        to_wall = (wall_center_x - centroid[0], wall_center_z - centroid[1])
        normal = node["outward_normal"]
        assert to_wall[0] * normal[0] + to_wall[1] * normal[2] > 0, (
            f"la normale de la face {node['face_label']} pointe vers l'intérieur"
        )


def test_face_cameras_stay_inside_the_room_whichever_way_it_was_drawn() -> None:
    """Une caméra d'élévation posée hors de la pièce a le mur opposé devant elle."""
    source = load("01_piece_carree.json")["input"]

    for scene_input in (source, _drawn_clockwise(source)):
        room = build_scene_graph(scene_input)["rooms"][0]
        for camera in room["cameras"]:
            if camera["face_label"] is None:
                continue
            x, _y, z = camera["position"]
            assert 0.0 <= x <= 400.0, f"{camera['name']} est hors de la pièce en x : {x}"
            assert 0.0 <= z <= 300.0, f"{camera['name']} est hors de la pièce en z : {z}"


def test_the_room_area_is_identical_whichever_way_it_was_drawn() -> None:
    source = load("01_piece_carree.json")["input"]

    direct = build_scene_graph(source)["rooms"][0]
    reversed_room = build_scene_graph(_drawn_clockwise(source))["rooms"][0]

    assert direct["floor_area_cm2"] == reversed_room["floor_area_cm2"] == 120000.0


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


def test_the_floor_lands_under_the_walls_and_not_in_a_mirror() -> None:
    """`R_x(-pi/2)` envoie (u, v, 0) sur (u, 0, -v) : sans négation du contour, le sol se
    retrouve en miroir, sur des z négatifs, donc sous aucun mur."""
    fixture = load("01_piece_carree.json")

    room = build_scene_graph(fixture["input"])["rooms"][0]
    floor = next(node for node in room["nodes"] if node["kind"] == "floor")

    angle = floor["rotation_x"]
    sin_a = math.sin(angle)
    reconstructed = []
    for u, v in floor["outline"]:
        # R_x : (y, z) -> (y cos - z sin, y sin + z cos), appliqué au point local (u, v, 0).
        world_z = v * sin_a
        reconstructed.append(
            (round(u + floor["origin"][0], 6), round(world_z + floor["origin"][2], 6))
        )

    assert sorted(reconstructed) == sorted(
        (float(x), float(y)) for x, y in fixture["input"]["rooms"][0]["polygon"]
    ), f"le sol est reconstruit sur {reconstructed}"


def test_furniture_on_the_floor_stays_inside_a_room_far_from_the_origin() -> None:
    """Les décalages au sol sont relatifs à la pièce, pas des coordonnées absolues du plan."""
    scene = build_scene_graph(
        {
            "project_id": 8,
            "rooms": [
                {
                    "id": 80,
                    "name": "Pièce éloignée",
                    "wall_thickness_cm": 10.0,
                    "ceiling_height_cm": 250.0,
                    "polygon": [[500, 500], [900, 500], [900, 800], [500, 800]],
                    "faces": [
                        {
                            "id": 800, "label": "SOL", "kind": "floor",
                            "start_x_cm": None, "start_y_cm": None,
                            "end_x_cm": None, "end_y_cm": None,
                            "covering": {},
                            "elements": [
                                {
                                    "id": 8000, "kind": "furniture",
                                    "x_offset_cm": 10, "y_offset_cm": 10,
                                    "width_cm": 80, "height_cm": 40, "depth_cm": 40,
                                    "rotation_deg": 0, "furniture_type_id": 1,
                                    "colors": {}, "variant_params": {},
                                }
                            ],
                        }
                    ],
                }
            ],
        },
        {
            1: {
                "id": 1, "slug": "table",
                "parts": [{"type": "box", "rel_position": [0.5, 0.5, 0.5],
                           "rel_size": [1, 1, 1], "color_slot": "plateau"}],
                "color_slots": ["plateau"],
            }
        },
    )

    furniture = next(node for node in scene["rooms"][0]["nodes"] if node["kind"] == "furniture")
    x, _y, z = furniture["position"]
    assert 500.0 <= x <= 900.0, f"le meuble est hors de la pièce en x : {x}"
    assert 500.0 <= z <= 800.0, f"le meuble est hors de la pièce en z : {z}"


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


# --- Fixtures 05 et 06 : outils communs ----------------------------------------------------------


def _catalog(fixture: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Le catalogue de la fixture, réindexé par entier : JSON n'a que des clés textuelles."""
    return {
        int(key): value for key, value in (fixture["input"].get("furniture_types") or {}).items()
    }


def _nodes(fixture: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    scene = build_scene_graph(fixture["input"], _catalog(fixture))
    return [node for node in scene["rooms"][0]["nodes"] if node["kind"] == kind]


def _miter_extensions(fixture: dict[str, Any]) -> dict[str, float]:
    """Rallonge d'onglet à chaque sommet, indexée `mur entrant>mur sortant`."""
    room = fixture["input"]["rooms"][0]
    walls = [face for face in room["faces"] if face["kind"] == "wall"]
    half_thickness = float(room["wall_thickness_cm"]) / 2.0

    def direction(face: dict[str, Any]) -> Any:
        return wall_direction(
            [face["start_x_cm"], face["start_y_cm"]], [face["end_x_cm"], face["end_y_cm"]]
        )

    return {
        f"{face['label']}>{walls[(index + 1) % len(walls)]['label']}": round(
            miter_extension(direction(face), direction(walls[(index + 1) % len(walls)]),
                            half_thickness),
            4,
        )
        for index, face in enumerate(walls)
    }


def _contains(polygon: list[list[float]], x: float, y: float) -> bool:
    """Appartenance d'un point à un polygone, par parité des croisements d'un rayon horizontal.

    Une boîte englobante suffit pour un rectangle et ment sur une pièce en L : c'est justement ce
    que ces fixtures cherchent à mettre en défaut.
    """
    inside = False
    for index, (start_x, start_y) in enumerate(polygon):
        end_x, end_y = polygon[(index + 1) % len(polygon)]
        if (start_y > y) != (end_y > y):
            crossing_x = start_x + (y - start_y) * (end_x - start_x) / (end_y - start_y)
            if x < crossing_x:
                inside = not inside
    return inside


# --- Fixture 05 : murs obliques ------------------------------------------------------------------


def test_oblique_walls_match_their_reference_fixture() -> None:
    fixture = load("05_mur_oblique.json")

    assert_matches(_nodes(fixture, "wall"), fixture["expected_walls"], "murs")


def test_the_four_quadrants_of_atan2_are_all_exercised() -> None:
    """Contre-mesure au biais des fixtures 01 à 04 : elles n'ont que des murs alignés sur les axes.

    Sur un rectangle, une composante sur deux de la direction est nulle : un `atan2` dont les
    arguments seraient permutés ou mal signés y donne parfois la bonne valeur par accident. Les
    quatre murs obliques de l'octogone, un par quadrant, ne laissent pas cette échappatoire.
    """
    fixture = load("05_mur_oblique.json")

    angles = {node["face_label"]: node["rotation_y"] for node in _nodes(fixture, "wall")}
    obliques = sorted(angles[label] for label in ("B", "D", "F", "H"))

    quarter = math.pi / 4
    assert obliques == pytest.approx([-3 * quarter, -quarter, quarter, 3 * quarter], abs=1e-6)


def test_oblique_face_cameras_match_their_reference_fixture() -> None:
    fixture = load("05_mur_oblique.json")

    cameras = build_scene_graph(fixture["input"], _catalog(fixture))["rooms"][0]["cameras"]

    assert_matches(cameras, fixture["expected_cameras"], "cameras")


def test_every_corner_of_the_octagon_needs_the_same_miter() -> None:
    """Huit sommets à 45° : la rallonge vaut partout 5 x tan(22.5°) = 2.0711."""
    fixture = load("05_mur_oblique.json")

    assert _miter_extensions(fixture) == fixture["expected_miter_extensions_cm"]


# --- Fixture 06 : pièce en L (polygone concave) --------------------------------------------------


def test_a_concave_room_matches_its_reference_fixture() -> None:
    fixture = load("06_piece_en_L.json")

    assert_matches(_nodes(fixture, "wall"), fixture["expected_walls"], "murs")


def test_a_reentrant_corner_is_mitred_outwards_and_not_shortened() -> None:
    """Au sommet rentrant, la rallonge signée serait négative et creuserait la fente au lieu de
    la combler : c'est la valeur absolue qui compte."""
    fixture = load("06_piece_en_L.json")

    extensions = _miter_extensions(fixture)

    assert extensions == fixture["expected_miter_extensions_cm"]
    assert extensions["C>D"] == 5.0


def test_face_cameras_of_a_concave_room_match_their_reference_fixture() -> None:
    fixture = load("06_piece_en_L.json")

    cameras = build_scene_graph(fixture["input"], _catalog(fixture))["rooms"][0]["cameras"]

    assert_matches(cameras, fixture["expected_cameras"], "cameras")


def test_no_face_camera_of_a_concave_room_lands_outside_it() -> None:
    """Régression : la profondeur venait de la projection des sommets, donc de la boîte englobante.

    Sur cette pièce en L, les caméras des murs A et F reculaient au-delà du retour et se
    retrouvaient hors de la pièce, le mur opposé devant elles.
    """
    fixture = load("06_piece_en_L.json")
    polygon = fixture["input"]["rooms"][0]["polygon"]

    cameras = build_scene_graph(fixture["input"], _catalog(fixture))["rooms"][0]["cameras"]

    for camera in cameras:
        if camera["face_label"] is None:
            continue
        x, _y, z = camera["position"]
        assert _contains(polygon, x, z), f"{camera['name']} est hors de la pièce en ({x}, {z})"


def test_a_ray_stops_at_the_first_edge_it_meets_and_not_at_the_furthest() -> None:
    """Le mur F borde l'aile courte : le retour l'arrête à 200, la boîte englobante disait 600."""
    polygon = load("06_piece_en_L.json")["input"]["rooms"][0]["polygon"]

    assert first_hit_distance([0.0, 250.0], [1.0, 0.0], polygon) == pytest.approx(200.0)
    assert first_hit_distance([300.0, 0.0], [0.0, 1.0], polygon) == pytest.approx(200.0)
    # Le côté qui porte le point de départ est touché à distance nulle : il doit être écarté.
    assert first_hit_distance([100.0, 500.0], [0.0, -1.0], polygon) == pytest.approx(500.0)
    # Un rayon qui sort de la pièce ne rencontre plus rien.
    assert first_hit_distance([700.0, 250.0], [1.0, 0.0], polygon) is None


# --- Onglet des murs -----------------------------------------------------------------------------


def test_two_collinear_walls_need_no_miter() -> None:
    """Droites décalées parallèles : l'intersection part à l'infini, et il n'y a aucune fente."""
    straight = wall_direction([0.0, 0.0], [100.0, 0.0])
    folded = wall_direction([100.0, 0.0], [0.0, 0.0])

    assert miter_extension(straight, straight, 5.0) == 0.0
    assert miter_extension(straight, folded, 5.0) == 0.0


def test_a_right_angle_needs_half_the_wall_thickness() -> None:
    """Sur la pièce de référence, la fente d'un angle droit fait 5 sur 5 : la rallonge vaut 5."""
    along_x = wall_direction([0.0, 0.0], [400.0, 0.0])
    along_z = wall_direction([400.0, 0.0], [400.0, 300.0])

    assert miter_extension(along_x, along_z, 5.0) == pytest.approx(5.0)
    assert miter_extension(along_x, along_z, 15.0) == pytest.approx(15.0)


# --- Aire nette ----------------------------------------------------------------------------------


def test_the_net_floor_area_is_measured_between_the_wall_faces() -> None:
    """`floor_area_cm2` mesure la ligne médiane des murs, pas le sol : c'est l'aire du plan.

    Sur la pièce de référence (400 sur 300), l'aire nette vaut 390 x 290 = 113100 avec des murs de
    10, et 370 x 270 = 99900 avec des murs de 30 : la médiane surévalue de 6 % puis de 20 %.
    """
    fixture = load("01_piece_carree.json")

    for thickness, expected in ((10.0, 113100.0), (30.0, 99900.0)):
        source = json.loads(json.dumps(fixture["input"]))
        source["rooms"][0]["wall_thickness_cm"] = thickness
        room = build_scene_graph(source)["rooms"][0]

        assert room["floor_area_cm2"] == 120000.0, "l'aire historique ne doit pas bouger"
        assert room["net_floor_area_cm2"] == pytest.approx(expected)


def test_the_net_floor_area_of_the_reference_fixtures() -> None:
    for name in ("05_mur_oblique.json", "06_piece_en_L.json"):
        fixture = load(name)
        room = build_scene_graph(fixture["input"], _catalog(fixture))["rooms"][0]

        assert_matches(
            {key: room[key] for key in fixture["expected_room"]},
            fixture["expected_room"],
            f"{name}.piece",
        )


def test_the_offset_contour_pushes_a_reentrant_corner_outwards() -> None:
    """Le sommet rentrant (200,200) doit partir en (195,195) : décalé vers l'extérieur du L."""
    fixture = load("06_piece_en_L.json")

    inner = offset_polygon(fixture["input"]["rooms"][0]["polygon"], 5.0)

    assert_matches(inner, fixture["expected_offset_polygon"], "contour_decale")


def test_a_room_narrower_than_its_own_walls_has_no_net_area() -> None:
    """Le contour décalé se replie sur lui-même : toute valeur positive serait une invention."""
    scene = build_scene_graph(
        {
            "project_id": 7,
            "rooms": [
                {
                    "id": 70, "name": "Placard", "wall_thickness_cm": 200.0,
                    "ceiling_height_cm": 250.0,
                    "polygon": [[0, 0], [60, 0], [60, 40], [0, 40]],
                    "faces": [],
                }
            ],
        }
    )

    assert scene["rooms"][0]["floor_area_cm2"] == 2400.0
    assert scene["rooms"][0]["net_floor_area_cm2"] == 0.0


# --- Menuiseries des ouvertures ------------------------------------------------------------------


def test_an_opening_produces_its_hole_and_its_joinery() -> None:
    """Spec §4.3 : le catalogue décrit une porte battante, pas seulement un vide dans un mur."""
    fixture = load("06_piece_en_L.json")

    joinery = _nodes(fixture, "joinery")
    wall_a = next(node for node in _nodes(fixture, "wall") if node["face_label"] == "A")

    assert len(wall_a["holes"]) == 1
    assert len(joinery) == 1
    assert_matches(joinery[0], fixture["expected_joinery_node"], "menuiserie")


def test_the_joinery_fills_the_thickness_of_the_wall() -> None:
    """La profondeur saisie sur l'élément (5) est ignorée : la menuiserie occupe le percement."""
    fixture = load("06_piece_en_L.json")

    joinery = _nodes(fixture, "joinery")[0]

    assert fixture["input"]["rooms"][0]["faces"][0]["elements"][0]["depth_cm"] == 5
    assert joinery["size_cm"][2] == fixture["input"]["rooms"][0]["wall_thickness_cm"]


def test_an_opening_without_its_recipe_in_the_catalogue_stays_a_plain_hole() -> None:
    """Comme un meuble sans recette : une menuiserie inventée masquerait le catalogue manquant."""
    fixture = load("02_mur_avec_ouvertures.json")

    scene = build_scene_graph(fixture["input"], {})

    assert [node["kind"] for node in scene["rooms"][0]["nodes"]] == ["wall"]


def test_each_opening_kind_maps_to_its_own_recipe() -> None:
    fixture = load("06_piece_en_L.json")
    catalog = {
        1: {"id": 1, "slug": "porte-battante", "parts": [], "color_slots": []},
        2: {"id": 2, "slug": "porte-coulissante", "parts": [], "color_slots": []},
        3: {"id": 3, "slug": "fenetre", "parts": [], "color_slots": []},
    }

    for kind, slug in (
        ("door_hinged", "porte-battante"),
        ("door_sliding", "porte-coulissante"),
        ("window", "fenetre"),
    ):
        source = json.loads(json.dumps(fixture["input"]))
        source["rooms"][0]["faces"][0]["elements"][0]["kind"] = kind
        scene = build_scene_graph(source, catalog)

        joinery = next(node for node in scene["rooms"][0]["nodes"] if node["kind"] == "joinery")
        assert joinery["opening_kind"] == kind
        assert joinery["furniture_type_slug"] == slug


# --- Axe de révolution des primitives ------------------------------------------------------------


def test_a_lying_cylinder_declares_its_axis() -> None:
    """Une poignée de porte est un cylindre couché : sans `axis`, le viewer la dresse debout."""
    fixture = load("06_piece_en_L.json")

    joinery = _nodes(fixture, "joinery")[0]
    handle = next(p for p in joinery["primitives"] if p["color_slot"] == "poignee")

    assert handle["axis"] == "z"


def test_a_primitive_without_axis_stands_upright() -> None:
    primitives = expand_recipe(
        [
            {"type": "cylinder", "rel_position": [0.5, 0.5, 0.5], "rel_size": [1, 1, 1],
             "color_slot": "pied"},
            # `parts` est du JSON libre : un axe fantaisiste ne doit pas remonter jusqu'au viewer.
            {"type": "cylinder", "rel_position": [0.5, 0.5, 0.5], "rel_size": [1, 1, 1],
             "color_slot": "pied", "axis": "diagonale"},
        ],
        (10.0, 40.0, 10.0),
        {},
    )

    assert [primitive.axis for primitive in primitives] == ["y", "y"]
