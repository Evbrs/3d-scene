"""Le mécanisme des compteurs : atomicité, idempotence, période de facturation.

Ce fichier ne teste pas la grille tarifaire (voir `test_offres.py`) mais la plomberie sous elle.
Trois défauts sont visés, et chacun est de ceux qui ne se voient pas en production : ils ne
provoquent aucune erreur, ils comptent simplement faux.

1. **La course du `SELECT` puis `UPDATE`.** Deux onglets ouverts sur le même compte lisent la même
   valeur et écrivent la même : le second geste passe au-dessus de la limite sans que rien ne le
   signale. Le premier test rejoue ce chemin naïf **à la main** pour montrer qu'il perd bien un
   incrément, puis le second prouve que le chemin réel ne le perd pas.
2. **La surfacturation par rejeu.** Une tâche Celery ressuscitée après un incident du courtier
   repasse par le même code. Sans clé d'idempotence, le client est facturé deux fois pour un
   fichier qu'il n'a demandé qu'une fois.
3. **La remise à zéro du 1er du mois.** Un abonnement souscrit le 20 se remettrait à zéro onze
   jours plus tard, c'est-à-dire offrirait un mois gratuit par mois.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlmodel import SQLModel, col, select

from app.models.billing_plan import (
    Subscription,
    SubscriptionStatus,
    UsageCounter,
    UsageEvent,
    UsageMetric,
)
from app.models.organization import Organization
from app.models.plan import Project
from app.models.user import User
from app.services.quotas import (
    active_project_count,
    add_months,
    billing_period_start,
    counter_value,
    enforce_active_project_limit,
    increment_counter,
    record_usage,
    resolve_entitlement,
    start_trial,
    usage_snapshot,
)
from app.services.seed_plans import (
    FREE_PLAN_CODE,
    LIMIT_ACTIVE_PROJECTS,
    TRIAL_PLAN_CODE,
    ensure_plans_seeded,
)
from tests.conftest import personal_organization

PERIOD = datetime(2026, 8, 1, tzinfo=UTC)


@pytest.fixture
async def organisation(session: AsyncSession, owner: User) -> Organization:
    organization = await personal_organization(session, owner)
    await ensure_plans_seeded(session)
    await session.commit()
    return organization


# --- Période de facturation ---------------------------------------------------------------------


def test_a_subscription_taken_on_the_20th_resets_on_the_20th() -> None:
    """Le mois calendaire offrirait une remise à zéro gratuite le 1er."""
    anchor = datetime(2026, 1, 20, 9, 30, tzinfo=UTC)

    assert billing_period_start(anchor, datetime(2026, 2, 1, tzinfo=UTC)) == anchor
    assert billing_period_start(anchor, datetime(2026, 2, 19, tzinfo=UTC)) == anchor
    assert billing_period_start(anchor, datetime(2026, 2, 20, 9, 31, tzinfo=UTC)) == datetime(
        2026, 2, 20, 9, 30, tzinfo=UTC
    )


def test_the_anchor_day_survives_a_short_month() -> None:
    """Un abonnement du 31 janvier facture le 28 février puis **de nouveau le 31** mars.

    Rabattre définitivement au 28 ferait glisser la date anniversaire vers le début du mois, et le
    client perdrait trois jours de période à chaque année bissextile manquée.
    """
    anchor = datetime(2026, 1, 31, tzinfo=UTC)

    assert add_months(anchor, 1) == datetime(2026, 2, 28, tzinfo=UTC)
    assert add_months(anchor, 2) == datetime(2026, 3, 31, tzinfo=UTC)
    assert add_months(anchor, 12) == datetime(2027, 1, 31, tzinfo=UTC)


def test_an_anchor_in_the_future_never_yields_a_negative_period() -> None:
    """Horloge décalée ou abonnement pris d'avance : l'ancre elle-même fait office de période."""
    anchor = datetime(2027, 1, 1, tzinfo=UTC)
    assert billing_period_start(anchor, datetime(2026, 8, 1, tzinfo=UTC)) == anchor


def test_the_billing_period_ignores_the_calendar_month_entirely() -> None:
    anchor = datetime(2026, 1, 20, tzinfo=UTC)
    # Un an plus tard jour pour jour : douze périodes, pas douze premiers du mois.
    assert billing_period_start(anchor, datetime(2027, 1, 20, tzinfo=UTC)) == datetime(
        2027, 1, 20, tzinfo=UTC
    )


async def test_an_organization_without_subscription_counts_from_its_creation(
    session: AsyncSession, organisation: Organization
) -> None:
    """Un compte gratuit a lui aussi une date anniversaire, et c'est sa création."""
    entitlement = await resolve_entitlement(session, organisation.id or 0)

    assert entitlement.plan.code == FREE_PLAN_CODE
    assert entitlement.period_start <= datetime.now(UTC)
    assert entitlement.period_end == add_months(entitlement.period_start, 1)


