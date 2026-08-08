"""Calepinage avancé et aménagement automatique sous contraintes.

Deux moteurs qui n'ont en commun que leur nature : ils **placent** des choses, et ils le font de
façon déterministe.

**Calepinage (première moitié du module).** Il prolonge `app/geometry/quantities.py`, qui compte
déjà entières, coupes et chutes par motif — il ne le réécrit pas, et n'en importe rien de privé.
Ce que le métré ne fait pas et qu'on ajoute ici :

- le **sens de pose** : une unité de 60 x 30 posée en long ou en travers ne donne pas le même
  nombre de coupes, et c'est le poseur qui tranche aujourd'hui, à l'œil ;
- la **position de la première rangée** : une trame calée sur le coin laisse en rive tout ce qui
  reste, éventuellement une lichette de 3 cm. La règle du métier est qu'on ne pose jamais moins
  d'un tiers d'unité en rive — une coupe plus étroite casse, ne tient pas au double encollage, et
  se voit depuis la porte. On recentre alors la trame pour partager le reste entre les deux rives ;
- le **calepinage des plinthes**, que le métré ne chiffre qu'en mètres linéaires : combien de
  barres commander, combien de coupes, et quelles chutes se reposent.

**Aménagement (seconde moitié).** C'est un problème d'**optimisation sous contraintes**, pas un
problème de génération (`docs/strategie-produit.md` §3.8). On ne « crée » pas un plan : on énumère
des implantations, on écarte celles qui violent une contrainte dure, on note les autres avec un
score dont chaque terme est lisible, et on rend les deux ou trois meilleures. L'utilisateur choisit
— c'est la seule façon honnête de traiter un arbitrage qui dépend de son client.

Aucune de ces deux moitiés n'a de dépendance réseau, de modèle appris ni d'aléa : deux appels sur
la même pièce rendent le même résultat, dans le même ordre.
"""

import math
from dataclasses import dataclass
from typing import Any

from app.intelligence.ergonomy import (
    DEFAULT_THRESHOLDS,
    DOOR_HINGED,
    FURNITURE,
    Obstacle,
    Opening,
    Point,
    RoomShell,
    Sector,
    Thresholds,
    build_shell,
    footprint_overflow,
    node_footprint,
    normalise,
    openings_of,
    overlap_depth,
    passages,
    point_in_polygon,
    polygon_distance,
    polygon_hits_sector,
    rectangle,
)

EPSILON = 1e-9

# Motifs dont la trame est parallèle aux bords de la face, donc les seuls calepinables position par
# position. Même frontière que `geometry/quantities.py` : un chevron pose à 45°, sa trame ne se
# déduit pas des dimensions de la face, et lui inventer un sens de pose serait pire que se taire.
ALIGNED_PATTERNS = frozenset({"straight", "staggered"})


# --- Calepinage ------------------------------------------------------------------------------


@dataclass(frozen=True)
class LayingRules:
    """Les règles de pose, paramétrables comme les seuils d'ergonomie.

    Un carreleur allemand ne pose pas comme un carreleur français, et la longueur d'une barre de
    plinthe change avec le fournisseur : rien de tout ça n'a sa place en dur dans un calcul.
    """

    # « Jamais moins d'un tiers d'unité en rive » : en deçà, la coupe casse à la pose, ne tient pas
    # au double encollage et se voit depuis la porte.
    min_edge_fraction: float = 1.0 / 3.0
    # Longueur d'une barre de plinthe du commerce.
    skirting_bar_cm: float = 240.0
    # Une chute plus courte que ça retourne à la benne : la reposer coûte plus de manutention
    # qu'elle ne fait économiser de matière.
    skirting_min_reuse_cm: float = 30.0


DEFAULT_LAYING = LayingRules()


@dataclass(frozen=True)
class AxisPlan:
    """La découpe d'un axe de la face : où démarre la trame, et ce qu'elle laisse en rive."""

    module_cm: float
    extent_cm: float
    start_offset_cm: float
    cells: int
    edge_cuts_cm: tuple[float, ...]

    @property
    def min_edge_cut_cm(self) -> float:
        return min(self.edge_cuts_cm) if self.edge_cuts_cm else self.module_cm


def plan_axis(extent_cm: float, module_cm: float, rules: LayingRules) -> AxisPlan:
    """Où démarrer la trame sur un axe pour ne jamais poser une rive famélique.

    Soit `n` unités entières et un reste `r`. Trois cas, et un seul demande une décision :

    - `r = 0` : la face tombe juste, aucune coupe de rive, la trame part du coin ;
    - `r >= module / 3` : la coupe unique de rive est acceptable, la trame part du coin. La
      décaler produirait **deux** coupes au lieu d'une, donc plus de travail pour rien ;
    - `r < module / 3` : on recule la trame d'une demi-unité moins la moitié du reste. Les deux
      rives valent alors `(module + r) / 2`, soit au moins la moitié d'une unité — le rebut de
      3 cm devient deux coupes confortables, et c'est exactement le geste du poseur.
    """
    if module_cm <= 0.0 or extent_cm <= 0.0:
        return AxisPlan(module_cm, extent_cm, 0.0, 0, ())
    whole = math.floor(extent_cm / module_cm + EPSILON)
    remainder = extent_cm - whole * module_cm

    if whole == 0:
        # La face est plus étroite qu'une unité : il n'y a pas de trame à caler, seulement une
        # coupe, et la règle du tiers ne peut pas être tenue. On le dit plutôt que de le masquer.
        return AxisPlan(module_cm, extent_cm, 0.0, 1, (extent_cm,))
    if remainder <= EPSILON:
        return AxisPlan(module_cm, extent_cm, 0.0, whole, ())
    if remainder >= module_cm * rules.min_edge_fraction:
        return AxisPlan(module_cm, extent_cm, 0.0, whole + 1, (remainder,))

    edge = (module_cm + remainder) / 2.0
    return AxisPlan(module_cm, extent_cm, -(module_cm - remainder) / 2.0, whole + 1, (edge, edge))


