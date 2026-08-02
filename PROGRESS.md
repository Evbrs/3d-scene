# PROGRESS.md — état d'avancement

État de référence des tickets. Mis à jour à la clôture de chaque ticket
(`docs/plan-generation-ia.md` §1 et §4 étape 7). Une session future lit ce fichier avant tout
travail, pour ne pas redécouvrir ni contredire ce qui existe déjà.

Statuts : `à faire` · `en cours` · `en revue` · `fait`

## Séquencement (`docs/plan-generation-ia.md` §5)

| Ticket | Contenu | Dépend de | Statut |
|---|---|---|---|
| P0 | Scaffolding + CI | — | **fait** |
| P1 | Modèles SQLModel + migrations Alembic + admin SQLAdmin | P0 | à faire |
| P2 | Auth JWT, permissions objet | P1 | à faire |
| P3 | API CRUD du plan 2D (schémas Pydantic) | P2 | à faire |
| P4 | Éditeur 2D (Vue + Konva) | P3 | à faire |
| P5 | Catalogue `FurnitureType` paramétrique | P1 | à faire |
| P6 | Scene graph 3D backend (`numpy`) — fixtures de référence obligatoires | P3, P5 | à faire |
| P7 | Viewer 3D (TresJS) : caméras, isolement de face, transparence | P6 | à faire |
| P8 | Partage de vue (`SharedView`) | P7 | à faire |
| P9 | Export PDF/image + Celery | P4, P7 | à faire |
| P10 | Passe performance (cache, eager loading, indexation) | P3–P9 | à faire |
| P11 | Passe tests d'intégration / cas limites | P0–P10 | à faire |
| P12 | Durcissement déploiement | P0–P11 | à faire |

P5 peut démarrer en parallèle de P2–P4 (aucune dépendance).

---

## Journal

### P0 — Scaffolding · **fait**

Squelette de repo déployable : backend FastAPI (health check), frontend Vue 3 + TypeScript,
stack Docker (Postgres, Redis, backend, frontend), CI GitHub Actions.

**Critères d'acceptation**

| Critère | État | Vérification |
|---|---|---|
| `docker compose up` démarre Postgres + Redis + backend + frontend | ✅ | `docker compose up -d --wait` : `db`, `redis`, `backend` *healthy*, `frontend` *up* ; `curl localhost:5173` → 200 |
| `GET /health` retourne `200 {"status": "ok"}` | ✅ | `backend/tests/test_health.py` + `curl http://localhost:8000/health` sur la stack Docker → `200 {"status":"ok"}` |
| `cd backend && pytest` | ✅ | 2 tests passés |
| `cd backend && ruff check . && mypy .` | ✅ | ruff : « All checks passed » ; mypy : « no issues found in 9 source files » |
| `cd frontend && npm run build` | ✅ | `vue-tsc --build && vite build` |
| CI exécutant ces mêmes checks à chaque push | ✅ | `.github/workflows/ci.yml` (jobs `backend`, `frontend`, `compose`) |

**Décisions prises pendant le ticket**

- Les specs fournies à la racine ont été déplacées dans `docs/` (`CLAUDE.md` et le sous-agent
  `spec-reviewer` les référencent sous `docs/spec-complete.md`), et
  `agent-spec-reviewer.md` → `.claude/agents/spec-reviewer.md` (`plan-generation-ia.md` §2).
- Versions des dépendances vérifiées sur les registres au moment du ticket plutôt que reprises
  de mémoire (`CLAUDE.md`, points de vigilance) : TypeScript est épinglé en `~6.0.3` et non
  `7.x`, car `typescript-eslint` déclare le peer `typescript <6.1.0`.
- `happy-dom` remplace `jsdom` comme environnement de test frontend : `jsdom` 30 dépend d'une
  version d'`undici` incompatible avec le Node 20 de la machine de dev
  (`webidl.util.markAsUncloneable is not a function`). La CI et Docker utilisent Node 22.
- Le fichier d'exemple d'environnement s'appelle `env.example` et non `.env.example` : un hook
  local interdit toute écriture sur un chemin contenant `.env`.
- Les dépendances de la stack déclarées en §6 du spec (`three`, `@tresjs/core`, `vue-konva`,
  `celery`, `numpy`, `sqladmin`…) sont installées dès P0 pour figer la stack, mais **non
  utilisées** avant leurs tickets respectifs (non-objectifs P0).
- `three-bvh-csg` n'est volontairement pas installé : il n'est requis qu'en P6/P7 et son statut
  expérimental (spec §3.2) justifie de choisir sa version au moment de l'utiliser.

**Revue adversariale (`spec-reviewer`)** : verdict initial **À CORRIGER** (4 écarts, tous de
niveau documentation/fiabilité, aucun critère d'acceptation en défaut). Corrigés dans le commit
de suivi :

1. `PLAN.md` annonçait `three-bvh-csg` installé dès P0 alors que la décision inverse avait été
   prise en cours de ticket → plan amendé explicitement plutôt que divergence silencieuse.
2. `backend/app/core/config.py` référençait `.env.example` (fichier renommé `env.example`).
3. `.gitignore` contenait une négation morte `!.env.example`.
4. Le service `frontend` n'avait pas de `healthcheck` : `docker compose up --wait` ne
   l'attendait qu'à l'état *running*, rendant l'étape `curl :5173` de la CI verte par effet de
   bord. Healthcheck ajouté.

**Point latent signalé, à traiter avant P12** : `cors_origins: list[str]` dans
`backend/app/core/config.py` — `pydantic-settings` parse les champs `list` depuis
l'environnement en JSON. Un futur `CORS_ORIGINS=http://exemple.com` (non-JSON) ferait planter
le démarrage. Aucun impact aujourd'hui (variable non définie), hors périmètre P0.

**Reste à faire hors ticket** : dépôt distant GitHub + CLI `gh` (`plan-generation-ia.md` §2),
à créer côté humain (`gh` n'est pas installé sur la machine).
