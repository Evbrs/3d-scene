# Éditeur de plan de rénovation 2D → 3D

Application web de conception de plans de rénovation, pour l'artisan de second œuvre : édition 2D
d'un plan (pièces, faces, ouvertures, revêtements, mobilier paramétrique), génération d'une scène
3D côté serveur, visualisation 3D (caméras multiples, isolement de face, transparence, partage de
vue), puis **métré, devis chiffré, facture Factur-X et dossier d'élévations cotées**. Un moteur
d'analyse du plan — contrôle de conformité, calepinage, aménagement — complète l'ensemble ; il est
**algorithmique et local** : déterministe, testable par fixtures, sans clé ni appel réseau sortant.

Le mobilier est **générique et paramétrique** : aucun catalogue de marque externe n'est importé
(`docs/spec-complete.md` §4.1).

- **Contrat fonctionnel et technique** : [`docs/spec-complete.md`](docs/spec-complete.md) — §10
  porte les amendements et les questions ouvertes, à lire avant tout travail
- **Positionnement, tarification** : [`docs/strategie-produit.md`](docs/strategie-produit.md)
- **Point de reprise** : [`docs/REPRISE.md`](docs/REPRISE.md)
- **Conformité RGPD (côté exploitant)** : [`docs/rgpd.md`](docs/rgpd.md)
- **Méthode de développement** : [`docs/plan-generation-ia.md`](docs/plan-generation-ia.md)
- **Avancement** : [`PROGRESS.md`](PROGRESS.md)

> **Avant toute mise en ligne**, quatre points sont bloquants et documentés en questions ouvertes
> (`docs/spec-complete.md` §10, n° 10 à 13) : aucun transport de courriel n'est branché (la
> réinitialisation de mot de passe est inutilisable en ligne), les documents légaux sont des
> gabarits non relus par un juriste, les durées de conservation annoncées ne sont pas appliquées,
> et aucun encaissement n'est intégré.

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

> **Après avoir ajouté une dépendance npm**, la stack Docker ne la voit pas : `node_modules` est
> un volume anonyme qui survit à `--build`. Il faut le renouveler :
> `docker compose up -d --force-recreate --renew-anon-volumes frontend`.

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
| Vulnérabilités des dépendances backend | `cd backend && pip-audit -r requirements.txt` |
| Vulnérabilités des dépendances frontend | `cd frontend && npm audit --audit-level=high` |
| Tests backend sur PostgreSQL | `cd backend && TEST_DATABASE_URL=postgresql+psycopg://app:<mdp>@localhost:5433/app_test pytest` |
| Migrations | `cd backend && alembic upgrade head` |
| Vérifier qu'il n'y a **qu'une seule** tête de migration | `cd backend && alembic heads` |
| Vérifier l'absence de dérive modèles/migrations | `cd backend && alembic check` |
| Lint + types backend | `cd backend && ruff check . && mypy .` |
| Régénérer l'instantané OpenAPI | `cd backend && ENVIRONMENT=development python -m scripts.dump_openapi ../frontend/src/api/openapi-snapshot.json` |
| Tests frontend | `cd frontend && npm run test` |
| Lint frontend | `cd frontend && npm run lint` |
| Build frontend | `cd frontend && npm run build` |
| Validation du fichier compose | `docker compose config -q` |
| Validation de la surcouche de production | `docker compose -f docker-compose.yml -f docker-compose.prod.yml config -q` |

L'instantané OpenAPI est un **fichier généré** : il ne se fusionne pas. On le régénère une fois, en
dernier, et la CI échoue si le fichier versionné a dérivé du backend. Une migration ajoutée sans
chaîner sa `down_revision` sur la tête courante ouvre une branche, que `alembic heads` révèle
immédiatement — c'est la vérification à faire en premier après un travail à plusieurs.

## Structure

