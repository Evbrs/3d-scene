"""Critères d'acceptation du ticket P1 (modèle de données).

Référence : `docs/plan-generation-ia.md` §8 (P1), `docs/spec-complete.md` §5 et §8.
"""

from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import class_mapper, selectinload
from sqlalchemy.orm.exc import StaleDataError
from sqlmodel import SQLModel, col, select

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
from tests.conftest import personal_organization


async def _make_plan(session: AsyncSession, owner: User) -> tuple[Project, Room, Face, Element]:
    """Crée l'arbre Project → Room → Face → Element et le persiste."""
    organization = await personal_organization(session, owner)
    project = Project(
        name="Rénovation appartement",
        description="T3 à rénover",
        owner_id=owner.id or 0,
        organization_id=organization.id or 0,
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
    project = Project(
        name="Projet partagé",
        owner_id=owner.id or 0,
        organization_id=(await personal_organization(session, owner)).id or 0,
    )
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
    project = Project(
        name="Projet à partager",
        owner_id=owner.id or 0,
        organization_id=(await personal_organization(session, owner)).id or 0,
    )
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


# --- Intégrité portée par la base elle-même ---------------------------------------------------
#
# SQLModel désactive la validation Pydantic sur les modèles `table=True` : `Room(wall_thickness_cm
# =-5)` se construit sans broncher. La seule barrière qui vaille pour SQLAdmin, la CLI, Celery et
# le SQL direct est donc la contrainte en base — c'est elle, et non le modèle, qu'on teste ici.


async def _refusal_message(session: AsyncSession, instance: SQLModel) -> str:
    """Message d'erreur renvoyé par la base pour une ligne qu'elle doit refuser."""
    session.add(instance)
    with pytest.raises(IntegrityError) as failure:
        await session.commit()
    await session.rollback()
    return str(failure.value)


@pytest.mark.parametrize(
    ("constraint", "field", "value"),
    [
        ("ck_room_wall_thickness_cm_bounded", "wall_thickness_cm", 0.0),
        ("ck_room_wall_thickness_cm_bounded", "wall_thickness_cm", -5.0),
        ("ck_room_wall_thickness_cm_bounded", "wall_thickness_cm", 10_001.0),
        ("ck_room_ceiling_height_cm_bounded", "ceiling_height_cm", 0.0),
        ("ck_room_ceiling_height_cm_bounded", "ceiling_height_cm", -250.0),
        ("ck_room_ceiling_height_cm_bounded", "ceiling_height_cm", 10_001.0),
        ("ck_room_name_not_empty", "name", ""),
    ],
)
async def test_the_database_refuses_an_impossible_room(
    session: AsyncSession, owner: User, constraint: str, field: str, value: float | str
) -> None:
    project = Project(
        name="Projet",
        owner_id=owner.id or 0,
        organization_id=(await personal_organization(session, owner)).id or 0,
    )
    session.add(project)
    await session.flush()

    attributes: dict[str, Any] = {"project_id": project.id or 0, "name": "Salon", field: value}
    message = await _refusal_message(session, Room(**attributes))

    # Les deux moteurs nomment la contrainte fautive : on vérifie que c'est bien *celle-là* qui a
    # rejeté la ligne, et pas une autre qui aurait masqué un trou.
    assert constraint in message, message


@pytest.mark.parametrize(
    ("constraint", "field", "value"),
    [
        ("ck_element_width_cm_bounded", "width_cm", 0.0),
        ("ck_element_width_cm_bounded", "width_cm", -100.0),
        ("ck_element_width_cm_bounded", "width_cm", 10_001.0),
        ("ck_element_height_cm_bounded", "height_cm", 0.0),
        ("ck_element_height_cm_bounded", "height_cm", -100.0),
        ("ck_element_depth_cm_bounded", "depth_cm", 0.0),
        ("ck_element_depth_cm_bounded", "depth_cm", -50.0),
        ("ck_element_rotation_deg_bounded", "rotation_deg", 99_999.0),
        ("ck_element_rotation_deg_bounded", "rotation_deg", -361.0),
        ("ck_element_offsets_not_negative", "x_offset_cm", -1.0),
        ("ck_element_offsets_not_negative", "y_offset_cm", -1.0),
    ],
)
async def test_the_database_refuses_an_impossible_element(
    session: AsyncSession, owner: User, constraint: str, field: str, value: float
) -> None:
    _project, _room, face, _element = await _make_plan(session, owner)

    attributes: dict[str, Any] = {
        "face_id": face.id or 0,
        "kind": ElementKind.FURNITURE,
        field: value,
    }
    message = await _refusal_message(session, Element(**attributes))

    assert constraint in message, message


@pytest.mark.parametrize(
    ("constraint", "field", "value"),
    [
        ("ck_furnituretype_default_width_cm_bounded", "default_width_cm", 0.0),
        ("ck_furnituretype_default_width_cm_bounded", "default_width_cm", -100.0),
        ("ck_furnituretype_default_width_cm_bounded", "default_width_cm", 1_001.0),
        ("ck_furnituretype_default_height_cm_bounded", "default_height_cm", 0.0),
        ("ck_furnituretype_default_height_cm_bounded", "default_height_cm", -100.0),
        ("ck_furnituretype_default_depth_cm_bounded", "default_depth_cm", 0.0),
        ("ck_furnituretype_default_depth_cm_bounded", "default_depth_cm", -50.0),
    ],
)
async def test_the_database_refuses_an_impossible_furniture_type(
    session: AsyncSession, constraint: str, field: str, value: float
) -> None:
    attributes: dict[str, Any] = {
        "slug": "meuble-impossible",
        "name": "Meuble impossible",
        "category": "general",
        field: value,
    }
    message = await _refusal_message(session, FurnitureType(**attributes))

    assert constraint in message, message


# --- Ordre des collections enfant --------------------------------------------------------------


@pytest.mark.parametrize(
    ("parent", "relation", "expected"),
    [
        (Project, "rooms", "room.id"),
        (Room, "faces", "face.id"),
        (Face, "elements", "element.id"),
    ],
)
def test_child_collections_declare_an_explicit_order(
    parent: type[SQLModel], relation: str, expected: str
) -> None:
    """Sans `order_by`, PostgreSQL rend les lignes dans l'ordre physique du heap.

    Un `UPDATE` y réécrit la ligne en fin de table : renommer une pièce suffit à la faire passer
    en dernier. Le frontend sélectionne la dernière pièce après création — l'utilisateur
    dessinerait alors dans une autre pièce et en écraserait le polygone.

    C'est cette assertion-là qui protège les deux moteurs : le test comportemental ci-dessous ne
    peut pas *reproduire* le désordre sur SQLite, dont le parcours suit toujours le rowid.
    """
    # `order_by` vaut `False` — et non une séquence vide — quand la relation n'en déclare aucun.
    order_by = class_mapper(parent).relationships[relation].order_by
    assert order_by, f"{parent.__name__}.{relation} ne déclare aucun ordre"

    assert [str(clause) for clause in order_by] == [expected]


async def _room_names(session: AsyncSession, project_id: int) -> list[str]:
    project = (
        await session.execute(
            select(Project)
            .where(Project.id == project_id)
            .options(selectinload(Project.rooms))  # type: ignore[arg-type]
        )
    ).scalar_one()
    return [room.name for room in project.rooms]


async def test_renaming_a_room_does_not_reshuffle_the_others(
    session: AsyncSession, owner: User
) -> None:
    project = Project(
        name="Trois pièces",
        owner_id=owner.id or 0,
        organization_id=(await personal_organization(session, owner)).id or 0,
    )
    session.add(project)
    await session.flush()
    for name in ("Salon", "Cuisine", "Chambre"):
        session.add(Room(project_id=project.id or 0, name=name))
    await session.commit()
    session.expunge_all()

    assert await _room_names(session, project.id or 0) == ["Salon", "Cuisine", "Chambre"]

    first = (
        await session.execute(select(Room).where(Room.name == "Salon"))
    ).scalar_one()
    first.name = "Salon rénové"
    await session.commit()
    session.expunge_all()

    assert await _room_names(session, project.id or 0) == ["Salon rénové", "Cuisine", "Chambre"]


# --- Colonnes d'hygiène ------------------------------------------------------------------------


async def test_a_raw_sql_insert_needs_only_the_business_columns(
    session: AsyncSession, owner: User
) -> None:
    """En incident, le chemin le plus court est `psql`.

    Sans valeur par défaut côté serveur, un `INSERT` écrit à la main échouait sur trois `NOT NULL`
    — `created_at`, `updated_at` et `version` — que l'auteur de la requête n'a aucune raison de
    connaître. La voie rapide était fermée précisément quand on en a besoin.
    """
    organization = await personal_organization(session, owner)
    # `organization_id` est une colonne **métier** au même titre que `owner_id` : qui insère un
    # projet à la main doit dire à quel locataire il appartient. Ce sont les colonnes de
    # plomberie — horodatages et version — que ce test exige de pouvoir omettre.
    await session.execute(
        text(
            "INSERT INTO project (owner_id, organization_id, name) "
            "VALUES (:owner, :organization, :name)"
        ),
        {
            "owner": owner.id,
            "organization": organization.id,
            "name": "Créé en SQL direct",
        },
    )
    await session.commit()

    project = (
        await session.execute(select(Project).where(Project.name == "Créé en SQL direct"))
    ).scalar_one()
    assert project.version == 1
    assert project.created_at is not None
    assert project.updated_at is not None

    # Une pièce garde ses dimensions obligatoires — ce sont des données métier, pas de la
    # plomberie : qui insère une pièce à la main doit décider de l'épaisseur de ses murs.
    await session.execute(
        text(
            "INSERT INTO room (project_id, name, wall_thickness_cm, ceiling_height_cm) "
            "VALUES (:project, :name, 10, 250)"
        ),
        {"project": project.id, "name": "Pièce créée en SQL"},
    )
    await session.commit()

    room = (
        await session.execute(select(Room).where(Room.name == "Pièce créée en SQL"))
    ).scalar_one()
    # Les conteneurs JSON, eux, ont une valeur par défaut : sans elle, toute lecture de cette
    # ligne échouerait plus loin sur un `None` là où le code attend une liste.
    assert room.polygon == []


async def test_a_raw_sql_insert_creates_a_usable_account(session: AsyncSession) -> None:
    await session.execute(
        text('INSERT INTO "user" (email, hashed_password) VALUES (:email, :hash)'),
        {"email": "cree-en-sql@exemple.fr", "hash": "argon2-factice"},
    )
    await session.commit()

    user = (
        await session.execute(select(User).where(User.email == "cree-en-sql@exemple.fr"))
    ).scalar_one()
    assert user.is_active is True
    assert user.is_superuser is False
    assert user.token_version == 0
    assert user.email_verified_at is None


async def test_updated_at_moves_without_being_assigned(
    session: AsyncSession, owner: User
) -> None:
    """Sans `onupdate`, seule une affectation explicite dans le code applicatif datait la ligne.

    Une correction passée par SQLAdmin, par la CLI ou par Celery laissait donc `updated_at` à sa
    valeur de création — et la liste des projets, triée dessus, mentait.
    """
    project = Project(
        name="Projet suivi",
        owner_id=owner.id or 0,
        organization_id=(await personal_organization(session, owner)).id or 0,
    )
    session.add(project)
    await session.commit()
    before = project.updated_at

    project.description = "Modifié sans toucher à updated_at"
    await session.commit()

    assert project.updated_at > before


async def test_the_expiry_of_a_shared_view_lives_in_a_column(
    session: AsyncSession, owner: User
) -> None:
    """L'expiration rangée dans le blob `state` n'était ni indexable, ni visible depuis SQLAdmin.

    Elle est désormais une colonne à part entière, comme la révocation et le libellé.
    """
    project = Project(
        name="Projet à partager",
        owner_id=owner.id or 0,
        organization_id=(await personal_organization(session, owner)).id or 0,
    )
    session.add(project)
    await session.flush()

    expiry = utcnow()
    session.add(
        SharedView(
            project_id=project.id or 0,
            token="jeton-avec-colonnes",
            state={"camera_preset": "face"},
            expires_at=expiry,
            label="Lien client",
        )
    )
    await session.commit()
    session.expunge_all()

    reloaded = (
        await session.execute(select(SharedView).where(SharedView.token == "jeton-avec-colonnes"))
    ).scalar_one()
    assert reloaded.expires_at is not None
    assert reloaded.label == "Lien client"
    assert reloaded.revoked_at is None
    assert reloaded.view_count == 0
    assert reloaded.password_hash is None

    # Tout l'intérêt de la manœuvre : l'expiration se **requête**. Rangée dans `state`, il fallait
    # relire et désérialiser chaque ligne pour savoir laquelle était morte.
    session.add(
        SharedView(project_id=project.id or 0, token="jeton-sans-expiration", state={})
    )
    await session.commit()

    expiring = (
        (
            await session.execute(
                select(SharedView).where(col(SharedView.expires_at) <= utcnow() + timedelta(days=1))
            )
        )
        .scalars()
        .all()
    )
    assert [view.token for view in expiring] == ["jeton-avec-colonnes"]


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


async def test_admin_session_is_revoked_when_the_account_loses_its_rights(
    admin_client: AsyncClient, session: AsyncSession
) -> None:
    """Une session admin doit être révocable.

    Ne vérifier les droits qu'au moment du login rendrait la session valide jusqu'à expiration
    du cookie (14 jours par défaut), même après rétrogradation ou suppression du compte.
    """
    assert (await admin_client.get("/admin/user/list")).status_code == 200

    admin = (
        await session.execute(select(User).where(User.email == "admin-test@exemple.fr"))
    ).scalar_one()
    admin.is_superuser = False
    await session.commit()

    downgraded = await admin_client.get("/admin/user/list")
    assert downgraded.status_code in (302, 307), downgraded.status_code


async def test_admin_session_dies_with_the_account(
    admin_client: AsyncClient, session: AsyncSession
) -> None:
    admin = (
        await session.execute(select(User).where(User.email == "admin-test@exemple.fr"))
    ).scalar_one()
    await session.delete(admin)
    await session.commit()

    response = await admin_client.get("/admin/user/list")
    assert response.status_code in (302, 307), response.status_code


async def test_a_non_superuser_cannot_log_into_the_admin(
    client: AsyncClient, session: AsyncSession
) -> None:
    from app.core.security import hash_password

    session.add(
        User(email="simple@exemple.fr", hashed_password=hash_password("motdepasse-simple-2026"))
    )
    await session.commit()

    await client.post(
        "/admin/login", data={"username": "simple@exemple.fr", "password": "motdepasse-simple-2026"}
    )
    response = await client.get("/admin/user/list")
    assert response.status_code in (302, 307)


async def test_admin_never_exposes_the_password_hash(
    admin_client: AsyncClient, owner: User
) -> None:
    for path in (f"/admin/user/details/{owner.id}", f"/admin/user/edit/{owner.id}"):
        response = await admin_client.get(path)
        assert response.status_code == 200
        assert owner.hashed_password not in response.text, path
        assert "hashed_password" not in response.text.lower(), path
