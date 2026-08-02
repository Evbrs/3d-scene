"""Ticket P10 — passe performance (`docs/spec-complete.md` §8, cas 4 et 6).

La spec est explicite sur la méthode : « **Mesurer** le N+1 en activant le logging SQL
(`echo=True`) **avant** d'optimiser — sinon l'optimisation n'a pas de sens concret ». Ces tests
comptent donc réellement les requêtes SQL, et comparent le chargement naïf au chargement anticipé
sur le même jeu de données.
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import col, select

from app.core.cache import SceneCache, scene_key
from app.models.plan import Face, Project, Room

CARRE: list[list[float]] = [[0, 0], [400, 0], [400, 300], [0, 300]]


class QueryCounter:
    """Compte les requêtes SQL réellement émises.

    C'est l'équivalent programmatique de `echo=True` : au lieu de lire des logs à l'œil, on
    obtient un nombre sur lequel un test peut porter.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine.sync_engine
        self.statements: list[str] = []

    def __enter__(self) -> "QueryCounter":
        event.listen(self.engine, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *exc: object) -> None:
        event.remove(self.engine, "before_cursor_execute", self._record)

    def _record(
        self, _conn: Any, _cursor: Any, statement: str, *_rest: Any
    ) -> None:
        self.statements.append(statement)

    @property
    def count(self) -> int:
        return len(self.statements)

    def selects_on(self, table: str) -> int:
        return sum(1 for s in self.statements if f"FROM {table}" in s or f'FROM "{table}"' in s)


async def _build_plan(client: AsyncClient, rooms: int) -> int:
    project = (await client.post("/api/projects", json={"name": "Mesure"})).json()
    for index in range(rooms):
        offset = index * 500
        polygon = [[x + offset, y] for x, y in CARRE]
        room = (
            await client.post(
                f"/api/projects/{project['id']}/rooms",
                json={"name": f"Pièce {index}", "polygon": polygon},
            )
        ).json()
        for face in room["faces"][:2]:
            await client.post(
                f"/api/faces/{face['id']}/elements",
                json={"kind": "window", "x_offset_cm": 10, "y_offset_cm": 100,
                      "width_cm": 90, "height_cm": 110},
            )
    return int(project["id"])


# --- §8 cas 4 : mesurer le N+1 avant d'optimiser ------------------------------------------


@pytest.mark.parametrize("rooms", [1, 3, 6])
async def test_the_naive_loading_really_produces_an_n_plus_one(
    auth_client: AsyncClient, session: AsyncSession, engine: AsyncEngine, rooms: int
) -> None:
    """Constat de départ : sans chargement anticipé, le nombre de requêtes suit la taille du plan.

    Ce test ne protège rien en soi — il **documente le problème** que le suivant résout, et il
    échouerait si quelqu'un croyait à tort que SQLAlchemy résout ça tout seul.
    """
    project_id = await _build_plan(auth_client, rooms)
    session.expunge_all()

    with QueryCounter(engine) as counter:
        project = (
            await session.execute(select(Project).where(col(Project.id) == project_id))
        ).scalar_one()
        for room in (await session.execute(
            select(Room).where(col(Room.project_id) == project.id)
        )).scalars().all():
            for face in (await session.execute(
                select(Face).where(col(Face.room_id) == room.id)
            )).scalars().all():
                await session.refresh(face, ["elements"])

    # 1 projet + 1 liste de pièces + 1 requête de faces par pièce + 1 par face.
    assert counter.count > rooms * 6, (
        f"{counter.count} requêtes pour {rooms} pièce(s) : le N+1 attendu n'est pas reproduit"
    )


@pytest.mark.parametrize("rooms", [1, 3, 6])
async def test_eager_loading_keeps_the_query_count_constant(
    auth_client: AsyncClient, session: AsyncSession, engine: AsyncEngine, rooms: int
) -> None:
    """Après optimisation : le nombre de requêtes ne dépend plus de la taille du plan."""
    project_id = await _build_plan(auth_client, rooms)
    session.expunge_all()

    with QueryCounter(engine) as counter:
        (
            await session.execute(
                select(Project)
                .where(col(Project.id) == project_id)
                .options(
                    selectinload(Project.rooms)  # type: ignore[arg-type]
                    .selectinload(Room.faces)  # type: ignore[arg-type]
                    .selectinload(Face.elements)  # type: ignore[arg-type]
                )
            )
        ).scalar_one()

    # 1 projet + 1 pièces + 1 faces + 1 éléments, quelle que soit la taille du plan.
    assert counter.count == 4, f"{counter.count} requêtes au lieu de 4 : {counter.statements}"


