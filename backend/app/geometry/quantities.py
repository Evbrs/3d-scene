"""Métré (« takeoff ») : les quantités chiffrables d'un scene graph.

Fonction pure, sans base de données ni entrée-sortie : elle ne lit que le dictionnaire produit par
`app.geometry.scene.build_scene_graph`, où tout est déjà calculé (`length_cm`, `height_cm`,
`holes`, `outline`, `net_floor_area_cm2`). C'est ce qui permet de la vérifier par fixtures, comme
le reste de `app/geometry/` (`docs/plan-generation-ia.md` §6).

Trois règles commandent tout le module.

**On chiffre sur `net_floor_area_cm2`, jamais sur `floor_area_cm2`.** Le second est l'aire du
contour tel qu'il est saisi, c'est-à-dire la ligne médiane des murs : il compte la moitié de chaque
mur et surévalue de 6 % (murs de 10 cm) à 20 % (murs de 30 cm). Facturer là-dessus, c'est un
litige. Quand la surface nette manque, le métré le dit dans `warnings` et laisse la valeur à
`None` — il ne se replie pas silencieusement sur l'autre. Aucune aire de ligne médiane n'est
d'ailleurs exposée par ce module, pour qu'aucun appelant ne puisse s'y tromper.

**La même prudence vaut pour les linéaires.** Une plinthe et une corniche se posent au nu
intérieur, pas sur l'axe des murs : le périmètre médian d'une pièce de 400 x 300 aux murs de 10
vaut 1400 cm là où la plinthe en fait 1360. Le métré reconstruit donc le contour au nu intérieur,
et renonce (`None` + avertissement) quand les murs ne se referment pas.

**Les unités de sortie sont celles d'un devis** : des m², des ml, des m³ et des unités entières.
La conversion depuis les centimètres du scene graph se fait ici, une seule fois, et non dans
chaque appelant (`docs/strategie-produit.md` §3.1).
"""

from collections.abc import Iterable
from math import ceil, cos, hypot, sin
from typing import Any

from app.geometry.vectors import offset_polygon, signed_area

WALL = "wall"
FLOOR = "floor"
CEILING = "ceiling"
FURNITURE = "furniture"
# Les seules natures de nœud qui portent un revêtement, donc les seules à métrer au m². Le
# mobilier et les menuiseries ne se chiffrent pas à la surface : ils se comptent à l'unité, et
# c'est un autre métier — voir `_furniture_lines` et l'amendement A7.
COVERED_KINDS = (WALL, FLOOR, CEILING)

CM_PER_M = 100.0
CM2_PER_M2 = 10_000.0
CM3_PER_M3 = 1_000_000.0

# Un devis se lit au décimètre : trois décimales suffisent en m², ml et m³, et arrondir plus fin
# ferait apparaître le bruit du flottant dans un document contractuel.
AREA_DIGITS = 3
LENGTH_DIGITS = 3
VOLUME_DIGITS = 3
RATIO_DIGITS = 4

# Taux de chute par motif de pose. Ce sont les provisions du métier, pas des valeurs inventées :
# elles couvrent les coupes de rive, la casse et le rattrapage de trame. On ne les recalcule pas
# à partir de la géométrie parce que le réemploi des chutes d'une coupe à l'autre est laissé à
# l'appréciation du poseur : une chute géométrique « exacte » donnerait 20 % sur un mur où un
# carreleur réemploie et 0 % sur un mur où il ne réemploie pas. Voir `docs/strategie-produit.md`
# §3.8 : c'est le chiffre qui rend le devis crédible auprès d'un homme de métier.
WASTE_RATIO_BY_PATTERN: dict[str, float] = {
    "straight": 0.08,
    # Pose décalée (« à coupe de pierre ») : le demi-module de décalage coupe les DEUX abouts
    # d'un rang sur deux, là où une pose droite n'en coupe qu'un.
    "staggered": 0.10,
    # Pose en diagonale : les quatre rives sont coupées au lieu de deux (`LayingPattern.DIAGONAL`,
    # spec §10, amendement A8).
    "diagonal": 0.12,
    "chevron": 0.15,
    # Bâton rompu : même trame à 45° que le chevron, mêmes coupes de rive.
    "herringbone": 0.15,
}
DEFAULT_PATTERN = "straight"

# Seuls ces motifs posent une trame parallèle aux bords de la face : eux seuls permettent de
# compter des unités entières et des coupes. Un chevron pose à 45°, sa trame ne se déduit pas des
# dimensions de la face, et inventer un décompte y serait pire que ne rien annoncer.
ALIGNED_PATTERNS = frozenset({"straight", "staggered"})

# Au-delà, le décompte cellule par cellule ne dit plus rien d'utile (une mosaïque de 1 cm sur un
# mur de 10 m) et coûte plus cher que le reste du métré réuni.
MAX_TRAME_CELLS = 100_000

