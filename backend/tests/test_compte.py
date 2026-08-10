"""Cycle de vie d'un compte : mot de passe, révocation, fermeture, projet de démonstration.

Ce que la suite vérifiait jusqu'ici s'arrêtait à la porte d'entrée : s'inscrire, se connecter,
rafraîchir. Tout ce qui vient après — oublier son mot de passe, en changer, partir — n'existait
pas, et un mot de passe oublié signifiait le compte et les chantiers perdus définitivement.

Deux propriétés portent l'essentiel de ce fichier et ne doivent jamais régresser :

1. **La révocation est globale.** Changer ou réinitialiser un mot de passe ferme *toutes* les
   sessions. Un JWT reste valide tant qu'il n'a pas expiré : sans le compteur `token_version`,
   reprendre la main sur un compte compromis serait une illusion d'interface.
2. **Rien ne dit si une adresse est inscrite.** L'inscription répond déjà 202 quoi qu'il arrive ;
   la demande de réinitialisation doit répondre 202 de la même façon, sinon la seconde porte
   annule la première.
"""

from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from app.main import app as fastapi_app
from app.models.base import ElementKind, utcnow
from app.models.organization import Membership, OrganizationRole
from app.models.plan import Element, Face, Project, Room
from app.models.user import User, UserToken
from app.services import demo as demo_service
from app.services.faces import element_fits_in_room, element_fits_on_face
from app.services.seed import seed_catalog
from app.services.seed_plans import PLAN_BUSINESS
from tests.conftest import USER_PASSWORD, subscribe

NOUVEAU_MOT_DE_PASSE = "un-nouveau-mot-de-passe-2026"


async def _token_of(client: AsyncClient, email: str, password: str) -> str:
    response = await client.post(
        "/api/auth/token", data={"username": email, "password": password}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


async def _forgot(client: AsyncClient, email: str) -> str | None:
    response = await client.post("/api/auth/password/forgot", json={"email": email})
    assert response.status_code == 202, response.text
    token = response.json()["reset_token"]
    return str(token) if token is not None else None


# --- Changement de mot de passe ----------------------------------------------------------------


async def test_changing_the_password_needs_the_current_one(auth_client: AsyncClient) -> None:
    """Un jeton d'accès volé ne doit pas suffire à verrouiller le titulaire hors de son compte."""
    refused = await auth_client.patch(
        "/api/auth/password",
        json={"current_password": "ce-n-est-pas-le-bon", "new_password": NOUVEAU_MOT_DE_PASSE},
    )
    assert refused.status_code == 403, refused.text

    # Et l'ancien mot de passe fonctionne toujours : rien n'a été écrit au passage.
    assert await _token_of(auth_client, "titulaire@exemple.fr", USER_PASSWORD)


async def test_changing_the_password_closes_every_other_session(
    client: AsyncClient, auth_client: AsyncClient
) -> None:
    """La propriété la plus importante du lot, et celle qu'aucune interface ne peut simuler.

    Le jeton détenu par une *autre* session est révoqué par le changement. Sans le compteur
    `token_version`, il resterait valide jusqu'à son expiration naturelle — et « j'ai changé mon
    mot de passe » ne mettrait personne dehors.
    """
    vole = await _token_of(auth_client, "titulaire@exemple.fr", USER_PASSWORD)

    changed = await auth_client.patch(
        "/api/auth/password",
        json={"current_password": USER_PASSWORD, "new_password": NOUVEAU_MOT_DE_PASSE},
    )
    assert changed.status_code == 200, changed.text

    encore_valide = await client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {vole}"}
    )
    assert encore_valide.status_code == 401, encore_valide.text

    # La session qui a demandé le changement, elle, continue : la réponse porte une paire fraîche.
    frais = changed.json()["access_token"]
    toujours_la = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {frais}"})
    assert toujours_la.status_code == 200, toujours_la.text


