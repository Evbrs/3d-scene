# PROGRESS.md — état d'avancement

État de référence des tickets. Mis à jour à la clôture de chaque ticket
(`docs/plan-generation-ia.md` §1 et §4 étape 7). Une session future lit ce fichier avant tout
travail, pour ne pas redécouvrir ni contredire ce qui existe déjà.

Statuts : `à faire` · `en cours` · `en revue` · `fait`

## Séquencement (`docs/plan-generation-ia.md` §5)

| Ticket | Contenu | Dépend de | Statut |
|---|---|---|---|
| P0 | Scaffolding + CI | — | **fait** |
| P1 | Modèles SQLModel + migrations Alembic + admin SQLAdmin | P0 | **fait** |
| P2 | Auth JWT, permissions objet | P1 | **fait** |
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

### P1 — Modèle de données · **fait**

Modèles `Project`/`Room`/`Face`/`Element`/`FurnitureType`/`SharedView`, migration Alembic
initiale, back-office SQLAdmin sur `/admin`.

**Critères d'acceptation**

| Critère | État | Vérification |
|---|---|---|
| `alembic upgrade head` sur une base vide crée toutes les tables | ✅ | `tests/test_migrations.py`, exécuté sur SQLite **et** sur PostgreSQL (schéma dédié) + application réelle sur la stack Docker |
| Un test crée `Project → Room → Face → Element` et relit les relations | ✅ | `test_creates_and_reads_back_project_room_face_element` |
| Un test vérifie qu'une FK bloque un `Element` sur une `Face` inexistante | ✅ | `test_foreign_key_blocks_element_on_missing_face`, avec garde `foreign_keys_enforced` |
| L'admin liste et permet d'éditer chaque modèle sur `/admin` | ✅ | `test_admin_edits_every_model` : formulaire d'édition en 200 sur une ligne réelle des 7 modèles, plus une modification effectivement écrite en base |

Suite complète : **23 tests verts sur SQLite *et* sur PostgreSQL**, `ruff` et `mypy --strict` verts.

**Décisions prises pendant le ticket**

- `TimestampedModel` utilise `DateTime(timezone=True)` : la migration autogénérée produisait des
  colonnes naïves alors que le code produit des datetimes *aware*, ce qui aurait fait perdre le
  fuseau à l'écriture. Migration régénérée.
