"""Routes d'authentification : inscription, connexion, rafraîchissement, profil.

Pattern JWT du tutoriel officiel FastAPI (`OAuth2PasswordBearer` + `OAuth2PasswordRequestForm`),
conformément à `docs/spec-complete.md` §6 — voir `app/core/security.py` pour l'écart assumé sur
les librairies de hachage et de JWT.
"""

import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
from pydantic import Field as PydanticField
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from app.api.deps import CurrentUser, SessionDep
from app.core.rate_limit import build_login_rate_limiter
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    InvalidTokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.user import User

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Anti-bourrage d'identifiants. Deux seaux (par cible et par IP) : voir `core/rate_limit.py`.
login_rate_limiter = build_login_rate_limiter()


def normalize_email(email: str) -> str:
    """Forme canonique d'une adresse e-mail.

    La partie domaine est insensible à la casse (RFC 5321) et aucun fournisseur grand public ne
    distingue la casse de la partie locale. Sans normalisation, `Alice@ex.fr` et `alice@ex.fr`
    créent deux comptes distincts, et l'utilisateur qui ressaisit son adresse avec une autre
    casse se retrouve verrouillé hors de son propre compte.
    """
    return email.strip().lower()


class UserCreate(BaseModel):
    email: EmailStr
    # 12 caractères minimum : recommandation NIST SP 800-63B pour un secret choisi par l'humain.
    password: str = PydanticField(min_length=12, max_length=128)


class UserRead(BaseModel):
    id: int
    email: EmailStr
    is_active: bool
    is_superuser: bool


class RegistrationAccepted(BaseModel):
    """Réponse d'inscription, volontairement identique que le compte existe ou non.

    Renvoyer 201 pour une adresse libre et 409 pour une adresse déjà inscrite fournit un oracle
    d'énumération parfait : le code de statut suffit à savoir qui possède un compte, quel que
    soit le soin apporté au message d'erreur.
    """

    detail: str = (
        "Si cette adresse n'est pas déjà utilisée, le compte a été créé. "
        "Connectez-vous pour continuer."
    )


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


def _client_key(request: Request) -> str:
    """Clé de limitation de débit.

    Volontairement dérivée de l'IP sans la stocker ailleurs : les journaux restent anonymisés
    (exigence RGPD des conventions du projet).
    """
    client = request.client
    return client.host if client else "inconnu"


def _too_many_attempts() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Trop de tentatives, réessayez plus tard",
    )


def _invalid_credentials() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Identifiants invalides",
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.post(
    "/register", response_model=RegistrationAccepted, status_code=status.HTTP_202_ACCEPTED
)
async def register(
    payload: UserCreate, request: Request, session: SessionDep
) -> RegistrationAccepted:
    """Inscription.

    Répond toujours 202 avec le même corps, que l'adresse soit libre ou déjà prise (voir
    `RegistrationAccepted`). L'inscription est elle aussi limitée en débit : sans ça, elle
    fournirait une source inépuisable de comptes valides pour contourner la limitation de la
    connexion.
    """
    email = normalize_email(payload.email)
    if not login_rate_limiter.allow(_client_key(request), f"register:{email}", time.monotonic()):
        raise _too_many_attempts()

    session.add(User(email=email, hashed_password=hash_password(payload.password)))
    try:
        await session.commit()
    except IntegrityError:
        # Adresse déjà inscrite : avalé silencieusement pour ne rien révéler au demandeur.
        await session.rollback()

    return RegistrationAccepted()


@router.post("/token", response_model=TokenPair)
async def login(
    request: Request,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: SessionDep,
) -> TokenPair:
    """Connexion. `username` porte l'adresse e-mail (nom de champ imposé par OAuth2)."""
    email = normalize_email(form_data.username)
    client_key = _client_key(request)

    if not login_rate_limiter.allow(client_key, email, time.monotonic()):
        raise _too_many_attempts()

    user = (
        await session.execute(select(User).where(col(User.email) == email))
    ).scalar_one_or_none()

    # Le hachage est vérifié même quand l'utilisateur est introuvable, pour que le temps de
    # réponse ne trahisse pas l'existence du compte.
    hashed = user.hashed_password if user else DUMMY_PASSWORD_HASH
    password_ok = verify_password(form_data.password, hashed)

    if user is None or not password_ok or not user.is_active:
        raise _invalid_credentials()

    # Seul le seau de cette cible est libéré ; celui de l'IP reste intact.
    login_rate_limiter.reset_target(client_key, email)
    return TokenPair(
        access_token=create_access_token(user.id or 0),
        refresh_token=create_refresh_token(user.id or 0),
    )


@router.post("/refresh", response_model=TokenPair)
async def refresh(payload: RefreshRequest, session: SessionDep) -> TokenPair:
    """Échange un jeton de rafraîchissement contre une nouvelle paire (rotation)."""
    try:
        subject = decode_token(payload.refresh_token, expected_type="refresh")
        user_id = int(subject)
    except (InvalidTokenError, ValueError) as exc:
        raise _invalid_credentials() from exc

    user = (
        await session.execute(select(User).where(col(User.id) == user_id))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        raise _invalid_credentials()

    return TokenPair(
        access_token=create_access_token(user.id or 0),
        refresh_token=create_refresh_token(user.id or 0),
    )


@router.get("/me", response_model=UserRead)
async def read_current_user(current_user: CurrentUser) -> User:
    return current_user
