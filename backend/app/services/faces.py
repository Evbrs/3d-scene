"""Génération et lettrage automatique des faces d'une pièce.

Règle métier héritée du modèle de base (`docs/spec-complete.md` §2 : « la logique de faces
lettrées automatiquement » reste valable) : les murs d'une pièce sont lettrés A, B, C… dans
l'ordre du polygone, et le sol et le plafond sont des faces à part entière (§1 : « plafond en
face à part entière »).

**L'identité d'un mur dépend de ce que l'utilisateur vient de faire.** Deux gestes produisent des
polygones différents et exigent des appariements opposés :

- **Déplacer un sommet** ne change pas le nombre de murs, mais change les coordonnées de deux
  d'entre eux. Un appariement par géométrie les croirait disparus et les supprimerait — avec
  leur revêtement et leurs meubles. Ici, c'est le **rang** qui fait l'identité.
- **Insérer ou retirer un sommet** change le nombre de murs et décale tous les rangs suivants. Un
  appariement par rang ferait alors hériter chaque mur du revêtement de son voisin. Ici, c'est la
  **géométrie** qui fait l'identité.

Le nombre de murs avant/après suffit à distinguer les deux cas, et c'est une information qu'on a
déjà. Le lettrage, lui, reste toujours positionnel : c'est un rang de lecture du plan, pas une
identité.
"""

import math
from string import ascii_uppercase

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import col, select

from app.models.base import ElementKind, FaceKind
from app.models.plan import Element, Face, Room

FLOOR_LABEL = "SOL"
CEILING_LABEL = "PLAFOND"

# Tolérance de comparaison de coordonnées, en centimètres. Deux sommets plus proches que 0,1 mm
# désignent le même point : le plan est saisi au centimètre.
COORDINATE_TOLERANCE = 0.01

# Tolérance des comparaisons d'encombrement. Elle n'absorbe que le bruit du flottant — une
# longueur de mur est une racine carrée, `400.0` peut sortir à `399.99999999999994`. L'ancienne
# valeur, `+ 1` cm, laissait une ouverture déborder de 9 mm : un trou qui sort du contour du mur
# n'est plus triangulable, et l'aire du mur passait de 100 000 à 163 258 cm² dans earcut.
FIT_TOLERANCE = 1e-6

# Taille minimale conservée quand on rétrécit un élément pour le faire tenir : les colonnes
# `*_cm` sont contraintes `> 0` en base, ramener à 0 lèverait une violation de contrainte.
MIN_ELEMENT_SIZE_CM = 1.0

# Une ouverture est un percement du mur (spec §3.1) : elle n'a de sens sur aucune autre face.
OPENING_KINDS = frozenset(
    {ElementKind.DOOR_HINGED, ElementKind.DOOR_SLIDING, ElementKind.WINDOW}
)

Segment = tuple[float, float, float, float]


class FaceRemovalWouldLoseElements(Exception):
    """Levée quand simplifier un polygone détruirait des éléments déjà posés.

    Supprimer silencieusement des meubles et des ouvertures en réponse à un `200 OK` est une
    perte de données invisible. L'appelant décide : soit il renonce, soit il confirme.
    """

    def __init__(self, labels: list[str], element_count: int) -> None:
        self.labels = labels
        self.element_count = element_count
        super().__init__(
            f"{element_count} élément(s) posé(s) sur les faces {', '.join(labels)} seraient "
            "supprimés avec ces murs"
        )


def wall_label(index: int) -> str:
    """Étiquette du mur n° `index` (0 → « A », 25 → « Z », 26 → « AA »…).

    Le débordement au-delà de 26 murs est traité explicitement : une pièce en L ou en U peut
    dépasser, et un lettrage qui recommencerait à « A » violerait la contrainte d'unicité
    `(room_id, label)`.
    """
    if index < 0:
        raise ValueError("l'index d'un mur ne peut pas être négatif")

    label = ""
    index += 1
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        label = ascii_uppercase[remainder] + label
    return label