async def test_reading_a_project_through_the_api_has_a_bounded_query_count(
    auth_client: AsyncClient, engine: AsyncEngine
) -> None:
    """La garantie qui compte vraiment : celle observée depuis l'API."""
    small = await _build_plan(auth_client, 1)
    large = await _build_plan(auth_client, 6)

    with QueryCounter(engine) as counter_small:
        await auth_client.get(f"/api/projects/{small}")
    with QueryCounter(engine) as counter_large:
        await auth_client.get(f"/api/projects/{large}")

    assert counter_small.count == counter_large.count, (
        f"{counter_small.count} requêtes pour 1 pièce contre {counter_large.count} pour 6 : "
        "le nombre de requêtes dépend encore de la taille du plan"
    )


async def test_the_scene_endpoint_has_a_bounded_query_count(
    auth_client: AsyncClient, engine: AsyncEngine
) -> None:
    small = await _build_plan(auth_client, 1)
    large = await _build_plan(auth_client, 6)

    with QueryCounter(engine) as counter_small:
        await auth_client.get(f"/api/projects/{small}/scene")
    with QueryCounter(engine) as counter_large:
        await auth_client.get(f"/api/projects/{large}/scene")

    assert counter_small.count == counter_large.count


# --- §8 cas 6 : cache du scene graph et son invalidation ------------------------------------


def test_the_cache_key_carries_the_project_version() -> None:
    """C'est ce qui rend l'invalidation structurelle plutôt que déclarative."""
    assert scene_key(7, 1) != scene_key(7, 2)
    assert scene_key(7, 3) == "scene:7:v3"


async def test_a_disabled_cache_never_breaks_the_endpoint(
    auth_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un cache est une optimisation : indisponible, il doit dégrader, pas casser."""
    project_id = await _build_plan(auth_client, 1)

    response = await auth_client.get(f"/api/projects/{project_id}/scene")

    assert response.status_code == 200
    assert response.headers["X-Cache"] == "miss"
    assert len(response.json()["rooms"][0]["nodes"]) == 6


async def test_the_cache_degrades_gracefully_when_redis_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Une panne Redis ne doit pas remonter en 500."""
    from redis.exceptions import RedisError

    class BrokenClient:
        async def get(self, _key: str) -> str:
            raise RedisError("indisponible")

        async def set(self, *_args: object, **_kwargs: object) -> None:
            raise RedisError("indisponible")

    monkeypatch.setattr("app.core.cache.get_client", lambda: BrokenClient())
    cache = SceneCache()

    assert await cache.get(1, 1) is None
    await cache.set(1, 1, {"rooms": []})
    assert cache.errors == 2


async def test_editing_the_plan_changes_the_cache_key(auth_client: AsyncClient) -> None:
    """L'invalidation : une écriture change la version, donc la clé, donc l'entrée visée."""
    project_id = await _build_plan(auth_client, 1)
    before = (await auth_client.get(f"/api/projects/{project_id}")).json()["version"]

    room_id = (await auth_client.get(f"/api/projects/{project_id}")).json()["rooms"][0]["id"]
    await auth_client.patch(f"/api/rooms/{room_id}", json={"name": "Renommée"})

    after = (await auth_client.get(f"/api/projects/{project_id}")).json()["version"]

    assert after > before
    assert scene_key(project_id, before) != scene_key(project_id, after)


async def test_the_scene_reflects_an_edit_immediately(auth_client: AsyncClient) -> None:
    """Le vrai test d'invalidation : après édition, la scène servie doit être la nouvelle."""
    project_id = await _build_plan(auth_client, 1)
    first = await auth_client.get(f"/api/projects/{project_id}/scene")
    assert len(first.json()["rooms"][0]["nodes"]) == 6

    project = (await auth_client.get(f"/api/projects/{project_id}")).json()
    room_id = project["rooms"][0]["id"]
    pentagone = [*project["rooms"][0]["polygon"], [-100, 150]]
    await auth_client.patch(f"/api/rooms/{room_id}", json={"polygon": pentagone, "force": True})

    second = await auth_client.get(f"/api/projects/{project_id}/scene")

    assert len(second.json()["rooms"][0]["nodes"]) == 7, (
        "la scène servie est restée sur la version précédente : invalidation défaillante"
    )