def _cells_along(plan: AxisPlan, pattern_offset_cm: float = 0.0) -> list[tuple[float, float]]:
    """Les positions de la trame sur un axe, bornées à la face."""
    cells: list[tuple[float, float]] = []
    start = plan.start_offset_cm + pattern_offset_cm
    while start < plan.extent_cm - EPSILON:
        cells.append((max(start, 0.0), min(start + plan.module_cm, plan.extent_cm)))
        start += plan.module_cm
    # Une pose décalée peut faire démarrer le premier rang après l'origine : la fraction restante
    # en début de face est alors une coupe, et l'oublier sous-compterait le travail.
    if cells and cells[0][0] > EPSILON:
        cells.insert(0, (0.0, cells[0][0]))
    return cells


def _count(
    horizontal: AxisPlan,
    vertical: AxisPlan,
    pattern: str,
    holes: list[tuple[float, float, float, float]],
) -> tuple[int, int, int]:
    """`(entières, coupes, avalées)` d'une face, trame calée par `plan_axis`.

    Même lecture que le métré — une position entièrement dans un percement n'est pas posée, une
    position à cheval ou rognée par la rive est une coupe — mais appliquée à une trame **décalée**,
    ce que `build_takeoff` ne sait pas faire puisqu'il part toujours du coin.
    """
    full = 0
    cut = 0
    swallowed = 0
    for row_index, (bottom, top) in enumerate(_cells_along(vertical)):
        shift = -horizontal.module_cm / 2.0 if pattern == "staggered" and row_index % 2 else 0.0
        for left, right in _cells_along(horizontal, shift):
            if any(
                low_u <= left + EPSILON
                and right <= high_u + EPSILON
                and low_v <= bottom + EPSILON
                and top <= high_v + EPSILON
                for low_u, low_v, high_u, high_v in holes
            ):
                swallowed += 1
                continue
            whole = (
                right - left >= horizontal.module_cm - EPSILON
                and top - bottom >= vertical.module_cm - EPSILON
            )
            pierced = any(
                min(right, high_u) - max(left, low_u) > EPSILON
                and min(top, high_v) - max(bottom, low_v) > EPSILON
                for low_u, low_v, high_u, high_v in holes
            )
            if whole and not pierced:
                full += 1
            else:
                cut += 1
    return (full, cut, swallowed)


def _hole_rectangles(node: dict[str, Any]) -> list[tuple[float, float, float, float]]:
    rectangles = []
    for hole in node.get("holes") or []:
        if not hole:
            continue
        us = [float(point[0]) for point in hole]
        vs = [float(point[1]) for point in hole]
        rectangles.append((min(us), min(vs), max(us), max(vs)))
    return rectangles


def _candidate(
    label: str,
    length_cm: float,
    height_cm: float,
    unit_width_cm: float,
    unit_height_cm: float,
    pattern: str,
    holes: list[tuple[float, float, float, float]],
    rules: LayingRules,
    *,
    centred: bool,
) -> dict[str, Any]:
    naive = LayingRules(
        min_edge_fraction=0.0,
        skirting_bar_cm=rules.skirting_bar_cm,
        skirting_min_reuse_cm=rules.skirting_min_reuse_cm,
    )
    applied = rules if centred else naive
    horizontal = plan_axis(length_cm, unit_width_cm, applied)
    vertical = plan_axis(height_cm, unit_height_cm, applied)
    full, cut, swallowed = _count(horizontal, vertical, pattern, holes)
    return {
        "orientation": label,
        "unit_width_cm": unit_width_cm,
        "unit_height_cm": unit_height_cm,
        "start_offset_u_cm": round(horizontal.start_offset_cm, 3) + 0.0,
        "start_offset_v_cm": round(vertical.start_offset_cm, 3) + 0.0,
        "first_row_centred": centred,
        "full_units": full,
        "cut_units": cut,
        "swallowed_units": swallowed,
        "min_edge_cut_cm": round(
            min(horizontal.min_edge_cut_cm, vertical.min_edge_cut_cm), 3
        )
        + 0.0,
    }


