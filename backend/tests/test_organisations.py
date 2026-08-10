"""Organisations, appartenances, rôles et invitations.

`docs/strategie-produit.md` §6, points 1 et 2. Deux familles de garanties y sont vérifiées :

- **le contenu légal** — SIRET, forme juridique, capital, RCS, adresse, TVA et surtout l'assurance
  décennale (assureur, police, couverture) : sans ces mentions, un devis de bâtiment est
  inopposable, et les stocker plus tard coûterait une migration sur une table chaude ;
- **la gouvernance** — on ne délègue pas au-dessus de son propre rôle, on ne fait pas disparaître
  le dernier propriétaire, et un jeton d'invitation ne sert qu'une fois, à la bonne adresse.

Le cloisonnement entre locataires est testé à part, dans `test_permissions_locataire.py`.
"""

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from app.api.organizations import hash_invitation_token, slugify
from app.models.base import utcnow
from app.models.organization import Invitation, Membership, Organization, OrganizationRole
from app.services.seed_plans import PLAN_BUSINESS
from tests.conftest import subscribe
from tests.test_permissions_locataire import logged_in

# Jeu complet de mentions légales : c'est exactement ce qu'un devis d'artisan doit porter.
IDENTITE_COMPLETE = {
    "siret": "12345678901234",
    "legal_form": "SASU",
    # 10 000,00 € — en **centimes entiers**, jamais en flottant.
    "share_capital_cents": 1_000_000,
    "rcs": "RCS Versailles B 123 456 789",
    "address_line1": "12 rue des Lilas",
    "address_line2": "Bâtiment C",
    "postal_code": "78000",
    "city": "Versailles",
    "country": "France",
    "vat_number": "FR12345678901",
    "decennial_insurer": "AXA France IARD",
    "decennial_policy_number": "POL-2026-004417",
    "decennial_coverage_area": "France métropolitaine",
    "billing_email": "compta@exemple.fr",
    "phone": "+33 1 23 45 67 89",
    "logo_url": "https://exemple.fr/logo.png",
}


async def _first_organization(client: AsyncClient) -> dict[str, object]:
    """Organisation personnelle du compte, créée paresseusement au premier projet."""
    await client.post("/api/projects", json={"name": "Premier chantier"})
    organizations = (await client.get("/api/organizations")).json()
    assert len(organizations) == 1
    return dict(organizations[0])


async def _organization_that_pays_for_seats(
    client: AsyncClient, session: AsyncSession
) -> dict[str, object]:
    """Organisation personnelle du compte, abonnée au palier qui ouvre le travail à plusieurs.

    Inviter est payant depuis l'amendement A14 : c'est la ligne qui distingue le palier Entreprise,
    et l'essai — palier Artisan, un siège — ne l'ouvre pas. Les tests ci-dessous portent sur la
    gouvernance et sur les rôles, jamais sur la grille tarifaire ; sans cet abonnement, ils
    mesureraient le mur de paiement au lieu de ce qu'ils cherchent. Le mur lui-même est vérifié
    dans `test_offres.py`.
    """
    organization = await _first_organization(client)
    identifier = organization["id"]
    assert isinstance(identifier, int)
    await subscribe(session, identifier, PLAN_BUSINESS)
    return organization


# --- Slug -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("nom", "attendu"),
    [
        ("Plomberie Martin", "plomberie-martin"),
        ("  ÉLECTRICITÉ  &  Cie  ", "lectricit-cie"),
        ("---", "espace"),
        ("建築", "espace"),
    ],
)
def test_the_slug_is_lowercase_and_never_empty(nom: str, attendu: str) -> None:
    """Le slug apparaît dans des URL : il ne peut être ni vide, ni porteur de séparateurs.

    Un nom sans aucun caractère latin retombe sur un repli plutôt que sur une chaîne vide, qui
    violerait la contrainte `ck_organization_slug_not_empty` au milieu d'une inscription.
    """
    assert slugify(nom) == attendu


async def test_two_organizations_with_the_same_name_get_distinct_slugs(
    auth_client: AsyncClient,
) -> None:
    first = await auth_client.post("/api/organizations", json={"name": "Plomberie Martin"})
    second = await auth_client.post("/api/organizations", json={"name": "Plomberie Martin"})

    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["slug"] == "plomberie-martin"
    assert second.json()["slug"].startswith("plomberie-martin-")
    assert second.json()["slug"] != first.json()["slug"]


