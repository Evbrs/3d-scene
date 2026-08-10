"""Droits, quotas et compteurs d'usage (`docs/strategie-produit.md` §4).

Ce module répond à une seule question, posée avant chaque geste monétisé : *de quoi cette
organisation a-t-elle le droit, maintenant ?* Il ne connaît aucun prix codé en dur — tout vient de
`plan_catalog`, donc de la base.

Quatre décisions structurent le fichier, et chacune corrige une erreur classique.

**Le compteur s'incrémente en une seule instruction.** `INSERT … ON CONFLICT DO UPDATE SET
value = value + :n RETURNING value` : un `SELECT` suivi d'un `UPDATE` laisse deux onglets ouverts
lire la même valeur et passer tous les deux au-dessus de la limite. Le test de concurrence de
`tests/test_quotas.py` le prouve en jouant les deux chemins l'un contre l'autre.

**La période de comptage est celle de la facturation.** Un abonnement souscrit le 20 se remet à
zéro le 20, pas le 1er — sinon le mois calendaire lui offre une remise à zéro gratuite. L'ancre
est le début de la première période d'abonnement, ou à défaut la date de création de
l'organisation : un compte gratuit a lui aussi une date anniversaire.

**Un événement rejoué ne compte pas deux fois.** `usage_event.idempotency_key` est unique ; pour
un export, c'est l'identifiant de la tâche Celery. Après un incident du courtier, la même tâche
revient : sans cette clé, le client est facturé deux fois pour un fichier qu'il n'a demandé qu'une
fois.

**Une limite inconnue vaut « illimité ».** C'est le sens de défaillance choisi : le pire incident
imaginable ici est un quota qui bloque un client payant en pleine journée de chantier, pas un
client qui exporte un PDF de trop. Une clé absente de `plan_catalog.limits` n'arrête donc rien.
"""

from calendar import monthrange
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Table, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import SQLModel, col, select

from app.models.base import utcnow
from app.models.billing_plan import (
    PlanCatalog,
    Subscription,
    SubscriptionStatus,
    UsageCounter,
    UsageMetric,
)
from app.models.organization import Membership, Organization
from app.models.plan import Project, Room
from app.services.seed_plans import (
    FEATURE_DIMENSIONED_ELEVATIONS,
    FEATURE_EXPORTS_WITHOUT_WATERMARK,
    FREE_PLAN_CODE,
    LIMIT_ACTIVE_PROJECTS,
    TRIAL_PLAN_CODE,
    ensure_plans_seeded,
    trial_days_of,
)

# Statuts qui ouvrent encore les droits du palier. `past_due` en fait partie : couper l'accès d'un
# client dont le prélèvement a échoué le matin même coûte plus cher que la relance. La coupure est
# un passage explicite en `canceled`.
LIVE_STATUSES = frozenset(
    {SubscriptionStatus.TRIALING, SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE}
)


@dataclass(frozen=True)
class Entitlement:
    """Ce à quoi une organisation a droit à un instant donné, et pourquoi.

    Immuable : deux appels dans la même requête doivent donner la même réponse, sans quoi une
    route pourrait accorder puis refuser le même geste.
    """

    organization_id: int
    plan: PlanCatalog
    plans: dict[str, PlanCatalog]
    subscription: Subscription | None
    period_start: datetime
    period_end: datetime
    # Vrai tant qu'aucun abonnement n'a jamais existé : l'essai ne se consomme qu'une fois.
    trial_available: bool
    trial_ends_at: datetime | None

    def has(self, feature: str) -> bool:
        return bool(self.plan.features.get(feature))

    def limit(self, name: str) -> int | None:
        """Plafond de `name`, ou `None` pour « illimité ».

        Une clé absente rend `None` : voir l'en-tête du module, le sens de défaillance est
        volontairement permissif.
        """
        raw = self.plan.limits.get(name)
        return None if raw is None else int(raw)

    @property
    def is_trialing(self) -> bool:
        return (
            self.subscription is not None
            and self.subscription.status is SubscriptionStatus.TRIALING
        )


# --- Période de facturation ---------------------------------------------------------------------