def plan_face_tiling(
    node: dict[str, Any], rules: LayingRules = DEFAULT_LAYING
) -> dict[str, Any] | None:
    """Le meilleur calepinage d'une face, et les candidats écartés.

    `None` quand la question ne se pose pas : pas de revêtement dimensionné (une peinture n'a pas
    de calepinage), ou motif dont la trame n'est pas parallèle aux bords.

    Quatre candidats sont évalués, et ils sont énumérés dans un ordre fixe pour que le résultat
    soit reproductible : les deux sens de pose, chacun avec la trame calée sur le coin puis
    recentrée. On retient le moins de coupes ; à égalité, la rive la plus large, qui est ce qui se
    pose le mieux ; à égalité encore, le premier candidat, c'est-à-dire le sens déclaré.
    """
    covering = node.get("covering") or {}
    width = covering.get("unit_width_cm")
    height = covering.get("unit_height_cm")
    if width is None or height is None:
        return None
    unit_width = float(width)
    unit_height = float(height)
    if unit_width <= 0.0 or unit_height <= 0.0:
        return None
    pattern = str(covering.get("pattern") or "straight")
    if pattern not in ALIGNED_PATTERNS:
        return None
    if node["kind"] != "wall":
        # Sol et plafond : le scene graph n'en donne que le contour de la ligne médiane, et le
        # métré refuse déjà d'y calepiner une pièce non rectangulaire. Optimiser un sens de pose
        # sur une trame qu'on ne sait pas poser serait une invention.
        return None

    length_cm = float(node["length_cm"])
    height_cm = float(node["height_cm"])
    holes = _hole_rectangles(node)

    candidates = [
        _candidate("declaree", length_cm, height_cm, unit_width, unit_height, pattern, holes,
                   rules, centred=False),
        _candidate("declaree_recentree", length_cm, height_cm, unit_width, unit_height, pattern,
                   holes, rules, centred=True),
        _candidate("pivotee", length_cm, height_cm, unit_height, unit_width, pattern, holes,
                   rules, centred=False),
        _candidate("pivotee_recentree", length_cm, height_cm, unit_height, unit_width, pattern,
                   holes, rules, centred=True),
    ]
    best = min(
        range(len(candidates)),
        key=lambda index: (
            candidates[index]["cut_units"],
            -candidates[index]["min_edge_cut_cm"],
            index,
        ),
    )
    reference = candidates[0]
    chosen = candidates[best]
    return {
        "face_label": node.get("face_label"),
        "face_id": node.get("face_id"),
        "pattern": pattern,
        "chosen": chosen,
        "candidates": candidates,
        # Ce que l'optimisation fait gagner par rapport à la pose de référence du métré : trame
        # calée sur le coin, unité dans le sens déclaré. C'est le chiffre que l'artisan lit.
        "cuts_saved": reference["cut_units"] - chosen["cut_units"],
    }


def plan_room_skirting(
    room: dict[str, Any], rules: LayingRules = DEFAULT_LAYING
) -> dict[str, Any] | None:
    """Combien de barres de plinthe commander, combien de coupes, et ce que les chutes rattrapent.

    Le métré donne un métrage (`skirting_ml`) : il ne dit pas combien de barres acheter, et une
    barre entamée sur un mur se repose sur le suivant. On pose donc mur après mur, dans l'ordre du
    contour, en réemployant la chute en cours tant qu'elle dépasse la longueur minimale de
    réemploi — c'est exactement le geste du poseur, et c'est ce qui fait l'écart avec un
    `ceil(longueur / barre)` mur par mur.

    `None` si le contour au nu intérieur n'est pas reconstructible : commander des plinthes sur un
    périmètre deviné, c'est livrer un chantier court de deux barres.
    """
    shell = build_shell(room)
    if shell is None:
        return None
    openings = openings_of(room, shell.walls)

    runs: list[dict[str, Any]] = []
    for wall, (start, end) in zip(shell.walls, shell.inner_edges, strict=True):
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        # Un percement dont le bas touche le sol interrompt la plinthe : c'est le critère physique
        # du métré, et une porte-fenêtre la coupe comme une porte.
        deduction = sum(
            opening.width_cm
            for opening in openings
            if opening.wall.label == wall.label and opening.sill_cm <= 1.0
        )
        runs.append(
            {
                "face_label": wall.label,
                "length_cm": round(max(length - deduction, 0.0), 3) + 0.0,
                "deduction_cm": round(deduction, 3) + 0.0,
            }
        )

    bar = rules.skirting_bar_cm
    bars = 0
    cuts = 0
    reused = 0
    offcut = 0.0
    for run in runs:
        remaining = float(run["length_cm"])
        if remaining <= EPSILON:
            continue
        if offcut >= rules.skirting_min_reuse_cm:
            if offcut <= remaining + EPSILON:
                # La chute se pose telle quelle : aucune coupe supplémentaire.
                remaining -= offcut
                offcut = 0.0
            else:
                offcut -= remaining
                remaining = 0.0
                cuts += 1
            reused += 1
        while remaining >= bar - EPSILON:
            bars += 1
            remaining -= bar
        if remaining > EPSILON:
            bars += 1
            cuts += 1
            offcut = bar - remaining

    total_cm = sum(float(run["length_cm"]) for run in runs)
    without_reuse = sum(
        math.ceil(float(run["length_cm"]) / bar - EPSILON) for run in runs if run["length_cm"] > 0
    )
    return {
        "bar_length_cm": bar,
        "runs": runs,
        "total_length_ml": round(total_cm / 100.0, 3) + 0.0,
        "bars": bars,
        "cuts": cuts,
        "reused_offcuts": reused,
        "waste_ml": round(max(bars * bar - total_cm, 0.0) / 100.0, 3) + 0.0,
        "bars_without_reuse": without_reuse,
        "bars_saved": without_reuse - bars,
    }


# --- Aménagement automatique sous contraintes ------------------------------------------------


@dataclass(frozen=True)
class Piece:
    """Un meuble à poser, avec ce qui contraint sa place.

    Les dimensions sont celles de l'usage courant du métier, pas celles d'une marque : le catalogue
    reste paramétrique et générique (spec §4.1). Elles sont substituables comme tout le reste.
    """

    slug: str
    width_cm: float
    depth_cm: float
    height_cm: float
    # Dégagement d'usage **devant** le meuble : ce qu'il faut pour s'en servir, pas pour y passer.
    clearance_cm: float
    # Adossé à un mur. Un plan de travail, un WC, une colonne : leur dos est une face de raccord.
    against_wall: bool = True
    # Préfère un angle : une douche calée dans un angle libère le reste de la pièce.
    prefer_corner: bool = False
    # Refuse un mur percé : une tête de lit ne se met pas devant une fenêtre, et rien ne s'adosse
    # au travers d'une porte.
    needs_blank_wall: bool = False
    # Cherche le contact d'un meuble de même nature : c'est ce qui fait un plan de travail continu
    # plutôt que trois caissons dispersés.
    prefers_neighbour: bool = False