def wall_segments(polygon: list[list[float]]) -> list[Segment]:
    """Segments (x1, y1, x2, y2) des murs d'un polygone fermé.

    Le polygone est implicitement fermé : le dernier sommet est relié au premier. Un polygone de
    moins de 3 sommets ne délimite aucune surface et ne produit donc aucun mur.
    """
    if len(polygon) < 3:
        return []

    segments: list[Segment] = []
    for index, start in enumerate(polygon):
        end = polygon[(index + 1) % len(polygon)]
        segments.append((start[0], start[1], end[0], end[1]))
    return segments


def polygon_area(polygon: list[list[float]]) -> float:
    """Aire absolue du contour (formule du lacet), en cm².

    Dupliquée ici plutôt qu'importée de `app.geometry` : la validation des schémas doit rester
    utilisable sans la couche de calcul 3D, et cette formule tient en trois lignes.
    """
    if len(polygon) < 3:
        return 0.0
    total = 0.0
    for index, (x_start, y_start) in enumerate(polygon):
        x_end, y_end = polygon[(index + 1) % len(polygon)]
        total += x_start * y_end - x_end * y_start
    return abs(total) / 2.0


def shortest_side_length(polygon: list[list[float]]) -> float:
    """Longueur du plus court côté du contour, en cm."""
    return min(
        float(((x_end - x_start) ** 2 + (y_end - y_start) ** 2) ** 0.5)
        for x_start, y_start, x_end, y_end in wall_segments(polygon)
    )


def _orientation(
    origin: tuple[float, float], first: tuple[float, float], second: tuple[float, float]
) -> float:
    return (first[0] - origin[0]) * (second[1] - origin[1]) - (first[1] - origin[1]) * (
        second[0] - origin[0]
    )


Point = tuple[float, float]


def _segments_cross(a_start: Point, a_end: Point, b_start: Point, b_end: Point) -> bool:
    """Vrai si les deux segments se croisent **franchement**.

    Franchement : chaque segment doit avoir ses deux extrémités strictement de part et d'autre de
    la droite portant l'autre. Deux segments colinéaires qui se recouvrent, ou une extrémité
    posée sur l'autre segment, ne comptent donc pas — c'est exactement ce que veulent les deux
    appelants, `polygon_crosses_itself` (un contour en escalier reste valide) et
    `element_fits_in_room` (un meuble posé pile contre un mur reste valide).
    """
    first_side = _orientation(a_start, a_end, b_start)
    second_side = _orientation(a_start, a_end, b_end)
    third_side = _orientation(b_start, b_end, a_start)
    fourth_side = _orientation(b_start, b_end, a_end)
    return first_side * second_side < 0 and third_side * fourth_side < 0


def polygon_crosses_itself(polygon: list[list[float]]) -> bool:
    """Vrai si deux côtés non adjacents se **croisent** franchement.

    Un contour qui se croise n'a plus d'aire signée fiable : sur un nœud papillon parfaitement
    symétrique elle vaut zéro, `ensure_counter_clockwise` bascule alors d'un appel à l'autre au
    gré du bruit du flottant, et toutes les normales sortantes s'inversent d'un coup — la pièce
    est vue de l'extérieur.

    Seuls les croisements **francs** sont détectés : deux côtés colinéaires qui se recouvrent, ou
    un sommet posé sur un autre côté, restent acceptés. Ce n'est pas un oubli — un contour en
    escalier refermé le long d'un axe qu'il a déjà emprunté est produit par l'éditeur et garde une
    aire parfaitement définie, donc ne présente aucun des symptômes ci-dessus.
    """
    segments = wall_segments(polygon)
    count = len(segments)
    for first in range(count):
        a_start = (segments[first][0], segments[first][1])
        a_end = (segments[first][2], segments[first][3])
        for second in range(first + 1, count):
            # Côtés consécutifs (y compris la paire dernier/premier) : ils partagent un sommet,
            # ce qui n'est pas un croisement.
            if second == first + 1 or (first == 0 and second == count - 1):
                continue
            b_start = (segments[second][0], segments[second][1])
            b_end = (segments[second][2], segments[second][3])
            if _segments_cross(a_start, a_end, b_start, b_end):
                return True
    return False