def add_months(moment: datetime, months: int) -> datetime:
    """Décale de `months` mois en gardant le jour, rabattu sur le dernier jour du mois cible.

    Un abonnement souscrit un 31 janvier se renouvelle le 28 février puis le 31 mars : le jour
    d'ancrage n'est pas perdu au passage d'un mois court, il est seulement rabattu. Le perdre
    ferait glisser la date anniversaire vers le 28 pour toujours.
    """
    total = moment.month - 1 + months
    year = moment.year + total // 12
    month = total % 12 + 1
    day = min(moment.day, monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def billing_period_start(anchor: datetime, now: datetime) -> datetime:
    """Début de la période de facturation en cours, à partir de la date d'ancrage.

    C'est un multiple de mois **depuis l'ancre**, jamais un 1er du mois. Une ancre dans le futur
    (horloge décalée, abonnement pris d'avance) rend l'ancre elle-même plutôt qu'une période
    négative.
    """
    anchor = _as_utc(anchor)
    now = _as_utc(now)
    if now <= anchor:
        return anchor

    months = (now.year - anchor.year) * 12 + (now.month - anchor.month)
    candidate = add_months(anchor, months)
    if candidate > now:
        candidate = add_months(anchor, months - 1)
    return candidate


def _as_utc(moment: datetime) -> datetime:
    """Rend un datetime *aware* en UTC.

    SQLite relit les colonnes `TIMESTAMP` sans fuseau : comparer une telle valeur à `utcnow()`
    lèverait « can't compare offset-naive and offset-aware datetimes », et la suite tourne dessus.
    """
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


# --- Résolution des droits ----------------------------------------------------------------------


async def load_plans(session: AsyncSession) -> dict[str, PlanCatalog]:
    """Catalogue complet, indexé par code, semé s'il est vide."""
    plans = await ensure_plans_seeded(session)
    return {plan.code: plan for plan in plans}


async def resolve_entitlement(
    session: AsyncSession, organization_id: int, *, now: datetime | None = None
) -> Entitlement:
    """Droits effectifs de l'organisation.

    Tous les abonnements de l'organisation sont lus en **une** requête : il en existe zéro ou un
    dans l'immense majorité des cas, et les deux informations dont on a besoin — l'abonnement
    vivant et la date d'ancrage de facturation — se déduisent de la même liste. Deux requêtes
    séparées auraient coûté un aller-retour de plus sur le chemin de chaque création de projet.
    """
    moment = _as_utc(now or utcnow())
    plans = await load_plans(session)

    history = list(
        (
            await session.execute(
                select(Subscription)
                .where(col(Subscription.organization_id) == organization_id)
                .order_by(col(Subscription.id))
            )
        )
        .scalars()
        .all()
    )

    live = _live_subscription(history, moment)
    plan = plans.get(live.plan_code) if live is not None else None
    if plan is None:
        # Palier disparu du catalogue, ou aucun abonnement : on retombe sur le gratuit plutôt que
        # de lever. Un catalogue incomplet ne doit pas rendre le produit inutilisable.
        plan = plans[FREE_PLAN_CODE]

    anchor = history[0].current_period_start if history else await _organization_birth(
        session, organization_id
    )
    period_start = billing_period_start(anchor, moment)

    return Entitlement(
        organization_id=organization_id,
        plan=plan,
        plans=plans,
        subscription=live,
        period_start=period_start,
        period_end=add_months(period_start, 1),
        trial_available=not history,
        trial_ends_at=history[0].trial_ends_at if history else None,
    )


def _live_subscription(
    history: list[Subscription], now: datetime
) -> Subscription | None:
    """Abonnement qui ouvre encore des droits, le plus récent d'abord.

    Un essai dont la date est passée n'ouvre plus rien, et son statut reste `trialing` en base :
    le faire basculer demanderait d'écrire pendant une lecture, or les droits se consultent sur
    des chemins qui n'ont aucune raison d'écrire.
    """
    for subscription in reversed(history):
        if subscription.status not in LIVE_STATUSES:
            continue
        if subscription.cancel_at is not None and _as_utc(subscription.cancel_at) <= now:
            continue
        if subscription.status is SubscriptionStatus.TRIALING and (
            subscription.trial_ends_at is None or _as_utc(subscription.trial_ends_at) <= now
        ):
            continue
        return subscription
    return None


async def _organization_birth(session: AsyncSession, organization_id: int) -> datetime:
    """Date d'ancrage d'une organisation sans abonnement : sa création.

    Un compte gratuit a lui aussi une date anniversaire ; le faire compter du 1er du mois lui
    offrirait une remise à zéro que personne n'a décidée.
    """
    created = (
        await session.execute(
            select(col(Organization.created_at)).where(col(Organization.id) == organization_id)
        )
    ).scalar_one_or_none()
    return _as_utc(created) if created is not None else utcnow()


async def start_trial(
    session: AsyncSession, organization_id: int, *, now: datetime | None = None
) -> Subscription | None:
    """Ouvre l'essai du palier Artisan, sans carte. `None` s'il a déjà été consommé.

    Déclenché au **premier geste monétisé**, jamais à l'inscription : un essai qui démarre à
    l'inscription est consommé par quelqu'un qui n'a pas encore compris le produit
    (`docs/strategie-produit.md` §4).

    La **durée** est lue sur le palier d'essai du catalogue et non sur une constante Python
    (amendement A14) : allonger l'essai à 30 jours le temps d'une campagne est le levier commercial
    le plus souvent tiré, et il n'avait aucune raison de coûter un déploiement. Une durée nulle veut
    dire « aucun essai offert » : la fonction n'ouvre alors rien, et le geste est refusé
    normalement — c'est un réglage licite, pas une panne.

    L'unicité est tenue ici et non par un index : une organisation qui a déjà une ligne
    d'abonnement — même résiliée — n'y a plus droit.
    """
    moment = _as_utc(now or utcnow())
    plans = await load_plans(session)
    days = trial_days_of(plans)
    if days <= 0:
        return None

    already = (
        await session.execute(
            select(func.count())
            .select_from(Subscription)
            .where(col(Subscription.organization_id) == organization_id)
        )
    ).scalar_one()
    if already:
        return None

    ends_at = moment + timedelta(days=days)
    subscription = Subscription(
        organization_id=organization_id,
        plan_code=TRIAL_PLAN_CODE,
        status=SubscriptionStatus.TRIALING,
        current_period_start=moment,
        current_period_end=ends_at,
        trial_ends_at=ends_at,
        seats=1,
    )
    session.add(subscription)
    await session.flush()
    return subscription


# --- Compteurs et journal -----------------------------------------------------------------------


def _table(name: str) -> Table:
    """Table SQLAlchemy sous-jacente d'un modèle, prise dans les métadonnées.

    `Model.__table__` ferait la même chose à l'exécution, mais SQLModel ne l'expose pas au
    vérificateur de types : passer par les métadonnées évite un `type: ignore` sur chaque emploi.
    """
    return SQLModel.metadata.tables[name]


def _upsert(session: AsyncSession) -> Any:
    """`insert()` du dialecte courant, seul à porter `on_conflict_do_update`.

    Le dépôt tourne sur PostgreSQL en production et sur SQLite en test ; les deux savent faire
    l'upsert, mais chacun par son propre constructeur. Un `insert()` générique ne l'expose pas.
    """
    name = session.get_bind().dialect.name
    if name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    elif name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as dialect_insert  # type: ignore[assignment]
    else:  # pragma: no cover — aucun autre moteur n'est supporté par le dépôt
        raise RuntimeError(f"upsert non supporté sur le dialecte {name}")
    return dialect_insert


async def increment_counter(
    session: AsyncSession,
    *,
    organization_id: int,
    metric: str,
    period_start: datetime,
    quantity: int = 1,
) -> int:
    """Incrémente un compteur et rend sa **nouvelle** valeur, en une seule instruction SQL.

    C'est le cœur du lot. Deux onglets ouverts sur le même compte exécutent cette instruction en
    parallèle : la base sérialise les deux `UPDATE` sur la ligne, et chacun voit la valeur laissée
    par l'autre. Un `SELECT` suivi d'un `UPDATE` lirait deux fois la même valeur et écrirait deux
    fois la même — le second geste passerait au-dessus de la limite sans que rien ne le signale.
    """
    table = _table("usage_counter")
    insert = _upsert(session)

    statement = (
        insert(table)
        .values(
            organization_id=organization_id,
            metric=str(metric),
            period_start=period_start,
            value=quantity,
        )
        .on_conflict_do_update(
            index_elements=[
                table.c.organization_id,
                table.c.metric,
                table.c.period_start,
            ],
            set_={"value": table.c.value + quantity, "updated_at": utcnow()},
        )
        .returning(table.c.value)
    )
    return int((await session.execute(statement)).scalar_one())


async def counter_value(
    session: AsyncSession, *, organization_id: int, metric: str, period_start: datetime
) -> int:
    """Valeur courante d'un compteur (0 s'il n'a jamais été incrémenté)."""
    found = (
        await session.execute(
            select(col(UsageCounter.value)).where(
                col(UsageCounter.organization_id) == organization_id,
                col(UsageCounter.metric) == str(metric),
                col(UsageCounter.period_start) == period_start,
            )
        )
    ).scalar_one_or_none()
    return int(found or 0)


async def record_usage(
    session: AsyncSession,
    *,
    organization_id: int,
    metric: str,
    idempotency_key: str,
    period_start: datetime,
    user_id: int | None = None,
    quantity: int = 1,
    metadata: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
) -> int | None:
    """Écrit un événement d'usage et incrémente le compteur associé.

    Rend la nouvelle valeur du compteur, ou `None` si l'événement était un **rejeu** — auquel cas
    rien n'a été compté. C'est la garantie qui empêche la surfacturation : une tâche Celery
    ressuscitée après un incident du courtier porte la même clé d'idempotence, l'`INSERT` est
    ignoré, et le compteur ne bouge pas.

    L'ordre compte : l'événement est écrit **avant** le compteur. L'inverse compterait un geste
    dont on ne saurait plus rien si l'écriture du journal échouait.
    """
    events = _table("usage_event")
    insert = _upsert(session)
    moment = occurred_at or utcnow()

    inserted = (
        await session.execute(
            insert(events)
            .values(
                organization_id=organization_id,
                user_id=user_id,
                metric=str(metric),
                quantity=quantity,
                idempotency_key=idempotency_key,
                metadata=metadata or {},
                occurred_at=moment,
            )
            .on_conflict_do_nothing(index_elements=[events.c.idempotency_key])
            .returning(events.c.id)
        )
    ).scalar_one_or_none()

    if inserted is None:
        return None

    return await increment_counter(
        session,
        organization_id=organization_id,
        metric=metric,
        period_start=period_start,
        quantity=quantity,
    )


# --- Métriques produit ---------------------------------------------------------------------------
#
# Elles ne plafonnent rien et ne facturent rien. Elles sont écrites dès maintenant parce que leur
# historique ne se reconstitue pas : le jour où il faudra arbitrer une grille tarifaire ou corriger
# l'accueil, la question sera « quel pourcentage des comptes de mars ont dessiné une pièce », et
# personne ne pourra y répondre après coup.
#
# Chacune est posée **une seule fois par organisation** : la clé d'idempotence porte l'identifiant
# du locataire, pas celui du geste.


async def record_activation(
    session: AsyncSession, organization_id: int, *, user_id: int | None = None
) -> None:
    """Première pièce dessinée : le geste qui prouve que le produit a été compris.

    Ce n'est ni l'inscription ni la création d'un chantier vide — deux gestes qu'on fait avant
    d'avoir rien vu du produit. C'est la première pièce, parce que c'est à partir d'elle qu'il y a
    une géométrie, une 3D et un métré.
    """
    entitlement = await resolve_entitlement(session, organization_id)
    await record_usage(
        session,
        organization_id=organization_id,
        metric=UsageMetric.ACTIVATION,
        idempotency_key=f"{UsageMetric.ACTIVATION}:organization:{organization_id}",
        period_start=entitlement.period_start,
        user_id=user_id,
    )


async def record_first_quote_delay(
    session: AsyncSession, entitlement: Entitlement, *, user_id: int | None = None
) -> None:
    """Délai, **en secondes**, entre la création de l'entreprise et son premier devis.

    Stocké dans la quantité de l'événement plutôt que dans son contexte : le compteur porte alors
    directement la valeur, et un événement unique par organisation suffit à la lire. C'est le
    seul cas du produit où la quantité n'est pas un nombre de gestes, d'où ce commentaire.
    """
    birth = await _organization_birth(session, entitlement.organization_id)
    seconds = max(int((utcnow() - birth).total_seconds()), 0)
    await record_usage(
        session,
        organization_id=entitlement.organization_id,
        metric=UsageMetric.TIME_TO_FIRST_QUOTE,
        idempotency_key=(
            f"{UsageMetric.TIME_TO_FIRST_QUOTE}:organization:{entitlement.organization_id}"
        ),
        period_start=entitlement.period_start,
        user_id=user_id,
        quantity=seconds,
    )


async def usage_snapshot(session: AsyncSession, entitlement: Entitlement) -> dict[str, int]:
    """Consommation de la période en cours, métrique par métrique.

    `projects_active` est un **état** et non un cumul : il se compte sur la table des projets, pas
    dans un compteur. Cumuler des créations dirait combien de chantiers ont été ouverts depuis le
    début du mois, ce qui n'est pas la limite annoncée.
    """
    rows = (
        await session.execute(
            select(col(UsageCounter.metric), col(UsageCounter.value)).where(
                col(UsageCounter.organization_id) == entitlement.organization_id,
                col(UsageCounter.period_start) == entitlement.period_start,
            )
        )
    ).all()

    snapshot: dict[str, int] = {str(metric): 0 for metric in UsageMetric}
    snapshot.update({str(metric): int(value) for metric, value in rows})
    snapshot[UsageMetric.PROJECTS_ACTIVE] = await active_project_count(
        session, entitlement.organization_id
    )
    return snapshot


# --- Projets actifs et déclassement --------------------------------------------------------------


async def room_count(session: AsyncSession, project_id: int) -> int:
    """Pièces d'un chantier — l'état que plafonne `rooms_per_project`.

    Un **état** et non un cumul, comme `projects_active` : le plafond annoncé sur la page tarifs est
    « 2 pièces par chantier », pas « 2 pièces créées par mois ». Compter des créations laisserait
    une pièce supprimée puis redessinée consommer deux fois le quota.
    """
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(Room)
                .where(col(Room.project_id) == project_id)
            )
        ).scalar_one()
    )