# Un percement dont le bas touche le sol interrompt la plinthe : c'est le critère physique, et il
# vaut mieux que la nature déclarée de l'ouverture, qui n'existe dans le scene graph que si le
# catalogue de menuiseries a été fourni. Une porte-fenêtre coupe la plinthe comme une porte.
FLOOR_LEVEL_TOLERANCE_CM = 1.0

# Tolérance de fermeture du contour : deux murs qui se suivent doivent partager un sommet. Le
# scene graph arrondit les origines à 1e-4 cm, un centième de millimètre laisse donc de la marge
# sans accepter un vrai trou dans le contour.
CLOSURE_TOLERANCE_CM = 0.01

# Bruit du flottant : sert à ne pas transformer `400 / 50 = 8.000000000000002` en neuf colonnes.
EPSILON = 1e-9

# Deux côtés sont perpendiculaires si le cosinus de leur angle est nul. La tolérance porte sur ce
# cosinus, pas sur le produit scalaire brut : sans normalisation par les longueurs, un rectangle
# de 10 m serait jugé moins droit qu'un rectangle de 1 m à angle égal.
RIGHT_ANGLE_COSINE_TOLERANCE = 1e-6


# --- Petites conversions -------------------------------------------------------------------------


def _to_m2(value_cm2: float) -> float:
    return round(value_cm2 / CM2_PER_M2, AREA_DIGITS) + 0.0


def _to_ml(value_cm: float) -> float:
    return round(value_cm / CM_PER_M, LENGTH_DIGITS) + 0.0


def _to_m3(value_cm3: float) -> float:
    return round(value_cm3 / CM3_PER_M3, VOLUME_DIGITS) + 0.0


def _ceil_units(value: float) -> int:
    """Arrondi supérieur tolérant au bruit du flottant.

    `10 m² à 8 % de chute / 0.25 m²` vaut 43.2 et demande 44 unités ; mais un calcul qui retombe
    sur 44.000000000000004 en demanderait 45, et la fixture ne serait plus reproductible.
    """
    return ceil(value - EPSILON)


def _rounded_sum(values: Iterable[float | None], digits: int = AREA_DIGITS) -> float:
    """Somme dont les inconnues (`None`) sont ignorées, jamais comptées pour zéro.

    L'avertissement qui accompagne chaque inconnue reste, lui, dans `warnings` : c'est là qu'un
    appelant lit qu'un total est partiel.
    """
    return round(sum(value for value in values if value is not None), digits) + 0.0


# --- Géométrie du contour ------------------------------------------------------------------------


def _wall_end(node: dict[str, Any]) -> tuple[float, float]:
    """Extrémité d'un mur, dans le plan 2D.

    `yaw_from_direction` pose `rotation_y = atan2(-dz, dx)` : la direction du mur se relit donc
    `(cos, -sin)` dans le plan, l'axe `z` du monde 3D étant l'axe `y` du plan.
    """
    origin = node["origin"]
    yaw = float(node["rotation_y"])
    length = float(node["length_cm"])
    return (float(origin[0]) + length * cos(yaw), float(origin[2]) - length * sin(yaw))


def _plan_contour(walls: list[dict[str, Any]]) -> list[list[float]] | None:
    """Contour de la pièce sur la ligne médiane, reconstruit depuis les murs.

    Le scene graph ne transporte pas le polygone de la pièce, mais chaque mur porte son origine,
    sa rotation et sa longueur : le contour s'en redéduit. On ne le retient que s'il se referme
    vraiment, la fin de chaque mur tombant sur le départ du suivant. Une pièce dont les murs ne
    s'enchaînent pas — une fixture d'un seul mur, un import partiel — n'a pas de contour, et lui
    en inventer un ferait facturer une plinthe qui n'existe pas.
    """
    if len(walls) < 3:
        return None
    contour = [[float(node["origin"][0]), float(node["origin"][2])] for node in walls]
    for index, node in enumerate(walls):
        end_x, end_y = _wall_end(node)
        next_x, next_y = contour[(index + 1) % len(contour)]
        if hypot(end_x - next_x, end_y - next_y) > CLOSURE_TOLERANCE_CM:
            return None
    return contour


def _perimeter_cm(polygon: list[list[float]]) -> float:
    total = 0.0
    for index, (x, y) in enumerate(polygon):
        following = polygon[(index + 1) % len(polygon)]
        total += hypot(following[0] - x, following[1] - y)
    return total


