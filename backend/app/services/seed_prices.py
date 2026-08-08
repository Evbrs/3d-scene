"""Barème de prix par défaut d'une organisation.

Même contrat que `app/services/seed.py` : idempotent, rejouable, et validé par les schémas
Pydantic avant écriture — un barème est du code, une entrée incohérente doit faire échouer le
chargement et non produire un devis silencieusement faux.

Deux différences avec le catalogue de mobilier, et elles sont structurantes :

- le catalogue de mobilier est **global** (spec §4), un barème appartient à une organisation. Il
  est donc semé paresseusement, à la première demande de devis, comme l'organisation personnelle
  elle-même (`app/api/permissions.py::default_organization_id`) ;
- les prix ne sont **jamais** écrasés au rejeu. C'est la différence entre une recette de meuble,
  qui est une donnée de référence du produit, et un prix, qui est la politique commerciale de
  l'artisan. Réécrire ses tarifs parce qu'un déploiement a rejoué le seed serait une faute.

Les montants sont des **entiers de centimes** et les taux des points de base (1000 = 10 %). Les
valeurs sont des ordres de grandeur de second œuvre en rénovation, fournitures et pose comprises :
elles servent de point de départ à modifier, pas de vérité de marché. C'est écrit ici pour que
personne ne les prenne pour un barème officiel.
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from app.models.billing import PriceBook, PriceItem, PriceUnit

DEFAULT_PRICE_BOOK_NAME = "Barème standard"

# (code, libellé, unité, prix unitaire en centimes, taux de TVA en points de base).
#
# Les taux ne sont pas décoratifs : la rénovation d'un logement de plus de deux ans relève de
# 10 %, la rénovation énergétique de 5,5 %, et un logement neuf ou de moins de deux ans de 20 %.
# Le barème porte les trois pour que le mélange, qui est le cas courant d'un chantier, soit
# représentable dès la première utilisation.
DEFAULT_PRICE_ITEMS: tuple[tuple[str, str, PriceUnit, int, int], ...] = (
    # --- Murs ---
    ("PEINT-MUR", "Peinture acrylique sur murs, 2 couches", PriceUnit.SQUARE_METER, 2_400, 1_000),
    ("ENDUIT-MUR", "Enduit de lissage sur murs", PriceUnit.SQUARE_METER, 1_500, 1_000),
    ("TOILE-VERRE", "Toile de verre à peindre et peinture", PriceUnit.SQUARE_METER, 3_100, 1_000),
    ("PAPIER-PEINT", "Papier peint, fourniture et pose", PriceUnit.SQUARE_METER, 3_200, 1_000),
    ("FAIENCE", "Faïence murale, fourniture et pose droite", PriceUnit.SQUARE_METER, 8_500, 1_000),
    ("PLACO-MUR", "Doublage plaque de plâtre sur ossature", PriceUnit.SQUARE_METER, 5_200, 1_000),
    ("LAMBRIS", "Lambris bois, fourniture et pose", PriceUnit.SQUARE_METER, 6_500, 1_000),
    # --- Sols ---
    ("RAGREAGE", "Ragréage autolissant avant pose", PriceUnit.SQUARE_METER, 2_200, 1_000),
    ("CARRELAGE-SOL", "Carrelage de sol, pose droite", PriceUnit.SQUARE_METER, 9_500, 1_000),
    ("PARQUET", "Parquet contrecollé, pose flottante", PriceUnit.SQUARE_METER, 7_800, 1_000),
    ("STRATIFIE", "Sol stratifié, fourniture et pose", PriceUnit.SQUARE_METER, 4_500, 1_000),
    ("SOUPLE-SOL", "Revêtement souple PVC ou linoléum", PriceUnit.SQUARE_METER, 4_200, 1_000),
    ("MOQUETTE", "Moquette, fourniture et pose", PriceUnit.SQUARE_METER, 3_500, 1_000),
    # --- Plafonds ---
    ("PEINT-PLAF", "Peinture sur plafond, deux couches", PriceUnit.SQUARE_METER, 2_800, 1_000),
    ("DALLE-PLAFOND", "Plafond suspendu en dalles", PriceUnit.SQUARE_METER, 5_500, 1_000),
    # --- Linéaires ---
    ("PLINTHE", "Plinthes, fourniture et pose", PriceUnit.LINEAR_METER, 1_800, 1_000),
    ("CORNICHE", "Corniche ou moulure, fourniture et pose", PriceUnit.LINEAR_METER, 2_400, 1_000),
    # --- À l'unité ---
    ("POSE-PORTE", "Dépose et pose d'un bloc-porte", PriceUnit.UNIT, 18_000, 1_000),
    ("POSE-FENETRE", "Dépose et pose d'une menuiserie extérieure", PriceUnit.UNIT, 45_000, 1_000),
    # --- Forfaits ---
    ("PROTECTION", "Protection des sols et du mobilier", PriceUnit.LUMP_SUM, 12_000, 1_000),
    ("DECHETS", "Évacuation des déchets de chantier", PriceUnit.LUMP_SUM, 25_000, 1_000),
    # --- Taux réduit à 5,5 % : rénovation énergétique ---
    ("ISOLATION-ITI", "Isolation thermique par l'intérieur", PriceUnit.SQUARE_METER, 6_800, 550),
    # --- Taux plein à 20 % : hors champ du taux réduit ---
    (
        "TRAVAUX-NEUF",
        "Travaux en logement neuf ou achevé depuis moins de deux ans",
        PriceUnit.SQUARE_METER,
        4_800,
        2_000,
    ),
)


@dataclass
class PriceSeedReport:
    created_books: int = 0
    created_items: int = 0
    unchanged_items: int = 0

    @property
    def total_items(self) -> int:
        return self.created_items + self.unchanged_items


def default_price_item_payloads() -> list[dict[str, Any]]:
    """Le barème de référence, sous forme de dictionnaires — utile aux tests et à la validation."""
    return [
        {
            "code": code,
            "label": label,
            "unit": unit.value,
            "unit_price_cents": price_cents,
            "vat_rate_bp": vat_rate_bp,
        }
        for code, label, unit, price_cents, vat_rate_bp in DEFAULT_PRICE_ITEMS
    ]


async def find_default_price_book(
    session: AsyncSession, organization_id: int
) -> PriceBook | None:
    """Barème par défaut d'une organisation, ou le plus ancien à défaut de drapeau.

    Le repli sur le plus ancien évite qu'une organisation dont personne n'a coché « par défaut »
    se retrouve sans barème alors qu'elle en a trois.
    """
    flagged = (
        await session.execute(
            select(PriceBook)
            .where(
                col(PriceBook.organization_id) == organization_id,
                col(PriceBook.is_default).is_(True),
            )
            .order_by(col(PriceBook.id))
            .limit(1)
        )
    ).scalar_one_or_none()
    if flagged is not None:
        return flagged

    return (
        await session.execute(
            select(PriceBook)
            .where(col(PriceBook.organization_id) == organization_id)
            .order_by(col(PriceBook.id))
            .limit(1)
        )
    ).scalar_one_or_none()


async def seed_default_price_book(
    session: AsyncSession, organization_id: int
) -> tuple[PriceBook, PriceSeedReport]:
    """Crée — ou complète — le barème par défaut d'une organisation.

    Idempotente : rejouée, elle n'ajoute que les codes absents et **ne touche à aucun prix déjà
    saisi**. C'est la seule façon de pouvoir enrichir le barème de référence dans une version
    ultérieure sans réécrire la politique commerciale des clients existants.

    N'émet pas de `commit` : l'appelant décide de la transaction, ce qui permet de semer le barème
    et d'écrire le devis qui l'a déclenché en une seule fois.
    """
    report = PriceSeedReport()

    book = await find_default_price_book(session, organization_id)
    if book is None:
        book = PriceBook(
            organization_id=organization_id, name=DEFAULT_PRICE_BOOK_NAME, is_default=True
        )
        session.add(book)
        await session.flush()
        report.created_books = 1

    existing = {
        row.code
        for row in (
            await session.execute(
                select(PriceItem).where(col(PriceItem.price_book_id) == book.id)
            )
        )
        .scalars()
        .all()
    }

    for code, label, unit, price_cents, vat_rate_bp in DEFAULT_PRICE_ITEMS:
        if code in existing:
            report.unchanged_items += 1
            continue
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
        report.created_items += 1

    await session.flush()
    return book, report