async def seat_count(session: AsyncSession, organization_id: int) -> int:
    """Sièges occupés : les appartenances **acceptées**.

    Les invitations en attente ne comptent pas. Une ligne sans `accepted_at` n'ouvre aucun accès
    (`app/models/organization.py`), et faire payer un siège pour quelqu'un qui n'a pas répondu
    serait faux dans les deux sens : l'entreprise le paierait sans l'avoir, et une invitation
    oubliée bloquerait l'embauche suivante.
    """
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(Membership)
                .where(
                    col(Membership.organization_id) == organization_id,
                    col(Membership.accepted_at).is_not(None),
                )
            )
        ).scalar_one()
    )


async def active_project_count(session: AsyncSession, organization_id: int) -> int:
    """Chantiers non archivés de l'organisation."""
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(Project)
                .where(
                    col(Project.organization_id) == organization_id,
                    col(Project.archived_at).is_(None),
                )
            )
        ).scalar_one()
    )


async def enforce_active_project_limit(
    session: AsyncSession, entitlement: Entitlement, *, now: datetime | None = None
) -> list[int]:
    """Déclasse les chantiers excédentaires en lecture seule. Rend les identifiants touchés.

    **Rien n'est jamais supprimé.** Un projet excédentaire reçoit `archived_at` : il reste lisible,
    exportable et consultable, il n'est plus modifiable. C'est la situation la plus favorable au
    réabonnement, et la seule qui ne détruise pas la confiance
    (`docs/strategie-produit.md` §4).

    Les chantiers conservés sont les plus récemment modifiés : c'est celui sur lequel l'artisan
    travaille aujourd'hui qu'il faut garder ouvert, pas le plus ancien.

    L'écriture passe par un `UPDATE` ensembliste et non par l'ORM : charger les projets pour les
    modifier un par un incrémenterait leur `version` et périmerait le cache de scène de chacun,
    alors que leur géométrie n'a pas bougé d'un centimètre.
    """
    limit = entitlement.limit(LIMIT_ACTIVE_PROJECTS)
    if limit is None:
        return []

    active = [
        identifier
        for identifier in (
            await session.execute(
                select(col(Project.id))
                .where(
                    col(Project.organization_id) == entitlement.organization_id,
                    col(Project.archived_at).is_(None),
                )
                .order_by(col(Project.updated_at).desc(), col(Project.id).desc())
            )
        )
        .scalars()
        .all()
        if identifier is not None
    ]
    excess = active[max(limit, 0) :]
    if not excess:
        return []

    await session.execute(
        update(Project).where(col(Project.id).in_(excess)).values(archived_at=now or utcnow())
    )
    return excess


