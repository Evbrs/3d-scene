# PROGRESS.md — état d'avancement

État de référence des tickets. Mis à jour à la clôture de chaque ticket
(`docs/plan-generation-ia.md` §1 et §4 étape 7). Une session future lit ce fichier avant tout
travail, pour ne pas redécouvrir ni contredire ce qui existe déjà.

Statuts : `à faire` · `en cours` · `en revue` · `fait`

Le plan du ticket en cours vit dans `PLAN.md` ; ceux des tickets clos sont archivés dans
`docs/plans/`, pour qu'une revue reste rejouable contre le plan effectivement validé.

## Séquencement (`docs/plan-generation-ia.md` §5)

| Ticket | Contenu | Dépend de | Statut |
|---|---|---|---|
| P0 | Scaffolding + CI | — | **fait** |
| P1 | Modèles SQLModel + migrations Alembic + admin SQLAdmin | P0 | **fait** |
| P2 | Auth JWT, permissions objet | P1 | **fait** |
| P3 | API CRUD du plan 2D (schémas Pydantic) | P2 | **fait** |
| P4 | Éditeur 2D (Vue + Konva) | P3 | **fait** |
| P5 | Catalogue `FurnitureType` paramétrique | P1 | **fait** |
| P6 | Scene graph 3D backend (`numpy`) — fixtures de référence obligatoires | P3, P5 | **fait** |
| P7 | Viewer 3D (TresJS) : caméras, isolement de face, transparence | P6 | **fait** |
| P8 | Partage de vue (`SharedView`) | P7 | **fait** |
| P9 | Export PDF/image + Celery | P4, P7 | **fait** |
| P10 | Passe performance (cache, eager loading, indexation) | P3–P9 | **fait** |
| P11 | Passe tests d'intégration / cas limites | P0–P10 | **fait** |
| P12 | Durcissement déploiement | P0–P11 | **fait** |

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

### P3 — API CRUD du plan 2D · **fait**

19 routes exposées (`/api/projects`, `/api/rooms`, `/api/faces`, `/api/elements`), schémas
Pydantic imbriqués distincts des modèles, lettrage automatique des faces.

**Critères d'acceptation**

| Critère | État | Vérification |
|---|---|---|
| CRUD complet projet / pièce / face / élément | ✅ | `tests/test_plan_api.py` |
| Créer une pièce génère murs lettrés + sol + plafond | ✅ | `test_creating_a_room_generates_lettered_walls_plus_floor_and_ceiling` — 4 murs A–D, orientation des segments vérifiée |
| Modifier le polygone préserve revêtements et éléments | ✅ | `test_growing_the_polygon_adds_walls_and_keeps_the_existing_ones` (identifiant de face conservé) |
| Version périmée → 409 sans écrasement | ✅ | `test_a_stale_version_is_rejected_with_409` |
| Toutes les routes authentifiées, aucun objet d'autrui atteignable | ✅ | `test_every_route_is_authenticated` (14 routes), `test_another_account_cannot_reach_the_whole_tree` (9 accès) |
| Entrées invalides refusées (422) | ✅ | polygones dégénérés, couleurs non hexadécimales, dimensions négatives, champs interdits |
| Arbre complet en une requête | ✅ | `test_reading_a_project_returns_the_whole_nested_tree` |

Suite complète : **95 tests sur SQLite, 96 sur PostgreSQL**, `ruff` et `mypy --strict` verts.

**Décisions prises pendant le ticket**

- Les faces ne sont pas créables ni supprimables par l'API : elles découlent du polygone. Un test
  vérifie que ces verbes renvoient bien 405.
- La resynchronisation des faces est non destructive (conservation par étiquette), pour qu'une
  correction de plan ne détruise pas les revêtements et les meubles déjà posés.
- Lettrage A…Z puis AA, AB… : une pièce en L peut dépasser 26 murs, et recommencer à « A »
  violerait la contrainte d'unicité `(room_id, label)` posée en P1.
- Le verrouillage optimiste est *opt-in* : `version` absent = écriture directe, `version` fourni
  et périmé = 409 avec `X-Current-Version`.
