"""API des offres et de l'abonnement (`docs/strategie-produit.md` §4).

Trois routes, et surtout une **absente**.

`GET /api/plans` est la seule route publique du produit qui ne soit pas un lien de partage : une
grille de prix est publique par nature, et la page tarifs doit s'afficher avant l'inscription.
Elle ne rend rien qui appartienne à un locataire.

`GET /api/organizations/{id}/subscription` sert à la fois la page compte et les boîtes de dialogue
des murs de paiement. Une seule route pour les deux : deux vérités affichées côte à côte finissent
toujours par diverger. C'est aussi le point où le **déclassement** est appliqué — voir plus bas.

`POST /api/organizations/{id}/subscription/trial` ouvre l'essai de 14 jours, sans carte. C'est le
bouton « retirer le filigrane » du mur d'export : le fichier filigrané s'est déjà téléchargé, et
l'artisan sait donc que le calcul est juste avant qu'on lui demande quoi que ce soit.

**Ce qui n'existe pas, et pourquoi.** Aucune route ne change de palier. Aucun prestataire de
paiement n'est intégré dans ce lot (le propriétaire n'en veut pas comme dépendance) : une route
de changement de palier sans encaissement laisserait n'importe quel administrateur s'attribuer le
palier Entreprise gratuitement. Tant que l'adaptateur de paiement n'existe pas, un changement de
palier est un geste commercial, fait en base — ce que `plan_catalog` rend précisément possible
sans déploiement.
"""

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, SessionDep
from app.api.permissions import require_membership
from app.models.billing_plan import TRIAL_DAYS, UsageMetric
from app.models.organization import OrganizationRole
from app.schemas.subscription import (
    EntitlementRead,
    PlanCatalogRead,
    PlanRead,
    SubscriptionRead,
    UsageRead,
)
from app.services.quotas import (
    Entitlement,
    enforce_active_project_limit,
    load_plans,
    resolve_entitlement,
    start_trial,
    usage_snapshot,
)
from app.services.seed_plans import (
    FEATURE_LABELS,
    LIMIT_LABELS,
    METRIC_LABELS,
    METRIC_LIMITS,
)

router = APIRouter(prefix="/api", tags=["abonnement"])


@router.get("/plans", response_model=PlanCatalogRead)
async def read_plans(session: SessionDep) -> PlanCatalogRead:
    """Grille tarifaire publique, lue en base et non codée en dur.

    Le tri est celui de `sort_order`, et le filtre celui de `is_public` : un palier négocié pour un
    réseau particulier existe en base sans figurer sur la page tarifs. Le prix puis le code
    départagent deux paliers au même rang, pour qu'un palier ajouté par `INSERT` sans `sort_order`
    n'affiche pas la grille dans l'ordre de lecture de la table.
    """
    plans = await load_plans(session)
    public = sorted(
        (plan for plan in plans.values() if plan.is_public),
        key=lambda plan: (plan.sort_order, plan.monthly_price_cents, plan.code),
    )
    return PlanCatalogRead(
        plans=[PlanRead.model_validate(plan) for plan in public],
        feature_labels=FEATURE_LABELS,
        limit_labels=LIMIT_LABELS,
        metric_labels=METRIC_LABELS,
        trial_days=TRIAL_DAYS,
    )


def _usage_lines(entitlement: Entitlement, snapshot: dict[str, int]) -> list[UsageRead]:
    """Consommation de la période, dans l'ordre des métriques déclarées.

    Toutes les métriques sont rendues, y compris celles qu'aucune limite ne plafonne : la page
    compte doit montrer l'usage réel, pas seulement ce qui approche d'un mur.
    """
    return [
        UsageRead(
            metric=str(metric),
            value=snapshot.get(str(metric), 0),
            limit=(
                entitlement.limit(METRIC_LIMITS[metric]) if metric in METRIC_LIMITS else None
            ),
        )
        for metric in UsageMetric
    ]


def _to_read(
    entitlement: Entitlement, snapshot: dict[str, int], archived: list[int]
) -> EntitlementRead:
    """Sérialisation commune aux deux routes : elles doivent rendre exactement la même forme."""
    return EntitlementRead(
        organization_id=entitlement.organization_id,
        plan=PlanRead.model_validate(entitlement.plan),
        subscription=(
            SubscriptionRead.model_validate(entitlement.subscription)
            if entitlement.subscription is not None
            else None
        ),
        period_start=entitlement.period_start,
        period_end=entitlement.period_end,
        trial_available=entitlement.trial_available,
        trial_ends_at=entitlement.trial_ends_at,
        usage=_usage_lines(entitlement, snapshot),
        archived_project_ids=archived,
    )


@router.get("/organizations/{organization_id}/subscription", response_model=EntitlementRead)
async def read_subscription(
    organization_id: int, session: SessionDep, current_user: CurrentUser
) -> EntitlementRead:
    """Droits, abonnement et consommation de l'organisation.

    C'est ici, et seulement ici, que le **déclassement** est appliqué : les chantiers excédentaires
    passent en lecture seule (`archived_at`), jamais à la corbeille. Le faire pendant une simple
    lecture de projet ferait écrire n'importe quelle requête ; le faire dans une tâche planifiée
    demanderait un ordonnanceur que le dépôt n'a pas. La page compte et la boîte de dialogue du
    mur de paiement passent toutes les deux par ici, donc la réconciliation a lieu au moment exact
    où l'utilisateur regarde ce qu'il lui reste.
    """
    await require_membership(session, organization_id, current_user, OrganizationRole.VIEWER)

    entitlement = await resolve_entitlement(session, organization_id)
    archived = await enforce_active_project_limit(session, entitlement)
    snapshot = await usage_snapshot(session, entitlement)
    await session.commit()

    return _to_read(entitlement, snapshot, archived)


@router.post(
    "/organizations/{organization_id}/subscription/trial",
    response_model=EntitlementRead,
    status_code=status.HTTP_201_CREATED,
)
async def open_trial(
    organization_id: int, session: SessionDep, current_user: CurrentUser
) -> EntitlementRead:
    """Ouvre l'essai de 14 jours du palier Artisan, sans carte.

    Idempotente en pratique : un essai déjà consommé ne rend pas une erreur mais l'état courant.
    Renvoyer un 409 obligerait le frontend à traiter un cas qui ne change rien pour l'utilisateur —
    il voulait connaître ses droits, il les obtient.

    `editor` suffit : c'est le rôle qui pose le geste monétisé (dessiner, chiffrer, exporter), et
    l'essai est déclenché par ce geste-là. Exiger `admin` ferait échouer l'ouverture automatique au
    moment précis où elle doit être invisible.
    """
    await require_membership(session, organization_id, current_user, OrganizationRole.EDITOR)

    await start_trial(session, organization_id)
    entitlement = await resolve_entitlement(session, organization_id)
    snapshot = await usage_snapshot(session, entitlement)
    await session.commit()

    return _to_read(entitlement, snapshot, [])