def _label_rank(label: str) -> int:
    """Rang d'une étiquette de mur (« A » → 0, « Z » → 25, « AA » → 26).

    Trier sur la chaîne brute placerait « AA » entre « A » et « B ».
    """
    rank = 0
    for character in label:
        if not character.isalpha():
            return 10**6  # étiquette temporaire : reléguée en fin de tri
        rank = rank * 26 + (ord(character.upper()) - 64)
    return rank - 1


def _same_segment(face: Face, segment: Segment) -> bool:
    coordinates = (face.start_x_cm, face.start_y_cm, face.end_x_cm, face.end_y_cm)
    if any(value is None for value in coordinates):
        return False
    return all(
        abs(float(actual or 0.0) - expected) <= COORDINATE_TOLERANCE
        for actual, expected in zip(coordinates, segment, strict=True)
    )


async def _load_faces(session: AsyncSession, room_id: int) -> list[Face]:
    return list(
        (
            await session.execute(
                select(Face)
                .where(col(Face.room_id) == room_id)
                .options(selectinload(Face.elements))  # type: ignore[arg-type]
                .order_by(col(Face.id))
            )
        )
        .scalars()
        .all()
    )


async def _load_free_elements(session: AsyncSession, room_id: int) -> list[Element]:
    """Mobilier posé au sol de la pièce, adossé à aucune face (spec §10, amendement A4)."""
    return list(
        (
            await session.execute(
                select(Element)
                .where(col(Element.room_id) == room_id)
                .order_by(col(Element.id))
            )
        )
        .scalars()
        .all()
    )