# --- Analyses automatiques du plan ----------------------------------------------------------------


async def register_ai_run(
    session: AsyncSession,
    *,
    organization_id: int,
    kind: str,
    subject_id: int,
    version: int,
    variant: str = "",
    user_id: int | None = None,
) -> None:
    """Compte une analyse du plan — contrôle de conformité, calepinage, aménagement.

    La clé d'idempotence porte la **version du plan** et les options : les trois moteurs sont
    déterministes, deux appels sur la même version rendent le même octet et ne sont donc qu'une
    seule analyse. Compter les clics à la place gonflerait la métrique à chaque rafraîchissement
    de panneau et rendrait « analyses par période » incomparable d'un compte à l'autre.

    Aucune garde n'en dépend aujourd'hui : `ai_runs` est illimité sur les quatre paliers. Le
    comptage existe parce que la page compte affiche la ligne, et qu'un compteur qui affiche
    toujours zéro est un compteur qui ment.
    """
    entitlement = await resolve_entitlement(session, organization_id)
    await record_usage(
        session,
        organization_id=organization_id,
        metric=UsageMetric.AI_RUNS,
        idempotency_key=f"{UsageMetric.AI_RUNS}:{kind}:{subject_id}:{version}:{variant}",
        period_start=entitlement.period_start,
        user_id=user_id,
        metadata={"kind": kind, "subject_id": subject_id, "version": version},
    )


