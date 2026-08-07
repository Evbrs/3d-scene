from typing import Any
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.seed import seed_catalog

CARRE: list[list[float]] = [[0, 0], [400, 0], [400, 300], [0, 300]]

async def test_dbg(auth_client: AsyncClient, session: AsyncSession) -> None:
    await seed_catalog(session)
    biblio = (await auth_client.get("/api/furniture-types/bibliotheque")).json()
    project = (await auth_client.post("/api/projects", json={"name": "D"})).json()
    room = (await auth_client.post(f"/api/projects/{project['id']}/rooms",
        json={"name": "S", "polygon": CARRE, "wall_thickness_cm": 10})).json()
    face = room["faces"][0]["id"]
    pid = project["id"]

    async def version() -> int:
        return int((await auth_client.get(f"/api/projects/{pid}")).json()["version"])

    for vp in ({}, {"nb_etageres": 3}, {"nb_etageres": 9}):
        print("version avant creation:", await version())
        el = (await auth_client.post(f"/api/faces/{face}/elements",
            json={"kind": "furniture", "furniture_type_id": biblio["id"],
                  "x_offset_cm": 0, "y_offset_cm": 0,
                  "width_cm": 80, "height_cm": 180, "depth_cm": 30,
                  "variant_params": vp})).json()
        print("version apres creation:", await version(), "el", el["id"])
        scene = (await auth_client.get(f"/api/projects/{pid}/scene")).json()
        nodes = [n for n in scene["rooms"][0]["nodes"] if n["kind"] == "furniture"]
        print("  -> node vp:", [n.get("variant_params") for n in nodes])
        await auth_client.delete(f"/api/elements/{el['id']}")
        print("version apres suppression:", await version())
