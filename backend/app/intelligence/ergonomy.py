"""Seuils d'usage et géométrie d'ergonomie, lus sur le scene graph.

Ce module ne juge rien : il **mesure**. Le jugement — quelle mesure devient une anomalie, avec
quelle sévérité — appartient à `rules.py`. La séparation n'est pas cosmétique : une norme change,
un usage varie selon le pays, et c'est la raison pour laquelle aucun seuil n'est écrit en dur dans
une règle. Ils vivent tous dans `Thresholds`, chacun avec sa source, et un appelant peut en
substituer un sans toucher au moteur.

**Les sources des seuils sont écrites à côté de chaque champ.** Elles relèvent d'une classe
d'exigences relevée dans la réglementation et l'usage courant du bâtiment français, pas d'un avis
technique : comme la liste de mentions légales de `docs/strategie-produit.md` §2, elles doivent
être confrontées à un homme de métier avant d'être présentées comme des normes. Ce qui est garanti
ici, c'est qu'elles sont **paramétrables** et que le produit dit toujours de combien il s'écarte.

Deux conventions de repère, à ne pas confondre en relisant :

- le **monde 3D** du scene graph est en `(X, Y, Z)`, `Y` vers le haut (Three.js) ;
- le **plan** est en `(x, y)` avec `x = X` et `y = Z`. C'est le repère de `Room.polygon`, celui que
  l'éditeur 2D manipule, et le seul qu'emploient les fonctions de ce module.

La direction d'un mur se relit `(cos θ, -sin θ)` dans le plan, `θ` étant le `rotation_y` du nœud :
`yaw_from_direction` pose `θ = atan2(-dz, dx)` (`app/geometry/vectors.py`).
"""

import math
import unicodedata
from dataclasses import dataclass, field, replace
from typing import Any

Point = tuple[float, float]

WALL = "wall"
FURNITURE = "furniture"
JOINERY = "joinery"

DOOR_HINGED = "door_hinged"
DOOR_SLIDING = "door_sliding"
WINDOW = "window"
DOOR_KINDS = frozenset({DOOR_HINGED, DOOR_SLIDING})

# Bruit du flottant. Le scene graph arrondit ses longueurs à 1e-4 cm : un seuil dix fois plus fin
# laisse de la marge sans jamais confondre deux points réellement distincts.
EPSILON = 1e-9
# Tolérance de fermeture du contour, identique à celle du métré (`geometry/quantities.py`) : deux
# murs qui se suivent doivent partager un sommet au centième de millimètre près.
CLOSURE_TOLERANCE_CM = 0.01


@dataclass(frozen=True)
class Thresholds:
    """Seuils d'usage, tous substituables.

    Un seuil codé en dur dans une règle est un seuil qu'on ne peut pas corriger sans redéployer :
    une norme évolue (l'accessibilité française a changé trois fois de rédaction depuis 2005) et
    un usage varie d'un pays à l'autre. Ils sont donc rassemblés ici, et le résultat de
    l'inspection les republie, pour qu'un lecteur du rapport sache **sur quoi** on s'est prononcé.
    """

    # --- Circulation ------------------------------------------------------------------------
    # 90 cm : largeur de passage courante dans un logement, celle d'un couloir standard. En deçà,
    # on passe encore mais on ne croise plus et on ne déménage plus un meuble.
    passage_min_cm: float = 90.0
    # 60 cm : en deçà, on ne passe plus avec une caisse à outils ni un carton. C'est le seuil qui
    # transforme un inconfort en impossibilité, donc le seul qui mérite « bloquant ».
    passage_blocking_cm: float = 60.0
    # 120 cm : largeur d'un couloir dans un logement accessible (arrêté du 24 décembre 2015
    # relatif à l'accessibilité des logements neufs). Ne s'applique que si `accessible` est armé —
    # l'imposer partout noierait un chantier ordinaire sous des avertissements sans objet.
    accessible_passage_min_cm: float = 120.0
    accessible: bool = False
    # Un interstice n'est un passage que s'il **mène quelque part** : on vérifie qu'il débouche de
    # part et d'autre sur 60 cm de dégagement, soit la longueur d'un pas. Sans ce contrôle, le
    # joint de 3 cm entre deux meubles alignés contre le même mur serait annoncé comme un passage
    # infranchissable, ce qu'il n'est pas — ce n'est pas un passage du tout.
    passage_probe_cm: float = 60.0
    # Une prise, un interrupteur, un miroir : moins de 5 cm de saillie ne réduit aucun passage.
    obstacle_min_depth_cm: float = 5.0
    # On passe sous 2 m. Un meuble haut dont le dessous est au-dessus ne gêne pas la circulation
    # au sol ; un meuble haut à 1,40 m, si.
    walk_under_height_cm: float = 200.0

    # --- Ouvertures -------------------------------------------------------------------------
    # 83 cm de passage utile : c'est la valeur retenue pour un logement accessible, et celle d'un
    # bloc-porte courant de 83. En deçà, la porte reste posable mais le passage n'est plus
    # conforme à l'usage accessible.
    door_clear_width_min_cm: float = 83.0
    # 63 cm : la plus étroite des largeurs de bloc-porte couramment fabriquées (porte de WC). En
    # deçà, il n'existe pas de menuiserie standard à poser.
    door_width_blocking_cm: float = 63.0
    # Allège de fenêtre : en deçà de 90 cm au-dessus du sol fini, une protection contre les chutes
    # (garde-corps ou barre d'appui) est exigée dès que la hauteur de chute dépasse 1 m
    # (NF P01-012). Le plan ne connaît pas l'étage, donc on avertit sans bloquer.
    window_sill_min_cm: float = 90.0
    # Distance minimale entre le bord d'un percement et l'angle du mur. Le tableau et le dormant
    # doivent trouver de la matière : en deçà de l'épaisseur du mur, il n'y en a plus du tout.
    # 10 cm est la marge de pose usuelle au-delà de cette contrainte purement physique.
    opening_corner_margin_cm: float = 10.0

    # --- Pièce ------------------------------------------------------------------------------
    # 2,20 m : hauteur sous plafond minimale d'un logement décent (décret n° 2002-120 du
    # 30 janvier 2002, article 4 — 9 m² et 2,20 m, ou 20 m³ de volume habitable).
    ceiling_height_min_cm: float = 220.0

    # --- Pièces humides ---------------------------------------------------------------------
    # La cuisine est volontairement **absente** de cette liste : aucune recette du catalogue
    # (spec §4.3) ne décrit un évier, donc la règle « pièce humide sans point d'eau » y
    # produirait un faux positif systématique. Voir le rapport du lot.
    wet_room_keywords: tuple[str, ...] = (
        "salle de bain",
        "salle d eau",
        "salle-de-bain",
        "sdb",
        "douche",
        "wc",
        "toilette",
        "buanderie",
    )
    water_point_slugs: tuple[str, ...] = (
        "vasque",
        "meuble-sous-vasque",
        "baignoire",
        "bac-de-douche",
        "wc",
    )

    # --- Tolérances -------------------------------------------------------------------------
    # Deux emprises en contact ne laissent pas un passage d'un centimètre : elles se touchent.
    # Le geste le plus courant du métier est de pousser un meuble **pile contre** un mur.
    contact_tolerance_cm: float = 1.0

    def effective_passage_min_cm(self) -> float:
        """Le seuil de passage réellement appliqué, mode accessible compris."""
        return self.accessible_passage_min_cm if self.accessible else self.passage_min_cm

    def to_dict(self) -> dict[str, Any]:
        """Les seuils tels qu'ils seront republiés avec le rapport d'inspection."""
        return {
            "passage_min_cm": self.passage_min_cm,
            "passage_blocking_cm": self.passage_blocking_cm,
            "accessible_passage_min_cm": self.accessible_passage_min_cm,
            "accessible": self.accessible,
            "door_clear_width_min_cm": self.door_clear_width_min_cm,
            "door_width_blocking_cm": self.door_width_blocking_cm,
            "window_sill_min_cm": self.window_sill_min_cm,
            "opening_corner_margin_cm": self.opening_corner_margin_cm,
            "ceiling_height_min_cm": self.ceiling_height_min_cm,
        }