# Programmes de départ : la salle de bain et la cuisine, « les pièces les plus contraintes et les
# plus lucratives » (`docs/strategie-produit.md` §3.8), plus la chambre, qui est la pièce où
# l'adjacence demandée par la spec — tête de lit contre un mur plein — est la plus parlante. Ils
# décrivent une intention de projet, pas un catalogue : l'appelant peut en fournir un autre.
BATHROOM_PROGRAM: tuple[Piece, ...] = (
    Piece("bac-de-douche", 90.0, 90.0, 8.0, clearance_cm=60.0, prefer_corner=True),
    Piece("meuble-sous-vasque", 60.0, 45.0, 55.0, clearance_cm=70.0),
    Piece("wc", 37.0, 60.0, 40.0, clearance_cm=60.0),
)
KITCHEN_PROGRAM: tuple[Piece, ...] = (
    Piece("meuble-bas", 60.0, 60.0, 88.0, clearance_cm=90.0, prefers_neighbour=True),
    Piece("meuble-bas", 60.0, 60.0, 88.0, clearance_cm=90.0, prefers_neighbour=True),
    Piece("meuble-bas", 60.0, 60.0, 88.0, clearance_cm=90.0, prefers_neighbour=True),
    Piece("table", 120.0, 80.0, 75.0, clearance_cm=75.0, against_wall=False),
)
BEDROOM_PROGRAM: tuple[Piece, ...] = (
    # « Une tête de lit contre un mur plein et jamais devant une fenêtre » : c'est exactement ce
    # que `needs_blank_wall` exprime, et c'est une contrainte dure, pas une préférence.
    Piece("lit", 160.0, 200.0, 45.0, clearance_cm=60.0, needs_blank_wall=True),
    Piece("armoire", 120.0, 60.0, 200.0, clearance_cm=70.0, needs_blank_wall=True),
    Piece("table-de-chevet", 40.0, 35.0, 50.0, clearance_cm=40.0),
)

PROGRAMS: dict[str, tuple[Piece, ...]] = {
    "salle_de_bain": BATHROOM_PROGRAM,
    "cuisine": KITCHEN_PROGRAM,
    "chambre": BEDROOM_PROGRAM,
}

# Mots du nom de la pièce qui désignent un programme. Le nom saisi à la main est le seul indice
# dont on dispose, et l'appelant peut toujours imposer le programme explicitement.
PROGRAM_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("salle de bain", "salle_de_bain"),
    ("salle d eau", "salle_de_bain"),
    ("sdb", "salle_de_bain"),
    ("douche", "salle_de_bain"),
    ("cuisine", "cuisine"),
    ("chambre", "chambre"),
)

# Pas d'échantillonnage des positions le long d'un mur. 15 cm : personne ne pose un meuble au
# centimètre près contre un mur, et diviser le pas multiplie d'autant le coût de la recherche.
WALL_STEP_CM = 15.0
# Un meuble libre est balayé sur une grille plus lâche : il n'est contraint par aucun mur, donc
# une position voisine ne change presque rien à son score.
FREE_STEP_CM = 30.0

# Bornes de la recherche. Elles ne sont pas des réglages de confort : une route HTTP ne peut pas
# dépendre du temps que met une heuristique à converger, et un plan tordu ne doit pas pouvoir
# faire tourner le serveur pendant une minute.
MAX_CANDIDATES = 48
MAX_SEEDS = 4
MAX_PASSES = 2
MAX_EVALUATIONS = 4_000
MAX_PROPOSALS = 3

# Poids du score. Ils sont écrits ici, pas dispersés dans le calcul : le score doit rester lisible
# et discutable, c'est ce qui le distingue d'une boîte noire.
WEIGHTS: dict[str, float] = {
    "degagements": 0.40,
    "circulation": 0.30,
    "adjacences": 0.20,
    "compacite": 0.10,
}


@dataclass(frozen=True)
class Placement:
    """Un meuble posé : le centre de son emprise dans le repère du plan (amendement A4)."""

    piece: Piece
    centre: Point
    rotation_deg: float
    face_label: str | None
    footprint: tuple[Point, ...]
    clearance: tuple[Point, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.piece.slug,
            "width_cm": self.piece.width_cm,
            "depth_cm": self.piece.depth_cm,
            "height_cm": self.piece.height_cm,
            "pos_x_cm": round(self.centre[0], 1) + 0.0,
            "pos_y_cm": round(self.centre[1], 1) + 0.0,
            "rotation_deg": round(self.rotation_deg, 1) + 0.0,
            "against_face_label": self.face_label,
            "clearance_cm": self.piece.clearance_cm,
        }


def _wall_rotation(direction: Point) -> float:
    """Rotation qui aligne la largeur du meuble sur `direction`.

    `node_footprint` lit une rotation `a` comme un axe de largeur `(cos a, -sin a)` et un axe de
    profondeur `(sin a, cos a)`. Poser `a = atan2(-d.y, d.x)` aligne donc la largeur sur `d`, et
    la profondeur sur la normale à gauche de `d`. En passant la normale **rentrante** tournée d'un
    quart de tour, on obtient un meuble dont la face avant regarde la pièce et le dos touche le
    mur — toute autre convention le retournerait.

    Le `+ 0.0` n'est pas décoratif, c'est la même précaution que `yaw_from_direction` : la négation
    d'un zéro donne `-0.0`, et `atan2(-0.0, -1)` vaut `-180` là où `atan2(0.0, -1)` vaut `+180`.
    Les deux décrivent la même rotation, mais la sortie ne serait plus canonique et deux appels
    identiques rendraient deux nombres différents.
    """
    return math.degrees(math.atan2(-direction[1] + 0.0, direction[0]))


