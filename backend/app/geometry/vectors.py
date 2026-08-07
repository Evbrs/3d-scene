"""Primitives vectorielles du calcul géométrique.

`numpy` est le choix de la spec §6 pour cette couche. Les fonctions sont volontairement petites
et pures : ce sont elles que les fixtures de référence vérifient point par point.
"""

import numpy as np
from numpy.typing import NDArray

Vector3 = NDArray[np.float64]

# Points et directions du plan 2D (x, y du plan), par opposition au monde 3D.
Vector2 = NDArray[np.float64]

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


def outward_normal(
    start_2d: list[float], end_2d: list[float], *, counter_clockwise: bool = True
) -> Vector3:
    """Normale sortante d'un mur (pointant hors de la pièce).

    La formule `Yup x direction` ne donne la normale *sortante* que si le mur est parcouru dans
    le sens trigonométrique du contour. Orienter le polygone ne suffit donc pas : c'est le sens
    de parcours **du segment** qui compte, et il est stocké tel que l'utilisateur l'a saisi.
    D'où `counter_clockwise`, qui dit si les segments suivent déjà le bon sens.

    Voir `README.md` pour la vérification numérique.
    """
    normal = normalize(np.cross(UP, wall_direction(start_2d, end_2d)))
    return normal if counter_clockwise else -normal


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


def line_intersection(
    point_a: Vector2, direction_a: Vector2, point_b: Vector2, direction_b: Vector2
) -> Vector2 | None:
    """Intersection de deux droites du plan, `None` si elles sont quasi parallèles.

    Le seuil porte sur le déterminant des directions, c'est-à-dire — les directions étant
    unitaires — sur le sinus de leur angle. En deçà, l'intersection part si loin qu'elle n'a plus
    aucun sens métier : l'appelant doit se replier sur autre chose plutôt que d'utiliser un point
    à des kilomètres du plan.
    """
    determinant = float(direction_a[0] * direction_b[1] - direction_a[1] * direction_b[0])
    if abs(determinant) < EPSILON:
        return None
    delta = point_b - point_a
    travel = float(delta[0] * direction_b[1] - delta[1] * direction_b[0]) / determinant
    return point_a + travel * direction_a


def miter_extension(direction_in: Vector3, direction_out: Vector3, half_thickness: float) -> float:
    """Rallonge d'onglet : de combien allonger un mur pour que son angle se referme.

    Les deux faces d'un mur d'épaisseur `t` sont les droites décalées de ±`t/2` de son axe. À un
    angle, les faces du mur entrant et celles du mur sortant se coupent en deux points symétriques
    par rapport au sommet, à `+s` et `-s` le long du mur entrant, avec
    `s = (t/2)·tan(θ/2) = (t/2)·(1 - cos θ) / sin θ`, θ étant l'angle dont on tourne.

    On retient `|s|` : c'est la valeur qui comble la fente, qu'elle soit du côté extérieur (angle
    convexe) ou du côté intérieur (angle rentrant, où `s` est négatif). Sans onglet, chaque angle
    laisse une fente verticale de `(t/2)² ` de section sur toute la hauteur — 25 cm² pour des murs
    de 10 cm, parfaitement visible sur la vue isométrique.

    Renvoie 0 pour deux murs quasi colinéaires ou repliés l'un sur l'autre : les droites décalées
    y sont parallèles, leur intersection part à l'infini, et il n'y a de toute façon pas de fente.

    Non branchée sur `_wall_node` à ce stade : l'appliquer déplace `length_cm`, `origin` et les
    trous des murs, donc quatre valeurs figées par les fixtures 01 et 02, qu'un ticket n'a pas le
    droit de réécrire (`CLAUDE.md`). Voir le rapport du lot.
    """
    sinus = float(direction_in[0] * direction_out[2] - direction_in[2] * direction_out[0])
    if abs(sinus) < EPSILON:
        return 0.0
    cosinus = float(np.dot(direction_in, direction_out))
    return abs(half_thickness * (1.0 - cosinus) / sinus)


def offset_polygon(polygon: list[list[float]], distance: float) -> list[list[float]]:
    """Polygone dont chaque côté est décalé de `distance` vers l'intérieur.

    Chaque sommet devient l'intersection des deux droites décalées qui l'encadrent : c'est le même
    onglet que pour les murs, et c'est ce qui donne l'aire **nette** d'une pièce, celle qui reste
    entre les parements. Deux côtés colinéaires n'ayant pas d'intersection, le sommet est alors
    pris directement sur la perpendiculaire.

    Le résultat n'est fiable que tant que `distance` reste petite devant la pièce : au-delà, les
    côtés se croisent et le contour se replie sur lui-même. L'appelant doit vérifier que l'aire
    obtenue garde le signe de l'aire d'origine.
    """
    if len(polygon) < 3:
        return []

    points = np.asarray(ensure_counter_clockwise(polygon), dtype=np.float64)
    count = len(points)

    directions: list[Vector2] = []
    normals: list[Vector2] = []
    for index in range(count):
        edge = points[(index + 1) % count] - points[index]
        length = float(np.linalg.norm(edge))
        if length < EPSILON:
            # Sommet dupliqué : le côté n'a pas de direction, donc pas de droite à décaler.
            return []
        direction = edge / length
        directions.append(direction)
        # Contour trigonométrique : l'intérieur est toujours à gauche du sens de parcours.
        normals.append(np.array([-direction[1], direction[0]]))

    offset: list[list[float]] = []
    for index in range(count):
        previous = index - 1
        origin_previous = points[previous] + normals[previous] * distance
        origin_current = points[index] + normals[index] * distance
        crossing = line_intersection(
            origin_previous, directions[previous], origin_current, directions[index]
        )
        vertex = origin_current if crossing is None else crossing
        offset.append([float(vertex[0]), float(vertex[1])])
    return offset


def first_hit_distance(
    origin_2d: list[float], direction_2d: list[float], polygon: list[list[float]]
) -> float | None:
    """Distance jusqu'au premier côté du contour touché par un rayon, `None` s'il n'en touche aucun.

    Le rayon part typiquement du milieu d'un mur : le côté qui le porte est alors touché à
    distance nulle, et c'est pour l'écarter que le paramètre doit être franchement positif.
    """
    if len(polygon) < 3:
        return None

    origin = np.asarray(origin_2d, dtype=np.float64)
    direction = np.asarray(direction_2d, dtype=np.float64)
    points = np.asarray(polygon, dtype=np.float64)

    nearest: float | None = None
    for index in range(len(points)):
        start = points[index]
        edge = points[(index + 1) % len(points)] - start
        determinant = float(direction[0] * edge[1] - direction[1] * edge[0])
        if abs(determinant) < EPSILON:
            continue
        delta = start - origin
        travel = float(delta[0] * edge[1] - delta[1] * edge[0]) / determinant
        along = float(delta[0] * direction[1] - delta[1] * direction[0]) / determinant
        if travel <= EPSILON or not -EPSILON <= along <= 1.0 + EPSILON:
            continue
        if nearest is None or travel < nearest:
            nearest = travel
    return nearest


def round_vector(vector: Vector3, digits: int = 4) -> list[float]:
    """Arrondi pour la sérialisation JSON.

    Sans arrondi, le scene graph diffère au 15ᵉ chiffre d'une machine à l'autre : les fixtures de
    référence deviendraient impossibles à comparer, et le cache (P10) ne ferait jamais mouche.
    """
    return [round(float(component) + 0.0, digits) for component in vector]
