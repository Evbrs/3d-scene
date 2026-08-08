"""Devis et facture Factur-X : PDF/A-3 lisible, avec le XML CII embarqué.

`docs/strategie-produit.md` §2. La facture électronique devient obligatoire **en réception** le
1er septembre 2026 et **en émission** pour les TPE et PME le 1er septembre 2027. Factur-X est un
PDF/A-3 portant un fichier XML attaché : il se génère entièrement ici, avec reportlab, sans
service externe ni clé d'API — ce qui satisfait la contrainte du propriétaire et fait de cette
échéance une fenêtre commerciale plutôt qu'une dépendance.

> **Limite assumée, écrite dans le PDF lui-même et répétée ici.** Nous produisons un fichier au
> format réglementaire. Nous **ne sommes pas** une plateforme de dématérialisation agréée et nous
> ne transmettons **rien** à l'administration. L'artisan transmet le fichier avec son propre
> outil. Prétendre le contraire serait faux et juridiquement dangereux.

Trois décisions méritent d'être expliquées.

**Le XML n'est embarqué que dans les factures.** Un devis n'est pas une facture : la norme
EN 16931 n'admet pas de code de type « proforma », et attacher un XML de facture à un devis
fabriquerait une facture que personne n'a émise. Le devis sort donc en PDF simple — même mise en
page, mêmes mentions, sans pièce jointe.

**Les polices sont embarquées.** PDF/A interdit les polices non incorporées, ce qui exclut les
quatorze polices standard du PDF (Helvetica en tête). On embarque Bitstream Vera, livrée avec
reportlab sous une licence qui l'autorise.

**Le profil colorimétrique est construit ici.** PDF/A exige un `OutputIntent` avec un profil ICC
incorporé. Aucun profil n'est distribuable depuis le dépôt et rien ne doit être téléchargé : le
module fabrique donc un profil ICC v2 matriciel aux primaires sRGB, dont la courbe de transfert
est approchée par un gamma 2,2. C'est une approximation, elle est nommée comme telle dans le
`/Info` de l'intention de sortie, et elle rend le document structurellement conforme là où
l'absence de profil le rendait invalide d'emblée.
"""

import io
import struct
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import reportlab
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfdoc, pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdfcanvas

from app.models.billing import PriceUnit, Quote, QuoteLine
from app.models.organization import Organization
from app.services.pricing import vat_buckets_from

# --- Constantes du format -----------------------------------------------------------------------

FACTURX_FILENAME = "factur-x.xml"
# Profil BASIC WL : en-tête complet, ventilation de TVA par taux, pas de détail de ligne dans le
# XML. C'est le premier profil qui porte le taux par taux, ce qu'exige la rénovation — un même
# chantier mélange 5,5 %, 10 % et 20 %.
FACTURX_PROFILE = "urn:factur-x.eu:1p0:basicwl"
FACTURX_CONFORMANCE = "BASIC WL"
FACTURX_VERSION = "1.0"
# 380 = facture commerciale (UNTDID 1001). Le devis n'a pas de code EN 16931 : il n'a pas de XML.
INVOICE_TYPE_CODE = "380"
CURRENCY = "EUR"
# « S » = régime de TVA de droit commun (standard rate) au sens de l'EN 16931.
VAT_CATEGORY_STANDARD = "S"

NS = {
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
    "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
}

# La phrase qui dit ce que nous ne sommes pas. Elle est imprimée sur **chaque** document, devis
# comme facture : c'est le point où un utilisateur pourrait croire que l'envoi est fait.
NOT_A_PLATFORM_NOTICE = (
    "Document généré au format réglementaire Factur-X. L'éditeur de ce logiciel n'est pas une "
    "plateforme de dématérialisation agréée et ne transmet aucune donnée à l'administration : "
    "la transmission reste à la charge de l'émetteur, avec l'outil de son choix."
)

# --- Constantes de mise en page -------------------------------------------------------------------

PAGE_SIZE = A4
PAGE_WIDTH: float = PAGE_SIZE[0]
PAGE_HEIGHT: float = PAGE_SIZE[1]
MARGIN: float = 18 * mm
CONTENT_WIDTH: float = PAGE_WIDTH - 2 * MARGIN
# Hauteur réservée au pied de page : numéro de planche et mention de non-agrément.
FOOTER_HEIGHT: float = 20 * mm

FONT_REGULAR = "FacturxSans"
FONT_BOLD = "FacturxSans-Bold"

INK = colors.HexColor("#10365e")
TEXT = colors.HexColor("#1f2933")
MUTED = colors.HexColor("#4a5568")
RULE = colors.HexColor("#b8c4d0")
BAND = colors.HexColor("#e8f1fb")

# Colonnes du tableau des lignes, en fraction de la largeur utile. Le libellé prend tout ce qui
# reste : c'est lui qui porte « Faïence murale — Salle de bains — mur B », et le tronquer rendrait
# le devis invérifiable contre les élévations.
COLUMN_RATIOS = (0.06, 0.44, 0.08, 0.10, 0.12, 0.08, 0.12)
COLUMN_HEADERS = ("N°", "Désignation", "Unité", "Quantité", "P.U. HT", "TVA", "Total HT")

LINE_HEIGHT = 11.0
BODY_FONT_SIZE = 8.0
SMALL_FONT_SIZE = 6.5


# --- Modèle du document (aucune dépendance à la base) ---------------------------------------------


@dataclass(frozen=True)
class Party:
    """Une partie du document : l'entreprise qui émet, ou le client qui reçoit."""

    name: str
    siret: str | None = None
    vat_number: str | None = None
    legal_form: str | None = None
    share_capital_cents: int | None = None
    rcs: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    postal_code: str | None = None
    city: str | None = None
    country: str | None = None
    email: str | None = None
    phone: str | None = None


@dataclass(frozen=True)
class DocumentLine:
    """Une ligne imprimée. Ce sont des copies : rien n'est relu depuis le barème."""

    position: int
    label: str
    unit: str
    quantity: Decimal
    unit_price_cents: int
    vat_rate_bp: int
    total_ht_cents: int


@dataclass(frozen=True)
class TaxBucket:
    rate_bp: int
    base_cents: int
    tax_cents: int


