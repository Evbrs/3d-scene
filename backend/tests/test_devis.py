"""Barème, chiffrage et devis (`docs/strategie-produit.md` §2 et §3.1).

Trois invariants sont vérifiés ici, et ce sont eux qui font qu'un devis est un document et non un
tableau de prix :

1. **une ligne émise est une copie** — modifier le barème ne change aucun devis déjà écrit ;
2. **la numérotation est continue** — un brouillon abandonné ne consomme pas de numéro, et deux
   organisations ne partagent pas leur suite ;
3. **un document émis se lit, il ne se modifie pas.**

La pièce de référence est celle de `tests/test_takeoff_api.py` : 400 x 300 aux murs de 10, sous
250 de plafond, faïence sur le mur A, carrelage au sol, peinture au plafond et une porte de 90 sur
le mur B. Le barème par défaut donne alors, au centime :

| ligne                          | quantité   | P.U.     | total HT     |
|--------------------------------|------------|----------|--------------|
| Faïence — mur A                | 10,000 m²  |  85,00 € |    850,00 €  |
| Peinture — mur B (porte déduite)|  5,664 m² |  24,00 € |    135,94 €  |
| Peinture — mur C               | 10,000 m²  |  24,00 € |    240,00 €  |
| Peinture — mur D               |  7,500 m²  |  24,00 € |    180,00 €  |
| Carrelage — sol                | 11,310 m²  |  95,00 € |  1 074,45 €  |
| Peinture — plafond             | 11,310 m²  |  28,00 € |    316,68 €  |
| Plinthes                       | 12,700 ml  |  18,00 € |    228,60 €  |
| **Total HT**                   |            |          | **3 025,67 €**|
"""

from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient

from app.services.pricing import (
    CostingOverride,
    PriceReference,
    PricingOptions,
    build_quote_lines,
    line_total_cents,
    normalize_material,
    resolve_price_code,
    vat_buckets_from,
)
from app.services.seed_prices import DEFAULT_PRICE_BOOK_NAME, DEFAULT_PRICE_ITEMS
from tests.test_permissions_locataire import logged_in
from tests.test_takeoff_api import build_room

MURS_EN_PEINTURE: dict[str, Any] = {"default_price_codes": {"wall": "PEINT-MUR"}}


async def create_quote(client: AsyncClient, project_id: int, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"client_name": "Monsieur Martin", **MURS_EN_PEINTURE, **extra}
    response = await client.post(f"/api/projects/{project_id}/quotes", json=payload)
    assert response.status_code == 201, response.text
    return dict(response.json())


async def organization_of(client: AsyncClient) -> int:
    """Organisation du compte, créée si le compte n'a encore rien écrit.

    L'organisation personnelle naît paresseusement, à la première écriture : un compte qui n'a pas
    encore créé de projet n'en a pas. Le repli explicite évite de faire dépendre le test d'un ordre
    d'appels qui n'a rien à voir avec ce qu'il vérifie.
    """
    listed = (await client.get("/api/organizations")).json()
    if listed:
        return int(listed[0]["id"])
    created = await client.post("/api/organizations", json={"name": "Entreprise de test"})
    assert created.status_code == 201, created.text
    return int(created.json()["id"])


# --- Barème --------------------------------------------------------------------------------------


async def test_the_default_price_book_is_seeded_on_the_first_quote(
    auth_client: AsyncClient,
) -> None:
    """Semé paresseusement, comme l'organisation personnelle : aucun chemin ne peut l'oublier."""
    project, _room = await build_room(auth_client)
    organization_id = await organization_of(auth_client)
    assert (await auth_client.get(f"/api/organizations/{organization_id}/price-books")).json() == []

    await create_quote(auth_client, project["id"])

    books = (await auth_client.get(f"/api/organizations/{organization_id}/price-books")).json()
    assert [book["name"] for book in books] == [DEFAULT_PRICE_BOOK_NAME]
    assert books[0]["is_default"] is True

    items = (await auth_client.get(f"/api/price-books/{books[0]['id']}/items")).json()
    assert len(items) == len(DEFAULT_PRICE_ITEMS)
    # Les trois taux de la rénovation sont représentés dès le premier usage : un chantier réel les
    # mélange, et un barème mono-taux enseignerait le mauvais réflexe.
    assert {item["vat_rate_bp"] for item in items} == {550, 1_000, 2_000}


