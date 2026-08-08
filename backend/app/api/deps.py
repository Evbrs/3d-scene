"""Dépendances FastAPI : session, utilisateur courant, permissions objet, murs de paiement."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.security import InvalidTokenError, decode_token_claims
from app.db import get_session
from app.models.billing_plan import UsageMetric
from app.models.user import User
from app.services.quotas import (
    Entitlement,
    active_project_count,
    counter_value,
    resolve_entitlement,
    start_trial,
)
from app.services.seed_plans import required_plan_for_feature, required_plan_for_limit

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

    `token_version` est confronté à celui du compte : c'est ce qui rend une révocation globale
    possible. Un JWT est valide tant qu'il n'a pas expiré, et rien d'autre — sans cette
    comparaison, changer son mot de passe après un vol de session laisserait le voleur à
    l'intérieur pendant toute la durée de vie du jeton dérobé.
    """
    try:
        subject, version = decode_token_claims(token, expected_type="access")
    except InvalidTokenError as exc:
        raise _CREDENTIALS_ERROR from exc

    try:
        user_id = int(subject)
    except ValueError as exc:
        raise _CREDENTIALS_ERROR from exc

    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or not user.is_active or version != user.token_version:
        raise _CREDENTIALS_ERROR
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


# --- Murs de paiement ----------------------------------------------------------------------------
#
# `RequireFeature` et `RequireQuota` sont des dépendances **configurées à l'import** et appelées
# dans le corps de la route, et non posées en `Depends(...)`. La raison est concrète :
# l'organisation à qui appartient le geste ne se lit pas dans l'URL, elle se déduit de l'objet visé
# (le projet du devis, le projet de l'export) après vérification d'appartenance. Une dépendance
# FastAPI résoudrait donc le locataire une seconde fois, et pourrait le résoudre **autrement** que
# la route — c'est-à-dire ouvrir un écart entre ce qui autorise et ce qui facture.
#
# Les deux refus portent un corps machine-lisible au premier niveau, comme le 409 du plan : le
# frontend doit pouvoir proposer le bon palier sans analyser une phrase en français.


class PaywallError(Exception):
    """Refus commercial. Traduit en réponse par les gestionnaires branchés dans `app/main.py`."""

    status_code: int = status.HTTP_402_PAYMENT_REQUIRED

    def __init__(self, detail: str, payload: dict[str, object]) -> None:
        self.detail = detail
        self.payload = payload
        super().__init__(detail)

    def to_response(self) -> JSONResponse:
        return JSONResponse(status_code=self.status_code, content=self.payload)


class FeatureRequired(PaywallError):
    """402 : la fonctionnalité existe, le palier courant n'y donne pas droit."""

    status_code = status.HTTP_402_PAYMENT_REQUIRED


class QuotaExceeded(PaywallError):
    """429 : le geste est permis par le palier, mais le plafond de la période est atteint.

    429 et non 402 : ce n'est pas la fonctionnalité qui manque, c'est le compteur qui est plein.
    Le client peut réessayer à la période suivante sans rien acheter, et la distinction change ce
    que l'interface doit proposer.
    """

    status_code = status.HTTP_429_TOO_MANY_REQUESTS


class ProjectReadOnly(PaywallError):
    """403 : le chantier a été déclassé en lecture seule, il n'est pas supprimé pour autant.

    403 et non 402 : ce n'est pas une fonctionnalité qui manque, c'est l'état de **cet** objet.
    Et 403 plutôt que 404, pour la même raison qu'un rôle insuffisant (`app/api/permissions.py`) :
    l'appelant peut lire le chantier, lui répondre 404 lui ferait croire à une disparition et
    transformerait une question d'abonnement en incident de données.

    Le corps ne recalcule pas le palier requis : cette exception est levée sur le chemin de
    **chaque** écriture du plan, et y ajouter une résolution de droits coûterait trois requêtes par
    déplacement de meuble. Le frontend lit le détail sur la route d'abonnement.
    """

    status_code = status.HTTP_403_FORBIDDEN

    def __init__(self, project_id: int) -> None:
        detail = (
            "Ce chantier est en lecture seule : il dépasse le nombre de chantiers actifs de "
            "votre palier. Il n'est pas supprimé et redevient modifiable dès que le palier le "
            "permet."
        )
        super().__init__(
            detail,
            {"detail": detail, "code": "project_archived", "project_id": project_id},
        )


