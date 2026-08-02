# PLAN.md — Ticket P1 : Modèle de données

> Source : `docs/plan-generation-ia.md` §8 (ticket P1), `docs/spec-complete.md` §5 et §6.

## Objectif

Modèles SQLModel `Project`/`Room`/`Face`/`Element`/`FurnitureType`/`SharedView` + migration
Alembic initiale + configuration SQLAdmin.

## Référence spec

`docs/spec-complete.md` §5 (ajouts au modèle de données), §6 (ORM/migrations/admin),
§8 (géométrie stockée en JSON ; verrouillage optimiste par champ `version`).

## Fichiers autorisés

`backend/app/models/`, `backend/alembic/`, `backend/app/admin.py`,
`backend/tests/test_models.py`.

**Extension signalée** : `backend/app/db.py` (moteur + session) et `backend/alembic.ini` sont
nécessaires pour que les modèles et la migration existent — l'énoncé du ticket ne les liste pas
mais aucune migration n'est possible sans eux. `backend/app/main.py` et `backend/tests/conftest.py`
sont touchés au minimum pour monter l'admin et fournir une session de test.

## Non-objectifs

- Aucune route API (→ P3)
- Aucune logique d'auth (→ P2) — le modèle `User` et le champ `Project.owner_id` arrivent en P2
- Aucun seed du catalogue `FurnitureType` (→ P5)
- Aucun calcul géométrique (→ P6)

## Décisions

- **Géométrie en JSON** (`docs/spec-complete.md` §8, cas 1) : polygone de pièce, revêtement,
  couleurs et paramètres de variante sont des colonnes JSON, pas des tables normalisées.
- **Verrouillage optimiste** (§8, cas 3) : `Project.version`, incrémenté à chaque écriture,
  via `__mapper_args__ = {"version_id_col": ...}` de SQLAlchemy.
- **Type `JSON` portable** plutôt que `JSONB` : la suite de tests tourne sur SQLite en mémoire
  (`cd backend && pytest` doit rester exécutable sans Docker, cf. `CLAUDE.md`), et la CI la
  rejoue contre un vrai PostgreSQL.
- **Labels de face** : la colonne `label` existe en P1 ; l'attribution automatique (A, B, C…)
  est une règle métier qui vit dans l'API, donc en P3.

## Critères d'acceptation (exécutables)

| # | Critère | Vérification |
|---|---|---|
| A1 | `alembic upgrade head` sur une base vide crée toutes les tables sans erreur | test `test_migrations.py` + commande manuelle sur la stack Docker |
| A2 | Un test crée `Project → Room → Face → Element` et relit les relations | `tests/test_models.py` |
| A3 | Un test vérifie qu'une FK bloque un `Element` référençant une `Face` inexistante | `tests/test_models.py` |
| A4 | L'admin SQLAdmin liste et permet d'éditer chaque modèle sur `/admin` | `tests/test_models.py` (requêtes HTTP sur `/admin/...`) |

## Definition of done

Critères verts + `pytest`/`ruff`/`mypy` verts + revue `spec-reviewer` + `PROGRESS.md` à jour.
