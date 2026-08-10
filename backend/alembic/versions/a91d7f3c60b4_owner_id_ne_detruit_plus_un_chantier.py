"""owner_id ne detruit plus un chantier

Revision ID: a91d7f3c60b4
Revises: f3d47c8a1b56
Create Date: 2026-08-08 16:20:41.507213

Amendement A13 de `docs/spec-complete.md` §10, volet « chantiers ».

`project.owner_id` portait un `ON DELETE CASCADE` depuis la révision `c2a9d1f47d84`, écrite à une
époque où un projet appartenait bel et bien à une personne. La vague 2 a déplacé les droits sur
l'organisation (`A1`) et a laissé la cascade en place : la colonne n'autorisait plus rien mais
détruisait toujours tout. Fermer son compte emportait donc tous les chantiers **créés** par ce
compte, y compris ceux qu'une entreprise à plusieurs éditait tous les jours, et l'API répondait
204 sans un mot.

La colonne devient nullable et sa clé étrangère passe en `SET NULL` : « créé par un compte depuis
fermé » est un état normal, et c'est celui que décrit une trace de création.

Deux points à signaler.

1. Tout passe par `op.batch_alter_table`, comme les révisions précédentes : SQLite ne sait pas
   relâcher un `NOT NULL` ni remplacer une clé étrangère, le mode batch recrée la table. Sur
   PostgreSQL il retombe sur des `ALTER TABLE` ordinaires. La contrainte est **nommée** depuis sa
   création (`fk_project_owner_id_user`), ce qui rend le `drop_constraint` exécutable sur les deux
   moteurs.
2. Le `downgrade` refuse de s'exécuter s'il reste des chantiers sans créateur. Remettre la colonne
   en `NOT NULL` échouerait de toute façon, mais sur une violation de contrainte illisible ; pis,
   la seule façon de « réparer » automatiquement serait de rattacher ces chantiers à un compte
   choisi au hasard, c'est-à-dire d'écrire une trace de création fausse. La migration s'arrête
   avant d'avoir rien écrit et donne les identifiants.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a91d7f3c60b4"
down_revision: str | None = "f3d47c8a1b56"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FK_NAME = "fk_project_owner_id_user"


def upgrade() -> None:
    with op.batch_alter_table("project") as batch_op:
        batch_op.drop_constraint(FK_NAME, type_="foreignkey")
        batch_op.alter_column("owner_id", existing_type=sa.Integer(), nullable=True)
        batch_op.create_foreign_key(
            FK_NAME, "user", ["owner_id"], ["id"], ondelete="SET NULL"
        )


def _refuse_to_invent_a_creator(connection: sa.Connection) -> None:
    """Arrête le retour arrière plutôt que d'attribuer un chantier à quelqu'un qui ne l'a pas créé.

    `owner_id` redevient `NOT NULL` : les chantiers dont le créateur a fermé son compte n'ont
    aucune valeur à y mettre. Leur en inventer une écrirait une trace d'audit fausse, et la laisser
    nulle ferait échouer la migration au milieu, sur un message de contrainte que personne ne relie
    à sa cause.
    """
    identifiers = (
        connection.execute(
            sa.text("SELECT id FROM project WHERE owner_id IS NULL ORDER BY id LIMIT 20")
        )
        .scalars()
        .all()
    )
    if identifiers:
        raise RuntimeError(
            "Des chantiers ont été créés par un compte depuis fermé : ce retour arrière exigerait "
            "de leur inventer un créateur. Rien n'a été écrit ; rattachez-les explicitement à un "
            f"compte, puis relancez. id : {list(identifiers)}"
        )


def downgrade() -> None:
    _refuse_to_invent_a_creator(op.get_bind())

    with op.batch_alter_table("project") as batch_op:
        batch_op.drop_constraint(FK_NAME, type_="foreignkey")
        batch_op.alter_column("owner_id", existing_type=sa.Integer(), nullable=False)
        batch_op.create_foreign_key(
            FK_NAME, "user", ["owner_id"], ["id"], ondelete="CASCADE"
        )
