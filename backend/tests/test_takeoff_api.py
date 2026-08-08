"""Le métré exposé par l'API, et son export tableur (`docs/strategie-produit.md` §3.1).

Les fixtures de `tests/geometry/` vérifient le *calcul* ; ici on vérifie le *chemin complet* :
depuis les modèles en base jusqu'au JSON et au CSV, avec les permissions.

La pièce de référence est la même dans les trois fichiers de ce lot — 400 x 300 aux murs de 10,
sous 250 de plafond — pour que les chiffres se retrouvent d'un test à l'autre :

- surface au sol **nette** : 390 x 290 = 11,31 m² (et non 12,00 : c'est tout l'enjeu du §3.1) ;
- surface de murs brute : (4,00 + 3,00 + 4,00 + 3,00) x 2,50 = 35,00 m² ;
- périmètre au nu intérieur : 2 x (3,90 + 2,90) = 13,60 ml.
"""

from typing import Any

from httpx import AsyncClient

from app.api.takeoff import CSV_COLUMNS, UTF8_BOM, takeoff_to_csv

CARRE: list[list[float]] = [[0, 0], [400, 0], [400, 300], [0, 300]]


async def build_room(client: AsyncClient) -> tuple[dict[str, Any], dict[str, Any]]:
    """Pièce de référence : revêtements posés, une porte sur le mur B."""
    project = (await client.post("/api/projects", json={"name": "Chantier"})).json()
    room = (
        await client.post(
            f"/api/projects/{project['id']}/rooms",
            json={
                "name": "Salle de bains",
                "polygon": CARRE,
                "wall_thickness_cm": 10,
                "ceiling_height_cm": 250,
            },
        )
    ).json()
    faces = {face["label"]: face for face in room["faces"]}

    await client.patch(
        f"/api/faces/{faces['A']['id']}",
        json={"covering": {"material": "faience", "unit_width_cm": 20, "unit_height_cm": 20}},
    )
    await client.patch(
        f"/api/faces/{faces['SOL']['id']}", json={"covering": {"material": "Carrelage"}}
    )
    await client.patch(
        f"/api/faces/{faces['PLAFOND']['id']}", json={"covering": {"material": "peinture"}}
    )
    await client.post(
        f"/api/faces/{faces['B']['id']}/elements",
        json={
            "kind": "door_hinged",
            "x_offset_cm": 50,
            "y_offset_cm": 0,
            "width_cm": 90,
            "height_cm": 204,
        },
    )
    return project, room


async def test_the_takeoff_measures_the_room_the_way_a_quote_needs_it(
    auth_client: AsyncClient,
) -> None:
    project, _room = await build_room(auth_client)

    response = await auth_client.get(f"/api/projects/{project['id']}/takeoff")

    assert response.status_code == 200, response.text
    takeoff = response.json()
    assert takeoff["units"] == {"area": "m2", "length": "ml", "volume": "m3"}
    room = takeoff["rooms"][0]
    # La surface **nette**, pas celle de la ligne médiane : 12,00 m² serait 6 % de trop, et
    # facturer 6 % de trop est un litige (`docs/strategie-produit.md` §3.1).
    assert room["floor_area_m2"] == 11.31
    assert room["wall_gross_area_m2"] == 35.0
    assert room["net_perimeter_ml"] == 13.6
    # La porte descend au sol : elle interrompt la plinthe sur sa largeur.
    assert room["skirting_ml"] == 12.7
    assert takeoff["warnings"] == []


async def test_an_empty_project_has_a_takeoff_and_not_an_error(auth_client: AsyncClient) -> None:
    """Un projet sans pièce doit répondre : c'est l'état d'un chantier qu'on vient d'ouvrir."""
    project = (await auth_client.post("/api/projects", json={"name": "Vide"})).json()

    takeoff = (await auth_client.get(f"/api/projects/{project['id']}/takeoff")).json()

    assert takeoff["rooms"] == []
    assert takeoff["totals"]["room_count"] == 0
    assert takeoff["warnings"] == []