async def test_prices_are_integer_cents_and_never_floats(auth_client: AsyncClient) -> None:
    """`1000.10` est refusé en 422 : un montant flottant ne se rejoue pas à l'identique."""
    organization_id = await organization_of(auth_client)
    book = (
        await auth_client.post(
            f"/api/organizations/{organization_id}/price-books",
            json={"name": "Tarif pro", "seed_default_items": False},
        )
    ).json()

    refused = await auth_client.post(
        f"/api/price-books/{book['id']}/items",
        json={"code": "TEST", "label": "Essai", "unit_price_cents": 1000.10},
    )
    assert refused.status_code == 422, refused.text

    accepted = await auth_client.post(
        f"/api/price-books/{book['id']}/items",
        json={"code": "TEST", "label": "Essai", "unit_price_cents": 100_010},
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["unit_price_cents"] == 100_010


async def test_a_duplicate_code_in_a_price_book_is_refused(auth_client: AsyncClient) -> None:
    organization_id = await organization_of(auth_client)
    book = (
        await auth_client.post(
            f"/api/organizations/{organization_id}/price-books",
            json={"name": "Tarif pro", "seed_default_items": False},
        )
    ).json()
    body = {"code": "PEINT-MUR", "label": "Peinture"}

    assert (
        await auth_client.post(f"/api/price-books/{book['id']}/items", json=body)
    ).status_code == 201
    doublon = await auth_client.post(f"/api/price-books/{book['id']}/items", json=body)

    assert doublon.status_code == 409, doublon.text


async def test_a_price_book_of_another_organization_is_invisible(
    auth_client: AsyncClient, other_client: AsyncClient
) -> None:
    """Utiliser le barème d'un concurrent ne doit ni marcher, ni révéler qu'il existe."""
    project, _room = await build_room(auth_client)
    intrus_organization = await organization_of(other_client)
    leur_bareme = (
        await other_client.post(
            f"/api/organizations/{intrus_organization}/price-books", json={"name": "Tarif voisin"}
        )
    ).json()

    response = await auth_client.post(
        f"/api/projects/{project['id']}/quotes",
        json={"client_name": "Client", "price_book_id": leur_bareme["id"]},
    )

    assert response.status_code == 404, response.text


# --- Chiffrage -----------------------------------------------------------------------------------


async def test_a_quote_is_built_from_the_takeoff_face_by_face(auth_client: AsyncClient) -> None:
    """La promesse produit : *le devis chiffré par mur*. Une ligne par face, pas un total global."""
    project, _room = await build_room(auth_client)

    quote = await create_quote(auth_client, project["id"])

    assert quote["status"] == "draft"
    # Un brouillon n'a pas de numéro : le réserver ferait un trou dans la suite s'il est abandonné.
    assert quote["number"] is None
    assert [(line["label"], line["total_ht_cents"]) for line in quote["lines"]] == [
        ("Faïence murale, fourniture et pose droite — Salle de bains — mur A", 85_000),
        ("Peinture acrylique sur murs, 2 couches — Salle de bains — mur B", 13_594),
        ("Peinture acrylique sur murs, 2 couches — Salle de bains — mur C", 24_000),
        ("Peinture acrylique sur murs, 2 couches — Salle de bains — mur D", 18_000),
        ("Carrelage de sol, pose droite — Salle de bains — sol", 107_445),
        ("Peinture sur plafond, deux couches — Salle de bains — plafond", 31_668),
        ("Plinthes, fourniture et pose — Salle de bains", 22_860),
    ]
    assert quote["total_ht_cents"] == 302_567
    assert quote["total_tva_cents"] == 30_257
    assert quote["total_ttc_cents"] == 332_824
    assert quote["warnings"] == []


async def test_the_material_of_a_covering_finds_its_price_line_by_itself(
    auth_client: AsyncClient,
) -> None:
    """« Carrelage » sur un sol et « faience » sur un mur ne désignent pas la même ligne.

    C'est la règle qui évite les soixante rattachements à la main d'un projet de douze pièces.
    """
    project, _room = await build_room(auth_client)

    quote = await create_quote(auth_client, project["id"])

    codes = {line["source_face_id"]: line["source_price_item_code"] for line in quote["lines"]}
    assert "FAIENCE" in codes.values()
    assert "CARRELAGE-SOL" in codes.values()
    assert "PEINT-PLAF" in codes.values()


async def test_a_face_without_a_covering_is_not_invented_a_price(
    auth_client: AsyncClient,
) -> None:
    """Sans matériau ni code par défaut, la face n'est pas chiffrée — et l'avertissement la nomme.

    Inventer un prix serait la pire issue : le devis aurait l'air complet.
    """
    project, _room = await build_room(auth_client)

    response = await auth_client.post(
        f"/api/projects/{project['id']}/quotes", json={"client_name": "Client"}
    )

    assert response.status_code == 201, response.text
    quote = response.json()
    non_chiffrees = [message for message in quote["warnings"] if "non chiffrée" in message]
    assert len(non_chiffrees) == 3  # les murs B, C et D n'ont pas de revêtement
    assert "Salle de bains — mur B" in " ".join(non_chiffrees)
    assert all("mur B" not in line["label"] for line in quote["lines"])


async def test_an_unknown_face_kind_in_the_default_codes_is_refused(
    auth_client: AsyncClient,
) -> None:
    """Une faute de frappe (`walls`) produirait un devis silencieusement incomplet."""
    project, _room = await build_room(auth_client)

    response = await auth_client.post(
        f"/api/projects/{project['id']}/quotes",
        json={"client_name": "Client", "default_price_codes": {"walls": "PEINT-MUR"}},
    )

    assert response.status_code == 422, response.text


async def test_a_face_costing_overrides_the_automatic_matching(auth_client: AsyncClient) -> None:
    """Le rattachement explicite l'emporte sur tout : c'est la décision de l'artisan."""
    project, room = await build_room(auth_client)
    mur_c = next(face for face in room["faces"] if face["label"] == "C")

    posee = await auth_client.put(
        f"/api/faces/{mur_c['id']}/costing",
        json={
            "price_item_code": "LAMBRIS",
            "override_quantity": "8.000",
            "override_unit_price_cents": 7_000,
        },
    )
    assert posee.status_code == 200, posee.text

    quote = await create_quote(auth_client, project["id"])

    ligne = next(line for line in quote["lines"] if line["source_face_id"] == mur_c["id"])
    assert ligne["source_price_item_code"] == "LAMBRIS"
    assert Decimal(ligne["quantity"]) == Decimal("8.000")
    assert ligne["unit_price_cents"] == 7_000
    assert ligne["total_ht_cents"] == 56_000


async def test_removing_a_face_costing_restores_the_automatic_matching(
    auth_client: AsyncClient,
) -> None:
    project, room = await build_room(auth_client)
    mur_a = next(face for face in room["faces"] if face["label"] == "A")
    await auth_client.put(
        f"/api/faces/{mur_a['id']}/costing", json={"price_item_code": "LAMBRIS"}
    )

    supprime = await auth_client.delete(f"/api/faces/{mur_a['id']}/costing")
    assert supprime.status_code == 204
    assert (await auth_client.get(f"/api/projects/{project['id']}/costings")).json() == []

    quote = await create_quote(auth_client, project["id"])
    ligne = next(line for line in quote["lines"] if line["source_face_id"] == mur_a["id"])
    assert ligne["source_price_item_code"] == "FAIENCE"


async def test_the_vat_is_carried_by_the_line_and_never_by_the_document(
    auth_client: AsyncClient,
) -> None:
    """Un même chantier mélange 5,5 %, 10 % et 20 % : le récapitulatif a trois assiettes."""
    project, _room = await build_room(auth_client)

    quote = await create_quote(
        auth_client,
        project["id"],
        extra_lines=[
            {
                "label": "Isolation thermique par l'intérieur",
                "unit": "m2",
                "quantity": "10.000",
                "unit_price_cents": 6_800,
                "vat_rate_bp": 550,
                "source_price_item_code": "ISOLATION-ITI",
            },
            {
                "label": "Travaux en logement de moins de deux ans",
                "unit": "forfait",
                "quantity": "1",
                "unit_price_cents": 50_000,
                "vat_rate_bp": 2_000,
            },
        ],
    )

    assiettes = {bucket["rate_bp"]: bucket for bucket in quote["vat_breakdown"]}
    assert sorted(assiettes) == [550, 1_000, 2_000]
    assert assiettes[550]["base_cents"] == 68_000
    assert assiettes[550]["tax_cents"] == 3_740
    assert assiettes[2_000]["base_cents"] == 50_000
    assert assiettes[2_000]["tax_cents"] == 10_000
    assert quote["total_ht_cents"] == 302_567 + 68_000 + 50_000
    assert quote["total_tva_cents"] == 30_257 + 3_740 + 10_000


async def test_a_discount_is_a_negative_line_and_not_a_second_kind_of_document(
    auth_client: AsyncClient,
) -> None:
    project, _room = await build_room(auth_client)

    quote = await create_quote(
        auth_client,
        project["id"],
        extra_lines=[
            {
                "label": "Remise commerciale",
                "unit": "forfait",
                "quantity": "1",
                "unit_price_cents": -25_000,
                "vat_rate_bp": 1_000,
            }
        ],
    )

    assert quote["lines"][-1]["total_ht_cents"] == -25_000
    assert quote["total_ht_cents"] == 302_567 - 25_000


# --- Le devis émis est un contrat -----------------------------------------------------------------


async def test_changing_a_price_item_leaves_an_issued_quote_strictly_unchanged(
    auth_client: AsyncClient,
) -> None:
    """**Le test central du lot.**

    En France un devis signé est un contrat : s'il change après envoi parce que l'artisan a revu
    son tarif, ce n'est pas un défaut d'affichage, c'est un problème juridique. La ligne recopie
    son libellé, son prix et son taux, et ne fait aucune jointure de lecture vers `price_item`.
    """
    project, _room = await build_room(auth_client)
    quote = await create_quote(auth_client, project["id"])
    emis = (await auth_client.post(f"/api/quotes/{quote['id']}/issue")).json()
    avant = (await auth_client.get(f"/api/quotes/{quote['id']}")).json()
    assert emis["number"] is not None

    organization_id = await organization_of(auth_client)
    book = (
        await auth_client.get(f"/api/organizations/{organization_id}/price-books")
    ).json()[0]
    items = (await auth_client.get(f"/api/price-books/{book['id']}/items")).json()
    faience = next(item for item in items if item["code"] == "FAIENCE")

    modifie = await auth_client.patch(
        f"/api/price-items/{faience['id']}",
        json={"label": "Faïence haut de gamme", "unit_price_cents": 19_900, "vat_rate_bp": 2_000},
    )
    assert modifie.status_code == 200, modifie.text

    apres = (await auth_client.get(f"/api/quotes/{quote['id']}")).json()
    assert apres == avant
    ligne = apres["lines"][0]
    assert ligne["label"] == "Faïence murale, fourniture et pose droite — Salle de bains — mur A"
    assert ligne["unit_price_cents"] == 8_500
    assert ligne["vat_rate_bp"] == 1_000
    assert apres["total_ttc_cents"] == 332_824


async def test_deleting_a_price_item_does_not_break_an_issued_quote(
    auth_client: AsyncClient,
) -> None:
    """`source_price_item_code` est une trace d'audit, pas une clé étrangère."""
    project, _room = await build_room(auth_client)
    quote = await create_quote(auth_client, project["id"])
    await auth_client.post(f"/api/quotes/{quote['id']}/issue")

    organization_id = await organization_of(auth_client)
    book = (await auth_client.get(f"/api/organizations/{organization_id}/price-books")).json()[0]
    items = (await auth_client.get(f"/api/price-books/{book['id']}/items")).json()
    faience = next(item for item in items if item["code"] == "FAIENCE")

    assert (await auth_client.delete(f"/api/price-items/{faience['id']}")).status_code == 204

    relu = await auth_client.get(f"/api/quotes/{quote['id']}")
    assert relu.status_code == 200, relu.text
    assert relu.json()["lines"][0]["source_price_item_code"] == "FAIENCE"
    assert relu.json()["total_ttc_cents"] == 332_824


async def test_an_issued_quote_refuses_every_change_but_its_status(
    auth_client: AsyncClient,
) -> None:
    """Corriger un devis parti chez le client ferait diverger deux exemplaires d'un même contrat."""
    project, _room = await build_room(auth_client)
    quote = await create_quote(auth_client, project["id"])
    await auth_client.post(f"/api/quotes/{quote['id']}/issue")

    refuse = await auth_client.patch(
        f"/api/quotes/{quote['id']}", json={"client_name": "Autre client"}
    )
    assert refuse.status_code == 409, refuse.text

    accepte = await auth_client.patch(f"/api/quotes/{quote['id']}", json={"status": "accepted"})
    assert accepte.status_code == 200, accepte.text
    assert accepte.json()["status"] == "accepted"


async def test_a_draft_is_freely_editable(auth_client: AsyncClient) -> None:
    project, _room = await build_room(auth_client)
    quote = await create_quote(auth_client, project["id"])

    modifie = await auth_client.patch(
        f"/api/quotes/{quote['id']}",
        json={"client_name": "Madame Durand", "site_city": "Versailles"},
    )

    assert modifie.status_code == 200, modifie.text
    assert modifie.json()["client_name"] == "Madame Durand"
    assert modifie.json()["site_city"] == "Versailles"


async def test_a_status_never_goes_backwards(auth_client: AsyncClient) -> None:
    """« Accepté » redevenu « brouillon » effacerait la trace d'un accord client."""
    project, _room = await build_room(auth_client)
    quote = await create_quote(auth_client, project["id"])
    await auth_client.post(f"/api/quotes/{quote['id']}/issue")
    await auth_client.patch(f"/api/quotes/{quote['id']}", json={"status": "accepted"})

    retour = await auth_client.patch(f"/api/quotes/{quote['id']}", json={"status": "draft"})

    assert retour.status_code == 409, retour.text


# --- Numérotation ---------------------------------------------------------------------------------


async def test_quote_numbers_are_sequential_and_have_no_gaps(auth_client: AsyncClient) -> None:
    project, _room = await build_room(auth_client)
    numeros = []
    for _ in range(3):
        quote = await create_quote(auth_client, project["id"])
        emis = await auth_client.post(f"/api/quotes/{quote['id']}/issue")
        assert emis.status_code == 200, emis.text
        numeros.append(emis.json()["number"])

    suffixes = [int(numero.rsplit("-", 1)[1]) for numero in numeros]
    assert suffixes == [1, 2, 3]
    assert all(numero.startswith("DEV-") for numero in numeros)


async def test_an_abandoned_draft_does_not_consume_a_number(auth_client: AsyncClient) -> None:
    """C'est la raison d'être de l'attribution tardive : la suite doit être continue."""
    project, _room = await build_room(auth_client)
    await create_quote(auth_client, project["id"])  # brouillon jamais émis
    await create_quote(auth_client, project["id"])  # brouillon jamais émis
    quote = await create_quote(auth_client, project["id"])

    emis = (await auth_client.post(f"/api/quotes/{quote['id']}/issue")).json()

    assert emis["number"].endswith("-0001")


async def test_two_organizations_do_not_share_their_numbering(
    auth_client: AsyncClient, other_client: AsyncClient
) -> None:
    """Deux clients du service peuvent parfaitement émettre le même « DEV-2026-0001 »."""
    mien_project, _room = await build_room(auth_client)
    leur_project, _autre = await build_room(other_client)

    mien = await create_quote(auth_client, mien_project["id"])
    leur = await create_quote(other_client, leur_project["id"])
    mien_numero = (await auth_client.post(f"/api/quotes/{mien['id']}/issue")).json()["number"]
    leur_numero = (await other_client.post(f"/api/quotes/{leur['id']}/issue")).json()["number"]

    assert mien_numero.endswith("-0001")
    assert leur_numero.endswith("-0001")


async def test_a_quote_is_issued_only_once(auth_client: AsyncClient) -> None:
    project, _room = await build_room(auth_client)
    quote = await create_quote(auth_client, project["id"])
    await auth_client.post(f"/api/quotes/{quote['id']}/issue")

    encore = await auth_client.post(f"/api/quotes/{quote['id']}/issue")

    assert encore.status_code == 409, encore.text


# --- Passage en facture ---------------------------------------------------------------------------


async def test_only_an_accepted_quote_becomes_an_invoice(auth_client: AsyncClient) -> None:
    project, _room = await build_room(auth_client)
    quote = await create_quote(auth_client, project["id"])
    await auth_client.post(f"/api/quotes/{quote['id']}/issue")

    trop_tot = await auth_client.post(f"/api/quotes/{quote['id']}/invoice")
    assert trop_tot.status_code == 409, trop_tot.text

    await auth_client.patch(f"/api/quotes/{quote['id']}", json={"status": "accepted"})
    facture = await auth_client.post(f"/api/quotes/{quote['id']}/invoice")

    assert facture.status_code == 200, facture.text
    corps = facture.json()
    assert corps["status"] == "invoiced"
    assert corps["invoice_number"].startswith("FAC-")
    assert corps["invoiced_at"] is not None
    assert corps["due_date"] is not None


async def test_the_invoice_keeps_the_lines_and_the_prices_of_the_quote(
    auth_client: AsyncClient,
) -> None:
    """Aucune recopie : c'est le même document qui change d'état.

    Dupliquer créerait deux vérités pour un seul contrat, et l'écart entre les deux fait le litige.
    """
    project, _room = await build_room(auth_client)
    quote = await create_quote(auth_client, project["id"])
    await auth_client.post(f"/api/quotes/{quote['id']}/issue")
    await auth_client.patch(f"/api/quotes/{quote['id']}", json={"status": "accepted"})
    avant = (await auth_client.get(f"/api/quotes/{quote['id']}")).json()

    apres = (await auth_client.post(f"/api/quotes/{quote['id']}/invoice")).json()

    assert apres["lines"] == avant["lines"]
    assert apres["total_ttc_cents"] == avant["total_ttc_cents"]
    # Le numéro de devis reste : la facture s'y rattache, elle ne l'efface pas.
    assert apres["number"] == avant["number"]


async def test_invoice_numbers_have_their_own_continuous_series(auth_client: AsyncClient) -> None:
    project, _room = await build_room(auth_client)
    numeros = []
    for _ in range(2):
        quote = await create_quote(auth_client, project["id"])
        await auth_client.post(f"/api/quotes/{quote['id']}/issue")
        await auth_client.patch(f"/api/quotes/{quote['id']}", json={"status": "accepted"})
        numeros.append((await auth_client.post(f"/api/quotes/{quote['id']}/invoice")).json())

    assert [int(quote["invoice_number"].rsplit("-", 1)[1]) for quote in numeros] == [1, 2]
    # Les deux suites sont indépendantes : le premier devis et la première facture portent tous
    # deux le numéro 1, sous des préfixes différents.
    assert numeros[0]["number"].endswith("-0001")
    assert numeros[0]["invoice_number"].endswith("-0001")


# --- Rôles ----------------------------------------------------------------------------------------


async def test_an_editor_prepares_a_quote_but_does_not_issue_it(auth_client: AsyncClient) -> None:
    """Émettre engage l'entreprise sur un prix : c'est un geste d'`admin`, pas de production."""
    project, _room = await build_room(auth_client)
    organization_id = await organization_of(auth_client)

    async with logged_in("compagnon@exemple.fr") as compagnon:
        invitation = await auth_client.post(
            f"/api/organizations/{organization_id}/invitations",
            json={"email": "compagnon@exemple.fr", "role": "editor"},
        )
        accepte = await compagnon.post(
            "/api/invitations/accept", json={"token": invitation.json()["token"]}
        )
        assert accepte.status_code == 200, accepte.text

        quote = await create_quote(compagnon, project["id"])
        refuse = await compagnon.post(f"/api/quotes/{quote['id']}/issue")
        assert refuse.status_code == 403, refuse.text

        refuse_bareme = await compagnon.post(
            f"/api/organizations/{organization_id}/price-books", json={"name": "Mes prix"}
        )
        assert refuse_bareme.status_code == 403, refuse_bareme.text


async def test_a_viewer_reads_a_quote_but_never_writes_one(auth_client: AsyncClient) -> None:
    project, _room = await build_room(auth_client)
    quote = await create_quote(auth_client, project["id"])
    organization_id = await organization_of(auth_client)

    async with logged_in("client-interne@exemple.fr") as lecteur:
        invitation = await auth_client.post(
            f"/api/organizations/{organization_id}/invitations",
            json={"email": "client-interne@exemple.fr", "role": "viewer"},
        )
        await lecteur.post("/api/invitations/accept", json={"token": invitation.json()["token"]})

        assert (await lecteur.get(f"/api/quotes/{quote['id']}")).status_code == 200
        refuse = await lecteur.post(
            f"/api/projects/{project['id']}/quotes", json={"client_name": "Client"}
        )
        assert refuse.status_code == 403, refuse.text


# --- Moteur de chiffrage, sans base ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("saisi", "attendu"),
    [
        ("Faïence", "faience"),
        ("FAIENCE", "faience"),
        ("Papier peint", "papier-peint"),
        ("  carrelage   mural ", "carrelage-mural"),
        (None, ""),
    ],
)
def test_material_normalisation_forgives_the_way_it_was_typed(saisi: str, attendu: str) -> None:
    assert normalize_material(saisi) == attendu