def _sample(placements: list[Placement]) -> list[Placement]:
    """Au plus `MAX_CANDIDATES` positions, prélevées à intervalle régulier.

    Prélever régulièrement plutôt que tronquer : tronquer condamnerait tout un pan de mur à ne
    jamais être essayé, alors qu'un prélèvement régulier ne fait que grossir le pas.
    """
    if len(placements) <= MAX_CANDIDATES:
        return placements
    stride = len(placements) / MAX_CANDIDATES
    return [placements[int(index * stride)] for index in range(MAX_CANDIDATES)]


def _candidates_for(piece: Piece, shell: RoomShell) -> list[Placement]:
    """Positions envisageables pour un meuble, dans un ordre fixe donc reproductible."""
    placements: list[Placement] = []
    if piece.against_wall:
        for wall, (start, end) in zip(shell.walls, shell.inner_edges, strict=True):
            span = math.hypot(end[0] - start[0], end[1] - start[1])
            if span < piece.width_cm:
                continue
            along = ((end[0] - start[0]) / span, (end[1] - start[1]) / span)
            inward = (-along[1], along[0])
            # `inward` est la normale à gauche du sens de parcours, qui n'est l'intérieur que si le
            # contour est décrit dans le sens trigonométrique — ce que les murs, stockés dans
            # l'ordre de saisie, ne garantissent pas. On tranche donc sur l'appartenance d'un point
            # d'essai au contour plutôt que sur une convention d'orientation.
            middle = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
            if not point_in_polygon(shell.polygon, (middle[0] + inward[0], middle[1] + inward[1])):
                inward = (-inward[0], -inward[1])
            rotation = _wall_rotation((inward[1], -inward[0]))
            steps = int((span - piece.width_cm) / WALL_STEP_CM) + 1
            for index in range(steps):
                travel = piece.width_cm / 2.0 + index * WALL_STEP_CM
                centre = (
                    start[0] + along[0] * travel + inward[0] * piece.depth_cm / 2.0,
                    start[1] + along[1] * travel + inward[1] * piece.depth_cm / 2.0,
                )
                placements.append(_placement(piece, centre, rotation, wall.label, inward))
        return _sample(placements)

    min_x = min(point[0] for point in shell.polygon)
    max_x = max(point[0] for point in shell.polygon)
    min_y = min(point[1] for point in shell.polygon)
    max_y = max(point[1] for point in shell.polygon)
    first = shell.inner_edges[0]
    span = math.hypot(first[1][0] - first[0][0], first[1][1] - first[0][1])
    along = ((first[1][0] - first[0][0]) / span, (first[1][1] - first[0][1]) / span)
    rotation = _wall_rotation(along)
    rows = max(int((max_y - min_y) / FREE_STEP_CM), 1)
    columns = max(int((max_x - min_x) / FREE_STEP_CM), 1)
    for row in range(rows + 1):
        for column in range(columns + 1):
            centre = (min_x + column * FREE_STEP_CM, min_y + row * FREE_STEP_CM)
            if not point_in_polygon(shell.polygon, centre):
                continue
            placements.append(_placement(piece, centre, rotation, None, (0.0, 0.0)))
    return _sample(placements)


def _placement(
    piece: Piece, centre: Point, rotation_deg: float, face_label: str | None, inward: Point
) -> Placement:
    footprint = rectangle(centre, piece.width_cm, piece.depth_cm, rotation_deg)
    if inward[0] or inward[1]:
        middle = (
            centre[0] + inward[0] * (piece.depth_cm + piece.clearance_cm) / 2.0,
            centre[1] + inward[1] * (piece.depth_cm + piece.clearance_cm) / 2.0,
        )
        clearance = rectangle(middle, piece.width_cm, piece.clearance_cm, rotation_deg)
    else:
        # Meuble libre : on se sert de lui de tous les côtés, son dégagement l'entoure donc.
        clearance = rectangle(
            centre,
            piece.width_cm + 2.0 * piece.clearance_cm,
            piece.depth_cm + 2.0 * piece.clearance_cm,
            rotation_deg,
        )
    return Placement(
        piece=piece,
        centre=centre,
        rotation_deg=rotation_deg,
        face_label=face_label,
        footprint=tuple(footprint),
        clearance=tuple(clearance),
    )


# --- Contraintes dures -----------------------------------------------------------------------


def _clear_of_openings(placement: Placement, openings: list[Opening]) -> bool:
    """Un meuble adossé ne barre pas une porte, et respecte l'exigence de mur plein.

    La comparaison se fait le long du mur : le meuble occupe `[u0, u1]`, le percement
    `[v0, v1]`, et un recouvrement les rend incompatibles. Elle est menée sur la ligne médiane du
    mur, comme tout ce que le scene graph publie des percements.
    """
    if placement.face_label is None:
        return True
    for opening in openings:
        if opening.wall.label != placement.face_label:
            continue
        touches_the_floor = opening.sill_cm <= 1.0
        if not touches_the_floor and not placement.piece.needs_blank_wall:
            continue
        wall = opening.wall
        spans = [
            (corner[0] - wall.origin[0]) * wall.direction[0]
            + (corner[1] - wall.origin[1]) * wall.direction[1]
            for corner in placement.footprint
        ]
        if min(max(spans), opening.u_max_cm) - max(min(spans), opening.u_min_cm) > 0.0:
            return False
    return True


