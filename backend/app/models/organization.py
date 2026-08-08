"""Multi-locataire : organisation, appartenance, invitation.

`docs/strategie-produit.md` §6, point 1 : les droits doivent être portés par une entité qui peut
avoir des sièges et un moyen de paiement, pas par un utilisateur. `Project.owner_id` reste la
trace de qui a créé le projet — c'est une information d'audit — et **n'autorise plus rien**.

Trois tables, et une seule règle d'accès :

- `organization` porte l'identité de l'entreprise. Tous les champs légaux sont **nullables** : on
  ne bloque pas une inscription sur un SIRET que l'artisan ira chercher au premier devis. C'est
  l'émission du devis qui les exigera, pas la création du compte.
- `membership` relie un compte à une organisation avec un rôle. Une appartenance n'autorise
  qu'une fois `accepted_at` renseigné : une invitation en attente est une ligne de `membership`
  sans acceptation, et elle ne doit donner accès à rien entre-temps.
- `invitation` porte le **hachage** du jeton d'invitation, jamais le jeton. Une fuite de la base
  ne doit pas permettre de rejoindre les organisations qu'on y lit.

Le montant du capital social est en **centimes entiers** (`share_capital_cents`), comme tous les
montants du produit. Le reste du modèle est en flottants centimètres et la tentation de continuer
serait forte : un capital de 1 000,10 € stocké en flottant ne se rejoue pas à l'identique.
"""

from datetime import datetime
from enum import StrEnum

# Pas de `from __future__ import annotations` : même raison que dans `app/models/plan.py`, les
# annotations d'un modèle SQLModel sont résolues à l'exécution.
from sqlalchemy import CheckConstraint, DateTime, UniqueConstraint, text
from sqlmodel import Field

from app.models.base import TimestampedModel, value_enum


class OrganizationRole(StrEnum):
    """Rôle d'un compte dans une organisation.

    Quatre rôles et pas davantage : chacun doit rester explicable en une phrase à un artisan.
    """

    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


# Hiérarchie des rôles. Une table explicite plutôt que l'ordre de déclaration de l'énumération :
# insérer un rôle intermédiaire un jour ne doit pas déplacer silencieusement des droits.
ROLE_RANK: dict[OrganizationRole, int] = {
    OrganizationRole.VIEWER: 0,
    OrganizationRole.EDITOR: 1,
    OrganizationRole.ADMIN: 2,
    OrganizationRole.OWNER: 3,
}

# Longueur d'un SHA-256 en hexadécimal. Le jeton d'invitation est un secret aléatoire de 256 bits,
# pas un mot de passe humain : un hachage rapide est ici le bon choix — il n'y a rien à
# force-brutaliser — et c'est le seul moyen d'indexer la colonne pour retrouver l'invitation.
TOKEN_HASH_LENGTH = 64


class Organization(TimestampedModel, table=True):
    """Le locataire. Porte l'identité légale de l'entreprise et les sièges.

    Les bornes sont répétées en base (`CheckConstraint`) et pas seulement dans les schémas
    Pydantic : SQLAdmin, la CLI, Celery et `psql` écrivent sans passer par l'API, et SQLModel
    désactive la validation `Field(...)` sur les modèles `table=True` (leçon du lot L4).
    """

    __tablename__ = "organization"
    __table_args__ = (
        # `length(...) > 0` et non `trim` : c'est exactement ce que l'API refuse (`min_length=1`).
        # Une contrainte plus stricte transformerait une requête acceptée en erreur 500.
        CheckConstraint("length(name) > 0", name="ck_organization_name_not_empty"),
        CheckConstraint("length(slug) > 0", name="ck_organization_slug_not_empty"),
        CheckConstraint(
            "share_capital_cents IS NULL OR share_capital_cents >= 0",
            name="ck_organization_share_capital_not_negative",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=200)
    # Identifiant lisible, unique, utilisé dans les URL de partage et les futurs sous-domaines.
    slug: str = Field(max_length=100, unique=True, index=True)

    # --- Identité légale (`docs/strategie-produit.md` §2) --------------------------------------
    # Sans ces mentions, un devis de bâtiment est inopposable. Elles sont nullables parce qu'elles
    # sont exigées à l'émission du devis, pas à l'inscription.
    siret: str | None = Field(default=None, max_length=14)
    legal_form: str | None = Field(default=None, max_length=50)
    share_capital_cents: int | None = Field(default=None)
    rcs: str | None = Field(default=None, max_length=100)
    address_line1: str | None = Field(default=None, max_length=200)
    address_line2: str | None = Field(default=None, max_length=200)
    postal_code: str | None = Field(default=None, max_length=20)
    city: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, max_length=100)
    vat_number: str | None = Field(default=None, max_length=20)

    # --- Assurance décennale -------------------------------------------------------------------
    # Obligatoire sur les devis **et** les factures du bâtiment, avec les trois informations :
    # qui assure, sous quel numéro, et sur quelle zone géographique.
    decennial_insurer: str | None = Field(default=None, max_length=200)
    decennial_policy_number: str | None = Field(default=None, max_length=100)
    decennial_coverage_area: str | None = Field(default=None, max_length=200)

    # --- Contact ------------------------------------------------------------------------------
    # Distinct de l'adresse du compte : la facturation de l'abonnement part au cabinet comptable
    # bien plus souvent qu'à l'artisan lui-même.
    billing_email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=30)
    logo_url: str | None = Field(default=None, max_length=500)


