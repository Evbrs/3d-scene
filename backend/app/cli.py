"""Commandes d'administration en ligne de commande.

Usage :
    python -m app.cli seed-catalog [--no-overwrite]
    python -m app.cli create-superuser <email>
"""

import argparse
import asyncio
import getpass
import sys

from sqlmodel import col, select

from app.db import get_session_factory
from app.models.user import User
from app.services.seed import seed_catalog

# Même plancher que l'inscription par l'API (`UserCreate.password`, recommandation NIST
# SP 800-63B pour un secret choisi par un humain) : un compte à tous les droits n'a pas vocation à
# être le seul à pouvoir être faible.
MIN_PASSWORD_LENGTH = 12


async def _seed(overwrite: bool) -> int:
    async with get_session_factory()() as session:
        report = await seed_catalog(session, overwrite=overwrite)
    print(
        f"catalogue : {report.created} créées, {report.updated} mises à jour, "
        f"{report.unchanged} inchangées ({report.total} au total)"
    )
    return 0


def read_password(prompt: str = "Mot de passe : ") -> str:
    """Lit un mot de passe sans le faire apparaître.

    Jamais en argument de la commande : la ligne de commande est lisible par tout le monde dans
    `ps`, et finit dans l'historique du shell. Le repli sur l'entrée standard permet un `echo ...
    | python -m app.cli create-superuser` scripté, où il n'y a pas de terminal.
    """
    if sys.stdin.isatty():
        return getpass.getpass(prompt)
    return sys.stdin.readline().rstrip("\n")


async def _create_superuser(email: str, password: str) -> int:
    from app.api.auth import normalize_email
    from app.core.security import hash_password

    normalized = normalize_email(email)
    async with get_session_factory()() as session:
        existing = (
            await session.execute(select(User).where(col(User.email) == normalized))
        ).scalar_one_or_none()

        if existing is None:
            session.add(
                User(
                    email=normalized,
                    hashed_password=hash_password(password),
                    is_superuser=True,
                    is_active=True,
                )
            )
            action = "créé"
        else:
            # Promotion plutôt que refus : le cas réel est « ce compte existe déjà et doit devenir
            # administrateur », et le seul recours était jusqu'ici un UPDATE à la main en base de
            # production.
            existing.hashed_password = hash_password(password)
            existing.is_superuser = True
            existing.is_active = True
            action = "mis à jour"

        await session.commit()

    print(f"superutilisateur {normalized} {action}")
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

    superuser_parser = subparsers.add_parser(
        "create-superuser",
        help="crée (ou promeut) un compte administrateur ; le mot de passe est lu sur l'entrée "
        "standard, jamais en argument",
    )
    superuser_parser.add_argument("email", help="adresse e-mail du compte")

    args = parser.parse_args(argv)
    if args.command == "seed-catalog":
        return asyncio.run(_seed(overwrite=not args.no_overwrite))
    if args.command == "create-superuser":
        password = read_password()
        if len(password) < MIN_PASSWORD_LENGTH:
            print(
                f"mot de passe trop court : {MIN_PASSWORD_LENGTH} caractères minimum",
                file=sys.stderr,
            )
            return 2
        return asyncio.run(_create_superuser(args.email, password))
    return 1


if __name__ == "__main__":
    sys.exit(main())
