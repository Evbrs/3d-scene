"""Cycle de vie d'un compte : mot de passe, portabilité RGPD, effacement.

Trois besoins que le produit n'avait pas et qui bloquaient sa mise en vente :

1. **Reprendre la main sur un compte.** Aucune route de réinitialisation n'existait, SQLAdmin
   exclut le mot de passe de son formulaire et la CLI ne l'expose pas : un mot de passe oublié
   signifiait le compte et tous les chantiers perdus définitivement.
2. **Le droit d'accès et de portabilité** (RGPD art. 15 et 20). Seul l'effacement était traité.
   Tout le modèle est déjà sérialisable : l'export est un parcours, pas une fonctionnalité.
3. **L'effacement sans dégât collatéral.** C'est l'objet de l'amendement A13, et il vise deux
   destructions que le code faisait en silence, en répondant 204.

Les jetons de réinitialisation suivent exactement la règle des invitations
(`app/models/organization.py`) : **seul le hachage est en base**, et une ligne consommée est
conservée pour interdire le rejeu.

Deux règles gouvernent la fermeture, et aucune n'est un choix de confort :

- **Un chantier ne part jamais avec la personne qui l'a créé.** `project.owner_id` portait un
  `ON DELETE CASCADE` : dès qu'une entreprise comptait un second propriétaire, ou dès que le
  partant était un simple `editor`, fermer son compte détruisait tous les chantiers qu'il avait
  ouverts — ceux que ses collègues éditaient tous les jours. La colonne est passée en `SET NULL` :
  elle n'est qu'une trace de création (A1), et une trace ne détruit rien.
- **Un document émis survit à son émetteur.** Dix ans (art. L. 123-22 du code de commerce). Quand
  l'organisation à effacer en porte un, elle reste et c'est le **compte** qui est pseudonymisé :
  la donnée personnelle disparaît, la pièce comptable reste.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from app.models.base import utcnow
from app.models.billing import PriceBook, PriceItem, Quote, QuoteLine
from app.models.organization import Membership, Organization, OrganizationRole
from app.models.plan import Element, Face, Project, Room, SharedView
from app.models.user import User, UserToken, UserTokenPurpose

# 32 octets encodés en base64 URL-safe, soit 256 bits : le jeton n'est pas devinable, ce qui rend
# inutile toute limitation de débit sur son échange (même choix que l'invitation).
RESET_TOKEN_BYTES = 32

# Une heure. Assez pour aller chercher le message et revenir, assez peu pour qu'un lien oublié
# dans une boîte mail partagée cesse rapidement d'ouvrir le compte.
RESET_TOKEN_TTL = timedelta(hours=1)

# Adresse donnée au compte pseudonymisé. Le domaine `.invalid` est réservé par la RFC 2606 : il ne
# se résout nulle part, donc aucun message ne peut partir vers lui par mégarde. L'identifiant y
# figure pour que l'unicité de la colonne tienne, et parce qu'un compte fermé doit rester
# distinguable d'un autre dans une trace comptable — c'est ce qui fait une pseudonymisation et non
# une anonymisation.
PSEUDONYMIZED_EMAIL = "compte-supprime-{user_id}@supprime.invalid"

# Hachage volontairement illisible. `verify_password` ne lève jamais et rend `False` sur un hachage
# qu'il ne sait pas relire (`app/core/security.py`) : aucun mot de passe ne rouvre donc le compte,
# même si quelqu'un remettait `is_active` à vrai depuis le back-office.
UNUSABLE_PASSWORD_HASH = "!compte-supprime"


def hash_account_token(token: str) -> str:
    """Hachage d'un jeton de compte.

    SHA-256 et non Argon2id, contrairement aux mots de passe : ce jeton est un secret aléatoire de
    256 bits, il n'y a aucun dictionnaire à lui opposer. Un hachage lent n'apporterait rien et
    interdirait la recherche par index, seule façon de retrouver la ligne.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _as_utc(moment: datetime) -> datetime:
    """Relit un horodatage comme UTC quand il revient naïf de la base.

    Même correctif que sur les invitations : la colonne est déclarée `timezone=True`, mais SQLite
    ne stocke aucun fuseau et la comparaison avec `utcnow()` lèverait « can't compare
    offset-naive and offset-aware datetimes ».
    """
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def revoke_all_sessions(user: User) -> None:
    """Invalide d'un coup tous les jetons déjà émis pour ce compte.

    Le compteur est recopié dans chaque jeton à l'émission et confronté au compte à chaque requête
    (`app/api/deps.py`). Sans cette incrémentation, changer son mot de passe après un vol de
    session laisserait le voleur à l'intérieur jusqu'à l'expiration naturelle du jeton dérobé.
    """
    user.token_version += 1


