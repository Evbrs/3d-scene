"""API du barème, du devis et de la facture (`docs/strategie-produit.md` §2 et §3.1).

C'est le module qui transforme un plan en argent, et c'est aussi celui qui porte des obligations
légales. Trois invariants le tiennent, et chacun est vérifié par un test dédié.

**Une ligne émise ne bouge plus.** `quote_line` recopie libellé, prix et taux de TVA. Aucune route
ne relit `price_item` pour afficher un devis, et modifier un barème ne change aucun document déjà
écrit — pas même un brouillon. En France un devis signé est un contrat.

**La numérotation est continue et faite en base.** Le numéro n'est attribué qu'à l'émission, par
un `UPDATE ... RETURNING` dans la transaction qui écrit le document : un brouillon abandonné ne
consomme aucun numéro, et un échec d'écriture rend celui qu'il avait pris. Une séquence
PostgreSQL, elle, avance même sur transaction annulée et laisserait des trous définitifs.

**Un document émis se lit, il ne se modifie pas.** `PATCH` n'accepte plus que le statut dès que le
devis est sorti à l'état de brouillon. Corriger une adresse sur un devis parti chez le client se
fait en en émettant un autre, comme sur le papier.

Les rôles suivent la même logique : lire demande `viewer`, préparer demande `editor`, et **émettre
demande `admin`** — engager l'entreprise et fixer ses prix ne sont pas des gestes de production.
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from app.api.deps import CurrentUser, RequireFeature, SessionDep
from app.api.permissions import (
    accessible_organization_ids,
    get_owned_face,
    get_owned_project,
    require_membership,
)
from app.api.takeoff import compute_takeoff
from app.models.base import utcnow
from app.models.billing import (
    DEFAULT_VALIDITY_DAYS,
    DocumentSeries,
    FaceCosting,
    PriceBook,
    PriceItem,
    Quote,
    QuoteCounter,
    QuoteLine,
    QuoteStatus,
)
from app.models.billing_plan import UsageMetric
from app.models.organization import Organization, OrganizationRole
from app.models.plan import Face, Project, Room
from app.schemas.quote import (
    FaceCostingRead,
    FaceCostingWrite,
    PriceBookCreate,
    PriceBookRead,
    PriceItemCreate,
    PriceItemRead,
    PriceItemUpdate,
    QuoteCreate,
    QuoteLineRead,
    QuoteRead,
    QuoteSummary,
    QuoteUpdate,
    VatBucketRead,
)
from app.services.facturx import (
    FacturXDocument,
    build_cii_xml,
    document_from_quote,
    render_facturx_pdf,
)
from app.services.pricing import (
    CostingOverride,
    PriceReference,
    PricingOptions,
    build_quote_lines,
    line_total_cents,
    vat_buckets_from,
)
from app.services.quotas import record_first_quote_delay, record_usage, resolve_entitlement
from app.services.seed_plans import FEATURE_QUOTES
from app.services.seed_prices import (
    DEFAULT_PRICE_ITEMS,
    find_default_price_book,
    seed_default_price_book,
)

router = APIRouter(prefix="/api", tags=["devis"])

# Premier mur de paiement : le devis chiffré. Le métré, lui, reste ouvert — c'est ce qui prouve
# que le calcul est juste avant qu'on demande de payer (`docs/strategie-produit.md` §4).
REQUIRE_QUOTES = RequireFeature(FEATURE_QUOTES)

_NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ressource introuvable")

# Préfixes des deux suites de numérotation. Devis et factures ne partagent pas la leur : ce sont
# deux documents de nature différente, et le fisc ne s'intéresse qu'à la continuité de la seconde.
SERIES_PREFIX = {DocumentSeries.QUOTE: "DEV", DocumentSeries.INVOICE: "FAC"}
NUMBER_DIGITS = 4

# Délai de paiement par défaut d'une facture entre professionnels, à défaut d'accord contraire.
DEFAULT_PAYMENT_DAYS = 30

# Transitions autorisées d'un devis. Tout le reste est refusé en 409 : un statut qui remonte
# (« accepté » redevenu « brouillon ») effacerait la trace d'un accord client.
ALLOWED_TRANSITIONS: dict[QuoteStatus, frozenset[QuoteStatus]] = {
    QuoteStatus.DRAFT: frozenset({QuoteStatus.DRAFT}),
    QuoteStatus.SENT: frozenset({QuoteStatus.SENT, QuoteStatus.ACCEPTED, QuoteStatus.REFUSED}),
    QuoteStatus.ACCEPTED: frozenset({QuoteStatus.ACCEPTED, QuoteStatus.REFUSED}),
    QuoteStatus.REFUSED: frozenset({QuoteStatus.REFUSED}),
    QuoteStatus.INVOICED: frozenset({QuoteStatus.INVOICED}),
}


# Champs du devis recopiés tels quels depuis la demande. Énumérés et non déduits d'un préfixe :
# la liste est ce qu'un relecteur doit pouvoir confronter au formulaire, et un champ ajouté au
# schéma sans être ajouté ici doit se voir, pas se propager tout seul.
COPIED_CLIENT_FIELDS = (
    "client_email",
    "client_phone",
    "client_address_line1",
    "client_address_line2",
    "client_postal_code",
    "client_city",
    "client_country",
    "client_vat_number",
    "client_is_consumer",
    "site_address_line1",
    "site_address_line2",
    "site_postal_code",
    "site_city",
    "vat_attestation_required",
    "vat_attestation_over_two_years",
    "vat_attestation_premises_use",
    "vat_attestation_signatory",
    "vat_attestation_signed_at",
    "payment_terms",
    "late_penalty_rate_bp",
    "recovery_indemnity_cents",
    "mediator_name",
    "mediator_url",
    "notes",
)


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


# --- Numérotation ---------------------------------------------------------------------------------


async def allocate_document_number(
    session: SessionDep, organization_id: int, series: DocumentSeries, moment: datetime
) -> str:
    """Numéro suivant de la série, **attribué par la base**.

    L'incrément est un `UPDATE ... SET next_value = next_value + 1 RETURNING next_value` : la base
    verrouille la ligne du compteur jusqu'au `COMMIT`, ce qui sérialise deux émissions simultanées
    de la même organisation et garantit une suite sans trou. Le numéro n'existe que si la
    transaction qui écrit le document aboutit.

    Python ne fait que la mise en forme (`DEV-2026-0001`). Aucun `SELECT max(...) + 1` : celui-là
    se course avec lui-même et produit deux fois le même numéro sous charge.
    """
    year = moment.year

    for _ in range(2):
        allocated = (
            await session.execute(
                update(QuoteCounter)
                .where(
                    col(QuoteCounter.organization_id) == organization_id,
                    col(QuoteCounter.series) == series,
                    col(QuoteCounter.year) == year,
                )
                .values(next_value=col(QuoteCounter.next_value) + 1, updated_at=utcnow())
                .returning(col(QuoteCounter.next_value))
                # L'ORM n'a rien à resynchroniser : le compteur n'est jamais gardé en mémoire, et
                # `auto` refuserait la combinaison avec `RETURNING`.
                .execution_options(synchronize_session=False)
            )
        ).scalar_one_or_none()
        if allocated is not None:
            return f"{SERIES_PREFIX[series]}-{year}-{allocated:0{NUMBER_DIGITS}d}"

        try:
            # Point de sauvegarde : deux premières émissions simultanées insèrent le même compteur,
            # et la perdante doit pouvoir reprendre sans perdre le reste de la transaction — un
            # `rollback` complet emporterait le devis en cours d'écriture.
            async with session.begin_nested():
                session.add(
                    QuoteCounter(
                        organization_id=organization_id, series=series, year=year, next_value=1
                    )
                )
            return f"{SERIES_PREFIX[series]}-{year}-{1:0{NUMBER_DIGITS}d}"
        except IntegrityError:
            continue

    raise _conflict("Numérotation indisponible : réessayez dans un instant.")


# --- Chargements cloisonnés -----------------------------------------------------------------------


async def _load_price_book(
    session: SessionDep, price_book_id: int, current_user: CurrentUser, minimum: OrganizationRole
) -> PriceBook:
    book = (
        await session.execute(select(PriceBook).where(col(PriceBook.id) == price_book_id))
    ).scalar_one_or_none()
    if book is None:
        raise _NOT_FOUND
    await require_membership(session, book.organization_id, current_user, minimum)
    return book


async def _load_price_item(
    session: SessionDep, price_item_id: int, current_user: CurrentUser, minimum: OrganizationRole
) -> PriceItem:
    item = (
        await session.execute(select(PriceItem).where(col(PriceItem.id) == price_item_id))
    ).scalar_one_or_none()
    if item is None:
        raise _NOT_FOUND
    await _load_price_book(session, item.price_book_id, current_user, minimum)
    return item


async def _load_quote(
    session: SessionDep, quote_id: int, current_user: CurrentUser, minimum: OrganizationRole
) -> Quote:
    quote = (
        await session.execute(select(Quote).where(col(Quote.id) == quote_id))
    ).scalar_one_or_none()
    if quote is None:
        raise _NOT_FOUND
    await require_membership(session, quote.organization_id, current_user, minimum)
    return quote


async def _quote_lines(session: SessionDep, quote_id: int) -> list[QuoteLine]:
    return list(
        (
            await session.execute(
                select(QuoteLine)
                .where(col(QuoteLine.quote_id) == quote_id)
                .order_by(col(QuoteLine.position), col(QuoteLine.id))
            )
        )
        .scalars()
        .all()
    )


def _to_read(quote: Quote, lines: list[QuoteLine]) -> QuoteRead:
    """Vue complète d'un devis.

    `vat_breakdown` est **recalculé** depuis les lignes plutôt que stocké : c'est une vue, pas une
    donnée, et la stocker en ferait une seconde vérité à maintenir. Les totaux, eux, sont relus
    depuis les colonnes — ce sont ceux qui ont été imprimés, et un recalcul les ferait dépendre de
    la version du code qui relit le document.
    """
    payload = QuoteRead.model_validate(quote)
    payload.lines = [QuoteLineRead.model_validate(line) for line in lines]
    payload.vat_breakdown = [
        VatBucketRead(
            rate_bp=bucket.rate_bp, base_cents=bucket.base_cents, tax_cents=bucket.tax_cents
        )
        for bucket in vat_buckets_from((line.vat_rate_bp, line.total_ht_cents) for line in lines)
    ]
    return payload


# --- Barèmes --------------------------------------------------------------------------------------


@router.get("/organizations/{organization_id}/price-books", response_model=list[PriceBookRead])
async def list_price_books(
    organization_id: int, session: SessionDep, current_user: CurrentUser
) -> list[PriceBook]:
    await require_membership(session, organization_id, current_user, OrganizationRole.VIEWER)
    return list(
        (
            await session.execute(
                select(PriceBook)
                .where(col(PriceBook.organization_id) == organization_id)
                .order_by(col(PriceBook.id))
            )
        )
        .scalars()
        .all()
    )


@router.post(
    "/organizations/{organization_id}/price-books",
    response_model=PriceBookRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_price_book(
    organization_id: int,
    payload: PriceBookCreate,
    session: SessionDep,
    current_user: CurrentUser,
) -> PriceBook:
    """Crée un barème. Réservé aux `admin` : un prix est une décision commerciale.

    `is_default` est exclusif — poser le drapeau le retire aux autres barèmes de l'organisation.
    L'unicité est tenue ici plutôt que par un index partiel, qui ne se reconstruit pas en mode
    batch sur SQLite.
    """
    await require_membership(session, organization_id, current_user, OrganizationRole.ADMIN)

    book = PriceBook(
        organization_id=organization_id, name=payload.name, is_default=payload.is_default
    )
    session.add(book)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise _conflict("Un barème porte déjà ce nom dans cette organisation.") from exc

    if payload.seed_default_items:
        for code, label, unit, price_cents, vat_rate_bp in DEFAULT_PRICE_ITEMS:
            session.add(
                PriceItem(
                    price_book_id=book.id or 0,
                    code=code,
                    label=label,
                    unit=unit,
                    unit_price_cents=price_cents,
                    vat_rate_bp=vat_rate_bp,
                )
            )

    if payload.is_default:
        await _clear_other_defaults(session, organization_id, book.id or 0)

    await session.commit()
    await session.refresh(book)
    return book


async def _clear_other_defaults(
    session: SessionDep, organization_id: int, keep_id: int
) -> None:
    others = (
        (
            await session.execute(
                select(PriceBook).where(
                    col(PriceBook.organization_id) == organization_id,
                    col(PriceBook.id) != keep_id,
                    col(PriceBook.is_default).is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    for other in others:
        other.is_default = False
        other.updated_at = utcnow()


@router.get("/price-books/{price_book_id}/items", response_model=list[PriceItemRead])
async def list_price_items(
    price_book_id: int, session: SessionDep, current_user: CurrentUser
) -> list[PriceItem]:
    await _load_price_book(session, price_book_id, current_user, OrganizationRole.VIEWER)
    return list(
        (
            await session.execute(
                select(PriceItem)
                .where(col(PriceItem.price_book_id) == price_book_id)
                .order_by(col(PriceItem.code))
            )
        )
        .scalars()
        .all()
    )


@router.post(
    "/price-books/{price_book_id}/items",
    response_model=PriceItemRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_price_item(
    price_book_id: int,
    payload: PriceItemCreate,
    session: SessionDep,
    current_user: CurrentUser,
) -> PriceItem:
    await _load_price_book(session, price_book_id, current_user, OrganizationRole.ADMIN)
    item = PriceItem(price_book_id=price_book_id, **payload.model_dump())
    session.add(item)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise _conflict(f"Le code « {payload.code} » existe déjà dans ce barème.") from exc
    await session.refresh(item)
    return item


@router.patch("/price-items/{price_item_id}", response_model=PriceItemRead)
async def update_price_item(
    price_item_id: int,
    payload: PriceItemUpdate,
    session: SessionDep,
    current_user: CurrentUser,
) -> PriceItem:
    """Modifie une ligne de barème.

    Aucun devis déjà écrit n'en est affecté, y compris un brouillon : la ligne de devis est une
    copie et ne fait aucune jointure de lecture vers cette table. C'est vérifié par un test.
    """
    item = await _load_price_item(session, price_item_id, current_user, OrganizationRole.ADMIN)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    item.updated_at = utcnow()
    await session.commit()
    await session.refresh(item)
    return item


@router.delete("/price-items/{price_item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_price_item(
    price_item_id: int, session: SessionDep, current_user: CurrentUser
) -> Response:
    item = await _load_price_item(session, price_item_id, current_user, OrganizationRole.ADMIN)
    await session.delete(item)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Rattachement des faces -----------------------------------------------------------------------


@router.get("/projects/{project_id}/costings", response_model=list[FaceCostingRead])
async def list_face_costings(
    project_id: int, session: SessionDep, current_user: CurrentUser
) -> list[FaceCosting]:
    """Rattachements explicites du projet. Ils sont l'exception, pas la règle."""
    await get_owned_project(session, project_id, current_user)
    return list(
        (
            await session.execute(
                select(FaceCosting)
                .join(Face, col(Face.id) == col(FaceCosting.face_id))
                .join(Room, col(Room.id) == col(Face.room_id))
                .where(col(Room.project_id) == project_id)
                .order_by(col(FaceCosting.face_id))
            )
        )
        .scalars()
        .all()
    )


