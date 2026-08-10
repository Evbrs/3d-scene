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
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from app.models.base import utcnow
from app.models.billing import Quote
from app.models.organization import Membership, OrganizationRole
from app.models.user import User
from app.services.seed_plans import LIMIT_SHARE_LINK_DAYS, PLAN_DISCOVERY, PLAN_GRID
from tests.conftest import USER_PASSWORD

# Lue dans la grille et non recopiée : la propriété vérifiée est « le lien vit exactement ce que le
# palier déclare », pas « il vit trente jours ». Déplacer la limite dans `seed_plans.py` doit
# déplacer le test avec elle, jamais le faire mentir.
DISCOVERY_SHARE_LINK_DAYS: int = next(
    plan["limits"][LIMIT_SHARE_LINK_DAYS]
    for plan in PLAN_GRID
    if plan["code"] == PLAN_DISCOVERY
)

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


# --- L'effacement de l'un ne détruit pas les données des autres (A13) ---------------------------


async def test_closing_an_account_leaves_the_companys_worksites_untouched(
    auth_client: AsyncClient, other_client: AsyncClient, session: AsyncSession
) -> None:
    """Le collègue part, ses chantiers restent : ils appartiennent à l'entreprise, pas à lui.

    Le scénario est celui du terrain : deux personnes de la même société, chacune ouvre des
    chantiers, l'une s'en va. `owner_id` n'est qu'une trace de création (A1) — il ne doit rien
    emporter. Le compte partant est **second** propriétaire, donc le garde-fou historique ne se
    déclenche pas et la fermeture aboutit : c'est précisément le cas où la cascade détruisait tout.
    """
    await auth_client.post("/api/projects", json={"name": "Chantier d'Alice"})
    organization_id = (await auth_client.get("/api/organizations")).json()[0]["id"]
    collegue = (await other_client.get("/api/auth/me")).json()

    # L'appartenance est posée directement en base, comme le fait `conftest.personal_organization`
    # pour les projets : ce qui est mis à l'épreuve ici est la **fermeture** du compte, et faire
    # dépendre ce test du parcours d'invitation — qui a son propre fichier et son propre mur de
    # paiement — le ferait rougir pour une raison qui ne le regarde pas.
    maintenant = utcnow()
    session.add(
        Membership(
            user_id=collegue["id"],
            organization_id=organization_id,
            role=OrganizationRole.OWNER,
            invited_at=maintenant,
            accepted_at=maintenant,
        )
    )
    await session.commit()

    for numero in (1, 2, 3):
        cree = await other_client.post(
            "/api/projects", json={"name": f"Chantier du collègue {numero}"}
        )
        assert cree.status_code == 201, cree.text

    avant = (await auth_client.get("/api/projects")).json()
    assert avant["total"] == 4

    parti = await other_client.request(
        "DELETE", "/api/auth/me", json={"current_password": USER_PASSWORD}
    )
    assert parti.status_code == 204, parti.text

    apres = (await auth_client.get("/api/projects")).json()
    assert apres["total"] == 4, "des chantiers de l'entreprise sont partis avec le compte"

    # Et ils restent réellement modifiables : survivre en lecture seule ne serait pas survivre.
    sien = next(
        projet for projet in apres["items"] if projet["name"] == "Chantier du collègue 1"
    )
    renomme = await auth_client.patch(
        f"/api/projects/{sien['id']}",
        json={"name": "Chantier repris", "version": sien["version"]},
    )
    assert renomme.status_code == 200, renomme.text