@dataclass(frozen=True)
class VatAttestation:
    """L'attestation du client qui ouvre le taux réduit.

    Depuis le 16 février 2025 elle remplace les CERFA 13947 et 13948 et se porte sur le devis ou
    la facture. Son absence expose l'artisan à un redressement sur la différence de taux : la
    faire figurer est un service, et son oubli mérite une mention explicite plutôt qu'un silence.
    """

    over_two_years: bool | None = None
    premises_use: str | None = None
    signatory: str | None = None
    signed_at: datetime | None = None


@dataclass(frozen=True)
class LegalMentions:
    """Les mentions sans lesquelles un document de bâtiment est inopposable (§2 de la stratégie)."""

    decennial_insurer: str | None = None
    decennial_policy_number: str | None = None
    decennial_coverage_area: str | None = None
    payment_terms: str | None = None
    late_penalty_rate_bp: int = 0
    recovery_indemnity_cents: int = 0
    mediator_name: str | None = None
    mediator_url: str | None = None
    is_consumer: bool = True
    vat_attestation: VatAttestation | None = None


@dataclass(frozen=True)
class FacturXDocument:
    """Tout ce qu'il faut pour produire le PDF et le XML, sans jamais relire la base."""

    # "quote" ou "invoice" : seul le second porte un XML CII.
    kind: str
    number: str
    issued_at: datetime
    seller: Party
    buyer: Party
    lines: tuple[DocumentLine, ...]
    taxes: tuple[TaxBucket, ...]
    total_ht_cents: int
    total_tva_cents: int
    total_ttc_cents: int
    mentions: LegalMentions = field(default_factory=LegalMentions)
    valid_until: datetime | None = None
    due_date: datetime | None = None
    site_address: tuple[str, ...] = ()
    project_name: str | None = None
    notes: str | None = None

    @property
    def is_invoice(self) -> bool:
        return self.kind == "invoice"

    @property
    def title(self) -> str:
        return "FACTURE" if self.is_invoice else "DEVIS"


# --- Formatage ------------------------------------------------------------------------------------


def amount(cents: int) -> str:
    """Montant décimal à deux chiffres, à partir de centimes entiers. Jamais un flottant."""
    quotient, remainder = divmod(abs(cents), 100)
    return f"{'-' if cents < 0 else ''}{quotient}.{remainder:02d}"


def euros(cents: int) -> str:
    """Montant à la française : « 1 234,56 € », séparateur de milliers et virgule décimale.

    Espace ordinaire et non insécable : le PDF ne coupe jamais une ligne au milieu d'un
    nombre, et l'insécable rendrait le fichier pénible à relire dans un éditeur de texte.
    """
    quotient, remainder = divmod(abs(cents), 100)
    grouped = f"{quotient:,}".replace(",", " ")
    return f"{'-' if cents < 0 else ''}{grouped},{remainder:02d} €"


def percent(rate_bp: int) -> str:
    """« 5,5 % » et non « 5.50 % » : le document est français et il est lu par un comptable."""
    return f"{rate_bp / 100:g}".replace(".", ",") + " %"


def quantity_text(value: Decimal) -> str:
    """Quantité au millième, zéros de queue retirés, virgule décimale."""
    text = format(value.normalize(), "f")
    return text.replace(".", ",")


def _iso_date(moment: datetime) -> str:
    """Date au format 102 de l'UNTDID (AAAAMMJJ), tel que l'attend le CII."""
    return moment.strftime("%Y%m%d")


# --- XML CII --------------------------------------------------------------------------------------


def _child(parent: ET.Element, tag: str, text: str | None = None, **attributes: str) -> ET.Element:
    element = ET.SubElement(parent, tag, dict(attributes))
    if text is not None:
        element.text = text
    return element


def _postal_address(parent: ET.Element, party: Party) -> None:
    """Adresse postale CII. `CountryID` est le seul champ obligatoire du bloc."""
    address = _child(parent, f"{{{NS['ram']}}}PostalTradeAddress")
    if party.postal_code:
        _child(address, f"{{{NS['ram']}}}PostcodeCode", party.postal_code)
    if party.address_line1:
        _child(address, f"{{{NS['ram']}}}LineOne", party.address_line1)
    if party.address_line2:
        _child(address, f"{{{NS['ram']}}}LineTwo", party.address_line2)
    if party.city:
        _child(address, f"{{{NS['ram']}}}CityName", party.city)
    _child(address, f"{{{NS['ram']}}}CountryID", _country_code(party.country))


def _country_code(country: str | None) -> str:
    """Code pays ISO 3166-1 alpha-2, replié sur `FR`.

    Le repli est assumé : le champ est obligatoire dans le CII, et une facture d'artisan français
    sans pays saisi est française. Un pays écrit en toutes lettres est réduit à ses deux premières
    lettres majuscules — approximation grossière, mais un code absent invaliderait le fichier.
    """
    if not country:
        return "FR"
    stripped = "".join(
        char
        for char in unicodedata.normalize("NFKD", country)
        if not unicodedata.combining(char)
    ).strip()
    if len(stripped) == 2:
        return stripped.upper()
    return {"france": "FR", "belgique": "BE", "suisse": "CH", "luxembourg": "LU"}.get(
        stripped.lower(), stripped[:2].upper() or "FR"
    )


def _trade_party(parent: ET.Element, tag: str, party: Party, *, with_registration: bool) -> None:
    node = _child(parent, tag)
    _child(node, f"{{{NS['ram']}}}Name", party.name)
    if with_registration and party.siret:
        legal = _child(node, f"{{{NS['ram']}}}SpecifiedLegalOrganization")
        # schemeID 0002 = SIRET dans la liste ISO 6523 retenue par Factur-X.
        _child(legal, f"{{{NS['ram']}}}ID", party.siret, schemeID="0002")
    _postal_address(node, party)
    if party.vat_number:
        registration = _child(node, f"{{{NS['ram']}}}SpecifiedTaxRegistration")
        _child(registration, f"{{{NS['ram']}}}ID", party.vat_number, schemeID="VA")