@router.put("/faces/{face_id}/costing", response_model=FaceCostingRead)
async def set_face_costing(
    face_id: int, payload: FaceCostingWrite, session: SessionDep, current_user: CurrentUser
) -> FaceCosting:
    """Pose — ou remplace — le chiffrage d'une face.

    `PUT` et non `PATCH` : la ressource est un petit ensemble de trois décisions, et un remplacement
    complet évite l'ambiguïté entre « ne touche pas » et « efface » sur des champs qui sont tous
    facultatifs.
    """
    await get_owned_face(session, face_id, current_user, OrganizationRole.EDITOR)

    costing = (
        await session.execute(select(FaceCosting).where(col(FaceCosting.face_id) == face_id))
    ).scalar_one_or_none()
    if costing is None:
        costing = FaceCosting(face_id=face_id)
        session.add(costing)

    costing.price_item_code = payload.price_item_code
    costing.override_quantity = payload.override_quantity
    costing.override_unit_price_cents = payload.override_unit_price_cents
    costing.updated_at = utcnow()

    await session.commit()
    await session.refresh(costing)
    return costing


@router.delete("/faces/{face_id}/costing", status_code=status.HTTP_204_NO_CONTENT)
async def delete_face_costing(
    face_id: int, session: SessionDep, current_user: CurrentUser
) -> Response:
    await get_owned_face(session, face_id, current_user, OrganizationRole.EDITOR)
    costing = (
        await session.execute(select(FaceCosting).where(col(FaceCosting.face_id) == face_id))
    ).scalar_one_or_none()
    if costing is not None:
        await session.delete(costing)
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Devis ----------------------------------------------------------------------------------------


