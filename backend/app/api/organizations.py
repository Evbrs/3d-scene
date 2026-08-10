"""API du multi-locataire : organisations, membres, invitations.

`docs/strategie-produit.md` §6, point 1. Ces routes sont le préalable de toute la monétisation :
un abonnement se facture à une entreprise qui a des sièges, pas à un compte.

Trois précautions structurent le module :

1. **Le secret d'invitation ne transite qu'une fois.** Le jeton est renvoyé dans la réponse qui
   crée l'invitation, et seul son hachage est écrit. Personne — pas même un administrateur du
   service — ne peut le relire ensuite.
2. **On ne peut pas se retirer le sol sous les pieds.** Le dernier `owner` d'une organisation ne
   peut ni se retirer, ni être rétrogradé : une organisation sans propriétaire n'a plus personne
   pour inviter, payer ou fermer le compte, et seule une intervention en base la débloquerait.
3. **On ne délègue pas plus haut que soi.** Un `admin` ne fabrique pas d'`owner` et ne touche pas
   à un `owner` : sans cette règle, le rôle d'administrateur serait équivalent à celui de
   propriétaire, en une requête.

Le travail à plusieurs est enfin **payant**, et il l'est depuis l'amendement A14. « Plusieurs
utilisateurs, rôles et invitations » est la ligne qui distingue le palier Entreprise de tous les
autres dans `docs/strategie-produit.md` §4, et le nombre de sièges y est annoncé palier par palier.
Rien ne les appliquait : un compte gratuit invitait autant de monde qu'il voulait. Les deux gardes
sont sur l'invitation, seul endroit d'où un second siège peut naître.
"""

import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Response, status
from sqlmodel import col, select

from app.api.auth import normalize_email
from app.api.deps import METRIC_SEATS, CurrentUser, RequireFeature, RequireQuota, SessionDep
from app.api.permissions import (
    accessible_organization_ids,
    find_membership,
    get_organization,
    require_membership,
)
from app.models.base import utcnow
from app.models.organization import (
    ROLE_RANK,
    Invitation,
    Membership,
    Organization,
    OrganizationRole,
)
from app.models.user import User
from app.schemas.organization import (
    InvitationAccept,
    InvitationCreate,
    InvitationCreated,
    InvitationRead,
    MemberRead,
    MemberRoleUpdate,
    OrganizationCreate,
    OrganizationRead,
    OrganizationUpdate,
)
from app.services.seed_plans import FEATURE_MULTI_SEAT, LIMIT_SEATS

router = APIRouter(prefix="/api", tags=["organisations"])

# Les deux gardes du travail à plusieurs, dans cet ordre et pas dans l'autre. La fonctionnalité
# d'abord : un palier qui n'ouvre pas le multi-utilisateur doit s'entendre dire « il vous faut
# Entreprise » (402) et non « vous avez atteint votre plafond de 1 siège » (429), qui laisserait
# croire qu'un siège de plus se rachète à l'unité. Le plafond ensuite, pour l'entreprise qui a bien
# le droit d'inviter mais qui a rempli ses quinze places.
REQUIRE_MULTI_SEAT = RequireFeature(FEATURE_MULTI_SEAT)
REQUIRE_SEAT_QUOTA = RequireQuota(
    METRIC_SEATS,
    LIMIT_SEATS,
    reassurance="Les sièges supplémentaires se facturent à l'unité sur le palier Entreprise.",
)

# 32 octets encodés en URL-safe base64, soit 256 bits : le jeton d'invitation n'est jamais
# devinable, ce qui rend inutile toute limitation de débit sur son échange.
INVITATION_TOKEN_BYTES = 32

# Réponse volontairement identique pour un jeton inconnu, expiré, déjà utilisé ou adressé à
# quelqu'un d'autre : les distinguer permettrait de confirmer qu'une invitation a existé, et pour
# quelle adresse.
_INVALID_INVITATION = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND, detail="Invitation introuvable ou expirée"
)

