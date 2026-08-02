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
    ensure_counter_clockwise,
    outward_normal,
    round_vector,
    signed_area,
    to_world,
    wall_direction,
    wall_length,
    yaw_from_direction,
)

# Ouvertures : elles deviennent des trous dans le mur, pas des objets posés (spec §3.1).
OPENING_KINDS = frozenset({"door_hinged", "door_sliding", "window"})

WALL = "wall"
FLOOR = "floor"
CEILING = "ceiling"

DIGITS = 4


def _rect(u_min: float, v_min: float, u_max: float, v_max: float) -> list[list[float]]:
    return [
        [round(u_min, DIGITS), round(v_min, DIGITS)],
        [round(u_max, DIGITS), round(v_min, DIGITS)],
        [round(u_max, DIGITS), round(v_max, DIGITS)],
        [round(u_min, DIGITS), round(v_max, DIGITS)],
    ]


def _wall_node(face: dict[str, Any], room: dict[str, Any]) -> dict[str, Any]:
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
        _rect(
            float(element["x_offset_cm"]),
            float(element["y_offset_cm"]),
            float(element["x_offset_cm"]) + float(element["width_cm"]),
            float(element["y_offset_cm"]) + float(element["height_cm"]),
        )
        for element in face.get("elements", [])
        if element["kind"] in OPENING_KINDS
    ]

    return {
        "kind": WALL,
        "face_id": face["id"],
        "face_label": face["label"],
        "length_cm": round(length, DIGITS),
        "height_cm": round(height, DIGITS),
        # Origine du repère local : le départ du mur, au niveau du sol.
        "origin": round_vector(to_world(start), DIGITS),
        "rotation_y": round(yaw_from_direction(wall_direction(start, end)), DIGITS),
        "outward_normal": round_vector(outward_normal(start, end), DIGITS),
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

    `rotation_x = -π/2` amène le repère local `(u, v)` sur le plan horizontal `(x, z)` : le `v`
    local devient donc l'axe `y` du plan 2D, sans réécriture des coordonnées.
    """
    return {
        "kind": FLOOR if face["kind"] == "floor" else CEILING,
        "face_id": face["id"],
        "face_label": face["label"],
        "origin": [0.0, round(altitude, DIGITS), 0.0],
        "rotation_x": round(-np.pi / 2.0, DIGITS),
        "outline": [[round(x, DIGITS), round(y, DIGITS)] for x, y in polygon],
        "holes": [],
        "covering": face.get("covering") or {},
    }


def _furniture_node(
    element: dict[str, Any],
    face: dict[str, Any],
    room: dict[str, Any],
    furniture_types: dict[int, dict[str, Any]],
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
        inward = -outward_normal(start, end)
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
        # Sol et plafond : les décalages sont directement des coordonnées du plan.
        altitude = 0.0 if face["kind"] == "floor" else float(room["ceiling_height_cm"]) - height
        position = np.array(
            [
                float(element["x_offset_cm"]) + width / 2.0,
                altitude + height / 2.0,
                float(element["y_offset_cm"]) + depth / 2.0,
            ]
        )
        rotation_y = np.radians(float(element["rotation_deg"]))

    primitives = expand_recipe(
        furniture_type["parts"], (width, height, depth), element.get("colors") or {}
    )

    return {
        "kind": "furniture",
        "element_id": element["id"],
        "face_label": face["label"],
        "furniture_type_slug": furniture_type["slug"],
        "position": round_vector(position, DIGITS),
        "rotation_y": round(float(rotation_y), DIGITS),
        "size_cm": [round(width, DIGITS), round(height, DIGITS), round(depth, DIGITS)],
        "primitives": [primitive.to_dict(DIGITS) for primitive in primitives],
        "requires_csg": requires_csg(primitives),
        "variant_params": element.get("variant_params") or {},
    }


def build_room(room: dict[str, Any], furniture_types: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """Scene graph d'une seule pièce."""
    polygon = ensure_counter_clockwise(room.get("polygon") or [])
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
            nodes.append(_wall_node(face, room))
            start = [face["start_x_cm"], face["start_y_cm"]]
            end = [face["end_x_cm"], face["end_y_cm"]]
            cameras.append(
                face_view(face["label"], start, end, height, outward_normal(start, end))
            )
        elif polygon:
            altitude = 0.0 if face["kind"] == "floor" else height
            nodes.append(_horizontal_node(face, polygon, altitude))

    # Le mobilier est ajouté après les faces : le viewer construit ainsi les murs d'abord, et
    # l'ordre du JSON reste stable d'un appel à l'autre (nécessaire au cache de P10).
    for face in faces:
        for element in face.get("elements") or []:
            if element["kind"] in OPENING_KINDS:
                continue
            node = _furniture_node(element, face, room, furniture_types)
            if node is not None:
                nodes.append(node)

    return {
        "id": room["id"],
        "name": room["name"],
        "wall_thickness_cm": round(float(room["wall_thickness_cm"]), DIGITS),
        "ceiling_height_cm": round(height, DIGITS),
        "floor_area_cm2": round(abs(signed_area(polygon)), DIGITS),
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