def test_the_price_code_resolution_follows_its_documented_order() -> None:
    """Rattachement explicite, puis synonyme, puis code lu tel quel, puis défaut."""
    references = {
        code: PriceReference(code, code, "m2", 1_000, 1_000)
        for code in ("FAIENCE", "CARRELAGE-SOL", "PEINT-MUR", "MON-PRIX")
    }
    options = PricingOptions(default_price_codes={"wall": "PEINT-MUR"})

    explicite = CostingOverride(price_item_code="MON-PRIX")
    assert resolve_price_code("wall", "faience", explicite, options, references) == "MON-PRIX"
    assert resolve_price_code("wall", "carrelage", None, options, references) == "FAIENCE"
    assert resolve_price_code("floor", "carrelage", None, options, references) == "CARRELAGE-SOL"
    assert resolve_price_code("wall", "mon-prix", None, options, references) == "MON-PRIX"
    assert resolve_price_code("wall", None, None, options, references) == "PEINT-MUR"
    assert resolve_price_code("floor", None, None, options, references) is None


def test_a_line_total_is_rounded_once_at_the_nearest_cent() -> None:
    """Un seul arrondi, au demi supérieur, puis figé dans la colonne."""
    assert line_total_cents(Decimal("5.664"), 2_400) == 13_594
    assert line_total_cents(Decimal("0.005"), 100) == 1
    assert line_total_cents(Decimal("11.310"), 9_500) == 107_445


