"""Facture Factur-X : PDF/A-3 et XML CII (`docs/strategie-produit.md` §2).

Le test **relit réellement le fichier produit** plutôt que d'en mesurer la longueur : sans quoi un
PDF vide passerait. Le décodage est fait à la main (ASCII85 puis Flate), comme dans
`tests/test_export_pdf.py`, pour ne pas ajouter de dépendance.

Un mot sur ce qui est lisible dans un flux de contenu : les polices sont incorporées et
sous-ensemblées, donc les caractères accentués y sont réécrits en codes propres au sous-ensemble
(« décennale » devient « d\\001cennale »). Les assertions portent donc sur des fragments purement
ASCII — c'est suffisant pour prouver qu'une mention est imprimée, et c'est stable.
"""

import base64
import re
import zlib
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient

from app.services.facturx import (
    FACTURX_FILENAME,
    FACTURX_PROFILE,
    NOT_A_PLATFORM_NOTICE,
    DocumentLine,
    FacturXDocument,
    LegalMentions,
    Party,
    TaxBucket,
    VatAttestation,
    amount,
    build_cii_xml,
    build_icc_profile,
    build_xmp,
    euros,
    percent,
    quantity_text,
    render_facturx_pdf,
)
from tests.test_devis import create_quote, organization_of
from tests.test_takeoff_api import build_room

VENDEUR = Party(
    name="SARL Dupont Rénovation",
    siret="12345678900012",
    vat_number="FR40123456789",
    legal_form="SARL",
    share_capital_cents=1_000_010,
    rcs="Versailles B 123 456 789",
    address_line1="12 rue des Lilas",
    postal_code="78000",
    city="Versailles",
    country="France",
    email="contact@dupont.fr",
    phone="01 23 45 67 89",
)
CLIENT = Party(
    name="Monsieur Martin",
    address_line1="3 allee du Parc",
    postal_code="78000",
    city="Versailles",
)
LIGNES = (
    DocumentLine(
        1, "Faience murale — Salle de bains — mur B", "m2",
        Decimal("12.500"), 8_500, 1_000, 106_250,
    ),
    DocumentLine(
        2, "Isolation thermique — Salon — mur C", "m2",
        Decimal("10.000"), 6_800, 550, 68_000,
    ),
)
TAXES = (TaxBucket(550, 68_000, 3_740), TaxBucket(1_000, 106_250, 10_625))
MENTIONS = LegalMentions(
    decennial_insurer="AXA Construction",
    decennial_policy_number="P-99887",
    decennial_coverage_area="France metropolitaine",
    payment_terms="Paiement a 30 jours par virement.",
    late_penalty_rate_bp=1_050,
    recovery_indemnity_cents=4_000,
    mediator_name="Mediation de la consommation BTP",
    mediator_url="https://exemple.fr/mediation",
    is_consumer=True,
    vat_attestation=VatAttestation(
        over_two_years=True,
        premises_use="habitation",
        signatory="Monsieur Martin",
        signed_at=datetime(2026, 8, 1, tzinfo=UTC),
    ),
)


def document(kind: str = "invoice", **overrides: Any) -> FacturXDocument:
    base: dict[str, Any] = {
        "kind": kind,
        "number": "FAC-2026-0001" if kind == "invoice" else "DEV-2026-0001",
        "issued_at": datetime(2026, 8, 7, tzinfo=UTC),
        "seller": VENDEUR,
        "buyer": CLIENT,
        "lines": LIGNES,
        "taxes": TAXES,
        "total_ht_cents": 174_250,
        "total_tva_cents": 14_365,
        "total_ttc_cents": 188_615,
        "mentions": MENTIONS,
        "due_date": datetime(2026, 9, 6, tzinfo=UTC),
        "valid_until": datetime(2026, 11, 5, tzinfo=UTC),
        "site_address": ("Chantier Martin", "3 allee du Parc", "78000 Versailles"),
        "project_name": "Renovation salle de bains",
    }
    return FacturXDocument(**{**base, **overrides})