async def _references_for(session: SessionDep, price_book_id: int) -> dict[str, PriceReference]:
    """Instantané du barème, indexé par code. Lu **une fois**, avant le chiffrage."""
    rows = (
        (
            await session.execute(
                select(PriceItem).where(col(PriceItem.price_book_id) == price_book_id)
            )
        )
        .scalars()
        .all()
    )
    return {
        row.code: PriceReference(
            code=row.code,
            label=row.label,
            unit=row.unit.value,
            unit_price_cents=row.unit_price_cents,
            vat_rate_bp=row.vat_rate_bp,
        )
        for row in rows
    }


async def _costings_for(session: SessionDep, project_id: int) -> dict[int, CostingOverride]:
    rows = (
        (
            await session.execute(
                select(FaceCosting)
                .join(Face, col(Face.id) == col(FaceCosting.face_id))
                .join(Room, col(Room.id) == col(Face.room_id))
                .where(col(Room.project_id) == project_id)
            )
        )
        .scalars()
        .all()
    )
    return {
        row.face_id: CostingOverride(
            price_item_code=row.price_item_code,
            quantity=row.override_quantity,
            unit_price_cents=row.override_unit_price_cents,
        )
        for row in rows
    }


@router.post(
    "/projects/{project_id}/quotes", response_model=QuoteRead, status_code=status.HTTP_201_CREATED
)
async def create_quote(
    project_id: int, payload: QuoteCreate, session: SessionDep, current_user: CurrentUser
) -> QuoteRead:
    """Crée un devis **brouillon** à partir du métré du projet.

    Sans numéro : il n'est attribué qu'à l'émission, sinon chaque brouillon abandonné ferait un
    trou dans une suite qui doit être continue.

    Les lignes sont écrites en dur — libellé, prix unitaire, taux — et ne référencent le barème
    que par un code, à titre de trace. Modifier le barème après coup ne les touche pas.

    C'est le **premier mur de paiement** (`docs/strategie-produit.md` §4) : le métré, lui, reste
    entièrement lisible sans abonnement (`GET /api/projects/{id}/takeoff`), et c'est délibéré — on
    prouve que le calcul est juste avant de demander de payer. Le premier devis demandé ouvre
    l'essai de 14 jours sans carte.
    """
    project = await get_owned_project(session, project_id, current_user, OrganizationRole.EDITOR)
    entitlement = await REQUIRE_QUOTES(session, project.organization_id)
    # Métrique produit, posée une seule fois par entreprise : combien de temps s'écoule entre
    # l'ouverture du compte et le premier devis établi. Son historique ne se reconstitue pas.
    await record_first_quote_delay(session, entitlement, user_id=current_user.id)

    book = await _resolve_price_book(session, project, payload.price_book_id)
    references = await _references_for(session, book.id or 0)
    costings = await _costings_for(session, project_id)
    takeoff = await compute_takeoff(session, project_id, project.version)

    plan = build_quote_lines(
        takeoff,
        references,
        costings,
        PricingOptions(
            default_price_codes=dict(payload.default_price_codes),
            include_skirting=payload.include_skirting,
            include_cornice=payload.include_cornice,
            include_openings=payload.include_openings,
        ),
    )

    quote = Quote(
        organization_id=project.organization_id,
        project_id=project_id,
        project_name=project.name,
        status=QuoteStatus.DRAFT,
        valid_until=utcnow() + timedelta(days=payload.valid_for_days or DEFAULT_VALIDITY_DAYS),
        client_name=payload.client_name,
        warnings=list(plan.warnings),
    )
    # `exclude_none` : un champ laissé vide garde la valeur par défaut de la colonne — c'est ainsi
    # que l'indemnité de recouvrement et le taux de pénalité restent renseignés sans que l'artisan
    # ait à les ressaisir sur chaque devis.
    supplied = payload.model_dump(include=set(COPIED_CLIENT_FIELDS), exclude_none=True)
    for field, value in supplied.items():
        setattr(quote, field, value)

    session.add(quote)
    await session.flush()

    position = 0
    for proposed in plan.lines:
        position += 1
        session.add(
            QuoteLine(
                quote_id=quote.id or 0,
                position=position,
                label=proposed.label,
                unit=proposed.unit,
                quantity=proposed.quantity,
                unit_price_cents=proposed.unit_price_cents,
                vat_rate_bp=proposed.vat_rate_bp,
                total_ht_cents=proposed.total_ht_cents,
                source_face_id=proposed.source_face_id,
                source_price_item_code=proposed.source_price_item_code,
            )
        )
    for extra in payload.extra_lines:
        position += 1
        session.add(
            QuoteLine(
                quote_id=quote.id or 0,
                position=position,
                label=extra.label,
                unit=extra.unit,
                quantity=extra.quantity,
                unit_price_cents=extra.unit_price_cents,
                vat_rate_bp=extra.vat_rate_bp,
                total_ht_cents=line_total_cents(extra.quantity, extra.unit_price_cents),
                source_price_item_code=extra.source_price_item_code,
            )
        )

    await session.flush()
    lines = await _quote_lines(session, quote.id or 0)
    _apply_totals(quote, lines)
    await session.commit()
    await session.refresh(quote)

    return _to_read(quote, await _quote_lines(session, quote.id or 0))


