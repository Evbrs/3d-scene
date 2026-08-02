"""Primitives vectorielles du calcul géométrique.

`numpy` est le choix de la spec §6 pour cette couche. Les fonctions sont volontairement petites
et pures : ce sont elles que les fixtures de référence vérifient point par point.
"""

import numpy as np
from numpy.typing import NDArray

Vector3 = NDArray[np.float64]

# Axe vertical du monde 3D (Y vers le haut, convention Three.js).
UP: Vector3 = np.array([0.0, 1.0, 0.0])

# En deçà, deux points sont considérés confondus. 1 µm : très en dessous de la précision utile
# d'un plan de rénovation (le centimètre), très au-dessus du bruit du flottant double.
EPSILON = 1e-6


def signed_area(polygon: list[list[float]]) -> float:
    """Aire signée d'un polygone 2D (formule du lacet).

    Positive si le polygone est décrit dans le sens trigonométrique.
    """
    if len(polygon) < 3:
        return 0.0
    points = np.asarray(polygon, dtype=np.float64)
    following = np.roll(points, -1, axis=0)
    return float(np.sum(points[:, 0] * following[:, 1] - following[:, 0] * points[:, 1]) / 2.0)


def ensure_counter_clockwise(polygon: list[list[float]]) -> list[list[float]]:
    """Renvoie le polygone orienté dans le sens trigonométrique.

    L'utilisateur dessine dans le sens qu'il veut. Sans cette normalisation, les normales
    sortantes seraient inversées une fois sur deux, et les vues « par face » regarderaient les
    murs depuis l'extérieur du logement.
    """
    if signed_area(polygon) < 0:
        return list(reversed(polygon))
    return list(polygon)


def to_world(point_2d: list[float], height: float = 0.0) -> Vector3:
    """Point du plan 2D vers le monde 3D : `X = x`, `Y = hauteur`, `Z = y`."""
    return np.array([point_2d[0], height, point_2d[1]], dtype=np.float64)


def normalize(vector: Vector3) -> Vector3:
    norm = float(np.linalg.norm(vector))
    if norm < EPSILON:
        raise ValueError("impossible de normaliser un vecteur de norme nulle")
    return vector / norm


def wall_direction(start_2d: list[float], end_2d: list[float]) -> Vector3:
    """Direction unitaire d'un mur, dans le monde 3D."""
    return normalize(to_world(end_2d) - to_world(start_2d))


def outward_normal(start_2d: list[float], end_2d: list[float]) -> Vector3:
    """Normale sortante d'un mur (pointant hors de la pièce).

    Valable pour un polygone orienté dans le sens trigonométrique — d'où
    `ensure_counter_clockwise` en amont. Voir `README.md` pour la vérification numérique.
    """
    return normalize(np.cross(UP, wall_direction(start_2d, end_2d)))


def wall_length(start_2d: list[float], end_2d: list[float]) -> float:
    return float(np.linalg.norm(to_world(end_2d) - to_world(start_2d)))


def yaw_from_direction(direction: Vector3) -> float:
    """Rotation autour de l'axe Y qui amène l'axe `+X` sur `direction`, en radians.

    C'est la seule rotation nécessaire pour poser un mur : les murs sont verticaux.

    Le `+ 0.0` n'est pas décoratif : la négation d'un `0.0` donne `-0.0`, et `atan2(-0.0, -1)`
    vaut `-π` là où `atan2(0.0, -1)` vaut `+π`. Les deux décrivent la même rotation, mais la
    sortie ne serait plus canonique — un mur orienté vers `-X` sortirait tantôt à `π`, tantôt à
    `-π`, ce qui casse la comparaison aux fixtures et l'efficacité du cache (P10).
    """
    return float(np.arctan2(-direction[2] + 0.0, direction[0]))


def polygon_centroid(polygon: list[list[float]]) -> tuple[float, float]:
    """Centroïde d'un polygone simple (et non moyenne des sommets).

    La moyenne des sommets se déplace dès qu'un côté est plus subdivisé qu'un autre ; le
    centroïde reste au centre de la surface, ce qui est ce qu'on veut pour cadrer une caméra.
    """
    points = np.asarray(polygon, dtype=np.float64)
    following = np.roll(points, -1, axis=0)
    cross = points[:, 0] * following[:, 1] - following[:, 0] * points[:, 1]
    area = float(np.sum(cross) / 2.0)

    if abs(area) < EPSILON:
        # Polygone dégénéré (tous les points alignés) : la formule du centroïde diverge.
        return float(np.mean(points[:, 0])), float(np.mean(points[:, 1]))

    centroid_x = float(np.sum((points[:, 0] + following[:, 0]) * cross) / (6.0 * area))
    centroid_y = float(np.sum((points[:, 1] + following[:, 1]) * cross) / (6.0 * area))
    return centroid_x, centroid_y


def bounding_box(polygon: list[list[float]]) -> tuple[float, float, float, float]:
    """`(min_x, min_y, max_x, max_y)` du polygone."""
    points = np.asarray(polygon, dtype=np.float64)
    return (
        float(np.min(points[:, 0])),
        float(np.min(points[:, 1])),
        float(np.max(points[:, 0])),
        float(np.max(points[:, 1])),
    )


def round_vector(vector: Vector3, digits: int = 4) -> list[float]:
    """Arrondi pour la sérialisation JSON.

    Sans arrondi, le scene graph diffère au 15ᵉ chiffre d'une machine à l'autre : les fixtures de
    référence deviendraient impossibles à comparer, et le cache (P10) ne ferait jamais mouche.
    """
    return [round(float(component) + 0.0, digits) for component in vector]
