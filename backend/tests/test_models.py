"""Critères d'acceptation du ticket P1 (modèle de données).

Référence : `docs/plan-generation-ia.md` §8 (P1), `docs/spec-complete.md` §5 et §8.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.exc import StaleDataError
from sqlmodel import select

from app.models import (
    Element,
    ElementKind,
    Face,
    FaceKind,
    FurnitureType,
    Project,
    Room,
    SharedView,
    User,
)
from app.models.base import utcnow


async def _make_plan(session: AsyncSession, owner: User) -> tuple[Project, Room, Face, Element]:
    """Crée l'arbre Project → Room → Face → Element et le persiste."""
    project = Project(
        name="Rénovation appartement",
        description="T3 à rénover",
        owner_id=owner.id or 0,
    )
    session.add(project)
    await session.flush()

    room = Room(
        project_id=project.id or 0,
        name="Salle de bain",
        wall_thickness_cm=12.5,
        polygon=[[0, 0], [300, 0], [300, 250], [0, 250]],
    )
    session.add(room)
    await session.flush()

    face = Face(
        room_id=room.id or 0,
        label="A",
        kind=FaceKind.WALL,
        start_x_cm=0,
        start_y_cm=0,
        end_x_cm=300,
        end_y_cm=0,
        covering={"color": "#ffffff", "material": "faience", "pattern": "straight"},
    )
    session.add(face)
    await session.flush()

    element = Element(
        face_id=face.id or 0,
        kind=ElementKind.WINDOW,
        x_offset_cm=80,
        y_offset_cm=100,
        width_cm=90,
        height_cm=110,
        depth_cm=12.5,
    )
    session.add(element)
    await session.commit()
    return project, room, face, element


# --- A2 : création et relecture de l'arbre --------------------------------------------------


async def test_creates_and_reads_back_project_room_face_element(
    session: AsyncSession, owner: User
) -> None:
    project, room, face, element = await _make_plan(session, owner)

    session.expunge_all()
    # Eager loading explicite : en asynchrone, un chargement paresseux lèverait
    # `MissingGreenlet`. C'est aussi le motif recommandé par la spec §8 (cas 4).
    reloaded = (
        await session.execute(
            select(Project)
            .where(Project.id == project.id)
            .options(
                # `type: ignore` : mypy voit l'attribut de relation SQLModel comme un `list[...]`
                # et non comme l'attribut mappé attendu par SQLAlchemy.
                selectinload(Project.rooms)  # type: ignore[arg-type]
                .selectinload(Room.faces)  # type: ignore[arg-type]
                .selectinload(Face.elements)  # type: ignore[arg-type]
            )
        )
    ).scalar_one()

    rooms = list(reloaded.rooms)
    assert [r.name for r in rooms] == ["Salle de bain"]
    assert rooms[0].wall_thickness_cm == 12.5
    # La géométrie est bien stockée et relue en JSON (spec §8, cas 1).
    assert rooms[0].polygon == [[0, 0], [300, 0], [300, 250], [0, 250]]

    faces = list(rooms[0].faces)
    assert [f.label for f in faces] == ["A"]
    assert faces[0].kind is FaceKind.WALL
    assert faces[0].covering["material"] == "faience"

    elements = list(faces[0].elements)
    assert [e.kind for e in elements] == [ElementKind.WINDOW]
    assert elements[0].x_offset_cm == 80
    assert elements[0].id == element.id
    assert face.room_id == room.id


async def test_deleting_a_project_cascades_to_the_whole_tree(
    session: AsyncSession, owner: User
) -> None:
    project, _room, _face, _element = await _make_plan(session, owner)

    await session.delete(project)
    await session.commit()

    assert (await session.execute(select(Room))).scalars().all() == []
    assert (await session.execute(select(Face))).scalars().all() == []
    assert (await session.execute(select(Element))).scalars().all() == []


# --- A3 : intégrité référentielle -------------------------------------------------------------