def build_cii_xml(document: FacturXDocument) -> bytes:
    """XML CII du profil BASIC WL, en UTF-8, sans dépendance externe.

    L'ordre des éléments n'est pas un choix de style : le schéma CII est une **séquence**, un
    élément placé avant son rang rend le fichier invalide pour tout lecteur qui le valide.

    Lève `ValueError` sur un devis : la norme EN 16931 n'a pas de code de type pour un document
    non exigible, et fabriquer un XML de facture à partir d'un devis créerait une facture que
    personne n'a émise.
    """
    if not document.is_invoice:
        raise ValueError(
            "Factur-X ne décrit que des factures : un devis sort en PDF simple, sans XML embarqué"
        )

    for prefix, uri in NS.items():
        ET.register_namespace(prefix, uri)

    root = ET.Element(f"{{{NS['rsm']}}}CrossIndustryInvoice")

    context = _child(root, f"{{{NS['rsm']}}}ExchangedDocumentContext")
    guideline = _child(context, f"{{{NS['ram']}}}GuidelineSpecifiedDocumentContextParameter")
    _child(guideline, f"{{{NS['ram']}}}ID", FACTURX_PROFILE)

    header = _child(root, f"{{{NS['rsm']}}}ExchangedDocument")
    _child(header, f"{{{NS['ram']}}}ID", document.number)
    _child(header, f"{{{NS['ram']}}}TypeCode", INVOICE_TYPE_CODE)
    issue = _child(header, f"{{{NS['ram']}}}IssueDateTime")
    _child(issue, f"{{{NS['udt']}}}DateTimeString", _iso_date(document.issued_at), format="102")
    note = _child(header, f"{{{NS['ram']}}}IncludedNote")
    _child(note, f"{{{NS['ram']}}}Content", NOT_A_PLATFORM_NOTICE)

    transaction = _child(root, f"{{{NS['rsm']}}}SupplyChainTradeTransaction")

    agreement = _child(transaction, f"{{{NS['ram']}}}ApplicableHeaderTradeAgreement")
    _trade_party(
        agreement, f"{{{NS['ram']}}}SellerTradeParty", document.seller, with_registration=True
    )
    _trade_party(
        agreement, f"{{{NS['ram']}}}BuyerTradeParty", document.buyer, with_registration=False
    )

    delivery = _child(transaction, f"{{{NS['ram']}}}ApplicableHeaderTradeDelivery")
    if document.site_address:
        # L'adresse du chantier est le lieu de livraison de la prestation. Elle est distincte de
        # celle du client dans le cas courant du bailleur, et c'est elle qui détermine le taux.
        ship_to = _child(delivery, f"{{{NS['ram']}}}ShipToTradeParty")
        _child(ship_to, f"{{{NS['ram']}}}Name", document.site_address[0])
        address = _child(ship_to, f"{{{NS['ram']}}}PostalTradeAddress")
        for tag, line in zip(("LineOne", "LineTwo"), document.site_address[1:], strict=False):
            _child(address, f"{{{NS['ram']}}}{tag}", line)
        _child(address, f"{{{NS['ram']}}}CountryID", _country_code(document.buyer.country))

    settlement = _child(transaction, f"{{{NS['ram']}}}ApplicableHeaderTradeSettlement")
    _child(settlement, f"{{{NS['ram']}}}InvoiceCurrencyCode", CURRENCY)
    for bucket in document.taxes:
        tax = _child(settlement, f"{{{NS['ram']}}}ApplicableTradeTax")
        _child(tax, f"{{{NS['ram']}}}CalculatedAmount", amount(bucket.tax_cents))
        _child(tax, f"{{{NS['ram']}}}TypeCode", "VAT")
        _child(tax, f"{{{NS['ram']}}}BasisAmount", amount(bucket.base_cents))
        _child(tax, f"{{{NS['ram']}}}CategoryCode", VAT_CATEGORY_STANDARD)
        _child(tax, f"{{{NS['ram']}}}RateApplicablePercent", _rate_text(bucket.rate_bp))

    terms = _child(settlement, f"{{{NS['ram']}}}SpecifiedTradePaymentTerms")
    if document.mentions.payment_terms:
        _child(terms, f"{{{NS['ram']}}}Description", document.mentions.payment_terms)
    if document.due_date is not None:
        due = _child(terms, f"{{{NS['ram']}}}DueDateDateTime")
        _child(due, f"{{{NS['udt']}}}DateTimeString", _iso_date(document.due_date), format="102")

    summary = _child(
        settlement, f"{{{NS['ram']}}}SpecifiedTradeSettlementHeaderMonetarySummation"
    )
    _child(summary, f"{{{NS['ram']}}}LineTotalAmount", amount(document.total_ht_cents))
    _child(summary, f"{{{NS['ram']}}}TaxBasisTotalAmount", amount(document.total_ht_cents))
    _child(
        summary,
        f"{{{NS['ram']}}}TaxTotalAmount",
        amount(document.total_tva_cents),
        currencyID=CURRENCY,
    )
    _child(summary, f"{{{NS['ram']}}}GrandTotalAmount", amount(document.total_ttc_cents))
    _child(summary, f"{{{NS['ram']}}}DuePayableAmount", amount(document.total_ttc_cents))

    # `xml_declaration=True` produirait une déclaration sans saut de ligne et sans garantie sur
    # les guillemets : on écrit la nôtre, puisque c'est un octet-à-octet que des validateurs
    # regardent.
    body = ET.tostring(root, encoding="unicode")
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + body.encode("utf-8")


def _rate_text(rate_bp: int) -> str:
    """Taux en pourcentage décimal : 550 → « 5.50 », 2000 → « 20.00 »."""
    quotient, remainder = divmod(rate_bp, 100)
    return f"{quotient}.{remainder:02d}"


# --- Profil ICC -----------------------------------------------------------------------------------


def _s15fixed16(value: float) -> bytes:
    return struct.pack(">i", round(value * 65536))


def _xyz_tag(x: float, y: float, z: float) -> bytes:
    return b"XYZ " + b"\x00" * 4 + _s15fixed16(x) + _s15fixed16(y) + _s15fixed16(z)


def _curve_tag(gamma: float) -> bytes:
    # `curv` à un seul point : la valeur est un u8Fixed8 interprété comme exposant.
    return b"curv" + b"\x00" * 4 + struct.pack(">I", 1) + struct.pack(">H", round(gamma * 256))


def _text_description_tag(text: str) -> bytes:
    """Type `desc` de l'ICC v2 : ASCII, puis deux blocs de traduction laissés vides."""
    ascii_text = text.encode("ascii", "replace") + b"\x00"
    return (
        b"desc"
        + b"\x00" * 4
        + struct.pack(">I", len(ascii_text))
        + ascii_text
        + struct.pack(">I", 0)
        + struct.pack(">I", 0)
        + struct.pack(">H", 0)
        + struct.pack(">B", 0)
        + b"\x00" * 67
    )