def _rectangle_sides(polygon: list[list[float]]) -> tuple[float, float] | None:
    """Les deux côtés du contour s'il est un rectangle, `None` sinon.

    C'est la condition d'existence d'une trame de calepinage sur un sol : sur une pièce en L, une
    trame rectangulaire ne décrit rien, et un décompte d'unités entières y serait une invention.
    """
    if len(polygon) != 4:
        return None
    edges = [
        (
            polygon[(index + 1) % 4][0] - vertex[0],
            polygon[(index + 1) % 4][1] - vertex[1],
        )
        for index, vertex in enumerate(polygon)
    ]
    lengths = [hypot(edge[0], edge[1]) for edge in edges]
    if any(length <= EPSILON for length in lengths):
        return None
    for index in range(4):
        current, following = edges[index], edges[(index + 1) % 4]
        dot = current[0] * following[0] + current[1] * following[1]
        cosine = abs(dot) / (lengths[index] * lengths[(index + 1) % 4])
        if cosine > RIGHT_ANGLE_COSINE_TOLERANCE:
            return None
    return (lengths[0], lengths[1])


# --- Percements ----------------------------------------------------------------------------------


def _hole_rectangles(node: dict[str, Any]) -> list[tuple[float, float, float, float]]:
    """Les percements de la face, en `(u_min, v_min, u_max, v_max)`.

    Le scene graph les émet déjà bornés au rectangle du mur (`_clipped_hole`) : il n'y a rien à
    revalider ici, seulement à relire.
    """
    rectangles = []
    for hole in node.get("holes") or []:
        if not hole:
            continue
        us = [float(point[0]) for point in hole]
        vs = [float(point[1]) for point in hole]
        rectangles.append((min(us), min(vs), max(us), max(vs)))
    return rectangles


def _touches_the_floor(rectangle: tuple[float, float, float, float]) -> bool:
    return rectangle[1] <= FLOOR_LEVEL_TOLERANCE_CM


# --- Calepinage ----------------------------------------------------------------------------------


def _cells_along(extent: float, module: float, offset: float) -> list[tuple[float, float]]:
    """Découpe d'un côté de longueur `extent` en cellules de `module`, la trame partant d'`offset`.

    `offset` est négatif en pose décalée : le rang commence alors par une demi-unité, qui est une
    coupe. Les cellules sont bornées au côté, si bien qu'une cellule plus étroite que le module
    est, par construction, une coupe.
    """
    cells: list[tuple[float, float]] = []
    start = offset
    while start < extent - EPSILON:
        cells.append((max(start, 0.0), min(start + module, extent)))
        start += module
    return cells


def _overlap(low_a: float, high_a: float, low_b: float, high_b: float) -> float:
    return min(high_a, high_b) - max(low_a, low_b)


def _trame_counts(
    trame_cm: tuple[float, float],
    unit_width_cm: float,
    unit_height_cm: float,
    pattern: str,
    holes: list[tuple[float, float, float, float]],
) -> tuple[int, int] | None:
    """`(unités entières, coupes)` d'une face rectangulaire, `None` si la trame est hors de portée.

    La trame part du coin d'origine de la face — c'est ce que veut dire « pose droite alignée sur
    le départ du mur ». Chaque position vaut une unité :

    - entièrement contenue dans un percement, elle n'est pas posée du tout ;
    - à cheval sur un percement, ou plus étroite que le module parce qu'elle borde la face, elle
      est une coupe ;
    - sinon, c'est une unité entière.

    C'est un décompte, pas une prévision de commande : le nombre d'unités à commander se déduit de
    la surface et du taux de chute, parce qu'une coupe consomme rarement une unité entière.
    """
    width, height = trame_cm
    if width <= 0.0 or height <= 0.0:
        return None
    if (width / unit_width_cm) * (height / unit_height_cm) > MAX_TRAME_CELLS:
        return None

    rows = _cells_along(height, unit_height_cm, 0.0)
    full = 0
    cut = 0
    for row_index, (bottom, top) in enumerate(rows):
        # Pose décalée : un rang sur deux recule d'une demi-unité, ce qui coupe ses deux abouts.
        offset = -unit_width_cm / 2.0 if pattern == "staggered" and row_index % 2 else 0.0
        for left, right in _cells_along(width, unit_width_cm, offset):
            if any(
                low_u <= left + EPSILON
                and right <= high_u + EPSILON
                and low_v <= bottom + EPSILON
                and top <= high_v + EPSILON
                for low_u, low_v, high_u, high_v in holes
            ):
                continue
            whole = (
                right - left >= unit_width_cm - EPSILON and top - bottom >= unit_height_cm - EPSILON
            )
            pierced = any(
                _overlap(left, right, low_u, high_u) > EPSILON
                and _overlap(bottom, top, low_v, high_v) > EPSILON
                for low_u, low_v, high_u, high_v in holes
            )
            if whole and not pierced:
                full += 1
            else:
                cut += 1
    return (full, cut)