async def sync_room_faces(
    session: AsyncSession, room: Room, *, force: bool = False
) -> list[Face]:
    """Aligne les faces d'une pièce sur son polygone.

    - les murs conservés sont reconnus à leur **géométrie**, donc gardent leur revêtement et
      leurs éléments même si un sommet est inséré en amont dans le polygone ;
    - les murs disparus sont supprimés — mais si des éléments y sont posés, l'opération est
      refusée tant que `force` n'est pas demandé (`FaceRemovalWouldLoseElements`) ;
    - le sol et le plafond suivent l'existence du polygone : créés avec lui, supprimés avec lui.
    """
    existing = await _load_faces(session, room.id or 0)
    existing_walls = [face for face in existing if face.kind is FaceKind.WALL]
    segments = wall_segments(room.polygon)

    # --- Appariement ---------------------------------------------------------------------------
    matched: dict[int, Face] = {}
    unmatched_walls = list(existing_walls)

    if existing_walls and len(existing_walls) == len(segments):
        # Même nombre de murs : l'utilisateur a déformé la pièce sans en ajouter ni en retirer.
        # Les murs gardent leur rang, donc leur identité, et se contentent de nouvelles
        # coordonnées.
        ordered = sorted(existing_walls, key=lambda face: _label_rank(face.label))
        matched = dict(enumerate(ordered))
        unmatched_walls = []
    else:
        # Le nombre de murs a changé : seuls ceux dont la géométrie est intacte sont conservés.
        for index, segment in enumerate(segments):
            for face in unmatched_walls:
                if _same_segment(face, segment):
                    matched[index] = face
                    unmatched_walls.remove(face)
                    break

    # --- Refus de la perte silencieuse -------------------------------------------------------
    doomed = [face for face in unmatched_walls if face.elements]
    if doomed and not force:
        raise FaceRemovalWouldLoseElements(
            labels=sorted(face.label for face in doomed),
            element_count=sum(len(face.elements) for face in doomed),
        )

    for face in unmatched_walls:
        await session.delete(face)
    await session.flush()

    # --- Création des murs nouveaux ------------------------------------------------------------
    ordered_walls: list[Face] = []
    for index, segment in enumerate(segments):
        reused = matched.get(index)
        if reused is None:
            # Étiquette temporaire unique : le lettrage définitif est posé plus bas, une fois
            # tous les murs connus, pour ne pas violer la contrainte `(room_id, label)`.
            face = Face(room_id=room.id or 0, label=f"~{index}", kind=FaceKind.WALL)
            session.add(face)
        else:
            face = reused
        face.start_x_cm, face.start_y_cm, face.end_x_cm, face.end_y_cm = segment
        ordered_walls.append(face)
    await session.flush()

    # --- Lettrage positionnel, en deux temps ---------------------------------------------------
    # Renommer directement provoquerait des collisions transitoires (B devient A alors que
    # l'ancien A existe encore). On passe donc par des étiquettes temporaires.
    for index, face in enumerate(ordered_walls):
        face.label = f"~{index}"
    await session.flush()
    for index, face in enumerate(ordered_walls):
        face.label = wall_label(index)
    await session.flush()

    # --- Sol et plafond ------------------------------------------------------------------------
    horizontal = {
        face.label: face for face in existing if face.kind in (FaceKind.FLOOR, FaceKind.CEILING)
    }
    if segments:
        for label, kind in ((FLOOR_LABEL, FaceKind.FLOOR), (CEILING_LABEL, FaceKind.CEILING)):
            if label not in horizontal:
                session.add(Face(room_id=room.id or 0, label=label, kind=kind))
    else:
        # Un polygone vide ne délimite plus rien : garder un sol et un plafond orphelins ferait
        # diverger deux pièces pourtant dans le même état, selon leur seul historique.
        doomed_horizontal = [face for face in horizontal.values() if face.elements]
        if doomed_horizontal and not force:
            raise FaceRemovalWouldLoseElements(
                labels=sorted(face.label for face in doomed_horizontal),
                element_count=sum(len(face.elements) for face in doomed_horizontal),
            )
        for face in horizontal.values():
            await session.delete(face)

    await session.flush()
    faces = await _load_faces(session, room.id or 0)
    _refit_elements(faces, room)
    # Le mobilier libre survit à la disparition d'un mur — c'est tout l'intérêt de l'ancrage à la
    # pièce — mais il peut se retrouver dehors quand le contour rétrécit.
    _refit_free_elements(await _load_free_elements(session, room.id or 0), room)
    await session.flush()
    return faces


def wall_face_length(face: Face) -> float | None:
    """Longueur d'un mur en cm, `None` si son segment n'est pas renseigné."""
    coordinates = (face.start_x_cm, face.start_y_cm, face.end_x_cm, face.end_y_cm)
    if any(value is None for value in coordinates):
        return None
    start_x, start_y, end_x, end_y = (float(value or 0.0) for value in coordinates)
    return float(((end_x - start_x) ** 2 + (end_y - start_y) ** 2) ** 0.5)


def _face_spans(face: Face, room: Room) -> tuple[float, float] | None:
    """Étendue utile d'une face, en cm : `(le long de x_offset, le long de y_offset)`.

    `None` quand la face n'a pas encore de géométrie (mur sans segment, pièce sans polygone) :
    il n'y a alors rien à borner.
    """
    if face.kind is FaceKind.WALL:
        length = wall_face_length(face)
        return None if length is None else (length, float(room.ceiling_height_cm))
    if not room.polygon:
        return None
    xs = [vertex[0] for vertex in room.polygon]
    ys = [vertex[1] for vertex in room.polygon]
    return max(xs) - min(xs), max(ys) - min(ys)