def _text_tag(text: str) -> bytes:
    return b"text" + b"\x00" * 4 + text.encode("ascii", "replace") + b"\x00"


def build_icc_profile() -> bytes:
    """Profil ICC v2 matriciel aux primaires sRGB, courbe approchée par un gamma 2,2.

    PDF/A exige une intention de sortie **avec** profil incorporé. Aucun profil n'est distribuable
    depuis ce dépôt et rien ne doit être téléchargé à l'exécution : on en construit donc un,
    déterministe et vérifiable octet par octet.

    L'approximation porte sur la courbe de transfert seule — les primaires et le point blanc sont
    ceux de l'sRGB adapté au D50, tels qu'ils figurent dans le profil de référence. Elle est
    annoncée dans le `/Info` de l'intention de sortie plutôt que passée sous silence.
    """
    tags: list[tuple[bytes, bytes]] = [
        (b"desc", _text_description_tag("sRGB approxime (gamma 2.2)")),
        (b"cprt", _text_tag("Profil genere par l'application, sans droits reserves.")),
        (b"wtpt", _xyz_tag(0.9642, 1.0, 0.8249)),
        (b"rXYZ", _xyz_tag(0.4360, 0.2225, 0.0139)),
        (b"gXYZ", _xyz_tag(0.3851, 0.7169, 0.0971)),
        (b"bXYZ", _xyz_tag(0.1431, 0.0606, 0.7141)),
        (b"rTRC", _curve_tag(2.2)),
        (b"gTRC", _curve_tag(2.2)),
        (b"bTRC", _curve_tag(2.2)),
    ]

    header_size = 128
    table_size = 4 + 12 * len(tags)
    offset = header_size + table_size
    entries = bytearray()
    payload = bytearray()
    for signature, data in tags:
        padding = (-len(data)) % 4
        entries += signature + struct.pack(">II", offset, len(data))
        payload += data + b"\x00" * padding
        offset += len(data) + padding

    total = header_size + table_size + len(payload)
    header = bytearray(header_size)
    struct.pack_into(">I", header, 0, total)
    header[8:12] = struct.pack(">I", 0x02400000)
    header[12:16] = b"mntr"
    header[16:20] = b"RGB "
    header[20:24] = b"XYZ "
    # Horodatage figé : le profil ne doit pas changer d'un appel à l'autre, sans quoi deux
    # exports du même document produiraient deux fichiers différents.
    header[24:36] = struct.pack(">6H", 2026, 1, 1, 0, 0, 0)
    header[36:40] = b"acsp"
    header[68:80] = _s15fixed16(0.9642) + _s15fixed16(1.0) + _s15fixed16(0.8249)

    return bytes(header) + struct.pack(">I", len(tags)) + bytes(entries) + bytes(payload)


# --- XMP ------------------------------------------------------------------------------------------


def build_xmp(document: FacturXDocument, *, with_attachment: bool) -> bytes:
    """Métadonnées XMP annonçant PDF/A-3B, et Factur-X quand un XML est joint.

    L'extension `fx:` est **obligatoire** : c'est elle qui dit à un lecteur qu'un des fichiers
    attachés est la facture structurée, et sous quel profil. Sans elle le PDF porte un XML que
    personne ne sait reconnaître.
    """
    facturx_block = (
        f"""
   <rdf:Description rdf:about="" xmlns:fx="urn:factur-x:pdfa:CrossIndustryDocument:invoice:1p0#">
    <fx:DocumentType>INVOICE</fx:DocumentType>
    <fx:DocumentFileName>{FACTURX_FILENAME}</fx:DocumentFileName>
    <fx:Version>{FACTURX_VERSION}</fx:Version>
    <fx:ConformanceLevel>{FACTURX_CONFORMANCE}</fx:ConformanceLevel>
   </rdf:Description>"""
        if with_attachment
        else ""
    )
    title = _xml_escape(f"{document.title} {document.number}")
    author = _xml_escape(document.seller.name)
    packet = f"""<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="" xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/">
   <pdfaid:part>3</pdfaid:part>
   <pdfaid:conformance>B</pdfaid:conformance>
  </rdf:Description>
  <rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">
   <dc:title><rdf:Alt><rdf:li xml:lang="x-default">{title}</rdf:li></rdf:Alt></dc:title>
   <dc:creator><rdf:Seq><rdf:li>{author}</rdf:li></rdf:Seq></dc:creator>
  </rdf:Description>{facturx_block}
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>"""
    return packet.encode("utf-8")


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


# --- Assemblage PDF/A-3 ---------------------------------------------------------------------------


_FONTS_REGISTERED = False


def _allow_catalog_keys(canvas: pdfcanvas.Canvas, *keys: str) -> None:
    """Autorise des entrées de catalogue que reportlab ne connaît pas.

    `PDFCatalog.format` n'écrit que les clés d'une liste blanche : `/AF` et `/OutputIntents`,
    postérieures à cette liste, étaient **silencieusement supprimées** — le PDF sortait sans
    intention de sortie et sans déclaration de fichier associé, donc ni PDF/A ni Factur-X, sans
    le moindre message. On étend la liste sur l'instance, jamais sur la classe : reportlab est
    partagé par tout le processus, y compris par l'export de plans.

    La liste des références (`__Refs__`) reste celle de la classe, ce qui laisse ces deux tableaux
    écrits en direct dans le catalogue plutôt qu'en objets indirects.
    """
    # `_doc` : reportlab n'expose aucune API publique sur le catalogue, et c'est le seul
    # point d'accroche possible pour les entrées que PDF/A exige.
    catalog = canvas._doc.Catalog
    known = list(getattr(catalog, "__NoDefault__", pdfdoc.PDFCatalog.__NoDefault__))
    catalog.__NoDefault__ = known + [key for key in keys if key not in known]


