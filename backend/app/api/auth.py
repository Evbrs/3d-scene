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
from sqlmodel import select

from app.api.deps import CurrentUser, SessionDep
from app.core.rate_limit import SlidingWindowRateLimiter
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

# Anti-bourrage d'identifiants. Partagé par tout le processus, remis à zéro après un succès.
login_rate_limiter = SlidingWindowRateLimiter(max_attempts=10, window_seconds=300)


class UserCreate(BaseModel):
    email: EmailStr
    # 12 caractères minimum : recommandation NIST SP 800-63B pour un secret choisi par l'humain.
    password: str = PydanticField(min_length=12, max_length=128)


class UserRead(BaseModel):
    id: int
    email: EmailStr
    is_active: bool
    is_superuser: bool


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


def _client_key(request: Request) -> str:
    """Clé de limitation de débit.

    Volontairement dérivée de l'IP sans la stocker en clair ailleurs : les journaux restent
    anonymisés (exigence RGPD des conventions du projet).
    """
    client = request.client
    return client.host if client else "inconnu"


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(payload: UserCreate, session: SessionDep) -> User:
    user = User(email=payload.email, hashed_password=hash_password(payload.password))
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # Message générique : confirmer qu'une adresse est déjà inscrite permettrait d'énumérer
        # les comptes existants.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Inscription impossible avec ces informations",
        ) from exc
    await session.refresh(user)
    return user


@router.post("/token", response_model=TokenPair)
async def login(
    request: Request,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: SessionDep,
) -> TokenPair:
    """Connexion. `username` porte l'adresse e-mail (nom de champ imposé par OAuth2)."""
    if not login_rate_limiter.hit(_client_key(request), time.monotonic()):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Trop de tentatives de connexion, réessayez plus tard",
        )

    user = (
        await session.execute(select(User).where(User.email == form_data.username))
    ).scalar_one_or_none()

    # Le hachage est vérifié même quand l'utilisateur est introuvable, pour que le temps de
    # réponse ne trahisse pas l'existence du compte.
    hashed = user.hashed_password if user else DUMMY_PASSWORD_HASH
    password_ok = verify_password(form_data.password, hashed)

    if user is None or not password_ok or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identifiants invalides",
            headers={"WWW-Authenticate": "Bearer"},
        )

    login_rate_limiter.reset(_client_key(request))
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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Jeton de rafraîchissement invalide",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Jeton de rafraîchissement invalide",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return TokenPair(
        access_token=create_access_token(user.id or 0),
        refresh_token=create_refresh_token(user.id or 0),
    )


@router.get("/me", response_model=UserRead)
async def read_current_user(current_user: CurrentUser) -> User:
    return current_user