DEFAULT_THRESHOLDS = Thresholds()

# Seuils qu'une organisation a le droit de régler. Exactement ceux que `to_dict` republie, moins
# `accessible` qui est un **mode** demandé requête par requête et non un réglage d'entreprise.
#
# L'égalité entre les deux listes n'est pas cosmétique : on ne peut régler que ce que le rapport
# relit. Un seuil réglable mais non republié serait un réglage dont personne ne pourrait vérifier
# l'effet, et un seuil republié mais non réglable serait une promesse en l'air — c'est exactement
# l'état que l'amendement A12 décrivait comme « une ligne SQL » sans qu'aucune colonne existe.
OVERRIDABLE_THRESHOLDS: frozenset[str] = frozenset(DEFAULT_THRESHOLDS.to_dict()) - {"accessible"}


def thresholds_from(
    overrides: dict[str, Any] | None, *, accessible: bool = False
) -> Thresholds:
    """Seuils d'une organisation : les valeurs par défaut, corrigées par ses surcharges.

    C'est la contrepartie de la règle « aucun seuil n'entre par le corps d'une requête »
    (spec §10, A12) : elle n'était tenable que si un réglage existait ailleurs, et il n'existait
    nulle part — `Thresholds` était une dataclass de constantes, l'API la construisait toujours
    avec ses valeurs par défaut. La surcharge est désormais une colonne JSONB de `organization`,
    donc bel et bien « une ligne SQL » (A14).

    Une clé inconnue, non numérique ou négative est **ignorée**, jamais fatale : ces valeurs
    arrivent par `psql` ou par le back-office, et faire échouer chaque inspection sur une faute de
    frappe transformerait un réglage raté en panne. L'opérateur n'est pas laissé sans retour pour
    autant — le rapport republie les seuils **appliqués**, et il y verra immédiatement que sa ligne
    n'a rien changé.

    Zéro est refusé au même titre qu'un négatif : un seuil de passage nul rendrait conforme un
    couloir inexistant, ce qui est précisément l'abus que A12 écarte.
    """
    if not overrides:
        return Thresholds(accessible=accessible)

    retained: dict[str, Any] = {}
    for key, value in overrides.items():
        if key not in OVERRIDABLE_THRESHOLDS:
            continue
        # `bool` est un `int` en Python : sans ce filtre, `true` deviendrait un seuil de 1 cm.
        if isinstance(value, bool) or not isinstance(value, int | float):
            continue
        if value <= 0:
            continue
        retained[key] = float(value)

    # `replace` et non un constructeur à mots-clés : les champs non surchargés gardent alors leur
    # valeur par défaut sans qu'on ait à les réénumérer, et ajouter un seuil au produit ne demande
    # pas de revenir ici.
    return replace(Thresholds(accessible=accessible), **retained)


# --- Chaînes ---------------------------------------------------------------------------------

# Ce qui sépare deux mots dans un nom de pièce saisi à la main. L'apostrophe typographique (U+2019)
# y figure autant que l'apostrophe droite : « Salle d'eau » s'écrit des deux façons selon le
# clavier et la correction automatique, et les deux désignent la même pièce.
_SEPARATORS = frozenset(["'", "\u2019", "-", "_"])


def normalise(text: str) -> str:
    """Minuscules, sans accent ni apostrophe : « Salle d'Eau » et « salle d eau » se rejoignent.

    Le nom de la pièce est saisi à la main. Comparer les chaînes brutes ferait dépendre une règle
    métier de la façon dont l'utilisateur a tapé son apostrophe.
    """
    decomposed = unicodedata.normalize("NFD", text.lower())
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return "".join(" " if char in _SEPARATORS else char for char in stripped)


# --- Géométrie du plan -----------------------------------------------------------------------