async def test_the_takeoff_follows_the_plan_when_it_changes(auth_client: AsyncClient) -> None:
    """Le métré lit la scène mise en cache : une écriture du plan doit s'y refléter.

    Sans ce test, une invalidation de cache cassée resterait invisible — le métré continuerait de
    servir les quantités d'avant les travaux de l'utilisateur.
    """
    project, room = await build_room(auth_client)
    before = (await auth_client.get(f"/api/projects/{project['id']}/takeoff")).json()

    await auth_client.patch(f"/api/rooms/{room['id']}", json={"ceiling_height_cm": 300})
    after = (await auth_client.get(f"/api/projects/{project['id']}/takeoff")).json()

    assert before["rooms"][0]["wall_gross_area_m2"] == 35.0
    assert after["rooms"][0]["wall_gross_area_m2"] == 42.0
    assert after["rooms"][0]["ceiling_height_m"] == 3.0


# --- Export tableur ------------------------------------------------------------------------------


async def test_the_csv_export_is_readable_by_a_french_spreadsheet(
    auth_client: AsyncClient,
) -> None:
    """Point-virgule, virgule décimale et BOM : sans les trois, le fichier est inexploitable."""
    project, _room = await build_room(auth_client)

    response = await auth_client.get(f"/api/projects/{project['id']}/takeoff.csv")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert f'filename="metre-projet-{project["id"]}.csv"' in response.headers[
        "content-disposition"
    ]

    body = response.text
    assert body.startswith(UTF8_BOM)
    header = body[len(UTF8_BOM) :].splitlines()[0]
    assert header.split(";") == list(CSV_COLUMNS)
    # Virgule décimale : « 11,31 » et non « 11.31 ».
    assert ";11,31;" in body


async def test_the_csv_has_one_row_per_face_and_repeats_the_room(auth_client: AsyncClient) -> None:
    """Une ligne par face, colonnes de pièce répétées : c'est ce qui rend le fichier pivotable."""
    project, _room = await build_room(auth_client)

    body = (await auth_client.get(f"/api/projects/{project['id']}/takeoff.csv")).text
    rows = [
        line for line in body[len(UTF8_BOM) :].splitlines()[1:] if line.startswith("Salle de bains")
    ]

    # 4 murs + le sol + le plafond.
    assert len(rows) == 6
    assert [row.split(";")[5] for row in rows] == ["A", "B", "C", "D", "SOL", "PLAFOND"]
    assert all(row.split(";")[1] == "2,5" for row in rows)


def test_the_csv_never_swallows_a_warning() -> None:
    """Un métré incomplet exporté sans ses réserves donne un devis trop bas.

    Le test travaille sur un métré fabriqué à la main : provoquer un avertissement réel demanderait
    une géométrie dégénérée, et ce qu'on vérifie ici est le **transport** de l'avertissement.
    """
    csv_text = takeoff_to_csv(
        {
            "rooms": [
                {
                    "name": "Combles",
                    "ceiling_height_m": 2.0,
                    "floor_area_m2": None,
                    "faces": [
                        {
                            "face_label": "A",
                            "kind": "wall",
                            "net_area_m2": None,
                            "material": None,
                            "tiling": None,
                        }
                    ],
                }
            ],
            "totals": {"floor_area_m2": 0.0},
            "warnings": ["surface au sol de « Combles » non établissable"],
        }
    )

    assert "# AVERTISSEMENT;surface au sol de « Combles » non établissable" in csv_text
    # Une inconnue reste une cellule vide : un zéro se confondrait avec une mesure réellement nulle.
    face_row = next(line for line in csv_text.splitlines() if line.startswith("Combles"))
    assert face_row.split(";")[11] == ""


def test_the_csv_carries_the_project_totals() -> None:
    """Les totaux sont écrits en pied de fichier : l'artisan ne doit pas avoir à les recalculer."""
    csv_text = takeoff_to_csv(
        {
            "rooms": [],
            "totals": {"floor_area_m2": 11.31, "skirting_ml": 12.7, "door_count": 1},
            "warnings": [],
        }
    )

    assert "# TOTAUX DU PROJET" in csv_text
    assert "# surface de sol (m2);11,31" in csv_text
    assert "# plinthe (ml);12,7" in csv_text
    assert "# nombre de portes;1" in csv_text


async def test_a_viewer_may_read_the_takeoff(auth_client: AsyncClient) -> None:
    """Le métré est une lecture : il ne demande pas plus que `viewer`.

    C'est délibéré — un conducteur de travaux consulte les quantités sans avoir le droit de
    toucher au plan.
    """
    project, _room = await build_room(auth_client)

    assert (await auth_client.get(f"/api/projects/{project['id']}/takeoff")).status_code == 200
    assert (await auth_client.get(f"/api/projects/{project['id']}/takeoff.csv")).status_code == 200