def _collides(first: list[Point], second: list[Point], tolerance_cm: float) -> bool:
    """Deux emprises se génent-elles vraiment, le contact ne comptant pas.

    C'est le piège que la vague 3 a payé une fois : un test d'intersection strict refuse un meuble
    poussé **pile contre** un autre ou contre un mur, c'est-à-dire le geste le plus courant du
    métier. Trois caissons de cuisine alignés bord à bord seraient déclarés en conflit, et
    l'aménagement automatique ne saurait jamais produire un plan de travail continu.
    """
    return overlap_depth(first, second) > tolerance_cm


def _valid(
    placement: Placement,
    placed: list[Placement],
    shell: RoomShell,
    existing: list[list[Point]],
    openings: list[Opening],
    thresholds: Thresholds,
) -> bool:
    if (
        footprint_overflow(
            shell.polygon, list(placement.footprint), thresholds.contact_tolerance_cm
        )
        is not None
    ):
        return False
    if not _clear_of_openings(placement, openings):
        return False
    taken = [list(other.footprint) for other in placed] + existing
    return not any(
        _collides(list(placement.footprint), other, thresholds.contact_tolerance_cm)
        for other in taken
    )


def _leaves_a_door_hand(
    placements: list[Placement], swings: list[list[Sector]], existing: list[list[Point]]
) -> bool:
    """Chaque porte battante garde au moins un ferrage entièrement libre.

    Exiger que les **deux** ferrages restent libres condamnerait la moitié d'une salle de bain sans
    raison : le plan ne stocke pas la main de la porte (spec §5), et c'est précisément une décision
    que l'implantation a le droit de prendre. N'en exiger aucun reviendrait à ne rien vérifier. Un
    seul suffit, et le rapport dit lequel il suppose.
    """
    footprints = [list(placement.footprint) for placement in placements] + existing
    return all(
        any(
            all(not polygon_hits_sector(sector, footprint) for footprint in footprints)
            for sector in door
        )
        for door in swings
    )


# --- Score -----------------------------------------------------------------------------------


def _clearance_score(
    placements: list[Placement], obstacles: list[list[Point]], tolerance_cm: float
) -> float:
    """Part des meubles dont le dégagement d'usage n'est pris par rien d'autre.

    Le contact ne compte pas ici non plus : un dégagement qui affleure le caisson voisin reste un
    dégagement libre, et le pénaliser reviendrait à demander un vide entre deux meubles alignés.
    """
    if not placements:
        return 1.0
    satisfied = 0
    for index, placement in enumerate(placements):
        others = [
            list(other.footprint) for position, other in enumerate(placements) if position != index
        ] + obstacles
        if not any(
            _collides(list(placement.clearance), other, tolerance_cm) for other in others
        ):
            satisfied += 1
    return satisfied / len(placements)


def _circulation_score(
    shell: RoomShell,
    placements: list[Placement],
    existing: list[Obstacle],
    walls: list[Obstacle],
    thresholds: Thresholds,
) -> float:
    """Le passage le plus étroit de l'implantation, rapporté au seuil de circulation.

    Mesuré par la **même** fonction que le contrôle de conformité : un moteur qui proposerait des
    implantations que le contrôle refuserait ensuite ne servirait à rien.
    """
    obstacles = (
        walls
        + existing
        + [
            Obstacle(label=placement.piece.slug, footprint=list(placement.footprint))
            for placement in placements
        ]
    )
    minimum = thresholds.effective_passage_min_cm()
    narrow = passages(shell, obstacles, thresholds, below_cm=minimum)
    if not narrow:
        return 1.0
    return min(passage.gap_cm for passage in narrow) / minimum


def _adjacency_score(placements: list[Placement], shell: RoomShell) -> float:
    """Les adjacences demandées : un angle pour la douche, un voisin pour un caisson de cuisine."""
    wanted = [
        placement
        for placement in placements
        if placement.piece.prefer_corner or placement.piece.prefers_neighbour
    ]
    if not wanted:
        return 1.0
    satisfied = 0
    for placement in wanted:
        if placement.piece.prefer_corner:
            nearest = min(
                math.hypot(corner[0] - vertex[0], corner[1] - vertex[1])
                for corner in placement.footprint
                for vertex in shell.polygon
            )
            if nearest <= 2.0:
                satisfied += 1
        elif any(
            polygon_distance(list(placement.footprint), list(other.footprint)) <= 2.0
            for other in placements
            if other is not placement and other.piece.slug == placement.piece.slug
        ):
            satisfied += 1
    return satisfied / len(wanted)


def _compactness_score(placements: list[Placement], shell: RoomShell) -> float:
    """Regrouper plutôt que disperser : les réseaux d'eau et d'évacuation sont plus courts.

    Ce n'est pas de l'esthétique. Un WC à l'opposé de la vasque, c'est une saignée de plus dans la
    chape et une pente d'évacuation à tenir sur toute la longueur de la pièce.
    """
    if len(placements) < 2:
        return 1.0
    diagonal = max(
        math.hypot(first[0] - second[0], first[1] - second[1])
        for first in shell.polygon
        for second in shell.polygon
    )
    if diagonal <= EPSILON:
        return 1.0
    distances = [
        math.hypot(
            placements[first].centre[0] - placements[second].centre[0],
            placements[first].centre[1] - placements[second].centre[1],
        )
        for first in range(len(placements))
        for second in range(first + 1, len(placements))
    ]
    return max(0.0, 1.0 - (sum(distances) / len(distances)) / diagonal)