def printed_text(pdf: bytes) -> bytes:
    """Concatène les flux de contenu décodés — le texte réellement dessiné sur les pages."""
    decoded = b""
    for raw in re.findall(rb"stream\r?\n(.*?)endstream", pdf, re.S):
        body = raw.strip(b"\r\n")
        for decode in (
            lambda blob: zlib.decompress(base64.a85decode(blob, adobe=True)),
            zlib.decompress,
        ):
            try:
                decoded += decode(body)
                break
            except Exception:  # un flux binaire (police, profil ICC) n'est pas du texte
                continue
    return decoded


def embedded_xml(pdf: bytes) -> bytes:
    """Le fichier attaché, relu depuis le PDF. Il n'est pas compressé, précisément pour ça."""
    start = pdf.find(b'<?xml version="1.0" encoding="UTF-8"?>')
    assert start != -1, "aucun XML embarqué dans le PDF"
    end = pdf.find(b"</rsm:CrossIndustryInvoice>", start)
    assert end != -1, "le XML embarqué est tronqué"
    return pdf[start : end + len(b"</rsm:CrossIndustryInvoice>")]


# --- Formatage des montants -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cents", "attendu"),
    [(0, "0.00"), (5, "0.05"), (106_250, "1062.50"), (-2_500, "-25.00")],
)
def test_an_amount_is_written_from_integer_cents(cents: int, attendu: str) -> None:
    """Aucun flottant sur le chemin : `divmod` sur des entiers, jamais `cents / 100`."""
    assert amount(cents) == attendu


def test_the_human_readable_amount_is_french() -> None:
    assert euros(1_234_567) == "12 345,67 €"
    assert euros(4_000) == "40,00 €"
    assert percent(550) == "5,5 %"
    assert percent(2_000) == "20 %"
    assert quantity_text(Decimal("12.500")) == "12,5"
    assert quantity_text(Decimal("11.310")) == "11,31"


# --- XML CII --------------------------------------------------------------------------------------


def test_the_xml_declares_the_facturx_profile_and_the_invoice_type() -> None:
    xml = build_cii_xml(document()).decode("utf-8")

    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert f"<ram:ID>{FACTURX_PROFILE}</ram:ID>" in xml
    # 380 = facture commerciale au sens de l'UNTDID 1001.
    assert "<ram:TypeCode>380</ram:TypeCode>" in xml
    assert '<udt:DateTimeString format="102">20260807</udt:DateTimeString>' in xml
    assert "<ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>" in xml


def test_the_xml_carries_the_full_legal_identity_of_the_seller() -> None:
    """Sans SIRET ni numéro de TVA structurés, aucune plateforme ne sait exploiter le fichier."""
    xml = build_cii_xml(document()).decode("utf-8")

    assert "<ram:Name>SARL Dupont Rénovation</ram:Name>" in xml
    # schemeID 0002 = SIRET dans la liste ISO 6523 retenue par Factur-X.
    assert '<ram:ID schemeID="0002">12345678900012</ram:ID>' in xml
    assert '<ram:ID schemeID="VA">FR40123456789</ram:ID>' in xml
    assert "<ram:CountryID>FR</ram:CountryID>" in xml


def test_the_xml_breaks_the_vat_down_by_rate() -> None:
    """La rénovation mélange 5,5 %, 10 % et 20 % : un taux global serait faux le plus souvent."""
    xml = build_cii_xml(document()).decode("utf-8")

    assert xml.count("<ram:ApplicableTradeTax>") == 2
    assert "<ram:RateApplicablePercent>5.50</ram:RateApplicablePercent>" in xml
    assert "<ram:RateApplicablePercent>10.00</ram:RateApplicablePercent>" in xml
    assert "<ram:BasisAmount>680.00</ram:BasisAmount>" in xml
    assert "<ram:CalculatedAmount>37.40</ram:CalculatedAmount>" in xml
    assert "<ram:CategoryCode>S</ram:CategoryCode>" in xml


def test_the_xml_totals_match_the_document() -> None:
    xml = build_cii_xml(document()).decode("utf-8")

    assert "<ram:LineTotalAmount>1742.50</ram:LineTotalAmount>" in xml
    assert "<ram:TaxBasisTotalAmount>1742.50</ram:TaxBasisTotalAmount>" in xml
    assert '<ram:TaxTotalAmount currencyID="EUR">143.65</ram:TaxTotalAmount>' in xml
    assert "<ram:GrandTotalAmount>1886.15</ram:GrandTotalAmount>" in xml
    assert "<ram:DuePayableAmount>1886.15</ram:DuePayableAmount>" in xml