# --- Exports PDF : filigrane, planches et comptage ------------------------------------------------


@dataclass(frozen=True)
class ExportGrants:
    """Ce que le palier accorde sur **ce** dossier d'export, décidé par le serveur seul.

    Deux décisions et non une, depuis l'amendement A14. `docs/strategie-produit.md` §4 place
    l'« export PDF filigrané » dans ce que le palier gratuit **inclut**, et les « élévations
    cotées » dans ce qu'il **bloque** : ce sont bien deux lignes distinctes de la grille, et le
    produit n'en appliquait qu'une.

    La forme du refus est celle qu'impose A11 et elle n'est pas négociable : le fichier **se
    télécharge quand même**. Un palier gratuit reçoit la page de garde, le plan coté de chaque
    pièce et le récapitulatif, le tout filigrané ; il ne reçoit pas les planches d'élévation. Rien
    n'est bloqué, quelque chose est retiré — et ce qui reste prouve que le calcul est juste.
    """

    watermark: bool
    elevations: bool

    @classmethod
    def refused(cls) -> "ExportGrants":
        """Le dossier le plus pauvre : servi quand on ne sait pas à qui appartient le chantier."""
        return cls(watermark=True, elevations=False)


async def register_pdf_export(
    session: AsyncSession,
    *,
    organization_id: int,
    idempotency_key: str,
    project_id: int | None = None,
    user_id: int | None = None,
) -> ExportGrants:
    """Compte un export PDF et rend ce que le palier accorde sur le dossier produit.

    Le serveur, et lui seul, décide : un filigrane apposé côté navigateur se retire en dix secondes
    par la console (`docs/strategie-produit.md` §4). Aucune route n'accepte donc de paramètre — les
    deux réponses se déduisent du palier, ici, et le fichier **se télécharge quand même**. Bloquer
    le téléchargement ferait douter du résultat ; le livrer amputé et filigrané le prouve.
    """
    entitlement = await resolve_entitlement(session, organization_id)
    await record_usage(
        session,
        organization_id=organization_id,
        metric=UsageMetric.EXPORTS_PDF,
        idempotency_key=idempotency_key,
        period_start=entitlement.period_start,
        user_id=user_id,
        metadata={"project_id": project_id},
    )
    return ExportGrants(
        watermark=not entitlement.has(FEATURE_EXPORTS_WITHOUT_WATERMARK),
        elevations=entitlement.has(FEATURE_DIMENSIONED_ELEVATIONS),
    )