- `extra="forbid"` sur tous les schémas d'entrée, contre l'assignation en masse.
- Les plans de tickets clos sont archivés dans `docs/plans/` (changement de convention par
  rapport au `PLAN.md` unique : une revue lancée après le ticket suivant ne pouvait plus lire le
  plan qu'elle devait vérifier).

### P2 — corrections après revue adversariale

La revue a rendu **À CORRIGER** avec 4 failles de sécurité réelles. Toutes corrigées :

1. **Énumération de comptes sur `/register`** — le *code de statut* était un oracle parfait
   (201 pour une adresse libre, 409 pour une adresse déjà inscrite), quel que soit le soin
   apporté au message. L'inscription répond désormais toujours `202` avec le même corps. Un test
   vérifie en plus qu'avaler le conflit ne permet pas de reprendre le compte d'autrui en
   réécrivant son mot de passe.
2. **Limitation de débit entièrement contournable** — un seul seau par IP, vidé à chaque
   connexion réussie : il suffisait d'intercaler un succès sur son propre compte pour attaquer
   indéfiniment celui d'autrui (54 tentatives, 0 bloquée dans le repro de la revue). Remplacé par
   deux seaux : un par cible (libéré au succès de *cette* cible) et un par IP (jamais libéré).
   `/register` est désormais limité aussi. Test de non-régression :
   `test_a_successful_login_does_not_unlock_attacks_on_other_accounts`.
3. **Session admin non révocable** — `authenticate()` ne testait que la présence de
   l'identifiant en session : rétrograder, désactiver ou supprimer un compte ne fermait pas les
   sessions ouvertes (cookie de 14 jours). Le compte est maintenant revalidé à chaque requête.
4. **Garde-fou `SECRET_KEY` fail-open** — `environment` valait `"development"` par défaut : un
   déploiement oubliant `ENVIRONMENT` signait jetons et cookies avec une clé publiée dans le
   dépôt. Le défaut est désormais `"production"`, donc l'oubli fait échouer le démarrage.
   `env.example` et `docker-compose.yml` déclarent explicitement l'environnement de dev.
5. **`env.example` restauré en double** (41 lignes au lieu de 20) — réécrit proprement, et
   complété avec `SECRET_KEY` et les ports hôte.
6. **Critère A5 non couvert** — `test_migrations.py` ne comparait que des noms de tables : une
   migration oubliant `project.owner_id` passait au vert. Ajout de
   `test_upgrade_head_creates_every_column_of_every_model`, qui compare colonne par colonne.
7. **Adresses e-mail sensibles à la casse** — `Case@ex.fr` et `case@ex.fr` créaient deux comptes,
   et l'utilisateur se retrouvait verrouillé hors du sien. Normalisation en minuscules à
   l'inscription et à la connexion.
8. **L'amendement cassait le tableau §6 de la spec** — le bloc était inséré au milieu du tableau
   Markdown, masquant les trois lignes suivantes. Déplacé après le tableau.

Nuance relevée par la revue et corrigée dans la spec : la casse de `passlib` est effective avec
`bcrypt` **5.0** (avec 4.1, l'erreur est encore rattrapée en interne). La conclusion ne change
pas.

### P5 — Catalogue `FurnitureType` paramétrique · **fait**

31 recettes de composition couvrant l'intégralité du tableau §4.3, API de consultation et
d'administration, chargement idempotent.

**Critères d'acceptation**

| Critère | État | Vérification |
|---|---|---|
| Chaque ligne du tableau §4.3 a une entrée | ✅ | `test_the_catalog_covers_the_whole_spec_table` (tableau de la spec recopié dans le test comme référence indépendante) |
| Toutes les recettes sont valides | ✅ | `test_every_catalog_entry_is_a_valid_recipe` |
| Vasque, baignoire, bac de douche déclarent une soustraction (§4.2) | ✅ | `test_the_bathroom_pieces_that_need_csg_declare_a_subtraction` |
| La commode est fidèle à l'exemple §4.1 | ✅ | `test_the_commode_matches_the_spec_example` |
| Seed idempotent | ✅ | `test_seeding_twice_creates_no_duplicate` + vérifié sur la stack (31 créées au premier démarrage) |
| Lecture authentifiée, écriture superutilisateur | ✅ | `test_a_regular_user_cannot_write_the_shared_catalog` |
| Recette invalide refusée par l'API | ✅ | `test_an_invalid_recipe_is_refused_by_the_api`, `test_an_update_cannot_desynchronise_slots_and_parts` |

Suite complète : **131 tests sur SQLite**, `ruff` et `mypy --strict` verts.

**Défaut trouvé par les tests, pas par relecture** : le validateur « `auto` seulement sur un axe
répété » a détecté une recette fausse que j'avais écrite (le canapé plaçait `auto` sur l'axe y
alors que les coussins se répètent sur x). Sans ce garde-fou, le défaut n'aurait été visible
qu'au rendu 3D en P7.

**Ordre** : P5 traité avant P4, ce que `plan-generation-ia.md` §5 autorise explicitement
(« P5 peut démarrer en parallèle de P2–P4 »), et qui débloque P6.

### P3 — corrections après revue adversariale

La revue a rendu **À CORRIGER**, dont un défaut bloquant. Tout est corrigé :

1. **BLOQUANT — perte d'intégrité et déni de service persistant.** `ElementUpdate` redéclarait
   `colors: dict[str, str]` sans reporter la validation des couleurs. `PATCH` avec
   `{"colors": {"corps": "rouge"}}` écrivait la valeur en base, *puis* faisait échouer la
   sérialisation — et ensuite **toute** lecture traversant cet élément (`GET /projects/{id}`,
   `/rooms/{id}`, `/rooms/{id}/faces`) renvoyait 500. Un compte authentifié rendait donc son
   propre projet illisible en une requête, sans plus aucun moyen de retrouver l'élément fautif
   par l'API. Corrigé par un type `ColorSlots` partagé entre création et mise à jour.
2. **Validations de couleur divergentes** : `#zzzzzz`, `#      `, `#<b>abc` étaient acceptés sur
   un emplacement de meuble et refusés sur un revêtement. Type `HexColor` unique pour toute l'API.
3. **Le verrouillage optimiste ne couvrait rien du plan** — `version` n'était incrémentée que par
   `PATCH /projects` : créer une pièce, poser un revêtement ou un meuble laissait la version
   inchangée, donc « dernière écriture gagne » sur le plan lui-même, exactement l'option écartée
   par §8 cas 3. Toutes les écritures passent désormais par `_claim_project`, qui vérifie la
   version transmise et marque le projet modifié. Effet de bord corrigé au passage : un projet
   activement édité remonte enfin en tête de la liste triée par `updated_at`.
4. **Appariement des murs positionnel, donc faux** : insérer un sommet en tête du polygone
   décalait tous les rangs, et chaque mur héritait du revêtement et des meubles de son voisin —
   avec des éléments se retrouvant hors du mur qui les portait. Les murs sont maintenant
   appariés par leur **géométrie** ; seul le lettrage reste positionnel.
5. **Sol et plafond jamais supprimés** : vider un polygone laissait deux faces orphelines, alors
   qu'une pièce créée avec un polygone vide n'en a aucune. Deux pièces dans le même état avaient
   donc des faces différentes selon leur historique.
6. **Réduire un polygone détruisait silencieusement les meubles posés**, en `200 OK`. L'opération
   est désormais refusée (409) tant que `force: true` n'est pas envoyé.
7. **`ConflictDetail` était du code mort** : le schéma est branché dans les `responses` des
   routes d'écriture, donc publié dans l'OpenAPI — source de vérité du frontend.
8. **Le 409 de `StaleDataError` n'émettait pas `X-Current-Version`** (le seul cas réellement
   concurrent, donc celui où le client en a le plus besoin).
