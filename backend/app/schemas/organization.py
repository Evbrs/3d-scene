"""Schémas Pydantic du multi-locataire.

Séparés des modèles SQLModel pour la même raison que le reste de l'API : ce qu'un client peut
écrire n'est pas ce que la base stocke. Un client ne fixe jamais un `id`, un `slug`, un
`token_hash` ni un horodatage.

Sur la mise à jour d'une organisation, un champ **absent** veut dire « ne touche pas » et un champ
à `null` veut dire « efface ». Les deux ont un sens ici, contrairement au plan 2D : toutes les
colonnes d'identité légale sont nullables, et un artisan doit pouvoir retirer un numéro de TVA
saisi par erreur. `PartialUpdate` (`app/schemas/plan.py`), qui refuse les nuls, ne convient donc
pas — c'est bien une différence de contrat, pas un oubli.
"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.models.organization import OrganizationRole

# Le SIRET est un identifiant à 14 chiffres. Le format seul est vérifié : la clé de contrôle de
# Luhn rejetterait des SIRET réellement attribués (celui de La Poste, notamment), et refuser un
# numéro valable au moment d'émettre un devis serait pire que de laisser passer une coquille.
Siret = Annotated[str, Field(pattern=r"^[0-9]{14}$")]
# Numéro de TVA intracommunautaire : deux lettres de pays, puis 2 à 13 caractères.
VatNumber = Annotated[str, Field(pattern=r"^[A-Za-z]{2}[0-9A-Za-z]{2,13}$")]
# Le logo est réaffiché dans l'interface et incrusté dans les PDF : n'accepter que `http(s)` ferme
# `javascript:` et `data:`, qui feraient de ce champ un vecteur d'injection stocké.
LogoUrl = Annotated[str, Field(pattern=r"^https?://", max_length=500)]

Name = Annotated[str, Field(min_length=1, max_length=200)]
# Le slug est dérivé du nom côté serveur ; le client peut le proposer mais pas inventer sa forme.
Slug = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=2, max_length=100)]


class CompanyFields(BaseModel):
    """Les mentions sans lesquelles un devis de bâtiment est inopposable.

    Toutes facultatives : elles sont exigées à l'émission du devis, pas à l'inscription. Bloquer
    la création d'un compte sur un SIRET que l'artisan n'a pas sous la main est le meilleur moyen
    de le perdre avant qu'il ait vu le produit.
    """

    model_config = ConfigDict(extra="forbid")

    siret: Siret | None = None
    legal_form: Annotated[str, Field(max_length=50)] | None = None
    # En **centimes entiers**, comme tout montant du produit : un capital de 1 000,10 € stocké en
    # flottant ne se relit pas à l'identique, et il finit imprimé sur un document contractuel.
    share_capital_cents: Annotated[int, Field(ge=0, le=10**15)] | None = None
    rcs: Annotated[str, Field(max_length=100)] | None = None
    address_line1: Annotated[str, Field(max_length=200)] | None = None
    address_line2: Annotated[str, Field(max_length=200)] | None = None
    postal_code: Annotated[str, Field(max_length=20)] | None = None
    city: Annotated[str, Field(max_length=100)] | None = None
    country: Annotated[str, Field(max_length=100)] | None = None
    vat_number: VatNumber | None = None

    decennial_insurer: Annotated[str, Field(max_length=200)] | None = None
    decennial_policy_number: Annotated[str, Field(max_length=100)] | None = None
    decennial_coverage_area: Annotated[str, Field(max_length=200)] | None = None

    billing_email: EmailStr | None = None
    phone: Annotated[str, Field(max_length=30)] | None = None
    logo_url: LogoUrl | None = None


class OrganizationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    # Facultatif : dérivé du nom quand il n'est pas fourni, puis rendu unique côté serveur.
    slug: Slug | None = None


class OrganizationUpdate(CompanyFields):
    """Mise à jour des champs d'entreprise, et du nom.

    Le `slug` n'est pas modifiable : il apparaît dans des URL déjà diffusées, et le changer
    casserait silencieusement les liens en circulation.
    """

    name: Name | None = None

    @model_validator(mode="after")
    def _reject_a_null_name(self) -> "OrganizationUpdate":
        """`name` est la seule colonne non nullable de la table : `null` n'y a pas de sens.

        Sans ce contrôle, `{"name": null}` traversait la validation, partait en violation
        `NOT NULL` et ressortait en 500 — n'importe quel administrateur pouvait provoquer une
        erreur serveur. Le `| None` de l'annotation dit « facultatif », pas « effaçable ».
        """
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError(
                "le nom d'une organisation ne peut pas être effacé — omettez le champ pour ne "
                "pas le modifier"
            )
        return self


class OrganizationRead(CompanyFields):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: int
    name: str
    slug: str
    created_at: datetime


class MemberRead(BaseModel):
    """Une ligne de la liste des membres.

    L'adresse e-mail est jointe depuis le compte : sans elle, la liste n'affiche que des
    identifiants numériques, et personne ne sait qui retirer.
    """

    model_config = ConfigDict(from_attributes=True)

    user_id: int
    email: EmailStr
    role: OrganizationRole
    invited_at: datetime | None = None
    accepted_at: datetime | None = None


class MemberRoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: OrganizationRole


class InvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    role: OrganizationRole = OrganizationRole.VIEWER
    # Bornée : une invitation qui n'expire jamais est une porte laissée ouverte dans une boîte
    # mail qu'on ne contrôle plus.
    expires_in_days: Annotated[int, Field(ge=1, le=30)] = 7


class InvitationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    organization_id: int
    email: EmailStr
    role: OrganizationRole
    expires_at: datetime
    accepted_at: datetime | None = None


class InvitationCreated(InvitationRead):
    """Réponse de création. **Seul endroit** où le jeton en clair existe.

    La base n'en garde que le hachage : ce jeton n'est plus jamais relisible, ni par l'API, ni par
    le back-office, ni par quelqu'un qui obtiendrait une copie de la base.
    """

    token: str


class InvitationAccept(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: Annotated[str, Field(min_length=16, max_length=128)]