def _score(
    shell: RoomShell,
    placements: list[Placement],
    existing: list[Obstacle],
    walls: list[Obstacle],
    thresholds: Thresholds,
) -> tuple[float, dict[str, float]]:
    footprints = [obstacle.footprint for obstacle in existing]
    breakdown = {
        "degagements": _clearance_score(placements, footprints, thresholds.contact_tolerance_cm),
        "circulation": _circulation_score(shell, placements, existing, walls, thresholds),
        "adjacences": _adjacency_score(placements, shell),
        "compacite": _compactness_score(placements, shell),
    }
    total = sum(WEIGHTS[key] * value for key, value in breakdown.items())
    return (total, {key: round(value, 4) + 0.0 for key, value in breakdown.items()})


def _greedy_score(
    placement: Placement,
    placed: list[Placement],
    shell: RoomShell,
    existing: list[list[Point]],
    tolerance_cm: float,
) -> float:
    """Note d'un placement isolé, **sans** mesurer les passages.

    La construction gloutonne essaie des milliers de positions : lui faire mesurer la circulation
    de la pièce à chaque essai coûte des secondes, pour une valeur qui sera de toute façon
    recalculée à la recherche locale. On s'y limite donc à ce qui est local et bon marché — le
    dégagement libre, l'adjacence, la proximité des meubles déjà posés.
    """
    trial = [*placed, placement]
    return (
        WEIGHTS["degagements"] * _clearance_score(trial, existing, tolerance_cm)
        + WEIGHTS["adjacences"] * _adjacency_score(trial, shell)
        + WEIGHTS["compacite"] * _compactness_score(trial, shell)
    )


# --- Recherche -------------------------------------------------------------------------------


def propose_layouts(
    room: dict[str, Any],
    *,
    program: str | None = None,
    thresholds: Thresholds | None = None,
    count: int = MAX_PROPOSALS,
) -> dict[str, Any]:
    """Deux ou trois implantations **valides** et classées, jamais une seule imposée.

    La méthode est écrite en toutes lettres, parce que c'est ce qui distingue une optimisation
    d'une boîte noire :

    1. **énumération.** Chaque meuble du programme reçoit ses positions envisageables : balayage
       le long de chaque mur pour ce qui s'adosse, grille pour ce qui est libre, dans un ordre
       fixe. Au-delà de `MAX_CANDIDATES`, on prélève régulièrement plutôt que de tronquer.
    2. **contraintes dures.** Tenir dans la pièce, ne rien chevaucher, ne pas barrer une porte, ne
       pas s'adosser à un mur percé quand le meuble l'interdit, et laisser à chaque porte battante
       au moins un ferrage libre. Ce qui les viole n'est pas noté : c'est écarté.
    3. **amorces.** Une par mur : le premier meuble y est forcé, les suivants sont posés au mieux.
       Deux amorces explorent deux régions de l'espace de recherche, là où une seule descente
       gloutonne resterait prisonnière de son premier choix.
    4. **recherche locale.** Chaque amorce est améliorée en déplaçant un meuble à la fois, tant
       qu'un déplacement augmente le score, dans la limite de `MAX_PASSES` passes et d'un budget
       global d'évaluations : une route HTTP ne peut pas dépendre de la convergence d'une
       heuristique.
    5. **classement.** Score pondéré de quatre termes lisibles, tous rendus dans le résultat.

    Les `items` sont directement exploitables : `pos_x_cm` / `pos_y_cm` donnent le centre de
    l'emprise dans le repère du plan, ce qu'attend exactement `POST /api/rooms/{id}/elements`
    (spec §10, amendement A4).
    """
    applied = thresholds or DEFAULT_THRESHOLDS
    shell = build_shell(room)
    chosen = program or _program_for(str(room.get("name") or ""))
    if shell is None or chosen not in PROGRAMS:
        return _nothing(room, chosen, shell is None)

    pieces = list(PROGRAMS[chosen])
    openings = openings_of(room, shell.walls)
    swings = [
        sectors
        for opening in openings
        if opening.kind == DOOR_HINGED and (sectors := opening.swings())
    ]
    existing = [
        Obstacle(
            label=str(node.get("furniture_type_slug") or "meuble"),
            footprint=node_footprint(node),
            element_id=node.get("element_id"),
        )
        for node in room.get("nodes") or []
        if node["kind"] == FURNITURE
    ]
    existing_footprints = [obstacle.footprint for obstacle in existing]
    walls = [
        Obstacle(label=f"mur {wall.label}", footprint=[start, end], face_label=wall.label,
                 is_wall=True)
        for wall, (start, end) in zip(shell.walls, shell.inner_edges, strict=True)
    ]

    candidates = [_candidates_for(piece, shell) for piece in pieces]
    budget = _Budget(MAX_EVALUATIONS)
    scored: list[tuple[float, dict[str, float], list[Placement]]] = []

    for wall in shell.walls[:MAX_SEEDS]:
        seed = _greedy(
            pieces, candidates, shell, existing_footprints, openings, applied, wall.label
        )
        if seed is None:
            continue
        arrangement = _improve(
            seed, candidates, shell, existing, existing_footprints, walls, openings, applied,
            budget,
        )
        if not _leaves_a_door_hand(arrangement, swings, existing_footprints):
            continue
        total, breakdown = _score(shell, arrangement, existing, walls, applied)
        scored.append((total, breakdown, arrangement))

    unique: dict[
        tuple[float, ...], tuple[float, dict[str, float], list[Placement]]
    ] = {}
    for entry in scored:
        key = _signature(entry[2])
        if key not in unique or unique[key][0] < entry[0]:
            unique[key] = entry

    ranked = sorted(unique.values(), key=lambda entry: (-entry[0], _signature(entry[2])))
    proposals = [
        {
            "rank": index + 1,
            "score": round(total, 4) + 0.0,
            "breakdown": breakdown,
            "items": [placement.to_dict() for placement in arrangement],
        }
        for index, (total, breakdown, arrangement) in enumerate(ranked[:count])
    ]
    return {
        "room_id": room.get("id"),
        "program": chosen,
        "weights": dict(WEIGHTS),
        "proposals": proposals,
        "warnings": []
        if proposals
        else [
            f"pièce « {room.get('name')} » : aucune implantation ne satisfait toutes les "
            "contraintes (encombrement, dégagements, débattements). La pièce est probablement "
            "trop petite pour le programme demandé — c'est un résultat, pas une panne."
        ],
    }