9. **Blobs JSON non bornés** : `variant_params` acceptait 3 Mo par élément. Bornés en nombre de
   clés et en types.
10. **Aucune validation géométrique des éléments** : une porte 9999×9999 à `x=99999` sur un mur
    de 400 cm était acceptée, et c'est le scene graph (P6) qui l'aurait découvert.
11. **`covering: null`** était un no-op silencieux : l'effacement d'un revêtement est désormais
    possible.
12. **Documentation inexacte** : « 19 routes » comptait aussi celles de l'auth ; l'API du plan en
    expose **14**. `FaceUpdate` mentionnait une hauteur qui n'existe pas.

19 tests de non-régression ajoutés, un par défaut. Suite : **150 tests sur SQLite, 151 sur
PostgreSQL**.

### P6 — Scene graph 3D côté backend · **fait**

Calcul complet de la scène (murs extrudés avec trous, sol, plafond, mobilier développé, 7 presets
de caméra) exposé par `GET /api/projects/{id}/scene`.

**Méthode** — les fixtures de `backend/tests/geometry/fixtures/` ont été calculées **à la main
avant l'implémentation**, chacune accompagnée du raisonnement qui produit ses valeurs (champ
`reasoning`). C'est la contre-mesure exigée par `plan-generation-ia.md` §6 : des valeurs
attendues issues du code lui-même ne prouveraient rien.

**Résultat de la confrontation** : 17 des 18 tests sont passés au premier essai. Le seul écart
était réel — `rotation_y` sortait à `-π` là où la fixture attend `+π`, parce que la négation d'un
`0.0` donne `-0.0` et fait basculer `atan2`. Les deux valeurs décrivent la même rotation, mais la
sortie n'était pas canonique : un même mur pouvait sortir tantôt à `π`, tantôt à `-π`, ce qui
aurait cassé le cache de P10. Conformément à `CLAUDE.md`, **c'est le code qui a été corrigé**, pas
la fixture.

**Critères d'acceptation**

| Critère | État | Vérification |
|---|---|---|
| La pièce de référence produit le scene graph attendu | ✅ | `test_a_bare_room_matches_its_reference_fixture` (comparaison récursive champ par champ) |
| Une ouverture devient un trou, pas un objet | ✅ | fixture 02 : `holes` conforme, 0 nœud de mobilier |
| Une recette paramétrique se développe conformément à la fixture | ✅ | fixture 03 : 9 primitives, décalages `-25.5 / -8.5 / +8.5 / +25.5` |
| Le sens de saisie du polygone est sans conséquence | ✅ | fixture 04 : scène identique en horaire et en trigonométrique |
| Un preset de caméra par face + 3 vues d'ensemble | ✅ | `test_every_face_has_its_own_camera_preset` |
| Les vues par face regardent depuis l'intérieur | ✅ | produit scalaire caméra→mur / normale sortante > 0 |
| API authentifiée et cloisonnée | ✅ | `tests/test_scene_api.py` |
| JSON stable entre deux appels | ✅ | `test_the_scene_graph_is_stable_between_two_calls` |

Vérifié aussi sur la stack réelle : 6 nœuds, 7 caméras, aire 120000 cm², mur A d'origine
`(0,0,0)` et de normale `(0,0,-1)`.

Suite complète : **176 tests sur SQLite, 177 sur PostgreSQL**, `ruff` et `mypy --strict` verts.

**Arbitrages de la spec respectés** : calcul synchrone (§8 cas 2 — la migration vers Celery et sa
mesure sont le sujet de P9) ; approche `THREE.Shape` + trous plutôt que CSG (§8 cas 5), le CSG
n'étant que *signalé* par `requires_csg` pour les meubles de §4.2 ; aucun cache (§8 cas 6 → P10).

### P4 et P7 — éditeur 2D et viewer 3D · **fait**

Traités ensemble : ils partagent l'ossature du frontend (client HTTP typé, routeur, stores,
styles). Les livrer séparément aurait imposé de la construire deux fois.

**Éditeur 2D (P4)** — canvas Konva avec tracé de polygone au clic, fermeture du contour,
magnétisme sur grille réglable, déplacement de sommets, cotes et lettrage affichés en direct,
détection de contour auto-sécant, panneau de pose des revêtements et des éléments.

