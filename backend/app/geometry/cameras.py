"""Presets de caméra du viewer 3D (`docs/spec-complete.md` §3.3).

Quatre points de vue : dessus (orthographique, reprend le plan 2D), isométrique, une vue par
face (élévation à plat), et une orbite libre.

Les caméras sont calculées côté serveur avec le reste du scene graph : elles dépendent de la
géométrie de la pièce, donc les recalculer côté client dupliquerait le calcul — ce que la spec
§3.1 écarte explicitement.
"""

import math
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

from app.geometry.vectors import (
    UP,
    Vector3,
    bounding_box,
    normalize,
    polygon_centroid,
    round_vector,
    to_world,
)

# Marge autour du cadrage : sans elle, la face touche exactement le bord de l'image.
FRAMING_PADDING = 1.05

# Champ de vision des caméras en perspective. 50° est un compromis courant : assez large pour
# embrasser une pièce, assez étroit pour ne pas déformer les angles.
DEFAULT_FOV_DEG = 50.0


@dataclass(frozen=True)
class CameraPreset:
    name: str
    kind: Literal["orthographic", "perspective"]
    position: Vector3
    target: Vector3
    up: Vector3
    face_label: str | None = None
    half_width_cm: float | None = None
    half_height_cm: float | None = None
    fov_deg: float | None = None

    def to_dict(self, digits: int = 4) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "position": round_vector(self.position, digits),
            "target": round_vector(self.target, digits),
            "up": round_vector(self.up, digits),
            "face_label": self.face_label,
        }
        if self.kind == "orthographic":
            payload["half_width_cm"] = round(self.half_width_cm or 0.0, digits)
            payload["half_height_cm"] = round(self.half_height_cm or 0.0, digits)
        else:
            payload["fov_deg"] = self.fov_deg
        return payload


def top_view(polygon: list[list[float]], ceiling_height_cm: float) -> CameraPreset:
    """Vue du dessus, orthographique — « reprend exactement le plan 2D » (§3.3).

    Le vecteur « haut » vaut `(0, 0, -1)` : c'est ce qui fait apparaître l'axe `y` du plan vers
    le bas de l'écran, exactement comme dans l'éditeur 2D (Konva, dont l'axe `y` descend). Une
    vue du dessus qui serait le miroir vertical du plan serait pire qu'inutile.
    """
    min_x, min_y, max_x, max_y = bounding_box(polygon)
    width, depth = max_x - min_x, max_y - min_y
    centroid_x, centroid_y = polygon_centroid(polygon)

    # Assez haut pour dominer les murs quelle que soit la taille de la pièce.
    altitude = ceiling_height_cm + max(width, depth)
    return CameraPreset(
        name="dessus",
        kind="orthographic",
        position=np.array([centroid_x, altitude, centroid_y]),
        target=np.array([centroid_x, 0.0, centroid_y]),
        up=np.array([0.0, 0.0, -1.0]),
        half_width_cm=width / 2.0 * FRAMING_PADDING,
        half_height_cm=depth / 2.0 * FRAMING_PADDING,
    )


def _isometric(
    polygon: list[list[float]], ceiling_height_cm: float, name: str
) -> CameraPreset:
    min_x, min_y, max_x, max_y = bounding_box(polygon)
    width, depth = max_x - min_x, max_y - min_y
    centroid_x, centroid_y = polygon_centroid(polygon)
    center = np.array([centroid_x, ceiling_height_cm / 2.0, centroid_y])

    distance = 2.0 * max(width, depth, ceiling_height_cm)
    direction = normalize(np.array([1.0, 1.0, 1.0]))
    return CameraPreset(
        name=name,
        kind="perspective",
        position=center + direction * distance,
        target=center,
        up=UP,
        fov_deg=DEFAULT_FOV_DEG,
    )


def isometric_view(polygon: list[list[float]], ceiling_height_cm: float) -> CameraPreset:
    """Vue isométrique 3/4, vue d'ensemble « catalogue » (§3.3)."""
    return _isometric(polygon, ceiling_height_cm, "isometrique")


def orbit_view(polygon: list[list[float]], ceiling_height_cm: float) -> CameraPreset:
    """Point de départ de l'orbite libre (§3.3).

    Même cadrage que l'isométrique : c'est une position de départ, que `OrbitControls` fera
    ensuite évoluer côté client. `target` sert de centre d'orbite.
    """
    return _isometric(polygon, ceiling_height_cm, "orbite")


def face_view(
    label: str,
    start_2d: list[float],
    end_2d: list[float],
    height_cm: float,
    outward: Vector3,
) -> CameraPreset:
    """Élévation à plat d'un mur (§3.3), orthographique.

    La caméra est placée à l'**intérieur** de la pièce, sur l'axe de la normale sortante, et
    regarde le mur : c'est ce qui donne l'élévation de la face vue depuis la pièce, celle qu'on
    veut mesurer et exporter (§3.5). La placer de l'autre côté montrerait le mur depuis
    l'extérieur du logement.
    """
    start = to_world(start_2d)
    end = to_world(end_2d)
    center = (start + end) / 2.0
    center[1] = height_cm / 2.0

    length = float(np.linalg.norm(end - start))
    # En projection orthographique, la distance ne change pas la taille apparente : elle doit
    # seulement placer la caméra hors de la géométrie.
    distance = max(length, height_cm)

    return CameraPreset(
        name=f"face-{label}",
        kind="orthographic",
        position=center - outward * distance,
        target=center,
        up=UP,
        face_label=label,
        half_width_cm=length / 2.0 * FRAMING_PADDING,
        half_height_cm=height_cm / 2.0 * FRAMING_PADDING,
    )


def degrees_to_radians(degrees: float) -> float:
    return math.radians(degrees)