def _element_footprint(element: Element, face: Face) -> tuple[float, float]:
    """Encombrement de l'élément **dans le plan de la face**, en cm.

    C'est tout l'enjeu : sur un mur, ce qui s'inscrit dans (longueur, hauteur sous plafond) est
    le couple (largeur, hauteur) ; sur un sol ou un plafond, la face est horizontale et c'est
    (largeur, profondeur) qui s'y pose — la hauteur, elle, s'élève dans la pièce. La version
    précédente comparait `y_offset + height` à l'étendue au sol dans les deux cas : elle refusait
    une armoire de 120x200x60 dans une pièce de 3 m de profondeur, et laissait un lit de
    140x45x200 traverser le mur d'en face.
    """
    if face.kind is FaceKind.WALL:
        return float(element.width_cm), float(element.height_cm)
    return float(element.width_cm), float(element.depth_cm)


def _assign(element: Element, field: str, value: float) -> None:
    """Écrit `field` seulement s'il change.

    Réécrire une valeur identique suffit à faire émettre un `UPDATE` par élément à chaque
    resynchronisation des faces — c'est-à-dire à chaque coup de souris sur le plan.
    """
    if getattr(element, field) != value:
        setattr(element, field, value)


def _fit_on_axis(size: float, offset: float, span: float) -> tuple[float, float]:
    """Ramène `(taille, décalage)` dans un segment de longueur `span`.

    La taille est réduite avant le décalage : un élément plus large que son mur n'y tiendra pas
    en le déplaçant. Le plancher `MIN_ELEMENT_SIZE_CM` est imposé par la contrainte `> 0` posée
    en base sur les dimensions.
    """
    size = min(size, max(span, MIN_ELEMENT_SIZE_CM))
    offset = min(max(offset, 0.0), max(span - size, 0.0))
    return size, offset


def _refit_elements(faces: list[Face], room: Room) -> None:
    """Ramène dans leur face les éléments qu'une déformation a fait déborder.

    L'alternative — les laisser tels quels — produit une ouverture percée en dehors du mur, donc
    une géométrie 3D absurde. Les supprimer serait pire : l'utilisateur perdrait son travail pour
    avoir déplacé un sommet. On les repousse donc à l'intérieur, ce qui reste visible et
    corrigeable d'un coup de souris.

    Les trois axes sont traités, sur les murs **comme** sur le sol et le plafond : abaisser la
    hauteur sous plafond ou rétrécir la pièce déborde tout autant qu'un mur raccourci, et ne
    déclenchait jusqu'ici aucune correction.
    """
    headroom = max(float(room.ceiling_height_cm), MIN_ELEMENT_SIZE_CM)
    for face in faces:
        spans = _face_spans(face, room)
        if spans is None:
            continue
        span_u, span_v = spans
        for element in face.elements:
            size_u, size_v = _element_footprint(element, face)
            size_u, offset_u = _fit_on_axis(size_u, element.x_offset_cm, span_u)
            size_v, offset_v = _fit_on_axis(size_v, element.y_offset_cm, span_v)
            _assign(element, "x_offset_cm", offset_u)
            _assign(element, "y_offset_cm", offset_v)
            _assign(element, "width_cm", size_u)
            if face.kind is FaceKind.WALL:
                _assign(element, "height_cm", size_v)
            else:
                _assign(element, "depth_cm", size_v)
                # La hauteur d'un meuble posé au sol se mesure contre la hauteur sous plafond, et
                # non contre l'étendue de la face : elle s'élève dans la pièce.
                _assign(element, "height_cm", min(element.height_cm, headroom))