async def paywall_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Traduit un refus commercial en réponse conforme au contrat."""
    assert isinstance(exc, PaywallError)
    return exc.to_response()


class RequireFeature:
    """Exige une fonctionnalité du palier, et ouvre l'essai si c'est le premier geste monétisé.

    L'essai de 14 jours démarre **ici** et pas à l'inscription : un essai qui démarre à
    l'inscription est consommé par quelqu'un qui n'a pas encore compris le produit
    (`docs/strategie-produit.md` §4). Le premier artisan qui demande son premier devis obtient donc
    son devis, et l'essai part au même instant — sans carte, et sans écran intermédiaire.
    """

    def __init__(self, feature: str, *, starts_trial: bool = True) -> None:
        self.feature = feature
        self.starts_trial = starts_trial

    async def __call__(self, session: AsyncSession, organization_id: int) -> Entitlement:
        entitlement = await resolve_entitlement(session, organization_id)
        if entitlement.has(self.feature):
            return entitlement

        if self.starts_trial and entitlement.trial_available:
            await start_trial(session, organization_id)
            entitlement = await resolve_entitlement(session, organization_id)
            if entitlement.has(self.feature):
                return entitlement

        required = required_plan_for_feature(self.feature, entitlement.plans)
        detail = f"Cette fonctionnalité demande un palier supérieur à « {entitlement.plan.name} »."
        raise FeatureRequired(
            detail,
            {
                "detail": detail,
                "code": "feature_required",
                "feature": self.feature,
                "current_plan": entitlement.plan.code,
                "required_plan": required,
            },
        )


class RequireQuota:
    """Exige qu'un plafond de la période ne soit pas atteint, essai compris.

    `current` est lu par un résolveur : `projects_active` est un **état** (on compte les chantiers
    non archivés), les autres métriques sont des cumuls lus dans `usage_counter`. Compter les
    créations de projet à la place dirait combien de chantiers ont été ouverts ce mois-ci, ce qui
    n'est pas la limite annoncée.
    """

    def __init__(
        self,
        metric: str,
        limit_key: str,
        *,
        starts_trial: bool = True,
        reassurance: str = "",
    ) -> None:
        self.metric = metric
        self.limit_key = limit_key
        self.starts_trial = starts_trial
        # Phrase ajoutée au refus, propre à la métrique. Elle vaut son paramètre : « vos chantiers
        # ne sont pas supprimés » est ce qui décide si un client se réabonne ou s'il croit avoir
        # perdu son travail, et cette classe ne peut pas la deviner.
        self.reassurance = reassurance

    async def __call__(
        self, session: AsyncSession, organization_id: int, *, needed: int = 1
    ) -> Entitlement:
        entitlement = await resolve_entitlement(session, organization_id)
        current = await self._current(session, entitlement)
        if self._fits(entitlement, current, needed):
            return entitlement

        if self.starts_trial and entitlement.trial_available:
            await start_trial(session, organization_id)
            entitlement = await resolve_entitlement(session, organization_id)
            if self._fits(entitlement, current, needed):
                return entitlement

        limit = entitlement.limit(self.limit_key) or 0
        required = required_plan_for_limit(self.limit_key, current + needed, entitlement.plans)
        detail = f"Le palier « {entitlement.plan.name} » s'arrête à {limit}."
        if self.reassurance:
            detail = f"{detail} {self.reassurance}"
        raise QuotaExceeded(
            detail,
            {
                "detail": detail,
                "code": "quota_exceeded",
                "metric": self.metric,
                "limit": limit,
                "current": current,
                "current_plan": entitlement.plan.code,
                "required_plan": required,
            },
        )

    def _fits(self, entitlement: Entitlement, current: int, needed: int) -> bool:
        limit = entitlement.limit(self.limit_key)
        return limit is None or current + needed <= limit

    async def _current(self, session: AsyncSession, entitlement: Entitlement) -> int:
        if self.metric == UsageMetric.PROJECTS_ACTIVE:
            return await active_project_count(session, entitlement.organization_id)
        return await counter_value(
            session,
            organization_id=entitlement.organization_id,
            metric=self.metric,
            period_start=entitlement.period_start,
        )
