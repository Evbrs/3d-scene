"""Commandes d'administration en ligne de commande.

Usage : `python -m app.cli seed-catalog [--no-overwrite]`
"""

import argparse
import asyncio
import sys

from app.db import get_session_factory
from app.services.seed import seed_catalog


async def _seed(overwrite: bool) -> int:
    async with get_session_factory()() as session:
        report = await seed_catalog(session, overwrite=overwrite)
    print(
        f"catalogue : {report.created} créées, {report.updated} mises à jour, "
        f"{report.unchanged} inchangées ({report.total} au total)"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    seed_parser = subparsers.add_parser(
        "seed-catalog", help="charge le catalogue de mobilier de référence"
    )
    seed_parser.add_argument(
        "--no-overwrite",
        action="store_true",
        help="n'écrit que les entrées absentes, sans écraser les recettes modifiées à la main",
    )

    args = parser.parse_args(argv)
    if args.command == "seed-catalog":
        return asyncio.run(_seed(overwrite=not args.no_overwrite))
    return 1


if __name__ == "__main__":
    sys.exit(main())
