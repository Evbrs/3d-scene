"""Permissions objet.

Le principe : l'accès n'est jamais déduit d'un identifiant fourni par le client, il est toujours
revérifié contre la base. C'est ce qui empêche la référence directe d'objet non sécurisée
(OWASP A01 : *Broken Access Control*).

Ce qui autorise n'est **plus** `Project.owner_id` mais l'appartenance à l'organisation qui porte
le projet (`docs/strategie-produit.md` §6, point 1). `owner_id` reste la trace de qui a créé le
projet, et ne doit plus jamais entrer dans une décision d'accès : sinon le second membre d'une
entreprise ne voit pas les chantiers de son collègue, et le produit multi-sièges n'existe pas.

Deux codes de refus, et la distinction est délibérée :

- **404** quand l'utilisateur n'est pas membre de l'organisation propriétaire. Répondre 403
  confirmerait que l'objet existe, ce qui permet d'énumérer les identifiants des autres clients.
  C'est la règle historique de ce fichier, et elle est **conservée telle quelle**.
- **403** quand il est bien membre mais que son rôle ne suffit pas. Il sait déjà que l'objet
  existe — il peut le lire — donc un 404 ne cacherait rien et lui ferait croire à une
  disparition. La frontière du secret est l'organisation, pas le rôle.

Les fonctions gardent leur nom historique `get_owned_*` : elles sont appelées depuis quatre
modules de routes, et un renommage se paierait en conflits sans rien changer au comportement.
« Owned » y désigne désormais « accessible au titre de l'organisation », ce que la signature
`minimum: OrganizationRole` rend explicite au point d'appel.
"""

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import col, select

from app.models.organization import ROLE_RANK, Membership, Organization, OrganizationRole
from app.models.plan import Element, Face, Project, Room
from app.models.user import User

_NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ressource introuvable")


def _forbidden(minimum: OrganizationRole) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Cette action demande au moins le rôle « {minimum.value} » dans l'organisation",
    )


async def find_membership(
    session: AsyncSession, organization_id: int, user: User
) -> Membership | None:
    """Appartenance **acceptée** de `user` à une organisation, ou `None`.

    Le filtre sur `accepted_at` est la moitié de la sécurité de ce module : une invitation en
    attente est déjà une ligne de `membership`, et sans ce filtre elle donnerait accès à tout le
    contenu de l'organisation avant même que l'invité ait répondu.
    """
    return (
        await session.execute(
            select(Membership).where(
                col(Membership.organization_id) == organization_id,
                col(Membership.user_id) == user.id,
                col(Membership.accepted_at).is_not(None),
            )
        )
    ).scalar_one_or_none()


async def require_role(
    session: AsyncSession, project: Project, user: User, minimum: OrganizationRole
) -> Membership:
    """Point de passage unique de toute autorisation sur un objet du plan.

    Renvoie l'appartenance résolue — les appelants qui ont besoin du rôle effectif (affichage,
    journalisation) n'ont donc pas à la relire.
    """
    membership = await find_membership(session, project.organization_id, user)
    if membership is None:
        raise _NOT_FOUND
    if ROLE_RANK[membership.role] < ROLE_RANK[minimum]:
        raise _forbidden(minimum)
    return membership


async def require_membership(
    session: AsyncSession, organization_id: int, user: User, minimum: OrganizationRole
) -> Membership:
    """Même règle, mais sur une organisation directement (routes de `app/api/organizations.py`)."""
    membership = await find_membership(session, organization_id, user)
    if membership is None:
        raise _NOT_FOUND
    if ROLE_RANK[membership.role] < ROLE_RANK[minimum]:
        raise _forbidden(minimum)
    return membership


