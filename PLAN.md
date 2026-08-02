# PLAN.md — Ticket P2 : Auth JWT et permissions objet

> Source : `docs/plan-generation-ia.md` §5 (ticket P2), `docs/spec-complete.md` §6 (auth) et §7.

## Objectif

Comptes utilisateurs, authentification JWT, et permissions objet fondées sur la propriété des
projets.

## Référence spec

`docs/spec-complete.md` §6 (ligne « Auth », amendée par ce ticket — voir ci-dessous) et §7
(phase P2 : « Comptes, propriété des projets »).

## Fichiers autorisés

`backend/app/core/security.py`, `backend/app/core/rate_limit.py`, `backend/app/models/user.py`,
`backend/app/api/auth.py`, `backend/app/api/deps.py`, `backend/app/api/permissions.py`,
`backend/alembic/versions/`, `backend/tests/test_auth.py`.

**Extensions signalées** : `app/core/config.py` (durées de vie des jetons, validation de la clé),
`app/models/plan.py` + `app/models/__init__.py` (`Project.owner_id`), `app/main.py` (montage du
routeur), `app/admin.py` (authentification du back-office), `backend/pyproject.toml`
(dépendances), `tests/conftest.py` (fixtures `owner` et `admin_client`).

**Corrections P1 incluses dans ce ticket** (issues de la revue `spec-reviewer`, voir PROGRESS.md).

## Non-objectifs

- Aucune route CRUD du plan 2D (→ P3)
- Aucun composant Vue de connexion (→ P4)
- Pas de révocation de jeton en base ni de liste noire (le jeton d'accès est court ; à
  reconsidérer en P12 si un besoin de déconnexion immédiate apparaît)

## Décisions

- **Écart assumé sur la spec §6**, formalisé par un amendement du fichier de spec :
  `pwdlib` (Argon2id) + `pyjwt` remplacent `passlib` / `python-jose`. `passlib` n'est plus publié
  depuis octobre 2020 et est cassé avec `bcrypt >= 4.1` (reproduit en local) ; le tutoriel
  officiel FastAPI — la raison même du choix inscrite dans la spec — utilise désormais ce couple.
- **Jeton d'accès court + jeton de rafraîchissement**, avec vérification explicite du type : un
  jeton de rafraîchissement présenté comme jeton d'accès est refusé.
- **404 plutôt que 403** sur un objet appartenant à autrui : un 403 confirmerait son existence.
- **Réponses indistinguables** entre compte inconnu et mauvais mot de passe, y compris en temps
  de réponse (hachage leurre).
- **Limitation de débit sur la connexion** en mémoire du processus ; passage à un compteur Redis
  partagé inscrit en P12.

## Critères d'acceptation (exécutables)

| # | Critère | Vérification |
|---|---|---|
| A1 | Inscription, connexion, lecture du profil via JWT | `tests/test_auth.py` |
| A2 | Un mot de passe n'est jamais stocké ni renvoyé en clair | `tests/test_auth.py` |
| A3 | Les routes protégées refusent jeton absent, invalide, expiré, forgé, de mauvais type | `tests/test_auth.py` |
| A4 | Un objet d'un autre utilisateur est inaccessible (404), à tous les niveaux de l'arbre | `tests/test_auth.py` |
| A5 | La migration ajoute `user` et `Project.owner_id`, réversible | `tests/test_migrations.py` |
| A6 | `/admin` n'est plus accessible sans authentification | `tests/test_models.py` |

## Definition of done

Critères verts + `pytest` (SQLite **et** PostgreSQL) + `ruff` + `mypy` + revue `spec-reviewer`
+ `PROGRESS.md` à jour.