def test_the_vat_is_computed_on_the_sum_of_the_bases_not_line_by_line() -> None:
    """Additionner des TVA déjà arrondies fait dériver le total, et c'est ce qui fait refaire
    un document."""
    # Trois lignes à 0,05 € : la TVA de chacune vaut 0,005 € et s'arrondirait à 1 centime, soit
    # 3 centimes en tout. Sur la base réunie de 0,15 €, elle en vaut 2.
    buckets = vat_buckets_from([(1_000, 5), (1_000, 5), (1_000, 5)])

    assert len(buckets) == 1
    assert buckets[0].base_cents == 15
    assert buckets[0].tax_cents == 2


def test_the_engine_reports_an_unusual_vat_rate_without_blocking_it() -> None:
    """200 points de base au lieu de 2000 — 2 % au lieu de 20 % — se voit avant l'envoi.

    Un avertissement et non un refus : la Corse et l'outre-mer connaissent d'autres taux, et
    bloquer une facture légitime serait pire qu'une coquille signalée.
    """
    takeoff = {
        "rooms": [
            {
                "name": "Salon",
                "skirting_ml": None,
                "faces": [
                    {
                        "face_id": 1,
                        "face_label": "A",
                        "kind": "wall",
                        "material": "peinture",
                        "net_area_m2": 10.0,
                    }
                ],
            }
        ],
        "warnings": [],
    }
    references = {"PEINT-MUR": PriceReference("PEINT-MUR", "Peinture", "m2", 2_400, 200)}

    plan = build_quote_lines(takeoff, references, {}, PricingOptions(include_skirting=False))

    assert plan.total_ht_cents == 24_000
    assert any("taux de TVA inhabituel" in message for message in plan.warnings)


def test_the_takeoff_warnings_travel_all_the_way_to_the_quote() -> None:
    """Un total est partiel dès que `warnings` n'est pas vide. Personne ne peut l'ignorer."""
    takeoff = {
        "rooms": [{"name": "Combles", "skirting_ml": None, "faces": []}],
        "warnings": ["surface au sol de « Combles » non établissable"],
    }

    plan = build_quote_lines(takeoff, {}, {}, PricingOptions(include_skirting=False))

    assert plan.warnings[0] == "surface au sol de « Combles » non établissable"
    assert plan.lines == ()