def register_fonts() -> None:
    """Incorpore Bitstream Vera, livrée avec reportlab.

    Idempotent : reportlab refuse un enregistrement en double, et le module peut être appelé une
    fois par requête. Les quatorze polices standard du PDF sont exclues par PDF/A, qui n'admet que
    des polices incorporées.
    """
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return
    fonts_dir = Path(reportlab.__file__).resolve().parent / "fonts"
    pdfmetrics.registerFont(TTFont(FONT_REGULAR, str(fonts_dir / "Vera.ttf")))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, str(fonts_dir / "VeraBd.ttf")))
    pdfmetrics.registerFontFamily(FONT_REGULAR, normal=FONT_REGULAR, bold=FONT_BOLD)
    _FONTS_REGISTERED = True


def _attach_facturx_xml(canvas: pdfcanvas.Canvas, xml_bytes: bytes) -> None:
    """Attache le XML en fichier associé au sens PDF/A-3.

    Trois choses sont nécessaires et aucune n'est facultative : l'entrée `/Names /EmbeddedFiles`
    qui rend la pièce jointe trouvable, le tableau `/AF` du catalogue qui la déclare *associée* au
    document, et la relation `/AFRelationship /Data` qui dit qu'elle est la donnée dont le PDF est
    la représentation lisible. Il en manque une et le fichier n'est plus une facture Factur-X,
    seulement un PDF avec une pièce jointe.

    Le flux n'est pas compressé : le XML fait quelques kilo-octets, et un fichier légal doit
    pouvoir être relu avec un éditeur de texte le jour où l'outil qui l'a produit n'existe plus.
    """
    # `_doc` : voir `_allow_catalog_keys`, reportlab n'a pas d'API d'attachement de fichier.
    document = canvas._doc
    modified = datetime.now(UTC).strftime("D:%Y%m%d%H%M%SZ")

    stream = pdfdoc.PDFStream(
        dictionary=pdfdoc.PDFDictionary(
            {
                "Type": pdfdoc.PDFName("EmbeddedFile"),
                # `/` doit être échappé dans un nom PDF : `PDFName` ne le fait pas, on écrit donc
                # la forme finale directement.
                "Subtype": "/text#2Fxml",
                "Params": pdfdoc.PDFDictionary(
                    {"ModDate": pdfdoc.PDFString(modified), "Size": len(xml_bytes)}
                ),
            }
        ),
        content=xml_bytes,
        filters=[],
    )
    stream_reference = document.Reference(stream)

    filespec = pdfdoc.PDFDictionary(
        {
            "Type": pdfdoc.PDFName("Filespec"),
            "F": pdfdoc.PDFString(FACTURX_FILENAME),
            "UF": pdfdoc.PDFString(FACTURX_FILENAME),
            "AFRelationship": pdfdoc.PDFName("Data"),
            "Desc": pdfdoc.PDFString("Facture electronique Factur-X (CII)"),
            "EF": pdfdoc.PDFDictionary({"F": stream_reference, "UF": stream_reference}),
        }
    )
    filespec_reference = document.Reference(filespec)

    canvas.setCatalogEntry(
        "Names",
        pdfdoc.PDFDictionary(
            {
                "EmbeddedFiles": pdfdoc.PDFDictionary(
                    {
                        "Names": pdfdoc.PDFArray(
                            [pdfdoc.PDFString(FACTURX_FILENAME), filespec_reference]
                        )
                    }
                )
            }
        ),
    )
    _allow_catalog_keys(canvas, "AF")
    canvas.setCatalogEntry("AF", pdfdoc.PDFArray([filespec_reference]))


def _declare_pdfa(canvas: pdfcanvas.Canvas, document: FacturXDocument, *, attached: bool) -> None:
    """Pose les métadonnées XMP et l'intention de sortie exigées par PDF/A."""
    pdf = canvas._doc

    metadata = pdfdoc.PDFStream(
        dictionary=pdfdoc.PDFDictionary(
            {"Type": pdfdoc.PDFName("Metadata"), "Subtype": pdfdoc.PDFName("XML")}
        ),
        content=build_xmp(document, with_attachment=attached),
        # Non compressé : c'est ce qu'attendent les validateurs, qui lisent ce flux avant tout
        # décodage de filtre.
        filters=[],
    )
    canvas.setCatalogEntry("Metadata", pdf.Reference(metadata))

    icc = pdfdoc.PDFStream(
        dictionary=pdfdoc.PDFDictionary({"N": 3}), content=build_icc_profile(), filters=[]
    )
    _allow_catalog_keys(canvas, "OutputIntents")
    canvas.setCatalogEntry(
        "OutputIntents",
        pdfdoc.PDFArray(
            [
                pdfdoc.PDFDictionary(
                    {
                        "Type": pdfdoc.PDFName("OutputIntent"),
                        # `GTS_PDFA1` est le sous-type de toutes les parties de PDF/A, y compris
                        # la troisième : le « 1 » désigne la famille, pas la version.
                        "S": pdfdoc.PDFName("GTS_PDFA1"),
                        "OutputConditionIdentifier": pdfdoc.PDFString("sRGB"),
                        "Info": pdfdoc.PDFString(
                            "sRGB : primaires IEC 61966-2.1, courbe de transfert approchee par un "
                            "gamma 2.2"
                        ),
                        "DestOutputProfile": pdf.Reference(icc),
                    }
                )
            ]
        ),
    )


# --- Dessin ---------------------------------------------------------------------------------------


class _Sheet:
    """Curseur de mise en page : garde la position verticale et enchaîne les pages.

    Une petite classe plutôt qu'une variable passée de fonction en fonction : le document a une
    longueur variable — dix lignes ou deux cents — et chaque bloc doit pouvoir demander « ai-je la
    place ? » sans que l'appelant ait à le savoir.
    """

    def __init__(self, canvas: pdfcanvas.Canvas, document: FacturXDocument) -> None:
        self.canvas = canvas
        self.document = document
        self.page = 0
        self.y = 0.0
        self.start_page()

    def start_page(self) -> None:
        if self.page:
            self.canvas.showPage()
        self.page += 1
        self.canvas.setFont(FONT_REGULAR, BODY_FONT_SIZE)
        self.y = PAGE_HEIGHT - MARGIN
        self._footer()

    def _footer(self) -> None:
        canvas = self.canvas
        canvas.saveState()
        canvas.setFont(FONT_REGULAR, SMALL_FONT_SIZE)
        canvas.setFillColor(MUTED)
        canvas.drawString(MARGIN, MARGIN - 2, f"Page {self.page}")
        canvas.drawRightString(
            PAGE_WIDTH - MARGIN,
            MARGIN - 2,
            f"{self.document.title} {self.document.number}",
        )
        for index, chunk in enumerate(
            wrap(canvas, NOT_A_PLATFORM_NOTICE, CONTENT_WIDTH, SMALL_FONT_SIZE)
        ):
            canvas.drawString(MARGIN, MARGIN + 18 - index * 7.5, chunk)
        canvas.restoreState()

    def need(self, height: float) -> None:
        """Ouvre une page si le bloc demandé ne tient plus sous le curseur."""
        if self.y - height < MARGIN + FOOTER_HEIGHT:
            self.start_page()

    def text(
        self, value: str, *, size: float = BODY_FONT_SIZE, bold: bool = False, color: Any = TEXT
    ) -> None:
        self.need(size + 3)
        self.canvas.setFont(FONT_BOLD if bold else FONT_REGULAR, size)
        self.canvas.setFillColor(color)
        self.canvas.drawString(MARGIN, self.y - size, value)
        self.y -= size + 3

    def gap(self, height: float) -> None:
        self.y -= height


