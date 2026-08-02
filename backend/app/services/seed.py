"""Chargement du catalogue de mobilier en base.

Idempotent : rejouable sans créer de doublon ni écraser une personnalisation involontairement.
C'est ce qui permet de l'appeler au démarrage d'un environnement de développement comme d'une
mise en production.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from app.models.plan import FurnitureType
from app.schemas.furniture import FurnitureTypeCreate
from app.services.catalog import CATALOG


@dataclass
class SeedReport:
    created: int = 0
    updated: int = 0
    unchanged: int = 0

    @property
    def total(self) -> int:
        return self.created + self.updated + self.unchanged


async def seed_catalog(session: AsyncSession, *, overwrite: bool = True) -> SeedReport:
    """Insère ou met à jour les entrées du catalogue de référence.

    `overwrite=False` n'écrit que les entrées absentes : utile pour ne pas écraser des recettes
    ajustées à la main dans le back-office.

    Chaque entrée est revalidée par le schéma Pydantic avant écriture : le catalogue est du code,
    et une recette incohérente (emplacement couleur non déclaré, `auto` sur un axe non répété)
    doit faire échouer le seed, pas produire un rendu silencieusement faux.
    """
    report = SeedReport()

    existing = {
        furniture_type.slug: furniture_type
        for furniture_type in (await session.execute(select(FurnitureType))).scalars().all()
    }

    for raw_entry in CATALOG:
        entry = FurnitureTypeCreate.model_validate(raw_entry)
        data = entry.model_dump(mode="json")
        current = existing.get(entry.slug)

        if current is None:
            session.add(FurnitureType(**data))
            report.created += 1
            continue

        if not overwrite:
            report.unchanged += 1
            continue

        changed = any(getattr(current, field) != value for field, value in data.items())
        if changed:
            for field, value in data.items():
                setattr(current, field, value)
            report.updated += 1
        else:
            report.unchanged += 1

    await session.commit()
    return report


async def catalog_slugs_missing_from(session: AsyncSession) -> list[str]:
    """Entrées du catalogue de référence absentes de la base (diagnostic)."""
    present = set(
        (await session.execute(select(col(FurnitureType.slug)))).scalars().all()
    )
    return sorted({entry["slug"] for entry in CATALOG} - present)
