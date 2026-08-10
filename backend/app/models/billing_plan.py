"""Offres, abonnements et compteurs d'usage (`docs/strategie-produit.md` §4).

C'est la frontière technique entre le gratuit et le payant. Quatre tables, et chacune existe pour
une raison qu'on ne peut pas rattraper après coup.

**`plan_catalog` porte les limites en base, jamais dans le code.** Déplacer une limite ou accorder
une remise commerciale doit être une ligne SQL, pas un déploiement — sinon chaque négociation
devient un ticket de développement, et surtout on n'a aucun correctif d'urgence le jour où un
quota bloque un client payant en pleine journée de chantier. Les codes de plan et les clés de
`limits` / `features` sont donc des chaînes libres et non des énumérations : ajouter un palier
« Réseau Bretagne » à 320 € doit rester un `INSERT`.

**`subscription` porte ses colonnes d'identifiants externes dès la première migration**, même
vides. Aucun prestataire de paiement n'est intégré ici (le propriétaire n'en veut pas comme
dépendance) : on pose le modèle et la frontière, l'encaissement viendra derrière un adaptateur.
Les ajouter plus tard signifierait migrer une table qui reçoit déjà des webhooks en production,
c'est-à-dire au pire moment possible.

**`usage_counter` est incrémenté par un unique `INSERT … ON CONFLICT DO UPDATE … RETURNING`**
(`app/services/quotas.py`). Un `SELECT` suivi d'un `UPDATE` laisse deux onglets ouverts passer
au-dessus de la limite, et c'est le défaut le plus courant de ce genre de table. La contrainte
d'unicité n'est donc pas une hygiène : c'est la cible du `ON CONFLICT`, donc la moitié du
mécanisme.

**`usage_event` est append-only et porte une clé d'idempotence.** Pour les exports, cette clé est
l'identifiant de tâche Celery : un rejeu après incident du courtier se traduirait sinon en
surfacturation. Les métriques sont posées **toutes** dès maintenant, y compris celles qu'on ne
facture pas — ajouter une métrique plus tard est facile, reconstituer son historique est
impossible.

Comme partout dans le produit, tout montant est un entier de centimes, et les bornes sont
répétées en base (`CheckConstraint`) : SQLAdmin, la CLI, Celery et `psql` écrivent sans passer par
l'API, et SQLModel désactive la validation `Field(...)` sur les modèles `table=True`.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Index,
    UniqueConstraint,
    text,
    true,
)
from sqlalchemy.ext.mutable import MutableDict
from sqlmodel import Field

from app.models.base import TimestampedModel, json_type, value_enum

# Longueur des codes de plan et des clés de métrique. Volontairement courte : ce sont des
# identifiants lisibles par un humain, retrouvés dans un `psql` en incident.
PLAN_CODE_LENGTH = 30
METRIC_LENGTH = 40
# Une clé d'idempotence peut être un identifiant de tâche Celery (UUID) préfixé par la métrique.
IDEMPOTENCY_KEY_LENGTH = 120
EXTERNAL_ID_LENGTH = 120

# Durée de l'essai sans carte (`docs/strategie-produit.md` §4). C'est la **valeur initiale** de
# `plan_catalog.trial_days`, plus jamais la vérité : allonger l'essai à 30 jours pour une campagne
# est une ligne SQL, comme un plafond déplacé ou une remise (spec §10, amendement A14). Elle reste
# ici parce que le semis et la migration ont besoin d'une valeur de départ.
TRIAL_DAYS = 14


class SubscriptionStatus(StrEnum):
    """Cycle de vie d'un abonnement.

    Fermé à quatre valeurs, contrairement aux codes de plan : ce sont les états que le code sait
    interpréter, et en inventer un cinquième par `INSERT` rendrait une organisation ni autorisée
    ni refusée. C'est donc bien un ENUM en base.

    `past_due` reste **autorisé** : couper l'accès d'un client dont le prélèvement a échoué le
    matin même, en plein chantier, coûte plus cher que les quelques jours de relance. La coupure
    est un passage explicite en `canceled`.
    """

    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"


class UsageMetric(StrEnum):
    """Métriques posées dès maintenant, facturées ou non.

    Les trois dernières ne servent à aucun quota : ce sont les **métriques produit**. Sans elles,
    on ne peut ni corriger l'accueil, ni arbitrer une grille tarifaire — et leur historique ne se
    reconstitue pas, contrairement à une facture.
    """

    PROJECTS_ACTIVE = "projects_active"
    EXPORTS_PDF = "exports_pdf"
    QUOTES_ISSUED = "quotes_issued"
    SHARED_VIEW_HITS = "shared_view_hits"
    AI_RUNS = "ai_runs"
    API_CALLS = "api_calls"
    # --- Métriques produit ---
    # Premier geste qui prouve que le produit a été compris : une pièce dessinée.
    ACTIVATION = "activation"
    # Délai, en secondes, entre l'inscription et le premier devis établi.
    TIME_TO_FIRST_QUOTE = "time_to_first_quote"
    # Écran où l'utilisateur s'est arrêté avant d'avoir produit quoi que ce soit.
    DROP_OFF = "drop_off"


class PlanCatalog(TimestampedModel, table=True):
    """Un palier de la grille tarifaire (`docs/strategie-produit.md` §4).

    Le code est la clé primaire, et il est **stable** : c'est lui que référencent les abonnements
    et les journaux. Renommer commercialement « Artisan » ne doit pas casser les abonnements en
    cours, d'où la séparation entre `code` (technique, figé) et `name` (commercial, libre).

    `limits` et `features` sont des dictionnaires JSONB volontairement ouverts. Une valeur de
    limite à `null` veut dire **illimité** et non « zéro » : les confondre transformerait un palier
    sans plafond en palier qui refuse tout, et c'est la panne la plus coûteuse imaginable ici.
    """

    __tablename__ = "plan_catalog"
    __table_args__ = (
        CheckConstraint("length(code) > 0", name="ck_plan_catalog_code_not_empty"),
        CheckConstraint("length(name) > 0", name="ck_plan_catalog_name_not_empty"),
        CheckConstraint(
            "monthly_price_cents >= 0", name="ck_plan_catalog_monthly_price_not_negative"
        ),
        CheckConstraint(
            "yearly_price_cents IS NULL OR yearly_price_cents >= 0",
            name="ck_plan_catalog_yearly_price_not_negative",
        ),
        CheckConstraint(
            "seat_price_cents >= 0", name="ck_plan_catalog_seat_price_not_negative"
        ),
        CheckConstraint("trial_days >= 0", name="ck_plan_catalog_trial_days_not_negative"),
    )

    code: str = Field(primary_key=True, max_length=PLAN_CODE_LENGTH)
    name: str = Field(max_length=100)
    # La colonne « Pour qui » de la grille. Elle vit ici et non dans la page tarifs : celle-ci doit
    # être alimentée par `plan_catalog` et non par une grille codée en dur côté navigateur.
    tagline: str = Field(default="", max_length=200, sa_column_kwargs={"server_default": ""})

    monthly_price_cents: int = Field(default=0)
    # Nul pour un palier « sur devis » : inventer un tarif annuel pour le Réseau afficherait un
    # prix que personne n'a négocié.
    yearly_price_cents: int | None = Field(default=None)
    # Le « + 19 €/siège » du palier Entreprise. En base pour la même raison que le reste : la page
    # tarifs ne doit rien coder en dur.
    seat_price_cents: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    currency: str = Field(default="EUR", max_length=3, sa_column_kwargs={"server_default": "EUR"})

    limits: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(
            MutableDict.as_mutable(json_type()), nullable=False, server_default=text("'{}'")
        ),
    )
    features: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(
            MutableDict.as_mutable(json_type()), nullable=False, server_default=text("'{}'")
        ),
    )

    # Durée de l'essai sans carte **offert par ce palier**, en jours. Zéro veut dire « aucun
    # essai », et c'est le défaut : seul le palier d'essai (`seed_plans.TRIAL_PLAN_CODE`) en porte
    # un. Ici et non dans une constante Python, pour la même raison que les limites — allonger
    # l'essai d'une campagne commerciale ne doit pas être un déploiement.
    trial_days: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})

    # Un palier négocié pour un réseau particulier existe en base sans figurer sur la page tarifs.
    is_public: bool = Field(default=True, sa_column_kwargs={"server_default": true()})
    sort_order: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})


class Subscription(TimestampedModel, table=True):
    """Abonnement d'une organisation à un palier.

    Une seule ligne vivante par organisation, tenue par la route et non par un index partiel :
    ceux-ci ne se reconstruisent pas en mode batch SQLite, et la suite de tests tourne dessus —
    même arbitrage que `PriceBook.is_default`.

    Les lignes ne sont **jamais supprimées**, même après résiliation : c'est ce qui empêche un
    second essai gratuit, et c'est la seule trace de ce qu'un client a payé.
    """

    __tablename__ = "subscription"
    __table_args__ = (
        # Exactement la requête de résolution des droits : « l'abonnement vivant de cette
        # organisation ». Elle est sur le chemin de chaque création de projet et de chaque devis.
        Index("ix_subscription_organization_status", "organization_id", "status"),
        CheckConstraint("seats >= 1", name="ck_subscription_seats_at_least_one"),
        CheckConstraint(
            "current_period_end > current_period_start",
            name="ck_subscription_period_ordered",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    organization_id: int = Field(foreign_key="organization.id", index=True, ondelete="CASCADE")
    # Référence au catalogue par le **code** : c'est lui qui est stable, et c'est lui qu'on lit
    # dans un `psql` en incident. Pas d'`ondelete` : supprimer un palier auquel des clients sont
    # abonnés doit échouer, pas les laisser sans droits.
    plan_code: str = Field(foreign_key="plan_catalog.code", max_length=PLAN_CODE_LENGTH)
    status: SubscriptionStatus = Field(  # type: ignore[call-overload]
        default=SubscriptionStatus.TRIALING,
        sa_type=value_enum(SubscriptionStatus, "subscriptionstatus"),
        sa_column_kwargs={"server_default": text("'trialing'")},
    )

    # Période de **facturation**, et c'est elle qui sert d'ancre aux compteurs d'usage. Le mois
    # calendaire offrirait une remise à zéro gratuite le 1er à un abonnement souscrit le 20.
    current_period_start: datetime = Field(  # type: ignore[call-overload]
        sa_type=DateTime(timezone=True), nullable=False
    )
    current_period_end: datetime = Field(  # type: ignore[call-overload]
        sa_type=DateTime(timezone=True), nullable=False
    )
    trial_ends_at: datetime | None = Field(  # type: ignore[call-overload]
        default=None, sa_type=DateTime(timezone=True), nullable=True
    )
    # Résiliation programmée : l'abonné garde ses droits jusqu'à cette date. Distincte de
    # `canceled`, qui est l'état une fois la date passée.
    cancel_at: datetime | None = Field(  # type: ignore[call-overload]
        default=None, sa_type=DateTime(timezone=True), nullable=True
    )
    seats: int = Field(default=1, sa_column_kwargs={"server_default": text("1")})

    # Vides tant qu'aucun prestataire n'est branché. Elles existent quand même : les ajouter le
    # jour où la table reçoit des webhooks serait une migration sur table chaude.
    external_customer_id: str | None = Field(default=None, max_length=EXTERNAL_ID_LENGTH)
    external_subscription_id: str | None = Field(default=None, max_length=EXTERNAL_ID_LENGTH)


class UsageCounter(TimestampedModel, table=True):
    """Compteur agrégé d'une métrique sur une période de facturation.

    La contrainte d'unicité est la cible du `ON CONFLICT` (`app/services/quotas.py`) : sans elle
    l'incrément atomique n'a nulle part où atterrir et retomberait sur un `SELECT` puis `UPDATE`,
    c'est-à-dire précisément la course que ce modèle existe pour supprimer.

    `period_start` est le premier jour de la période de **facturation** de l'organisation, jamais
    le 1er du mois.
    """

    __tablename__ = "usage_counter"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "metric",
            "period_start",
            name="uq_usage_counter_organization_metric_period",
        ),
        CheckConstraint("value >= 0", name="ck_usage_counter_value_not_negative"),
        CheckConstraint("length(metric) > 0", name="ck_usage_counter_metric_not_empty"),
    )

    id: int | None = Field(default=None, primary_key=True)
    organization_id: int = Field(foreign_key="organization.id", index=True, ondelete="CASCADE")
    # Chaîne libre et non ENUM : ajouter une métrique doit rester un déploiement de code, pas un
    # `ALTER TYPE` sur une base de production. `UsageMetric` énumère celles que le produit connaît.
    metric: str = Field(max_length=METRIC_LENGTH)
    period_start: datetime = Field(  # type: ignore[call-overload]
        sa_type=DateTime(timezone=True), nullable=False
    )
    value: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})


class UsageEvent(TimestampedModel, table=True):
    """Journal append-only de la consommation. Aucune route ne le modifie ni ne l'efface.

    `idempotency_key` est unique et **obligatoire** : c'est ce qui rend le rejeu inoffensif. Pour
    un export, elle vaut l'identifiant de la tâche Celery — après un incident du courtier, la même
    tâche revient, et sans cette clé elle serait comptée deux fois. Un événement rejoué n'incrémente
    pas le compteur : l'`INSERT` échoue sur la contrainte, et l'appelant le voit.

    `user_id` est en `SET NULL` : la consommation d'un compte supprimé reste comptée pour
    l'organisation qui l'a payée, alors que le compte, lui, doit pouvoir disparaître (RGPD).
    """

    __tablename__ = "usage_event"
    __table_args__ = (
        CheckConstraint("quantity >= 0", name="ck_usage_event_quantity_not_negative"),
        CheckConstraint("length(metric) > 0", name="ck_usage_event_metric_not_empty"),
        CheckConstraint(
            "length(idempotency_key) > 0", name="ck_usage_event_idempotency_key_not_empty"
        ),
        # La lecture réelle du journal : « tout ce qu'a consommé cette organisation depuis telle
        # date ». Sans cet index, reconstituer un compteur balaie la table entière.
        Index("ix_usage_event_organization_occurred", "organization_id", "occurred_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    organization_id: int = Field(foreign_key="organization.id", ondelete="CASCADE")
    user_id: int | None = Field(default=None, foreign_key="user.id", ondelete="SET NULL")
    metric: str = Field(max_length=METRIC_LENGTH)
    quantity: int = Field(default=1, sa_column_kwargs={"server_default": text("1")})
    idempotency_key: str = Field(max_length=IDEMPOTENCY_KEY_LENGTH, unique=True, index=True)

    # L'attribut Python ne peut pas s'appeler `metadata` : c'est le nom que la base déclarative
    # SQLAlchemy réserve sur toute classe mappée. La **colonne**, elle, s'appelle bien `metadata`,
    # parce que c'est sous ce nom qu'on la cherchera en incident.
    event_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(
            "metadata",
            MutableDict.as_mutable(json_type()),
            nullable=False,
            server_default=text("'{}'"),
        ),
    )

    # Distinct de `created_at` : l'événement peut être écrit après coup (rejeu d'une tâche, import
    # d'un journal), et c'est la date du geste qui compte pour la facturation.
    occurred_at: datetime = Field(  # type: ignore[call-overload]
        sa_type=DateTime(timezone=True), nullable=False
    )