async def test_the_billing_anchor_follows_the_subscription_once_it_exists(
    session: AsyncSession, organisation: Organization
) -> None:
    """Dès qu'un abonnement existe, c'est **lui** qui donne la date anniversaire.

    Et il la donne pour toujours : même résilié, il reste le premier de la liste. Repartir de la
    création de l'organisation après une résiliation décalerait la période sans prévenir, donc
    remettrait des compteurs à zéro au milieu d'un mois payé.
    """
    organization_id = organisation.id or 0
    souscrit_le_20 = datetime(2026, 3, 20, 14, 0, tzinfo=UTC)
    session.add(
        Subscription(
            organization_id=organization_id,
            plan_code=TRIAL_PLAN_CODE,
            status=SubscriptionStatus.ACTIVE,
            current_period_start=souscrit_le_20,
            current_period_end=add_months(souscrit_le_20, 1),
        )
    )
    await session.commit()

    entitlement = await resolve_entitlement(
        session, organization_id, now=datetime(2026, 5, 2, tzinfo=UTC)
    )

    assert entitlement.period_start == datetime(2026, 4, 20, 14, 0, tzinfo=UTC)
    assert entitlement.period_end == datetime(2026, 5, 20, 14, 0, tzinfo=UTC)


# --- Atomicité de l'incrément --------------------------------------------------------------------


async def _read_value(factory: async_sessionmaker[AsyncSession], organization_id: int) -> int:
    async with factory() as reader:
        return (
            await reader.execute(
                select(col(UsageCounter.value)).where(
                    col(UsageCounter.organization_id) == organization_id
                )
            )
        ).scalar_one()


async def _write_value(
    factory: async_sessionmaker[AsyncSession], organization_id: int, value: int
) -> None:
    async with factory() as writer:
        row = (
            await writer.execute(
                select(UsageCounter).where(col(UsageCounter.organization_id) == organization_id)
            )
        ).scalar_one()
        row.value = value
        await writer.commit()


async def test_a_read_then_write_really_does_lose_an_increment(
    engine: AsyncEngine, session: AsyncSession, organisation: Organization
) -> None:
    """La preuve par l'absurde, avant la preuve par le code.

    Les deux lectures ont lieu **avant** les deux écritures : c'est exactement l'entrelacement que
    produisent deux onglets ouverts sur le même compte. Le compteur finit à 1 pour deux gestes, et
    aucune erreur n'a été levée — c'est là tout le problème, le défaut ne se voit pas.
    """
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    organization_id = organisation.id or 0
    session.add(
        UsageCounter(
            organization_id=organization_id,
            metric=UsageMetric.EXPORTS_PDF,
            period_start=PERIOD,
            value=0,
        )
    )
    await session.commit()

    lu_par_le_premier = await _read_value(factory, organization_id)
    lu_par_le_second = await _read_value(factory, organization_id)
    await _write_value(factory, organization_id, lu_par_le_premier + 1)
    await _write_value(factory, organization_id, lu_par_le_second + 1)

    session.expunge_all()
    assert await counter_value(
        session,
        organization_id=organization_id,
        metric=UsageMetric.EXPORTS_PDF,
        period_start=PERIOD,
    ) == 1, "le chemin naïf doit perdre un incrément — sinon le test suivant ne prouve rien"


async def test_the_atomic_increment_never_loses_a_concurrent_one(
    engine: AsyncEngine, session: AsyncSession, organisation: Organization
) -> None:
    """Huit incréments réellement concurrents, sur huit sessions distinctes, donnent huit.

    C'est le test que le lot existe pour écrire : chaque `INSERT … ON CONFLICT DO UPDATE SET
    value = value + 1 RETURNING value` lit la valeur laissée par le précédent, parce que la base
    sérialise les écritures sur la ligne — et non l'application, qui n'en a aucun moyen.
    """
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    organization_id = organisation.id or 0

    async def once() -> int:
        async with factory() as concurrent:
            value = await increment_counter(
                concurrent,
                organization_id=organization_id,
                metric=UsageMetric.EXPORTS_PDF,
                period_start=PERIOD,
            )
            await concurrent.commit()
            return value

    obtenus = await asyncio.gather(*(once() for _ in range(8)))

    assert sorted(obtenus) == [1, 2, 3, 4, 5, 6, 7, 8], (
        f"chaque appel doit rendre une valeur distincte : {sorted(obtenus)}"
    )
    assert (
        await counter_value(
            session,
            organization_id=organization_id,
            metric=UsageMetric.EXPORTS_PDF,
            period_start=PERIOD,
        )
        == 8
    )


