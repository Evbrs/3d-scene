"""Routes d'authentification : inscription, connexion, rafraîchissement, profil, cycle de vie.

Pattern JWT du tutoriel officiel FastAPI (`OAuth2PasswordBearer` + `OAuth2PasswordRequestForm`),
conformément à `docs/spec-complete.md` §6 — voir `app/core/security.py` pour l'écart assumé sur
les librairies de hachage et de JWT.

Le module porte aussi ce qui manquait pour qu'un compte soit *vivable* : changer son mot de passe,
le réinitialiser quand on l'a oublié, exporter ses données (RGPD art. 15 et 20) et fermer son
compte (art. 17). Sans la réinitialisation, un mot de passe oublié signifiait le compte et tous
les chantiers perdus définitivement — SQLAdmin exclut le mot de passe de son formulaire et la CLI
ne l'expose pas.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Body, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
from pydantic import Field as PydanticField
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from app.api.deps import CurrentUser, SessionDep
from app.api.permissions import default_organization_id
from app.core.config import get_settings
from app.core.rate_limit import build_login_rate_limiter, too_many_attempts
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    InvalidTokenError,
    create_access_token,
    create_refresh_token,
    decode_token_claims,
    hash_password,
    verify_password,
)
from app.models.base import utcnow
from app.models.user import User
from app.services.account import (
    consume_password_reset,
    delete_account,
    export_account,
    issue_password_reset,
    organizations_blocking_deletion,
    revoke_all_sessions,
)
from app.services.demo import create_demo_project, organization_has_projects

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


# 12 caractères minimum : recommandation NIST SP 800-63B pour un secret choisi par l'humain.
# Déclaré une fois et réutilisé : une borne recopiée à trois endroits finit par diverger, et c'est
# toujours la route la moins visible qui garde l'ancienne.
Password = Annotated[str, PydanticField(min_length=12, max_length=128)]


class UserCreate(BaseModel):
    email: EmailStr
    password: Password


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


def _issue_tokens(response: Response, user: User) -> TokenPair:
    """Émet une paire de jetons et pose le jeton de rafraîchissement en cookie.

    `secure` suit l'environnement : imposé partout ailleurs, il rendrait le cookie invisible en
    développement, où le service tourne en clair sur `localhost` — la session serait perdue à
    chaque expiration du jeton d'accès, c'est-à-dire toutes les trente minutes.

    Les deux jetons embarquent le `token_version` du compte : c'est ce qui rend une révocation
    globale possible, et c'est le compte qui la décide, jamais le porteur du jeton.
    """
    settings = get_settings()
    user_id = user.id or 0
    refresh_token = create_refresh_token(user_id, user.token_version)
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
        access_token=create_access_token(user_id, user.token_version),
        refresh_token=refresh_token if settings.is_development else None,
    )


def _clear_refresh_cookie(response: Response) -> None:
    """Efface le cookie de rafraîchissement.

    Les attributs répétés ici ne sont pas décoratifs : un navigateur n'efface un cookie que si le
    `Path` correspond. Regroupés dans une fonction parce que trois routes en dépendent, et qu'un
    `Path` divergent laisserait une session ouverte après une fermeture de compte.
    """
    response.delete_cookie(
        REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=not get_settings().is_development,
        samesite="lax",
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
    return _issue_tokens(response, user)


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
        subject, version = decode_token_claims(presented, expected_type="refresh")
        user_id = int(subject)
    except (InvalidTokenError, ValueError) as exc:
        raise _invalid_credentials() from exc

    user = (
        await session.execute(select(User).where(col(User.id) == user_id))
    ).scalar_one_or_none()
    # Le compteur de révocation est revérifié ici aussi : sans ça, un jeton de rafraîchissement
    # volé rendrait des jetons d'accès frais pendant des jours, et changer son mot de passe
    # n'aurait fermé que la porte d'entrée.
    if user is None or not user.is_active or version != user.token_version:
        raise _invalid_credentials()

    return _issue_tokens(response, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout() -> Response:
    """Ferme la session en effaçant le cookie de rafraîchissement.

    Sans cette route, un client ne peut **pas** se déconnecter : le cookie est `httpOnly`, donc
    hors de portée du JavaScript qui a posé le bouton « Se déconnecter ».

    La réponse est construite ici plutôt qu'injectée : FastAPI ne reporte les en-têtes du
    paramètre `Response` que sur les retours sérialisés, et le `Set-Cookie` d'effacement serait
    silencieusement perdu.
    """
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(response)
    return response


@router.get("/me", response_model=UserRead)
async def read_current_user(current_user: CurrentUser) -> User:
    return current_user


# --- Cycle de vie du mot de passe ---------------------------------------------------------------


class PasswordChange(BaseModel):
    """Changement de mot de passe par quelqu'un qui connaît l'actuel."""

    current_password: str = PydanticField(max_length=128)
    new_password: Password


class PasswordForgot(BaseModel):
    email: EmailStr


class PasswordReset(BaseModel):
    token: str = PydanticField(min_length=1, max_length=512)
    new_password: Password


class PasswordResetAccepted(BaseModel):
    """Réponse d'une demande de réinitialisation, identique que l'adresse existe ou non.

    Même raisonnement que `RegistrationAccepted` : un 404 pour une adresse inconnue et un 202 pour
    une adresse inscrite formeraient un oracle d'énumération parfait, et l'inscription est déjà
    protégée contre ça — laisser une seconde porte ouverte annulerait la première.

    `reset_token` n'est renseigné **qu'en développement**, et pour la même raison que
    `TokenPair.refresh_token` : aucun service d'acheminement de messages n'existe encore dans le
    dépôt, et la suite de tests comme `curl` doivent pouvoir aller au bout du parcours. En
    production le champ est nul, et **il faut un transport de courriel avant la mise en ligne** —
    c'est écrit ici parce que c'est ici qu'on l'oublierait.
    """

    detail: str = (
        "Si cette adresse correspond à un compte, un lien de réinitialisation vient d'être "
        "envoyé. Il est valable une heure."
    )
    reset_token: str | None = None


class PasswordResetDone(BaseModel):
    detail: str = (
        "Mot de passe mis à jour. Toutes les sessions ouvertes ont été fermées : "
        "reconnectez-vous."
    )


def _wrong_password() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail="Mot de passe actuel incorrect"
    )


@router.patch("/password", response_model=TokenPair)
async def change_password(
    payload: PasswordChange,
    response: Response,
    session: SessionDep,
    current_user: CurrentUser,
) -> TokenPair:
    """Change le mot de passe d'un compte connecté.

    Le mot de passe actuel est exigé : un jeton d'accès volé ne doit pas suffire à verrouiller le
    titulaire hors de son propre compte. C'est la différence entre une session compromise, dont on
    reprend la main, et un compte perdu.

    Toutes les sessions sont révoquées, **y compris celle-ci** — d'où la paire de jetons rendue en
    réponse. Sans elle, l'utilisateur serait déconnecté par son propre changement de mot de passe
    et croirait à un échec.
    """
    ok = await run_in_threadpool(
        verify_password, payload.current_password, current_user.hashed_password
    )
    if not ok:
        raise _wrong_password()

    current_user.hashed_password = await run_in_threadpool(hash_password, payload.new_password)
    revoke_all_sessions(current_user)
    current_user.updated_at = utcnow()
    await session.commit()
    await session.refresh(current_user)

    return _issue_tokens(response, current_user)


@router.post(
    "/password/forgot",
    response_model=PasswordResetAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def forgot_password(
    payload: PasswordForgot, request: Request, session: SessionDep
) -> PasswordResetAccepted:
    """Demande un lien de réinitialisation. Répond toujours 202, avec le même corps.

    Limité en débit comme la connexion et l'inscription : sans ça, la route serait le moyen le
    moins cher d'inonder de messages une adresse qu'on n'a pas, et de périmer en boucle le lien
    qu'un utilisateur légitime vient de recevoir.
    """
    email = normalize_email(payload.email)
    decision = await login_rate_limiter.check(_client_key(request), f"forgot:{email}")
    if not decision.allowed:
        raise too_many_attempts(decision.retry_after)

    user = (
        await session.execute(select(User).where(col(User.email) == email))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        return PasswordResetAccepted()

    token = await issue_password_reset(session, user)
    await session.commit()
    return PasswordResetAccepted(
        reset_token=token if get_settings().is_development else None
    )


@router.post("/password/reset", response_model=PasswordResetDone)
async def reset_password(
    payload: PasswordReset, request: Request, session: SessionDep
) -> PasswordResetDone:
    """Consomme un jeton de réinitialisation et pose le nouveau mot de passe.

    Un jeton inconnu, expiré ou déjà utilisé rend le même 400 : distinguer ces cas dirait à qui
    présente un jeton au hasard s'il a existé. Le refus n'est pas un oracle d'énumération pour
    autant — le secret est le jeton, pas l'adresse.

    La réinitialisation révoque toutes les sessions : quelqu'un a peut-être pris la main sur le
    compte, et c'est précisément le moment de le mettre dehors.
    """
    decision = await login_rate_limiter.check(_client_key(request), "reset")
    if not decision.allowed:
        raise too_many_attempts(decision.retry_after)

    user = await consume_password_reset(session, payload.token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lien de réinitialisation invalide ou expiré. Demandez-en un nouveau.",
        )

    user.hashed_password = await run_in_threadpool(hash_password, payload.new_password)
    revoke_all_sessions(user)
    user.updated_at = utcnow()
    await session.commit()
    return PasswordResetDone()


# --- Portabilité et effacement (RGPD) -----------------------------------------------------------


@router.get("/me/export")
async def export_current_account(
    session: SessionDep, current_user: CurrentUser
) -> dict[str, Any]:
    """Export complet des données du compte, en JSON (RGPD art. 15 et 20).

    Le périmètre est exactement celui que les routes de l'API autorisent : les organisations dont
    le compte est membre **accepté**, et ce qu'elles portent. Un export plus large serait une
    fuite entre locataires déguisée en conformité, ce que `tests/test_rgpd.py` vérifie.
    """
    return await export_account(session, current_user)


class AccountDeletion(BaseModel):
    """Confirmation d'une fermeture de compte.

    Le mot de passe est exigé pour la même raison qu'au changement : un jeton volé ne doit pas
    suffire à détruire les chantiers d'une entreprise.
    """

    current_password: str = PydanticField(max_length=128)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_current_account(
    payload: Annotated[AccountDeletion, Body()],
    session: SessionDep,
    current_user: CurrentUser,
) -> Response:
    """Ferme le compte et efface ses données (RGPD art. 17).

    Refusé — 409 — quand le compte est le dernier propriétaire accepté d'une organisation qui
    compte d'autres membres. Ce n'est pas une restriction du droit à l'effacement : c'est le refus
    de détruire les données d'un tiers au passage. `project.owner_id` porte un
    `ON DELETE CASCADE` : partir emporterait tous les chantiers **créés** par ce compte, y compris
    ceux que ses collègues éditent tous les jours. Le message nomme les organisations à transmettre
    d'abord.
    """
    ok = await run_in_threadpool(
        verify_password, payload.current_password, current_user.hashed_password
    )
    if not ok:
        raise _wrong_password()

    blocking = await organizations_blocking_deletion(session, current_user)
    if blocking:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Vous êtes le dernier propriétaire de : "
                + ", ".join(blocking)
                + ". Nommez un autre propriétaire avant de fermer votre compte, sinon les "
                "chantiers de vos collègues partiraient avec lui."
            ),
        )

    await delete_account(session, current_user)
    await session.commit()

    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(response)
    return response


# --- Accueil --------------------------------------------------------------------------------


class DemoProjectCreated(BaseModel):
    project_id: int
    name: str


@router.post(
    "/demo-project", response_model=DemoProjectCreated, status_code=status.HTTP_201_CREATED
)
async def create_demo(session: SessionDep, current_user: CurrentUser) -> DemoProjectCreated:
    """Sème le chantier de démonstration dans l'organisation par défaut du compte.

    Réservée à un espace **vierge** : dès qu'un chantier existe, la route répond 409 et n'écrit
    rien. C'est ce qui permet à l'artisan de supprimer la démonstration pour de bon — un objet
    qu'on ne peut pas jeter est plus irritant qu'un état vide — et c'est aussi le garde-fou contre
    deux onglets qui la demanderaient en même temps.
    """
    organization_id = await default_organization_id(session, current_user)
    if await organization_has_projects(session, organization_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cet espace contient déjà des chantiers : la démonstration n'est pas recréée.",
        )

    project = await create_demo_project(
        session, organization_id=organization_id, owner_id=current_user.id or 0
    )
    await session.commit()
    return DemoProjectCreated(project_id=project.id or 0, name=project.name)