def _refit_free_elements(elements: list[Element], room: Room) -> None:
    """Ramène le mobilier libre dans la **boîte englobante** du contour après déformation.

    Même arbitrage que `_refit_elements` : ni suppression (l'utilisateur perdrait son travail
    pour avoir déplacé un sommet), ni abandon sur place (le meuble finirait dehors, absurde en
    3D). Le meuble n'est pas rétréci non plus — un lit ne rapetisse pas parce qu'on a bougé un
    mur —, il est seulement translaté ; s'il est plus grand que la pièce, il est recentré.

    La boîte englobante et non le polygone : ramener une emprise tournée dans un contour concave
    n'a pas de solution unique, et en inventer une déplacerait le meuble à un endroit que
    l'utilisateur n'a pas choisi. Sur une pièce en L, le meuble peut donc atterrir dans le
    renfoncement. C'est visible, corrigeable d'un coup de souris, et la garantie réelle reste le
    contrôle d'`element_fits_in_room`, exercé à chaque écriture explicite.
    """
    if not room.polygon:
        return
    xs = [vertex[0] for vertex in room.polygon]
    ys = [vertex[1] for vertex in room.polygon]
    bounds = ((min(xs), max(xs)), (min(ys), max(ys)))
    headroom = max(float(room.ceiling_height_cm), MIN_ELEMENT_SIZE_CM)

    for element in elements:
        _assign(element, "height_cm", min(element.height_cm, headroom))
        corners = free_element_footprint(element)
        for axis, field in enumerate(("pos_x_cm", "pos_y_cm")):
            low, high = bounds[axis]
            span = max(corner[axis] for corner in corners) - min(
                corner[axis] for corner in corners
            )
            # L'emprise tournée reste symétrique autour du centre : borner le centre suffit.
            half = span / 2.0
            center = float(getattr(element, field) or 0.0)
            if low + half > high - half:
                _assign(element, field, (low + high) / 2.0)
            else:
                _assign(element, field, min(max(center, low + half), high - half))


def free_element_footprint(element: Element) -> list[Point]:
    """Les quatre coins de l'emprise au sol d'un meuble libre, dans le repère du plan.

    La rotation est celle appliquée autour de la verticale par le scene graph
    (`rotation_y = radians(rotation_deg)`). Une rotation `R_y(a)` envoie l'axe local `+X` — la
    largeur — sur `(cos a, 0, -sin a)` et l'axe local `+Z` — la profondeur — sur
    `(sin a, 0, cos a)` ; relus dans le plan, où `x = X` et `y = Z`, cela donne les deux axes
    ci-dessous. Sans cette conversion, un meuble tourné à 90° serait contrôlé avec sa largeur et
    sa profondeur échangées, et une table de 2 m sur 80 traverserait le mur d'en face.
    """
    angle = math.radians(float(element.rotation_deg))
    cosine, sine = math.cos(angle), math.sin(angle)
    width_axis = (cosine, -sine)
    depth_axis = (sine, cosine)
    half_width = float(element.width_cm) / 2.0
    half_depth = float(element.depth_cm) / 2.0
    center_x = float(element.pos_x_cm or 0.0)
    center_y = float(element.pos_y_cm or 0.0)
    return [
        (
            center_x + along * half_width * width_axis[0] + across * half_depth * depth_axis[0],
            center_y + along * half_width * width_axis[1] + across * half_depth * depth_axis[1],
        )
        for along, across in ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0))
    ]


def point_in_polygon(polygon: list[list[float]], x: float, y: float) -> bool:
    """Appartenance d'un point au contour, par parité des croisements d'un rayon horizontal.

    Une boîte englobante suffirait pour un rectangle et mentirait sur une pièce en L, où le
    renfoncement est *hors* de la pièce tout en étant dans la boîte. Le verdict sur le contour
    lui-même n'est pas fiable (le rayon peut passer exactement par un sommet) : les appelants
    traitent le bord à part, avec une tolérance.
    """
    inside = False
    for index, (start_x, start_y) in enumerate(polygon):
        end_x, end_y = polygon[(index + 1) % len(polygon)]
        if (start_y > y) != (end_y > y):
            crossing_x = start_x + (y - start_y) * (end_x - start_x) / (end_y - start_y)
            if x < crossing_x:
                inside = not inside
    return inside