def _apply_totals(quote: Quote, lines: list[QuoteLine]) -> None:
    """Fige les trois totaux du document à partir de ses lignes."""
    buckets = vat_buckets_from((line.vat_rate_bp, line.total_ht_cents) for line in lines)
    quote.total_ht_cents = sum(line.total_ht_cents for line in lines)
    quote.total_tva_cents = sum(bucket.tax_cents for bucket in buckets)
    quote.total_ttc_cents = quote.total_ht_cents + quote.total_tva_cents


async def _resolve_price_book(
    session: SessionDep, project: Project, wanted_id: int | None
) -> PriceBook:
    """Barème à utiliser, semé au besoin.

    Le barème par défaut est créé paresseusement, à la première demande de devis, exactement comme
    l'organisation personnelle : un chemin de création de compte qui l'oublierait rendrait la
    fonctionnalité inutilisable sans le dire.
    """
    if wanted_id is not None:
        book = (
            await session.execute(select(PriceBook).where(col(PriceBook.id) == wanted_id))
        ).scalar_one_or_none()
        # Cloisonnement : un identifiant de barème appartenant à une autre organisation ne doit ni
        # servir, ni révéler son existence.
        if book is None or book.organization_id != project.organization_id:
            raise _NOT_FOUND
        return book

    existing = await find_default_price_book(session, project.organization_id)
    if existing is not None:
        return existing
    book, _report = await seed_default_price_book(session, project.organization_id)
    return book