def segments_intersect(a_start: Point, a_end: Point, b_start: Point, b_end: Point) -> bool:
    """Deux segments se croisent-ils, extrémités et colinéarité comprises."""

    def orientation(origin: Point, first: Point, second: Point) -> float:
        return (first[0] - origin[0]) * (second[1] - origin[1]) - (first[1] - origin[1]) * (
            second[0] - origin[0]
        )

    def on_segment(origin: Point, end: Point, probe: Point) -> bool:
        return (
            min(origin[0], end[0]) - EPSILON <= probe[0] <= max(origin[0], end[0]) + EPSILON
            and min(origin[1], end[1]) - EPSILON <= probe[1] <= max(origin[1], end[1]) + EPSILON
        )

    d1 = orientation(a_start, a_end, b_start)
    d2 = orientation(a_start, a_end, b_end)
    d3 = orientation(b_start, b_end, a_start)
    d4 = orientation(b_start, b_end, a_end)

    straddles = ((d1 > EPSILON) != (d2 > EPSILON)) and ((d3 > EPSILON) != (d4 > EPSILON))
    if straddles and min(abs(d1), abs(d2), abs(d3), abs(d4)) > EPSILON:
        return True
    for determinant, (origin, end, probe) in (
        (d1, (a_start, a_end, b_start)),
        (d2, (a_start, a_end, b_end)),
        (d3, (b_start, b_end, a_start)),
        (d4, (b_start, b_end, a_end)),
    ):
        if abs(determinant) <= EPSILON and on_segment(origin, end, probe):
            return True
    return False


def point_in_polygon(polygon: list[Point], probe: Point) -> bool:
    """Appartenance au contour par parité des croisements d'un rayon horizontal.

    Même algorithme que `app/services/faces.py` : une boîte englobante mentirait sur une pièce en
    L, où le renfoncement est *hors* de la pièce tout en étant dans la boîte. Le verdict sur le
    bord lui-même n'est pas fiable, et les appelants le traitent à part avec une tolérance.
    """
    inside = False
    for index, (start_x, start_y) in enumerate(polygon):
        end_x, end_y = polygon[(index + 1) % len(polygon)]
        if (start_y > probe[1]) != (end_y > probe[1]):
            crossing = start_x + (probe[1] - start_y) * (end_x - start_x) / (end_y - start_y)
            if probe[0] < crossing:
                inside = not inside
    return inside


def point_to_segment_distance(probe: Point, start: Point, end: Point) -> float:
    edge_x, edge_y = end[0] - start[0], end[1] - start[1]
    squared = edge_x * edge_x + edge_y * edge_y
    travel = 0.0
    if squared > EPSILON:
        travel = ((probe[0] - start[0]) * edge_x + (probe[1] - start[1]) * edge_y) / squared
        travel = min(max(travel, 0.0), 1.0)
    return math.hypot(
        probe[0] - (start[0] + travel * edge_x), probe[1] - (start[1] + travel * edge_y)
    )


def segment_distance(a_start: Point, a_end: Point, b_start: Point, b_end: Point) -> float:
    if segments_intersect(a_start, a_end, b_start, b_end):
        return 0.0
    return min(
        point_to_segment_distance(a_start, b_start, b_end),
        point_to_segment_distance(a_end, b_start, b_end),
        point_to_segment_distance(b_start, a_start, a_end),
        point_to_segment_distance(b_end, a_start, a_end),
    )


def polygon_edges(polygon: list[Point]) -> list[tuple[Point, Point]]:
    """Les côtés du contour. Un « contour » de deux points est un segment, pas un aller-retour.

    Les murs sont représentés par leur seul segment de nu intérieur : les boucler donnerait deux
    fois le même côté et fausserait toute somme.
    """
    if len(polygon) < 2:
        return []
    if len(polygon) == 2:
        return [(polygon[0], polygon[1])]
    return [(polygon[index], polygon[(index + 1) % len(polygon)]) for index in range(len(polygon))]


def polygons_overlap(first: list[Point], second: list[Point]) -> bool:
    """Recouvrement de deux contours simples, inclusion comprise.

    Un croisement de côtés ne suffit pas : un petit rectangle entièrement contenu dans un grand
    n'en a aucun. D'où les deux tests d'appartenance qui suivent.
    """
    for a_start, a_end in polygon_edges(first):
        for b_start, b_end in polygon_edges(second):
            if segments_intersect(a_start, a_end, b_start, b_end):
                return True
    if len(first) >= 3 and point_in_polygon(first, second[0]):
        return True
    return len(second) >= 3 and point_in_polygon(second, first[0])


def polygon_distance(first: list[Point], second: list[Point]) -> float:
    """Distance entre deux contours, nulle s'ils se recouvrent."""
    if polygons_overlap(first, second):
        return 0.0
    return min(
        segment_distance(a_start, a_end, b_start, b_end)
        for a_start, a_end in polygon_edges(first)
        for b_start, b_end in polygon_edges(second)
    )


def gate_between(first: list[Point], second: list[Point]) -> tuple[Point, Point]:
    """Le « portillon » du passage entre deux contours : un segment canonique, pas un minimum.

    Le couple de points le plus proche ne suffit pas, et c'est un piège coûteux : dès que deux
    obstacles se font face sur toute une longueur, ce minimum est atteint en une infinité de
    points, et celui qu'un balayage renvoie dépend de l'ordre des côtés. Le portillon se
    déplacerait alors d'un bout à l'autre du passage selon la façon dont les sommets ont été
    saisis, et le contrôle « ce passage débouche-t-il » changerait de réponse avec lui.

    On construit donc un portillon **déterminé** : la direction de rapprochement vient du couple le
    plus proche, mais sa position est prise au **milieu de la zone où les deux obstacles se font
    réellement face**, projetée perpendiculairement. Deux obstacles qui ne se font pas face du tout
    n'ont pas de zone commune : on retombe alors sur le couple le plus proche, et le contrôle de
    débouché écartera presque toujours ce faux couloir.
    """
    start, end = _nearest_points(first, second)
    span = math.hypot(end[0] - start[0], end[1] - start[1])
    if span <= EPSILON:
        return (start, end)
    toward = ((end[0] - start[0]) / span, (end[1] - start[1]) / span)
    across = (-toward[1], toward[0])

    first_across = [point[0] * across[0] + point[1] * across[1] for point in first]
    second_across = [point[0] * across[0] + point[1] * across[1] for point in second]
    low = max(min(first_across), min(second_across))
    high = min(max(first_across), max(second_across))
    if high <= low:
        return (start, end)

    middle = (low + high) / 2.0
    near = max(point[0] * toward[0] + point[1] * toward[1] for point in first)
    far = min(point[0] * toward[0] + point[1] * toward[1] for point in second)
    return (
        (across[0] * middle + toward[0] * near, across[1] * middle + toward[1] * near),
        (across[0] * middle + toward[0] * far, across[1] * middle + toward[1] * far),
    )


