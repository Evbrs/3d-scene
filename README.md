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
| Back-office (SQLAdmin) | http://localhost:8000/admin |
| Health check | http://localhost:8000/health |
| PostgreSQL | `localhost:5433` (décalé : une installation locale occupe souvent 5432) |
| Redis | `localhost:6380` |
| Worker Celery | exports PDF en tâche de fond |

Le conteneur `backend` applique `alembic upgrade head` au démarrage. Le volume PostgreSQL crée
deux bases : `app` (développement) et `app_test` (suite de tests).

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
| Tests backend (SQLite temporaire, sans Docker) | `cd backend && pytest` |
| Tests backend sur PostgreSQL | `cd backend && TEST_DATABASE_URL=postgresql+psycopg://app:<mdp>@localhost:5433/app_test pytest` |
| Migrations | `cd backend && alembic upgrade head` |
| Vérifier l'absence de dérive modèles/migrations | `cd backend && alembic check` |
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
│   │   ├── models/   # modèles SQLModel
│   │   ├── admin.py  # back-office SQLAdmin
│   │   ├── db.py     # moteur + session
│   │   └── main.py
│   ├── alembic/      # migrations
│   └── tests/
├── frontend/         # SPA Vue 3
│   └── src/
│       ├── api/      # client HTTP typé vers le backend
│       └── App.vue
├── docs/             # spec de référence + méthode de développement
├── .claude/agents/   # sous-agent de revue adversariale (spec-reviewer)
└── docker-compose.yml
```

## Déploiement en production

```bash
export POSTGRES_USER=... POSTGRES_PASSWORD=... POSTGRES_DB=...
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export CORS_ORIGINS=https://plan.exemple.fr
export PUBLIC_API_URL=https://api.plan.exemple.fr

docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Différences volontaires avec le développement :

| Point | Développement | Production |
|---|---|---|
| Frontend | Vite avec rechargement à chaud | build statique servi par nginx (non-root, port 8080) |
| Code source | monté depuis le disque | figé dans l'image |
| Base et Redis | ports exposés sur l'hôte | aucun port publié |
| `/docs`, `/redoc`, `/openapi.json` | exposés | **fermés** — ils décrivent toute la surface d'attaque |
| `SECRET_KEY`, `CORS_ORIGINS` | valeurs de développement | **obligatoires**, le démarrage échoue sinon |
| HSTS | absent (service en clair) | `max-age=31536000; includeSubDomains` |

Le schéma OpenAPI reste disponible pour le frontend sous forme de fichier versionné
(`frontend/src/api/openapi-snapshot.json`), régénéré et vérifié par la CI.

### Ce qui reste à faire côté infrastructure

Ces points sortent du périmètre du dépôt et dépendent de l'hébergeur :

- **Terminaison TLS** devant nginx (l'en-tête HSTS est déjà posé par l'API).
- **Sauvegardes PostgreSQL** et test de restauration.
- **Limitation de débit partagée** : celle en place vit dans la mémoire de chaque processus, donc
  se dilue avec plusieurs workers. À porter sur Redis.
- **Supervision** : Sentry est prévu par les conventions du projet, pas encore branché.

## Contribution

Le développement suit la boucle décrite dans `docs/plan-generation-ia.md` §4 : un ticket = un
diff revuable, critères d'acceptation exécutables, revue adversariale (`spec-reviewer`) avant
clôture, `PROGRESS.md` mis à jour à chaque ticket.