def test_the_settlement_block_follows_the_cii_sequence() -> None:
    """Le schéma CII est une **séquence** : un élément placé avant son rang invalide le fichier."""
    xml = build_cii_xml(document()).decode("utf-8")
    settlement = xml.split("<ram:ApplicableHeaderTradeSettlement>", 1)[1]

    ordre = [
        settlement.index("<ram:InvoiceCurrencyCode>"),
        settlement.index("<ram:ApplicableTradeTax>"),
        settlement.index("<ram:SpecifiedTradePaymentTerms>"),
        settlement.index("<ram:SpecifiedTradeSettlementHeaderMonetarySummation>"),
    ]
    assert ordre == sorted(ordre)


def test_the_site_address_is_the_delivery_place() -> None:
    """L'adresse du chantier détermine le taux : structurée, et non noyée dans une note."""
    xml = build_cii_xml(document()).decode("utf-8")

    assert "<ram:ShipToTradeParty>" in xml
    assert "<ram:Name>Chantier Martin</ram:Name>" in xml
    assert "<ram:LineOne>3 allee du Parc</ram:LineOne>" in xml


def test_the_xml_says_we_are_not_a_certified_platform() -> None:
    """La mention voyage avec le fichier, pas seulement avec l'interface qui l'a produit."""
    xml = build_cii_xml(document()).decode("utf-8")

    assert NOT_A_PLATFORM_NOTICE in xml
    assert "plateforme de dématérialisation agréée" in xml


def test_a_quote_never_produces_an_invoice_xml() -> None:
    """La norme EN 16931 n'a pas de code de type pour un devis.

    En fabriquer un à partir d'un document non exigible créerait une facture que personne n'a
    émise — c'est un refus, pas une approximation.
    """
    with pytest.raises(ValueError, match="devis"):
        build_cii_xml(document(kind="quote", number="DEV-2026-0001"))


# --- Profil ICC -----------------------------------------------------------------------------------


def test_the_icc_profile_is_structurally_valid() -> None:
    """PDF/A exige une intention de sortie **avec** profil incorporé, sans rien télécharger."""
    profile = build_icc_profile()

    assert int.from_bytes(profile[0:4], "big") == len(profile)
    assert profile[36:40] == b"acsp"
    assert profile[12:16] == b"mntr"
    assert profile[16:20] == b"RGB "
    assert profile[20:24] == b"XYZ "
    assert int.from_bytes(profile[128:132], "big") == 9
    # Chaque étiquette pointe dans le fichier, et son bloc y tient entièrement.
    for index in range(9):
        offset_at = 132 + index * 12
        start = int.from_bytes(profile[offset_at + 4 : offset_at + 8], "big")
        size = int.from_bytes(profile[offset_at + 8 : offset_at + 12], "big")
        assert start >= 128 and start + size <= len(profile)


def test_the_icc_profile_is_identical_from_one_call_to_the_next() -> None:
    """Deux exports du même document doivent donner le même fichier, à la pièce jointe près."""
    assert build_icc_profile() == build_icc_profile()


# --- XMP ------------------------------------------------------------------------------------------


def test_the_xmp_announces_pdfa_3b_and_facturx() -> None:
    packet = build_xmp(document(), with_attachment=True).decode("utf-8")

    assert "<pdfaid:part>3</pdfaid:part>" in packet
    assert "<pdfaid:conformance>B</pdfaid:conformance>" in packet
    assert f"<fx:DocumentFileName>{FACTURX_FILENAME}</fx:DocumentFileName>" in packet
    assert "<fx:DocumentType>INVOICE</fx:DocumentType>" in packet


def test_the_xmp_of_a_quote_claims_no_facturx_attachment() -> None:
    """Annoncer une facture structurée absente ferait échouer tout lecteur qui la cherche."""
    packet = build_xmp(document(kind="quote"), with_attachment=False).decode("utf-8")

    assert "<pdfaid:part>3</pdfaid:part>" in packet
    assert "fx:DocumentFileName" not in packet