def _nearest_points(first: list[Point], second: list[Point]) -> tuple[Point, Point]:
    """Le couple de points le plus proche entre deux contours."""
    best = (math.inf, first[0], second[0])
    for a_start, a_end in polygon_edges(first):
        for b_start, b_end in polygon_edges(second):
            for probe, (start, end) in (
                (a_start, (b_start, b_end)),
                (a_end, (b_start, b_end)),
            ):
                foot = _closest_on_segment(probe, start, end)
                gap = math.hypot(probe[0] - foot[0], probe[1] - foot[1])
                if gap < best[0]:
                    best = (gap, probe, foot)
            for probe, (start, end) in (
                (b_start, (a_start, a_end)),
                (b_end, (a_start, a_end)),
            ):
                foot = _closest_on_segment(probe, start, end)
                gap = math.hypot(probe[0] - foot[0], probe[1] - foot[1])
                if gap < best[0]:
                    best = (gap, foot, probe)
    return (best[1], best[2])


def bounding_circle(polygon: list[Point]) -> tuple[Point, float]:
    """Centre et rayon d'un cercle qui contient le contour — un rejet à peu de frais.

    Ce n'est pas le plus petit cercle englobant, et ça n'a pas à l'être : il sert uniquement à
    écarter en trois soustractions des couples d'obstacles trop éloignés pour se gêner.
    """
    centre = (
        sum(point[0] for point in polygon) / len(polygon),
        sum(point[1] for point in polygon) / len(polygon),
    )
    radius = max(math.hypot(point[0] - centre[0], point[1] - centre[1]) for point in polygon)
    return (centre, radius)


def _closest_on_segment(probe: Point, start: Point, end: Point) -> Point:
    edge_x, edge_y = end[0] - start[0], end[1] - start[1]
    squared = edge_x * edge_x + edge_y * edge_y
    travel = 0.0
    if squared > EPSILON:
        travel = ((probe[0] - start[0]) * edge_x + (probe[1] - start[1]) * edge_y) / squared
        travel = min(max(travel, 0.0), 1.0)
    return (start[0] + travel * edge_x, start[1] + travel * edge_y)


def overlap_depth(first: list[Point], second: list[Point]) -> float:
    """Profondeur de recouvrement de deux contours **convexes**, 0 s'ils sont disjoints.

    Théorème des axes séparateurs : deux convexes sont disjoints si et seulement s'il existe un
    axe — parmi les normales à leurs côtés — sur lequel leurs projections ne se chevauchent pas.
    Le plus petit chevauchement est la distance dont il faut écarter l'un pour libérer l'autre,
    donc le « de combien » que doit porter le message d'anomalie. Les emprises de mobilier sont
    des rectangles : l'hypothèse de convexité est ici toujours vérifiée.
    """
    smallest = math.inf
    for polygon in (first, second):
        for start, end in polygon_edges(polygon):
            axis = (-(end[1] - start[1]), end[0] - start[0])
            norm = math.hypot(axis[0], axis[1])
            if norm <= EPSILON:
                continue
            unit = (axis[0] / norm, axis[1] / norm)
            first_min, first_max = _project(first, unit)
            second_min, second_max = _project(second, unit)
            overlap = min(first_max, second_max) - max(first_min, second_min)
            if overlap <= 0.0:
                return 0.0
            smallest = min(smallest, overlap)
    return 0.0 if smallest is math.inf else smallest


def _project(polygon: list[Point], axis: Point) -> tuple[float, float]:
    values = [point[0] * axis[0] + point[1] * axis[1] for point in polygon]
    return (min(values), max(values))


def footprint_overflow(
    shell: list[Point], footprint: list[Point], tolerance_cm: float
) -> float | None:
    """De combien l'emprise sort de la pièce, `None` si elle tient entièrement dedans.

    Deux façons de sortir, et la seconde n'est visible que sur une pièce non convexe :

    - un coin de l'emprise est hors du contour — le débordement est sa distance au contour ;
    - tous les coins sont dedans mais un **angle rentrant** de la pièce mord dans l'emprise. Une
      table posée en travers du renfoncement d'une pièce en L est exactement dans ce cas ; ne
      tester que les coins la laisserait traverser le mur.

    La tolérance n'est pas une commodité : un meuble mural est plaqué **pile** contre le nu
    intérieur, ses coins arrière tombent donc exactement sur le contour, et le verdict d'un test
    d'appartenance y est indécidable. Sans marge, tout ce qui est adossé serait déclaré hors de
    la pièce — c'est-à-dire le geste le plus courant du métier.
    """
    worst = 0.0
    for corner in footprint:
        if not point_in_polygon(shell, corner):
            gap = min(
                point_to_segment_distance(corner, start, end)
                for start, end in polygon_edges(shell)
            )
            if gap > tolerance_cm:
                worst = max(worst, gap)
    if worst > 0.0:
        return worst
    for vertex in shell:
        if point_in_polygon(footprint, vertex):
            gap = min(
                point_to_segment_distance(vertex, start, end)
                for start, end in polygon_edges(footprint)
            )
            if gap > tolerance_cm:
                worst = max(worst, gap)
    return worst if worst > 0.0 else None


# --- Secteurs de débattement -----------------------------------------------------------------


