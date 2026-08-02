"""Hachage de mot de passe et jetons JWT.

Écart assumé et documenté par rapport à `docs/spec-complete.md` §6 (voir la note ajoutée dans
ce fichier de spec) : le tableau de la spec citait `passlib` / `python-jose` parce que c'était
le couple du tutoriel officiel FastAPI. Ce tutoriel utilise désormais `pyjwt` et `pwdlib`, et
`passlib` (dernière publication en octobre 2020) est cassé avec `bcrypt >= 4.1` —
`module 'bcrypt' has no attribute '__about__'`, vérifié empiriquement. La *raison* donnée par la
spec (« suivre le pattern officiel, le mieux documenté ») conduit donc à `pyjwt` + `pwdlib`.
"""

import secrets
from datetime import datetime, timedelta
from typing import Any, Literal

import jwt
from pwdlib import PasswordHash
from pwdlib.exceptions import PwdlibError

from app.core.config import get_settings
from app.models.base import utcnow

ALGORITHM = "HS256"
TokenType = Literal["access", "refresh"]

# `recommended()` sélectionne Argon2id, lauréat de la Password Hashing Competition et
# recommandation actuelle de l'OWASP pour le stockage de mots de passe.
_password_hash = PasswordHash.recommended()

# Haché une fois au chargement, à partir d'un secret aléatoire jetable. Sert à vérifier un mot de
# passe même lorsque le compte n'existe pas, pour que le temps de réponse de la connexion ne
# révèle pas quelles adresses sont inscrites.
DUMMY_PASSWORD_HASH = _password_hash.hash(secrets.token_urlsafe(32))


class InvalidTokenError(Exception):
    """Jeton absent, expiré, mal signé, ou du mauvais type."""


def hash_password(plain_password: str) -> str:
    return _password_hash.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Vérifie un mot de passe.

    Ne lève jamais : un hachage corrompu en base doit se traduire par un refus d'authentification,
    pas par une 500 qui révèle l'état interne.
    """
    try:
        return _password_hash.verify(plain_password, hashed_password)
    except (PwdlibError, ValueError, TypeError):
        # `PwdlibError` couvre notamment `UnknownHashError` : un hachage illisible en base ne
        # doit pas remonter en 500, qui révélerait l'état interne au client.
        return False


def _create_token(subject: str, token_type: TokenType, expires_in: timedelta) -> str:
    settings = get_settings()
    now = utcnow()
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + expires_in,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def create_access_token(subject: str | int) -> str:
    settings = get_settings()
    return _create_token(
        str(subject), "access", timedelta(minutes=settings.access_token_expire_minutes)
    )


def create_refresh_token(subject: str | int) -> str:
    settings = get_settings()
    return _create_token(
        str(subject), "refresh", timedelta(days=settings.refresh_token_expire_days)
    )


def decode_token(token: str, expected_type: TokenType) -> str:
    """Valide un jeton et retourne son sujet (l'identifiant utilisateur).

    `expected_type` est vérifié explicitement : sans ça, un jeton de rafraîchissement — à durée
    de vie longue — serait accepté comme jeton d'accès, ce qui annule l'intérêt d'avoir deux
    durées de vie distinctes.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[ALGORITHM],
            options={"require": ["exp", "sub", "type"]},
        )
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc)) from exc

    if payload.get("type") != expected_type:
        raise InvalidTokenError(
            f"type de jeton inattendu : {payload.get('type')!r}, attendu {expected_type!r}"
        )

    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        raise InvalidTokenError("sujet de jeton absent ou invalide")
    return subject


def token_expiry(token_type: TokenType) -> datetime:
    """Date d'expiration d'un jeton fraîchement émis (exposée dans les réponses de l'API)."""
    settings = get_settings()
    if token_type == "access":
        return utcnow() + timedelta(minutes=settings.access_token_expire_minutes)
    return utcnow() + timedelta(days=settings.refresh_token_expire_days)
