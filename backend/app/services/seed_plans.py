"""Grille tarifaire de référence (`docs/strategie-produit.md` §4).

Ce module est la **valeur initiale** de `plan_catalog`, pas sa vérité. La vérité est en base :
une remise, un plafond déplacé ou un palier négocié se font par `UPDATE`, et ce fichier ne les
reprend pas. C'est délibéré et c'est tout l'intérêt du modèle — sinon chaque négociation
commerciale redeviendrait un déploiement, et il n'existerait aucun correctif d'urgence le jour où
un quota bloque un client payant en pleine journée de chantier.

Le semis est donc **non destructif** : il crée les paliers absents et ne touche jamais un palier
existant. Le rejouer après une remise accordée à la main ne l'efface pas.

Il est appelé paresseusement (`app/services/quotas.py`) autant que par la migration : un compte
peut naître de la CLI ou du back-office, et un chemin de création qui oublierait le catalogue
rendrait toute résolution de droits impossible sans le dire.

**Une fonctionnalité n'entre dans cette grille que si elle est construite et appliquée** (spec §10,
amendement A14). La grille a longtemps porté six clés — marque blanche, signature « bon pour
accord », variantes chiffrées, SSO, statistiques par agence, API — qui n'avaient aucune
implémentation nulle part : la page tarifs, qui est publique, affichait donc « ✓ » en face d'un
prix pour des fonctions qui n'existaient pas. Elles ont été retirées. Le jour où l'une d'elles est
construite, on ajoute la clé **et** le point de passage qui l'applique dans le même lot ; les deux
sont indissociables, et `tests/test_offres.py` refuse désormais l'un sans l'autre.
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.billing_plan import TRIAL_DAYS, PlanCatalog, UsageMetric

# --- Codes de palier ----------------------------------------------------------------------------
# Le code est technique et figé ; `name` est le libellé commercial et peut changer sans casser un
# abonnement en cours.
PLAN_DISCOVERY = "decouverte"
PLAN_ARTISAN = "artisan"
PLAN_BUSINESS = "entreprise"
PLAN_NETWORK = "reseau"

# Palier servi à toute organisation sans abonnement vivant.
FREE_PLAN_CODE = PLAN_DISCOVERY
# Palier offert pendant l'essai sans carte. Sa **durée** est la colonne `trial_days` de ce
# palier-là, en base : c'est le seul endroit où elle se règle, et une organisation en essai n'est
# rien d'autre qu'un abonnement `trialing` sur ce code (spec §10, amendement A14).
TRIAL_PLAN_CODE = PLAN_ARTISAN

# --- Clés de limite -----------------------------------------------------------------------------
# `None` veut dire **illimité**, jamais zéro. Les confondre transformerait un palier sans plafond
# en palier qui refuse tout.
LIMIT_ACTIVE_PROJECTS = "active_projects"
LIMIT_ROOMS_PER_PROJECT = "rooms_per_project"
LIMIT_SEATS = "seats"
LIMIT_SHARE_LINK_DAYS = "share_link_days"
LIMIT_EXPORTS_PDF = "exports_pdf"
LIMIT_QUOTES_ISSUED = "quotes_issued"
LIMIT_AI_RUNS = "ai_runs"
LIMIT_API_CALLS = "api_calls"

# --- Clés de fonctionnalité ---------------------------------------------------------------------
# Chacune est refusée quelque part, et `ENFORCEMENT_POINTS` dit où. Six clés ont été retirées ici
# (marque blanche, signature « bon pour accord », variantes chiffrées, SSO, statistiques par
# agence, API) : elles n'avaient d'implémentation nulle part et la page tarifs, publique, les
# affichait « ✓ » en face d'un prix. Voir spec §10, amendement A14.
FEATURE_QUOTES = "quotes"
FEATURE_EXPORTS_WITHOUT_WATERMARK = "exports_without_watermark"
FEATURE_DIMENSIONED_ELEVATIONS = "dimensioned_elevations"
FEATURE_TILING_WASTE = "tiling_waste"
FEATURE_COMPLIANCE_CHECK = "compliance_check"
FEATURE_MULTI_SEAT = "multi_seat"
FEATURE_SHARED_PRICE_BOOK = "shared_price_book"
FEATURE_AUTO_LAYOUT = "auto_layout"

# Libellés servis avec le catalogue. Ils vivent ici et non côté navigateur : une clé ajoutée en
# base sans libellé s'affiche telle quelle plutôt que de disparaître de la page tarifs.
FEATURE_LABELS: dict[str, str] = {
    FEATURE_QUOTES: "Devis chiffré et facture Factur-X",
    FEATURE_EXPORTS_WITHOUT_WATERMARK: "Exports PDF sans filigrane",
    FEATURE_DIMENSIONED_ELEVATIONS: "Élévations cotées par mur",
    FEATURE_TILING_WASTE: "Calepinage et taux de chute",
    FEATURE_COMPLIANCE_CHECK: "Contrôle de conformité du plan",
    FEATURE_MULTI_SEAT: "Plusieurs utilisateurs, rôles et invitations",
    FEATURE_SHARED_PRICE_BOOK: "Barème de prix partagé",
    FEATURE_AUTO_LAYOUT: "Aménagement automatique",
}

# Où chaque fonctionnalité est **réellement** refusée. Cette table n'a aucun effet à l'exécution :
# elle existe pour qu'un test confronte la grille au code, et pour qu'un relecteur trouve la garde
# en une recherche. `shared_price_book` est la seule à partager la garde d'une autre, et c'est
# justifié : le barème appartient à l'organisation, il est donc partagé par construction dès qu'un
# second siège existe — la garde est celle de `multi_seat`.
ENFORCEMENT_POINTS: dict[str, str] = {
    FEATURE_QUOTES: "app/api/quotes.py::create_quote",
    FEATURE_EXPORTS_WITHOUT_WATERMARK: "app/services/quotas.py::register_pdf_export",
    FEATURE_DIMENSIONED_ELEVATIONS: "app/services/quotas.py::register_pdf_export",
    FEATURE_TILING_WASTE: "app/api/intelligence.py::read_laying_plan",
    FEATURE_COMPLIANCE_CHECK: "app/api/intelligence.py::read_inspection",
    FEATURE_MULTI_SEAT: "app/api/organizations.py::invite_member",
    FEATURE_SHARED_PRICE_BOOK: "app/api/organizations.py::invite_member",
    FEATURE_AUTO_LAYOUT: "app/api/intelligence.py::propose_room_layouts",
}

# Libellés des métriques d'usage. Distincts de `LIMIT_LABELS` : une métrique et la limite qui la
# plafonne ne portent pas le même nom (`projects_active` est compté, `active_projects` est
# plafonné), et les confondre afficherait la mauvaise ligne sur la page compte.
METRIC_LABELS: dict[str, str] = {
    UsageMetric.PROJECTS_ACTIVE: "Chantiers actifs",
    UsageMetric.EXPORTS_PDF: "Exports PDF",
    UsageMetric.QUOTES_ISSUED: "Devis émis",
    UsageMetric.SHARED_VIEW_HITS: "Ouvertures des liens client",
    UsageMetric.AI_RUNS: "Analyses automatiques du plan",
    UsageMetric.API_CALLS: "Appels d'API",
    UsageMetric.ACTIVATION: "Activation (première pièce dessinée)",
    UsageMetric.TIME_TO_FIRST_QUOTE: "Délai jusqu'au premier devis",
    UsageMetric.DROP_OFF: "Points d'abandon",
}

# Limites **publiées** sur la page tarifs. Une limite ne s'affiche que si une garde l'applique :
# un plafond annoncé que rien ne fait respecter est une promesse sans contrepartie, exactement au
# même titre qu'une fonctionnalité inexistante (spec §10, amendement A14). `exports_pdf`,
# `quotes_issued`, `ai_runs` et `api_calls` restent dans `plan_catalog.limits` — c'est là que le
# plafond s'écrira le jour où la garde existera — mais ne sont plus affichés : ils valent `None`
# sur les quatre paliers, si bien que la page répétait « Illimité » quatre fois sans que rien ne
# puisse jamais le démentir.
LIMIT_LABELS: dict[str, str] = {
    LIMIT_ACTIVE_PROJECTS: "Chantiers actifs",
    LIMIT_ROOMS_PER_PROJECT: "Pièces par chantier",
    LIMIT_SEATS: "Sièges",
    LIMIT_SHARE_LINK_DAYS: "Durée des liens de partage (jours)",
}

# Où chaque limite publiée est refusée. Même rôle que `ENFORCEMENT_POINTS` : sans effet à
# l'exécution, confrontée à `LIMIT_LABELS` par un test pour qu'aucun plafond ne puisse être affiché
# sans être appliqué.
LIMIT_ENFORCEMENT_POINTS: dict[str, str] = {
    LIMIT_ACTIVE_PROJECTS: "app/api/plan.py::create_project",
    LIMIT_ROOMS_PER_PROJECT: "app/api/plan.py::create_room + apply_batch",
    LIMIT_SEATS: "app/api/organizations.py::invite_member",
    LIMIT_SHARE_LINK_DAYS: "app/api/share.py::create_shared_view",
}


# Métrique d'usage → clé de limite qui la plafonne. Les métriques absentes de cette table sont
# comptées sans jamais rien bloquer : `shared_view_hits` et les trois métriques produit servent à
# comprendre l'usage, pas à le restreindre.
METRIC_LIMITS: dict[str, str] = {
    UsageMetric.PROJECTS_ACTIVE: LIMIT_ACTIVE_PROJECTS,
    UsageMetric.EXPORTS_PDF: LIMIT_EXPORTS_PDF,
    UsageMetric.QUOTES_ISSUED: LIMIT_QUOTES_ISSUED,
    UsageMetric.AI_RUNS: LIMIT_AI_RUNS,
    UsageMetric.API_CALLS: LIMIT_API_CALLS,
}


def _features(*granted: str) -> dict[str, Any]:
    """Dictionnaire complet des fonctionnalités, celles non citées étant explicitement fausses.

    Explicite plutôt que par absence : une clé manquante et une clé à `false` se lisent pareil
    dans le code, mais pas sur la page tarifs — la colonne « ce qui est bloqué » a besoin de savoir
    que la fonctionnalité existe ailleurs.
    """
    return {key: key in granted for key in FEATURE_LABELS}


# La grille de `docs/strategie-produit.md` §4, telle quelle. Prix en centimes entiers, hors taxes
# et par mois ; `yearly_price_cents` est le prix mensuel équivalent en engagement annuel (deux mois
# offerts), et non le montant annuel.
PLAN_GRID: tuple[dict[str, Any], ...] = (
    {
        "code": PLAN_DISCOVERY,
        "name": "Découverte",
        "trial_days": 0,
        "tagline": "Essayer, et faire circuler des liens",
        "monthly_price_cents": 0,
        "yearly_price_cents": 0,
        "seat_price_cents": 0,
        "sort_order": 10,
        "limits": {
            LIMIT_ACTIVE_PROJECTS: 1,
            LIMIT_ROOMS_PER_PROJECT: 2,
            LIMIT_SEATS: 1,
            LIMIT_SHARE_LINK_DAYS: 30,
            LIMIT_EXPORTS_PDF: None,
            LIMIT_QUOTES_ISSUED: None,
            LIMIT_AI_RUNS: None,
            LIMIT_API_CALLS: None,
        },
        # La 3D complète et le catalogue entier sont inclus dès le palier gratuit : c'est un
        # argument de vente (`docs/strategie-produit.md` §1), pas un oubli.
        "features": _features(),
    },
    {
        "code": PLAN_ARTISAN,
        "name": "Artisan",
        "trial_days": TRIAL_DAYS,
        "tagline": "Le solo, cœur de cible",
        "monthly_price_cents": 2_900,
        "yearly_price_cents": 2_400,
        "seat_price_cents": 0,
        "sort_order": 20,
        "limits": {
            LIMIT_ACTIVE_PROJECTS: None,
            LIMIT_ROOMS_PER_PROJECT: None,
            LIMIT_SEATS: 1,
            LIMIT_SHARE_LINK_DAYS: 90,
            LIMIT_EXPORTS_PDF: None,
            LIMIT_QUOTES_ISSUED: None,
            LIMIT_AI_RUNS: None,
            LIMIT_API_CALLS: None,
        },
        "features": _features(
            FEATURE_QUOTES,
            FEATURE_EXPORTS_WITHOUT_WATERMARK,
            FEATURE_DIMENSIONED_ELEVATIONS,
            FEATURE_TILING_WASTE,
            FEATURE_COMPLIANCE_CHECK,
        ),
    },
    {
        "code": PLAN_BUSINESS,
        "name": "Entreprise",
        "trial_days": 0,
        "tagline": "2 à 15 personnes",
        "monthly_price_cents": 7_900,
        "yearly_price_cents": 6_500,
        "seat_price_cents": 1_900,
        "sort_order": 30,
        "limits": {
            LIMIT_ACTIVE_PROJECTS: None,
            LIMIT_ROOMS_PER_PROJECT: None,
            LIMIT_SEATS: 15,
            LIMIT_SHARE_LINK_DAYS: 90,
            LIMIT_EXPORTS_PDF: None,
            LIMIT_QUOTES_ISSUED: None,
            LIMIT_AI_RUNS: None,
            LIMIT_API_CALLS: None,
        },
        "features": _features(
            FEATURE_QUOTES,
            FEATURE_EXPORTS_WITHOUT_WATERMARK,
            FEATURE_DIMENSIONED_ELEVATIONS,
            FEATURE_TILING_WASTE,
            FEATURE_COMPLIANCE_CHECK,
            FEATURE_MULTI_SEAT,
            FEATURE_SHARED_PRICE_BOOK,
            FEATURE_AUTO_LAYOUT,
        ),
    },
    {
        "code": PLAN_NETWORK,
        "name": "Réseau",
        "trial_days": 0,
        # Ce qui distingue réellement ce palier aujourd'hui est le nombre de sièges : sous-domaine,
        # SSO, catalogue imposé et statistiques par agence ne sont pas construits, et le tagline ne
        # les annonce donc pas (spec §10, amendement A14).
        "tagline": "Franchises et réseaux : sièges illimités, accompagnement sur devis",
        "monthly_price_cents": 39_000,
        # « Sur devis » : le tarif annuel se négocie, et en inventer un afficherait un prix que
        # personne n'a accepté.
        "yearly_price_cents": None,
        "seat_price_cents": 0,
        "sort_order": 40,
        "limits": {
            LIMIT_ACTIVE_PROJECTS: None,
            LIMIT_ROOMS_PER_PROJECT: None,
            LIMIT_SEATS: None,
            LIMIT_SHARE_LINK_DAYS: 90,
            LIMIT_EXPORTS_PDF: None,
            LIMIT_QUOTES_ISSUED: None,
            LIMIT_AI_RUNS: None,
            LIMIT_API_CALLS: None,
        },
        "features": _features(*FEATURE_LABELS),
    },
)

# Ordre des paliers, du moins-disant au plus-disant. Il sert à nommer le palier **requis** dans le
# 402 : « cette fonctionnalité demande Artisan » est actionnable, « vous n'y avez pas droit » ne
# l'est pas.
PLAN_ORDER: tuple[str, ...] = tuple(entry["code"] for entry in PLAN_GRID)


async def ensure_plans_seeded(session: AsyncSession) -> list[PlanCatalog]:
    """Charge le catalogue, en le semant s'il est vide. Idempotent.

    Ne complète volontairement que ce qui manque : un palier déjà en base garde ses limites, y
    compris celles qu'un commercial vient d'ajuster par `UPDATE`.
    """
    existing = list((await session.execute(select(PlanCatalog))).scalars().all())
    known = {plan.code for plan in existing}

    missing = [entry for entry in PLAN_GRID if entry["code"] not in known]
    if not missing:
        return existing

    for entry in missing:
        plan = PlanCatalog(**entry)
        session.add(plan)
        existing.append(plan)
    await session.flush()
    return existing


def trial_days_of(plans: dict[str, PlanCatalog]) -> int:
    """Durée de l'essai sans carte, lue sur le palier d'essai du catalogue.

    Elle a longtemps été une constante Python, alors que c'est le levier commercial le plus
    souvent tiré du produit : allonger l'essai à 30 jours pour une campagne est exactement le genre
    de décision qui ne doit pas passer par un déploiement (spec §10, amendement A14).

    Zéro — ou un palier d'essai absent du catalogue — veut dire « aucun essai offert », et c'est un
    réglage licite : `start_trial` n'ouvre alors rien du tout.
    """
    plan = plans.get(TRIAL_PLAN_CODE)
    return max(int(plan.trial_days), 0) if plan is not None else 0


def required_plan_for_feature(feature: str, plans: dict[str, PlanCatalog]) -> str | None:
    """Palier le moins cher qui accorde `feature`, ou `None` si aucun ne l'accorde.

    Calculé depuis la base et non depuis une table de correspondance : déplacer une fonctionnalité
    d'un palier à l'autre est une ligne SQL, et le message du 402 doit suivre sans redéploiement.
    """
    candidates = [plan for plan in plans.values() if bool(plan.features.get(feature))]
    if not candidates:
        return None
    return min(candidates, key=_commercial_rank).code


def required_plan_for_limit(limit: str, needed: int, plans: dict[str, PlanCatalog]) -> str | None:
    """Palier le moins cher dont la limite `limit` couvre `needed` (`None` = illimité)."""
    candidates = [
        plan
        for plan in plans.values()
        if limit in plan.limits
        and (plan.limits[limit] is None or int(plan.limits[limit]) >= needed)
    ]
    if not candidates:
        return None
    return min(candidates, key=_commercial_rank).code


def _commercial_rank(plan: PlanCatalog) -> tuple[int, int, str]:
    """Ordre « du moins cher au plus cher », déterministe même pour un palier hors grille.

    Le prix mensuel d'abord — c'est ce qui définit « le moins cher » ; `sort_order` départage deux
    paliers au même prix ; le code termine, pour qu'un palier ajouté en base sans `sort_order` ne
    rende pas le message dépendant de l'ordre de lecture de la table.
    """
    return (plan.monthly_price_cents, plan.sort_order, plan.code)
