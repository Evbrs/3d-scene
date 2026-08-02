"""Génération et lettrage automatique des faces d'une pièce.

Règle métier héritée du modèle de base (`docs/spec-complete.md` §2 : « la logique de faces
lettrées automatiquement » reste valable) : les murs d'une pièce sont lettrés A, B, C… dans
l'ordre du polygone, et le sol et le plafond sont des faces à part entière (§1 : « plafond en
face à part entière »).

**Les murs sont identifiés par leur géométrie, pas par leur rang.** Un appariement par rang
(« le mur n° 2 reste le mur n° 2 ») paraît naturel mais est faux dès qu'un sommet est inséré
ailleurs qu'en fin de polygone : tous les murs suivants glissent d'un cran et héritent du
revêtement et des meubles de leur voisin. Le lettrage, lui, reste bien positionnel — c'est un
rang de lecture du plan, pas une identité.
"""

from string import ascii_uppercase

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import col, select

from app.models.base import FaceKind
from app.models.plan import Element, Face, Room

FLOOR_LABEL = "SOL"
CEILING_LABEL = "PLAFOND"

# Tolérance de comparaison de coordonnées, en centimètres. Deux sommets plus proches que 0,1 mm
# désignent le même point : le plan est saisi au centimètre.
COORDINATE_TOLERANCE = 0.01

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

    # --- Appariement par géométrie -----------------------------------------------------------
    matched: dict[int, Face] = {}
    unmatched_walls = list(existing_walls)
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
    return await _load_faces(session, room.id or 0)


def element_fits_on_face(element: Element, face: Face, room: Room) -> str | None:
    """Message d'erreur si l'élément déborde de sa face, `None` s'il tient.

    Sans cette vérification, une fenêtre posée à 350 cm sur un mur long de 180 cm est acceptée,
    et c'est le calcul du scene graph (P6) qui produit une géométrie absurde — très loin du
    point d'insertion.
    """
    if face.kind is FaceKind.WALL:
        if None in (face.start_x_cm, face.start_y_cm, face.end_x_cm, face.end_y_cm):
            return None
        length = (
            (float(face.end_x_cm or 0) - float(face.start_x_cm or 0)) ** 2
            + (float(face.end_y_cm or 0) - float(face.start_y_cm or 0)) ** 2
        ) ** 0.5
        height = room.ceiling_height_cm
    else:
        # Sol et plafond : on borne par la boîte englobante du polygone.
        if not room.polygon:
            return None
        xs = [vertex[0] for vertex in room.polygon]
        ys = [vertex[1] for vertex in room.polygon]
        length, height = max(xs) - min(xs), max(ys) - min(ys)

    if element.x_offset_cm < 0 or element.x_offset_cm + element.width_cm > length + 1:
        return (
            f"l'élément déborde de la face {face.label} en largeur "
            f"({element.x_offset_cm} + {element.width_cm} > {round(length, 1)} cm)"
        )
    if element.y_offset_cm < 0 or element.y_offset_cm + element.height_cm > height + 1:
        return (
            f"l'élément déborde de la face {face.label} en hauteur "
            f"({element.y_offset_cm} + {element.height_cm} > {round(height, 1)} cm)"
        )
    return None
