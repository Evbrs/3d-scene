"""Génération et lettrage automatique des faces d'une pièce.

Règle métier héritée du modèle de base (`docs/spec-complete.md` §2 : « la logique de faces
lettrées automatiquement » reste valable) : les murs d'une pièce sont lettrés A, B, C… dans
l'ordre du polygone, et le sol et le plafond sont des faces à part entière (§1 : « plafond en
face à part entière »).

Le lettrage vit ici, côté API, et non dans le modèle : la colonne `label` ne fait que stocker le
résultat (décision du ticket P1).
"""

from string import ascii_uppercase

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from app.models.base import FaceKind
from app.models.plan import Face, Room

FLOOR_LABEL = "SOL"
CEILING_LABEL = "PLAFOND"


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


def wall_segments(polygon: list[list[float]]) -> list[tuple[float, float, float, float]]:
    """Segments (x1, y1, x2, y2) des murs d'un polygone fermé.

    Le polygone est implicitement fermé : le dernier sommet est relié au premier. Un polygone de
    moins de 3 sommets ne délimite aucune surface et ne produit donc aucun mur.
    """
    if len(polygon) < 3:
        return []

    segments: list[tuple[float, float, float, float]] = []
    for index, start in enumerate(polygon):
        end = polygon[(index + 1) % len(polygon)]
        segments.append((start[0], start[1], end[0], end[1]))
    return segments


async def sync_room_faces(session: AsyncSession, room: Room) -> list[Face]:
    """Aligne les faces d'une pièce sur son polygone.

    Conserve les faces existantes (et donc les éléments et revêtements qui y sont rattachés)
    quand le nombre de murs ne change pas : recréer les faces à chaque modification du polygone
    détruirait silencieusement le travail de l'utilisateur. Seuls les murs en trop sont
    supprimés, et les murs manquants ajoutés.
    """
    existing = list(
        (
            await session.execute(
                select(Face).where(col(Face.room_id) == room.id).order_by(col(Face.id))
            )
        )
        .scalars()
        .all()
    )
    existing_walls = [face for face in existing if face.kind is FaceKind.WALL]
    existing_by_label = {face.label: face for face in existing}

    segments = wall_segments(room.polygon)

    for index, (start_x, start_y, end_x, end_y) in enumerate(segments):
        label = wall_label(index)
        face = existing_by_label.get(label)
        if face is None:
            face = Face(room_id=room.id or 0, label=label, kind=FaceKind.WALL)
            session.add(face)
        face.start_x_cm, face.start_y_cm = start_x, start_y
        face.end_x_cm, face.end_y_cm = end_x, end_y

    # Murs devenus surnuméraires après simplification du polygone.
    expected_labels = {wall_label(index) for index in range(len(segments))}
    for face in existing_walls:
        if face.label not in expected_labels:
            await session.delete(face)

    # Sol et plafond : une seule face de chaque, indépendante du nombre de murs.
    for label, kind in ((FLOOR_LABEL, FaceKind.FLOOR), (CEILING_LABEL, FaceKind.CEILING)):
        if label not in existing_by_label and segments:
            session.add(Face(room_id=room.id or 0, label=label, kind=kind))

    await session.flush()
    return list(
        (
            await session.execute(
                select(Face).where(col(Face.room_id) == room.id).order_by(col(Face.id))
            )
        )
        .scalars()
        .all()
    )