def distance_to_polygon(polygon: list[list[float]], x: float, y: float) -> float:
    """Distance du point au côté le plus proche du contour, en cm.

    C'est le « de combien » du message de refus : dire qu'un meuble déborde sans dire de combien
    oblige l'utilisateur à chercher lui-même de quel côté et à quelle distance.
    """
    nearest = float("inf")
    for index, (start_x, start_y) in enumerate(polygon):
        end_x, end_y = polygon[(index + 1) % len(polygon)]
        edge_x, edge_y = end_x - start_x, end_y - start_y
        squared = edge_x * edge_x + edge_y * edge_y
        if squared <= 0.0:
            travel = 0.0
        else:
            travel = ((x - start_x) * edge_x + (y - start_y) * edge_y) / squared
            travel = min(max(travel, 0.0), 1.0)
        gap_x = x - (start_x + travel * edge_x)
        gap_y = y - (start_y + travel * edge_y)
        nearest = min(nearest, math.hypot(gap_x, gap_y))
    return nearest


def _footprint_crossing(polygon: list[list[float]], corners: list[Point]) -> Point | None:
    """Premier point où un côté de l'emprise franchit le contour, `None` s'il n'y en a pas.

    Les quatre coins peuvent être dans la pièce alors que l'emprise en sort quand même : sur un
    contour concave, un meuble posé en travers du renfoncement a ses coins de part et d'autre et
    son milieu dehors. Coins dedans **et** aucun franchissement : le rectangle est entièrement
    dans le contour, et c'est ce couple qui fait la garantie.
    """
    for index, start in enumerate(corners):
        end = corners[(index + 1) % len(corners)]
        for edge_index, (wall_start_x, wall_start_y) in enumerate(polygon):
            wall_end_x, wall_end_y = polygon[(edge_index + 1) % len(polygon)]
            wall_start = (wall_start_x, wall_start_y)
            wall_end = (wall_end_x, wall_end_y)
            if _segments_cross(start, end, wall_start, wall_end):
                return _intersection(start, end, wall_start, wall_end)
    return None


def _intersection(a_start: Point, a_end: Point, b_start: Point, b_end: Point) -> Point:
    """Point d'intersection de deux segments dont on sait déjà qu'ils se croisent franchement."""
    a_x, a_y = a_end[0] - a_start[0], a_end[1] - a_start[1]
    b_x, b_y = b_end[0] - b_start[0], b_end[1] - b_start[1]
    determinant = a_x * b_y - a_y * b_x
    travel = (
        (b_start[0] - a_start[0]) * b_y - (b_start[1] - a_start[1]) * b_x
    ) / determinant
    return (a_start[0] + travel * a_x, a_start[1] + travel * a_y)


def element_fits_in_room(element: Element, room: Room) -> str | None:
    """Message d'erreur si un meuble libre ne tient pas dans la pièce, `None` s'il tient.

    Le contrôle porte sur l'**emprise** et non sur le centre : un centre dans la pièce ne dit
    rien d'un lit de 2 m dont la moitié traverse le mur. Les quatre coins tournés sont testés un
    par un, puis les quatre côtés de l'emprise sont confrontés au contour.

    Un coin posé pile sur le contour est accepté (à `COORDINATE_TOLERANCE` près) : c'est le geste
    le plus courant du métier — pousser un meuble contre le mur — et le polygone saisi est la
    ligne médiane des murs, donc la limite que l'utilisateur voit dans l'éditeur.
    """
    if element.kind in OPENING_KINDS:
        return (
            f"une ouverture ({element.kind.value}) ne peut pas être posée au sol : "
            "un percement n'a de sens que dans un mur"
        )
    if element.height_cm > room.ceiling_height_cm + FIT_TOLERANCE:
        return (
            f"le meuble est plus haut que la pièce "
            f"({element.height_cm} > {round(room.ceiling_height_cm, 1)} cm sous plafond)"
        )
    if not room.polygon:
        return None

    corners = free_element_footprint(element)
    worst_corner: Point | None = None
    worst_gap = 0.0
    for corner in corners:
        if point_in_polygon(room.polygon, corner[0], corner[1]):
            continue
        gap = distance_to_polygon(room.polygon, corner[0], corner[1])
        if gap <= COORDINATE_TOLERANCE:
            continue
        if worst_corner is None or gap > worst_gap:
            worst_corner, worst_gap = corner, gap
    if worst_corner is not None:
        return (
            f"le meuble sort du contour de la pièce : son coin "
            f"({round(worst_corner[0], 1)}, {round(worst_corner[1], 1)}) est à "
            f"{round(worst_gap, 1)} cm à l'extérieur"
        )

    crossing = _footprint_crossing(room.polygon, corners)
    if crossing is not None:
        return (
            f"le meuble traverse le contour de la pièce : son emprise le franchit en "
            f"({round(crossing[0], 1)}, {round(crossing[1], 1)})"
        )
    return None