async def test_foreign_key_blocks_element_on_missing_face(
    session: AsyncSession, foreign_keys_enforced: bool
) -> None:
    assert foreign_keys_enforced, "le moteur de test n'applique pas les FK : le test serait vide"

    session.add(Element(face_id=999_999, kind=ElementKind.DOOR_HINGED))

    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_face_label_is_unique_within_a_room(
    session: AsyncSession, owner: User
) -> None:
    _project, room, _face, _element = await _make_plan(session, owner)

    session.add(Face(room_id=room.id or 0, label="A", kind=FaceKind.WALL))

    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_furniture_type_slug_is_unique(session: AsyncSession) -> None:
    session.add(FurnitureType(slug="commode", name="Commode", category="bedroom"))
    await session.commit()

    session.add(FurnitureType(slug="commode", name="Commode 4 tiroirs", category="bedroom"))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


# --- Décisions figées de la spec §8 -----------------------------------------------------------


async def test_furniture_recipe_survives_a_round_trip(session: AsyncSession) -> None:
    """La recette de composition (spec §4.1) est stockée telle quelle, sans normalisation."""
    parts = [
        {"type": "box", "rel_position": [0.5, 0.5, 0.5], "rel_size": [1, 1, 1],
         "color_slot": "corps"},
        {"type": "box", "rel_position": [0.5, "auto", 1.01], "rel_size": [0.9, 0.18, 0.02],
         "color_slot": "facade", "repeat_y": 4, "gap": 0.02},
    ]
    session.add(
        FurnitureType(
            slug="commode",
            name="Commode",
            category="bedroom",
            color_slots=["corps", "facade", "poignee"],
            parts=parts,
        )
    )
    await session.commit()
    session.expunge_all()

    reloaded = (
        await session.execute(select(FurnitureType).where(FurnitureType.slug == "commode"))
    ).scalar_one()
    assert reloaded.color_slots == ["corps", "facade", "poignee"]
    assert reloaded.parts == parts
    assert reloaded.parts[1]["repeat_y"] == 4


async def test_optimistic_locking_detects_a_concurrent_write(
    session: AsyncSession, engine: object, owner: User
) -> None:
    """Spec §8, cas 3 : verrouillage optimiste, pas de « dernière écriture gagne »."""
    from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

    assert isinstance(engine, AsyncEngine)
    project = Project(name="Projet partagé", owner_id=owner.id or 0)
    session.add(project)
    await session.commit()
    assert project.version == 1

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as other_session:
        concurrent = (
            await other_session.execute(select(Project).where(Project.id == project.id))
        ).scalar_one()
        concurrent.name = "Renommé par l'autre session"
        await other_session.commit()

    # Notre copie porte encore version=1 : l'écriture doit être refusée, pas écrasée.
    project.name = "Renommé par nous"
    with pytest.raises(StaleDataError):
        await session.commit()
    await session.rollback()


async def test_shared_view_state_is_stored_as_json(
    session: AsyncSession, owner: User
) -> None:
    project = Project(name="Projet à partager", owner_id=owner.id or 0)
    session.add(project)
    await session.flush()

    state = {
        "camera_preset": "face",
        "visible_faces": ["A", "B"],
        "transparent_faces": ["C"],
        "camera_position": [100.0, 200.0, 300.0],
    }
    session.add(SharedView(project_id=project.id or 0, token="abc123", state=state))
    await session.commit()
    session.expunge_all()

    reloaded = (
        await session.execute(select(SharedView).where(SharedView.token == "abc123"))
    ).scalar_one()
    assert reloaded.state == state


