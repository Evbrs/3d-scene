"""Schémas Pydantic du barème, du devis et de la facture.

Séparés des modèles SQLModel pour la même raison que le reste de l'API : ce qu'un client peut
écrire n'est pas ce que la base stocke. Ici la frontière est plus tranchée qu'ailleurs — un client
ne fixe **jamais** un numéro de document, une date d'émission, un total ni le contenu d'une ligne
déjà émise. Ces champs-là sont calculés par le serveur, et les laisser entrer par le corps de la
requête reviendrait à laisser réécrire un contrat signé.

Tous les montants sont des **entiers de centimes** : `Field(ge=…)` sur un `int` rejette `1000.10`
en 422 plutôt que de le tronquer en silence. Toutes les quantités sont des `Decimal` bornés à
trois décimales, comme la colonne.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, ClassVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.models.billing import BASIS_POINTS, PriceUnit, QuoteStatus
from app.schemas.plan import PartialUpdate

# Un code de barème est une référence courte et stable, saisie à la main et retrouvée dans un
# tableur : on interdit les espaces et la ponctuation exotique, qui cassent l'export CSV et les
# rapprochements que l'artisan fera dessus.
PriceCode = Annotated[str, Field(pattern=r"^[A-Z0-9][A-Z0-9_\-]{0,39}$")]
Label = Annotated[str, Field(min_length=1, max_length=200)]
LineLabel = Annotated[str, Field(min_length=1, max_length=300)]
# Un prix unitaire de ligne peut être négatif : c'est ainsi qu'on porte une remise commerciale
# sans inventer un second type de ligne. Un prix de **barème**, lui, ne l'est jamais.
Cents = Annotated[int, Field(ge=0, le=10**12)]
SignedCents = Annotated[int, Field(ge=-(10**12), le=10**12)]
VatRateBp = Annotated[int, Field(ge=0, le=BASIS_POINTS)]
Quantity = Annotated[Decimal, Field(ge=0, le=Decimal("999999999"), decimal_places=3)]


# --- Barème ---------------------------------------------------------------------------------------


class PriceItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: PriceCode
    label: Label
    unit: PriceUnit = PriceUnit.SQUARE_METER
    unit_price_cents: Cents = 0
    vat_rate_bp: VatRateBp = 2_000


class PriceItemUpdate(PartialUpdate):
    """Le code n'est pas modifiable : c'est la clé de rattachement du métré et des devis passés.

    Le renommer casserait silencieusement les correspondances déjà faites — supprimez la ligne et
    recréez-la si c'est vraiment le code qui est faux.
    """

    label: Label | None = None
    unit: PriceUnit | None = None
    unit_price_cents: Cents | None = None
    vat_rate_bp: VatRateBp | None = None


class PriceItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: int
    price_book_id: int
    code: str
    label: str
    unit: PriceUnit
    unit_price_cents: int
    vat_rate_bp: int


class PriceBookCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Label
    is_default: bool = False
    # Recopie le barème de référence dans le nouveau livre. C'est le comportement attendu à la
    # création d'un second barème (« tarif professionnel »), qu'on ajuste ensuite ligne à ligne.
    seed_default_items: bool = True


class PriceBookRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: int
    organization_id: int
    name: str
    is_default: bool


# --- Rattachement d'une face ----------------------------------------------------------------------


class FaceCostingWrite(BaseModel):
    """Décision explicite de l'artisan sur une face.

    Les trois champs sont facultatifs et indépendants : on peut imposer le seul code, la seule
    quantité (une reprise partielle relevée sur place) ou le seul prix (un tarif négocié).
    """

    model_config = ConfigDict(extra="forbid")

    price_item_code: PriceCode | None = None
    override_quantity: Quantity | None = None
    override_unit_price_cents: SignedCents | None = None


class FaceCostingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: int
    face_id: int
    price_item_code: str | None = None
    override_quantity: Decimal | None = None
    override_unit_price_cents: int | None = None


# --- Devis ----------------------------------------------------------------------------------------


class ClientFields(BaseModel):
    """Identité du client et adresse du chantier.

    L'adresse du chantier est distincte de celle du client, et c'est le cas courant : un
    propriétaire bailleur fait rénover un logement où il n'habite pas, et c'est ce logement-là qui
    détermine le taux de TVA applicable.
    """

    model_config = ConfigDict(extra="forbid")

    client_email: EmailStr | None = None
    client_phone: Annotated[str, Field(max_length=30)] | None = None
    client_address_line1: Annotated[str, Field(max_length=200)] | None = None
    client_address_line2: Annotated[str, Field(max_length=200)] | None = None
    client_postal_code: Annotated[str, Field(max_length=20)] | None = None
    client_city: Annotated[str, Field(max_length=100)] | None = None
    client_country: Annotated[str, Field(max_length=100)] | None = None
    client_vat_number: Annotated[str, Field(max_length=20)] | None = None
    client_is_consumer: bool = True

    site_address_line1: Annotated[str, Field(max_length=200)] | None = None
    site_address_line2: Annotated[str, Field(max_length=200)] | None = None
    site_postal_code: Annotated[str, Field(max_length=20)] | None = None
    site_city: Annotated[str, Field(max_length=100)] | None = None

    vat_attestation_required: bool = False
    vat_attestation_over_two_years: bool | None = None
    vat_attestation_premises_use: Annotated[str, Field(max_length=100)] | None = None
    vat_attestation_signatory: Annotated[str, Field(max_length=200)] | None = None
    vat_attestation_signed_at: datetime | None = None

    payment_terms: Annotated[str, Field(max_length=500)] | None = None
    late_penalty_rate_bp: VatRateBp | None = None
    recovery_indemnity_cents: Cents | None = None
    mediator_name: Annotated[str, Field(max_length=200)] | None = None
    mediator_url: Annotated[str, Field(pattern=r"^https?://", max_length=500)] | None = None
    notes: Annotated[str, Field(max_length=2000)] | None = None


class QuoteLineInput(BaseModel):
    """Une ligne saisie à la main, ajoutée aux lignes déduites du métré."""

    model_config = ConfigDict(extra="forbid")

    label: LineLabel
    unit: PriceUnit = PriceUnit.LUMP_SUM
    quantity: Quantity = Decimal("1")
    unit_price_cents: SignedCents = 0
    vat_rate_bp: VatRateBp = 2_000
    source_price_item_code: PriceCode | None = None


class QuoteCreate(ClientFields):
    """Demande de devis : le client, et ce que le métré ne peut pas deviner.

    `default_price_codes` est la fonctionnalité utile de tout ce module : « tous les murs en
    peinture, tous les sols en carrelage » en une requête, plutôt que soixante rattachements à la
    main sur un projet de douze pièces.
    """

    client_name: Label
    price_book_id: int | None = None
    valid_for_days: Annotated[int, Field(ge=1, le=365)] | None = None
    # Clés attendues : `wall`, `floor`, `ceiling`. Une clé inconnue est refusée plutôt qu'ignorée —
    # une faute de frappe produirait sinon un devis silencieusement incomplet.
    default_price_codes: dict[str, PriceCode] = Field(default_factory=dict)
    include_skirting: bool = True
    include_cornice: bool = False
    include_openings: bool = False
    extra_lines: Annotated[list[QuoteLineInput], Field(max_length=200)] = Field(
        default_factory=list
    )

    ALLOWED_FACE_KINDS: ClassVar[frozenset[str]] = frozenset({"wall", "floor", "ceiling"})

    @model_validator(mode="after")
    def _reject_unknown_face_kinds(self) -> "QuoteCreate":
        unknown = sorted(set(self.default_price_codes) - self.ALLOWED_FACE_KINDS)
        if unknown:
            raise ValueError(
                f"nature(s) de face inconnue(s) dans default_price_codes : {', '.join(unknown)} — "
                f"attendu parmi {', '.join(sorted(self.ALLOWED_FACE_KINDS))}"
            )
        return self


class QuoteUpdate(PartialUpdate):
    """Modification d'un devis.

    Ne touche jamais aux lignes ni aux totaux : un devis émis est un contrat, et un devis brouillon
    se régénère plutôt qu'il ne se rafistole. Le statut est le seul champ qui pilote un cycle de
    vie, et les transactions interdites sont refusées par la route, pas ici.
    """

    NULLABLE_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "client_email",
            "client_phone",
            "client_address_line1",
            "client_address_line2",
            "client_postal_code",
            "client_city",
            "client_country",
            "client_vat_number",
            "site_address_line1",
            "site_address_line2",
            "site_postal_code",
            "site_city",
            "vat_attestation_over_two_years",
            "vat_attestation_premises_use",
            "vat_attestation_signatory",
            "vat_attestation_signed_at",
            "payment_terms",
            "mediator_name",
            "mediator_url",
            "notes",
        }
    )

    client_name: Label | None = None
    client_email: EmailStr | None = None
    client_phone: Annotated[str, Field(max_length=30)] | None = None
    client_address_line1: Annotated[str, Field(max_length=200)] | None = None
    client_address_line2: Annotated[str, Field(max_length=200)] | None = None
    client_postal_code: Annotated[str, Field(max_length=20)] | None = None
    client_city: Annotated[str, Field(max_length=100)] | None = None
    client_country: Annotated[str, Field(max_length=100)] | None = None
    client_vat_number: Annotated[str, Field(max_length=20)] | None = None
    client_is_consumer: bool | None = None
    site_address_line1: Annotated[str, Field(max_length=200)] | None = None
    site_address_line2: Annotated[str, Field(max_length=200)] | None = None
    site_postal_code: Annotated[str, Field(max_length=20)] | None = None
    site_city: Annotated[str, Field(max_length=100)] | None = None
    vat_attestation_required: bool | None = None
    vat_attestation_over_two_years: bool | None = None
    vat_attestation_premises_use: Annotated[str, Field(max_length=100)] | None = None
    vat_attestation_signatory: Annotated[str, Field(max_length=200)] | None = None
    vat_attestation_signed_at: datetime | None = None
    payment_terms: Annotated[str, Field(max_length=500)] | None = None
    late_penalty_rate_bp: VatRateBp | None = None
    recovery_indemnity_cents: Cents | None = None
    mediator_name: Annotated[str, Field(max_length=200)] | None = None
    mediator_url: Annotated[str, Field(pattern=r"^https?://", max_length=500)] | None = None
    notes: Annotated[str, Field(max_length=2000)] | None = None
    status: QuoteStatus | None = None
    valid_until: datetime | None = None


class QuoteLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: int
    position: int
    label: str
    unit: PriceUnit
    quantity: Decimal
    unit_price_cents: int
    vat_rate_bp: int
    total_ht_cents: int
    source_face_id: int | None = None
    source_price_item_code: str | None = None


class VatBucketRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rate_bp: int
    base_cents: int
    tax_cents: int


class QuoteRead(BaseModel):
    """Le devis complet. `warnings` n'est pas décoratif : voir `QuoteRead.warnings`."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: int
    organization_id: int
    project_id: int | None = None
    project_name: str | None = None
    number: str | None = None
    status: QuoteStatus
    issued_at: datetime | None = None
    valid_until: datetime | None = None
    invoice_number: str | None = None
    invoiced_at: datetime | None = None
    due_date: datetime | None = None

    total_ht_cents: int
    total_tva_cents: int
    total_ttc_cents: int

    client_name: str
    client_email: str | None = None
    client_phone: str | None = None
    client_address_line1: str | None = None
    client_address_line2: str | None = None
    client_postal_code: str | None = None
    client_city: str | None = None
    client_country: str | None = None
    client_vat_number: str | None = None
    client_is_consumer: bool

    site_address_line1: str | None = None
    site_address_line2: str | None = None
    site_postal_code: str | None = None
    site_city: str | None = None

    vat_attestation_required: bool
    vat_attestation_over_two_years: bool | None = None
    vat_attestation_premises_use: str | None = None
    vat_attestation_signatory: str | None = None
    vat_attestation_signed_at: datetime | None = None

    payment_terms: str | None = None
    late_penalty_rate_bp: int
    recovery_indemnity_cents: int
    mediator_name: str | None = None
    mediator_url: str | None = None
    notes: str | None = None

    lines: list[QuoteLineRead] = Field(default_factory=list)
    vat_breakdown: list[VatBucketRead] = Field(default_factory=list)
    # Avertissements du métré et du chiffrage, recopiés à la création. Un total est **partiel** dès
    # que cette liste n'est pas vide : une surface que le métré n'a pas su établir est une ligne
    # absente, pas une ligne à zéro. Émettre sans les lire, c'est facturer moins que le chantier.
    warnings: list[str] = Field(default_factory=list)


class QuoteSummary(BaseModel):
    """Vue de liste : ce qui tient dans un tableau, sans charger les lignes."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: int
    number: str | None = None
    invoice_number: str | None = None
    status: QuoteStatus
    client_name: str
    issued_at: datetime | None = None
    valid_until: datetime | None = None
    total_ht_cents: int
    total_ttc_cents: int


class TakeoffRead(BaseModel):
    """Enveloppe du métré.

    Le contenu reste un dictionnaire libre : sa forme dépend de la nature des faces, et un modèle
    Pydantic de six variantes alourdirait le schéma OpenAPI sans rien apporter au client, qui
    aiguille sur `kind`. Même arbitrage que pour le scene graph (`app/api/scene.py`).
    """

    model_config = ConfigDict(extra="forbid")

    project_id: int | None = None
    units: dict[str, str]
    rooms: list[dict[str, Any]]
    totals: dict[str, Any]
    warnings: list[str]
