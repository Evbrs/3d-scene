"""Construction du scene graph 3D (`docs/spec-complete.md` §3.1).

Le backend calcule un arbre de données décrivant la scène ; le frontend le traduit en objets
Three.js sans aucune logique métier. Ce module travaille sur des **dictionnaires**, pas sur des
modèles SQLModel : c'est ce qui permet aux fixtures de référence de l'alimenter directement,
sans base de données.
"""

from typing import Any

import numpy as np

from app.geometry.cameras import CameraPreset, face_view, isometric_view, orbit_view, top_view
from app.geometry.furniture import expand_recipe, requires_csg
from app.geometry.vectors import (
    bounding_box,
    ensure_counter_clockwise,
    offset_polygon,
    outward_normal,
    round_vector,
    signed_area,
    to_world,
    wall_direction,
    wall_length,
    yaw_from_direction,
)

# Ouvertures : le percement du mur reste un trou, pas un objet posé (spec §3.1). La menuiserie qui
# vient s'y loger, elle, est bien un objet — sans elle, les trois recettes correspondantes du
# catalogue sont inatteignables et toute ouverture reste un rectangle noir traversant.
OPENING_SLUGS = {
    "door_hinged": "porte-battante",
    "door_sliding": "porte-coulissante",
    "window": "fenetre",
}
OPENING_KINDS = frozenset(OPENING_SLUGS)

WALL = "wall"
FLOOR = "floor"
CEILING = "ceiling"
JOINERY = "joinery"

DIGITS = 4

# Les angles sont arrondis plus finement que les longueurs : à 1e-4 rad près, un mur de 10 m
# dérive d'environ 1 cm à son extrémité. Un angle n'a pas d'unité métier, rien n'oblige à
# l'arrondir au même pas qu'un centimètre.
ANGLE_DIGITS = 9


def _rect(u_min: float, v_min: float, u_max: float, v_max: float) -> list[list[float]]:
    return [
        [round(u_min, DIGITS), round(v_min, DIGITS)],
        [round(u_max, DIGITS), round(v_min, DIGITS)],
        [round(u_max, DIGITS), round(v_max, DIGITS)],
        [round(u_min, DIGITS), round(v_max, DIGITS)],
    ]


def _clipped_hole(
    u: float, v: float, width: float, height: float, length: float, wall_height: float
) -> list[list[float]] | None:
    """Le percement, borné au rectangle du mur. `None` s'il n'en reste rien.

    Ceinture et bretelles : l'API refuse déjà tout débordement (`services/faces.py`), mais un
    trou qui dépasse ne produit pas un mur troué — il produit un contour `THREE.Shape` dont le
    trou croise le bord, ce qui donne une triangulation dégénérée et un mur invisible. La donnée
    peut aussi venir d'ailleurs : import, correction en base, ou pièce rétrécie par une migration.
    """
    u_min = max(0.0, u)
    v_min = max(0.0, v)
    u_max = min(length, u + width)
    v_max = min(wall_height, v + height)
    if u_max <= u_min or v_max <= v_min:
        return None
    return _rect(u_min, v_min, u_max, v_max)


def _wall_node(
    face: dict[str, Any], room: dict[str, Any], *, counter_clockwise: bool
) -> dict[str, Any]:
    """Un mur extrudé, avec un trou par ouverture.

    Approche « simple » de la spec §3.2 : une `THREE.Shape` avec des trous rectangulaires,
    extrudée. Le CSG complet n'est justifié que pour ce que cette approche ne couvre pas.
    """
    start = [face["start_x_cm"], face["start_y_cm"]]
    end = [face["end_x_cm"], face["end_y_cm"]]
    length = wall_length(start, end)
    height = float(room["ceiling_height_cm"])
    thickness = float(room["wall_thickness_cm"])

    holes = [
        hole
        for element in face.get("elements", [])
        if element["kind"] in OPENING_KINDS
        and (
            hole := _clipped_hole(
                float(element["x_offset_cm"]),
                float(element["y_offset_cm"]),
                float(element["width_cm"]),
                float(element["height_cm"]),
                length,
                height,
            )
        )
        is not None
    ]

    return {
        "kind": WALL,
        "face_id": face["id"],
        "face_label": face["label"],
        "length_cm": round(length, DIGITS),
        "height_cm": round(height, DIGITS),
        # Origine du repère local : le départ du mur, au niveau du sol.
        "origin": round_vector(to_world(start), DIGITS),
        "rotation_y": round(yaw_from_direction(wall_direction(start, end)), ANGLE_DIGITS),
        "outward_normal": round_vector(
            outward_normal(start, end, counter_clockwise=counter_clockwise), DIGITS
        ),
        "outline": _rect(0.0, 0.0, length, height),
        "holes": holes,
        "extrude_depth_cm": round(thickness, DIGITS),
        # Le mur est centré sur son axe : l'extrusion démarre une demi-épaisseur en arrière.
        "extrude_offset_cm": round(-thickness / 2.0, DIGITS),
        "covering": face.get("covering") or {},
    }


