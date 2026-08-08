"""Cloisonnement entre locataires : « un membre d'une autre organisation reçoit 404 ».

C'est le test que `docs/strategie-produit.md` §6 exige d'écrire **avant** de réécrire les
permissions : passer de `owner_id != user.id` à une résolution d'appartenance est le chemin le
plus court vers une fuite de données entre clients, et une fuite de ce genre ne se voit pas — la
requête réussit, simplement elle réussit pour la mauvaise personne.

Trois garanties sont vérifiées ici, et les deux dernières sont celles qui tiennent dans le temps :

1. sur **chaque** route authentifiée portant un identifiant d'objet, un compte d'une autre
   organisation obtient 404 — et non 403, qui confirmerait l'existence de l'objet ;
2. la liste des routes exercées est confrontée au schéma OpenAPI. Une route ajoutée demain sans
   test de cloisonnement fait échouer `test_every_tenant_scoped_route_is_covered`, au lieu de
   passer inaperçue jusqu'au jour où un client lit le chantier d'un autre ;
3. les routes authentifiées **sans** identifiant d'objet sont classées une par une. Le garde-fou
   (2) ne peut rien en dire — il n'y a aucun identifiant à détourner — alors que c'est par là que
   passe la fuite la plus grave : une liste qui oublie son filtre livre tout le fichier client
   d'un coup, sans qu'un intrus ait eu à deviner quoi que ce soit.

La séparation 404 / 403 est délibérée : hors de l'organisation, on ne révèle rien (404) ; dedans,
un rôle insuffisant est un vrai refus (403), puisque l'intéressé sait déjà que l'objet existe.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app as fastapi_app
from tests.conftest import USER_PASSWORD

CARRE: list[list[float]] = [[0, 0], [400, 0], [400, 300], [0, 300]]
VUE_PARTAGEE: dict[str, Any] = {"camera_preset": "face", "room_index": 0}

# Segments d'URL qui désignent un objet appartenant à un locataire. Une route qui en porte un doit
# apparaître dans `TENANT_ROUTES`. `{slug}` en est volontairement absent : le catalogue de
# mobilier est **global** (spec §4), il n'appartient à personne.
SCOPED_MARKERS = (
    "{project_id}",
    "{room_id}",
    "{face_id}",
    "{element_id}",
    "{shared_view_id}",
    "{organization_id}",
    # Devis, barèmes et lignes de prix : ce sont les objets les plus sensibles du produit — un
    # devis porte l'identité d'un client et le prix auquel l'artisan travaille.
    "{quote_id}",
    "{price_book_id}",
    "{price_item_id}",
)

# (méthode, gabarit de chemin, corps éventuel). Le gabarit est celui d'OpenAPI : c'est ce qui
# permet de confronter cette liste au schéma publié.
TENANT_ROUTES: tuple[tuple[str, str, dict[str, Any] | None], ...] = (
    ("GET", "/api/projects/{project_id}", None),
    ("PATCH", "/api/projects/{project_id}", {"name": "Détourné"}),
    ("DELETE", "/api/projects/{project_id}", None),
    ("POST", "/api/projects/{project_id}/rooms", {"name": "Pièce volée", "polygon": CARRE}),
    ("GET", "/api/projects/{project_id}/scene", None),
    ("POST", "/api/projects/{project_id}/shared-views", {"state": VUE_PARTAGEE}),
    ("GET", "/api/projects/{project_id}/shared-views", None),
    ("POST", "/api/projects/{project_id}/exports/pdf", None),
    ("GET", "/api/projects/{project_id}/exports/pdf/direct", None),
    ("GET", "/api/projects/{project_id}/exports/tasks/{task_id}", None),
    ("GET", "/api/projects/{project_id}/exports/{filename}", None),
    ("GET", "/api/rooms/{room_id}", None),
    ("PATCH", "/api/rooms/{room_id}", {"name": "Renommée par un tiers"}),
    ("DELETE", "/api/rooms/{room_id}", None),
    ("GET", "/api/rooms/{room_id}/faces", None),
    # Mobilier libre (spec §10, amendement A4) : l'ancrage à la pièce ouvre une seconde porte
    # d'écriture sur le plan, et elle doit être cloisonnée comme celle des faces.
    (
        "POST",
        "/api/rooms/{room_id}/elements",
        {"kind": "furniture", "pos_x_cm": 100, "pos_y_cm": 100, "width_cm": 80,
         "depth_cm": 60, "height_cm": 70},
    ),
    # Écriture en lot (spec §10, amendement A6) : c'est la route la plus dense en identifiants
    # fournis par le client, donc celle où une vérification manquante coûterait le plus cher.
    (
        "POST",
        "/api/projects/{project_id}/batch",
        {"operations": [{"op": "create_room", "room": {"name": "Pièce volée", "polygon": CARRE}}]},
    ),
    ("PATCH", "/api/faces/{face_id}", {"covering": {"color": "#123456"}}),
    (
        "POST",
        "/api/faces/{face_id}/elements",
        {"kind": "window", "x_offset_cm": 10, "y_offset_cm": 100, "width_cm": 90,
         "height_cm": 110},
    ),
    ("PATCH", "/api/elements/{element_id}", {"width_cm": 42}),
    ("DELETE", "/api/elements/{element_id}", None),
    ("DELETE", "/api/shared-views/{shared_view_id}", None),
    ("GET", "/api/organizations/{organization_id}", None),
    ("PATCH", "/api/organizations/{organization_id}", {"city": "Ailleurs"}),
    ("GET", "/api/organizations/{organization_id}/members", None),
    ("GET", "/api/organizations/{organization_id}/invitations", None),
    (
        "POST",
        "/api/organizations/{organization_id}/invitations",
        {"email": "complice@exemple.fr", "role": "admin"},
    ),
    (
        "PATCH",
        "/api/organizations/{organization_id}/members/{user_id}",
        {"role": "viewer"},
    ),
    ("DELETE", "/api/organizations/{organization_id}/members/{user_id}", None),
    # --- Métré, barème, devis et facture ---
    ("GET", "/api/projects/{project_id}/takeoff", None),
    ("GET", "/api/projects/{project_id}/takeoff.csv", None),
    ("GET", "/api/projects/{project_id}/costings", None),
    ("GET", "/api/projects/{project_id}/quotes", None),
    ("POST", "/api/projects/{project_id}/quotes", {"client_name": "Client détourné"}),
    ("PUT", "/api/faces/{face_id}/costing", {"price_item_code": "FAIENCE"}),
    ("DELETE", "/api/faces/{face_id}/costing", None),
    ("GET", "/api/organizations/{organization_id}/price-books", None),
    ("POST", "/api/organizations/{organization_id}/price-books", {"name": "Barème détourné"}),
    ("GET", "/api/price-books/{price_book_id}/items", None),
    ("POST", "/api/price-books/{price_book_id}/items", {"code": "VOL", "label": "Détourné"}),
    ("PATCH", "/api/price-items/{price_item_id}", {"unit_price_cents": 1}),
    ("DELETE", "/api/price-items/{price_item_id}", None),
    ("GET", "/api/quotes/{quote_id}", None),
    ("PATCH", "/api/quotes/{quote_id}", {"client_name": "Client détourné"}),
    ("POST", "/api/quotes/{quote_id}/issue", None),
    ("POST", "/api/quotes/{quote_id}/invoice", None),
    ("GET", "/api/quotes/{quote_id}/pdf", None),
    ("GET", "/api/quotes/{quote_id}/invoice.pdf", None),
    ("GET", "/api/quotes/{quote_id}/invoice.xml", None),
)

# Routes authentifiées ne portant **aucun** identifiant d'objet : le garde-fou de complétude ne
# peut pas les couvrir, faute de quelque chose à détourner. Elles sont donc classées à la main,
# et le classement est lui-même confronté au schéma publié — ajouter une route de liste sans se
# poser la question devient impossible.
#
# `TENANT_COLLECTIONS` : renvoient des lignes appartenant à un locataire, donc doivent filtrer sur
# `accessible_organization_ids`. Chacune a son test de non-fuite ci-dessous.
TENANT_COLLECTIONS: tuple[tuple[str, str], ...] = (
    ("GET", "/api/projects"),
    ("GET", "/api/organizations"),
    ("GET", "/api/quotes"),
)

# `GLOBAL_ROUTES` : ne renvoient rien qui appartienne à un locataire. Le catalogue de mobilier est
# **global** (spec §4) ; les autres n'agissent que sur le compte appelant ou créent un objet neuf.
GLOBAL_ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", "/api/auth/me"),
    ("GET", "/api/furniture-types"),
    ("POST", "/api/furniture-types"),
    ("GET", "/api/furniture-types/{slug}"),
    ("PATCH", "/api/furniture-types/{slug}"),
    ("DELETE", "/api/furniture-types/{slug}"),
    ("POST", "/api/invitations/accept"),
    ("POST", "/api/organizations"),
    ("POST", "/api/projects"),
)


@asynccontextmanager
async def logged_in(email: str) -> AsyncIterator[AsyncClient]:
    """Client authentifié sur son **propre** transport.

    Réutiliser l'instance du premier compte écraserait son en-tête d'autorisation, et un test de
    cloisonnement qui interroge l'API avec le jeton de la victime ne prouve rien.
    """
    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        registered = await client.post(
            "/api/auth/register", json={"email": email, "password": USER_PASSWORD}
        )
        assert registered.status_code == 202, registered.text
        tokens = await client.post(
            "/api/auth/token", data={"username": email, "password": USER_PASSWORD}
        )
        assert tokens.status_code == 200, tokens.text
        client.headers["Authorization"] = f"Bearer {tokens.json()['access_token']}"
        yield client


async def build_tenant(client: AsyncClient) -> dict[str, int | str]:
    """Un locataire complet : organisation, projet, pièce, face, élément, lien de partage."""
    project = (await client.post("/api/projects", json={"name": "Chantier privé"})).json()
    room = (
        await client.post(
            f"/api/projects/{project['id']}/rooms", json={"name": "Salon", "polygon": CARRE}
        )
    ).json()
    face = room["faces"][0]
    element = (
        await client.post(
            f"/api/faces/{face['id']}/elements",
            json={"kind": "window", "x_offset_cm": 10, "y_offset_cm": 100,
                  "width_cm": 90, "height_cm": 110},
        )
    ).json()
    shared = (
        await client.post(
            f"/api/projects/{project['id']}/shared-views", json={"state": VUE_PARTAGEE}
        )
    ).json()
    organizations = (await client.get("/api/organizations")).json()
    me = (await client.get("/api/auth/me")).json()

    # Un barème et un devis appartenant à la victime : sans eux, les routes de chiffrage
    # répondraient 404 pour la bonne raison (l'objet n'existe pas) et le test ne prouverait rien.
    book = (
        await client.post(
            f"/api/organizations/{organizations[0]['id']}/price-books",
            json={"name": "Barème privé"},
        )
    ).json()
    item = (await client.get(f"/api/price-books/{book['id']}/items")).json()[0]
    quote = (
        await client.post(
            f"/api/projects/{project['id']}/quotes", json={"client_name": "Client privé"}
        )
    ).json()

    return {
        "project_id": project["id"],
        "room_id": room["id"],
        "face_id": face["id"],
        "element_id": element["id"],
        "shared_view_id": shared["id"],
        "organization_id": organizations[0]["id"],
        "user_id": me["id"],
        "price_book_id": book["id"],
        "price_item_id": item["id"],
        "quote_id": quote["id"],
        # Les deux routes d'export prennent un identifiant libre : elles vérifient la propriété du
        # projet *avant* de le regarder, ce qui est précisément ce qu'on teste.
        "task_id": "tache-inexistante-0001",
        "filename": f"projet-{project['id']}-inexistant.pdf",
    }


@pytest.fixture
async def victime(auth_client: AsyncClient) -> dict[str, int | str]:
    return await build_tenant(auth_client)


@pytest.mark.parametrize(
    ("method", "template", "body"), TENANT_ROUTES, ids=[f"{m} {p}" for m, p, _ in TENANT_ROUTES]
)
async def test_a_member_of_another_organization_gets_404(
    victime: dict[str, int | str],
    other_client: AsyncClient,
    method: str,
    template: str,
    body: dict[str, Any] | None,
) -> None:
    """Le compte intrus a sa propre organisation, et aucun accès à celle de la victime."""
    # L'intrus crée un projet : il a donc bien une organisation à lui, et n'est pas simplement un
    # compte vide pour lequel tout échouerait de toute façon.
    assert (await other_client.post("/api/projects", json={"name": "Chez moi"})).status_code == 201

    response = await other_client.request(
        method, template.format(**victime), json=body
    )
    assert response.status_code == 404, (
        f"{method} {template} répond {response.status_code} au lieu de 404 : {response.text}"
    )


async def test_every_tenant_scoped_route_is_covered(auth_client: AsyncClient) -> None:
    """Garde-fou de complétude : le schéma publié fait foi, pas la mémoire de l'auteur du test.

    Sans cette confrontation, la liste ci-dessus vieillit en silence — et la route ajoutée sans
    contrôle d'appartenance est exactement celle qui ne sera jamais testée.
    """
    schema = (await auth_client.get("/openapi.json")).json()
    publiees = {
        (method.upper(), path)
        for path, operations in schema["paths"].items()
        for method, operation in operations.items()
        if "security" in operation and any(marker in path for marker in SCOPED_MARKERS)
    }
    exercees = {(method, template) for method, template, _ in TENANT_ROUTES}

    assert publiees - exercees == set(), (
        "routes authentifiées portant un objet de locataire et jamais confrontées à un intrus : "
        f"{sorted(publiees - exercees)}"
    )
    assert exercees - publiees == set(), (
        f"routes testées qui n'existent plus dans le schéma : {sorted(exercees - publiees)}"
    )


async def test_every_unscoped_authenticated_route_is_classified(auth_client: AsyncClient) -> None:
    """Second garde-fou : une route de liste échappe entièrement au premier.

    Le test ci-dessus ne regarde que les chemins portant un identifiant. `GET /api/quotes` n'en
    porte aucun, et c'est pourtant la route qui rend d'un seul coup tous les devis — donc tous les
    clients et tous les prix — d'un locataire. Une nouvelle route de ce genre doit être rangée
    dans `TENANT_COLLECTIONS` (et recevoir son test de non-fuite) ou dans `GLOBAL_ROUTES`, jamais
    rester non classée.
    """
    schema = (await auth_client.get("/openapi.json")).json()
    publiees = {
        (method.upper(), path)
        for path, operations in schema["paths"].items()
        for method, operation in operations.items()
        if "security" in operation and not any(marker in path for marker in SCOPED_MARKERS)
    }
    classees = set(TENANT_COLLECTIONS) | set(GLOBAL_ROUTES)

    assert publiees - classees == set(), (
        "routes authentifiées sans identifiant d'objet et jamais classées — une liste sans filtre "
        f"livre tout le locataire d'un coup : {sorted(publiees - classees)}"
    )
    assert classees - publiees == set(), (
        f"routes classées qui n'existent plus dans le schéma : {sorted(classees - publiees)}"
    )


async def test_the_project_list_never_leaks_another_organization(
    auth_client: AsyncClient, other_client: AsyncClient
) -> None:
    await auth_client.post("/api/projects", json={"name": "Chantier A"})
    await other_client.post("/api/projects", json={"name": "Chantier B"})

    mine = (await auth_client.get("/api/projects")).json()
    assert [item["name"] for item in mine["items"]] == ["Chantier A"]
    assert mine["total"] == 1


async def test_the_organization_list_never_leaks_another_tenant(
    auth_client: AsyncClient, other_client: AsyncClient
) -> None:
    await auth_client.post("/api/projects", json={"name": "Chantier A"})
    await other_client.post("/api/projects", json={"name": "Chantier B"})

    mine = (await auth_client.get("/api/organizations")).json()
    theirs = (await other_client.get("/api/organizations")).json()
    assert len(mine) == 1 and len(theirs) == 1
    assert mine[0]["id"] != theirs[0]["id"]


async def test_the_quote_list_never_leaks_another_organization(
    auth_client: AsyncClient, other_client: AsyncClient
) -> None:
    """`GET /api/quotes` ne porte aucun identifiant : rien ne le protège qu'un filtre correct.

    C'est la route la plus dense du produit — elle rend d'un coup l'identité des clients d'un
    artisan et les prix auxquels il travaille. Un filtre oublié ici ne se voit pas : la requête
    réussit, elle réussit simplement pour tout le monde.
    """
    mine = await build_tenant(auth_client)
    theirs = await build_tenant(other_client)

    listed = (await auth_client.get("/api/quotes")).json()
    identifiants = {devis["id"] for devis in listed}

    assert identifiants == {mine["quote_id"]}
    assert theirs["quote_id"] not in identifiants
    assert all(devis["client_name"] == "Client privé" for devis in listed)


async def test_owner_id_no_longer_grants_anything(
    auth_client: AsyncClient, other_client: AsyncClient
) -> None:
    """Le cœur du lot : deux comptes d'une même organisation se voient, `owner_id` ou non.

    Le collègue n'a **jamais** créé ce projet — `owner_id` désigne quelqu'un d'autre — et il doit
    pourtant le lire, le modifier, et le voir dans sa liste. C'est la propriété que l'ancienne
    comparaison `project.owner_id != user.id` rendait impossible.
    """
    project = (await auth_client.post("/api/projects", json={"name": "Chantier partagé"})).json()
    organization_id = (await auth_client.get("/api/organizations")).json()[0]["id"]
    collegue = (await other_client.get("/api/auth/me")).json()

    invitation = await auth_client.post(
        f"/api/organizations/{organization_id}/invitations",
        json={"email": collegue["email"], "role": "editor"},
    )
    assert invitation.status_code == 201, invitation.text
    accepted = await other_client.post(
        "/api/invitations/accept", json={"token": invitation.json()["token"]}
    )
    assert accepted.status_code == 200, accepted.text

    assert (await other_client.get(f"/api/projects/{project['id']}")).status_code == 200
    renamed = await other_client.patch(
        f"/api/projects/{project['id']}", json={"name": "Renommé par le collègue"}
    )
    assert renamed.status_code == 200, renamed.text

    listed = (await other_client.get("/api/projects")).json()
    assert "Renommé par le collègue" in [item["name"] for item in listed["items"]]


async def test_a_pending_invitation_grants_nothing_before_acceptance(
    auth_client: AsyncClient, other_client: AsyncClient
) -> None:
    """Être invité n'est pas être membre.

    Sans le filtre sur `accepted_at`, une invitation envoyée — y compris à la mauvaise adresse —
    ouvrirait le locataire avant même que l'invité ait répondu.
    """
    project = (await auth_client.post("/api/projects", json={"name": "Pas encore partagé"})).json()
    organization_id = (await auth_client.get("/api/organizations")).json()[0]["id"]
    invite = (await other_client.get("/api/auth/me")).json()

    invitation = await auth_client.post(
        f"/api/organizations/{organization_id}/invitations",
        json={"email": invite["email"], "role": "admin"},
    )
    assert invitation.status_code == 201, invitation.text

    assert (await other_client.get(f"/api/projects/{project['id']}")).status_code == 404
    assert (await other_client.get(f"/api/organizations/{organization_id}")).status_code == 404


async def test_a_viewer_reads_but_never_writes(auth_client: AsyncClient) -> None:
    """Un rôle insuffisant donne 403 et non 404 : le lecteur sait déjà que l'objet existe.

    Lui répondre 404 lui ferait croire à une suppression, et transformerait une question de droits
    en incident de données.
    """
    project = (await auth_client.post("/api/projects", json={"name": "En lecture seule"})).json()
    organization_id = (await auth_client.get("/api/organizations")).json()[0]["id"]

    async with logged_in("lecteur@exemple.fr") as lecteur:
        invitation = await auth_client.post(
            f"/api/organizations/{organization_id}/invitations",
            json={"email": "lecteur@exemple.fr", "role": "viewer"},
        )
        accepted = await lecteur.post(
            "/api/invitations/accept", json={"token": invitation.json()["token"]}
        )
        assert accepted.status_code == 200, accepted.text

        assert (await lecteur.get(f"/api/projects/{project['id']}")).status_code == 200
        assert (await lecteur.get(f"/api/projects/{project['id']}/scene")).status_code == 200

        refused = await lecteur.patch(
            f"/api/projects/{project['id']}", json={"name": "Modifié par un lecteur"}
        )
        assert refused.status_code == 403, refused.text
        creation = await lecteur.post(
            f"/api/projects/{project['id']}/rooms", json={"name": "Salon", "polygon": CARRE}
        )
        assert creation.status_code == 403, creation.text


async def test_an_editor_writes_the_plan_but_does_not_delete_the_project(
    auth_client: AsyncClient,
) -> None:
    """La suppression d'un chantier entier demande `admin`.

    C'est la seule écriture irréversible du plan : un `editor` pose et retire des meubles, il
    n'efface pas le dossier du client.
    """
    project = (await auth_client.post("/api/projects", json={"name": "Chantier"})).json()
    organization_id = (await auth_client.get("/api/organizations")).json()[0]["id"]

    async with logged_in("editeur@exemple.fr") as editeur:
        invitation = await auth_client.post(
            f"/api/organizations/{organization_id}/invitations",
            json={"email": "editeur@exemple.fr", "role": "editor"},
        )
        await editeur.post(
            "/api/invitations/accept", json={"token": invitation.json()["token"]}
        )

        created = await editeur.post(
            f"/api/projects/{project['id']}/rooms", json={"name": "Cuisine", "polygon": CARRE}
        )
        assert created.status_code == 201, created.text

        refused = await editeur.delete(f"/api/projects/{project['id']}")
        assert refused.status_code == 403, refused.text