async def test_closing_an_account_never_destroys_an_issued_invoice(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """L'obligation comptable de dix ans prime sur l'effacement, et elle n'est pas négociable.

    L'artisan qui ferme son compte emportait ses propres factures **émises** : l'organisation dont
    il était seul membre était supprimée, et `quote.organization_id` porte un `ON DELETE CASCADE`.
    C'est lui qui aurait été redressé, pas nous.
    """
    project = (await auth_client.post("/api/projects", json={"name": "Chantier facturé"})).json()
    quote = (
        await auth_client.post(
            f"/api/projects/{project['id']}/quotes", json={"client_name": "Madame Durand"}
        )
    ).json()

    emis = await auth_client.post(f"/api/quotes/{quote['id']}/issue")
    assert emis.status_code == 200, emis.text
    await auth_client.patch(f"/api/quotes/{quote['id']}", json={"status": "accepted"})
    facture = await auth_client.post(f"/api/quotes/{quote['id']}/invoice")
    assert facture.status_code == 200, facture.text
    numero = facture.json()["invoice_number"]

    closed = await auth_client.request(
        "DELETE", "/api/auth/me", json={"current_password": USER_PASSWORD}
    )
    assert closed.status_code == 204, closed.text

    restantes = list(
        (
            await session.execute(
                select(Quote).where(col(Quote.invoice_number) == numero)
            )
        )
        .scalars()
        .all()
    )
    assert len(restantes) == 1, "la facture émise a été détruite avec le compte"


async def test_a_pseudonymized_account_keeps_no_personal_data_and_no_way_back(
    auth_client: AsyncClient, client: AsyncClient, session: AsyncSession
) -> None:
    """Conserver la facture ne doit rien conserver de la personne.

    C'est la moitié du compromis de A13, et c'est celle qu'on peut rater sans que rien ne casse :
    une organisation gardée « pour la comptabilité » avec un compte intact serait un effacement
    qui n'efface rien.
    """
    project = (await auth_client.post("/api/projects", json={"name": "Chantier facturé"})).json()
    quote = (
        await auth_client.post(
            f"/api/projects/{project['id']}/quotes", json={"client_name": "Madame Durand"}
        )
    ).json()
    await auth_client.post(f"/api/quotes/{quote['id']}/issue")
    await auth_client.patch(f"/api/quotes/{quote['id']}", json={"status": "accepted"})
    assert (await auth_client.post(f"/api/quotes/{quote['id']}/invoice")).status_code == 200

    closed = await auth_client.request(
        "DELETE", "/api/auth/me", json={"current_password": USER_PASSWORD}
    )
    assert closed.status_code == 204, closed.text

    session.expire_all()
    reste = (
        await session.execute(select(User).where(col(User.email) == "titulaire@exemple.fr"))
    ).scalar_one_or_none()
    assert reste is None, "l'adresse e-mail du compte fermé est toujours en base"

    # La session en cours est fermée, et le mot de passe ne rouvre plus rien.
    assert (await auth_client.get("/api/auth/me")).status_code == 401
    refuse = await client.post(
        "/api/auth/token",
        data={"username": "titulaire@exemple.fr", "password": USER_PASSWORD},
    )
    assert refuse.status_code == 401, refuse.text


# --- Rétention des liens de partage (A13) -------------------------------------------------------


def _as_utc(moment: datetime) -> datetime:
    """SQLite ne stocke aucun fuseau : la date relue revient naïve, et la comparaison lèverait."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


async def _shared_link(auth_client: AsyncClient, **extra: Any) -> dict[str, Any]:
    project = (await auth_client.post("/api/projects", json={"name": "Chantier partagé"})).json()
    created = await auth_client.post(
        f"/api/projects/{project['id']}/shared-views",
        json={"state": {"camera_preset": "face", "room_index": 0}, **extra},
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


async def test_a_share_link_is_never_created_without_an_expiry(
    auth_client: AsyncClient,
) -> None:
    """`docs/rgpd.md` annonce « jusqu'à révocation ou échéance ». Sans échéance, il n'y a rien.

    Le chemin par défaut — celui que le frontend emprunte, `expires_in_days` valant `null` —
    fabriquait un lien **permanent** sur la géométrie d'un logement. Une durée de conservation
    annoncée et jamais appliquée n'est pas une politique, c'est une phrase.
    """
    lien = await _shared_link(auth_client)

    assert lien["expires_at"] is not None, "lien public sans aucune échéance"
    reste = _as_utc(datetime.fromisoformat(lien["expires_at"])) - utcnow()
    assert reste <= timedelta(days=DISCOVERY_SHARE_LINK_DAYS)


async def test_a_share_link_never_outlives_the_tier_it_was_created_on(
    auth_client: AsyncClient,
) -> None:
    """La durée demandée est rabotée par le palier, sinon la limite affichée ne veut rien dire."""
    lien = await _shared_link(auth_client, expires_in_days=365)

    reste = _as_utc(datetime.fromisoformat(lien["expires_at"])) - utcnow()
    assert reste <= timedelta(days=DISCOVERY_SHARE_LINK_DAYS)