# --- PDF ------------------------------------------------------------------------------------------


def test_the_invoice_pdf_is_a_pdfa3_carrying_its_xml() -> None:
    pdf = render_facturx_pdf(document())

    assert pdf.startswith(b"%PDF-1.7")
    # Les trois entrées indispensables : sans l'une d'elles, ce n'est plus une facture Factur-X.
    assert b"/AF " in pdf
    assert b"/AFRelationship /Data" in pdf
    assert b"/EmbeddedFiles" in pdf
    assert b"/OutputIntents" in pdf
    assert b"GTS_PDFA1" in pdf
    assert b"pdfaid:part" in pdf
    assert embedded_xml(pdf) == build_cii_xml(document())


def test_the_embedded_file_is_declared_as_xml() -> None:
    """`/` doit être échappé dans un nom PDF : `/text#2Fxml`, jamais `/text/xml`."""
    pdf = render_facturx_pdf(document())

    assert b"/text#2Fxml" in pdf
    assert b"(factur-x.xml)" in pdf


def test_a_quote_pdf_carries_no_xml_but_stays_pdfa() -> None:
    pdf = render_facturx_pdf(document(kind="quote", number="DEV-2026-0001"))

    assert b"factur-x.xml" not in pdf
    assert b"/AFRelationship" not in pdf
    assert b"/OutputIntents" in pdf
    assert b"pdfaid:part" in pdf


def test_every_font_is_embedded() -> None:
    """PDF/A interdit les quatorze polices standard : Helvetica ne doit pas même être déclarée."""
    pdf = render_facturx_pdf(document())

    assert b"/FontFile2" in pdf
    assert b"Helvetica" not in pdf
    # La table ToUnicode rend le texte copiable-collable, ce qui est aussi une exigence pratique
    # pour l'expert-comptable qui relira la facture.
    assert b"/ToUnicode" in pdf


def test_the_pdf_prints_every_mandatory_mention() -> None:
    """Un devis de bâtiment sans ces mentions est inopposable (`strategie-produit.md` §2)."""
    text = printed_text(render_facturx_pdf(document()))

    assert b"MENTIONS OBLIGATOIRES" in text
    assert b"AXA Construction" in text
    assert b"P-99887" in text
    assert b"France metropolitaine" in text
    assert b"Paiement a 30 jours par virement." in text
    assert b"10,5 %" in text
    assert b"40,00" in text
    assert b"D. 441-5" in text
    assert b"Mediation de la consommation BTP" in text
    assert b"CERFA 13947" in text
    assert b"habitation" in text


def test_the_pdf_prints_the_identity_the_totals_and_the_site() -> None:
    text = printed_text(render_facturx_pdf(document()))

    assert b"FACTURE" in text
    assert b"FAC-2026-0001" in text
    assert b"12345678900012" in text
    assert b"ADRESSE DU CHANTIER" in text
    assert b"Total TTC" in text
    assert b"1 886,15" in text
    assert b"5,5 %" in text and b"10 %" in text


def test_the_pdf_says_on_every_page_that_we_transmit_nothing() -> None:
    """C'est le point où un utilisateur pourrait croire que l'envoi est fait."""
    text = printed_text(render_facturx_pdf(document()))

    assert b"plateforme de d" in text
    assert b"transmet" in text
    assert b"administration" in text


def test_a_missing_decennial_insurance_is_shouted_not_hidden() -> None:
    """Le silence serait pire : l'artisan doit voir le trou avant son client."""
    text = printed_text(render_facturx_pdf(document(mentions=LegalMentions())))

    assert b"NON RENSEIGN" in text


def test_only_a_quote_carries_the_acceptance_box() -> None:
    devis = printed_text(render_facturx_pdf(document(kind="quote", number="DEV-2026-0001")))
    facture = printed_text(render_facturx_pdf(document()))

    assert b"Bon pour accord" in devis
    assert b"Bon pour accord" not in facture