async def test_enums_are_stored_by_value_not_by_python_name(
    session: AsyncSession, owner: User
) -> None:
    """La base et le JSON de l'API doivent parler la même langue.

    Par défaut SQLAlchemy persiste le *nom* du membre (`CEILING`) alors que l'API sérialise sa
    valeur (`"ceiling"`). Sans alignement, tout accès SQL direct doit connaître deux conventions.
    """
    _project, _room, face, element = await _make_plan(session, owner)

    raw_face_kind = (
        await session.execute(text("SELECT kind FROM face WHERE id = :id"), {"id": face.id})
    ).scalar_one()
    raw_element_kind = (
        await session.execute(text("SELECT kind FROM element WHERE id = :id"), {"id": element.id})
    ).scalar_one()

    assert raw_face_kind == "wall", raw_face_kind
    assert raw_element_kind == "window", raw_element_kind


# --- A4 : admin SQLAdmin ----------------------------------------------------------------------

ADMIN_IDENTITIES = ["user", "project", "room", "face", "element", "furniture-type", "shared-view"]


@pytest.mark.parametrize("identity", ADMIN_IDENTITIES)
async def test_admin_requires_authentication(client: AsyncClient, identity: str) -> None:
    """`/admin` expose un CRUD complet : il ne doit pas être accessible sans session."""
    response = await client.get(f"/admin/{identity}/list")

    assert response.status_code in (302, 307), response.status_code
    assert "/admin/login" in response.headers.get("location", "")


@pytest.mark.parametrize("identity", ADMIN_IDENTITIES)
async def test_admin_lists_every_model(admin_client: AsyncClient, identity: str) -> None:
    response = await admin_client.get(f"/admin/{identity}/list")
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("identity", ADMIN_IDENTITIES)
async def test_admin_exposes_a_creation_form_for_every_model(
    admin_client: AsyncClient, identity: str
) -> None:
    response = await admin_client.get(f"/admin/{identity}/create")
    assert response.status_code == 200, response.text


async def test_admin_edits_every_model(
    admin_client: AsyncClient, session: AsyncSession, owner: User
) -> None:
    """Le critère demande que l'admin *permette d'éditer* : on charge le formulaire d'édition
    de chaque modèle sur une ligne réelle, et on vérifie qu'une modification est bien écrite."""
    _project, room, face, element = await _make_plan(session, owner)
    furniture = FurnitureType(slug="table", name="Table", category="kitchen")
    session.add(furniture)
    shared = SharedView(project_id=_project.id or 0, token="jeton-admin", state={})
    session.add(shared)
    await session.commit()

    rows = {
        "user": owner.id,
        "project": _project.id,
        "room": room.id,
        "face": face.id,
        "element": element.id,
        "furniture-type": furniture.id,
        "shared-view": shared.id,
    }
    for identity, pk in rows.items():
        response = await admin_client.get(f"/admin/{identity}/edit/{pk}")
        assert response.status_code == 200, f"{identity} : {response.status_code}"

    # Une édition réelle, pas seulement l'affichage du formulaire.
    # Noms de champs relevés sur le formulaire réel : SQLAdmin expose la *relation*
    # (`project`) et non la clé étrangère, et attend les horodatages.
    now = utcnow().strftime("%Y-%m-%d %H:%M:%S")
    response = await admin_client.post(
        f"/admin/room/edit/{room.id}",
        data={
            "project": str(_project.id),
            "name": "Salle de bain rénovée",
            "wall_thickness_cm": "15",
            "ceiling_height_cm": "250",
            "polygon": "[]",
            "created_at": now,
            "updated_at": now,
        },
    )
    assert response.status_code in (200, 302), response.text

    session.expunge_all()
    reloaded = (await session.execute(select(Room).where(Room.id == room.id))).scalar_one()
    assert reloaded.name == "Salle de bain rénovée"
    assert reloaded.wall_thickness_cm == 15


async def test_admin_never_exposes_the_password_hash(
    admin_client: AsyncClient, owner: User
) -> None:
    for path in (f"/admin/user/details/{owner.id}", f"/admin/user/edit/{owner.id}"):
        response = await admin_client.get(path)
        assert response.status_code == 200
        assert owner.hashed_password not in response.text, path
        assert "hashed_password" not in response.text.lower(), path