```
.
├── backend/          # API FastAPI
│   ├── app/
│   │   ├── api/          # routers HTTP
│   │   ├── core/         # configuration, sécurité, limitation de débit, cache
│   │   ├── models/       # modèles SQLModel
│   │   ├── schemas/      # frontière Pydantic de l'API
│   │   ├── geometry/     # scene graph et métré — fonctions pures, sans SQLModel
│   │   ├── intelligence/ # conformité, calepinage, aménagement — pures elles aussi
│   │   ├── services/     # chiffrage, export PDF, quotas, catalogue, comptes
│   │   ├── tasks/        # tâches Celery
│   │   ├── admin.py      # back-office SQLAdmin
│   │   ├── db.py         # moteur + session
│   │   └── main.py
│   ├── alembic/      # migrations
│   ├── scripts/      # outillage hors application (dump du schéma OpenAPI)
│   ├── requirements.txt  # versions figées installées par l'image de production
│   └── tests/
│       ├── geometry/fixtures/      # références calculées à la main — elles font foi
│       └── intelligence/fixtures/  # idem, pour le contrôle de conformité
├── frontend/         # SPA Vue 3
│   ├── nginx/        # service statique + relais /api (production)
│   ├── public/       # servi tel quel : robots.txt, favicon
│   └── src/
│       ├── api/         # client HTTP typé + instantané OpenAPI versionné
│       ├── components/  # briques partagées entre écrans
│       ├── editor/      # éditeur 2D (Konva) : géométrie, magnétisme, historique
│       ├── stores/      # état partagé (Pinia)
│       ├── viewer/      # viewer 3D (TresJS) : visibilité, textures, ressources
│       ├── views/       # écrans
│       ├── router.ts
│       └── App.vue
├── docs/             # spec de référence + méthode de développement
├── .claude/agents/   # sous-agent de revue adversariale (spec-reviewer)
└── docker-compose.yml
```

## Déploiement en production

```bash
export POSTGRES_USER=... POSTGRES_PASSWORD=... POSTGRES_DB=...
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export PUBLIC_DOMAIN=plan.exemple.fr

docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile tls up -d
```

Seuls `POSTGRES_*` et `SECRET_KEY` sont obligatoires ; tout le reste a une valeur par défaut sûre.
Le modèle commenté de toutes les variables est dans [`env.example`](env.example).

### Une seule origine

nginx sert la page **et** relaie l'API : le navigateur ne parle qu'à `https://plan.exemple.fr`.
Ce choix n'est pas cosmétique — il referme d'un coup la directive `connect-src` de la CSP, le
CORS, et l'exposition publique du port 8000.

> `PUBLIC_API_URL` doit donc rester **vide**. En particulier, `/api` n'est pas la bonne valeur :
> les chemins de `frontend/src/api/client.ts` portent déjà ce préfixe, et une base `/api`
> produirait des appels vers `/api/api/...`. Ne la renseigner que si l'API vit réellement sur un
> autre domaine — il faut alors aussi remplir `CORS_ORIGINS` et rouvrir `connect-src`.

Conséquence à ne pas manquer : derrière un relais, `request.client.host` vaut l'adresse du relais
pour **tous** les visiteurs, ce qui transformerait les limiteurs de débit en un seau unique
partagé par la plateforme entière. La chaîne est donc explicite de bout en bout — Caddy renseigne
`X-Forwarded-For`, nginx ne l'accepte que du sous-réseau de la pile (`set_real_ip_from`), et
uvicorn ne fait confiance qu'à ce même sous-réseau (`--forwarded-allow-ips`, jamais `*`). Une
seule variable les commande : `DOCKER_SUBNET`.

### Terminaison TLS

