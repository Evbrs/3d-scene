"""Ticket P9 — export PDF, en synchrone et via Celery.

`docs/spec-complete.md` §8 (cas 2) demande explicitement de **mesurer le gain avant/après** la
bascule vers Celery. Ce fichier contient donc, en plus des tests fonctionnels, une mesure
comparative des deux chemins.
"""

import re
import threading
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.export_pdf import render_project_pdf
from app.services.seed import seed_catalog
from tests.test_export_pdf import _pdf_text

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


async def test_the_elevation_names_the_furniture_it_draws(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """Le meuble porte son nom de catalogue, pas l'étiquette générique « Meuble ».

    Le chargement de l'export ne lisait que `furniture_type_id` : sur une planche portant
    plusieurs meubles, le document ne disait plus lequel allait où. Le test passe par la route
    réelle, seul moyen de prouver que le nom traverse bien la base et pas seulement le rendu.
    """
    await seed_catalog(session)
    project = (await auth_client.post("/api/projects", json={"name": "À nommer"})).json()
    room = (
        await auth_client.post(
            f"/api/projects/{project['id']}/rooms", json={"name": "Salle de bains",
                                                          "polygon": CARRE}
        )
    ).json()
    meuble = (await auth_client.get("/api/furniture-types/meuble-sous-vasque")).json()
    face = next(f for f in room["faces"] if f["label"] == "A")
    posé = await auth_client.post(
        f"/api/faces/{face['id']}/elements",
        json={"kind": "furniture", "furniture_type_id": meuble["id"], "x_offset_cm": 20,
              "y_offset_cm": 0, "width_cm": 120, "height_cm": 90, "depth_cm": 60},
    )
    assert posé.status_code == 201, posé.text

    response = await auth_client.get(f"/api/projects/{project['id']}/exports/pdf/direct")

    assert response.status_code == 200
    assert meuble["name"] in _pdf_text(response.content)


async def test_the_floor_plan_names_the_furniture_standing_on_the_floor(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """Même exigence pour le mobilier **libre**, que seul le plan coté dessine (spec §10, A7).

    Le chargement de l'export ne parcourait que les éléments adossés à une face : un lit ou un
    îlot, ancré à la pièce (A4), n'atteignait jamais son nom de catalogue et s'imprimait
    « Meuble » — sur la seule planche du dossier où il apparaît, donc sans rattrapage possible.
    """
    await seed_catalog(session)
    project = (await auth_client.post("/api/projects", json={"name": "Au sol"})).json()
    room = (
        await auth_client.post(
            f"/api/projects/{project['id']}/rooms", json={"name": "Chambre", "polygon": CARRE}
        )
    ).json()
    meuble = (await auth_client.get("/api/furniture-types/lit")).json()
    posé = await auth_client.post(
        f"/api/rooms/{room['id']}/elements",
        json={"kind": "furniture", "furniture_type_id": meuble["id"], "pos_x_cm": 200,
              "pos_y_cm": 150, "width_cm": 140, "height_cm": 50, "depth_cm": 190},
    )
    assert posé.status_code == 201, posé.text

    response = await auth_client.get(f"/api/projects/{project['id']}/exports/pdf/direct")

    assert response.status_code == 200
    assert meuble["name"] in _pdf_text(response.content)


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
    assert body["poll_url"] == f"/api/projects/{project_id}/exports/tasks/{body['task_id']}"

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


async def test_the_synchronous_path_exposes_its_generation_time(
    auth_client: AsyncClient,
) -> None:
    """Le chemin synchrone doit rester mesurable : c'est le point de comparaison de §8 cas 2."""
    project_id = await _project_with_plan(auth_client, rooms=2)

    response = await auth_client.get(
        f"/api/projects/{project_id}/exports/pdf/direct", params={"measure": "true"}
    )

    assert response.status_code == 200
    assert re.fullmatch(r"\d+\.\d", response.headers["X-Generation-Ms"])
    assert float(response.headers["X-Generation-Ms"]) > 0


async def test_the_generation_time_grows_with_the_plan(auth_client: AsyncClient) -> None:
    """Ce qui justifie de déporter le travail : la durée suit la taille du plan.

    C'est la propriété qui rend Celery utile, et elle est vérifiable **sans** dépendre de la
    vitesse de la machine — contrairement à un seuil en millisecondes. Une comparaison de
    latences perçues en mode `task_always_eager` ne prouverait rien non plus : la tâche s'y
    exécute dans le processus appelant. La mesure de bout en bout est faite sur la stack réelle
    et consignée dans `PROGRESS.md`.
    """
    small = await _project_with_plan(auth_client, rooms=1)
    large = await _project_with_plan(auth_client, rooms=10)

    async def fastest_of_three(project_id: int) -> float:
        """Minimum de trois mesures, et non une mesure isolée.

        Le rendu se fait maintenant dans un fil du pool : l'ordonnanceur peut y ajouter quelques
        millisecondes arbitraires, ce qui suffisait à inverser deux mesures uniques et faisait
        rougir la CI par intermittence. Le minimum est l'estimateur robuste d'une durée : le bruit
        d'ordonnancement ne fait qu'ajouter du temps, jamais en retirer.
        """
        measures = []
        for _ in range(3):
            response = await auth_client.get(
                f"/api/projects/{project_id}/exports/pdf/direct", params={"measure": "true"}
            )
            measures.append(float(response.headers["X-Generation-Ms"]))
        return min(measures)

    small_ms = await fastest_of_three(small)
    large_ms = await fastest_of_three(large)

    assert large_ms > small_ms, (
        f"génération de {large_ms:.1f} ms pour 10 pièces contre {small_ms:.1f} ms pour 1 : "
        "la durée ne suit pas la taille du plan, la mesure est douteuse"
    )


async def test_the_pdf_is_never_rendered_on_the_event_loop(
    auth_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ReportLab est synchrone : rendu sur la boucle, il fige toutes les requêtes en cours.

    Le chemin synchrone est justement celui que la spec §8 (cas 2) demande de garder pour la
    mesure — il doit rester utilisable en production sans bloquer les autres clients.
    """
    from app.services import export_pdf

    project_id = await _project_with_plan(auth_client)
    seen: list[str] = []
    original = export_pdf.render_project_pdf

    # `watermark` est décidé par le serveur d'après le palier (`app/services/quotas.py`) et passé
    # nommément : sans ce paramètre, l'espion refuse l'appel réel de la route.
    def spy(
        project: dict[str, object], generated_at: datetime, *, watermark: bool = False
    ) -> bytes:
        seen.append(threading.current_thread().name)
        return original(project, generated_at, watermark=watermark)

    monkeypatch.setattr("app.api.exports.render_project_pdf", spy)
    response = await auth_client.get(f"/api/projects/{project_id}/exports/pdf/direct")

    assert response.status_code == 200
    assert seen and threading.main_thread().name not in seen, (
        f"le PDF est rendu sur {seen}, donc sur la boucle d'événements"
    )