class Membership(TimestampedModel, table=True):
    """Appartenance d'un compte à une organisation, avec son rôle.

    `accepted_at` est la condition d'accès et non un simple horodatage : une ligne sans
    acceptation est une invitation en attente, elle n'ouvre rien. Confondre les deux donnerait
    l'accès à quiconque a été invité, y compris par erreur, avant même d'avoir répondu.
    """

    __tablename__ = "membership"
    __table_args__ = (
        # Cloisonne le modèle : un compte a **un** rôle par organisation, pas une pile de rôles
        # dont on ne saurait plus lequel l'emporte. L'index unique servi par cette contrainte
        # couvre aussi la recherche « les organisations de ce compte », qui a `user_id` en tête.
        UniqueConstraint("user_id", "organization_id", name="uq_membership_user_organization"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", ondelete="CASCADE")
    # Indexé à part : la liste des membres filtre sur cette seule colonne, que la contrainte
    # d'unicité ci-dessus ne sert pas (elle a `user_id` en tête).
    organization_id: int = Field(foreign_key="organization.id", index=True, ondelete="CASCADE")
    role: OrganizationRole = Field(  # type: ignore[call-overload]
        default=OrganizationRole.VIEWER,
        sa_type=value_enum(OrganizationRole, "organizationrole"),
        sa_column_kwargs={"server_default": text("'viewer'")},
    )

    invited_at: datetime | None = Field(  # type: ignore[call-overload]
        default=None, sa_type=DateTime(timezone=True), nullable=True
    )
    accepted_at: datetime | None = Field(  # type: ignore[call-overload]
        default=None, sa_type=DateTime(timezone=True), nullable=True
    )


class Invitation(TimestampedModel, table=True):
    """Invitation à rejoindre une organisation, adressée à une adresse e-mail.

    Seul le **hachage** du jeton est stocké. Le jeton en clair n'existe qu'une fois, dans la
    réponse HTTP qui crée l'invitation : le relire depuis la base est impossible, et une copie de
    la base ne permet pas de rejoindre les organisations qu'elle contient.

    La ligne est conservée après acceptation (`accepted_at`) plutôt que supprimée : elle empêche
    la réutilisation du jeton et garde la trace de qui a ouvert la porte à qui.
    """

    __tablename__ = "invitation"
    __table_args__ = (
        CheckConstraint("length(email) > 0", name="ck_invitation_email_not_empty"),
    )

    id: int | None = Field(default=None, primary_key=True)
    organization_id: int = Field(foreign_key="organization.id", index=True, ondelete="CASCADE")
    email: str = Field(max_length=320)
    role: OrganizationRole = Field(  # type: ignore[call-overload]
        default=OrganizationRole.VIEWER,
        sa_type=value_enum(OrganizationRole, "organizationrole"),
        sa_column_kwargs={"server_default": text("'viewer'")},
    )
    token_hash: str = Field(max_length=TOKEN_HASH_LENGTH, unique=True, index=True)

    expires_at: datetime = Field(  # type: ignore[call-overload]
        sa_type=DateTime(timezone=True), nullable=False
    )
    accepted_at: datetime | None = Field(  # type: ignore[call-overload]
        default=None, sa_type=DateTime(timezone=True), nullable=True
    )