def _horizontal_node(
    face: dict[str, Any], polygon: list[list[float]], altitude: float
) -> dict[str, Any]:
    """Sol ou plafond : le contour de la pièce, à plat.

    Attention au signe. `R_x(-π/2)` envoie le point local `(u, v, 0)` sur `(u, 0, -v)` : le
    contour se retrouverait **en miroir**, sur des `z` négatifs, donc sous aucun mur. Le contour
    est donc émis avec son `y` négué, de sorte que `(u, -v, 0)` retombe sur `(u, 0, v)`.

    Ce choix — plutôt que `+π/2`, qui replacerait correctement le contour — préserve en prime
    l'orientation de la normale : la face locale `+Z` devient `+Y`, donc un sol tourné vers le
    haut plutôt que vers le bas.
    """
    return {
        "kind": FLOOR if face["kind"] == "floor" else CEILING,
        "face_id": face["id"],
        "face_label": face["label"],
        "origin": [0.0, round(altitude, DIGITS), 0.0],
        "rotation_x": round(-np.pi / 2.0, ANGLE_DIGITS),
        "outline": [[round(x, DIGITS), round(-y, DIGITS)] for x, y in polygon],
        "holes": [],
        "covering": face.get("covering") or {},
    }


def _furniture_node(
    element: dict[str, Any],
    face: dict[str, Any],
    room: dict[str, Any],
    furniture_types: dict[int, dict[str, Any]],
    *,
    counter_clockwise: bool,
    polygon: list[list[float]],
) -> dict[str, Any] | None:
    """Un meuble posé sur une face, développé en primitives absolues.

    Renvoie `None` si l'élément référence un type de mobilier absent du catalogue : un meuble
    sans recette n'a rien à afficher, et inventer une boîte grise masquerait le problème.
    """
    furniture_type = furniture_types.get(element.get("furniture_type_id") or -1)
    if furniture_type is None:
        return None

    width = float(element["width_cm"])
    height = float(element["height_cm"])
    depth = float(element["depth_cm"])

    if face["kind"] == WALL:
        start = [face["start_x_cm"], face["start_y_cm"]]
        end = [face["end_x_cm"], face["end_y_cm"]]
        direction = wall_direction(start, end)
        inward = -outward_normal(start, end, counter_clockwise=counter_clockwise)
        thickness = float(room["wall_thickness_cm"])

        position = (
            to_world(start)
            + direction * (float(element["x_offset_cm"]) + width / 2.0)
            + np.array([0.0, float(element["y_offset_cm"]) + height / 2.0, 0.0])
            # Le mur est centré sur son axe : sa face intérieure est à une demi-épaisseur.
            + inward * (thickness / 2.0 + depth / 2.0)
        )
        rotation_y = yaw_from_direction(direction) + np.radians(float(element["rotation_deg"]))
    else:
        # Sol et plafond : les décalages sont relatifs au coin de la boîte englobante de la
        # pièce, pas des coordonnées absolues du plan. C'est la lecture qu'impose la validation
        # en amont (`element_fits_on_face`, qui borne par l'étendue) ; les traiter comme
        # absolues plaçait le meuble hors de la pièce dès que celle-ci n'est pas à l'origine.
        min_x, min_y, _max_x, _max_y = bounding_box(polygon) if polygon else (0.0, 0.0, 0.0, 0.0)
        altitude = 0.0 if face["kind"] == "floor" else float(room["ceiling_height_cm"]) - height
        position = np.array(
            [
                min_x + float(element["x_offset_cm"]) + width / 2.0,
                altitude + height / 2.0,
                min_y + float(element["y_offset_cm"]) + depth / 2.0,
            ]
        )
        rotation_y = np.radians(float(element["rotation_deg"]))

    primitives = expand_recipe(
        furniture_type["parts"],
        (width, height, depth),
        element.get("colors") or {},
        furniture_type.get("variants"),
        element.get("variant_params") or {},
    )

    return {
        "kind": "furniture",
        "element_id": element["id"],
        "face_label": face["label"],
        "furniture_type_slug": furniture_type["slug"],
        "position": round_vector(position, DIGITS),
        "rotation_y": round(float(rotation_y), ANGLE_DIGITS),
        "size_cm": [round(width, DIGITS), round(height, DIGITS), round(depth, DIGITS)],
        "primitives": [primitive.to_dict(DIGITS) for primitive in primitives],
        "requires_csg": requires_csg(primitives),
        "variant_params": element.get("variant_params") or {},
    }