def element_fits_on_face(element: Element, face: Face, room: Room) -> str | None:
    """Message d'erreur si l'élément déborde de sa face, `None` s'il tient.

    Sans cette vérification, une fenêtre posée à 350 cm sur un mur long de 180 cm est acceptée,
    et c'est le calcul du scene graph (P6) qui produit une géométrie absurde — très loin du
    point d'insertion.
    """
    if face.kind is not FaceKind.WALL and element.kind in OPENING_KINDS:
        return (
            f"une ouverture ne peut pas être posée sur la face {face.label} : "
            "un percement n'a de sens que dans un mur"
        )

    spans = _face_spans(face, room)
    if spans is None:
        return None
    span_u, span_v = spans
    size_u, size_v = _element_footprint(element, face)
    axis_v = "hauteur" if face.kind is FaceKind.WALL else "profondeur"

    if element.x_offset_cm < 0 or element.x_offset_cm + size_u > span_u + FIT_TOLERANCE:
        return (
            f"l'élément déborde de la face {face.label} en largeur "
            f"({element.x_offset_cm} + {size_u} > {round(span_u, 1)} cm)"
        )
    if element.y_offset_cm < 0 or element.y_offset_cm + size_v > span_v + FIT_TOLERANCE:
        return (
            f"l'élément déborde de la face {face.label} en {axis_v} "
            f"({element.y_offset_cm} + {size_v} > {round(span_v, 1)} cm)"
        )
    too_tall = element.height_cm > room.ceiling_height_cm + FIT_TOLERANCE
    if face.kind is not FaceKind.WALL and too_tall:
        return (
            f"l'élément est plus haut que la pièce "
            f"({element.height_cm} > {round(room.ceiling_height_cm, 1)} cm sous plafond)"
        )
    return None


def _overlap(start_a: float, size_a: float, start_b: float, size_b: float) -> float:
    return min(start_a + size_a, start_b + size_b) - max(start_a, start_b)


def openings_overlap(element: Element, face: Face) -> str | None:
    """Message d'erreur si `element` recouvre une autre ouverture de la même face.

    Deux trous sécants ne sont pas triangulables : `earcut`, l'algorithme derrière
    `THREE.Shape`, n'accepte que des trous disjoints. Sur deux fenêtres qui se croisent il retire
    leur différence symétrique — 5 860 cm² au lieu des 27 460 de leur union — et le mur apparaît
    percé de biais. Deux ouvertures posées bord à bord restent acceptées : elles ne se croisent
    pas, elles se touchent.

    L'élément candidat est écarté de la comparaison par son identité **et** par son `id` : à la
    création il n'a pas encore d'`id`, et à la modification il est déjà dans `face.elements` avec
    ses nouvelles valeurs — il se recouvrirait lui-même.
    """
    if element.kind not in OPENING_KINDS:
        return None
    for other in face.elements:
        if other is element or other.kind not in OPENING_KINDS:
            continue
        if other.id is not None and other.id == element.id:
            continue
        along = _overlap(element.x_offset_cm, element.width_cm, other.x_offset_cm, other.width_cm)
        across = _overlap(
            element.y_offset_cm, element.height_cm, other.y_offset_cm, other.height_cm
        )
        if along > FIT_TOLERANCE and across > FIT_TOLERANCE:
            return (
                f"cette ouverture en recouvre une autre sur la face {face.label} "
                f"(recouvrement de {round(along, 1)} x {round(across, 1)} cm)"
            )
    return None