**Viewer 3D (P7)** — traduction du scene graph en objets Three.js via TresJS, 7 presets de
caméra, liste de faces avec les trois états visible / transparente / masquée, isolement d'une
face (qui bascule aussi sur sa caméra d'élévation), capture PNG de la vue courante (§3.5).

**Critères d'acceptation**

| Critère | État | Vérification |
|---|---|---|
| Lettrage client identique au backend | ✅ | `editor/geometry.spec.ts` (mêmes cas que le test Python) |
| Conversion plan ↔ écran réversible, magnétisme | ✅ | `editor/geometry.spec.ts` |
| Contour auto-sécant détecté | ✅ | nœud papillon détecté, pièce en L acceptée |
| Trois états de visibilité conformes à §3.4 | ✅ | `viewer/visibility.spec.ts` |
| Isolement par transparence, pas par masquage | ✅ | `viewer/visibility.spec.ts` |
| Ouvertures → trous dans la forme Three.js | ✅ | `viewer/geometry.spec.ts` |
| Aucun chemin inventé par le client | ✅ | `api/contract.spec.ts` + job CI `contrat-api` |
| build / test / lint verts | ✅ | 44 tests, build 261 ms, eslint propre |

**Le garde-fou anti-hallucination du frontend est rendu exécutable.** `plan-generation-ia.md` §6
prévoit que l'OpenAPI serve de source de vérité ; c'était jusqu'ici une intention. Désormais :
un instantané du schéma est versionné, un test confronte chaque chemin appelé par `client.ts` à
cet instantané, et un job CI régénère l'instantané depuis le backend pour échouer s'il a dérivé.
Sans ce dernier maillon, le test aurait pu valider indéfiniment un contrat périmé.

**Défaut trouvé par ce test, pas par relecture** : `listFurnitureTypes` interpolait toute la
chaîne de requête dans le chemin (`/api/furniture-types${query}`), ce qui rendait le contrat
invérifiable statiquement. Corrigé par un helper `withQuery` qui garde les chemins littéraux.

**Non-objectifs tenus** : aucun partage de vue (P8), aucun export PDF (P9), aucun CSG réel — les
primitives `subtract` sont ignorées à l'affichage plutôt que rendues en plein, ce qui donnerait
une baignoire pleine au lieu de creuse.

### P6 — corrections après revue adversariale

La revue a rendu **À CORRIGER** avec 3 défauts bloquants, trouvés par recalcul indépendant des
fixtures et par injection de 13 mutations (les 13 avaient été détectées par les tests existants —
mais aucun test ne couvrait les zones défaillantes ci-dessous).

1. **BLOQUANT — le sens de saisie du polygone avait bien une conséquence** (critère A4 faux).
   `ensure_counter_clockwise` n'était appliqué qu'au champ `polygon`, qui ne sert qu'au sol, au
   plafond, aux caméras d'ensemble et à l'aire. Les murs, eux, sont construits à partir des
   segments stockés, jamais réorientés. Sur une pièce dessinée dans le sens horaire, **les quatre
   normales pointaient vers l'intérieur et les quatre caméras étaient hors du logement**.
   Corrigé : l'orientation du contour est propagée aux segments de mur.
2. **BLOQUANT — le test A4 ne testait pas A4.** Il ne remplaçait que le polygone en laissant les
   faces intactes (entrée incohérente que personne ne peut saisir), puis ne comparait que les
   caméras — dont les valeurs sont invariantes par inversion. Il passait quoi qu'il arrive.
   Remplacé par trois tests qui retournent réellement la pièce et vérifient les normales,
   l'appartenance des caméras à la pièce, et l'aire.
3. **BLOQUANT — sol et plafond en miroir.** `R_x(-π/2)` envoie le point local `(u, v, 0)` sur
   `(u, 0, -v)` : le contour était posé sur des `z` négatifs, donc **sous aucun mur**, et le
   mobilier au sol atterrissait dans le demi-espace opposé. C'était la seule valeur de fixture
   que le champ `reasoning` ne dérivait pas — d'où le fait qu'elle ait traversé la première
   passe. Le contour est désormais émis avec son `y` négué.
4. **Caméras d'élévation hors de la pièce** : la distance de recul valait `max(longueur,
   hauteur)`, sans rapport avec la profondeur de la pièce. Elle est maintenant bornée par la
   profondeur réellement disponible (80 %).
5. **Mobilier au sol mal placé** : les décalages étaient traités comme des coordonnées absolues
   du plan, alors que la validation amont les borne comme des offsets relatifs à la pièce. Une
   pièce éloignée de l'origine plaçait ses meubles à des mètres de là.
6. **Fixture 04 : une valeur attendue morte et fausse** — `expected_normalized_polygon` supposait
   une rotation du point de départ que le code ne fait pas. Aucun test ne la lisait, ce qui
   explique qu'elle soit passée inaperçue. Corrigée et désormais assertée.
7. **`assert_matches` ne comparait qu'un sous-ensemble** : un champ ajouté par le code passait
   inaperçu. Comparaison stricte des clés ajoutée.
8. **Précision des angles** portée de 4 à 9 décimales : à 1e-4 rad près, un mur de 10 m dérive
   d'environ 1 cm à son extrémité.

**Modification de fixtures — assumée et documentée.** `CLAUDE.md` interdit d'ajuster une fixture
pour faire passer un test. Ici la revue a **démontré** que deux valeurs étaient fausses (sol en
miroir, caméras hors de la pièce) avant toute modification du code. Les valeurs corrigées sont
redérivées à la main dans le champ `reasoning`, et un bloc `revision` de la fixture 01 explique
ce qui a changé et pourquoi. Aucune fixture n'a été touchée pour accommoder du code.

**Non-vacuité vérifiée** : en réinjectant chacun des quatre défauts, les tests correspondants
échouent (1 → 2 tests, 2 → 2 tests, 3 → 2 tests, 4 → 1 test).

Suite : **180 tests sur SQLite, 181 sur PostgreSQL**, `ruff` et `mypy --strict` verts.

### P8 — Partage de vue · **fait**

Lien permalien en lecture seule, sans authentification, avec révocation et expiration
optionnelle. Page publique dédiée côté frontend.

**Critères d'acceptation**

| Critère | État | Vérification |
|---|---|---|
| Création, liste et révocation par le propriétaire | ✅ | `test_the_owner_can_create_and_revoke_a_share` |
| Lecture sans authentification | ✅ | test + `curl` sans en-tête sur la stack réelle |
| Aucune fuite d'information sur le propriétaire | ✅ | test dédié + vérification sur la stack (`owner`, `email`, `version`, `updated_at`, `project_id` tous absents) |
| Jeton imprévisible | ✅ | 43 caractères, 256 bits d'entropie, 5 jetons tous distincts |
| Endpoint public limité en débit | ✅ | `test_the_public_endpoint_is_rate_limited` |
| Lien expiré indiscernable d'un lien inexistant | ✅ | même code **et** même corps de réponse |
| Cloisonnement entre comptes | ✅ | 404 sur partage et révocation d'autrui |
| `state` invalide refusé | ✅ | 5 cas paramétrés |

Suite : **197 tests backend**, 44 tests frontend, `ruff` / `mypy --strict` / build verts.

**Les trois contraintes de la spec §3.5 sont traitées comme la fonctionnalité elle-même** — un
endpoint public sans jeton imprévisible, sans limitation de débit et sans filtrage des données
serait une régression de sécurité déguisée en fonctionnalité.

### P9 — Export PDF et tâches Celery · **fait**

PDF vectoriel (une page par pièce : plan coté + détail par face), généré par un worker Celery,
avec le chemin synchrone conservé comme référence.

**Mesure exigée par §8 cas 2** — relevée sur la stack réelle, projet de 8 pièces :

| Chemin | Réponse HTTP perçue (5 mesures après chauffe) |
|---|---|
| Synchrone (`/exports/pdf/direct`) | min 17,2 · **médiane 22,6** · max 45,0 ms |
| Asynchrone (Celery, vrai worker + Redis) | min 3,1 · **médiane 3,2** · max 4,4 ms |

**Celery divise la latence perçue par ~7.** La requête rend la main dès la mise en file, quelle
que soit la durée du rendu, là où le chemin synchrone la suit linéairement.

> **Mesure corrigée après revue.** Une première version de ce tableau annonçait 33,7 ms en
> synchrone contre 42,4 ms en asynchrone, et en concluait que « Celery ne fait pas gagner de
> temps ». C'était **faux** : un unique appel à froid, incluant la première connexion au broker.
> La revue adversariale a refait la mesure avec chauffe et cinq échantillons et obtenu la
> conclusion inverse. C'est précisément le piège que §8 cas 2 cherche à éviter — mesurer plutôt
> que supposer ne sert à rien si la mesure est faite une fois, à froid.

**Critères d'acceptation**

| Critère | État | Vérification |
|---|---|---|
| PDF valide et reproductible | ✅ | en-tête `%PDF-`, fin `%%EOF`, taille croissant avec le plan |
| Export asynchrone immédiat produisant un fichier | ✅ | 202 en 42 ms, puis `SUCCESS`, 8148 octets téléchargés |
| Téléchargement cloisonné et résistant à la traversée de chemin | ✅ | 4 cas paramétrés + test inter-comptes |
| Gain mesuré | ✅ | tableau ci-dessus, en-tête `X-Generation-Ms` |
| Routes authentifiées et cloisonnées | ✅ | tests dédiés |

Suite : **211 tests backend**, `ruff` et `mypy --strict` verts.

**Défaut trouvé uniquement en exécutant la vraie stack** : le worker tourne en non-root, et un
volume Docker nommé hérite des droits du répertoire de l'image — `/exports` appartenait à root,
donc `PermissionError` à la première écriture. Les tests en mode `task_always_eager` ne pouvaient
pas le voir : ils écrivent ailleurs, sous une autre identité. Corrigé dans le `Dockerfile`. C'est
l'illustration de la règle du `CLAUDE.md` : un ticket n'est pas fini sur la seule foi des tests.

### P10 — Passe performance · **fait**

**§8 cas 4 — le N+1, mesuré avant d'être corrigé.** Un compteur de requêtes SQL remplace la
lecture de `echo=True` à l'œil : la mesure devient une assertion.

| Chargement | 1 pièce | 3 pièces | 6 pièces |
|---|---|---|---|
| Naïf (relations chargées à la demande) | croît | croît | croît |
| Anticipé (`selectinload`) | **4 requêtes** | **4 requêtes** | **4 requêtes** |

Le premier test *documente le problème* plutôt que de protéger un comportement : il échouerait si
quelqu'un supposait à tort que SQLAlchemy résout le N+1 tout seul. Vu depuis l'API,
`GET /api/projects/{id}` et `GET /api/projects/{id}/scene` émettent le même nombre de requêtes
pour 1 et pour 6 pièces.

**§8 cas 6 — cache du scene graph, mesuré sur la stack réelle avec Redis :**

| Appel | En-tête `X-Cache` | Durée |
|---|---|---|
| 1ᵉʳ appel | `miss` | 17,0 ms |
| 2ᵉ appel | `hit` | **2,9 ms** (≈ 6× plus rapide) |
| Après édition du plan | `miss` | recalculé |

**L'invalidation est structurelle, pas déclarative.** La clé porte la version du projet
(`scene:1:v9`, `scene:1:v10`) : une écriture change la version, donc la clé, donc l'entrée visée.
Il n'y a aucun `delete` à placer sur chaque chemin d'écriture — et donc aucun à oublier quand on
en ajoutera un. Le prix assumé : les entrées périmées restent en mémoire jusqu'à expiration (TTL
d'une heure), ce que la lecture des clés Redis confirme.

Seul cas nécessitant une purge explicite : la **suppression** d'un projet, où aucune version
future ne viendra rendre les anciennes clés inatteignables.

**Critères d'acceptation**

| Critère | État | Vérification |
|---|---|---|
| N+1 reproduit et mesuré | ✅ | `test_the_naive_loading_really_produces_an_n_plus_one` (3 tailles) |
| Chargement anticipé constant | ✅ | exactement 4 requêtes, quelle que soit la taille |
| Requêtes de l'API indépendantes de la taille du plan | ✅ | 2 tests |
| Clé portant la version | ✅ | test + clés observées dans Redis |
| Édition immédiatement reflétée | ✅ | `test_the_scene_reflects_an_edit_immediately` |
| Panne Redis sans conséquence | ✅ | `test_the_cache_degrades_gracefully_when_redis_fails` |

**Un seul index ajouté** — `(owner_id, updated_at)` — calqué sur la requête réelle de la liste
des projets. Indexer « au cas où » coûte à chaque écriture pour un gain hypothétique ; §8 cas 1
reste appliqué (géométrie en JSON, aucun besoin de requête n'ayant émergé).

Suite : **224 tests backend**, `ruff` et `mypy --strict` verts.

### P11 — Tests d'intégration et cas limites · **fait**

14 tests suivant des **parcours complets** plutôt que des couches isolées, plus les coins du
système.

| Famille | Couverture |
|---|---|
| Parcours nominal | inscription → connexion → projet → pièce en L → revêtement → fenêtre → meuble → scène 3D → partage public → export PDF |
| Géométrie limite | pièce en L, polygone de 30 sommets (lettrage au-delà de Z : « AA » vérifié), coordonnées négatives, pièce à 5 000 cm de l'origine, polygone trop grand refusé |
| Cycles de vie | suppression de compte effaçant projets, pièces, faces et éléments (RGPD) ; projet vide et pièce sans contour traversant scène et export sans casser |
| Concurrence | 10 lectures simultanées renvoyant exactement la même scène ; deux écritures parties de la même version, la seconde refusée |
| Cohérence inter-couches | plan et scène décrivant toujours les mêmes faces ; les 3 types d'ouverture devenant des trous et jamais des objets |

### P12 — Durcissement du déploiement · **fait**

**Principe directeur : la configuration échoue plutôt que de démarrer mal.**

| Point | Développement | Production |
|---|---|---|
| `SECRET_KEY`, `CORS_ORIGINS`, identifiants DB | valeurs de développement | **obligatoires** — `${VAR:?}` refuse le démarrage |
| `/docs`, `/redoc`, `/openapi.json` | exposés | **fermés** |
| Frontend | Vite avec rechargement à chaud | build statique, nginx non-root sur 8080 |
| Base et Redis | ports publiés | **aucun port publié** |
| Code source | monté depuis le disque | figé dans l'image |
| HSTS | absent (service en clair) | un an, sous-domaines inclus |

En-têtes vérifiés sur la stack réelle : `X-Content-Type-Options`, `X-Frame-Options`,
`Referrer-Policy`, `Permissions-Policy`, et une CSP `default-src 'none'` pour l'API — avec une
politique distincte pour le back-office, qui sert ses propres feuilles et scripts.

Un job CI dédié vérifie **les deux sens** : que la configuration de production est valide, et
qu'elle **refuse** de démarrer quand `SECRET_KEY` manque.

`CORS_ORIGINS` accepte désormais `https://a.fr,https://b.fr` en plus du JSON : c'est la forme
qu'on écrit spontanément, et `pydantic-settings` seul l'aurait rejetée au démarrage — un échec
qui ne se serait manifesté qu'en production.

