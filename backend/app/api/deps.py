"""Dépendances FastAPI : session, utilisateur courant, permissions objet."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.security import InvalidTokenError, decode_token
from app.db import get_session
from app.models.user import User

# `tokenUrl` alimente le bouton « Authorize » de /docs ; il doit pointer sur la vraie route.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")

# Nom de l'attribut portant la session sur `request.state`.
SESSION_STATE_ATTRIBUTE = "db_session"


async def request_session(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> AsyncSession:
    """Session de la requête, également déposée sur `request.state`.

    Le dépôt sert au filet de sécurité de `app/api/conflicts.py` : il rattrape une collision
    remontée par un `flush` implicite, et doit pouvoir annuler la transaction avant de répondre.
    Sans ça, la session reste « à annuler » et la requête suivante échoue en `PendingRollbackError`
    — une panne qui survit à la requête fautive.
    """
    setattr(request.state, SESSION_STATE_ATTRIBUTE, session)
    return session


SessionDep = Annotated[AsyncSession, Depends(request_session)]

_CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Identifiants invalides",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    session: SessionDep,
    token: Annotated[str, Depends(oauth2_scheme)],
) -> User:
    """Utilisateur authentifié par le jeton d'accès.

    Le message d'erreur est volontairement identique pour un jeton invalide, un utilisateur
    supprimé ou un compte désactivé : distinguer ces cas révélerait quels comptes existent.
    """
    try:
        subject = decode_token(token, expected_type="access")
    except InvalidTokenError as exc:
        raise _CREDENTIALS_ERROR from exc

    try:
        user_id = int(subject)
    except ValueError as exc:
        raise _CREDENTIALS_ERROR from exc

    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise _CREDENTIALS_ERROR
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