@router.get("/projects/{project_id}/quotes", response_model=list[QuoteSummary])
async def list_project_quotes(
    project_id: int, session: SessionDep, current_user: CurrentUser
) -> list[Quote]:
    await get_owned_project(session, project_id, current_user)
    return list(
        (
            await session.execute(
                select(Quote)
                .where(col(Quote.project_id) == project_id)
                .order_by(col(Quote.id).desc())
            )
        )
        .scalars()
        .all()
    )


@router.get("/quotes", response_model=list[QuoteSummary])
async def list_quotes(session: SessionDep, current_user: CurrentUser) -> list[Quote]:
    """Tous les devis des organisations du compte, le plus récent d'abord."""
    organization_ids = await accessible_organization_ids(session, current_user)
    if not organization_ids:
        return []
    return list(
        (
            await session.execute(
                select(Quote)
                .where(col(Quote.organization_id).in_(organization_ids))
                .order_by(col(Quote.id).desc())
            )
        )
        .scalars()
        .all()
    )


@router.get("/quotes/{quote_id}", response_model=QuoteRead)
async def read_quote(quote_id: int, session: SessionDep, current_user: CurrentUser) -> QuoteRead:
    quote = await _load_quote(session, quote_id, current_user, OrganizationRole.VIEWER)
    return _to_read(quote, await _quote_lines(session, quote_id))