async def test_the_new_password_is_the_only_one_that_works(auth_client: AsyncClient) -> None:
    changed = await auth_client.patch(
        "/api/auth/password",
        json={"current_password": USER_PASSWORD, "new_password": NOUVEAU_MOT_DE_PASSE},
    )
    assert changed.status_code == 200, changed.text

    ancien = await auth_client.post(
        "/api/auth/token",
        data={"username": "titulaire@exemple.fr", "password": USER_PASSWORD},
    )
    assert ancien.status_code == 401, ancien.text
    assert await _token_of(auth_client, "titulaire@exemple.fr", NOUVEAU_MOT_DE_PASSE)


async def test_a_short_new_password_is_refused(auth_client: AsyncClient) -> None:
    """La borne NIST vaut aussi ici : elle était déclarée sur l'inscription seulement."""
    refused = await auth_client.patch(
        "/api/auth/password",
        json={"current_password": USER_PASSWORD, "new_password": "court"},
    )
    assert refused.status_code == 422, refused.text


async def test_a_refreshed_token_carries_the_revocation_counter(
    client: AsyncClient, auth_client: AsyncClient
) -> None:
    """Le rafraîchissement ne doit pas être une porte dérobée sur la révocation.

    Un jeton de rafraîchissement volé rendrait sinon des jetons d'accès frais pendant des jours,
    et changer son mot de passe n'aurait fermé que la porte d'entrée.
    """
    connexion = await client.post(
        "/api/auth/token",
        data={"username": "titulaire@exemple.fr", "password": USER_PASSWORD},
    )
    ancien_refresh = connexion.json()["refresh_token"]
    assert ancien_refresh, "le repli de développement doit rendre le jeton de rafraîchissement"

    changed = await auth_client.patch(
        "/api/auth/password",
        json={"current_password": USER_PASSWORD, "new_password": NOUVEAU_MOT_DE_PASSE},
    )
    assert changed.status_code == 200, changed.text

    refused = await client.post(
        "/api/auth/refresh", json={"refresh_token": ancien_refresh}
    )
    assert refused.status_code == 401, refused.text


# --- Mot de passe oublié -----------------------------------------------------------------------


async def test_forgot_answers_the_same_thing_for_an_unknown_address(
    client: AsyncClient, auth_client: AsyncClient
) -> None:
    """Anti-énumération : le statut *et* le message sont identiques dans les deux cas.

    L'inscription est déjà protégée. Une réponse qui distinguerait ici « adresse connue » de
    « adresse inconnue » rendrait cette protection décorative.
    """
    connue = await client.post(
        "/api/auth/password/forgot", json={"email": "titulaire@exemple.fr"}
    )
    inconnue = await client.post(
        "/api/auth/password/forgot", json={"email": "personne@exemple.fr"}
    )

    assert connue.status_code == inconnue.status_code == 202
    assert connue.json()["detail"] == inconnue.json()["detail"]


async def test_a_reset_link_replaces_the_password_and_closes_the_sessions(
    client: AsyncClient, auth_client: AsyncClient
) -> None:
    ouverte = await _token_of(auth_client, "titulaire@exemple.fr", USER_PASSWORD)
    token = await _forgot(client, "titulaire@exemple.fr")
    assert token is not None

    done = await client.post(
        "/api/auth/password/reset",
        json={"token": token, "new_password": NOUVEAU_MOT_DE_PASSE},
    )
    assert done.status_code == 200, done.text

    assert await _token_of(client, "titulaire@exemple.fr", NOUVEAU_MOT_DE_PASSE)
    fermee = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {ouverte}"})
    assert fermee.status_code == 401, fermee.text


