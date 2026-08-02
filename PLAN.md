# PLAN.md — Ticket P0 : Scaffolding

> Plan du ticket en cours. Régénéré à chaque ticket, jamais accumulé.
> Source : `docs/plan-generation-ia.md` §8 (ticket P0), `docs/spec-complete.md` §6 (stack).

## Objectif

Poser un squelette de repo déployable, avant tout code métier.

## Référence spec

`docs/spec-complete.md` §6 (stack technique).

## Fichiers autorisés

L'ensemble du repo — seul ticket avec un périmètre aussi large, puisque rien n'existe encore
(`docs/plan-generation-ia.md` §8).

## Non-objectifs

- Aucun modèle de données (→ P1)
- Aucune route métier (→ P3)
- Aucun composant Vue au-delà d'un écran de test (→ P4)
- Aucune auth (→ P2), aucun catalogue de mobilier (→ P5), aucune géométrie (→ P6)

## Découpage

1. **Mise en place §2** : dépôt Git, `CLAUDE.md` à la racine, `spec-reviewer.md` dans
   `.claude/agents/`, specs déplacées dans `docs/` (le `CLAUDE.md` fourni les référence sous
   `docs/spec-complete.md`), `PROGRESS.md` créé.
2. **Backend** : projet FastAPI Python 3.12, `pyproject.toml` (deps de la stack §6),
   configuration par variables d'environnement (`pydantic-settings`), route `GET /health`,
   config `ruff` + `mypy` stricts, un test pytest du health check.
3. **Frontend** : projet Vue 3 + TypeScript + Vite, dépendances de la stack (`vue-konva`,
   `@tresjs/core`, `three`), un unique écran de test qui appelle `/health`, `vitest` avec un
   test de fumée.
4. **Docker** : `Dockerfile` backend et frontend, `docker-compose.yml` avec Postgres + Redis +
   backend + frontend, healthchecks et dépendances de démarrage.
5. **CI** : workflow GitHub Actions exécutant exactement les mêmes checks que les critères
   d'acceptation.
6. **Docs** : `README.md` (démarrage local), `PROGRESS.md` (phases §5 + statuts).

## Critères d'acceptation (exécutables)

| # | Critère | Commande de vérification |
|---|---|---|
| A1 | `docker compose up` démarre Postgres + Redis + backend + frontend sans erreur | `docker compose config -q` puis `docker compose up` |
| A2 | `GET /health` retourne `200 {"status": "ok"}` | `pytest tests/test_health.py` + `curl` sur l'app lancée |
| A3 | `cd backend && pytest` passe | `pytest` |
| A4 | `cd backend && ruff check . && mypy .` sans erreur | idem |
| A5 | `cd frontend && npm run build` réussit | idem |
| A6 | La CI exécute ces mêmes checks à chaque push | `.github/workflows/ci.yml` |

## Definition of done

Tous les critères verts + `README.md` avec instructions de démarrage local + `PROGRESS.md` créé
+ revue `spec-reviewer` + commit descriptif.

## Risques identifiés

- **Docker daemon indisponible sur la machine de dev** : A1 se vérifie alors uniquement par
  `docker compose config -q` (validation statique) ; le démarrage réel reste à valider par
  l'humain ou par la CI. À signaler explicitement, jamais à déclarer vert par défaut.
- **TresJS / `three` / `vue-konva`** : installés en dépendance dès P0 pour figer la stack (§6),
  mais non utilisés avant P4/P7 (non-objectif).
- **`three-bvh-csg`** : *décision prise en cours de ticket* — volontairement **non installé**
  en P0. Il n'est requis qu'à partir de P6/P7 et son statut expérimental (spec §3.2) justifie
  d'en choisir la version au moment de l'utiliser, plutôt que de figer dès maintenant une
  version d'une librairie mouvante.