async def default_organization_id(session: AsyncSession, user: User) -> int:
    """Organisation dans laquelle `user` agit par défaut, créée si elle n'existe pas encore.

    Créée paresseusement plutôt qu'à l'inscription : un compte peut aussi naître de la CLI
    (`create-superuser`) ou du back-office, et un chemin de création de compte qui oublierait
    l'organisation rendrait le compte inutilisable sans le dire.

    Quand le compte appartient à plusieurs organisations, c'est la **plus ancienne appartenance
    acceptée** qui gagne. Un choix explicite par le client viendra avec la sélection
    d'organisation dans l'interface ; en attendant, une règle déterministe vaut mieux qu'un
    « la première que la base renvoie », qui change au gré des réécritures de lignes.
    """
    existing = (
        await session.execute(
            select(Membership.organization_id)
            .where(
                col(Membership.user_id) == user.id,
                col(Membership.accepted_at).is_not(None),
            )
            .order_by(col(Membership.accepted_at), col(Membership.id))
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    # Import local et non en tête de module : `app.api.organizations` importe ce module-ci pour
    # ses propres contrôles d'accès. Le cycle n'existe qu'au chargement, pas à l'exécution.
    from app.api.organizations import create_personal_organization

    organization = await create_personal_organization(session, user)
    return organization.id or 0


async def accessible_organization_ids(session: AsyncSession, user: User) -> list[int]:
    """Organisations dont `user` est membre accepté — le périmètre de toute liste."""
    return list(
        (
            await session.execute(
                select(Membership.organization_id).where(
                    col(Membership.user_id) == user.id,
                    col(Membership.accepted_at).is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )


async def get_organization(
    session: AsyncSession, organization_id: int, user: User, minimum: OrganizationRole
) -> Organization:
    """Charge une organisation en vérifiant l'appartenance de `user`."""
    await require_membership(session, organization_id, user, minimum)
    organization = (
        await session.execute(select(Organization).where(col(Organization.id) == organization_id))
    ).scalar_one_or_none()
    if organization is None:
        raise _NOT_FOUND
    return organization


async def get_owned_project(
    session: AsyncSession,
    project_id: int,
    user: User,
    minimum: OrganizationRole = OrganizationRole.VIEWER,
) -> Project:
    """Charge un projet en vérifiant que `user` a au moins `minimum` dans son organisation."""
    project = (
        await session.execute(select(Project).where(col(Project.id) == project_id))
    ).scalar_one_or_none()
    if project is None:
        raise _NOT_FOUND
    await require_role(session, project, user, minimum)
    return project


async def get_owned_room(
    session: AsyncSession,
    room_id: int,
    user: User,
    minimum: OrganizationRole = OrganizationRole.VIEWER,
) -> Room:
    """Charge une pièce en remontant jusqu'à l'organisation du projet."""
    room = (
        await session.execute(
            select(Room)
            .where(col(Room.id) == room_id)
            .options(selectinload(Room.project))  # type: ignore[arg-type]
        )
    ).scalar_one_or_none()
    if room is None:
        raise _NOT_FOUND
    await require_role(session, room.project, user, minimum)
    return room


async def get_owned_face(
    session: AsyncSession,
    face_id: int,
    user: User,
    minimum: OrganizationRole = OrganizationRole.VIEWER,
) -> Face:
    """Charge une face **avec ses éléments**.

    Les éléments sont nécessaires à la validation de placement : une nouvelle ouverture doit être
    confrontée à celles déjà posées sur la même face. Les charger ici, en une requête anticipée,
    évite de les découvrir au milieu d'une écriture — où le `SELECT` déclencherait un autoflush.
    """
    face = (
        await session.execute(
            select(Face)
            .where(col(Face.id) == face_id)
            .options(
                selectinload(Face.room).selectinload(Room.project),  # type: ignore[arg-type]
                selectinload(Face.elements),  # type: ignore[arg-type]
            )
        )
    ).scalar_one_or_none()
    if face is None:
        raise _NOT_FOUND
    await require_role(session, face.room.project, user, minimum)
    return face


async def get_owned_element(
    session: AsyncSession,
    element_id: int,
    user: User,
    minimum: OrganizationRole = OrganizationRole.VIEWER,
) -> Element:
    element = (
        await session.execute(
            select(Element)
            .where(col(Element.id) == element_id)
            .options(
                selectinload(Element.face)  # type: ignore[arg-type]
                .selectinload(Face.room)  # type: ignore[arg-type]
                .selectinload(Room.project),  # type: ignore[arg-type]
                # Même raison que dans `get_owned_face` : déplacer une ouverture doit pouvoir la
                # confronter aux autres ouvertures du même mur.
                selectinload(Element.face).selectinload(Face.elements),  # type: ignore[arg-type]
            )
        )
    ).scalar_one_or_none()
    if element is None:
        raise _NOT_FOUND
    await require_role(session, element.face.room.project, user, minimum)
    return element