async def issue_password_reset(session: AsyncSession, user: User) -> str:
    """Émet un jeton de réinitialisation et rend sa forme **en clair**, une seule fois.

    Les jetons non consommés du même compte sont supprimés au passage : demander un nouveau lien
    doit périmer le précédent, sinon un lien intercepté reste utilisable après que l'utilisateur
    en a demandé un autre — ce qu'il fait précisément quand il a un doute.
    """
    await session.execute(
        delete(UserToken).where(
            col(UserToken.user_id) == user.id,
            col(UserToken.purpose) == UserTokenPurpose.PASSWORD_RESET,
            col(UserToken.consumed_at).is_(None),
        )
    )

    token = secrets.token_urlsafe(RESET_TOKEN_BYTES)
    session.add(
        UserToken(
            user_id=user.id or 0,
            purpose=UserTokenPurpose.PASSWORD_RESET,
            token_hash=hash_account_token(token),
            expires_at=utcnow() + RESET_TOKEN_TTL,
        )
    )
    return token


async def consume_password_reset(session: AsyncSession, token: str) -> User | None:
    """Marque un jeton comme utilisé et rend le compte visé, ou `None` s'il ne vaut rien.

    Un seul retour pour « inconnu », « expiré », « déjà utilisé » et « compte désactivé » : les
    distinguer dirait à qui présente un jeton au hasard s'il a existé, et pour quel compte.
    """
    row = (
        await session.execute(
            select(UserToken).where(col(UserToken.token_hash) == hash_account_token(token))
        )
    ).scalar_one_or_none()

    if (
        row is None
        or row.purpose is not UserTokenPurpose.PASSWORD_RESET
        or row.consumed_at is not None
        or _as_utc(row.expires_at) <= utcnow()
    ):
        return None

    user = (
        await session.execute(select(User).where(col(User.id) == row.user_id))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        return None

    row.consumed_at = utcnow()
    row.updated_at = row.consumed_at
    return user


# --- Portabilité (RGPD art. 15 et 20) ----------------------------------------------------------


def _rows(records: list[Any]) -> list[dict[str, Any]]:
    """Sérialise des lignes SQLModel en JSON, sans dépendre d'un schéma de lecture.

    `mode="json"` est indispensable : les horodatages et les `Decimal` du chemin de l'argent ne
    sont pas sérialisables tels quels, et un export qui échoue au dernier moment ne rend rien.
    """
    return [record.model_dump(mode="json") for record in records]


async def _scalars(session: AsyncSession, statement: Any) -> list[Any]:
    return list((await session.execute(statement)).scalars().all())


async def export_account(session: AsyncSession, user: User) -> dict[str, Any]:
    """Toutes les données rattachées au compte, sous une forme lisible et réutilisable.

    Le périmètre est celui des **organisations dont le compte est membre accepté** : c'est là que
    vivent ses chantiers, ses barèmes et ses devis. Il est délibérément identique à celui de
    l'API — un export qui rendrait davantage que ce que les routes autorisent serait une fuite
    déguisée en conformité, et c'est exactement ce que vérifie `tests/test_rgpd.py`.

    Ce qui n'y figure jamais : le hachage du mot de passe et les hachages de jetons. Ils ne
    concernent pas la personne, ils protègent son compte — les exporter reviendrait à mettre le
    verrou dans l'enveloppe.
    """
    organization_ids = list(
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

    memberships = await _scalars(
        session, select(Membership).where(col(Membership.user_id) == user.id)
    )
    organizations = (
        await _scalars(
            session,
            select(Organization)
            .where(col(Organization.id).in_(organization_ids))
            .order_by(col(Organization.id)),
        )
        if organization_ids
        else []
    )

    projects = (
        await _scalars(
            session,
            select(Project)
            .where(col(Project.organization_id).in_(organization_ids))
            .order_by(col(Project.id)),
        )
        if organization_ids
        else []
    )
    project_ids = [project.id for project in projects]

    rooms = (
        await _scalars(
            session,
            select(Room).where(col(Room.project_id).in_(project_ids)).order_by(col(Room.id)),
        )
        if project_ids
        else []
    )
    room_ids = [room.id for room in rooms]

    faces = (
        await _scalars(
            session, select(Face).where(col(Face.room_id).in_(room_ids)).order_by(col(Face.id))
        )
        if room_ids
        else []
    )
    face_ids = [face.id for face in faces]

    # Les deux ancrages de l'amendement A4 : un meuble libre n'est sous aucune face, et une
    # requête qui ne remonterait que par `face_id` l'oublierait — le même défaut que celui
    # trouvé dans le back-office à l'assemblage de la vague 3.
    elements = (
        await _scalars(
            session,
            select(Element)
            .where(col(Element.face_id).in_(face_ids) | col(Element.room_id).in_(room_ids))
            .order_by(col(Element.id)),
        )
        if (face_ids or room_ids)
        else []
    )

    shared_views = (
        await _scalars(
            session,
            select(SharedView)
            .where(col(SharedView.project_id).in_(project_ids))
            .order_by(col(SharedView.id)),
        )
        if project_ids
        else []
    )

    price_books = (
        await _scalars(
            session,
            select(PriceBook)
            .where(col(PriceBook.organization_id).in_(organization_ids))
            .order_by(col(PriceBook.id)),
        )
        if organization_ids
        else []
    )
    book_ids = [book.id for book in price_books]
    price_items = (
        await _scalars(
            session,
            select(PriceItem)
            .where(col(PriceItem.price_book_id).in_(book_ids))
            .order_by(col(PriceItem.id)),
        )
        if book_ids
        else []
    )

    quotes = (
        await _scalars(
            session,
            select(Quote)
            .where(col(Quote.organization_id).in_(organization_ids))
            .order_by(col(Quote.id)),
        )
        if organization_ids
        else []
    )
    quote_ids = [quote.id for quote in quotes]
    quote_lines = (
        await _scalars(
            session,
            select(QuoteLine)
            .where(col(QuoteLine.quote_id).in_(quote_ids))
            .order_by(col(QuoteLine.id)),
        )
        if quote_ids
        else []
    )

    return {
        "format": "renovation-plan/export-compte",
        "version": 1,
        "generated_at": utcnow().isoformat(),
        "notice": (
            "Export réalisé au titre des articles 15 et 20 du RGPD. Il contient les données du "
            "compte et celles des organisations dont il est membre accepté. Le hachage du mot de "
            "passe et les jetons ne sont jamais exportés."
        ),
        "compte": {
            "id": user.id,
            "email": user.email,
            "is_active": user.is_active,
            "is_superuser": user.is_superuser,
            "email_verified_at": (
                user.email_verified_at.isoformat() if user.email_verified_at else None
            ),
            "created_at": user.created_at.isoformat(),
            "updated_at": user.updated_at.isoformat(),
        },
        "appartenances": _rows(memberships),
        "organisations": _rows(organizations),
        "projets": _rows(projects),
        "pieces": _rows(rooms),
        "faces": _rows(faces),
        "elements": _rows(elements),
        "vues_partagees": _rows(shared_views),
        "baremes": _rows(price_books),
        "lignes_de_bareme": _rows(price_items),
        "devis": _rows(quotes),
        "lignes_de_devis": _rows(quote_lines),
    }


# --- Effacement (RGPD art. 17) -----------------------------------------------------------------


async def organizations_blocking_deletion(session: AsyncSession, user: User) -> list[str]:
    """Organisations que l'effacement de ce compte laisserait sans propriétaire.

    Même règle que le retrait d'un membre (`app/api/organizations.py`) : une entreprise sans
    propriétaire accepté n'a plus personne pour inviter, payer ou fermer le compte, et seule une
    intervention en base la débloquerait. Le refus nomme les organisations concernées, pour que
    l'utilisateur sache quoi transmettre avant de recommencer.

    Ce garde-fou est **de gouvernance et non de sauvegarde** depuis l'amendement A13 : ce qui
    détruisait les chantiers des collègues n'était pas le rôle du partant mais le
    `ON DELETE CASCADE` de `project.owner_id`, qui frappait aussi un simple `editor` — que cette
    fonction ne regarde même pas. La cascade est devenue `SET NULL` ; il ne reste donc ici que la
    question du pilotage de l'entreprise.

    Une organisation dont il est le **seul** membre ne bloque rien : il n'y a personne à laisser
    derrière, et son sort est réglé par `delete_account`.
    """
    mine = await _scalars(
        session,
        select(Membership).where(
            col(Membership.user_id) == user.id,
            col(Membership.accepted_at).is_not(None),
            col(Membership.role) == OrganizationRole.OWNER,
        ),
    )
    if not mine:
        return []

    blocking: list[int] = []
    for membership in mine:
        others = await _scalars(
            session,
            select(Membership).where(
                col(Membership.organization_id) == membership.organization_id,
                col(Membership.user_id) != user.id,
                col(Membership.accepted_at).is_not(None),
            ),
        )
        if not others:
            continue
        if not any(other.role is OrganizationRole.OWNER for other in others):
            blocking.append(membership.organization_id)

    if not blocking:
        return []
    names = await _scalars(
        session, select(Organization).where(col(Organization.id).in_(blocking))
    )
    return sorted(organization.name for organization in names)


async def organizations_with_issued_documents(
    session: AsyncSession, organization_ids: list[int]
) -> set[int]:
    """Celles de ces organisations qui portent au moins un document **émis**.

    « Émis » et non « établi » : l'obligation comptable de dix ans (art. L. 123-22 du code de
    commerce) ne porte que sur ce qui est parti chez le client. Un brouillon n'a ni numéro ni date
    d'émission, il n'entre donc pas dans le décompte et rien n'empêche de l'effacer avec le compte.

    Les deux colonnes sont interrogées et non la seule `issued_at` : un document peut être devenu
    facture par un chemin qui ne repasserait pas par l'émission, et une facture sans devis émis
    reste une facture.
    """
    if not organization_ids:
        return set()

    return set(
        await _scalars(
            session,
            select(Quote.organization_id)
            .where(
                col(Quote.organization_id).in_(organization_ids),
                col(Quote.issued_at).is_not(None) | col(Quote.invoice_number).is_not(None),
            )
            .distinct(),
        )
    )


async def pseudonymize_account(session: AsyncSession, user: User, *, keep: set[int]) -> None:
    """Vide le compte de toute donnée personnelle **sans** supprimer la ligne.

    C'est le compromis de l'amendement A13 entre l'article 17 et l'obligation comptable : les
    données personnelles disparaissent — l'adresse e-mail est la seule que porte `user` — et le
    document comptable survit avec l'organisation qui l'a émis.

    Le compte quitte toutes ses organisations **sauf** celles de `keep`, qui portent un document
    émis : y laisser son appartenance leur évite de devenir des organisations sans aucun membre,
    c'est-à-dire des résidus qu'aucune route ne peut plus atteindre.

    Les sessions sont fermées et le mot de passe rendu illisible : rouvrir `is_active` depuis le
    back-office ne doit pas suffire à ressusciter un compte fermé.
    """
    await session.execute(
        delete(Membership).where(
            col(Membership.user_id) == user.id,
            col(Membership.organization_id).not_in(sorted(keep)),
        )
    )
    await session.execute(delete(UserToken).where(col(UserToken.user_id) == user.id))

    user.email = PSEUDONYMIZED_EMAIL.format(user_id=user.id or 0)
    user.hashed_password = UNUSABLE_PASSWORD_HASH
    user.is_active = False
    user.is_superuser = False
    user.email_verified_at = None
    revoke_all_sessions(user)
    user.updated_at = utcnow()


async def delete_account(session: AsyncSession, user: User) -> None:
    """Efface le compte et ce qui ne survit pas à son départ.

    Les organisations dont il était le seul membre sont supprimées **explicitement** : la cascade
    de `membership` les laisserait sinon en place, sans aucun membre, donc invisibles de l'API et
    impossibles à effacer autrement qu'en base. Une organisation sans personne n'est pas une
    entreprise, c'est un résidu.

    **Sauf** si elle porte un document émis (amendement A13). `quote.organization_id` est en
    `ON DELETE CASCADE` : supprimer l'organisation emportait les devis émis et les factures, que
    la loi impose de conserver dix ans. Ce n'est pas nous qui aurions été redressés, c'est
    l'artisan. Dans ce cas l'organisation reste, et c'est le **compte** qui est pseudonymisé au
    lieu d'être supprimé — la donnée personnelle disparaît, la pièce comptable reste.

    Les chantiers, eux, ne partent plus avec le compte : `project.owner_id` est passé en
    `SET NULL` (A13). Il n'était qu'une trace de création (A1), et une trace ne détruit rien.
    """
    solo: list[int] = []
    for membership in await _scalars(
        session, select(Membership).where(col(Membership.user_id) == user.id)
    ):
        others = await _scalars(
            session,
            select(Membership.id).where(
                col(Membership.organization_id) == membership.organization_id,
                col(Membership.user_id) != user.id,
            ),
        )
        if not others:
            solo.append(membership.organization_id)

    kept = await organizations_with_issued_documents(session, solo)
    removable = [organization_id for organization_id in solo if organization_id not in kept]

    if kept:
        await pseudonymize_account(session, user, keep=kept)
    else:
        await session.delete(user)
    await session.flush()

    if removable:
        await session.execute(delete(Organization).where(col(Organization.id).in_(removable)))
