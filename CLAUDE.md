# Éditeur de plan de rénovation 2D → 3D

## Contrat de référence
Toute décision fonctionnelle ou d'architecture vit dans `docs/spec-complete.md`. Ne jamais dévier silencieusement de ce document. Si un ticket semble exiger un changement de scope ou d'architecture, s'arrêter et le signaler plutôt que d'improviser — proposer une modification explicite du fichier spec.

Décisions déjà tranchées, à ne pas rediscuter en cours de ticket : voir `docs/spec-complete.md` §6.1 (choix FastAPI) et §8 (arbitrages performance vs intégrité).

## Stack
- Backend : FastAPI + SQLModel + Alembic + SQLAdmin, Python 3.12
- Frontend : Vue 3 + Konva (éditeur 2D) + TresJS/Three.js (viewer 3D)
- Base de données : PostgreSQL. Tâches asynchrones : Celery + Redis.
- Détails et justifications complètes : `docs/spec-complete.md` §6

## Commandes
- Backend (dev) : `cd backend && uvicorn app.main:app --reload`
- Tests backend : `cd backend && pytest`
- Lint + types backend : `cd backend && ruff check . && mypy .`
- Nouvelle migration : `cd backend && alembic revision --autogenerate -m "message"`
- Appliquer les migrations : `cd backend && alembic upgrade head`
- Frontend (dev) : `cd frontend && npm run dev`
- Tests frontend : `cd frontend && npm run test`
- Tout démarrer (dev) : `docker compose up`

## Règles de travail
- Pour tout ticket non trivial (plus d'un fichier, logique métier) : passer par le mode plan avant d'écrire du code.
- Un ticket = un diff revuable. Ne pas toucher à des fichiers hors du périmètre annoncé sans le signaler explicitement.
- Un ticket n'est "fini" que si ses tests passent. Ne jamais déclarer un ticket terminé sur la seule base d'une lecture du code — montrer la sortie des tests.
- Après implémentation, invoquer le sous-agent `spec-reviewer` pour une revue adversariale avant de considérer le ticket clos.
- Géométrie 3D (`backend/app/geometry/`) : les fixtures dans `backend/tests/geometry/fixtures/` font foi. Ne jamais modifier une fixture pour faire passer un test — si un résultat attendu semble faux, le signaler plutôt que de l'ajuster.

## Points de vigilance
- FastAPI et SQLModel évoluent vite. En cas de doute sur une API ou une signature, vérifier dans le package installé (`pip show`, lire le code source) ou la documentation officielle plutôt que de se fier à la mémoire d'entraînement.
- `three-bvh-csg` (CSG pour les ouvertures de murs et certains meubles) est une librairie expérimentale. N'y recourir que là où la version simple (`THREE.Shape` avec trou, voir spec §3.2) ne suffit pas.
- `fastapi-users` est en mode maintenance (plus de nouvelles fonctionnalités) — ne pas l'introduire, l'auth suit le pattern manuel documenté dans le spec §6.