Le profil `tls` ajoute une bordure [Caddy](https://caddyserver.com/) qui obtient et renouvelle
seule ses certificats (ACME) pour `PUBLIC_DOMAIN`. Sans elle, HSTS serait posé sur une réponse en
clair et un cookie `Secure` ne reviendrait jamais.

**Si l'hébergeur termine déjà le TLS** (répartiteur de charge managé, ingress, tunnel), démarrer
sans `--profile tls` et pointer ce terminateur sur le port du frontend. Il doit alors transmettre
`X-Forwarded-For` et `X-Forwarded-Proto`, et il faut publier le port hors de la boucle locale :

```bash
export FRONTEND_BIND=0.0.0.0   # défaut : 127.0.0.1
```

### Back-office

`/admin` (SQLAdmin) n'est pas relayé par nginx : il n'a rien à faire sur l'Internet public. Le
port 8000 n'est publié que sur la boucle locale du serveur, donc on l'atteint par un tunnel :

```bash
ssh -L 8000:127.0.0.1:8000 utilisateur@serveur
# puis http://localhost:8000/admin depuis son poste
```

### Différences volontaires avec le développement

| Point | Développement | Production |
|---|---|---|
| Frontend | Vite avec rechargement à chaud | build statique servi par nginx (non-root, port 8080) |
| Origine de l'API | `http://localhost:8000` (deux origines) | même origine, relayée par nginx |
| Migrations | jouées par la commande du conteneur backend | service `migrate` dédié, à usage unique |
| Image backend | étage `dev` (pytest, ruff, mypy) | étage `runtime`, sans outillage de test |
| Dépendances backend | planchers du `pyproject.toml` | versions figées de `backend/requirements.txt` |
| Code source | monté depuis le disque | figé dans l'image |
| Base et Redis | ports exposés sur l'hôte | aucun port publié |
| Backend et frontend | publiés sur toutes les interfaces | boucle locale ; seule la bordure TLS est publique |
| `/docs`, `/redoc`, `/openapi.json` | exposés | **fermés** — ils décrivent toute la surface d'attaque |
| `SECRET_KEY` | valeur de développement | **obligatoire**, le démarrage échoue sinon |
| TLS et HSTS | absents (service en clair) | Caddy + `max-age=31536000; includeSubDomains` |

Le schéma OpenAPI reste disponible pour le frontend sous forme de fichier versionné
(`frontend/src/api/openapi-snapshot.json`), régénéré et vérifié par la CI.

### Indexation

Rien de ce service n'a vocation à être indexé : un jeton de partage qui fuit une fois rendrait la
géométrie complète du logement d'un client visible pour toujours. `frontend/public/robots.txt`
interdit tout, et nginx pose `X-Robots-Tag: noindex, nofollow` sur `/partage/` et `/api/public/` —
c'est cet en-tête, et non robots.txt, qui fait retirer une URL déjà indexée.

### Ce qui reste à faire côté infrastructure

Ces points sortent du périmètre du dépôt et dépendent de l'hébergeur :

- **Sauvegardes PostgreSQL** et test de restauration.
- **Service d'envoi de courriel** : rien n'achemine les invitations ni les jetons de
  réinitialisation de mot de passe. Sans lui, un mot de passe oublié reste perdu en production.
- **Supervision** : Sentry est prévu par les conventions du projet, pas encore branché.
- **Ordonnanceur** : `celery beat` n'est pas configuré. Deux traitements l'attendent — la purge des
  données arrivées au terme de leur conservation, et la réconciliation du déclassement des
  chantiers excédentaires, qui n'a lieu aujourd'hui qu'à la consultation de la page abonnement.
- **Dimensionnement** : les `mem_limit` du compose de production visent un petit déploiement ;
  les relire avant d'y mettre du trafic réel.

La limitation de débit, elle, est **partagée** : elle vit sur Redis (fenêtre glissante en Lua), pas
dans la mémoire de chaque processus.

## Contribution

Le développement suit la boucle décrite dans `docs/plan-generation-ia.md` §4 : un ticket = un
diff revuable, critères d'acceptation exécutables, revue adversariale (`spec-reviewer`) avant
clôture, `PROGRESS.md` mis à jour à chaque ticket.