@dataclass(frozen=True)
class Sector:
    """Un quart de disque : le volume balayé par un vantail qui s'ouvre.

    `sweep` est **signé** — le sens de rotation dépend de la main de la porte et de l'orientation
    du mur, et le figer à `+π/2` renverrait l'arc du mauvais côté sur un mur sur deux.
    """

    hinge: Point
    radius: float
    start_angle: float
    sweep: float

    def direction(self, angle: float) -> Point:
        return (math.cos(angle), math.sin(angle))

    def radius_segments(self) -> tuple[tuple[Point, Point], tuple[Point, Point]]:
        first = self.direction(self.start_angle)
        second = self.direction(self.start_angle + self.sweep)
        return (
            (self.hinge, (self.hinge[0] + first[0] * self.radius,
                          self.hinge[1] + first[1] * self.radius)),
            (self.hinge, (self.hinge[0] + second[0] * self.radius,
                          self.hinge[1] + second[1] * self.radius)),
        )

    def contains(self, probe: Point) -> bool:
        delta_x, delta_y = probe[0] - self.hinge[0], probe[1] - self.hinge[1]
        if math.hypot(delta_x, delta_y) > self.radius + EPSILON:
            return False
        return self.angle_inside(math.atan2(delta_y, delta_x))

    def angle_inside(self, angle: float) -> bool:
        relative = _wrap(angle - self.start_angle)
        if self.sweep >= 0.0:
            return -EPSILON <= relative <= self.sweep + EPSILON
        return self.sweep - EPSILON <= relative <= EPSILON

    def midpoint(self) -> Point:
        """Le point médian de l'arc — celui sur lequel recentrer un plan."""
        angle = self.start_angle + self.sweep / 2.0
        return (
            self.hinge[0] + math.cos(angle) * self.radius / 2.0,
            self.hinge[1] + math.sin(angle) * self.radius / 2.0,
        )


def _wrap(angle: float) -> float:
    """Ramène un angle dans `(-π, π]`."""
    wrapped = math.fmod(angle, 2.0 * math.pi)
    if wrapped <= -math.pi:
        wrapped += 2.0 * math.pi
    elif wrapped > math.pi:
        wrapped -= 2.0 * math.pi
    return wrapped


def segment_hits_sector(sector: Sector, start: Point, end: Point) -> bool:
    """Un segment rencontre-t-il le quart de disque.

    Trois cas, et il faut les trois : une extrémité dedans (le segment y plonge), un croisement
    avec l'un des deux rayons (il traverse par un côté droit), un croisement avec l'arc (il
    traverse par la partie ronde). Omettre le dernier laisserait passer une cloison qui coupe
    l'arc sans jamais toucher ses rayons.
    """
    if sector.contains(start) or sector.contains(end):
        return True
    for radius_start, radius_end in sector.radius_segments():
        if segments_intersect(start, end, radius_start, radius_end):
            return True
    return any(sector.angle_inside(angle) for angle in _arc_crossings(sector, start, end))


def _arc_crossings(sector: Sector, start: Point, end: Point) -> list[float]:
    """Angles auxquels le segment coupe le cercle du secteur, entre ses extrémités."""
    direction = (end[0] - start[0], end[1] - start[1])
    offset = (start[0] - sector.hinge[0], start[1] - sector.hinge[1])
    a = direction[0] ** 2 + direction[1] ** 2
    if a <= EPSILON:
        return []
    b = 2.0 * (offset[0] * direction[0] + offset[1] * direction[1])
    c = offset[0] ** 2 + offset[1] ** 2 - sector.radius**2
    discriminant = b * b - 4.0 * a * c
    if discriminant < 0.0:
        return []
    root = math.sqrt(discriminant)
    angles = []
    for travel in ((-b - root) / (2.0 * a), (-b + root) / (2.0 * a)):
        if -EPSILON <= travel <= 1.0 + EPSILON:
            point_x = start[0] + travel * direction[0] - sector.hinge[0]
            point_y = start[1] + travel * direction[1] - sector.hinge[1]
            angles.append(math.atan2(point_y, point_x))
    return angles


def polygon_hits_sector(sector: Sector, polygon: list[Point]) -> bool:
    if len(polygon) >= 3 and point_in_polygon(polygon, sector.hinge):
        return True
    return any(
        segment_hits_sector(sector, start, end) for start, end in polygon_edges(polygon)
    )


def sectors_intersect(first: Sector, second: Sector) -> bool:
    """Deux débattements se percutent-ils.

    Deux convexes se rencontrent si et seulement si l'un contient un point de l'autre ou si leurs
    frontières se croisent. Un secteur de 90° **est** convexe, donc tester les deux charnières
    puis les frontières (rayons contre secteur, puis arc contre arc) est exhaustif.
    """
    if first.contains(second.hinge) or second.contains(first.hinge):
        return True
    for start, end in second.radius_segments():
        if segment_hits_sector(first, start, end):
            return True
    for start, end in first.radius_segments():
        if segment_hits_sector(second, start, end):
            return True
    return _arcs_intersect(first, second)


def _arcs_intersect(first: Sector, second: Sector) -> bool:
    """Intersection des deux arcs, par les points d'intersection des cercles porteurs."""
    delta_x = second.hinge[0] - first.hinge[0]
    delta_y = second.hinge[1] - first.hinge[1]
    centre_gap = math.hypot(delta_x, delta_y)
    if centre_gap <= EPSILON or centre_gap > first.radius + second.radius:
        return False
    if centre_gap < abs(first.radius - second.radius):
        return False
    along = (centre_gap**2 + first.radius**2 - second.radius**2) / (2.0 * centre_gap)
    height_squared = first.radius**2 - along**2
    if height_squared < 0.0:
        return False
    height = math.sqrt(height_squared)
    base_x = first.hinge[0] + along * delta_x / centre_gap
    base_y = first.hinge[1] + along * delta_y / centre_gap
    for sign in (1.0, -1.0):
        crossing = (
            base_x + sign * height * (-delta_y) / centre_gap,
            base_y + sign * height * delta_x / centre_gap,
        )
        if first.contains(crossing) and second.contains(crossing):
            return True
    return False


# --- Lecture du scene graph ------------------------------------------------------------------