async def test_a_reset_link_only_works_once(
    client: AsyncClient, auth_client: AsyncClient
) -> None:
    """Le rejeu est ce qui transforme un lien intercepté en accès permanent."""
    token = await _forgot(client, "titulaire@exemple.fr")
    assert token is not None

    premier = await client.post(
        "/api/auth/password/reset",
        json={"token": token, "new_password": NOUVEAU_MOT_DE_PASSE},
    )
    assert premier.status_code == 200, premier.text

    second = await client.post(
        "/api/auth/password/reset",
        json={"token": token, "new_password": "encore-un-autre-mot-de-passe"},
    )
    assert second.status_code == 400, second.text


async def test_asking_again_invalidates_the_previous_link(
    client: AsyncClient, auth_client: AsyncClient
) -> None:
    """Demander un nouveau lien est ce qu'on fait quand on a un doute sur le précédent."""
    premier = await _forgot(client, "titulaire@exemple.fr")
    second = await _forgot(client, "titulaire@exemple.fr")
    assert premier is not None and second is not None and premier != second

    perime = await client.post(
        "/api/auth/password/reset",
        json={"token": premier, "new_password": NOUVEAU_MOT_DE_PASSE},
    )
    assert perime.status_code == 400, perime.text

    valide = await client.post(
        "/api/auth/password/reset",
        json={"token": second, "new_password": NOUVEAU_MOT_DE_PASSE},
    )
    assert valide.status_code == 200, valide.text


async def test_an_expired_link_is_refused(
    client: AsyncClient, auth_client: AsyncClient, session: AsyncSession
) -> None:
    token = await _forgot(client, "titulaire@exemple.fr")
    assert token is not None

    row = (await session.execute(select(UserToken))).scalar_one()
    row.expires_at = utcnow() - timedelta(minutes=1)
    await session.commit()

    refused = await client.post(
        "/api/auth/password/reset",
        json={"token": token, "new_password": NOUVEAU_MOT_DE_PASSE},
    )
    assert refused.status_code == 400, refused.text


async def test_the_reset_token_is_never_stored_in_clear(
    client: AsyncClient, auth_client: AsyncClient, session: AsyncSession
) -> None:
    """Une copie de la base ne doit pas permettre de prendre la main sur les comptes qu'elle lit."""
    token = await _forgot(client, "titulaire@exemple.fr")
    assert token is not None

    row = (await session.execute(select(UserToken))).scalar_one()
    assert row.token_hash != token
    assert token not in row.token_hash
    assert len(row.token_hash) == 64


async def test_an_unknown_reset_token_is_refused(client: AsyncClient) -> None:
    refused = await client.post(
        "/api/auth/password/reset",
        json={"token": "jeton-invente-de-toutes-pieces", "new_password": NOUVEAU_MOT_DE_PASSE},
    )
    assert refused.status_code == 400, refused.text


# --- Fermeture du compte -----------------------------------------------------------------------


async def test_closing_an_account_needs_the_password(auth_client: AsyncClient) -> None:
    refused = await auth_client.request(
        "DELETE", "/api/auth/me", json={"current_password": "ce-n-est-pas-le-bon"}
    )
    assert refused.status_code == 403, refused.text
    assert (await auth_client.get("/api/auth/me")).status_code == 200


async def test_closing_an_account_removes_it_and_its_projects(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """Ce qui emporte le chantier est la suppression de l'organisation, jamais celle du créateur.

    La nuance est tout l'amendement A13 : `project.owner_id` est en `SET NULL` et ne détruit plus
    rien, le compte étant ici le seul membre de son organisation, c'est elle qui part — et la
    cascade d'`organization_id` avec elle.
    """
    created = await auth_client.post("/api/projects", json={"name": "Chantier à effacer"})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]

    closed = await auth_client.request(
        "DELETE", "/api/auth/me", json={"current_password": USER_PASSWORD}
    )
    assert closed.status_code == 204, closed.text

    session.expire_all()
    reste = (
        await session.execute(select(User).where(col(User.email) == "titulaire@exemple.fr"))
    ).scalar_one_or_none()
    assert reste is None
    survivant = (
        await session.execute(select(Project).where(col(Project.id) == project_id))
    ).scalar_one_or_none()
    assert survivant is None
    assert (await auth_client.get("/api/auth/me")).status_code == 401