_UNSLUGGABLE = re.compile(r"[^a-z0-9]+")
# Repli quand le nom ne contient aucun caractère latin (« 建築 », emoji seuls…) : le slug reste
# obligatoire et unique, il n'a pas à être lisible dans toutes les écritures du monde.
DEFAULT_SLUG_BASE = "espace"
SLUG_MAX_LENGTH = 100


def slugify(value: str) -> str:
    """Forme canonique d'un slug : minuscules, chiffres et tirets simples."""
    reduced = _UNSLUGGABLE.sub("-", value.strip().lower()).strip("-")
    return reduced[:SLUG_MAX_LENGTH].strip("-") or DEFAULT_SLUG_BASE


def _as_utc(moment: datetime) -> datetime:
    """Relit un horodatage comme UTC quand il revient naïf de la base.

    Même correctif que sur les vues partagées (`app/api/share.py`) : la colonne est déclarée
    `timezone=True`, mais SQLite ne stocke aucun fuseau et la comparaison avec `utcnow()` lèverait
    « can't compare offset-naive and offset-aware datetimes ».
    """
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def hash_invitation_token(token: str) -> str:
    """Hachage du jeton d'invitation.

    SHA-256 et non Argon2id, contrairement aux mots de passe : ce jeton est un secret aléatoire de
    256 bits, il n'y a aucun dictionnaire à lui opposer. Un hachage lent n'apporterait rien et
    interdirait la recherche par index, qui est la seule façon de retrouver l'invitation.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def _slug_is_taken(session: SessionDep, slug: str) -> bool:
    return (
        await session.execute(select(Organization.id).where(col(Organization.slug) == slug))
    ).scalar_one_or_none() is not None


async def _available_slug(session: SessionDep, wanted: str) -> str:
    """Slug libre dérivé de `wanted`.

    Un suffixe aléatoire plutôt qu'un compteur incrémental : `-2`, `-3`… demande de relire la
    table à chaque tentative et se course avec lui-même dès deux inscriptions simultanées. Trois
    tentatives sur 2^24 valeurs : la dernière ligne de repli est la contrainte d'unicité en base,
    qui refuse le doublon plutôt que de le laisser passer.
    """
    if not await _slug_is_taken(session, wanted):
        return wanted
    base = wanted[: SLUG_MAX_LENGTH - 7]
    for _ in range(3):
        candidate = f"{base}-{secrets.token_hex(3)}"
        if not await _slug_is_taken(session, candidate):
            return candidate
    return f"{base}-{secrets.token_hex(3)}"


def _personal_identity(user: User) -> tuple[str, str]:
    """Nom et slug de l'organisation personnelle d'un compte.

    Le slug se termine par l'identifiant du compte : il est donc unique **par construction**, sans
    aucune relecture de la table. La migration de rétro-remplissage applique exactement la même
    règle, pour que les organisations créées avant et après elle se ressemblent.
    """
    local_part = (user.email or "").split("@", 1)[0]
    base = slugify(local_part)
    return base.replace("-", " ").title() or DEFAULT_SLUG_BASE, f"{base}-{user.id or 0}"


async def create_personal_organization(session: SessionDep, user: User) -> Organization:
    """Crée l'organisation personnelle d'un compte, et l'y installe comme `owner`.

    Appelée paresseusement à la première écriture qui en a besoin (voir
    `permissions.default_organization_id`) : c'est le seul point qui couvre à la fois
    l'inscription, la CLI et le back-office.
    """
    name, slug = _personal_identity(user)
    organization = Organization(name=name, slug=slug)
    session.add(organization)
    await session.flush()

    now = utcnow()
    session.add(
        Membership(
            user_id=user.id or 0,
            organization_id=organization.id or 0,
            role=OrganizationRole.OWNER,
            invited_at=now,
            accepted_at=now,
        )
    )
    await session.flush()
    return organization


async def _accepted_owner_count(session: SessionDep, organization_id: int) -> int:
    return len(
        (
            await session.execute(
                select(Membership.id).where(
                    col(Membership.organization_id) == organization_id,
                    col(Membership.role) == OrganizationRole.OWNER,
                    col(Membership.accepted_at).is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )


def _refuse_delegation_above(actor: Membership, target_role: OrganizationRole) -> None:
    """Interdit d'agir sur — ou d'accorder — un rôle supérieur au sien.

    Sans cette règle, un `admin` se promeut `owner` en une requête, et la distinction entre les
    deux rôles n'existe plus.
    """
    if ROLE_RANK[actor.role] < ROLE_RANK[target_role]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Votre rôle ne permet pas d'agir sur le rôle « {target_role.value} »",
        )


async def _refuse_losing_the_last_owner(
    session: SessionDep, organization_id: int, target: Membership
) -> None:
    if target.role is not OrganizationRole.OWNER or target.accepted_at is None:
        return
    if await _accepted_owner_count(session, organization_id) <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Cette organisation n'a qu'un seul propriétaire : nommez-en un autre avant de "
                "retirer celui-ci."
            ),
        )


# --- Organisations ----------------------------------------------------------------------------


@router.post(
    "/organizations", response_model=OrganizationRead, status_code=status.HTTP_201_CREATED
)
async def create_organization(
    payload: OrganizationCreate, session: SessionDep, current_user: CurrentUser
) -> Organization:
    """Crée une organisation. Son créateur en devient propriétaire, sans invitation."""
    wanted = payload.slug or slugify(payload.name)
    organization = Organization(name=payload.name, slug=await _available_slug(session, wanted))
    session.add(organization)
    await session.flush()

    now = utcnow()
    session.add(
        Membership(
            user_id=current_user.id or 0,
            organization_id=organization.id or 0,
            role=OrganizationRole.OWNER,
            invited_at=now,
            accepted_at=now,
        )
    )
    await session.commit()
    await session.refresh(organization)
    return organization


@router.get("/organizations", response_model=list[OrganizationRead])
async def list_organizations(
    session: SessionDep, current_user: CurrentUser
) -> list[Organization]:
    """Organisations dont le compte est membre accepté. Rien d'autre n'est visible."""
    organization_ids = await accessible_organization_ids(session, current_user)
    if not organization_ids:
        return []
    return list(
        (
            await session.execute(
                select(Organization)
                .where(col(Organization.id).in_(organization_ids))
                .order_by(col(Organization.created_at), col(Organization.id))
            )
        )
        .scalars()
        .all()
    )


@router.get("/organizations/{organization_id}", response_model=OrganizationRead)
async def read_organization(
    organization_id: int, session: SessionDep, current_user: CurrentUser
) -> Organization:
    return await get_organization(
        session, organization_id, current_user, OrganizationRole.VIEWER
    )


@router.patch("/organizations/{organization_id}", response_model=OrganizationRead)
async def update_organization(
    organization_id: int,
    payload: OrganizationUpdate,
    session: SessionDep,
    current_user: CurrentUser,
) -> Organization:
    """Met à jour l'identité de l'entreprise.

    Réservé aux `admin` : ces champs sont imprimés sur les devis et les factures. Un champ absent
    n'est pas touché, un champ à `null` est effacé (voir `app/schemas/organization.py`).
    """
    organization = await get_organization(
        session, organization_id, current_user, OrganizationRole.ADMIN
    )
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(organization, field, value)
    organization.updated_at = utcnow()
    await session.commit()
    await session.refresh(organization)
    return organization


# --- Membres ----------------------------------------------------------------------------------


@router.get("/organizations/{organization_id}/members", response_model=list[MemberRead])
async def list_members(
    organization_id: int, session: SessionDep, current_user: CurrentUser
) -> list[MemberRead]:
    """Membres de l'organisation, invitations en attente comprises.

    Une jointure et non deux requêtes : la liste affiche l'adresse de chaque membre, et la lire
    compte par compte serait un N+1 sur une page ouverte à chaque gestion d'équipe.
    """
    await require_membership(session, organization_id, current_user, OrganizationRole.VIEWER)
    rows = (
        await session.execute(
            select(Membership, User.email)
            .join(User, col(User.id) == col(Membership.user_id))
            .where(col(Membership.organization_id) == organization_id)
            .order_by(col(Membership.id))
        )
    ).all()
    return [
        MemberRead(
            user_id=membership.user_id,
            email=email,
            role=membership.role,
            invited_at=membership.invited_at,
            accepted_at=membership.accepted_at,
        )
        for membership, email in rows
    ]


async def _load_member(
    session: SessionDep, organization_id: int, user_id: int
) -> Membership:
    membership = (
        await session.execute(
            select(Membership).where(
                col(Membership.organization_id) == organization_id,
                col(Membership.user_id) == user_id,
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membre introuvable")
    return membership


@router.patch(
    "/organizations/{organization_id}/members/{user_id}", response_model=MemberRead
)
async def update_member_role(
    organization_id: int,
    user_id: int,
    payload: MemberRoleUpdate,
    session: SessionDep,
    current_user: CurrentUser,
) -> MemberRead:
    actor = await require_membership(
        session, organization_id, current_user, OrganizationRole.ADMIN
    )
    target = await _load_member(session, organization_id, user_id)

    _refuse_delegation_above(actor, target.role)
    _refuse_delegation_above(actor, payload.role)
    if payload.role is not OrganizationRole.OWNER:
        await _refuse_losing_the_last_owner(session, organization_id, target)

    target.role = payload.role
    target.updated_at = utcnow()
    await session.commit()
    await session.refresh(target)

    email = (
        await session.execute(select(User.email).where(col(User.id) == user_id))
    ).scalar_one()
    return MemberRead(
        user_id=target.user_id,
        email=email,
        role=target.role,
        invited_at=target.invited_at,
        accepted_at=target.accepted_at,
    )


@router.delete(
    "/organizations/{organization_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_member(
    organization_id: int, user_id: int, session: SessionDep, current_user: CurrentUser
) -> Response:
    """Retire un membre — ou soi-même.

    Quitter une organisation ne demande aucun rôle particulier : c'est le pendant du droit de
    partir. Retirer *quelqu'un d'autre* demande `admin`, et jamais au-dessus de son propre rôle.
    """
    leaving_alone = user_id == current_user.id
    minimum = OrganizationRole.VIEWER if leaving_alone else OrganizationRole.ADMIN
    actor = await require_membership(session, organization_id, current_user, minimum)

    target = await _load_member(session, organization_id, user_id)
    if not leaving_alone:
        _refuse_delegation_above(actor, target.role)
    await _refuse_losing_the_last_owner(session, organization_id, target)

    await session.delete(target)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Invitations ------------------------------------------------------------------------------


@router.post(
    "/organizations/{organization_id}/invitations",
    response_model=InvitationCreated,
    status_code=status.HTTP_201_CREATED,
)
async def invite_member(
    organization_id: int,
    payload: InvitationCreate,
    session: SessionDep,
    current_user: CurrentUser,
) -> InvitationCreated:
    """Invite une adresse e-mail à rejoindre l'organisation.

    Le jeton en clair n'apparaît que dans cette réponse : c'est à l'appelant de l'acheminer. La
    base n'en garde que le hachage, donc le relire est impossible — une invitation perdue se
    réémet, elle ne se retrouve pas.

    C'est ici que le travail à plusieurs se paie (spec §10, amendement A14). Deux refus distincts et
    non un seul : 402 quand le palier n'ouvre pas le multi-utilisateur, 429 quand il l'ouvre mais
    que les sièges sont pris. Les confondre ferait proposer un changement de palier à une entreprise
    qui n'a besoin que d'un siège de plus.

    Le plafond se compte sur les appartenances **acceptées** : une invitation en attente n'ouvre
    aucun accès, et la faire compter laisserait une invitation oubliée bloquer une embauche.
    """
    actor = await require_membership(
        session, organization_id, current_user, OrganizationRole.ADMIN
    )
    _refuse_delegation_above(actor, payload.role)
    await REQUIRE_MULTI_SEAT(session, organization_id)
    await REQUIRE_SEAT_QUOTA(session, organization_id)

    token = secrets.token_urlsafe(INVITATION_TOKEN_BYTES)
    invitation = Invitation(
        organization_id=organization_id,
        email=normalize_email(payload.email),
        role=payload.role,
        token_hash=hash_invitation_token(token),
        expires_at=utcnow() + timedelta(days=payload.expires_in_days),
    )
    session.add(invitation)
    await session.commit()
    await session.refresh(invitation)

    return InvitationCreated(
        id=invitation.id or 0,
        organization_id=invitation.organization_id,
        email=invitation.email,
        role=invitation.role,
        expires_at=invitation.expires_at,
        accepted_at=invitation.accepted_at,
        token=token,
    )


@router.get(
    "/organizations/{organization_id}/invitations", response_model=list[InvitationRead]
)
async def list_invitations(
    organization_id: int, session: SessionDep, current_user: CurrentUser
) -> list[Invitation]:
    await require_membership(session, organization_id, current_user, OrganizationRole.ADMIN)
    return list(
        (
            await session.execute(
                select(Invitation)
                .where(col(Invitation.organization_id) == organization_id)
                .order_by(col(Invitation.id))
            )
        )
        .scalars()
        .all()
    )


@router.post("/invitations/accept", response_model=MemberRead)
async def accept_invitation(
    payload: InvitationAccept, session: SessionDep, current_user: CurrentUser
) -> MemberRead:
    """Accepte une invitation avec son jeton.

    L'adresse de l'invitation doit être celle du compte connecté. Le jeton seul ne suffit
    volontairement pas : un lien transféré, ou intercepté dans une boîte mail, ne doit pas ouvrir
    l'organisation à un compte qui n'était pas l'invité.
    """
    invitation = (
        await session.execute(
            select(Invitation).where(
                col(Invitation.token_hash) == hash_invitation_token(payload.token)
            )
        )
    ).scalar_one_or_none()

    if (
        invitation is None
        or invitation.accepted_at is not None
        or _as_utc(invitation.expires_at) <= utcnow()
        or invitation.email != normalize_email(current_user.email)
    ):
        raise _INVALID_INVITATION

    now = utcnow()
    membership = await find_membership(session, invitation.organization_id, current_user)
    if membership is None:
        # `find_membership` ne voit que les appartenances acceptées : une ligne en attente peut
        # exister sans qu'elle la renvoie, et la recréer violerait la contrainte d'unicité.
        membership = (
            await session.execute(
                select(Membership).where(
                    col(Membership.organization_id) == invitation.organization_id,
                    col(Membership.user_id) == current_user.id,
                )
            )
        ).scalar_one_or_none()

    if membership is None:
        membership = Membership(
            user_id=current_user.id or 0,
            organization_id=invitation.organization_id,
            role=invitation.role,
            invited_at=invitation.created_at,
            accepted_at=now,
        )
        session.add(membership)
    else:
        # Une invitation qui rétrograde un membre existant reste une rétrogradation : elle doit
        # buter sur la même règle que la route de changement de rôle. Sans ce contrôle, un `admin`
        # invitait le dernier `owner` en `viewer`, et l'organisation se retrouvait sans
        # propriétaire dès que celui-ci cliquait sur le lien.
        if invitation.role is not OrganizationRole.OWNER:
            await _refuse_losing_the_last_owner(session, invitation.organization_id, membership)
        membership.role = invitation.role
        membership.accepted_at = membership.accepted_at or now
        membership.updated_at = now

    invitation.accepted_at = now
    await session.commit()
    await session.refresh(membership)

    return MemberRead(
        user_id=membership.user_id,
        email=current_user.email,
        role=membership.role,
        invited_at=membership.invited_at,
        accepted_at=membership.accepted_at,
    )