@dataclass(frozen=True)
class Wall:
    """Un mur, relu dans le repère du plan."""

    label: str
    face_id: int | None
    origin: Point
    direction: Point
    inward: Point
    length_cm: float
    height_cm: float
    thickness_cm: float
    holes: list[tuple[float, float, float, float]]

    def at(self, along_cm: float, offset_cm: float = 0.0) -> Point:
        """Le point du plan à `along_cm` du départ, décalé de `offset_cm` vers l'intérieur."""
        return (
            self.origin[0] + self.direction[0] * along_cm + self.inward[0] * offset_cm,
            self.origin[1] + self.direction[1] * along_cm + self.inward[1] * offset_cm,
        )


@dataclass(frozen=True)
class Opening:
    """Un percement, avec ce qu'il faut pour juger de sa réalisabilité."""

    wall: Wall
    element_id: int | None
    kind: str
    u_min_cm: float
    u_max_cm: float
    sill_cm: float
    head_cm: float

    @property
    def width_cm(self) -> float:
        return self.u_max_cm - self.u_min_cm

    @property
    def centre(self) -> Point:
        return self.wall.at((self.u_min_cm + self.u_max_cm) / 2.0)

    def swings(self) -> list[Sector]:
        """Les deux ferrages possibles d'une porte battante, ouverte vers l'intérieur.

        Le modèle ne stocke **pas** la main de la porte ni son sens de battement (spec §5). Deux
        conséquences assumées, et écrites ici plutôt que devinées ailleurs :

        - on retient l'ouverture vers l'intérieur de la pièce, le seul cas où le débattement gêne
          quelque chose que le plan connaisse ;
        - on produit les deux ferrages. Une porte n'est en défaut que si **aucun** des deux ne
          passe : si l'un est libre, il suffit de ferrer la porte de ce côté-là, et l'annoncer est
          un conseil, pas une anomalie.
        """
        if self.kind != DOOR_HINGED:
            return []
        leaf = self.width_cm
        sectors = []
        for hinge_u, along in ((self.u_min_cm, 1.0), (self.u_max_cm, -1.0)):
            start = (self.wall.direction[0] * along, self.wall.direction[1] * along)
            cross = start[0] * self.wall.inward[1] - start[1] * self.wall.inward[0]
            sectors.append(
                Sector(
                    hinge=self.wall.at(hinge_u, self.wall.thickness_cm / 2.0),
                    radius=leaf,
                    start_angle=math.atan2(start[1], start[0]),
                    sweep=math.pi / 2.0 if cross > 0.0 else -math.pi / 2.0,
                )
            )
        return sectors


@dataclass(frozen=True)
class Obstacle:
    """Ce qui occupe le sol : un meuble, ou le nu intérieur d'un mur."""

    label: str
    footprint: list[Point]
    element_id: int | None = None
    face_label: str | None = None
    slug: str | None = None
    is_wall: bool = False


@dataclass
class RoomShell:
    """Le contour au nu intérieur d'une pièce, mur par mur.

    Reconstruit depuis les murs du scene graph, comme le fait déjà le métré : la scène ne
    transporte pas le polygone de la pièce, mais chaque mur porte son origine, sa rotation et sa
    longueur. La reconstruction est refaite ici plutôt qu'importée de `geometry/quantities.py`,
    dont la fonction est privée — la rendre publique pour ce seul appelant élargirait le contrat
    d'un module que ce lot n'a pas le droit de réécrire (voir le rapport).

    Contrairement au métré, on retient aussi **quel mur porte quel côté** : sans cette
    correspondance, une anomalie ne saurait pas dire entre quel mur et quel meuble le passage est
    trop étroit.
    """

    polygon: list[Point]
    walls: list[Wall]
    inner_edges: list[tuple[Point, Point]] = field(default_factory=list)


def wall_of(node: dict[str, Any], room: dict[str, Any]) -> Wall:
    origin = node["origin"]
    yaw = float(node["rotation_y"])
    normal = node["outward_normal"]
    return Wall(
        label=str(node["face_label"]),
        face_id=node.get("face_id"),
        origin=(float(origin[0]), float(origin[2])),
        direction=(math.cos(yaw), -math.sin(yaw)),
        # Le nœud publie sa normale **sortante**, déjà corrigée du sens de parcours du polygone
        # (`app/geometry/scene.py`). La retourner est plus sûr que de la recalculer : recalculer
        # obligerait à redécider l'orientation, et une erreur de signe enverrait tous les meubles
        # muraux hors du logement.
        inward=(-float(normal[0]), -float(normal[2])),
        length_cm=float(node["length_cm"]),
        height_cm=float(node["height_cm"]),
        thickness_cm=float(room["wall_thickness_cm"]),
        holes=_hole_rectangles(node),
    )


def _hole_rectangles(node: dict[str, Any]) -> list[tuple[float, float, float, float]]:
    rectangles = []
    for hole in node.get("holes") or []:
        if not hole:
            continue
        us = [float(point[0]) for point in hole]
        vs = [float(point[1]) for point in hole]
        rectangles.append((min(us), min(vs), max(us), max(vs)))
    return rectangles


def walls_of(room: dict[str, Any]) -> list[Wall]:
    """Les murs de la pièce, dans l'ordre du scene graph.

    Séparé de `build_shell` à dessein : la largeur d'une porte, l'allège d'une fenêtre ou la
    distance d'un percement à l'angle se jugent mur par mur et n'ont besoin d'aucun contour. Une
    pièce dont les murs ne se referment pas perd les règles de circulation, pas toutes les règles.
    """
    return [wall_of(node, room) for node in room.get("nodes") or [] if node["kind"] == WALL]