@router.patch("/quotes/{quote_id}", response_model=QuoteRead)
async def update_quote(
    quote_id: int, payload: QuoteUpdate, session: SessionDep, current_user: CurrentUser
) -> QuoteRead:
    """Modifie un devis.

    Tant qu'il est brouillon, tout est modifiable sauf les lignes — celles-ci se régénèrent en
    créant un nouveau devis, ce qui évite d'avoir à réconcilier un métré et une saisie manuelle.

    Dès qu'il est émis, **seul le statut** peut encore changer : le document est parti chez le
    client, et le corriger silencieusement ferait diverger deux exemplaires du même contrat.
    """
    quote = await _load_quote(session, quote_id, current_user, OrganizationRole.EDITOR)
    changes = payload.model_dump(exclude_unset=True)

    new_status = changes.pop("status", None)
    if new_status is not None:
        allowed = ALLOWED_TRANSITIONS[quote.status]
        if new_status not in allowed:
            raise _conflict(
                f"Transition impossible : « {quote.status.value} » → « {new_status.value} »."
            )
        quote.status = new_status

    if changes and quote.status is not QuoteStatus.DRAFT:
        raise _conflict(
            "Ce document est émis : son contenu est figé. Seul son statut peut encore changer — "
            "pour corriger une information, émettez un nouveau devis."
        )

    for field, value in changes.items():
        setattr(quote, field, value)
    quote.updated_at = utcnow()
    await session.commit()
    await session.refresh(quote)
    return _to_read(quote, await _quote_lines(session, quote_id))


