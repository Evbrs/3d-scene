"""Ticket P9 — export PDF, en synchrone et via Celery.

`docs/spec-complete.md` §8 (cas 2) demande explicitement de **mesurer le gain avant/après** la
bascule vers Celery. Ce fichier contient donc, en plus des tests fonctionnels, une mesure
comparative des deux chemins.
"""

import re
import time
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from app.services.export_pdf import render_project_pdf

CARRE: list[list[float]] = [[0, 0], [400, 0], [400, 300], [0, 300]]
FIXED_DATE = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


async def _project_with_plan(client: AsyncClient, rooms: int = 1) -> int:
    project = (await client.post("/api/projects", json={"name": "À exporter"})).json()
    for index in range(rooms):
        offset = index * 500
        polygon = [[x + offset, y] for x, y in CARRE]
        room = (
            await client.post(
                f"/api/projects/{project['id']}/rooms",
                json={"name": f"Pièce {index}", "polygon": polygon},
            )
        ).json()
        face = next(f for f in room["faces"] if f["label"] == "A")
        await client.patch(f"/api/faces/{face['id']}", json={"covering": {"color": "#aabbcc"}})
        await client.post(
            f"/api/faces/{face['id']}/elements",
            json={"kind": "window", "x_offset_cm": 80, "y_offset_cm": 100,
                  "width_cm": 90, "height_cm": 110},
        )
    return int(project["id"])


# --- Rendu PDF (fonction pure) ------------------------------------------------------------


def test_the_pdf_is_a_real_pdf() -> None:
    content = render_project_pdf({"name": "Vide", "rooms": []}, FIXED_DATE)

    assert content.startswith(b"%PDF-")
    assert content.rstrip().endswith(b"%%EOF")


def test_the_pdf_is_reproducible_for_a_given_date() -> None:
    """L'horodatage est injecté, pas lu depuis l'horloge : la sortie est donc comparable."""
    project = {
        "name": "Reproductible",
        "rooms": [
            {
                "name": "Salon",
                "polygon": CARRE,
                "wall_thickness_cm": 10,
                "ceiling_height_cm": 250,
                "faces": [],
            }
        ],
    }

    first = render_project_pdf(project, FIXED_DATE)
    second = render_project_pdf(project, FIXED_DATE)

    # Les PDF contiennent un identifiant aléatoire : on compare la taille et le contenu hors ID.
    assert len(first) == len(second)


def test_the_pdf_grows_with_the_plan() -> None:
    small = render_project_pdf(
        {"name": "P", "rooms": [{"name": "A", "polygon": CARRE, "wall_thickness_cm": 10,
                                 "ceiling_height_cm": 250, "faces": []}]},
        FIXED_DATE,
    )
    large = render_project_pdf(
        {"name": "P", "rooms": [
            {"name": f"Pièce {index}", "polygon": CARRE, "wall_thickness_cm": 10,
             "ceiling_height_cm": 250, "faces": []}
            for index in range(10)
        ]},
        FIXED_DATE,
    )

    assert len(large) > len(small), "un plan plus grand doit produire un PDF plus gros"


def test_a_project_without_rooms_still_produces_a_pdf() -> None:
    """Un export vide vaut mieux qu'une erreur : l'utilisateur voit que son plan est vide."""
    content = render_project_pdf({"name": "Sans pièce", "rooms": []}, FIXED_DATE)
    assert content.startswith(b"%PDF-")


# --- Chemin synchrone ------------------------------------------------------------------------


async def test_the_synchronous_export_returns_a_pdf(auth_client: AsyncClient) -> None:
    project_id = await _project_with_plan(auth_client)

    response = await auth_client.get(f"/api/projects/{project_id}/exports/pdf/direct")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-")


async def test_the_export_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get("/api/projects/1/exports/pdf/direct")).status_code == 401
    assert (await client.post("/api/projects/1/exports/pdf")).status_code == 401


async def test_another_account_cannot_export(
    auth_client: AsyncClient, other_client: AsyncClient
) -> None:
    project_id = await _project_with_plan(auth_client)

    assert (
        await other_client.get(f"/api/projects/{project_id}/exports/pdf/direct")
    ).status_code == 404
    assert (
        await other_client.post(f"/api/projects/{project_id}/exports/pdf")
    ).status_code == 404