async def test_the_increment_is_one_single_statement(
    engine: AsyncEngine, session: AsyncSession, organisation: Organization
) -> None:
    """Garde-fou de régression : revenir à un `SELECT` puis `UPDATE` rouvrirait la course.

    Le test précédent resterait vert sur SQLite, dont les écritures se sérialisent d'elles-mêmes.
    Celui-ci regarde le SQL réellement émis.
    """
    emitted: list[str] = []

    def record(_conn: object, _cursor: object, statement: str, *_rest: object) -> None:
        emitted.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        await increment_counter(
            session,
            organization_id=organisation.id or 0,
            metric=UsageMetric.AI_RUNS,
            period_start=PERIOD,
        )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)

    assert len(emitted) == 1, f"un seul aller-retour attendu, {len(emitted)} émis : {emitted}"
    assert "ON CONFLICT" in emitted[0].upper()
    assert "RETURNING" in emitted[0].upper()


async def test_two_periods_of_the_same_metric_are_two_counters(
    session: AsyncSession, organisation: Organization
) -> None:
    """La remise à zéro est structurelle : une nouvelle période est une nouvelle ligne."""
    organization_id = organisation.id or 0
    await increment_counter(
        session,
        organization_id=organization_id,
        metric=UsageMetric.EXPORTS_PDF,
        period_start=PERIOD,
    )
    await increment_counter(
        session,
        organization_id=organization_id,
        metric=UsageMetric.EXPORTS_PDF,
        period_start=add_months(PERIOD, 1),
    )
    await session.commit()

    assert (
        await counter_value(
            session,
            organization_id=organization_id,
            metric=UsageMetric.EXPORTS_PDF,
            period_start=PERIOD,
        )
        == 1
    )
    assert (
        await counter_value(
            session,
            organization_id=organization_id,
            metric=UsageMetric.EXPORTS_PDF,
            period_start=add_months(PERIOD, 1),
        )
        == 1
    )


# --- Idempotence du journal -----------------------------------------------------------------------


async def test_a_replayed_event_is_neither_written_twice_nor_counted_twice(
    session: AsyncSession, organisation: Organization
) -> None:
    """Le scénario réel : une tâche Celery revient après un incident du courtier.

    Elle porte le même identifiant, donc la même clé. L'événement n'est pas réécrit et le compteur
    ne bouge pas — sans quoi l'artisan paierait deux exports pour un seul fichier.
    """
    organization_id = organisation.id or 0
    cle = "exports_pdf:0f2c1a44-tache-celery"

    premier = await record_usage(
        session,
        organization_id=organization_id,
        metric=UsageMetric.EXPORTS_PDF,
        idempotency_key=cle,
        period_start=PERIOD,
    )
    rejeu = await record_usage(
        session,
        organization_id=organization_id,
        metric=UsageMetric.EXPORTS_PDF,
        idempotency_key=cle,
        period_start=PERIOD,
    )
    await session.commit()

    assert premier == 1
    assert rejeu is None, "un rejeu ne compte rien et le dit"
    assert (
        await counter_value(
            session,
            organization_id=organization_id,
            metric=UsageMetric.EXPORTS_PDF,
            period_start=PERIOD,
        )
        == 1
    )

    ecrits = (
        await session.execute(
            select(UsageEvent).where(col(UsageEvent.organization_id) == organization_id)
        )
    ).scalars().all()
    assert len(ecrits) == 1
    assert ecrits[0].idempotency_key == cle


async def test_the_usage_journal_keeps_its_metadata(
    session: AsyncSession, organisation: Organization
) -> None:
    """La colonne s'appelle `metadata` en base, l'attribut Python `event_metadata`.

    `metadata` est réservé par la base déclarative SQLAlchemy : le nom ne peut pas être le même des
    deux côtés, et c'est le genre de détail qu'un renommage distrait ferait disparaître.
    """
    await record_usage(
        session,
        organization_id=organisation.id or 0,
        metric=UsageMetric.EXPORTS_PDF,
        idempotency_key="exports_pdf:avec-contexte",
        period_start=PERIOD,
        metadata={"project_id": 42},
    )
    await session.commit()

    ecrit = (await session.execute(select(UsageEvent))).scalars().one()
    assert ecrit.event_metadata == {"project_id": 42}
    colonnes = {column.name for column in SQLModel.metadata.tables["usage_event"].columns}
    assert "metadata" in colonnes and "event_metadata" not in colonnes