- Pas de `from __future__ import annotations` dans `app/models/plan.py` : SQLAlchemy refuse une
  annotation de relation devenue chaîne. `FurnitureType` est défini avant `Element` pour la même
  raison (une union en chaîne, `"FurnitureType | None"`, n'est pas résoluble).
- Verrouillage optimiste (spec §8, cas 3) implémenté via `version_id_col` sur `Project`, couvert
  par un test qui simule deux sessions concurrentes.
- Base de test : fichier SQLite temporaire par défaut (pour que `pytest` reste exécutable sans
  Docker), la CI rejouant la même suite sur PostgreSQL. Un fichier et non `:memory:` parce que
  SQLAdmin ouvre son propre moteur synchrone.
- Le conteneur `backend` applique les migrations au démarrage (sinon la stack sert une API sur
  une base sans tables).

**Hors périmètre annoncé** (liste complétée après revue) : `backend/app/db.py`,
`backend/alembic.ini`, `backend/app/main.py`, `backend/tests/conftest.py`,
`backend/tests/test_migrations.py`, `backend/app/core/config.py`, `backend/pyproject.toml`,
`docker/postgres-init/01-create-test-database.sh`, `docker-compose.yml`,
`.github/workflows/ci.yml`, `README.md`. Aucun n'est listé dans les fichiers autorisés du ticket
P1 ; chacun est nécessaire à un critère d'acceptation.

**Pièges rencontrés, corrigés** :
- Une installation PostgreSQL locale occupait `5432` et masquait le conteneur (« role "app" does
  not exist » alors que la base est saine). Ports hôte décalés par défaut : `5433` et `6380`.
- Pointer `TEST_DATABASE_URL` sur la base de développement la vidait (la suite fait `drop_all`
  en fin de test). Le volume PostgreSQL crée maintenant une base `app_test` dédiée.

### P1 — corrections après revue adversariale

La revue `spec-reviewer` a rendu **À CORRIGER** sur P1 avec un défaut réel de correction. Tout
est corrigé dans le commit de P2 :

1. **Migration non réversible sur PostgreSQL (bloquant).** `downgrade base` supprimait les tables
   mais laissait les types ENUM `facekind` / `elementkind` : l'`upgrade` suivant échouait sur
   « type "facekind" already exists ». Le défaut était invisible parce que
   `tests/test_migrations.py` codait en dur une URL SQLite et ignorait `TEST_DATABASE_URL` — les
   tests de migration ne tournaient jamais sur le moteur de production, contrairement à ce
   qu'affirmait leur docstring. Corrigé : `downgrade()` supprime explicitement les types, les
   tests de migration tournent aussi sur PostgreSQL (schéma dédié), et deux tests ont été ajoutés
   (`test_the_migration_can_be_replayed_after_a_full_downgrade`,
   `test_downgrade_also_removes_the_enum_types`). Vérifié comme non vacuous : en retirant le
   correctif, ces deux tests échouent.
2. **Critère A4 non couvert.** Le test interrogeait `/admin/{modèle}/create` (formulaire de
   *création*) pour justifier « permet d'éditer », et `SharedView` était en lecture seule.
   Corrigé : `SharedView` est éditable avec son `token` exclu du formulaire, et
   `test_admin_edits_every_model` charge le formulaire d'édition de chaque modèle sur une ligne
   réelle puis vérifie qu'une modification est bien persistée.
3. **`env.example` supprimé par inadvertance** alors que `README.md`, `.gitignore` et
   `config.py` y renvoient — la procédure de démarrage documentée était cassée sur un clone
   frais. Fichier restauré.
4. **Périmètre** : liste des fichiers hors périmètre complétée (ci-dessus).
5. **Commentaire faux dans `config.py`** sur une vérification de `SECRET_KEY` qui n'existait pas.
   La vérification existe désormais réellement (validateur qui refuse le démarrage hors
   développement avec une clé faible ou par défaut).
6. **Énumérations stockées par leur nom Python** (`CEILING`) alors que l'API sérialise leur
   valeur (`"ceiling"`). Corrigé via `values_callable` ; couvert par
   `test_enums_are_stored_by_value_not_by_python_name`. La migration initiale a été retouchée sur
   place plutôt que corrigée par une migration supplémentaire : le projet n'est déployé nulle
   part, et le seul environnement concerné (la base de dev locale) a été recréé.
7. **Affirmations inexactes de `PROGRESS.md`** rectifiées ci-dessus.

Points latents signalés par la revue, traités dans P2 :

- **`/admin` était exposé sans authentification** (CRUD complet sur toutes les données, même
  port que l'API) → back-office protégé par `AuthenticationBackend`, compte superutilisateur
  requis, couvert par `test_admin_requires_authentication`.
- **Colonnes JSON non mutables** : `room.polygon.append(...)` n'aurait jamais été persisté,
  SQLAlchemy ne détectant pas les mutations en place. Or §8 cas 1 fait du JSON le stockage
  principal de la géométrie, donc c'est le mode d'usage naturel en P3/P4. Toutes les colonnes
  JSON utilisent maintenant `MutableDict` / `MutableList`.

### P2 — Auth JWT et permissions objet · **fait**

Comptes, JWT (accès + rafraîchissement), permissions objet par propriété du projet.

**Critères d'acceptation**

| Critère | État | Vérification |
|---|---|---|
| Inscription, connexion, profil via JWT | ✅ | `tests/test_auth.py` + vérifié sur la stack Docker (`register` 201, `token` 200, `/me` 200) |
| Mot de passe jamais stocké ni renvoyé en clair | ✅ | `test_the_password_is_never_returned_nor_stored_in_clear` — Argon2id, sel aléatoire |
| Jeton absent / invalide / expiré / forgé / `alg:none` / de mauvais type refusé | ✅ | 8 tests dédiés |
| Objet d'un autre utilisateur inaccessible (404) à tous les niveaux de l'arbre | ✅ | `test_object_permissions_*` (projet, pièce, face, élément) |
| Migration `user` + `Project.owner_id`, réversible | ✅ | `tests/test_migrations.py` sur PostgreSQL, aller-retour + rejeu |
| `/admin` inaccessible sans authentification | ✅ | `test_admin_requires_authentication` (7 modèles) + `curl` sur la stack → 302 vers `/admin/login` |

Suite complète : **61 tests sur SQLite, 62 sur PostgreSQL**, `ruff` et `mypy --strict` verts.

**Écart de spec formalisé** : `docs/spec-complete.md` §6 a été amendé (bloc « Amendement
(ticket P2) ») pour remplacer `passlib` / `python-jose` par `pwdlib` / `pyjwt`. Motif vérifié
empiriquement, pas de mémoire : `passlib` 1.7.4 (dernière publication octobre 2020) lève
`AttributeError: module 'bcrypt' has no attribute '__about__'` avec `bcrypt` 5. Le tutoriel
officiel FastAPI, raison invoquée par la spec pour ce choix, utilise désormais `pwdlib` + `pyjwt`.

**Choix de sécurité** (au-delà du strict énoncé du ticket, alignés sur les conventions du
`CLAUDE.md` global) : Argon2id, réponses et temps de réponse indistinguables entre compte
inconnu et mauvais mot de passe, 404 au lieu de 403 sur les objets d'autrui, limitation de débit
sur la connexion, refus de démarrer hors développement avec une `SECRET_KEY` faible, hachage de
mot de passe jamais exposé dans le back-office.

**À traiter en P12** : la limitation de débit est en mémoire du processus (insuffisant en
multi-workers, à porter sur Redis) ; pas de révocation de jeton.
