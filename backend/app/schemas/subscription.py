"""Schémas de l'offre, de l'abonnement et des murs de paiement.

Deux publics, et ils n'ont pas les mêmes droits de lecture.

La page tarifs est **publique** : elle sert `plan_catalog` filtré sur `is_public` et trié sur
`sort_order`, avec les libellés. Rien de ce qu'elle contient n'appartient à un locataire — c'est
une grille de prix, la même pour tout le monde.

La page compte est **cloisonnée** : elle rend l'abonnement d'une organisation, sa consommation de
la période en cours et l'état de l'essai. Les identifiants du prestataire de paiement
(`external_customer_id`, `external_subscription_id`) n'en font délibérément pas partie : ils
n'aident aucun utilisateur et n'ont aucune raison de sortir de la base.

Les corps de refus (`PaywallDetail`, `QuotaDetail`) sont **machine-lisibles au premier niveau**,
comme le 409 du plan (`app/schemas/plan.py`). Le frontend doit pouvoir proposer le bon palier sans
analyser une phrase en français, qui changerait à la première reformulation.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.models.billing_plan import SubscriptionStatus


class PlanRead(BaseModel):
    """Un palier de la grille, tel qu'il est en base.

    `limits` et `features` sortent bruts : la page tarifs les affiche avec les libellés servis à
    côté, et n'en code aucun en dur. Une limite déplacée par `UPDATE` change donc la page sans
    déploiement, ce qui est exactement l'intention de `plan_catalog`.
    """

    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    tagline: str
    monthly_price_cents: int
    # Nul pour un palier « sur devis ».
    yearly_price_cents: int | None
    seat_price_cents: int
    currency: str
    limits: dict[str, Any]
    features: dict[str, Any]
    sort_order: int


class PlanCatalogRead(BaseModel):
    """La grille complète, libellés compris.

    Les libellés accompagnent le catalogue au lieu de vivre côté navigateur : une clé ajoutée en
    base sans libellé s'affiche telle quelle, plutôt que de disparaître silencieusement de la page.
    """

    plans: list[PlanRead]
    feature_labels: dict[str, str]
    limit_labels: dict[str, str]
    # Distincts des libellés de limite : `projects_active` est ce qu'on compte, `active_projects`
    # ce qu'on plafonne. Les confondre afficherait la mauvaise ligne sur la page compte.
    metric_labels: dict[str, str]
    trial_days: int


class SubscriptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    plan_code: str
    status: SubscriptionStatus
    current_period_start: datetime
    current_period_end: datetime
    trial_ends_at: datetime | None
    cancel_at: datetime | None
    seats: int


class UsageRead(BaseModel):
    """Consommation d'une métrique sur la période de facturation en cours.

    `limit` à `null` veut dire **illimité**, jamais zéro : les confondre afficherait « 3 / 0 » à un
    client qui n'a aucun plafond.
    """

    metric: str
    value: int
    limit: int | None


class EntitlementRead(BaseModel):
    """Ce à quoi une organisation a droit, et ce qu'elle a consommé.

    C'est la réponse qui alimente la page compte **et** les boîtes de dialogue des murs de
    paiement : une seule route, pour que les deux ne puissent pas afficher deux vérités.
    """

    organization_id: int
    plan: PlanRead
    subscription: SubscriptionRead | None
    period_start: datetime
    period_end: datetime
    # Vrai tant que l'essai n'a jamais été ouvert. Il ne démarre pas à l'inscription mais au
    # premier geste monétisé (`docs/strategie-produit.md` §4).
    trial_available: bool
    trial_ends_at: datetime | None
    usage: list[UsageRead]
    # Chantiers passés en lecture seule par le déclassement. Jamais supprimés : ils restent
    # lisibles, et c'est la seule issue qui ne détruit pas la confiance.
    archived_project_ids: list[int]


class PaywallDetail(BaseModel):
    """Corps du 402. Le frontend y lit quel palier proposer, sans analyser le message."""

    detail: str
    code: Literal["feature_required"] = "feature_required"
    feature: str
    current_plan: str
    # Nul si **aucun** palier du catalogue n'accorde la fonctionnalité — cas d'une clé mal
    # orthographiée ou d'une fonctionnalité retirée de toute la grille.
    required_plan: str | None


class QuotaDetail(BaseModel):
    """Corps du 429. Même principe : la métrique, le plafond atteint et le palier qui le lève."""

    detail: str
    code: Literal["quota_exceeded"] = "quota_exceeded"
    metric: str
    limit: int
    current: int
    current_plan: str
    required_plan: str | None