async def test_the_last_owner_of_an_inhabited_organization_cannot_leave(
    auth_client: AsyncClient, other_client: AsyncClient, session: AsyncSession
) -> None:
    """Le refus est de **gouvernance** depuis l'amendement A13, et il ne prétend plus autre chose.

    Il protégeait censément les chantiers des collègues, ce qu'il ne faisait pas : la destruction
    venait du `ON DELETE CASCADE` de `project.owner_id`, qui frappait aussi un simple `editor` —
    cas que ce garde-fou ne regarde même pas, puisqu'il filtre sur le rôle `owner`. La cascade est
    devenue `SET NULL`. Ce qui reste ici est le seul vrai motif : une entreprise sans propriétaire
    accepté n'a plus personne pour inviter, payer ni la fermer.
    """
    await auth_client.post("/api/projects", json={"name": "Chantier partagé"})
    organization_id = (await auth_client.get("/api/organizations")).json()[0]["id"]
    # Une entreprise « habitée » a plusieurs membres, donc paie ses sièges (A14).
    await subscribe(session, int(organization_id), PLAN_BUSINESS)
    collegue = (await other_client.get("/api/auth/me")).json()

    # L'appartenance est posée directement, comme le fait `conftest.personal_organization` : ce
    # qui est mis à l'épreuve ici est le garde-fou de la **fermeture**, et passer par le parcours
    # d'invitation ferait rougir ce test au premier changement de mur de paiement des sièges.
    maintenant = utcnow()
    session.add(
        Membership(
            user_id=collegue["id"],
            organization_id=organization_id,
            role=OrganizationRole.EDITOR,
            invited_at=maintenant,
            accepted_at=maintenant,
        )
    )
    await session.commit()

    refused = await auth_client.request(
        "DELETE", "/api/auth/me", json={"current_password": USER_PASSWORD}
    )
    assert refused.status_code == 409, refused.text
    assert "propriétaire" in refused.json()["detail"]

    # Le collègue, lui, n'est pas propriétaire : il peut partir sans rien casser.
    parti = await other_client.request(
        "DELETE", "/api/auth/me", json={"current_password": USER_PASSWORD}
    )
    assert parti.status_code == 204, parti.text