# --- Identité de l'entreprise -----------------------------------------------------------------


async def test_an_organization_starts_without_any_legal_field(auth_client: AsyncClient) -> None:
    """L'inscription ne se bloque pas sur un SIRET.

    Exiger l'identité légale à la création perdrait l'artisan avant qu'il ait vu le produit :
    ces champs sont exigés à l'émission du devis, pas à l'ouverture du compte.
    """
    created = await auth_client.post("/api/organizations", json={"name": "Tout neuf"})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["siret"] is None
    assert body["decennial_insurer"] is None
    assert body["share_capital_cents"] is None


async def test_the_company_identity_round_trips_including_the_decennial_insurance(
    auth_client: AsyncClient,
) -> None:
    organization = await _first_organization(auth_client)

    updated = await auth_client.patch(
        f"/api/organizations/{organization['id']}", json=IDENTITE_COMPLETE
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    for field, value in IDENTITE_COMPLETE.items():
        assert body[field] == value, field

    # Relu par une autre requête : le champ est bien en base, pas seulement dans la réponse.
    reread = (await auth_client.get(f"/api/organizations/{organization['id']}")).json()
    assert reread["decennial_policy_number"] == "POL-2026-004417"
    assert reread["decennial_coverage_area"] == "France métropolitaine"


async def test_the_share_capital_stays_an_integer_number_of_cents(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """Un capital est un montant : centimes entiers, jamais un flottant.

    Le reste du modèle est en flottants centimètres, et un capital de 1 000,10 € relu en flottant
    finirait imprimé de travers sur un document contractuel.
    """
    organization = await _first_organization(auth_client)

    await auth_client.patch(
        f"/api/organizations/{organization['id']}", json={"share_capital_cents": 100_010}
    )
    stored = (
        await session.execute(
            select(Organization).where(col(Organization.id) == organization["id"])
        )
    ).scalar_one()
    await session.refresh(stored)
    assert stored.share_capital_cents == 100_010
    assert isinstance(stored.share_capital_cents, int)

    refused = await auth_client.patch(
        f"/api/organizations/{organization['id']}", json={"share_capital_cents": 1000.10}
    )
    assert refused.status_code == 422, refused.text


async def test_a_null_field_clears_it_and_an_absent_field_leaves_it_alone(
    auth_client: AsyncClient,
) -> None:
    """Contrat de la mise à jour partielle : absent ≠ nul.

    Toutes ces colonnes sont nullables, donc un artisan doit pouvoir retirer un numéro de TVA
    saisi par erreur — ce que le `PartialUpdate` du plan 2D, qui refuse les nuls, ne permettrait
    pas.
    """
    organization = await _first_organization(auth_client)
    await auth_client.patch(
        f"/api/organizations/{organization['id']}",
        json={"vat_number": "FR12345678901", "city": "Versailles"},
    )

    body = (
        await auth_client.patch(
            f"/api/organizations/{organization['id']}", json={"vat_number": None}
        )
    ).json()
    assert body["vat_number"] is None
    assert body["city"] == "Versailles", "un champ absent de la charge utile ne doit pas bouger"


@pytest.mark.parametrize(
    ("champ", "valeur"),
    [
        ("siret", "1234"),
        ("siret", "1234567890123A"),
        ("vat_number", "12345678901"),
        ("share_capital_cents", -1),
        # `javascript:` et `data:` sont fermés : ce logo est réaffiché dans l'interface et incrusté
        # dans les PDF, un schéma libre en ferait une injection stockée.
        ("logo_url", "javascript:alert(1)"),
        ("logo_url", "data:text/html;base64,PHNjcmlwdD4="),
        ("billing_email", "pas-une-adresse"),
    ],
)
async def test_an_invalid_legal_field_is_refused(
    auth_client: AsyncClient, champ: str, valeur: object
) -> None:
    organization = await _first_organization(auth_client)
    refused = await auth_client.patch(
        f"/api/organizations/{organization['id']}", json={champ: valeur}
    )
    assert refused.status_code == 422, f"{champ}={valeur!r} accepté : {refused.text}"


async def test_the_name_cannot_be_erased(auth_client: AsyncClient) -> None:
    """`name` est la seule colonne non nullable : `null` n'y a pas de sens.

    Sans contrôle en amont, la requête partait en violation `NOT NULL` et ressortait en 500 —
    n'importe quel administrateur pouvait provoquer une erreur serveur en une requête.
    """
    organization = await _first_organization(auth_client)
    refused = await auth_client.patch(
        f"/api/organizations/{organization['id']}", json={"name": None}
    )
    assert refused.status_code == 422, refused.text
    assert (
        await auth_client.get(f"/api/organizations/{organization['id']}")
    ).json()["name"] is not None


async def test_the_slug_cannot_be_changed_after_creation(auth_client: AsyncClient) -> None:
    """Il apparaît dans des URL déjà diffusées : le modifier casserait des liens en circulation."""
    organization = await _first_organization(auth_client)
    refused = await auth_client.patch(
        f"/api/organizations/{organization['id']}", json={"slug": "autre-chose"}
    )
    assert refused.status_code == 422, refused.text


# --- Membres et rôles -------------------------------------------------------------------------


async def test_the_creator_becomes_owner_without_any_invitation(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    created = (
        await auth_client.post("/api/organizations", json={"name": "Menuiserie Durand"})
    ).json()

    members = (await auth_client.get(f"/api/organizations/{created['id']}/members")).json()
    assert len(members) == 1
    assert members[0]["role"] == "owner"
    assert members[0]["accepted_at"] is not None, "le créateur n'a personne pour l'inviter"

    membership = (
        await session.execute(
            select(Membership).where(col(Membership.organization_id) == created["id"])
        )
    ).scalar_one()
    assert membership.role is OrganizationRole.OWNER


async def test_the_member_list_shows_email_and_role(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    organization = await _organization_that_pays_for_seats(auth_client, session)

    async with logged_in("compagnon@exemple.fr") as compagnon:
        invitation = await auth_client.post(
            f"/api/organizations/{organization['id']}/invitations",
            json={"email": "compagnon@exemple.fr", "role": "editor"},
        )
        await compagnon.post(
            "/api/invitations/accept", json={"token": invitation.json()["token"]}
        )

    members = (await auth_client.get(f"/api/organizations/{organization['id']}/members")).json()
    par_email = {member["email"]: member for member in members}
    assert par_email["compagnon@exemple.fr"]["role"] == "editor"
    assert par_email["titulaire@exemple.fr"]["role"] == "owner"


async def test_an_admin_can_neither_create_nor_touch_an_owner(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """Sans cette règle, `admin` et `owner` sont le même rôle, à une requête près."""
    organization = await _organization_that_pays_for_seats(auth_client, session)
    titulaire = (await auth_client.get("/api/auth/me")).json()

    async with logged_in("second@exemple.fr") as second:
        invitation = await auth_client.post(
            f"/api/organizations/{organization['id']}/invitations",
            json={"email": "second@exemple.fr", "role": "admin"},
        )
        accepted = await second.post(
            "/api/invitations/accept", json={"token": invitation.json()["token"]}
        )
        assert accepted.status_code == 200, accepted.text
        moi = accepted.json()

        promotion = await second.patch(
            f"/api/organizations/{organization['id']}/members/{moi['user_id']}",
            json={"role": "owner"},
        )
        assert promotion.status_code == 403, promotion.text

        retrogradation = await second.patch(
            f"/api/organizations/{organization['id']}/members/{titulaire['id']}",
            json={"role": "viewer"},
        )
        assert retrogradation.status_code == 403, retrogradation.text

        eviction = await second.delete(
            f"/api/organizations/{organization['id']}/members/{titulaire['id']}"
        )
        assert eviction.status_code == 403, eviction.text

        # Sur les rôles inférieurs au sien, en revanche, il gouverne.
        invitation_lecteur = await second.post(
            f"/api/organizations/{organization['id']}/invitations",
            json={"email": "lecteur@exemple.fr", "role": "editor"},
        )
        assert invitation_lecteur.status_code == 201, invitation_lecteur.text


async def test_the_last_owner_can_neither_leave_nor_be_demoted(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """Une organisation sans propriétaire n'a plus personne pour inviter, payer, ou fermer.

    Seule une intervention en base la débloquerait : c'est exactement le genre d'impasse qu'un
    produit ne doit pas laisser fabriquer en un clic.
    """
    organization = await _organization_that_pays_for_seats(auth_client, session)
    titulaire = (await auth_client.get("/api/auth/me")).json()

    depart = await auth_client.delete(
        f"/api/organizations/{organization['id']}/members/{titulaire['id']}"
    )
    assert depart.status_code == 409, depart.text

    retrogradation = await auth_client.patch(
        f"/api/organizations/{organization['id']}/members/{titulaire['id']}",
        json={"role": "admin"},
    )
    assert retrogradation.status_code == 409, retrogradation.text

    # Avec un second propriétaire, le premier peut partir.
    async with logged_in("associe@exemple.fr") as associe:
        invitation = await auth_client.post(
            f"/api/organizations/{organization['id']}/invitations",
            json={"email": "associe@exemple.fr", "role": "owner"},
        )
        await associe.post("/api/invitations/accept", json={"token": invitation.json()["token"]})

    libere = await auth_client.delete(
        f"/api/organizations/{organization['id']}/members/{titulaire['id']}"
    )
    assert libere.status_code == 204, libere.text
    assert (await auth_client.get(f"/api/organizations/{organization['id']}")).status_code == 404


async def test_anyone_may_leave_an_organization_on_their_own(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """Quitter ne demande aucun rôle : c'est le pendant du droit de partir."""
    organization = await _organization_that_pays_for_seats(auth_client, session)

    async with logged_in("passant@exemple.fr") as passant:
        invitation = await auth_client.post(
            f"/api/organizations/{organization['id']}/invitations",
            json={"email": "passant@exemple.fr", "role": "viewer"},
        )
        accepted = await passant.post(
            "/api/invitations/accept", json={"token": invitation.json()["token"]}
        )
        moi = accepted.json()

        depart = await passant.delete(
            f"/api/organizations/{organization['id']}/members/{moi['user_id']}"
        )
        assert depart.status_code == 204, depart.text
        assert (
            await passant.get(f"/api/organizations/{organization['id']}")
        ).status_code == 404


# --- Invitations ------------------------------------------------------------------------------


async def test_only_the_hash_of_the_invitation_token_is_stored(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """Une copie de la base ne doit pas permettre de rejoindre les organisations qu'elle
    contient : c'est tout l'objet du hachage.
    """
    organization = await _organization_that_pays_for_seats(auth_client, session)

    created = await auth_client.post(
        f"/api/organizations/{organization['id']}/invitations",
        json={"email": "invite@exemple.fr", "role": "editor"},
    )
    assert created.status_code == 201, created.text
    token = created.json()["token"]

    stored = (
        await session.execute(
            select(Invitation).where(
                col(Invitation.organization_id) == organization["id"]
            )
        )
    ).scalar_one()
    await session.refresh(stored)
    assert stored.token_hash != token
    assert stored.token_hash == hash_invitation_token(token)
    assert token not in str(stored.model_dump())

    # Et la relecture par l'API ne le rend pas non plus : il n'existe plus nulle part.
    listed = (
        await auth_client.get(f"/api/organizations/{organization['id']}/invitations")
    ).json()
    assert "token" not in listed[0]


async def test_an_invitation_only_works_for_the_address_it_was_sent_to(
    auth_client: AsyncClient, other_client: AsyncClient, session: AsyncSession
) -> None:
    """Le jeton seul ne suffit pas.

    Un lien transféré — ou intercepté dans une boîte mail — ne doit pas ouvrir l'organisation à un
    compte qui n'était pas l'invité.
    """
    organization = await _organization_that_pays_for_seats(auth_client, session)
    invitation = await auth_client.post(
        f"/api/organizations/{organization['id']}/invitations",
        json={"email": "destinataire@exemple.fr", "role": "admin"},
    )

    detourne = await other_client.post(
        "/api/invitations/accept", json={"token": invitation.json()["token"]}
    )
    assert detourne.status_code == 404, detourne.text
    assert (await other_client.get(f"/api/organizations/{organization['id']}")).status_code == 404


async def test_an_invitation_cannot_be_used_twice(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    organization = await _organization_that_pays_for_seats(auth_client, session)
    invitation = await auth_client.post(
        f"/api/organizations/{organization['id']}/invitations",
        json={"email": "unique@exemple.fr", "role": "viewer"},
    )
    token = invitation.json()["token"]

    async with logged_in("unique@exemple.fr") as invite:
        assert (
            await invite.post("/api/invitations/accept", json={"token": token})
        ).status_code == 200
        rejoue = await invite.post("/api/invitations/accept", json={"token": token})
        assert rejoue.status_code == 404, rejoue.text


async def test_an_expired_invitation_is_refused(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    organization = await _organization_that_pays_for_seats(auth_client, session)
    invitation = await auth_client.post(
        f"/api/organizations/{organization['id']}/invitations",
        json={"email": "tardif@exemple.fr", "role": "editor", "expires_in_days": 1},
    )
    token = invitation.json()["token"]

    stored = (
        await session.execute(
            select(Invitation).where(
                col(Invitation.token_hash) == hash_invitation_token(token)
            )
        )
    ).scalar_one()
    stored.expires_at = utcnow() - timedelta(seconds=1)
    await session.commit()

    async with logged_in("tardif@exemple.fr") as tardif:
        refused = await tardif.post("/api/invitations/accept", json={"token": token})
        assert refused.status_code == 404, refused.text


async def test_an_unknown_token_says_exactly_the_same_thing_as_an_expired_one(
    auth_client: AsyncClient,
) -> None:
    """Distinguer les deux permettrait de confirmer qu'une invitation a existé, et pour qui."""
    inconnu = await auth_client.post(
        "/api/invitations/accept", json={"token": "jeton-parfaitement-inconnu-mais-assez-long"}
    )
    assert inconnu.status_code == 404
    assert inconnu.json()["detail"] == "Invitation introuvable ou expirée"


async def test_an_invitation_cannot_demote_the_last_owner(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """L'invitation est un second chemin vers le rôle : elle doit buter sur la même règle.

    Sans ce contrôle, un `admin` invitait le dernier `owner` en `viewer` et l'organisation se
    retrouvait sans propriétaire au premier clic de celui-ci — un contournement complet de la
    protection posée sur le changement de rôle.
    """
    organization = await _organization_that_pays_for_seats(auth_client, session)
    titulaire = (await auth_client.get("/api/auth/me")).json()

    async with logged_in("adjoint@exemple.fr") as adjoint:
        promotion = await auth_client.post(
            f"/api/organizations/{organization['id']}/invitations",
            json={"email": "adjoint@exemple.fr", "role": "admin"},
        )
        await adjoint.post("/api/invitations/accept", json={"token": promotion.json()["token"]})

        piege = await adjoint.post(
            f"/api/organizations/{organization['id']}/invitations",
            json={"email": titulaire["email"], "role": "viewer"},
        )
        assert piege.status_code == 201, piege.text

        refused = await auth_client.post(
            "/api/invitations/accept", json={"token": piege.json()["token"]}
        )
        assert refused.status_code == 409, refused.text

    members = (await auth_client.get(f"/api/organizations/{organization['id']}/members")).json()
    par_email = {member["email"]: member["role"] for member in members}
    assert par_email[titulaire["email"]] == "owner"


async def test_accepting_an_invitation_upgrades_an_existing_pending_membership(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """Deux invitations successives ne fabriquent pas deux appartenances.

    `uq_membership_user_organization` l'interdit en base ; la route doit donc mettre à jour la
    ligne existante plutôt que d'en ajouter une, sinon la seconde invitation sort en 500.
    """
    organization = await _organization_that_pays_for_seats(auth_client, session)

    async with logged_in("hesitant@exemple.fr") as hesitant:
        premiere = await auth_client.post(
            f"/api/organizations/{organization['id']}/invitations",
            json={"email": "hesitant@exemple.fr", "role": "viewer"},
        )
        await hesitant.post("/api/invitations/accept", json={"token": premiere.json()["token"]})

        seconde = await auth_client.post(
            f"/api/organizations/{organization['id']}/invitations",
            json={"email": "hesitant@exemple.fr", "role": "editor"},
        )
        promu = await hesitant.post(
            "/api/invitations/accept", json={"token": seconde.json()["token"]}
        )
        assert promu.status_code == 200, promu.text
        assert promu.json()["role"] == "editor"

    memberships = (
        (
            await session.execute(
                select(Membership).where(
                    col(Membership.organization_id) == organization["id"]
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(memberships) == 2, "une seule appartenance par compte et par organisation"