def build_shell(room: dict[str, Any]) -> RoomShell | None:
    """Contour au nu intérieur, ou `None` s'il n'est pas reconstructible de façon sûre.

    Le contour n'est retenu que si les murs se referment vraiment et si le décalage vers
    l'intérieur ne retourne aucun côté. Une pièce plus étroite que ses propres murs, un import
    partiel, une fixture d'un seul mur : dans tous ces cas on renonce plutôt que d'inventer un
    contour, comme le fait déjà le métré. Une règle de circulation prononcée sur un contour
    inventé serait pire que pas de règle du tout.
    """
    walls = walls_of(room)
    if len(walls) < 3:
        return None

    for index, wall in enumerate(walls):
        end = wall.at(wall.length_cm)
        following = walls[(index + 1) % len(walls)].origin
        if math.hypot(end[0] - following[0], end[1] - following[1]) > CLOSURE_TOLERANCE_CM:
            return None

    # Chaque sommet du nu intérieur est l'intersection des deux droites décalées qui l'encadrent :
    # c'est le même onglet que celui des murs (`vectors.miter_extension`), calculé ici mur par mur
    # pour garder la correspondance côté ↔ mur.
    vertices: list[Point] = []
    for index, wall in enumerate(walls):
        previous = walls[index - 1]
        crossing = _lines_intersection(
            previous.at(0.0, previous.thickness_cm / 2.0),
            previous.direction,
            wall.at(0.0, wall.thickness_cm / 2.0),
            wall.direction,
        )
        vertices.append(crossing if crossing is not None else wall.at(0.0, wall.thickness_cm / 2.0))

    inner_edges = [
        (vertices[index], vertices[(index + 1) % len(vertices)]) for index in range(len(vertices))
    ]
    # Un côté retourné signale que le décalage a dépassé la largeur de la pièce. Le contrôle du
    # signe de l'aire ne suffit pas : sur un rectangle, les deux paires de côtés se croisent
    # ensemble et le contour se replie en gardant son orientation.
    for wall, (start, end) in zip(walls, inner_edges, strict=True):
        moved = (end[0] - start[0], end[1] - start[1])
        if moved[0] * wall.direction[0] + moved[1] * wall.direction[1] <= 0.0:
            return None

    return RoomShell(polygon=vertices, walls=walls, inner_edges=inner_edges)


def _lines_intersection(
    point_a: Point, direction_a: Point, point_b: Point, direction_b: Point
) -> Point | None:
    determinant = direction_a[0] * direction_b[1] - direction_a[1] * direction_b[0]
    if abs(determinant) < 1e-6:
        return None
    delta = (point_b[0] - point_a[0], point_b[1] - point_a[1])
    travel = (delta[0] * direction_b[1] - delta[1] * direction_b[0]) / determinant
    return (point_a[0] + travel * direction_a[0], point_a[1] + travel * direction_a[1])


def node_footprint(node: dict[str, Any]) -> list[Point]:
    """Les quatre coins de l'emprise au sol d'un nœud de mobilier, après rotation.

    Même convention que `services/faces.py::free_element_footprint`, et pour cause : la géométrie
    qui décide si un meuble tient dans la pièce doit être celle qui le dessine. Une rotation
    `R_y(a)` envoie l'axe local `+X` — la largeur — sur `(cos a, 0, -sin a)` et l'axe local `+Z`
    — la profondeur — sur `(sin a, 0, cos a)` ; relus dans le plan, cela donne les deux axes
    ci-dessous. Sans cette conversion, un meuble tourné à 90° serait contrôlé avec sa largeur et
    sa profondeur échangées.
    """
    position = node["position"]
    angle = float(node["rotation_y"])
    cosine, sine = math.cos(angle), math.sin(angle)
    width_axis = (cosine, -sine)
    depth_axis = (sine, cosine)
    half_width = float(node["size_cm"][0]) / 2.0
    half_depth = float(node["size_cm"][2]) / 2.0
    centre = (float(position[0]), float(position[2]))
    return [
        (
            centre[0] + along * half_width * width_axis[0] + across * half_depth * depth_axis[0],
            centre[1] + along * half_width * width_axis[1] + across * half_depth * depth_axis[1],
        )
        for along, across in ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0))
    ]


def rectangle(centre: Point, width_cm: float, depth_cm: float, angle_deg: float) -> list[Point]:
    """Emprise rectangulaire quelconque — celle que l'aménagement automatique fait varier."""
    angle = math.radians(angle_deg)
    cosine, sine = math.cos(angle), math.sin(angle)
    width_axis = (cosine, -sine)
    depth_axis = (sine, cosine)
    return [
        (
            centre[0] + along * width_cm / 2.0 * width_axis[0]
            + across * depth_cm / 2.0 * depth_axis[0],
            centre[1] + along * width_cm / 2.0 * width_axis[1]
            + across * depth_cm / 2.0 * depth_axis[1],
        )
        for along, across in ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0))
    ]


def blocks_circulation(node: dict[str, Any], thresholds: Thresholds) -> bool:
    """Ce nœud réduit-il réellement un passage.

    Deux exclusions, sans lesquelles le rapport se noie sous des anomalies sans objet :

    - ce dont le dessous est au-dessus de la hauteur de passage : on marche dessous ;
    - ce qui fait moins de quelques centimètres de saillie : une prise, un interrupteur, un
      miroir ne rétrécissent aucun couloir.
    """
    height = float(node["size_cm"][1])
    underside = float(node["position"][1]) - height / 2.0
    if underside >= thresholds.walk_under_height_cm:
        return False
    return float(node["size_cm"][2]) >= thresholds.obstacle_min_depth_cm


def obstacles_of(
    room: dict[str, Any], shell: RoomShell, thresholds: Thresholds
) -> list[Obstacle]:
    """Tout ce qui occupe le sol de la pièce : le mobilier, puis les murs eux-mêmes.

    Les murs entrent dans la même liste que les meubles, et c'est délibéré : un passage entre un
    lit et le mur d'en face se mesure exactement comme un passage entre deux lits, et les deux
    murs opposés d'un couloir donnent gratuitement la largeur de ce couloir.
    """
    obstacles = [
        Obstacle(
            label=str(node.get("furniture_type_slug") or "meuble"),
            footprint=node_footprint(node),
            element_id=node.get("element_id"),
            face_label=node.get("face_label"),
            slug=node.get("furniture_type_slug"),
        )
        for node in room.get("nodes") or []
        if node["kind"] == FURNITURE and blocks_circulation(node, thresholds)
    ]
    obstacles.extend(
        Obstacle(
            label=f"mur {wall.label}",
            footprint=[start, end],
            face_label=wall.label,
            is_wall=True,
        )
        for wall, (start, end) in zip(shell.walls, shell.inner_edges, strict=True)
    )
    return obstacles