def _tiling(
    net_area_cm2: float,
    covering: dict[str, Any],
    *,
    trame_cm: tuple[float, float] | None,
    holes: list[tuple[float, float, float, float]],
    face_label: str,
    warnings: list[str],
) -> dict[str, Any] | None:
    """Calepinage d'une face, ou `None` si le revêtement ne déclare pas de dimensions d'unité.

    Un revêtement sans unité (une peinture) n'a pas de calepinage : ce n'est pas une anomalie.
    Un revêtement qui n'en déclare qu'une seule, en revanche, est une saisie inachevée, et on le
    signale plutôt que de deviner la seconde.
    """
    width = covering.get("unit_width_cm")
    height = covering.get("unit_height_cm")
    if width is None and height is None:
        return None
    if width is None or height is None:
        warnings.append(
            f"face {face_label} : le revêtement ne déclare qu'une dimension d'unité "
            "(`unit_width_cm` ou `unit_height_cm`) — calepinage impossible."
        )
        return None

    unit_width_cm = float(width)
    unit_height_cm = float(height)
    if unit_width_cm <= 0.0 or unit_height_cm <= 0.0:
        warnings.append(
            f"face {face_label} : dimension d'unité nulle ou négative — calepinage impossible."
        )
        return None

    pattern = str(covering.get("pattern") or DEFAULT_PATTERN)
    waste_ratio = WASTE_RATIO_BY_PATTERN.get(pattern)
    if waste_ratio is None:
        warnings.append(
            f"face {face_label} : motif de pose « {pattern} » inconnu — chute provisionnée comme "
            f"une pose droite ({WASTE_RATIO_BY_PATTERN[DEFAULT_PATTERN]:.0%})."
        )
        waste_ratio = WASTE_RATIO_BY_PATTERN[DEFAULT_PATTERN]

    unit_area_cm2 = unit_width_cm * unit_height_cm
    ordered_cm2 = net_area_cm2 * (1.0 + waste_ratio)

    counts: tuple[int, int] | None = None
    if trame_cm is not None and pattern in ALIGNED_PATTERNS:
        counts = _trame_counts(trame_cm, unit_width_cm, unit_height_cm, pattern, holes)
        if counts is None:
            warnings.append(
                f"face {face_label} : unité de {unit_width_cm:g} x {unit_height_cm:g} trop petite "
                f"devant la face (plus de {MAX_TRAME_CELLS} positions) — unités entières et coupes "
                "non dénombrées. La quantité à commander, elle, reste chiffrée."
            )

    return {
        "pattern": pattern,
        "unit_width_cm": unit_width_cm,
        "unit_height_cm": unit_height_cm,
        "unit_area_m2": _to_m2(unit_area_cm2),
        "waste_ratio": round(waste_ratio, RATIO_DIGITS),
        "ordered_area_m2": _to_m2(ordered_cm2),
        "units_total": _ceil_units(ordered_cm2 / unit_area_cm2),
        "full_units": None if counts is None else counts[0],
        "cut_units": None if counts is None else counts[1],
    }


# --- Regroupement des revêtements ----------------------------------------------------------------