def _joinery_node(
    element: dict[str, Any],
    face: dict[str, Any],
    room: dict[str, Any],
    furniture_types: dict[int, dict[str, Any]],
) -> dict[str, Any] | None:
    """La menuiserie qui vient se loger dans le trou d'une ouverture.

    Le trou seul ne décrit pas une ouverture : il décrit un vide. Une porte reste une porte, avec
    son panneau et sa poignée — et le catalogue (§4.3) en donne déjà la recette. La menuiserie est
    donc développée comme n'importe quel meuble, mais avec la profondeur du mur et **centrée sur
    l'axe du mur** : elle occupe exactement le percement, ni en avant ni en arrière.

    Le type est retrouvé par son `slug` : une ouverture n'a pas de `furniture_type_id` (spec §5,
    ce champ est renseigné « uniquement pour `kind == FURNITURE` »), c'est sa nature qui désigne
    la recette. Renvoie `None` si cette recette n'est pas dans le catalogue fourni — comme pour un
    meuble sans recette, une boîte grise inventée masquerait le problème.
    """
    if face["kind"] != WALL:
        return None
    slug = OPENING_SLUGS.get(element["kind"])
    recipe = next(
        (entry for entry in furniture_types.values() if entry.get("slug") == slug), None
    )
    if recipe is None:
        return None

    width = float(element["width_cm"])
    height = float(element["height_cm"])
    depth = float(room["wall_thickness_cm"])

    start = [face["start_x_cm"], face["start_y_cm"]]
    end = [face["end_x_cm"], face["end_y_cm"]]
    direction = wall_direction(start, end)
    position = (
        to_world(start)
        + direction * (float(element["x_offset_cm"]) + width / 2.0)
        + np.array([0.0, float(element["y_offset_cm"]) + height / 2.0, 0.0])
    )

    primitives = expand_recipe(
        recipe["parts"],
        (width, height, depth),
        element.get("colors") or {},
        recipe.get("variants"),
        element.get("variant_params") or {},
    )

    return {
        "kind": JOINERY,
        "element_id": element["id"],
        "face_label": face["label"],
        "opening_kind": element["kind"],
        "furniture_type_slug": recipe["slug"],
        "position": round_vector(position, DIGITS),
        "rotation_y": round(float(yaw_from_direction(direction)), ANGLE_DIGITS),
        "size_cm": [round(width, DIGITS), round(height, DIGITS), round(depth, DIGITS)],
        "primitives": [primitive.to_dict(DIGITS) for primitive in primitives],
        "requires_csg": requires_csg(primitives),
    }


def _net_floor_area(polygon: list[list[float]], wall_thickness: float) -> float:
    """Aire nette : celle du contour ramené de `t/2` vers l'intérieur, entre les parements.

    `floor_area_cm2` mesure le contour tel qu'il est saisi, c'est-à-dire la **ligne médiane** des
    murs. C'est l'aire du plan, pas l'aire du sol : elle compte la moitié de chaque mur. Sur la
    pièce de référence (400 sur 300, murs de 10) l'écart est de 6 %, et il monte à 20 % avec des
    murs de 30. C'est l'aire nette qui sert au devis.

    Renvoie 0 si le décalage retourne le contour sur lui-même : la pièce est alors plus étroite
    que ses propres murs, et toute valeur positive serait une invention.
    """
    if not polygon:
        return 0.0
    oriented = ensure_counter_clockwise(polygon)
    inner = offset_polygon(oriented, wall_thickness / 2.0)
    if not inner or not _keeps_its_sides(oriented, inner):
        return 0.0
    return max(signed_area(inner), 0.0)