def test_a_long_document_spills_over_and_repeats_the_table_header() -> None:
    """Un chantier de douze pièces ne tient pas sur une page, et rien ne doit être tronqué."""
    lignes = tuple(
        DocumentLine(
            position=index + 1,
            # Libellé en un seul mot : le retour à la ligne automatique le couperait sinon, et
            # l'assertion ne saurait plus distinguer un texte tronqué d'un texte replié.
            label=f"Poste-{index + 1}",
            unit="m2",
            quantity=Decimal("10.000"),
            unit_price_cents=2_400,
            vat_rate_bp=1_000,
            total_ht_cents=24_000,
        )
        for index in range(160)
    )
    pdf = render_facturx_pdf(document(lines=lignes))
    text = printed_text(pdf)

    assert pdf.count(b"/Type /Page\n") >= 3
    # « Désignation » : le « é » est réécrit par le sous-ensemble de police, on lit la suite.
    assert text.count(b"signation") >= 3
    assert b"Poste-160" in text


# --- Chemin complet depuis l'API ------------------------------------------------------------------


async def _invoiced_quote(client: AsyncClient) -> dict[str, Any]:
    project, _room = await build_room(client)
    organization_id = await organization_of(client)
    await client.patch(
        f"/api/organizations/{organization_id}",
        json={
            "siret": "12345678900012",
            "legal_form": "SARL",
            "share_capital_cents": 1_000_010,
            "vat_number": "FR40123456789",
            "address_line1": "12 rue des Lilas",
            "postal_code": "78000",
            "city": "Versailles",
            "country": "France",
            "decennial_insurer": "AXA Construction",
            "decennial_policy_number": "P-99887",
            "decennial_coverage_area": "France metropolitaine",
        },
    )
    quote = await create_quote(
        client,
        project["id"],
        vat_attestation_required=True,
        vat_attestation_over_two_years=True,
        vat_attestation_premises_use="habitation",
        site_address_line1="3 allee du Parc",
        site_city="Versailles",
    )
    await client.post(f"/api/quotes/{quote['id']}/issue")
    await client.patch(f"/api/quotes/{quote['id']}", json={"status": "accepted"})
    return dict((await client.post(f"/api/quotes/{quote['id']}/invoice")).json())


async def test_the_api_serves_a_real_facturx_invoice(auth_client: AsyncClient) -> None:
    facture = await _invoiced_quote(auth_client)

    response = await auth_client.get(f"/api/quotes/{facture['id']}/invoice.pdf")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    pdf = response.content
    assert pdf.startswith(b"%PDF-1.7")
    xml = embedded_xml(pdf).decode("utf-8")
    assert f"<ram:ID>{facture['invoice_number']}</ram:ID>" in xml
    # L'identité de l'entreprise vient d'`organization`, comme l'exige le lot.
    assert '<ram:ID schemeID="0002">12345678900012</ram:ID>' in xml
    text = printed_text(pdf)
    assert b"AXA Construction" in text
    assert facture["invoice_number"].encode() in text


async def test_the_api_serves_the_xml_alone(auth_client: AsyncClient) -> None:
    """Pour l'artisan qui alimente déjà une plateforme avec ses propres outils."""
    facture = await _invoiced_quote(auth_client)

    response = await auth_client.get(f"/api/quotes/{facture['id']}/invoice.xml")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/xml"
    assert response.content.startswith(b'<?xml version="1.0" encoding="UTF-8"?>')
    assert b"CrossIndustryInvoice" in response.content


async def test_a_draft_has_no_printable_document(auth_client: AsyncClient) -> None:
    """Sans numéro, il n'y a rien à imprimer : ce serait un document sans identité."""
    project, _room = await build_room(auth_client)
    quote = await create_quote(auth_client, project["id"])

    response = await auth_client.get(f"/api/quotes/{quote['id']}/pdf")

    assert response.status_code == 409, response.text


async def test_the_quote_pdf_is_served_once_issued(auth_client: AsyncClient) -> None:
    project, _room = await build_room(auth_client)
    quote = await create_quote(auth_client, project["id"])
    emis = (await auth_client.post(f"/api/quotes/{quote['id']}/issue")).json()

    response = await auth_client.get(f"/api/quotes/{quote['id']}/pdf")

    assert response.status_code == 200, response.text
    text = printed_text(response.content)
    assert b"DEVIS" in text
    assert emis["number"].encode() in text
    assert b"factur-x.xml" not in response.content