@dataclass(frozen=True)
class Passage:
    """Un interstice franchissable entre deux obstacles, et le portillon qui le mesure."""

    gap_cm: float
    gate: tuple[Point, Point]
    first: Obstacle
    second: Obstacle

    @property
    def middle(self) -> Point:
        return ((self.gate[0][0] + self.gate[1][0]) / 2.0,
                (self.gate[0][1] + self.gate[1][1]) / 2.0)


def leads_somewhere(shell: RoomShell, gate: tuple[Point, Point], thresholds: Thresholds) -> bool:
    """Cet interstice mène-t-il quelque part.

    On sonde de part et d'autre du portillon, perpendiculairement à lui, sur la longueur d'un pas.
    Un interstice bouché d'un côté — la fente entre deux meubles poussés contre le même mur — n'a
    jamais été un passage, et l'annoncer comme trop étroit ferait passer le contrôle de conformité
    pour un générateur d'alertes.
    """
    start, end = gate
    middle = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
    if not point_in_polygon(shell.polygon, middle):
        return False
    span = math.hypot(end[0] - start[0], end[1] - start[1])
    if span <= EPSILON:
        return False
    across = (-(end[1] - start[1]) / span, (end[0] - start[0]) / span)
    for sign in (1.0, -1.0):
        probe = (
            middle[0] + across[0] * sign * thresholds.passage_probe_cm,
            middle[1] + across[1] * sign * thresholds.passage_probe_cm,
        )
        if not point_in_polygon(shell.polygon, probe):
            return False
    return True


def passages(
    shell: RoomShell, obstacles: list[Obstacle], thresholds: Thresholds, *, below_cm: float
) -> list[Passage]:
    """Les passages plus étroits que `below_cm`, obstacles pris deux à deux.

    Mutualisé entre le contrôle de conformité, qui en fait des anomalies, et l'aménagement
    automatique, qui s'en sert pour noter une implantation. Les deux doivent mesurer un passage de
    la même façon, sans quoi le moteur proposerait des plans que le contrôle refuserait ensuite.
    """
    found: list[Passage] = []
    circles = [bounding_circle(obstacle.footprint) for obstacle in obstacles]
    for index, first in enumerate(obstacles):
        for offset, second in enumerate(obstacles[index + 1 :]):
            other = index + 1 + offset
            # Rejet rapide par cercles englobants. Sans lui, chaque appel compare 16 couples de
            # côtés pour tous les couples d'obstacles, et l'aménagement automatique — qui note des
            # milliers d'implantations — devient une route HTTP qui répond en secondes.
            centre_gap = math.hypot(
                circles[index][0][0] - circles[other][0][0],
                circles[index][0][1] - circles[other][0][1],
            )
            if centre_gap - circles[index][1] - circles[other][1] >= below_cm:
                continue
            if first.is_wall and second.is_wall and _share_a_vertex(first, second):
                # Deux murs voisins se rejoignent : leur « écart » est l'angle de la pièce et non
                # un passage. Le test de contact ci-dessous le dirait aussi — mais pas si l'onglet
                # a laissé un micron entre les deux sommets calculés.
                continue
            gap = polygon_distance(first.footprint, second.footprint)
            if gap <= thresholds.contact_tolerance_cm or gap >= below_cm:
                continue
            gate = gate_between(first.footprint, second.footprint)
            if not leads_somewhere(shell, gate, thresholds):
                continue
            found.append(Passage(gap_cm=gap, gate=gate, first=first, second=second))
    return found


def _share_a_vertex(first: Obstacle, second: Obstacle) -> bool:
    return any(
        math.hypot(a[0] - b[0], a[1] - b[1]) <= 1.0
        for a in first.footprint
        for b in second.footprint
    )


def openings_of(room: dict[str, Any], walls: list[Wall]) -> list[Opening]:
    """Les percements de la pièce, enrichis de leur nature quand elle est connaissable.

    Le percement lui-même vient du mur (`holes`), qui l'a déjà borné au rectangle du mur. Sa
    **nature** — porte battante, coulissante, fenêtre — n'est portée que par le nœud de
    menuiserie, lequel n'existe que si le catalogue a été fourni au calcul de la scène. Quand il
    manque, on retombe sur le critère physique du métré : un percement dont le bas touche le sol
    est une porte — **battante**, faute de mieux, donc son débattement est contrôlé. C'est le pire
    des deux replis possibles pour une coulissante, mais l'inverse laisserait passer sans un mot le
    cas le plus fréquent. L'API fournit toujours le catalogue des menuiseries (`app/api/scene.py`),
    donc ce repli ne concerne qu'un appelant qui construit la scène sans lui.

    Rattacher les deux par la position plutôt que par le rang est indispensable : un percement
    débordant du mur est écarté par `_clipped_hole` sans que sa menuiserie le soit, et les deux
    listes ne sont alors plus alignées.
    """
    joinery = [node for node in room.get("nodes") or [] if node["kind"] == JOINERY]
    openings: list[Opening] = []
    for wall in walls:
        for u_min, v_min, u_max, v_max in wall.holes:
            centre_u = (u_min + u_max) / 2.0
            centre_v = (v_min + v_max) / 2.0
            match = _matching_joinery(joinery, wall, centre_u, centre_v)
            openings.append(
                Opening(
                    wall=wall,
                    element_id=None if match is None else match.get("element_id"),
                    kind=(
                        str(match["opening_kind"])
                        if match is not None
                        else (DOOR_HINGED if v_min <= 1.0 else WINDOW)
                    ),
                    u_min_cm=u_min,
                    u_max_cm=u_max,
                    sill_cm=v_min,
                    head_cm=v_max,
                )
            )
    return openings


def _matching_joinery(
    joinery: list[dict[str, Any]], wall: Wall, centre_u: float, centre_v: float
) -> dict[str, Any] | None:
    for node in joinery:
        if node.get("face_label") != wall.label:
            continue
        position = node["position"]
        offset = (float(position[0]) - wall.origin[0], float(position[2]) - wall.origin[1])
        along = offset[0] * wall.direction[0] + offset[1] * wall.direction[1]
        if abs(along - centre_u) <= 1.0 and abs(float(position[1]) - centre_v) <= 1.0:
            return node
    return None