async def test_closing_a_solo_account_takes_its_organization_with_it(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """Une organisation sans membre n'est pas une entreprise, c'est un résidu.

    La cascade de `membership` la laisserait en place : invisible de l'API, impossible à effacer
    autrement qu'en base, et portant encore des devis et l'identité d'un client (RGPD art. 17).
    """
    from app.models.organization import Organization

    await auth_client.post("/api/projects", json={"name": "Chantier solo"})
    organization_id = (await auth_client.get("/api/organizations")).json()[0]["id"]

    closed = await auth_client.request(
        "DELETE", "/api/auth/me", json={"current_password": USER_PASSWORD}
    )
    assert closed.status_code == 204, closed.text

    reste = (
        await session.execute(
            select(Organization).where(col(Organization.id) == organization_id)
        )
    ).scalar_one_or_none()
    assert reste is None


# --- Projet de démonstration --------------------------------------------------------------------


async def test_the_demo_project_is_created_once(auth_client: AsyncClient) -> None:
    created = await auth_client.post("/api/auth/demo-project")
    assert created.status_code == 201, created.text
    assert created.json()["name"] == demo_service.DEMO_PROJECT_NAME

    encore = await auth_client.post("/api/auth/demo-project")
    assert encore.status_code == 409, encore.text


async def test_a_deleted_demo_project_does_not_come_back(auth_client: AsyncClient) -> None:
    """Un objet qu'on ne peut pas jeter est plus irritant qu'un état vide."""
    project_id = (await auth_client.post("/api/auth/demo-project")).json()["project_id"]
    assert (await auth_client.delete(f"/api/projects/{project_id}")).status_code == 204

    # L'espace est de nouveau vierge : la route accepte, et c'est la seule fois où c'est voulu.
    assert (await auth_client.post("/api/auth/demo-project")).status_code == 201


async def test_the_demo_project_is_never_created_next_to_an_existing_one(
    auth_client: AsyncClient,
) -> None:
    assert (
        await auth_client.post("/api/projects", json={"name": "Chantier réel"})
    ).status_code == 201
    refused = await auth_client.post("/api/auth/demo-project")
    assert refused.status_code == 409, refused.text


async def test_the_demo_project_is_a_complete_readable_plan(auth_client: AsyncClient) -> None:
    """Il ne suffit pas que la démonstration existe : elle doit se lire et se rendre en 3D."""
    project_id = (await auth_client.post("/api/auth/demo-project")).json()["project_id"]

    project = (await auth_client.get(f"/api/projects/{project_id}")).json()
    assert len(project["rooms"]) == 1
    room = project["rooms"][0]
    assert room["name"] == demo_service.DEMO_ROOM_NAME
    # Quatre murs, un sol, un plafond.
    assert len(room["faces"]) == 6

    posed = sum(len(face["elements"]) for face in room["faces"])
    assert posed == len(demo_service.DEMO_FACE_ELEMENTS)
    assert len(room["free_elements"]) == len(demo_service.DEMO_ROOM_ELEMENTS)

    scene = await auth_client.get(f"/api/projects/{project_id}/scene")
    assert scene.status_code == 200, scene.text


async def test_the_demo_project_is_actually_priceable(auth_client: AsyncClient) -> None:
    """« Entièrement chiffrable » veut dire : un devis chiffré sort sans un seul réglage.

    C'est toute la raison d'être de la démonstration. Un plan de démonstration qui rendrait un
    devis vide montrerait précisément l'inverse de ce qu'on veut montrer.
    """
    project_id = (await auth_client.post("/api/auth/demo-project")).json()["project_id"]

    takeoff = await auth_client.get(f"/api/projects/{project_id}/takeoff")
    assert takeoff.status_code == 200, takeoff.text

    quote = await auth_client.post(
        f"/api/projects/{project_id}/quotes", json={"client_name": "Client de démonstration"}
    )
    assert quote.status_code == 201, quote.text
    body = quote.json()
    assert body["lines"], "aucune ligne : le rattachement automatique matière → barème a cassé"
    assert body["total_ht_cents"] > 0


async def test_the_demo_furniture_uses_the_catalogue_when_it_is_seeded(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """Les slugs cités par la démonstration doivent exister dans le catalogue de référence.

    Sans cette confrontation, une recette renommée dans `app/services/catalog.py` laisserait la
    démonstration poser des boîtes nues, et personne ne le verrait avant une capture d'écran.
    """
    await seed_catalog(session)
    await session.commit()

    project_id = (await auth_client.post("/api/auth/demo-project")).json()["project_id"]
    project = (await auth_client.get(f"/api/projects/{project_id}")).json()

    meubles = [
        element
        for face in project["rooms"][0]["faces"]
        for element in face["elements"]
        if element["kind"] == "furniture"
    ] + project["rooms"][0]["free_elements"]

    assert meubles
    assert all(element["furniture_type_id"] is not None for element in meubles), (
        "un slug de `services/demo.py` n'existe plus dans `services/catalog.py`"
    )


@pytest.mark.parametrize(
    ("label", "kind", "slug", "width", "height", "depth", "offset_x", "offset_y"),
    demo_service.DEMO_FACE_ELEMENTS,
    ids=[f"{label}-{slug}" for label, _, slug, *_ in demo_service.DEMO_FACE_ELEMENTS],
)
def test_every_demo_element_fits_on_its_wall(
    label: str,
    kind: ElementKind,
    slug: str,
    width: float,
    height: float,
    depth: float,
    offset_x: float,
    offset_y: float,
) -> None:
    """La géométrie de la démonstration est une constante : elle se vérifie une fois, hors base.

    Les mêmes fonctions d'encombrement que l'API, appelées directement. Un décalage saisi de
    travers dans `services/demo.py` ferait sinon échouer l'accueil de tous les nouveaux comptes,
    et le message serait un 500 au lieu d'un test rouge.
    """
    room = Room(
        name=demo_service.DEMO_ROOM_NAME,
        polygon=[list(vertex) for vertex in demo_service.DEMO_POLYGON],
        wall_thickness_cm=demo_service.DEMO_WALL_THICKNESS_CM,
        ceiling_height_cm=demo_service.DEMO_CEILING_HEIGHT_CM,
    )
    index = "ABCD".index(label)
    start = demo_service.DEMO_POLYGON[index]
    end = demo_service.DEMO_POLYGON[(index + 1) % len(demo_service.DEMO_POLYGON)]
    face = Face(
        label=label,
        start_x_cm=start[0],
        start_y_cm=start[1],
        end_x_cm=end[0],
        end_y_cm=end[1],
    )
    element = Element(
        kind=kind,
        width_cm=width,
        height_cm=height,
        depth_cm=depth,
        x_offset_cm=offset_x,
        y_offset_cm=offset_y,
    )

    assert element_fits_on_face(element, face, room) is None


@pytest.mark.parametrize(
    ("slug", "width", "height", "depth", "pos_x", "pos_y"),
    demo_service.DEMO_ROOM_ELEMENTS,
    ids=[slug for slug, *_ in demo_service.DEMO_ROOM_ELEMENTS],
)
def test_every_free_demo_element_fits_in_the_room(
    slug: str, width: float, height: float, depth: float, pos_x: float, pos_y: float
) -> None:
    room = Room(
        name=demo_service.DEMO_ROOM_NAME,
        polygon=[list(vertex) for vertex in demo_service.DEMO_POLYGON],
        wall_thickness_cm=demo_service.DEMO_WALL_THICKNESS_CM,
        ceiling_height_cm=demo_service.DEMO_CEILING_HEIGHT_CM,
    )
    element = Element(
        width_cm=width,
        height_cm=height,
        depth_cm=depth,
        pos_x_cm=pos_x,
        pos_y_cm=pos_y,
    )

    assert element_fits_in_room(element, room) is None


async def test_the_demo_project_belongs_to_its_creator_only(
    auth_client: AsyncClient, other_client: AsyncClient
) -> None:
    """Le cloisonnement s'applique à la démonstration comme au reste : elle porte un chantier."""
    project_id = (await auth_client.post("/api/auth/demo-project")).json()["project_id"]
    assert (await other_client.get(f"/api/projects/{project_id}")).status_code == 404


async def test_signing_up_leaves_the_space_empty(client: AsyncClient) -> None:
    """L'inscription ne sème rien : c'est l'accueil qui décide, et il ne le fait qu'une fois.

    Semer dans `register` ferait construire une salle de bain complète à chaque compte créé, y
    compris ceux qu'aucun humain n'ouvrira jamais — et rendrait la route d'inscription dépendante
    du catalogue de mobilier.
    """
    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as isole:
        registered = await isole.post(
            "/api/auth/register",
            json={"email": "nouveau@exemple.fr", "password": USER_PASSWORD},
        )
        assert registered.status_code == 202, registered.text
        tokens = await isole.post(
            "/api/auth/token",
            data={"username": "nouveau@exemple.fr", "password": USER_PASSWORD},
        )
        isole.headers["Authorization"] = f"Bearer {tokens.json()['access_token']}"

        listed = await isole.get("/api/projects")
        assert listed.json()["total"] == 0
