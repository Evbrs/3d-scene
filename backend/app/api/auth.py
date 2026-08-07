"""Routes d'authentification : inscription, connexion, rafraîchissement, profil.

Pattern JWT du tutoriel officiel FastAPI (`OAuth2PasswordBearer` + `OAuth2PasswordRequestForm`),
conformément à `docs/spec-complete.md` §6 — voir `app/core/security.py` pour l'écart assumé sur
les librairies de hachage et de JWT.
"""

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
from pydantic import Field as PydanticField
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from app.api.deps import CurrentUser, SessionDep
from app.core.config import get_settings
from app.core.rate_limit import build_login_rate_limiter, too_many_attempts
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

# Anti-bourrage d'identifiants : trois seaux (par IP, par couple IP/compte, par compte) comptés
# dans Redis — voir `core/rate_limit.py`. Le comptage doit être partagé par les workers : en
# mémoire de processus, `--workers 4` quadruple le nombre d'essais réellement accordés.
login_rate_limiter = build_login_rate_limiter()

# Le jeton de rafraîchissement vit dans un cookie `httpOnly` : c'est le seul stockage qu'un script
# injecté dans la page ne peut pas lire. `Path` le restreint aux routes qui en ont besoin, de
# sorte qu'il n'accompagne aucun autre appel de l'API — moins de surface, et pas de CSRF possible
# sur le reste. `SameSite=Lax` suffit ici : le rafraîchissement est un `POST`, que Lax n'envoie
# jamais depuis un site tiers.
REFRESH_COOKIE_NAME = "refresh_token"
REFRESH_COOKIE_PATH = "/api/auth"


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
    """Réponse d'une connexion ou d'un rafraîchissement.

    `refresh_token` n'est renvoyé dans le corps **qu'en développement** : la source de vérité est
    le cookie `httpOnly`, et un jeton long présent dans le corps est un jeton que du JavaScript
    peut lire, donc exfiltrer. Le repli existe pour les clients sans gestion de cookies — la
    suite de tests, `curl`, un script d'intégration — qui tournent tous en développement.
    """

    access_token: str
    refresh_token: str | None = None
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


def _invalid_credentials() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Identifiants invalides",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _issue_tokens(response: Response, user_id: int) -> TokenPair:
    """Émet une paire de jetons et pose le jeton de rafraîchissement en cookie.

    `secure` suit l'environnement : imposé partout ailleurs, il rendrait le cookie invisible en
    développement, où le service tourne en clair sur `localhost` — la session serait perdue à
    chaque expiration du jeton d'accès, c'est-à-dire toutes les trente minutes.
    """
    settings = get_settings()
    refresh_token = create_refresh_token(user_id)
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        refresh_token,
        max_age=settings.refresh_token_expire_days * 24 * 3600,
        httponly=True,
        secure=not settings.is_development,
        samesite="lax",
        path=REFRESH_COOKIE_PATH,
    )
    return TokenPair(
        access_token=create_access_token(user_id),
        refresh_token=refresh_token if settings.is_development else None,
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
    decision = await login_rate_limiter.check(_client_key(request), f"register:{email}")
    if not decision.allowed:
        raise too_many_attempts(decision.retry_after)

    # Argon2id est *conçu* pour être lent (plusieurs dizaines de millisecondes et 64 Mio de
    # mémoire). Exécuté sur la boucle d'événements, il fige toutes les requêtes en cours — et
    # l'inscription comme la connexion sont atteignables sans authentification, donc en volume.
    hashed = await run_in_threadpool(hash_password, payload.password)
    session.add(User(email=email, hashed_password=hashed))
    try:
        await session.commit()
    except IntegrityError:
        # Adresse déjà inscrite : avalé silencieusement pour ne rien révéler au demandeur.
        await session.rollback()

    return RegistrationAccepted()


@router.post("/token", response_model=TokenPair)
async def login(
    request: Request,
    response: Response,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: SessionDep,
) -> TokenPair:
    """Connexion. `username` porte l'adresse e-mail (nom de champ imposé par OAuth2)."""
    email = normalize_email(form_data.username)
    client_key = _client_key(request)

    decision = await login_rate_limiter.check(client_key, email)
    if not decision.allowed:
        raise too_many_attempts(decision.retry_after)

    user = (
        await session.execute(select(User).where(col(User.email) == email))
    ).scalar_one_or_none()

    # Le hachage est vérifié même quand l'utilisateur est introuvable, pour que le temps de
    # réponse ne trahisse pas l'existence du compte.
    hashed = user.hashed_password if user else DUMMY_PASSWORD_HASH
    password_ok = await run_in_threadpool(verify_password, form_data.password, hashed)

    if user is None or not password_ok or not user.is_active:
        raise _invalid_credentials()

    # Seul le seau de cette cible est libéré ; celui de l'IP reste intact.
    await login_rate_limiter.reset_target_async(client_key, email)
    return _issue_tokens(response, user.id or 0)


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    response: Response,
    session: SessionDep,
    payload: RefreshRequest | None = None,
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE_NAME)] = None,
) -> TokenPair:
    """Échange un jeton de rafraîchissement contre une nouvelle paire (rotation).

    Le cookie est la source de vérité du navigateur, mais un jeton **explicitement** fourni dans
    le corps l'emporte : un client qui en présente un demande à valider celui-là, et se replier
    en silence sur le cookie ferait passer un jeton invalide pour bon.
    """
    presented = (payload.refresh_token if payload else None) or refresh_token
    if not presented:
        raise _invalid_credentials()

    try:
        subject = decode_token(presented, expected_type="refresh")
        user_id = int(subject)
    except (InvalidTokenError, ValueError) as exc:
        raise _invalid_credentials() from exc

    user = (
        await session.execute(select(User).where(col(User.id) == user_id))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        raise _invalid_credentials()

    return _issue_tokens(response, user.id or 0)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout() -> Response:
    """Ferme la session en effaçant le cookie de rafraîchissement.

    Sans cette route, un client ne peut **pas** se déconnecter : le cookie est `httpOnly`, donc
    hors de portée du JavaScript qui a posé le bouton « Se déconnecter ». Les attributs répétés
    ici ne sont pas décoratifs — un navigateur n'efface un cookie que si le `Path` correspond.

    La réponse est construite ici plutôt qu'injectée : FastAPI ne reporte les en-têtes du
    paramètre `Response` que sur les retours sérialisés, et le `Set-Cookie` d'effacement serait
    silencieusement perdu.
    """
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=not get_settings().is_development,
        samesite="lax",
    )
    return response


@router.get("/me", response_model=UserRead)
async def read_current_user(current_user: CurrentUser) -> User:
    return current_user