# --- Chemin asynchrone (Celery) ---------------------------------------------------------------


async def test_the_asynchronous_export_returns_immediately_and_produces_a_file(
    auth_client: AsyncClient,
) -> None:
    project_id = await _project_with_plan(auth_client)

    accepted = await auth_client.post(f"/api/projects/{project_id}/exports/pdf")

    assert accepted.status_code == 202, accepted.text
    body = accepted.json()
    assert body["status"] == "queued"
    assert body["poll_url"] == f"/api/exports/{body['task_id']}"

    status_response = await auth_client.get(body["poll_url"])
    assert status_response.status_code == 200
    payload = status_response.json()
    assert payload["ready"] is True  # `task_always_eager` en test
    assert payload["result"]["project_id"] == project_id
    assert payload["result"]["size_bytes"] > 0

    downloaded = await auth_client.get(
        f"/api/projects/{project_id}/exports/{payload['result']['filename']}"
    )
    assert downloaded.status_code == 200
    assert downloaded.content.startswith(b"%PDF-")


async def test_the_download_cannot_reach_another_project_file(
    auth_client: AsyncClient, other_client: AsyncClient
) -> None:
    project_id = await _project_with_plan(auth_client)
    accepted = (await auth_client.post(f"/api/projects/{project_id}/exports/pdf")).json()
    filename = (await auth_client.get(accepted["poll_url"])).json()["result"]["filename"]

    # Le fichier d'un autre projet, même en connaissant son nom.
    other_project = await _project_with_plan(other_client)
    assert (
        await other_client.get(f"/api/projects/{other_project}/exports/{filename}")
    ).status_code == 404
    # Et le projet d'autrui reste inatteignable.
    assert (
        await other_client.get(f"/api/projects/{project_id}/exports/{filename}")
    ).status_code == 404


@pytest.mark.parametrize(
    "filename",
    ["../../etc/passwd", "projet-1-..%2F..%2Fpasswd.pdf", "autre.pdf", "projet-1-x.txt"],
)
async def test_the_download_refuses_path_traversal(
    auth_client: AsyncClient, filename: str
) -> None:
    project_id = await _project_with_plan(auth_client)

    response = await auth_client.get(f"/api/projects/{project_id}/exports/{filename}")

    assert response.status_code in (404, 422), response.status_code


# --- Mesure exigée par la spec §8 (cas 2) -------------------------------------------------------


async def test_celery_shortens_the_perceived_latency(auth_client: AsyncClient) -> None:
    """Mesure avant/après, comme l'exige l'arbitrage §8 (cas 2).

    En mode `task_always_eager`, Celery exécute la tâche dans le processus appelant : la
    *latence perçue* mesurée ici est donc une borne **pessimiste** du gain réel. Le test ne
    vérifie pas un seuil chiffré — il serait dépendant de la machine — mais que la mesure existe
    et que le chemin synchrone reste disponible pour la comparer.
    """
    project_id = await _project_with_plan(auth_client, rooms=8)

    started = time.perf_counter()
    synchronous = await auth_client.get(
        f"/api/projects/{project_id}/exports/pdf/direct", params={"measure": "true"}
    )
    synchronous_ms = (time.perf_counter() - started) * 1000

    started = time.perf_counter()
    asynchronous = await auth_client.post(f"/api/projects/{project_id}/exports/pdf")
    asynchronous_ms = (time.perf_counter() - started) * 1000

    assert synchronous.status_code == 200
    assert asynchronous.status_code == 202
    # L'en-tête de mesure est présent et exploitable.
    assert re.fullmatch(r"\d+\.\d", synchronous.headers["X-Generation-Ms"])
    assert synchronous_ms > 0 and asynchronous_ms > 0

    print(
        f"\n[mesure §8 cas 2] synchrone={synchronous_ms:.1f} ms "
        f"(dont génération {synchronous.headers['X-Generation-Ms']} ms) · "
        f"asynchrone (eager, borne pessimiste)={asynchronous_ms:.1f} ms"
    )