@router.post("/quotes/{quote_id}/issue", response_model=QuoteRead)
async def issue_quote(quote_id: int, session: SessionDep, current_user: CurrentUser) -> QuoteRead:
    """Émet le devis : lui attribue son numéro et le fige.

    `admin` et non `editor` : c'est le geste qui engage l'entreprise sur un prix.
    """
    quote = await _load_quote(session, quote_id, current_user, OrganizationRole.ADMIN)
    if quote.status is not QuoteStatus.DRAFT:
        raise _conflict("Ce devis est déjà émis.")

    now = utcnow()
    quote.number = await allocate_document_number(
        session, quote.organization_id, DocumentSeries.QUOTE, now
    )
    quote.status = QuoteStatus.SENT
    quote.issued_at = now
    if quote.valid_until is None:
        quote.valid_until = now + timedelta(days=DEFAULT_VALIDITY_DAYS)
    quote.updated_at = now

    # Consommation comptée à l'émission et non à la création : un brouillon abandonné ne consomme
    # rien, exactement comme il ne consomme aucun numéro. La clé d'idempotence est l'identifiant du
    # devis, qui ne s'émet qu'une fois — un double clic ne compte donc pas deux fois.
    entitlement = await resolve_entitlement(session, quote.organization_id)
    await record_usage(
        session,
        organization_id=quote.organization_id,
        metric=UsageMetric.QUOTES_ISSUED,
        idempotency_key=f"{UsageMetric.QUOTES_ISSUED}:{quote_id}",
        period_start=entitlement.period_start,
        user_id=current_user.id,
        metadata={"quote_id": quote_id, "project_id": quote.project_id},
    )

    _apply_totals(quote, await _quote_lines(session, quote_id))
    await session.commit()
    await session.refresh(quote)
    return _to_read(quote, await _quote_lines(session, quote_id))