async def register_pdf_export_for_task(project_id: int, task_id: str) -> ExportGrants:
    """Même décision, prise depuis un worker Celery — donc hors de tout contexte HTTP.

    La clé d'idempotence **est** l'identifiant de la tâche : un rejeu après incident du courtier
    reprend la même, l'événement n'est pas réécrit et le compteur ne bouge pas. Sans elle, une
    panne de Redis se traduirait en surfacturation.

    Les droits sont recalculés ici plutôt que passés en arguments de la tâche : un argument
    sérialisé dans le courtier survit à un changement de palier, et rejouer une tâche vieille de
    deux jours ressortirait un dossier complet pour un compte redevenu gratuit.
    """
    from app.db import get_session_factory

    async with get_session_factory()() as session:
        organization_id = (
            await session.execute(
                select(col(Project.organization_id)).where(col(Project.id) == project_id)
            )
        ).scalar_one_or_none()
        if organization_id is None:
            # Projet disparu entre la demande et le rendu : ne pas savoir n'est pas une raison de
            # livrer un document propre.
            return ExportGrants.refused()

        grants = await register_pdf_export(
            session,
            organization_id=organization_id,
            idempotency_key=f"{UsageMetric.EXPORTS_PDF}:{task_id}",
            project_id=project_id,
        )
        await session.commit()
        return grants
