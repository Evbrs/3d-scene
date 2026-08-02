# Éditeur de plan de rénovation 2D → 3D

Application web de conception de plans de rénovation : édition 2D d'un plan (pièces, faces,
ouvertures, revêtements), génération d'une scène 3D côté serveur, et visualisation 3D
(caméras multiples, isolement de face, transparence, partage de vue).

- **Contrat fonctionnel et technique** : [`docs/spec-complete.md`](docs/spec-complete.md)
- **Méthode de développement** : [`docs/plan-generation-ia.md`](docs/plan-generation-ia.md)
- **Avancement** : [`PROGRESS.md`](PROGRESS.md)

## Stack

| Couche | Techno |
|---|---|
| Backend | FastAPI, SQLModel, Alembic, SQLAdmin — Python 3.12 |
| Frontend | Vue 3 + TypeScript, Konva (2D), TresJS/Three.js (3D) — Vite |
| Base de données | PostgreSQL |
| Tâches asynchrones | Celery + Redis |

Justification des choix : `docs/spec-complete.md` §6.

## Démarrage rapide (Docker)

```bash
cp env.example .env       # puis adapter les valeurs si besoin
docker compose up
```

| Service | URL |
|---|---|
| Frontend (Vite) | http://localhost:5173 |
| Backend (FastAPI) | http://localhost:8000 |
| Doc API interactive | http://localhost:8000/docs |
| Health check | http://localhost:8000/health |
| PostgreSQL | `localhost:5432` |
| Redis | `localhost:6379` |

Le schéma OpenAPI (`http://localhost:8000/openapi.json`) est la **source de vérité** des routes
et formats de réponse pour le frontend — aucune route ne doit être devinée.

## Démarrage sans Docker

### Backend

Python **3.12** requis (voir `requires-python` dans `backend/pyproject.toml`).

```bash
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Avec [uv](https://docs.astral.sh/uv/) : `uv venv --python 3.12 && uv pip install -e ".[dev]"`.

### Frontend

Node **22 LTS** recommandé (la CI et l'image Docker utilisent Node 22).

```bash
cd frontend
npm install
npm run dev
```

## Commandes de vérification

Ce sont exactement les checks exécutés par la CI (`.github/workflows/ci.yml`).

| But | Commande |
|---|---|
| Tests backend | `cd backend && pytest` |
| Lint + types backend | `cd backend && ruff check . && mypy .` |
| Tests frontend | `cd frontend && npm run test` |
| Lint frontend | `cd frontend && npm run lint` |
| Build frontend | `cd frontend && npm run build` |
| Validation du fichier compose | `docker compose config -q` |

## Structure

```
.
├── backend/          # API FastAPI
│   ├── app/
│   │   ├── api/      # routers HTTP
│   │   ├── core/     # configuration
│   │   └── main.py
│   └── tests/
├── frontend/         # SPA Vue 3
│   └── src/
│       ├── api/      # client HTTP typé vers le backend
│       └── App.vue
├── docs/             # spec de référence + méthode de développement
├── .claude/agents/   # sous-agent de revue adversariale (spec-reviewer)
└── docker-compose.yml
```

## Contribution

Le développement suit la boucle décrite dans `docs/plan-generation-ia.md` §4 : un ticket = un
diff revuable, critères d'acceptation exécutables, revue adversariale (`spec-reviewer`) avant
clôture, `PROGRESS.md` mis à jour à chaque ticket.
