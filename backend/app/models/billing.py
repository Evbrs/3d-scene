"""Barème de prix, devis et facture (`docs/strategie-produit.md` §2 et §3.1).

C'est la couche qui fait payer le produit, et c'est aussi la seule du dépôt qui porte des
obligations légales. Trois règles la structurent, et aucune n'est négociable.

**Tout montant est un entier de centimes.** Le reste du modèle est en flottants centimètres et la
tentation de continuer est forte, mais 0,1 € ne se représente pas en binaire : deux additions
donnent 0,30000000000000004, et le total imprimé sur un document contractuel ne se rejoue plus.
Les quantités, elles, sont des `Numeric(12, 3)` — le métré rend des m² au décimètre carré près, et
un flottant y réintroduirait le même bruit.

**Une ligne de devis est une copie, jamais une jointure.** `quote_line` recopie le libellé, le
prix unitaire et le taux de TVA au moment de l'émission et ne référence `price_item` que par un
*code* (`source_price_item_code`), sans clé étrangère. En France un devis signé est un contrat :
s'il change après envoi parce que l'artisan a augmenté son tarif horaire, ce n'est pas un défaut
d'affichage, c'est un problème juridique.

**Le taux de TVA est porté par la ligne.** La rénovation relève de 10 %, 5,5 % ou 20 % selon la
nature des travaux, et un même chantier mélange les trois. Un taux global au niveau du document
serait faux dans le cas courant, pas dans un cas limite.

Les bornes sont répétées en base (`CheckConstraint`) et pas seulement dans les schémas Pydantic :
SQLAdmin, la CLI, Celery et `psql` écrivent sans passer par l'API, et SQLModel désactive la
validation `Field(...)` sur les modèles `table=True`.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

# Pas de `from __future__ import annotations` : même raison que dans `app/models/plan.py`, les
# annotations d'un modèle SQLModel sont résolues à l'exécution.
from sqlalchemy import CheckConstraint, Column, DateTime, UniqueConstraint, false, text, true
from sqlalchemy.ext.mutable import MutableList
from sqlmodel import Field

from app.models.base import TimestampedModel, json_type, value_enum

# Un taux exprimé en points de base : 2000 = 20 %, 1000 = 10 %, 550 = 5,5 %. L'entier évite le
# flottant sur une donnée qui entre dans un calcul de montant, et il distingue 5,5 % de 5,50 %.
BASIS_POINTS = 10_000

# Taux de TVA de la rénovation en métropole. Ils ne sont **pas** une liste blanche : la Corse et
# l'outre-mer en connaissent d'autres, et refuser un taux légitime au moment d'émettre une facture
# serait pire que de laisser passer une coquille. `app/services/pricing.py` s'en sert pour
# *avertir*, jamais pour bloquer.
FRENCH_RENOVATION_VAT_RATES_BP = (550, 1000, 2000)

# Indemnité forfaitaire pour frais de recouvrement entre professionnels : 40 €, fixés par
# l'article D. 441-5 du code de commerce. C'est la seule de nos mentions légales dont le montant
# est un chiffre et non un usage — elle reste malgré tout une colonne, parce qu'un décret la
# changera un jour et qu'une constante codée en dur ne se corrige pas sur les documents déjà émis.
DEFAULT_RECOVERY_INDEMNITY_CENTS = 4_000

# Pénalités de retard : usage le plus répandu dans le bâtiment, trois fois le taux d'intérêt légal.
# Paramétrable par document, comme l'exige `docs/strategie-produit.md` §2 (« le produit doit rendre
# ces champs paramétrables plutôt que codés en dur »).
DEFAULT_LATE_PENALTY_RATE_BP = 1_050

# Un devis d'artisan vaut classiquement trois mois. La durée est une mention obligatoire : elle
# doit donc exister même quand personne ne l'a saisie.
DEFAULT_VALIDITY_DAYS = 90

# Longueur maximale d'un numéro de document : « DEV-2026-000001 » et une marge pour les séries
# personnalisées à venir.
DOCUMENT_NUMBER_LENGTH = 40


class PriceUnit(StrEnum):
    """Unité de facturation d'une ligne de prix.

    Quatre unités et pas davantage : ce sont celles que le métré sait produire (`m2` pour les
    surfaces, `ml` pour les linéaires de plinthe et de corniche) plus les deux que l'artisan
    ajoute à la main.
    """

    SQUARE_METER = "m2"
    LINEAR_METER = "ml"
    UNIT = "u"
    LUMP_SUM = "forfait"


class QuoteStatus(StrEnum):
    """Cycle de vie d'un devis.

    L'ordre compte : `draft` est le seul état où les lignes se modifient encore. Dès `sent`, le
    document a un numéro, il est parti chez le client, et il est figé.
    """

    DRAFT = "draft"
    SENT = "sent"
    ACCEPTED = "accepted"
    REFUSED = "refused"
    INVOICED = "invoiced"


class DocumentSeries(StrEnum):
    """Série de numérotation. Devis et factures ont chacun leur suite continue."""

    QUOTE = "quote"
    INVOICE = "invoice"


class PriceBook(TimestampedModel, table=True):
    """Barème de prix d'une organisation.

    Plusieurs barèmes par organisation sont prévus dès maintenant (tarif public, tarif
    professionnel, tarif d'un donneur d'ordre) : les ajouter plus tard aurait demandé de migrer
    toutes les lignes de prix déjà saisies.
    """

    __tablename__ = "price_book"
    __table_args__ = (
        CheckConstraint("length(name) > 0", name="ck_price_book_name_not_empty"),
        UniqueConstraint("organization_id", "name", name="uq_price_book_organization_name"),
    )

    id: int | None = Field(default=None, primary_key=True)
    organization_id: int = Field(foreign_key="organization.id", index=True, ondelete="CASCADE")
    name: str = Field(max_length=200)
    # Barème retenu quand la création d'un devis n'en désigne aucun. L'unicité de ce drapeau est
    # tenue par la route (`app/api/quotes.py`) et non par un index partiel : ceux-ci ne se
    # reconstruisent pas en mode batch SQLite, et la suite de tests tourne dessus.
    # `false()` et non `text("0")` : PostgreSQL refuse un entier comme défaut de colonne
    # booléenne (« column is of type boolean but default expression is of type integer »).
    is_default: bool = Field(default=False, sa_column_kwargs={"server_default": false()})


class PriceItem(TimestampedModel, table=True):
    """Une ligne de barème : un code, un libellé, une unité, un prix et un taux de TVA.

    Le `code` est la clé de rattachement au métré (`Covering.material` → code, voir
    `app/services/pricing.py`) et c'est **lui** que la ligne de devis recopie. Il est donc
    volontairement court, stable et lisible par un humain : c'est ce que l'artisan retrouvera dans
    son export CSV et dans son classeur de prix.
    """

    __tablename__ = "price_item"
    __table_args__ = (
        UniqueConstraint("price_book_id", "code", name="uq_price_item_book_code"),
        CheckConstraint("length(code) > 0", name="ck_price_item_code_not_empty"),
        CheckConstraint("length(label) > 0", name="ck_price_item_label_not_empty"),
        CheckConstraint("unit_price_cents >= 0", name="ck_price_item_unit_price_not_negative"),
        CheckConstraint(
            f"vat_rate_bp >= 0 AND vat_rate_bp <= {BASIS_POINTS}",
            name="ck_price_item_vat_rate_bounded",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    price_book_id: int = Field(foreign_key="price_book.id", index=True, ondelete="CASCADE")
    code: str = Field(max_length=40)
    label: str = Field(max_length=200)
    unit: PriceUnit = Field(  # type: ignore[call-overload]
        default=PriceUnit.SQUARE_METER,
        sa_type=value_enum(PriceUnit, "priceunit"),
        sa_column_kwargs={"server_default": text("'m2'")},
    )
    unit_price_cents: int = Field(default=0)
    vat_rate_bp: int = Field(default=2_000, sa_column_kwargs={"server_default": text("2000")})


class FaceCosting(TimestampedModel, table=True):
    """Rattachement explicite d'une face à une ligne de barème.

    Table de liaison plutôt qu'un élargissement de `Face.covering` : ce dictionnaire est validé en
    `extra="forbid"` (`app/schemas/plan.py`), son schéma est fermé par la spec §1, et y glisser du
    chiffrage mélangerait la description physique du revêtement avec sa valorisation commerciale —
    deux choses qui ne changent ni au même moment ni par les mêmes mains.

    Elle est **facultative** : sans elle, `app/services/pricing.py` déduit le code du matériau du
    revêtement. C'est ce qui évite les soixante rattachements à la main d'un projet de douze
    pièces. Elle sert aux exceptions : une face à reprendre, un prix négocié sur un seul mur.
    """

    __tablename__ = "face_costing"
    __table_args__ = (
        # Une face n'a qu'un chiffrage : deux lignes concurrentes rendraient le devis dépendant de
        # l'ordre de lecture de la table.
        UniqueConstraint("face_id", name="uq_face_costing_face"),
        CheckConstraint(
            "override_quantity IS NULL OR override_quantity >= 0",
            name="ck_face_costing_override_quantity_not_negative",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    face_id: int = Field(foreign_key="face.id", index=True, ondelete="CASCADE")
    # Code du barème, pas un identifiant : un barème remplacé ne doit pas casser les
    # rattachements déjà faits, et le code est ce que l'artisan reconnaît.
    price_item_code: str | None = Field(default=None, max_length=40)
    # Quantité imposée, quand le métré ne sait pas mesurer ce qu'on veut facturer (une reprise
    # partielle, une surface relevée sur place).
    override_quantity: Decimal | None = Field(default=None, max_digits=12, decimal_places=3)
    override_unit_price_cents: int | None = Field(default=None)


class Quote(TimestampedModel, table=True):
    """Un devis, puis la facture qu'il devient.

    Un seul document et deux numéros plutôt que deux tables : la facture d'un devis accepté porte
    exactement les mêmes lignes, aux mêmes prix. Les dupliquer aurait créé deux vérités pour un
    seul contrat, et c'est précisément l'écart entre les deux qui fait les litiges.

    Les mentions obligatoires (`docs/strategie-produit.md` §2) sont des colonnes de **ce**
    document et non des colonnes d'`organization`, même quand leur valeur en est recopiée : les
    conditions de règlement d'une facture émise l'an dernier ne doivent pas changer parce que
    l'artisan a revu ses conditions générales ce matin.
    """

    __tablename__ = "quote"
    __table_args__ = (
        # Un numéro par organisation : deux clients du service peuvent parfaitement émettre le
        # même « DEV-2026-0001 ». Les NULL ne comptent pas dans une contrainte d'unicité, ce qui
        # laisse coexister autant de brouillons non numérotés qu'on veut.
        UniqueConstraint("organization_id", "number", name="uq_quote_organization_number"),
        UniqueConstraint(
            "organization_id", "invoice_number", name="uq_quote_organization_invoice_number"
        ),
        CheckConstraint("length(client_name) > 0", name="ck_quote_client_name_not_empty"),
        CheckConstraint(
            f"late_penalty_rate_bp >= 0 AND late_penalty_rate_bp <= {BASIS_POINTS}",
            name="ck_quote_late_penalty_rate_bounded",
        ),
        CheckConstraint(
            "recovery_indemnity_cents >= 0", name="ck_quote_recovery_indemnity_not_negative"
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    organization_id: int = Field(foreign_key="organization.id", index=True, ondelete="CASCADE")
    # `SET NULL` et non `CASCADE` : un devis émis est un contrat, et une facture se conserve dix
    # ans. Supprimer le plan du chantier ne doit pas effacer la comptabilité qui en découle — d'où
    # aussi la copie du nom du projet ci-dessous, qui survit à la suppression.
    project_id: int | None = Field(
        default=None, foreign_key="project.id", index=True, ondelete="SET NULL"
    )
    project_name: str | None = Field(default=None, max_length=200)

    # Nul tant que le document est un brouillon. Le numéro n'est attribué qu'à l'émission : le
    # réserver dès la création ferait un trou dans la suite à chaque brouillon abandonné, et la
    # numérotation doit être continue.
    number: str | None = Field(default=None, max_length=DOCUMENT_NUMBER_LENGTH)
    status: QuoteStatus = Field(  # type: ignore[call-overload]
        default=QuoteStatus.DRAFT,
        sa_type=value_enum(QuoteStatus, "quotestatus"),
        sa_column_kwargs={"server_default": text("'draft'")},
    )
    issued_at: datetime | None = Field(  # type: ignore[call-overload]
        default=None, sa_type=DateTime(timezone=True), nullable=True
    )
    # Durée de validité : mention obligatoire d'un devis de bâtiment.
    valid_until: datetime | None = Field(  # type: ignore[call-overload]
        default=None, sa_type=DateTime(timezone=True), nullable=True
    )

    invoice_number: str | None = Field(default=None, max_length=DOCUMENT_NUMBER_LENGTH)
    invoiced_at: datetime | None = Field(  # type: ignore[call-overload]
        default=None, sa_type=DateTime(timezone=True), nullable=True
    )
    due_date: datetime | None = Field(  # type: ignore[call-overload]
        default=None, sa_type=DateTime(timezone=True), nullable=True
    )

    # --- Totaux figés --------------------------------------------------------------------------
    # Recalculables depuis les lignes, et pourtant stockés : ce sont eux qui partent dans le XML
    # Factur-X et sur le papier signé. Les recalculer à la lecture ferait dépendre un document
    # contractuel de la version du code qui le relit.
    total_ht_cents: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    total_tva_cents: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    total_ttc_cents: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})

    # --- Client --------------------------------------------------------------------------------
    client_name: str = Field(max_length=200)
    client_email: str | None = Field(default=None, max_length=320)
    client_phone: str | None = Field(default=None, max_length=30)
    client_address_line1: str | None = Field(default=None, max_length=200)
    client_address_line2: str | None = Field(default=None, max_length=200)
    client_postal_code: str | None = Field(default=None, max_length=20)
    client_city: str | None = Field(default=None, max_length=100)
    client_country: str | None = Field(default=None, max_length=100)
    client_vat_number: str | None = Field(default=None, max_length=20)
    # Un particulier n'a pas les mêmes mentions obligatoires qu'une entreprise : médiateur de la
    # consommation et droit de rétractation ne s'impriment qu'en B2C.
    client_is_consumer: bool = Field(default=True, sa_column_kwargs={"server_default": true()})

    # --- Chantier ------------------------------------------------------------------------------
    # Distinct de l'adresse du client, et c'est le cas courant : un propriétaire bailleur fait
    # rénover un logement où il n'habite pas, et le taux de TVA dépend de ce logement-là.
    site_address_line1: str | None = Field(default=None, max_length=200)
    site_address_line2: str | None = Field(default=None, max_length=200)
    site_postal_code: str | None = Field(default=None, max_length=20)
    site_city: str | None = Field(default=None, max_length=100)

    # --- Attestation TVA du client ---------------------------------------------------------------
    # Depuis le 16 février 2025, une attestation portée sur le devis ou la facture remplace les
    # formulaires CERFA 13947 et 13948. Sans elle, c'est l'artisan qui est redressé sur la
    # différence entre 20 % et le taux réduit qu'il a appliqué : la porter ici est un service qu'on
    # lui rend, pas une décoration.
    vat_attestation_required: bool = Field(
        default=False, sa_column_kwargs={"server_default": false()}
    )
    # Condition de fond du taux réduit : le local doit être achevé depuis plus de deux ans.
    vat_attestation_over_two_years: bool | None = Field(default=None)
    # Affectation du local (« habitation », « habitation et professionnel »…).
    vat_attestation_premises_use: str | None = Field(default=None, max_length=100)
    vat_attestation_signatory: str | None = Field(default=None, max_length=200)
    vat_attestation_signed_at: datetime | None = Field(  # type: ignore[call-overload]
        default=None, sa_type=DateTime(timezone=True), nullable=True
    )

    # --- Conditions contractuelles ---------------------------------------------------------------
    payment_terms: str | None = Field(default=None, max_length=500)
    late_penalty_rate_bp: int = Field(
        default=DEFAULT_LATE_PENALTY_RATE_BP,
        sa_column_kwargs={"server_default": text(str(DEFAULT_LATE_PENALTY_RATE_BP))},
    )
    recovery_indemnity_cents: int = Field(
        default=DEFAULT_RECOVERY_INDEMNITY_CENTS,
        sa_column_kwargs={"server_default": text(str(DEFAULT_RECOVERY_INDEMNITY_CENTS))},
    )
    mediator_name: str | None = Field(default=None, max_length=200)
    mediator_url: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)

    # Avertissements du métré et du chiffrage, figés à la création du devis. Ils sont stockés et
    # non recalculés : le plan changera, et ce qui compte est ce qu'on savait **au moment** où le
    # document a été établi. Une liste non vide veut dire « total partiel », jamais « détail
    # cosmétique » — c'est ce qui distingue une surface non mesurée d'une surface nulle.
    warnings: list[str] = Field(
        default_factory=list,
        sa_column=Column(
            MutableList.as_mutable(json_type()), nullable=False, server_default=text("'[]'")
        ),
    )


class QuoteLine(TimestampedModel, table=True):
    """Une ligne de devis. **Aucune** jointure de lecture vers `price_item`.

    `label`, `unit`, `unit_price_cents` et `vat_rate_bp` sont des copies prises au moment où la
    ligne est écrite. `source_price_item_code` n'est là que pour retrouver *d'où* venait le prix —
    c'est une trace d'audit, jamais un chemin de lecture. Une clé étrangère aurait suffi à
    ressusciter la jointure au premier `selectinload` distrait.

    `total_ht_cents` est figé pour la même raison : l'arrondi de `quantité x prix unitaire` doit
    être celui qu'on a imprimé, pas celui que produira la prochaine version du code.
    """

    __tablename__ = "quote_line"
    __table_args__ = (
        UniqueConstraint("quote_id", "position", name="uq_quote_line_quote_position"),
        CheckConstraint("length(label) > 0", name="ck_quote_line_label_not_empty"),
        CheckConstraint("position >= 0", name="ck_quote_line_position_not_negative"),
        CheckConstraint("quantity >= 0", name="ck_quote_line_quantity_not_negative"),
        CheckConstraint(
            f"vat_rate_bp >= 0 AND vat_rate_bp <= {BASIS_POINTS}",
            name="ck_quote_line_vat_rate_bounded",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    quote_id: int = Field(foreign_key="quote.id", index=True, ondelete="CASCADE")
    position: int = Field(default=0)
    label: str = Field(max_length=300)
    unit: PriceUnit = Field(  # type: ignore[call-overload]
        default=PriceUnit.SQUARE_METER,
        sa_type=value_enum(PriceUnit, "priceunit"),
        sa_column_kwargs={"server_default": text("'m2'")},
    )
    # `Numeric` et non un flottant : le métré rend des m² au millième, et un flottant y
    # réintroduirait le bruit que tout ce module cherche à éviter.
    quantity: Decimal = Field(default=Decimal("0"), max_digits=12, decimal_places=3)
    # Un prix unitaire négatif est licite : c'est ainsi qu'on porte une remise commerciale sans
    # inventer un second type de ligne. La quantité, elle, reste positive.
    unit_price_cents: int = Field(default=0)
    vat_rate_bp: int = Field(default=2_000, sa_column_kwargs={"server_default": text("2000")})
    total_ht_cents: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})

    # Traces d'origine, sans clé étrangère : la face peut être supprimée du plan sans que la
    # ligne facturée disparaisse ou devienne invalide.
    source_face_id: int | None = Field(default=None)
    source_price_item_code: str | None = Field(default=None, max_length=40)


class QuoteCounter(TimestampedModel, table=True):
    """Compteur de numérotation, une ligne par (organisation, série, année).

    La numérotation légale doit être **chronologique et sans trou**. Une séquence PostgreSQL n'en
    est pas capable : elle avance même quand la transaction est annulée, et laisse donc des trous
    qu'aucune écriture ne comblera. Un compteur de ce genre, incrémenté par un `UPDATE ...
    RETURNING` dans la **même transaction** que l'écriture du document, tient la garantie : si le
    document n'est pas écrit, le numéro n'est pas consommé.

    Le prix à payer est explicite : l'`UPDATE` verrouille la ligne jusqu'au `COMMIT`, donc deux
    émissions simultanées dans la même organisation se sérialisent. C'est le comportement voulu —
    la loi demande une suite, pas du parallélisme.
    """

    __tablename__ = "quote_counter"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "series", "year", name="uq_quote_counter_organization_series_year"
        ),
        CheckConstraint("next_value >= 0", name="ck_quote_counter_next_value_not_negative"),
    )

    id: int | None = Field(default=None, primary_key=True)
    organization_id: int = Field(foreign_key="organization.id", index=True, ondelete="CASCADE")
    series: DocumentSeries = Field(  # type: ignore[call-overload]
        default=DocumentSeries.QUOTE,
        sa_type=value_enum(DocumentSeries, "documentseries"),
        sa_column_kwargs={"server_default": text("'quote'")},
    )
    year: int = Field(default=0)
    # Dernier numéro **attribué**, et non le prochain à l'être : c'est ce que l'`UPDATE ... SET
    # next_value = next_value + 1 RETURNING next_value` renvoie directement, sans second
    # aller-retour.
    next_value: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
