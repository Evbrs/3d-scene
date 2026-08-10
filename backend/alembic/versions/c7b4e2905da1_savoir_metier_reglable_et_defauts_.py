"""savoir metier reglable et defauts commerciaux de l'entreprise

Revision ID: c7b4e2905da1
Revises: a91d7f3c60b4
Create Date: 2026-08-08 17:05:12.884001

Amendement A14 de `docs/spec-complete.md` §10. Trois réglages qui étaient des constantes Python
deviennent des colonnes, et pour la même raison à chaque fois : ce sont des décisions
**commerciales ou métier**, prises par l'entreprise ou par un décret, et les faire vivre dans le
code obligeait à un déploiement pour chacune.

1. `organization.default_*` — les mentions commerciales obligatoires d'un devis de bâtiment
   (délai de paiement, durée de validité, pénalités de retard, indemnité de recouvrement,
   conditions de règlement, médiateur). `docs/strategie-produit.md` §2 le demandait en toutes
   lettres ; le produit les rendait paramétrables **par devis** et jamais par entreprise, si bien
   qu'un artisan qui accorde 45 jours à son donneur d'ordre devait les ressaisir à chaque
   document. Toutes nullables : `NULL` veut dire « prends le défaut du produit », et non zéro.

2. `organization.inspection_thresholds` — la surcharge des seuils du contrôle de conformité.
   L'amendement A12 refuse tout seuil venu du corps d'une requête en s'accordant une porte de
   sortie : « un réglage par organisation est une ligne SQL ». Il n'existait aucune colonne où
   écrire cette ligne. C'est celle-ci.

3. `plan_catalog.trial_days` — la durée de l'essai sans carte, jusqu'ici la constante `TRIAL_DAYS`.
   C'est le levier commercial le plus souvent tiré (« 30 jours pour la campagne de septembre ») et
   il n'avait aucune raison de coûter un déploiement, alors que tout le reste du palier — prix,
   plafonds, fonctionnalités — se règle déjà par `UPDATE`.

Deux points à signaler.

- Le `server_default` de `trial_days` est **0** et non 14 : zéro veut dire « ce palier n'offre
  aucun essai », ce qui est vrai de trois paliers sur quatre. Seul le palier d'essai
  (`seed_plans.TRIAL_PLAN_CODE`) reçoit 14, par un `UPDATE` ciblé qui ne touche que lui — un défaut
  à 14 aurait fait croire que le palier Réseau offre lui aussi deux semaines gratuites.
- Le `downgrade` ne restitue pas les valeurs perdues et ne le prétend pas : les colonnes sont
  supprimées, et un artisan qui avait réglé son délai à 45 jours repassera à 30. C'est acceptable
  parce que ces colonnes sont des **défauts de saisie** : aucun document déjà émis n'en dépend, ils
  portent tous leur propre copie (amendement A2). Les seuils d'inspection sont dans le même cas —
  le rapport republie ceux qu'il a appliqués, il ne les relit jamais après coup.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c7b4e2905da1"
down_revision: str | None = "a91d7f3c60b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Même type portable que partout ailleurs (`app/models/base.py::json_type`) : `JSONB` sur
# PostgreSQL, `JSON` textuel sur SQLite, moteur de la suite de tests.
JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

# Le palier qui porte l'essai, et sa durée initiale. Répétés ici plutôt qu'importés de
# `app/services/seed_plans.py` : une migration doit continuer de décrire ce qu'elle a fait le jour
# où le code change d'avis, sinon rejouer l'historique ne reproduit plus la base.
TRIAL_PLAN_CODE = "artisan"
TRIAL_DAYS = 14

# Les six clés de `plan_catalog.features` retirées de la grille : aucune n'avait d'implémentation.
# Elles sont effacées des paliers **semés**, jamais des paliers négociés à la main — voir le corps
# de `upgrade` pour la raison.
REMOVED_FEATURE_KEYS = (
    "white_label",
    "client_signature",
    "priced_variants",
    "sso",
    "agency_stats",
    "api",
)
SEEDED_PLAN_CODES = ("decouverte", "artisan", "entreprise", "reseau")

BASIS_POINTS = 10_000


def upgrade() -> None:
    with op.batch_alter_table("organization") as batch:
        batch.add_column(sa.Column("default_payment_days", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("default_validity_days", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("default_late_penalty_rate_bp", sa.Integer(), nullable=True)
        )
        batch.add_column(
            sa.Column("default_recovery_indemnity_cents", sa.Integer(), nullable=True)
        )
        batch.add_column(
            sa.Column("default_payment_terms", sa.String(length=500), nullable=True)
        )
        batch.add_column(
            sa.Column("default_mediator_name", sa.String(length=200), nullable=True)
        )
        batch.add_column(
            sa.Column("default_mediator_url", sa.String(length=500), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "inspection_thresholds",
                JSON_TYPE,
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        # Les bornes sont en base et pas seulement dans les schémas Pydantic : SQLAdmin, la CLI,
        # Celery et `psql` écrivent sans passer par l'API, et SQLModel désactive la validation
        # `Field(...)` sur les modèles `table=True` (leçon du lot L4).
        batch.create_check_constraint(
            "ck_organization_default_payment_days_not_negative",
            "default_payment_days IS NULL OR default_payment_days >= 0",
        )
        batch.create_check_constraint(
            "ck_organization_default_validity_days_not_negative",
            "default_validity_days IS NULL OR default_validity_days >= 0",
        )
        batch.create_check_constraint(
            "ck_organization_default_late_penalty_bounded",
            "default_late_penalty_rate_bp IS NULL OR (default_late_penalty_rate_bp >= 0 "
            f"AND default_late_penalty_rate_bp <= {BASIS_POINTS})",
        )
        batch.create_check_constraint(
            "ck_organization_default_recovery_indemnity_not_negative",
            "default_recovery_indemnity_cents IS NULL OR default_recovery_indemnity_cents >= 0",
        )

    with op.batch_alter_table("plan_catalog") as batch:
        batch.add_column(
            sa.Column(
                "trial_days", sa.Integer(), nullable=False, server_default=sa.text("0")
            )
        )
        batch.create_check_constraint(
            "ck_plan_catalog_trial_days_not_negative", "trial_days >= 0"
        )

    # La durée de l'essai est déplacée, pas inventée : le palier d'essai reprend exactement la
    # valeur que la constante `TRIAL_DAYS` appliquait jusqu'ici, et les autres paliers restent à 0.
    op.execute(
        sa.text("UPDATE plan_catalog SET trial_days = :days WHERE code = :code").bindparams(
            days=TRIAL_DAYS, code=TRIAL_PLAN_CODE
        )
    )

    # Les six fonctionnalités jamais construites disparaissent des paliers de la grille de
    # référence. Restreint à ces quatre codes **exprès** : un palier négocié (« Réseau Bretagne »)
    # a pu être créé à la main avec ses propres clés, et une migration n'a pas à décider de ce
    # qu'un commercial a écrit dans une négociation. Les paliers semés, eux, sont notre grille
    # publique, et c'est elle qui affichait « ✓ » en face d'un prix pour des fonctions inexistantes.
    for code in SEEDED_PLAN_CODES:
        for key in REMOVED_FEATURE_KEYS:
            _drop_feature_key(code, key)


def downgrade() -> None:
    with op.batch_alter_table("plan_catalog") as batch:
        batch.drop_constraint("ck_plan_catalog_trial_days_not_negative", type_="check")
        batch.drop_column("trial_days")

    with op.batch_alter_table("organization") as batch:
        batch.drop_constraint(
            "ck_organization_default_recovery_indemnity_not_negative", type_="check"
        )
        batch.drop_constraint("ck_organization_default_late_penalty_bounded", type_="check")
        batch.drop_constraint(
            "ck_organization_default_validity_days_not_negative", type_="check"
        )
        batch.drop_constraint(
            "ck_organization_default_payment_days_not_negative", type_="check"
        )
        batch.drop_column("inspection_thresholds")
        batch.drop_column("default_mediator_url")
        batch.drop_column("default_mediator_name")
        batch.drop_column("default_payment_terms")
        batch.drop_column("default_recovery_indemnity_cents")
        batch.drop_column("default_late_penalty_rate_bp")
        batch.drop_column("default_validity_days")
        batch.drop_column("default_payment_days")

    # Les six clés ne sont pas remises : le semis de `app/services/seed_plans.py` ne les connaît
    # plus, et les réécrire ferait réapparaître sur la page tarifs des fonctionnalités qui
    # n'existent toujours pas. Un `downgrade` remet un **schéma**, il n'a pas à ressusciter une
    # promesse commerciale que le code ne tient pas.


def _drop_feature_key(code: str, key: str) -> None:
    """Retire une clé du dictionnaire `features` d'un palier, sur les deux moteurs.

    Aucun `UPDATE ... - 'clé'` : cet opérateur est propre à `JSONB`, donc à PostgreSQL, et la suite
    de tests tourne sur SQLite. La lecture-modification-écriture qui suit est sûre ici parce qu'une
    migration s'exécute seule sur une base au repos — ce serait un défaut dans du code de requête,
    ce n'en est pas un dans une révision.
    """
    import json

    connection = op.get_bind()
    row = connection.execute(
        sa.text("SELECT features FROM plan_catalog WHERE code = :code").bindparams(code=code)
    ).scalar_one_or_none()
    if row is None:
        return

    features = json.loads(row) if isinstance(row, str) else dict(row)
    if key not in features:
        return

    del features[key]
    # Le paramètre est typé, et pas passé en chaîne déjà sérialisée : le pilote `psycopg` rend un
    # `CAST` d'après le type du bind, donc une chaîne produisait `SET features = $1::VARCHAR` et
    # PostgreSQL refusait l'affectation à une colonne `jsonb` (`DatatypeMismatch`). SQLite acceptait
    # la même écriture sans broncher, si bien que la suite de tests ne pouvait pas voir le défaut :
    # il n'apparaissait qu'au démarrage réel de la pile.
    connection.execute(
        sa.text("UPDATE plan_catalog SET features = :features WHERE code = :code").bindparams(
            sa.bindparam("features", value=features, type_=JSON_TYPE),
            sa.bindparam("code", value=code),
        )
    )