def wrap(canvas: pdfcanvas.Canvas, value: str, width: float, size: float) -> list[str]:
    """Découpe un texte à la largeur disponible, sans jamais couper un mot.

    Mesuré avec la police réelle et non estimé au nombre de caractères : « Faïence murale —
    Salle de bains — mur B » et « IIIII » n'occupent pas la même place, et une désignation
    tronquée rend le devis invérifiable.
    """
    words = value.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if canvas.stringWidth(candidate, FONT_REGULAR, size) <= width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _party_block(sheet: _Sheet, party: Party, x: float, top: float, width: float) -> float:
    canvas = sheet.canvas
    y = top
    canvas.setFont(FONT_BOLD, 9)
    canvas.setFillColor(INK)
    canvas.drawString(x, y, party.name)
    y -= 12

    canvas.setFont(FONT_REGULAR, 7.5)
    canvas.setFillColor(TEXT)
    for value in _party_lines(party):
        for chunk in wrap(canvas, value, width, 7.5):
            canvas.drawString(x, y, chunk)
            y -= 9.5
    return y


def _party_lines(party: Party) -> list[str]:
    lines = [
        line
        for line in (
            party.address_line1,
            party.address_line2,
            " ".join(part for part in (party.postal_code, party.city) if part) or None,
            party.country,
        )
        if line
    ]
    identity = [
        part
        for part in (
            party.legal_form,
            f"capital de {euros(party.share_capital_cents)}"
            if party.share_capital_cents is not None
            else None,
            f"SIRET {party.siret}" if party.siret else None,
            f"RCS {party.rcs}" if party.rcs else None,
            f"TVA {party.vat_number}" if party.vat_number else None,
        )
        if part
    ]
    if identity:
        lines.append(" — ".join(identity))
    contact = [part for part in (party.phone, party.email) if part]
    if contact:
        lines.append(" — ".join(contact))
    return lines