def _nothing(room: dict[str, Any], program: str, no_shell: bool) -> dict[str, Any]:
    reason = (
        "les murs ne se referment pas en un contour au nu intérieur, aucune implantation ne peut "
        "être proposée."
        if no_shell
        else f"aucun programme d'aménagement ne correspond à « {program} » "
        f"(connus : {', '.join(sorted(PROGRAMS))})."
    )
    return {
        "room_id": room.get("id"),
        "program": program,
        "weights": dict(WEIGHTS),
        "proposals": [],
        "warnings": [f"pièce « {room.get('name')} » : {reason}"],
    }


class _Budget:
    """Compteur d'évaluations partagé par toutes les amorces.

    Sans lui, le coût de la recherche dépend de la forme de la pièce : une grande pièce à huit murs
    multiplie les candidats, et rien ne borne le temps de réponse. Le budget est consommé dans un
    ordre fixe, donc son épuisement est lui-même déterministe.
    """

    def __init__(self, total: int) -> None:
        self.remaining = total

    def spend(self) -> bool:
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True


def _program_for(name: str) -> str:
    normalised = normalise(name)
    for keyword, program in PROGRAM_KEYWORDS:
        if keyword in normalised:
            return program
    return "inconnu"


def _signature(arrangement: list[Placement]) -> tuple[float, ...]:
    """Clé de départage stable : à score égal, l'ordre ne doit rien devoir au hasard du tri."""
    return tuple(
        value
        for placement in arrangement
        for value in (round(placement.centre[0], 1), round(placement.centre[1], 1))
    )


def _greedy(
    pieces: list[Piece],
    candidates: list[list[Placement]],
    shell: RoomShell,
    existing: list[list[Point]],
    openings: list[Opening],
    thresholds: Thresholds,
    forced_face: str,
) -> list[Placement] | None:
    placed: list[Placement] = []
    for index in range(len(pieces)):
        options = candidates[index]
        if index == 0:
            # L'amorce force le premier meuble sur un mur donné. C'est ce qui rend les amorces
            # différentes entre elles ; sans ça, elles convergeraient toutes vers la même.
            options = [option for option in options if option.face_label == forced_face] or options
        best: tuple[float, Placement] | None = None
        for option in options:
            if not _valid(option, placed, shell, existing, openings, thresholds):
                continue
            note = _greedy_score(
                option, placed, shell, existing, thresholds.contact_tolerance_cm
            )
            if best is None or note > best[0]:
                best = (note, option)
        if best is None:
            return None
        placed.append(best[1])
    return placed


def _improve(
    arrangement: list[Placement],
    candidates: list[list[Placement]],
    shell: RoomShell,
    existing: list[Obstacle],
    existing_footprints: list[list[Point]],
    walls: list[Obstacle],
    openings: list[Opening],
    thresholds: Thresholds,
    budget: _Budget,
) -> list[Placement]:
    current = list(arrangement)
    best_total, _ = _score(shell, current, existing, walls, thresholds)
    for _ in range(MAX_PASSES):
        moved = False
        for index in range(len(current)):
            others = [item for position, item in enumerate(current) if position != index]
            for option in candidates[index]:
                if not budget.spend():
                    return current
                if not _valid(
                    option, others, shell, existing_footprints, openings, thresholds
                ):
                    continue
                trial = list(current)
                trial[index] = option
                total, _ = _score(shell, trial, existing, walls, thresholds)
                if total > best_total + 1e-6:
                    best_total = total
                    current = trial
                    moved = True
        if not moved:
            break
    return current


def plan_project_tiling(
    scene_graph: dict[str, Any], rules: LayingRules = DEFAULT_LAYING
) -> dict[str, Any]:
    """Calepinage optimisé de tout un projet : par face, puis les plinthes par pièce."""
    units = scene_graph.get("units", "cm")
    if units != "cm":
        raise ValueError(f"calepinage : scene graph en « {units} », attendu en centimètres")

    rooms = []
    for room in scene_graph.get("rooms") or []:
        faces = [
            plan
            for node in room.get("nodes") or []
            if (plan := plan_face_tiling(node, rules)) is not None
        ]
        rooms.append(
            {
                "room_id": room.get("id"),
                "name": room.get("name"),
                "faces": faces,
                "skirting": plan_room_skirting(room, rules),
                "cuts_saved": sum(int(face["cuts_saved"]) for face in faces),
            }
        )
    return {
        "project_id": scene_graph.get("project_id"),
        "rooms": rooms,
        "cuts_saved": sum(int(room["cuts_saved"]) for room in rooms),
    }