def _keeps_its_sides(polygon: list[list[float]], offset: list[list[float]]) -> bool:
    """Vrai si chaque côté du contour décalé pointe encore dans le même sens que l'original.

    Un côté retourné signale que le décalage a dépassé la largeur de la pièce. Le contrôle du
    signe de l'aire ne suffit pas : sur un rectangle, les deux paires de côtés se croisent
    ensemble, le contour se replie en gardant son orientation, et un placard de 60 sur 40 bordé de
    murs de 200 annonce alors 22400 cm² de surface nette au lieu de rien du tout.
    """
    for index, start in enumerate(polygon):
        end = polygon[(index + 1) % len(polygon)]
        moved_start = offset[index]
        moved_end = offset[(index + 1) % len(offset)]
        original = (end[0] - start[0], end[1] - start[1])
        moved = (moved_end[0] - moved_start[0], moved_end[1] - moved_start[1])
        if original[0] * moved[0] + original[1] * moved[1] <= 0.0:
            return False
    return True


def build_room(room: dict[str, Any], furniture_types: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """Scene graph d'une seule pièce."""
    raw_polygon = room.get("polygon") or []
    polygon = ensure_counter_clockwise(raw_polygon)
    # Les segments de mur sont stockés dans l'ordre de saisie de l'utilisateur : orienter le
    # polygone ne les réoriente pas. Ce drapeau dit si leur sens de parcours est déjà le bon.
    counter_clockwise = signed_area(raw_polygon) >= 0
    height = float(room["ceiling_height_cm"])
    faces = list(room.get("faces") or [])

    nodes: list[dict[str, Any]] = []
    cameras: list[CameraPreset] = []

    if polygon:
        cameras.extend(
            [
                top_view(polygon, height),
                isometric_view(polygon, height),
                orbit_view(polygon, height),
            ]
        )

    for face in faces:
        if face["kind"] == WALL:
            if None in (face["start_x_cm"], face["start_y_cm"], face["end_x_cm"], face["end_y_cm"]):
                continue
            nodes.append(_wall_node(face, room, counter_clockwise=counter_clockwise))
            start = [face["start_x_cm"], face["start_y_cm"]]
            end = [face["end_x_cm"], face["end_y_cm"]]
            cameras.append(
                face_view(
                    face["label"],
                    start,
                    end,
                    height,
                    outward_normal(start, end, counter_clockwise=counter_clockwise),
                    polygon,
                )
            )
        elif polygon:
            altitude = 0.0 if face["kind"] == "floor" else height
            nodes.append(_horizontal_node(face, polygon, altitude))

    # Le mobilier est ajouté après les faces : le viewer construit ainsi les murs d'abord, et
    # l'ordre du JSON reste stable d'un appel à l'autre (nécessaire au cache de P10).
    for face in faces:
        for element in face.get("elements") or []:
            node = (
                _joinery_node(element, face, room, furniture_types)
                if element["kind"] in OPENING_KINDS
                else _furniture_node(
                    element,
                    face,
                    room,
                    furniture_types,
                    counter_clockwise=counter_clockwise,
                    polygon=polygon,
                )
            )
            if node is not None:
                nodes.append(node)

    return {
        "id": room["id"],
        "name": room["name"],
        "wall_thickness_cm": round(float(room["wall_thickness_cm"]), DIGITS),
        "ceiling_height_cm": round(height, DIGITS),
        "floor_area_cm2": round(abs(signed_area(polygon)), DIGITS),
        "net_floor_area_cm2": round(
            _net_floor_area(polygon, float(room["wall_thickness_cm"])), DIGITS
        ),
        "nodes": nodes,
        "cameras": [camera.to_dict(DIGITS) for camera in cameras],
    }


def build_scene_graph(
    project: dict[str, Any], furniture_types: dict[int, dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Scene graph complet d'un projet, prêt à être sérialisé en JSON."""
    catalog = furniture_types or {}
    return {
        "units": "cm",
        "project_id": project["project_id"] if "project_id" in project else project["id"],
        "rooms": [build_room(room, catalog) for room in project.get("rooms") or []],
    }