**Reste-à-faire d'infrastructure, listé explicitement dans le README** plutôt que passé sous
silence : terminaison TLS, sauvegardes PostgreSQL et test de restauration, limitation de débit
partagée via Redis (celle en place vit dans la mémoire de chaque processus, donc se dilue avec
plusieurs workers), et supervision Sentry.

Suite : **254 tests backend**, 44 tests frontend, `ruff` et `mypy --strict` verts.

---

## Vague 2 — multi-locataire, métré, devis et documents de chantier · **fait**

*Quatre lots menés en parallèle avec propriété exclusive des fichiers, puis un agent d'assemblage
dont c'était le seul travail (`docs/plan-generation-ia.md` §4). Décisions de contrat consignées
dans `docs/spec-complete.md` §10 (amendements A1 à A3).*

| Lot | Contenu |
|---|---|
| **V2-L1** | `organization` / `membership` / `invitation`, quatre rôles ordonnés, invitations à jeton non rejouable (la base ne garde qu'un SHA-256). `app/api/permissions.py` réécrit : l'appartenance **acceptée** remplace `Project.owner_id`, qui n'autorise plus rien. Migration avec rétro-remplissage en trois temps — une organisation personnelle par propriétaire distinct, l'appartenance `owner` correspondante, puis `NOT NULL`. |
| **V2-L2** | `app/geometry/quantities.py::build_takeoff` — fonction pure, 4 fixtures calculées à la main. Surfaces nettes, linéaires au **nu intérieur** (et non sur l'axe), plinthe amputée des percements qui touchent le sol, calepinage par motif avec chutes, unités entières et coupes comptées trame par trame. |
| **V2-L3** | `services/export_pdf.py` réécrit : une planche A4 paysage par mur, chaînes de cotes, allèges, échelle **normalisée et écrite**, cartouche, page de garde. Les 4 routes d'export ont enfin un appelant côté frontend. |
| **V2-L4** | `price_book` / `price_item` / `face_costing` / `quote` / `quote_line` / `quote_counter`, moteur de chiffrage pur, correspondance `material → price_item` en 4 paliers, et Factur-X (PDF/A-3 + XML CII BASIC WL) produit sans aucune dépendance ajoutée. 19 routes. |

**Les trois invariants d'argent, tenus par des tests dédiés** : tout montant est un entier de
centimes ; la TVA est arrondie une seule fois, par taux, sur la somme des bases (`vat_buckets_from`
est le seul endroit du dépôt qui l'arrondit) ; et un `price_item` modifié ou supprimé laisse un
devis déjà émis **strictement** inchangé — `quote_line` copie, elle ne joint jamais.

**La régression qui n'était pas permise : la fuite entre locataires.** Le test s'écrit avant la
réécriture des permissions, pas après. `tests/test_permissions_locataire.py` confronte 47 routes
authentifiées à un membre d'une autre organisation — toutes répondent 404, jamais 403 — et le fait
avec deux garde-fous de complétude qui lisent le schéma OpenAPI publié :

- une route portant un identifiant de locataire et absente de la liste fait échouer la suite ;
- une route **de liste**, qui n'en porte aucun et par laquelle une fuite serait totale, doit être
  classée à la main entre « appartient à un locataire » et « globale ». C'est ce second garde-fou
  qui a révélé que `GET /api/quotes` — l'identité des clients de l'artisan et ses prix, d'un seul
  appel — n'avait aucun test de non-fuite. Il en a un, et il a été vérifié en cassant le filtre
  exprès : le test échoue bien.

### Assemblage — ce que la réconciliation a corrigé

- **La page de garde du dossier de plans et le métré donnaient deux surfaces différentes pour la
  même pièce** (12,00 m² contre 11,31 m²) : le PDF mesurait l'aire du contour saisi, c'est-à-dire
  la ligne médiane des murs, surévaluée de 6 à 20 %. Les deux lisent désormais la même fonction,
  `geometry.scene.net_floor_area`, rendue publique pour cela plutôt que dupliquée.
- **Les élévations étiquetaient tout meuble « Meuble »** : le chargement de l'export ne lisait que
  `furniture_type_id`. Le nom du catalogue est joint en une requête pour tout le projet.
- Instantané OpenAPI régénéré (**45 chemins**, 21 nouveaux pour 31 opérations), `Project.organization_id` ajouté au
  back-office, docstring de `models/user.py` corrigée (elle désignait encore `owner_id` comme le
  socle des permissions), fixtures 07 à 10 documentées dans leur `README.md`.
- **Une seule tête Alembic** (`4c1e8b7a92d5`), vérifiée sur PostgreSQL 17 : aller-retour complet
  jusqu'à `base` sans type ENUM ni table résiduels, `alembic check` sans dérive, et
  rétro-remplissage confronté à des données réellement présentes — deux projets d'un même
  propriétaire atterrissent bien dans une seule organisation, et aucun locataire n'est fusionné.

Suite : **709 tests backend** (708 sur SQLite, le 709ᵉ ne s'exécutant que sur PostgreSQL), **96
tests frontend**, `ruff`, `mypy --strict`, `eslint` et `npm run build` verts.

---

## Vague 3 — éditeur professionnel : mobilier libre, fidélité 3D, relevé au clavier et à la souris · **fait**

Trois lots en parallèle, propriété exclusive des fichiers, puis assemblage. Les décisions de
contrat sont écrites dans `docs/spec-complete.md` §10, **amendements A4, A5 et A6, rédigés avant
le code** comme `CLAUDE.md` l'exige.

| Lot | Contenu |
|---|---|
| **V3-L1** | **Le verrou du modèle sauté.** `Element.face_id` devient nullable, `room_id` / `pos_x_cm` / `pos_y_cm` apparaissent : un lit, une table, un îlot n'étaient pas mal placés, ils étaient **impossibles**. Trois `CheckConstraint` en base — exactement un ancrage, coordonnées comprises ; une ouverture exige une face ; position bornée — parce que les `Field` de SQLModel sont inertes sur `table=True` et que SQLAdmin, Celery et `psql` écrivent sans passer par Pydantic. Propriété `Element.anchor_room` comme point de passage unique : `element.face.room.project` était partout et lève un `AttributeError` sur le premier meuble libre venu. Fond de plan calibré sur `Room` (6 colonnes, `background_url` validée côté serveur — OWASP A03). Route de lot `POST /projects/{id}/batch`. |
| **V3-L2** | **Ce qui est rendu correspond enfin à ce qui est modélisé.** CSG branché (`three-bvh-csg`, importé dynamiquement et uniquement si la scène contient un nœud `requires_csg`), étanchéité de la géométrie vérifiée **avant** évaluation, repli sur la primitive pleine plutôt qu'un résultat troué. Éclairage ACES et environnement PMREM procédural, ombres portées. Textures de revêtement générées par motif de pose. Isolement multi-faces, coupe horizontale, masquage des murs qui font écran. Mode logement complet. `ResourcePool` qui possède et libère tout : les fuites GPU étaient réelles et mesurées. |
| **V3-L3** | **L'éditeur devient un outil de relevé.** Glisser-déposer depuis une palette métier, l'ancrage se décidant à la dépose. Annuler/refaire exprimé en **appels serveur inverses**. Magnétisme à trois priorités avec guides, saisie numérique des cotes, longueurs de mur éditables. Sélection multiple et gestes groupés, tous par la route de lot. Fond de plan et calibrage à deux clics. Chaque geste souris a son pendant clavier (WCAG AAA). |

**Ce que la route de lot change vraiment.** Déplacer quinze meubles imposait quinze allers-retours
strictement sériels, chacun invalidant la version détenue par le client. Le lot est **une**
transaction et **une** incrémentation de version : sept opérations en union discriminée sur `op`,
bornées à 100, tout ou rien, résultats rendus dans l'ordre d'envoi. Une opération refusée annule le
lot et **nomme son rang**. Chaque identifiant du corps est revérifié contre le projet de l'URL en
deux requêtes de préchargement — pas de N+1, et surtout pas de porte dérobée : c'est la route la
plus dense en identifiants fournis par le client, donc celle où une vérification manquante
coûterait le plus cher.

**La fixture 11 fige ce que le code ne peut pas prouver seul.** `11_mobilier_libre.json` est la
seule fixture où les deux ancrages coexistent — un radiateur adossé au mur A, un lit libre au
centre, une table libre tournée à 90° — calculée à la main avec son `reasoning`. Elle attrape les
trois confusions possibles entre les repères : le placement d'un meuble libre est exprimé dans le
repère du **plan** et non relativement au coin de la boîte englobante comme sur la face SOL ;
`face_label` vaut `null`, ce qui le tient hors de tout groupe de face et donc hors de l'isolement ;
et l'**ordre d'émission** est stable, le cache de scène (P10) s'appuyant sur la sortie octet pour
octet. Aucune fixture existante n'a été modifiée.

**Quatre défauts trouvés par les agents dans leur propre travail, et corrigés.** Le test
d'intersection segment/segment refusait un meuble poussé **pile contre** le mur — le geste le plus
courant du métier — parce qu'un test d'intersection ordinaire compte un contact comme un
croisement ; remplacé par le croisement franc, miroir exact de `_segments_cross` côté backend. Un
sommet en cours de déplacement figurait dans ses propres candidats d'accroche et devenait
incorrigible. Un seuil d'adossement fixe à 45 cm couvrait toute la surface d'un couloir de 90 cm,
rendant la pose libre impossible dans un couloir — cas banal en rénovation. Et une annulation
refusée par le serveur faisait glisser son entrée dans la branche « refaire », proposant de rejouer
un geste que le serveur venait de refuser.

### Assemblage — ce que la réconciliation a corrigé

- **Le back-office ne purgeait pas le cache de scène d'un meuble libre.** `admin.py::_project_id_of`
  remontait au projet par `Element.face_id` seul ; nul sur un meuble libre, `session.get(Face, None)`
  rend `None` **sans lever** — seulement un `SAWarning`. Corriger un meuble libre depuis
  l'administration laissait donc servir l'ancienne scène pendant toute la durée du cache. C'est le
  genre de défaut que rien ne signale : il a été trouvé parce que **deux agents l'ont signalé sans
  pouvoir y toucher**. Le test qui le couvre a été vérifié en le rejouant contre le code fautif,
  où il échoue bien. `Element.room_id` rejoint aussi la liste du back-office, qui n'affichait que
  `face_id` et laissait la moitié des lignes sans rattachement visible.
- **Les types du lot promettaient au compilateur ce que le serveur refuse.** Les corps d'opération
  sont validés en `extra="forbid"` : un `version` glissé dans le corps d'un élément n'aurait pas
  été ignoré, il aurait fait refuser le lot entier en 422. `BatchOperation` retire désormais
  `version` de chaque variante — et `force` de `create_room`, que seul `RoomPatch` accepte. Aucun
  appelant ne les envoyait ; la règle est maintenant tenue par le compilateur et non par la
  vigilance.
- **L'énoncé de la question ouverte n° 6 était faux** et aurait envoyé quelqu'un corriger du code
  correct : le métré ne « parcourt » pas les éléments par face, il ne les lit pas du tout. Il
  chiffre des surfaces, des linéaires et du calepinage, et n'a jamais itémisé de mobilier, libre ou
  adossé. Le vrai manque est le dossier d'élévations, dont le récapitulatif de pièce **sous-compte**
  les éléments. La question a été réécrite pour dire cela.
- Instantané OpenAPI régénéré et diffé à vide (**47 chemins**, 2 nouveaux), fixture 11 documentée
  dans `tests/geometry/fixtures/README.md`.
- **Une seule tête Alembic** (`e7b3c05f1a62`), `upgrade` → `downgrade` → `upgrade` rejoué sans
  résidu et `alembic check` sans dérive aux deux extrémités. Le `downgrade` **refuse de s'exécuter**
  s'il reste du mobilier libre, et donne les identifiants : remettre `face_id NOT NULL` en
  supprimant les meubles en silence est une perte de données, pas une migration.
- **Les deux nouvelles routes sont classées** dans `tests/test_permissions_locataire.py` : 49 routes
  confrontées à un membre d'une autre organisation, plus 3 collections et 9 routes globales. Le
  garde-fou de complétude qui lit l'OpenAPI publié est repassé au vert de lui-même.

**Ce qui n'a pas été vérifié, et qu'il ne faut pas présenter comme fait** : aucun pixel n'a été
observé. Il n'y a pas de navigateur dans cet environnement, donc le tone mapping, les ombres, les
textures, le glisser-déposer, les guides d'aimantation et le fond de plan calibré n'ont **jamais
été vus**. Les tests couvrent la logique et le montage, pas le rendu WebGL ni les évènements
pointeur réels de Konva. C'est le premier point de la reprise (`docs/REPRISE.md`).

Suite : **787 tests backend** (786 sur SQLite, le 787ᵉ ne s'exécutant que sur PostgreSQL), **368
tests frontend** sur 23 fichiers, `ruff`, `mypy --strict`, `eslint` et `npm run build` verts.