def _sum_optional(values: list[int | None]) -> int | None:
    """Somme d'entiers dont un seul inconnu rend le total inconnu.

    Compter les coupes d'un mur et les additionner à celles d'un sol dont on ne sait pas les
    compter donnerait un total plus petit que la réalité, et rien ne le dirait.
    """
    if any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def _group_coverings(faces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Les calepinages des faces, regroupés par référence de revêtement.

    C'est la forme qu'attend une commande de matériaux : « 12,25 m² de carrelage 60 x 60 en pose
    droite, 38 unités ». Le regroupement se fait sur la matière, le motif et les dimensions
    d'unité — deux faces qui ne partagent pas ces quatre valeurs ne partagent pas une palette.

    Les unités sont **sommées face par face** et non recalculées sur l'aire du groupe : les chutes
    d'un mur ne se réemploient pas sur un autre, et arrondir une fois pour tout le groupe
    sous-estimerait la commande.
    """
    groups: dict[tuple[str, str, float, float], dict[str, Any]] = {}
    order: list[tuple[str, str, float, float]] = []
    for face in faces:
        tiling = face["tiling"]
        if tiling is None:
            continue
        material = face["material"]
        key = (
            material or "",
            tiling["pattern"],
            tiling["unit_width_cm"],
            tiling["unit_height_cm"],
        )
        if key not in groups:
            order.append(key)
            groups[key] = {
                "material": material,
                "pattern": tiling["pattern"],
                "unit_width_cm": tiling["unit_width_cm"],
                "unit_height_cm": tiling["unit_height_cm"],
                "waste_ratio": tiling["waste_ratio"],
                "net_area_m2": 0.0,
                "ordered_area_m2": 0.0,
                "units_total": 0,
                "_full": [],
                "_cut": [],
            }
        group = groups[key]
        group["net_area_m2"] += face["net_area_m2"] or 0.0
        group["ordered_area_m2"] += tiling["ordered_area_m2"]
        group["units_total"] += tiling["units_total"]
        group["_full"].append(tiling["full_units"])
        group["_cut"].append(tiling["cut_units"])

    grouped = []
    for key in sorted(order):
        group = groups[key]
        grouped.append(
            {
                "material": group["material"],
                "pattern": group["pattern"],
                "unit_width_cm": group["unit_width_cm"],
                "unit_height_cm": group["unit_height_cm"],
                "waste_ratio": group["waste_ratio"],
                "net_area_m2": round(group["net_area_m2"], AREA_DIGITS) + 0.0,
                "ordered_area_m2": round(group["ordered_area_m2"], AREA_DIGITS) + 0.0,
                "units_total": group["units_total"],
                "full_units": _sum_optional(group["_full"]),
                "cut_units": _sum_optional(group["_cut"]),
            }
        )
    return grouped


# --- Mobilier (spec §10, amendement A7) ----------------------------------------------------------

# Clé de regroupement d'une fourniture : la recette **et** le gabarit. Deux lits de la même recette
# mais de 140 et de 160 ne s'achètent pas ensemble, et les fondre ferait perdre la seule dimension
# qu'un fournisseur demande.
FurnitureKey = tuple[str, float, float, float]


def _furniture_key(node: dict[str, Any]) -> FurnitureKey | None:
    """Recette et gabarit d'un nœud de mobilier, ou `None` s'il est inexploitable.

    `size_cm` est écrit par `geometry/scene.py::_furniture_node` et vaut toujours trois valeurs ;
    on le revérifie parce que le métré est aussi alimenté par des fixtures écrites à la main, où
    une faute de saisie ne doit pas sortir en `IndexError` — donc en 500 sur la route du métré.
    """
    size = node.get("size_cm") or []
    if len(size) != 3:
        return None
    width, height, depth = (float(value) for value in size)
    return (str(node.get("furniture_type_slug") or ""), width, height, depth)


def _group_furniture(entries: Iterable[tuple[FurnitureKey, int, int]]) -> list[dict[str, Any]]:
    """`(clé, unités posées au sol, unités adossées)` regroupées en lignes de fourniture.

    Une seule fonction pour la pièce et pour le projet : les deux regroupent la même chose, à ceci
    près que la première part de nœuds et la seconde de lignes déjà comptées. Les faire diverger
    ferait deux totaux pour un seul décompte.

    **Aucun montant n'apparaît ici** (spec §10, A7) : une recette de `FurnitureType` n'a pas de
    prix, et le barème de A2 ne connaît que des ouvrages au m², au ml et à l'unité de pose. Le
    mobilier est une information de dossier tant qu'un tarif par recette n'existe pas.
    """
    totals: dict[FurnitureKey, list[int]] = {}
    for key, free, on_face in entries:
        counts = totals.setdefault(key, [0, 0])
        counts[0] += free
        counts[1] += on_face

    lines = []
    for key in sorted(totals):
        slug, width, height, depth = key
        free, on_face = totals[key]
        lines.append(
            {
                "furniture_type_slug": slug,
                "width_cm": width,
                "height_cm": height,
                "depth_cm": depth,
                # Emprise au sol d'**une** unité : c'est elle qui dit si le meuble tient dans la
                # pièce, et elle ne se déduit pas des dimensions sans savoir laquelle est la
                # hauteur.
                "footprint_m2": _to_m2(width * depth),
                "count": free + on_face,
                # Le décompte est scindé parce que les deux poses ne se lisent pas au même endroit
                # du dossier : ce qui est adossé figure sur une planche d'élévation, ce qui est
                # posé au sol n'apparaît que sur le plan coté de la pièce.
                "free_count": free,
                "on_face_count": on_face,
            }
        )
    return lines


def _furniture_lines(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Le mobilier d'une pièce, compté à l'unité depuis les nœuds de sa scène.

    Un meuble dont la recette manque au catalogue ne produit aucun nœud (`_furniture_node` rend
    `None`) : il n'a déjà ni forme 3D ni élévation, et il n'est pas davantage compté. Ce n'est pas
    un silence du métré, c'est un catalogue incomplet — et le signaler ici demanderait au métré de
    connaître le plan, dont il est justement indépendant.

    Les menuiseries (`kind == "joinery"`) sont exclues : elles sont déjà comptées comme percements
    par `opening_count`, `door_count` et `window_count`, et les reprendre ici les compterait deux
    fois dans un même document.
    """
    entries: list[tuple[FurnitureKey, int, int]] = []
    for node in nodes:
        if node.get("kind") != FURNITURE:
            continue
        key = _furniture_key(node)
        if key is None:
            continue
        # `face_label` nul est la marque d'un meuble libre, posée par `_furniture_node`.
        free = node.get("face_label") is None
        entries.append((key, 1 if free else 0, 0 if free else 1))
    return _group_furniture(entries)


def _merged_furniture(rooms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Le mobilier de tout le projet, à partir des lignes déjà établies pièce par pièce."""
    return _group_furniture(
        (
            (
                line["furniture_type_slug"],
                line["width_cm"],
                line["height_cm"],
                line["depth_cm"],
            ),
            line["free_count"],
            line["on_face_count"],
        )
        for room in rooms
        for line in room.get("furniture") or []
    )


# --- Faces ---------------------------------------------------------------------------------------


def _wall_face(node: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    length_cm = float(node["length_cm"])
    height_cm = float(node["height_cm"])
    gross_cm2 = length_cm * height_cm
    holes = _hole_rectangles(node)
    # L'aire est reprise du percement tel que le scene graph l'émet, et non de la largeur saisie
    # sur l'élément : une ouverture qui déborderait du mur y a déjà été rognée, et déduire sa
    # largeur d'origine retirerait de la surface qui n'a jamais été percée.
    openings_cm2 = sum(
        (high_u - low_u) * (high_v - low_v) for low_u, low_v, high_u, high_v in holes
    )
    net_cm2 = max(gross_cm2 - openings_cm2, 0.0)
    covering = node.get("covering") or {}
    material = covering.get("material")
    label = str(node["face_label"])
    doors = [hole for hole in holes if _touches_the_floor(hole)]

    return {
        "face_id": node.get("face_id"),
        "face_label": label,
        "kind": WALL,
        "length_m": _to_ml(length_cm),
        "height_m": _to_ml(height_cm),
        "gross_area_m2": _to_m2(gross_cm2),
        "openings_area_m2": _to_m2(openings_cm2),
        "net_area_m2": _to_m2(net_cm2),
        "opening_count": len(holes),
        "door_count": len(doors),
        "window_count": len(holes) - len(doors),
        # Largeur cumulée des percements qui descendent au sol : c'est ce que la plinthe perd.
        "skirting_deduction_ml": _to_ml(sum(hole[2] - hole[0] for hole in doors)),
        "material": material,
        "tiling": _tiling(
            net_cm2,
            covering,
            trame_cm=(length_cm, height_cm),
            holes=holes,
            face_label=label,
            warnings=warnings,
        ),
    }


def _horizontal_face(
    node: dict[str, Any],
    net_area_cm2: float | None,
    trame_cm: tuple[float, float] | None,
    warnings: list[str],
) -> dict[str, Any]:
    """Sol ou plafond.

    L'aire vient de `net_floor_area_cm2` et non du contour du nœud : ce contour est celui de la
    ligne médiane des murs, et c'est précisément l'aire qu'on s'interdit de facturer. Quand la
    surface nette manque, la face sort sans aire plutôt qu'avec la mauvaise.
    """
    covering = node.get("covering") or {}
    label = str(node["face_label"])
    return {
        "face_id": node.get("face_id"),
        "face_label": label,
        "kind": node["kind"],
        "length_m": None,
        "height_m": None,
        "gross_area_m2": None if net_area_cm2 is None else _to_m2(net_area_cm2),
        "openings_area_m2": 0.0,
        "net_area_m2": None if net_area_cm2 is None else _to_m2(net_area_cm2),
        "opening_count": 0,
        "door_count": 0,
        "window_count": 0,
        "skirting_deduction_ml": 0.0,
        "material": covering.get("material"),
        "tiling": None
        if net_area_cm2 is None
        else _tiling(
            net_area_cm2,
            covering,
            trame_cm=trame_cm,
            holes=[],
            face_label=label,
            warnings=warnings,
        ),
    }


# --- Pièce ---------------------------------------------------------------------------------------


def _net_floor_area_cm2(room: dict[str, Any], label: str, warnings: list[str]) -> float | None:
    value = room.get("net_floor_area_cm2")
    if value is None:
        warnings.append(
            f"pièce {label} : `net_floor_area_cm2` absent du scene graph — sol, plafond et volume "
            "non chiffrés. On ne se replie pas sur `floor_area_cm2`, qui mesure la ligne médiane "
            "des murs et surévalue de 6 % (murs de 10 cm) à 20 % (murs de 30 cm)."
        )
        return None
    return float(value)


def _net_contour(
    walls: list[dict[str, Any]],
    thickness_cm: float,
    net_area_cm2: float | None,
    label: str,
    warnings: list[str],
) -> list[list[float]] | None:
    """Contour au nu intérieur, ou `None` s'il n'est pas reconstructible de façon sûre.

    Le résultat est confronté à `net_floor_area_cm2`, qui vient du même calcul dans
    `app/geometry/scene.py` : un écart signifie que la reconstruction du contour est fausse — murs
    dans le désordre, pièce ouverte — et on renonce plutôt que de livrer un linéaire faux.
    """
    if net_area_cm2 is None:
        # Le manque a déjà été signalé par `_net_floor_area_cm2`, inutile de le redire.
        return None
    if net_area_cm2 <= 0.0:
        warnings.append(
            f"pièce {label} : surface nette nulle, la pièce est plus étroite que ses propres murs "
            "— plinthe, corniche et périmètre au nu intérieur non chiffrés."
        )
        return None
    contour = _plan_contour(walls)
    if contour is None:
        warnings.append(
            f"pièce {label} : les murs ne se referment pas en un contour — plinthe, corniche et "
            "périmètre au nu intérieur non chiffrés."
        )
        return None
    inner = offset_polygon(contour, thickness_cm / 2.0)
    if not inner:
        return None
    tolerance = max(1.0, net_area_cm2 * 1e-6)
    if abs(abs(signed_area(inner)) - net_area_cm2) > tolerance:
        warnings.append(
            f"pièce {label} : le contour reconstruit ne retombe pas sur `net_floor_area_cm2` — "
            "plinthe, corniche et périmètre au nu intérieur non chiffrés."
        )
        return None
    return inner


def _room_takeoff(room: dict[str, Any]) -> dict[str, Any]:
    warnings: list[str] = []
    label = f"« {room.get('name')} » (id {room.get('id')})"
    scene_nodes = list(room.get("nodes") or [])
    nodes = [node for node in scene_nodes if node["kind"] in COVERED_KINDS]
    walls = [node for node in nodes if node["kind"] == WALL]
    furniture = _furniture_lines(scene_nodes)

    height_cm = float(room["ceiling_height_cm"])
    thickness_cm = float(room["wall_thickness_cm"])
    net_area_cm2 = _net_floor_area_cm2(room, label, warnings)
    inner = _net_contour(walls, thickness_cm, net_area_cm2, label, warnings)

    perimeter_cm = sum(float(node["length_cm"]) for node in walls)
    net_perimeter_cm = None if inner is None else _perimeter_cm(inner)
    floor_trame = None if inner is None else _rectangle_sides(inner)

    faces = [
        _wall_face(node, warnings)
        if node["kind"] == WALL
        else _horizontal_face(node, net_area_cm2, floor_trame, warnings)
        for node in nodes
    ]

    wall_faces = [face for face in faces if face["kind"] == WALL]
    net_perimeter_ml = None if net_perimeter_cm is None else _to_ml(net_perimeter_cm)
    doors_ml = _rounded_sum(
        (face["skirting_deduction_ml"] for face in wall_faces), digits=LENGTH_DIGITS
    )

    return {
        "room_id": room.get("id"),
        "name": room.get("name"),
        "ceiling_height_m": _to_ml(height_cm),
        "wall_thickness_m": _to_ml(thickness_cm),
        # Ligne médiane : c'est le périmètre du plan, celui qu'on dessine. Il ne sert pas à
        # chiffrer, seulement à situer la pièce.
        "perimeter_ml": _to_ml(perimeter_cm),
        "net_perimeter_ml": net_perimeter_ml,
        # Plinthe : le nu intérieur, moins ce que les percements descendant au sol lui prennent.
        "skirting_ml": None
        if net_perimeter_ml is None
        else round(net_perimeter_ml - doors_ml, LENGTH_DIGITS) + 0.0,
        # Corniche : le nu intérieur en entier. Aucun percement ne monte au plafond — une porte de
        # 204 sous 250 de plafond n'interrompt pas la moulure, contrairement à la plinthe.
        "cornice_ml": net_perimeter_ml,
        "floor_area_m2": None if net_area_cm2 is None else _to_m2(net_area_cm2),
        "ceiling_area_m2": None if net_area_cm2 is None else _to_m2(net_area_cm2),
        "volume_m3": None if net_area_cm2 is None else _to_m3(net_area_cm2 * height_cm),
        "wall_gross_area_m2": _rounded_sum(face["gross_area_m2"] for face in wall_faces),
        "wall_openings_area_m2": _rounded_sum(face["openings_area_m2"] for face in wall_faces),
        "wall_net_area_m2": _rounded_sum(face["net_area_m2"] for face in wall_faces),
        "opening_count": sum(face["opening_count"] for face in wall_faces),
        "door_count": sum(face["door_count"] for face in wall_faces),
        "window_count": sum(face["window_count"] for face in wall_faces),
        "faces": faces,
        "coverings": _group_coverings(faces),
        # Mobilier compté à l'unité (spec §10, amendement A7). La clé est **absente** quand la
        # pièce n'en porte aucun : son absence vaut zéro et jamais « inconnu », la présence de
        # mobilier étant toujours établissable depuis la scène — contrairement à une surface nette
        # manquante. C'est aussi ce qui laisse intact le contrat que décrivent exhaustivement les
        # fixtures de référence 07 à 10, qui font foi (`CLAUDE.md`).
        **({"furniture": furniture} if furniture else {}),
        "warnings": warnings,
    }


# --- Projet --------------------------------------------------------------------------------------


def _project_totals(rooms: list[dict[str, Any]]) -> dict[str, Any]:
    faces = [face for room in rooms for face in room["faces"]]
    furniture = _merged_furniture(rooms)
    return {
        "room_count": len(rooms),
        "floor_area_m2": _rounded_sum(room["floor_area_m2"] for room in rooms),
        "ceiling_area_m2": _rounded_sum(room["ceiling_area_m2"] for room in rooms),
        "wall_gross_area_m2": _rounded_sum(room["wall_gross_area_m2"] for room in rooms),
        "wall_openings_area_m2": _rounded_sum(room["wall_openings_area_m2"] for room in rooms),
        "wall_net_area_m2": _rounded_sum(room["wall_net_area_m2"] for room in rooms),
        "volume_m3": _rounded_sum((room["volume_m3"] for room in rooms), digits=VOLUME_DIGITS),
        "perimeter_ml": _rounded_sum(
            (room["perimeter_ml"] for room in rooms), digits=LENGTH_DIGITS
        ),
        "skirting_ml": _rounded_sum((room["skirting_ml"] for room in rooms), digits=LENGTH_DIGITS),
        "cornice_ml": _rounded_sum((room["cornice_ml"] for room in rooms), digits=LENGTH_DIGITS),
        "opening_count": sum(room["opening_count"] for room in rooms),
        "door_count": sum(room["door_count"] for room in rooms),
        "window_count": sum(room["window_count"] for room in rooms),
        "coverings": _group_coverings(faces),
        # Même règle d'absence que sur la pièce, et pour la même raison.
        **({"furniture": furniture} if furniture else {}),
    }


# --- Point d'entrée ------------------------------------------------------------------------------


def build_takeoff(scene_graph: dict[str, Any]) -> dict[str, Any]:
    """Métré complet d'un scene graph : par face, par pièce, puis par projet.

    Forme du résultat, décrite ici parce que c'est le contrat que consomment l'API et le moteur
    de devis :

    ```
    units      {area: "m2", length: "ml", volume: "m3"}
    project_id
    rooms[]    room_id, name, ceiling_height_m, wall_thickness_m,
               perimeter_ml (ligne médiane), net_perimeter_ml, skirting_ml, cornice_ml,
               floor_area_m2, ceiling_area_m2, volume_m3,
               wall_gross_area_m2, wall_openings_area_m2, wall_net_area_m2,
               opening_count, door_count, window_count,
               faces[]     face_id, face_label, kind (wall|floor|ceiling), length_m, height_m,
                           gross_area_m2, openings_area_m2, net_area_m2,
                           opening_count, door_count, window_count, skirting_deduction_ml,
                           material, tiling
               coverings[] regroupement des `tiling` par référence de revêtement
               furniture[] furniture_type_slug, width_cm, height_cm, depth_cm, footprint_m2,
                           count, free_count, on_face_count — clé absente si la pièce n'en porte
                           aucun
               warnings[]
    totals     mêmes agrégats, plus room_count, sur tout le projet
    warnings[] tous les avertissements des pièces, à plat
    ```

    `furniture` compte le mobilier **à l'unité** et ne porte aucun montant (spec §10, amendement
    A7) : une recette de `FurnitureType` n'a pas de prix. Son absence vaut zéro, jamais
    « inconnu ».

    `tiling` vaut `None` quand le revêtement ne déclare pas de dimensions d'unité ; sinon il porte
    `pattern`, `unit_width_cm`, `unit_height_cm`, `unit_area_m2`, `waste_ratio`,
    `ordered_area_m2`, `units_total`, `full_units` et `cut_units`. Les trois derniers sont trois
    grandeurs distinctes : ce qu'on commande, ce qu'on pose entier, ce qu'on coupe.

    Lève `ValueError` si le scene graph n'est pas en centimètres : toutes les conversions de ce
    module en dépendent, et livrer des m² faux serait pire que refuser de répondre.

    Les valeurs qu'on ne sait pas établir sortent à `None` et jamais à zéro, chacune accompagnée
    d'une entrée dans `warnings`. Les totaux ignorent ces inconnues — ils sont donc partiels quand
    `warnings` n'est pas vide, et c'est là qu'un appelant doit le lire.
    """
    units = scene_graph.get("units", "cm")
    if units != "cm":
        raise ValueError(f"métré : scene graph en « {units} », attendu en centimètres")

    rooms = [_room_takeoff(room) for room in scene_graph.get("rooms") or []]
    return {
        "units": {"area": "m2", "length": "ml", "volume": "m3"},
        "project_id": scene_graph.get("project_id"),
        "rooms": rooms,
        "totals": _project_totals(rooms),
        "warnings": [message for room in rooms for message in room["warnings"]],
    }