async def test_every_metric_is_reported_even_when_never_used(
    session: AsyncSession, organisation: Organization
) -> None:
    """Les métriques produit sont posées dès maintenant, même à zéro.

    Ajouter une métrique plus tard est facile ; reconstituer son historique est impossible.
    """
    entitlement = await resolve_entitlement(session, organisation.id or 0)
    snapshot = await usage_snapshot(session, entitlement)

    assert set(snapshot) == {str(metric) for metric in UsageMetric}
    for produit in (
        UsageMetric.ACTIVATION,
        UsageMetric.TIME_TO_FIRST_QUOTE,
        UsageMetric.DROP_OFF,
    ):
        assert snapshot[str(produit)] == 0


# --- Essai et déclassement ----------------------------------------------------------------------


async def test_the_trial_is_never_granted_twice(
    session: AsyncSession, organisation: Organization
) -> None:
    organization_id = organisation.id or 0

    assert await start_trial(session, organization_id) is not None
    assert await start_trial(session, organization_id) is None

    lignes = (
        await session.execute(
            select(Subscription).where(col(Subscription.organization_id) == organization_id)
        )
    ).scalars().all()
    assert len(lignes) == 1
    assert lignes[0].plan_code == TRIAL_PLAN_CODE
    assert lignes[0].status is SubscriptionStatus.TRIALING


async def test_an_expired_trial_falls_back_to_the_free_plan(
    session: AsyncSession, organisation: Organization
) -> None:
    """Le statut reste `trialing` en base : c'est la **date** qui décide, pas une tâche de fond.

    Faire basculer le statut demanderait d'écrire pendant une lecture, or les droits se consultent
    sur des chemins qui n'ont aucune raison d'écrire.
    """
    organization_id = organisation.id or 0
    essai = await start_trial(session, organization_id)
    assert essai is not None
    essai.trial_ends_at = datetime.now(UTC) - timedelta(days=1)
    await session.commit()

    entitlement = await resolve_entitlement(session, organization_id)

    assert entitlement.plan.code == FREE_PLAN_CODE
    assert entitlement.subscription is None
    assert entitlement.trial_available is False


async def test_a_past_due_subscription_still_opens_its_plan(
    session: AsyncSession, organisation: Organization
) -> None:
    """Couper un client en pleine journée de chantier coûte plus cher que la relance."""
    organization_id = organisation.id or 0
    now = datetime.now(UTC)
    session.add(
        Subscription(
            organization_id=organization_id,
            plan_code=TRIAL_PLAN_CODE,
            status=SubscriptionStatus.PAST_DUE,
            current_period_start=now - timedelta(days=3),
            current_period_end=now + timedelta(days=27),
        )
    )
    await session.commit()

    entitlement = await resolve_entitlement(session, organization_id)
    assert entitlement.plan.code == TRIAL_PLAN_CODE


async def test_the_downgrade_archives_the_excess_and_deletes_nothing(
    session: AsyncSession, owner: User, organisation: Organization
) -> None:
    """Le chantier sur lequel l'artisan travaille aujourd'hui est celui qui reste ouvert.

    Les excédentaires reçoivent `archived_at` : ils restent lisibles, ils ne sont **jamais**
    supprimés. C'est la seule issue qui ne détruit pas la confiance.
    """
    organization_id = organisation.id or 0
    base = datetime(2026, 8, 1, tzinfo=UTC)
    for index in range(3):
        session.add(
            Project(
                organization_id=organization_id,
                owner_id=owner.id or 0,
                name=f"Chantier {index}",
                updated_at=base + timedelta(days=index),
            )
        )
    await session.commit()

    entitlement = await resolve_entitlement(session, organization_id)
    assert entitlement.limit(LIMIT_ACTIVE_PROJECTS) == 1

    archives = await enforce_active_project_limit(session, entitlement)
    await session.commit()

    restants = (
        await session.execute(
            select(Project).where(col(Project.archived_at).is_(None))
        )
    ).scalars().all()
    tous = (await session.execute(select(Project))).scalars().all()

    assert len(archives) == 2
    assert len(tous) == 3, "aucun projet n'est supprimé par le déclassement"
    assert [projet.name for projet in restants] == ["Chantier 2"]
    assert await active_project_count(session, organization_id) == 1


async def test_the_downgrade_is_idempotent(
    session: AsyncSession, owner: User, organisation: Organization
) -> None:
    """Rejouée, la réconciliation ne réarchive rien : elle ne voit que les chantiers actifs."""
    organization_id = organisation.id or 0
    for index in range(3):
        session.add(
            Project(
                organization_id=organization_id, owner_id=owner.id or 0, name=f"Chantier {index}"
            )
        )
    await session.commit()

    entitlement = await resolve_entitlement(session, organization_id)
    assert len(await enforce_active_project_limit(session, entitlement)) == 2
    await session.commit()
    assert await enforce_active_project_limit(session, entitlement) == []