@router.post("/quotes/{quote_id}/invoice", response_model=QuoteRead)
async def convert_to_invoice(
    quote_id: int, session: SessionDep, current_user: CurrentUser
) -> QuoteRead:
    """Transforme un devis **accepté** en facture, aux mêmes lignes et aux mêmes prix.

    Aucune ligne n'est recopiée ni recalculée : c'est le même document qui change d'état. Dupliquer
    créerait deux vérités pour un seul contrat, et l'écart entre les deux est ce qui fait les
    litiges.
    """
    quote = await _load_quote(session, quote_id, current_user, OrganizationRole.ADMIN)
    if quote.status is QuoteStatus.INVOICED:
        raise _conflict("Ce devis a déjà été facturé.")
    if quote.status is not QuoteStatus.ACCEPTED:
        raise _conflict(
            "Seul un devis accepté se facture : faites-le passer à « accepted » d'abord."
        )

    now = utcnow()
    quote.invoice_number = await allocate_document_number(
        session, quote.organization_id, DocumentSeries.INVOICE, now
    )
    quote.invoiced_at = now
    quote.due_date = now + timedelta(days=DEFAULT_PAYMENT_DAYS)
    quote.status = QuoteStatus.INVOICED
    quote.updated_at = now

    await session.commit()
    await session.refresh(quote)
    return _to_read(quote, await _quote_lines(session, quote_id))


# --- Documents imprimables ------------------------------------------------------------------------


async def _document_for(
    session: SessionDep, quote: Quote, *, as_invoice: bool
) -> FacturXDocument:
    organization = (
        await session.execute(
            select(Organization).where(col(Organization.id) == quote.organization_id)
        )
    ).scalar_one_or_none()
    if organization is None:
        raise _NOT_FOUND
    lines = await _quote_lines(session, quote.id or 0)
    try:
        return document_from_quote(organization, quote, lines, as_invoice=as_invoice)
    except ValueError as exc:
        raise _conflict(str(exc)) from exc


@router.get(
    "/quotes/{quote_id}/pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "Devis au format PDF"}},
)
async def download_quote_pdf(
    quote_id: int, session: SessionDep, current_user: CurrentUser
) -> Response:
    """PDF du devis, avec toutes ses mentions obligatoires et le cadre « bon pour accord ».

    Pas de XML embarqué : un devis n'est pas une facture, et la norme EN 16931 n'a pas de code de
    type pour un document non exigible.
    """
    quote = await _load_quote(session, quote_id, current_user, OrganizationRole.VIEWER)
    document = await _document_for(session, quote, as_invoice=False)
    content = render_facturx_pdf(document)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="devis-{quote.number}.pdf"'},
    )


@router.get(
    "/quotes/{quote_id}/invoice.pdf",
    response_class=Response,
    responses={
        200: {"content": {"application/pdf": {}}, "description": "Facture Factur-X (PDF/A-3)"}
    },
)
async def download_invoice_pdf(
    quote_id: int, session: SessionDep, current_user: CurrentUser
) -> Response:
    """Facture au format Factur-X : PDF/A-3 lisible avec le XML CII embarqué.

    Rappel écrit dans le document lui-même : nous **ne sommes pas** une plateforme de
    dématérialisation agréée et nous ne transmettons rien à l'administration. Ce fichier est
    conforme ; sa transmission reste à la charge de l'artisan, avec l'outil de son choix.
    """
    quote = await _load_quote(session, quote_id, current_user, OrganizationRole.VIEWER)
    document = await _document_for(session, quote, as_invoice=True)
    content = render_facturx_pdf(document)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="facture-{quote.invoice_number}.pdf"'
        },
    )


@router.get(
    "/quotes/{quote_id}/invoice.xml",
    response_class=Response,
    responses={200: {"content": {"application/xml": {}}, "description": "XML CII seul"}},
)
async def download_invoice_xml(
    quote_id: int, session: SessionDep, current_user: CurrentUser
) -> Response:
    """Le XML CII seul, pour l'artisan qui alimente déjà une plateforme avec ses propres outils."""
    quote = await _load_quote(session, quote_id, current_user, OrganizationRole.VIEWER)
    document = await _document_for(session, quote, as_invoice=True)
    return Response(
        content=build_cii_xml(document),
        media_type="application/xml",
        headers={
            "Content-Disposition": f'attachment; filename="facture-{quote.invoice_number}.xml"'
        },
    )


__all__ = ["allocate_document_number", "router"]