def _header(sheet: _Sheet) -> None:
    document = sheet.document
    canvas = sheet.canvas

    canvas.setFillColor(BAND)
    canvas.rect(MARGIN, PAGE_HEIGHT - MARGIN - 44, CONTENT_WIDTH, 44, stroke=0, fill=1)

    canvas.setFont(FONT_BOLD, 18)
    canvas.setFillColor(INK)
    canvas.drawString(MARGIN + 8, PAGE_HEIGHT - MARGIN - 24, document.title)
    canvas.setFont(FONT_BOLD, 11)
    canvas.drawString(MARGIN + 8, PAGE_HEIGHT - MARGIN - 38, f"n° {document.number}")

    canvas.setFont(FONT_REGULAR, 8)
    canvas.setFillColor(TEXT)
    right = PAGE_WIDTH - MARGIN - 8
    canvas.drawRightString(
        right, PAGE_HEIGHT - MARGIN - 18, f"Émis le {document.issued_at.strftime('%d/%m/%Y')}"
    )
    deadline = document.due_date if document.is_invoice else document.valid_until
    if deadline is not None:
        label = "Échéance" if document.is_invoice else "Valable jusqu'au"
        canvas.drawRightString(
            right, PAGE_HEIGHT - MARGIN - 30, f"{label} {deadline.strftime('%d/%m/%Y')}"
        )
    if document.project_name:
        canvas.drawRightString(
            right, PAGE_HEIGHT - MARGIN - 42, f"Projet : {document.project_name}"
        )

    sheet.y = PAGE_HEIGHT - MARGIN - 60

    column = (CONTENT_WIDTH - 12) / 2
    left_bottom = _party_block(sheet, document.seller, MARGIN, sheet.y, column)
    canvas.setFont(FONT_BOLD, 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(MARGIN + column + 12, sheet.y + 11, "CLIENT")
    right_bottom = _party_block(
        sheet, document.buyer, MARGIN + column + 12, sheet.y, column
    )
    sheet.y = min(left_bottom, right_bottom) - 6

    if document.site_address:
        canvas.setFont(FONT_BOLD, 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(MARGIN, sheet.y, "ADRESSE DU CHANTIER")
        sheet.y -= 10
        canvas.setFont(FONT_REGULAR, 7.5)
        canvas.setFillColor(TEXT)
        canvas.drawString(MARGIN, sheet.y, " — ".join(document.site_address))
        sheet.y -= 14


def _column_positions() -> list[float]:
    positions = [MARGIN]
    for ratio in COLUMN_RATIOS:
        positions.append(positions[-1] + ratio * CONTENT_WIDTH)
    return positions


def _table_header(sheet: _Sheet) -> None:
    canvas = sheet.canvas
    positions = _column_positions()
    sheet.need(24)
    canvas.setFillColor(BAND)
    canvas.rect(MARGIN, sheet.y - 14, CONTENT_WIDTH, 14, stroke=0, fill=1)
    canvas.setFont(FONT_BOLD, 7.5)
    canvas.setFillColor(INK)
    for index, title in enumerate(COLUMN_HEADERS):
        if index in (0, 1, 2):
            canvas.drawString(positions[index] + 3, sheet.y - 10, title)
        else:
            canvas.drawRightString(positions[index + 1] - 3, sheet.y - 10, title)
    sheet.y -= 18


def _table(sheet: _Sheet) -> None:
    canvas = sheet.canvas
    positions = _column_positions()
    label_width = positions[2] - positions[1] - 6
    _table_header(sheet)

    for line in sheet.document.lines:
        chunks = wrap(canvas, line.label, label_width, BODY_FONT_SIZE)
        height = max(LINE_HEIGHT, len(chunks) * LINE_HEIGHT)
        if sheet.y - height < MARGIN + FOOTER_HEIGHT:
            sheet.start_page()
            _table_header(sheet)

        canvas.setFont(FONT_REGULAR, BODY_FONT_SIZE)
        canvas.setFillColor(TEXT)
        baseline = sheet.y - BODY_FONT_SIZE
        canvas.drawString(positions[0] + 3, baseline, str(line.position))
        for index, chunk in enumerate(chunks):
            canvas.drawString(positions[1] + 3, baseline - index * LINE_HEIGHT, chunk)
        canvas.drawString(positions[2] + 3, baseline, line.unit)
        canvas.drawRightString(positions[4] - 3, baseline, quantity_text(line.quantity))
        canvas.drawRightString(positions[5] - 3, baseline, euros(line.unit_price_cents))
        canvas.drawRightString(positions[6] - 3, baseline, percent(line.vat_rate_bp))
        canvas.drawRightString(positions[7] - 3, baseline, euros(line.total_ht_cents))

        sheet.y -= height + 2
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.3)
        canvas.line(MARGIN, sheet.y + 1, PAGE_WIDTH - MARGIN, sheet.y + 1)

    if not sheet.document.lines:
        sheet.text("Aucune ligne : ce document est vide.", color=MUTED)


def _totals(sheet: _Sheet) -> None:
    document = sheet.document
    canvas = sheet.canvas
    height = 26 + 12 * (len(document.taxes) + 3)
    sheet.need(height)
    sheet.gap(8)

    left = MARGIN + CONTENT_WIDTH * 0.45
    width = PAGE_WIDTH - MARGIN - left

    canvas.setFont(FONT_BOLD, 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(left, sheet.y, "RÉCAPITULATIF DE TVA")
    sheet.y -= 12

    canvas.setFont(FONT_REGULAR, 7.5)
    canvas.setFillColor(TEXT)
    for bucket in document.taxes:
        canvas.drawString(left, sheet.y, f"Base {percent(bucket.rate_bp)}")
        canvas.drawString(left + width * 0.4, sheet.y, euros(bucket.base_cents))
        canvas.drawRightString(PAGE_WIDTH - MARGIN, sheet.y, euros(bucket.tax_cents))
        sheet.y -= 11

    sheet.y -= 3
    canvas.setStrokeColor(RULE)
    canvas.line(left, sheet.y, PAGE_WIDTH - MARGIN, sheet.y)
    sheet.y -= 12

    for label, value, bold in (
        ("Total HT", document.total_ht_cents, False),
        ("Total TVA", document.total_tva_cents, False),
        ("Total TTC", document.total_ttc_cents, True),
    ):
        canvas.setFont(FONT_BOLD if bold else FONT_REGULAR, 9 if bold else 8)
        canvas.setFillColor(INK if bold else TEXT)
        canvas.drawString(left, sheet.y, label)
        canvas.drawRightString(PAGE_WIDTH - MARGIN, sheet.y, euros(value))
        sheet.y -= 13


def _legal_block(sheet: _Sheet) -> None:
    document = sheet.document
    mentions = document.mentions
    sheet.gap(10)
    sheet.text("MENTIONS OBLIGATOIRES", size=7.5, bold=True, color=MUTED)

    paragraphs: list[str] = []

    decennial = [
        part
        for part in (
            f"assureur {mentions.decennial_insurer}" if mentions.decennial_insurer else None,
            f"police n° {mentions.decennial_policy_number}"
            if mentions.decennial_policy_number
            else None,
            f"couverture {mentions.decennial_coverage_area}"
            if mentions.decennial_coverage_area
            else None,
        )
        if part
    ]
    paragraphs.append(
        "Assurance décennale : " + ", ".join(decennial) + "."
        if decennial
        # Le silence serait pire que l'aveu : un devis de bâtiment sans mention d'assurance
        # décennale est inopposable, et l'artisan doit voir le trou avant son client.
        else "Assurance décennale : NON RENSEIGNÉE — mention obligatoire manquante, complétez "
        "l'identité de l'entreprise avant d'envoyer ce document."
    )

    if mentions.payment_terms:
        paragraphs.append(f"Conditions de règlement : {mentions.payment_terms}")
    paragraphs.append(
        f"Pénalités de retard : {percent(mentions.late_penalty_rate_bp)} par an, exigibles sans "
        "rappel dès le lendemain de la date d'échéance. Indemnité forfaitaire pour frais de "
        f"recouvrement : {euros(mentions.recovery_indemnity_cents)} (article D. 441-5 du code de "
        "commerce)."
    )

    attestation = mentions.vat_attestation
    if attestation is not None:
        details = [
            "Attestation de TVA du client (remplace depuis le 16 février 2025 les formulaires "
            "CERFA 13947 et 13948) :",
            "le local est achevé depuis plus de deux ans"
            if attestation.over_two_years
            else "le local N'EST PAS achevé depuis plus de deux ans",
        ]
        if attestation.premises_use:
            details.append(f"affectation : {attestation.premises_use}")
        if attestation.signatory:
            signed = (
                attestation.signed_at.strftime(" le %d/%m/%Y") if attestation.signed_at else ""
            )
            details.append(f"attestée par {attestation.signatory}{signed}")
        paragraphs.append(" ; ".join(details) + ".")

    if mentions.is_consumer:
        mediator = mentions.mediator_name or "NON RENSEIGNÉ — mention obligatoire en B2C"
        url = f" ({mentions.mediator_url})" if mentions.mediator_url else ""
        paragraphs.append(f"Médiateur de la consommation : {mediator}{url}.")

    if document.notes:
        paragraphs.append(document.notes)

    for paragraph in paragraphs:
        for chunk in wrap(sheet.canvas, paragraph, CONTENT_WIDTH, 7.0):
            sheet.text(chunk, size=7.0, color=TEXT)
        sheet.gap(2)

    if not document.is_invoice:
        sheet.gap(6)
        sheet.text(
            "Bon pour accord — date, signature et mention « lu et approuvé » :",
            size=7.5,
            bold=True,
            color=INK,
        )
        sheet.need(48)
        sheet.canvas.setStrokeColor(RULE)
        sheet.canvas.rect(MARGIN, sheet.y - 44, CONTENT_WIDTH * 0.5, 44, stroke=1, fill=0)
        sheet.y -= 50


def render_facturx_pdf(document: FacturXDocument) -> bytes:
    """Produit le PDF/A-3. Une facture y porte son XML CII ; un devis n'en porte pas.

    Le résultat est déterministe à l'horodatage de la pièce jointe près : deux appels sur le même
    document rendent le même dessin, ce qui rend les tests possibles sur le contenu réel du
    fichier plutôt que sur sa seule longueur.
    """
    register_fonts()
    buffer = io.BytesIO()
    # `initialFontName` : sans lui, reportlab déclare Helvetica dans les ressources de chaque page,
    # même sans l'utiliser. C'est une des quatorze polices standard du PDF, donc **non incorporée**,
    # et sa seule présence dans les ressources suffit à disqualifier le fichier en PDF/A.
    canvas = pdfcanvas.Canvas(
        buffer, pagesize=PAGE_SIZE, pdfVersion=(1, 7), initialFontName=FONT_REGULAR
    )
    canvas.setTitle(f"{document.title} {document.number}")
    canvas.setAuthor(document.seller.name)
    canvas.setSubject(
        f"{document.title} {document.number} — {document.buyer.name}"
    )

    sheet = _Sheet(canvas, document)
    _header(sheet)
    _table(sheet)
    _totals(sheet)
    _legal_block(sheet)

    attached = document.is_invoice
    if attached:
        _attach_facturx_xml(canvas, build_cii_xml(document))
    _declare_pdfa(canvas, document, attached=attached)

    canvas.save()
    return buffer.getvalue()


# --- Passage du modèle relationnel au document ----------------------------------------------------


def document_from_quote(
    organization: Organization, quote: Quote, lines: Sequence[QuoteLine], *, as_invoice: bool
) -> FacturXDocument:
    """Assemble le document imprimable à partir des lignes **déjà écrites** en base.

    Aucune relecture du barème : le prix, le libellé et le taux imprimés sont ceux de
    `quote_line`, c'est-à-dire ceux qui ont été copiés à l'émission. C'est toute la raison d'être
    de cette copie (`docs/strategie-produit.md` §3.2).

    Les mentions légales sont prises sur le **document** et non sur l'organisation, à l'exception
    de l'assurance décennale et de l'identité, qui n'ont pas d'autre source. Une facture émise
    l'an dernier ne doit pas changer parce que l'artisan a revu ses conditions ce matin.
    """
    number = quote.invoice_number if as_invoice else quote.number
    issued_at = quote.invoiced_at if as_invoice else quote.issued_at
    if not number or issued_at is None:
        raise ValueError(
            "un document sans numéro ni date d'émission ne s'imprime pas : émettez le devis "
            "(ou la facture) d'abord"
        )

    seller = Party(
        name=organization.name,
        siret=organization.siret,
        vat_number=organization.vat_number,
        legal_form=organization.legal_form,
        share_capital_cents=organization.share_capital_cents,
        rcs=organization.rcs,
        address_line1=organization.address_line1,
        address_line2=organization.address_line2,
        postal_code=organization.postal_code,
        city=organization.city,
        country=organization.country,
        email=organization.billing_email,
        phone=organization.phone,
    )
    buyer = Party(
        name=quote.client_name,
        vat_number=quote.client_vat_number,
        address_line1=quote.client_address_line1,
        address_line2=quote.client_address_line2,
        postal_code=quote.client_postal_code,
        city=quote.client_city,
        country=quote.client_country,
        email=quote.client_email,
        phone=quote.client_phone,
    )

    ordered = sorted(lines, key=lambda line: (line.position, line.id or 0))
    document_lines = tuple(
        DocumentLine(
            position=line.position,
            label=line.label,
            unit=line.unit.value if isinstance(line.unit, PriceUnit) else str(line.unit),
            quantity=line.quantity,
            unit_price_cents=line.unit_price_cents,
            vat_rate_bp=line.vat_rate_bp,
            total_ht_cents=line.total_ht_cents,
        )
        for line in ordered
    )
    taxes = tuple(
        TaxBucket(rate_bp=bucket.rate_bp, base_cents=bucket.base_cents, tax_cents=bucket.tax_cents)
        for bucket in vat_buckets_from(
            (line.vat_rate_bp, line.total_ht_cents) for line in document_lines
        )
    )

    attestation = (
        VatAttestation(
            over_two_years=quote.vat_attestation_over_two_years,
            premises_use=quote.vat_attestation_premises_use,
            signatory=quote.vat_attestation_signatory,
            signed_at=quote.vat_attestation_signed_at,
        )
        if quote.vat_attestation_required
        else None
    )

    site = tuple(
        part
        for part in (
            quote.project_name or "Chantier",
            quote.site_address_line1,
            quote.site_address_line2,
            " ".join(bit for bit in (quote.site_postal_code, quote.site_city) if bit) or None,
        )
        if part
    )

    return FacturXDocument(
        kind="invoice" if as_invoice else "quote",
        number=number,
        issued_at=issued_at,
        seller=seller,
        buyer=buyer,
        lines=document_lines,
        taxes=taxes,
        total_ht_cents=quote.total_ht_cents,
        total_tva_cents=quote.total_tva_cents,
        total_ttc_cents=quote.total_ttc_cents,
        mentions=LegalMentions(
            decennial_insurer=organization.decennial_insurer,
            decennial_policy_number=organization.decennial_policy_number,
            decennial_coverage_area=organization.decennial_coverage_area,
            payment_terms=quote.payment_terms,
            late_penalty_rate_bp=quote.late_penalty_rate_bp,
            recovery_indemnity_cents=quote.recovery_indemnity_cents,
            mediator_name=quote.mediator_name,
            mediator_url=quote.mediator_url,
            is_consumer=quote.client_is_consumer,
            vat_attestation=attestation,
        ),
        valid_until=quote.valid_until,
        due_date=quote.due_date,
        site_address=site if len(site) > 1 else (),
        project_name=quote.project_name,
        notes=quote.notes,
    )
