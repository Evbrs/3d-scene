"""Conformité RGPD : droit d'accès, portabilité, effacement, minimisation.

Seul le droit à l'effacement était traité par le produit, et il l'était par accident — la cascade
`ON DELETE` des clés étrangères. Les articles 15 (accès) et 20 (portabilité) n'avaient aucune
route ; l'article 13 (information) n'avait aucune page.

Trois propriétés sont vérifiées ici, et la deuxième est celle qui compte le plus :

1. l'export **contient** ce que le compte a produit — sinon ce n'est pas un droit d'accès ;
2. l'export **s'arrête au périmètre du compte**. Une route qui rend tout ce que le titulaire peut
   voir est aussi celle où un filtre oublié rend tout ce que les *autres* peuvent voir : elle ne
   porte aucun identifiant dans son URL, donc rien ne la protège qu'un `WHERE` correct. C'est à ce
   titre qu'elle est classée dans `TENANT_COLLECTIONS`
   (`tests/test_permissions_locataire.py`), et ce fichier est le test de non-fuite qu'exige cette
   classification ;
3. l'export **ne rend pas les secrets** : ni le hachage du mot de passe, ni celui d'un jeton. Ils
   ne concernent pas la personne, ils protègent son compte.
"""

import json
from typing import Any

from httpx import AsyncClient

from tests.conftest import USER_PASSWORD

CARRE: list[list[float]] = [[0, 0], [400, 0], [400, 300], [0, 300]]


async def _tenant(client: AsyncClient, nom: str) -> dict[str, Any]:
    """Un locataire complet : projet, pièce, élément, vue partagée, barème, devis."""
    project = (await client.post("/api/projects", json={"name": nom})).json()
    room = (
        await client.post(
            f"/api/projects/{project['id']}/rooms", json={"name": "Salon", "polygon": CARRE}
        )
    ).json()
    face = room["faces"][0]
    await client.post(
        f"/api/faces/{face['id']}/elements",
        json={"kind": "window", "x_offset_cm": 10, "y_offset_cm": 100,
              "width_cm": 90, "height_cm": 110},
    )
    await client.post(
        f"/api/projects/{project['id']}/shared-views",
        json={"state": {"camera_preset": "face", "room_index": 0}},
    )
    quote = (
        await client.post(
            f"/api/projects/{project['id']}/quotes", json={"client_name": f"Client {nom}"}
        )
    ).json()
    return {"project": project, "room": room, "quote": quote}


async def test_the_export_contains_the_account_and_its_plan(auth_client: AsyncClient) -> None:
    mine = await _tenant(auth_client, "Chantier exporté")

    export = await auth_client.get("/api/auth/me/export")
    assert export.status_code == 200, export.text
    body = export.json()

    assert body["compte"]["email"] == "titulaire@exemple.fr"
    assert [projet["name"] for projet in body["projets"]] == ["Chantier exporté"]
    assert [piece["name"] for piece in body["pieces"]] == ["Salon"]
    assert len(body["faces"]) == len(mine["room"]["faces"])
    assert len(body["elements"]) == 1
    assert len(body["vues_partagees"]) == 1
    assert [devis["id"] for devis in body["devis"]] == [mine["quote"]["id"]]
    assert body["organisations"] and body["appartenances"]


async def test_the_export_is_really_serialisable(auth_client: AsyncClient) -> None:
    """Un export qui casse au dernier moment ne rend rien du tout.

    Les horodatages et les `Decimal` du chemin de l'argent ne passent pas tels quels : c'est le
    genre de défaut qui n'apparaît que sur un compte ayant réellement établi un devis.
    """
    await _tenant(auth_client, "Chantier sérialisé")
    export = await auth_client.get("/api/auth/me/export")

    dumped = json.dumps(export.json(), ensure_ascii=False)
    assert len(dumped) > 500


async def test_the_export_never_leaks_another_organization(
    auth_client: AsyncClient, other_client: AsyncClient
) -> None:
    """Le test de non-fuite exigé par le classement en `TENANT_COLLECTIONS`.

    La route ne porte aucun identifiant : un intrus n'a rien à deviner, il lui suffit d'appeler.
    Un filtre oublié ne se verrait pas — la requête réussit, simplement elle réussit pour tout le
    monde, et elle rend le fichier client complet d'un artisan.
    """
    mine = await _tenant(auth_client, "Chantier à moi")
    theirs = await _tenant(other_client, "Chantier du voisin")

    body = (await auth_client.get("/api/auth/me/export")).json()

    noms = {projet["name"] for projet in body["projets"]}
    assert noms == {"Chantier à moi"}
    assert theirs["project"]["name"] not in noms

    devis = {ligne["id"] for ligne in body["devis"]}
    assert devis == {mine["quote"]["id"]}
    assert theirs["quote"]["id"] not in devis

    organisations = {organisation["id"] for organisation in body["organisations"]}
    assert len(organisations) == 1


async def test_the_export_never_contains_a_secret(auth_client: AsyncClient) -> None:
    """Exporter le hachage du mot de passe reviendrait à mettre le verrou dans l'enveloppe."""
    await auth_client.post("/api/auth/password/forgot", json={"email": "titulaire@exemple.fr"})
    await _tenant(auth_client, "Chantier discret")

    dumped = json.dumps((await auth_client.get("/api/auth/me/export")).json())

    assert "hashed_password" not in dumped
    assert "token_hash" not in dumped
    assert "$argon2" not in dumped


async def test_the_export_needs_a_session(client: AsyncClient) -> None:
    assert (await client.get("/api/auth/me/export")).status_code == 401


async def test_erasure_removes_the_exported_data(
    auth_client: AsyncClient, other_client: AsyncClient
) -> None:
    """Article 17 : ce que l'export rend, l'effacement doit le reprendre.

    Le voisin sert de témoin : sa présence prouve que la suppression a porté sur un compte et non
    sur la base entière — une cascade trop large est le défaut symétrique, et il est pire.
    """
    await _tenant(auth_client, "Chantier effacé")
    temoin = await _tenant(other_client, "Chantier du témoin")

    closed = await auth_client.request(
        "DELETE", "/api/auth/me", json={"current_password": USER_PASSWORD}
    )
    assert closed.status_code == 204, closed.text

    assert (await auth_client.get("/api/auth/me/export")).status_code == 401
    encore = await other_client.get("/api/auth/me/export")
    assert encore.status_code == 200
    assert [projet["name"] for projet in encore.json()["projets"]] == [
        temoin["project"]["name"]
    ]


async def test_the_export_declares_what_it_is(auth_client: AsyncClient) -> None:
    """Un fichier de portabilité doit se lire sans nous : il dit son format et sa raison d'être."""
    body = (await auth_client.get("/api/auth/me/export")).json()

    assert body["format"] == "renovation-plan/export-compte"
    assert body["version"] == 1
    assert "15" in body["notice"] and "20" in body["notice"]
    assert body["generated_at"]
