# Plan de génération du projet par IA (Claude Code)

*Complète `spec-complete.md` (le contrat fonctionnel/technique) avec le **comment** : la méthode pour faire construire ce projet par un agent IA sans qu'il n'hallucine ni ne parte dans tous les sens. Écrit pour Claude Code, mais les principes s'appliquent à tout agent codant en autonomie.*

---

## 0. Le problème n'est pas hypothétique

Ce n'est pas une inquiétude vague : Anthropic documente explicitement les modes d'échec d'un agent codant en autonomie, avec leurs correctifs. Deux faits structurent tout ce plan :

1. **La fenêtre de contexte se dégrade.** Plus une session s'allonge, plus l'agent "oublie" des instructions données plus tôt et fait plus d'erreurs. Une seule session de debug peut consommer des dizaines de milliers de tokens.
2. **Sans moyen de vérifier, "ça a l'air fini" est le seul signal disponible.** L'agent s'arrête quand le résultat *semble* correct, pas quand il l'est. C'est la faille numéro un : *"the trust-then-verify gap"* — l'agent produit une implémentation plausible qui ne gère pas les cas limites, et personne ne le remarque avant la revue.

Tout ce qui suit découle de ces deux faits : des sessions courtes et cadrées (contre la dégradation de contexte), et un moyen de vérification exécutable à chaque étape (contre le trust-then-verify gap).

---

## 1. Les documents qui font foi

| Document | Rôle |
|---|---|
| `spec-complete.md` | Le contrat fonctionnel et technique. Toute divergence passe par une modification explicite de ce fichier, jamais par une décision silencieuse en cours de code. |
| `CLAUDE.md` (fourni, à la racine du repo) | Conventions, commandes, rappel des décisions figées (§6.1 et §8 du spec). Chargé automatiquement par Claude Code au début de chaque session. |
| `.claude/agents/spec-reviewer.md` (fourni) | Sous-agent de revue adversariale, invoqué en fin de ticket. |
| `PLAN.md` (généré par ticket, temporaire) | Le plan produit par le mode plan de Claude Code pour ce ticket précis — validé par toi avant toute implémentation. |
| `PROGRESS.md` (à créer, vit dans le repo) | Etat d'avancement : quels tickets sont faits, lesquels sont en cours. Évite qu'une session future ne redécouvre ou ne contredise ce qui existe déjà. |

---

## 2. Mise en place initiale (une seule fois)

1. Repo Git + dépôt distant (GitHub) — installer le CLI `gh`, Claude Code s'en sert nativement pour les PR.
2. Placer `CLAUDE.md` à la racine (fichier fourni ci-joint).
3. Placer `spec-reviewer.md` dans `.claude/agents/` (fichier fourni ci-joint).
4. Créer `PROGRESS.md` vide avec la liste des phases (§5) et leur statut.
5. Premier ticket : **P0 — Scaffolding** (détaillé en §8). Rien d'autre ne démarre avant que P0 soit vert.

---

## 3. Anatomie d'un ticket

Un ticket est l'unité de travail atomique — l'équivalent d'une session Claude Code, jamais plusieurs à la suite sans revue humaine entre les deux.

| Champ | Contenu |
|---|---|
| **Objectif** | Une phrase. Ce que le ticket construit, rien de plus. |
| **Référence spec** | La ou les sections précises de `spec-complete.md` concernées. |
| **Fichiers autorisés** | Liste ou pattern explicite. L'agent ne touche pas au reste sans le signaler. |
| **Non-objectifs** | Ce que ce ticket ne fait *pas*, même si c'est tentant en cours de route (refactor, renommage, fonctionnalité voisine). |
| **Critères d'acceptation** | Exécutables — des tests, une commande, un exit code. Jamais une description en prose. |
| **Definition of done** | Tous les critères passent + `PROGRESS.md` mis à jour + commit avec message descriptif. |

Règle de taille : un ticket doit produire un diff qu'un humain peut relire en une passe — quelques centaines de lignes, pas un backend entier.

---

## 4. La boucle de travail

Basée sur les mécanismes réels de Claude Code, pas sur un usage générique du chat :

1. **`/clear`** — session propre. Ne jamais enchaîner un nouveau ticket dans le contexte encombré du précédent (*"kitchen sink session"*, le mode d'échec le plus fréquent).
2. **Mode plan.** Donner l'objectif du ticket + la référence spec. Claude explore le code existant et propose un `PLAN.md` sans rien modifier.
3. **Revue humaine du plan.** `Ctrl+G` ouvre le plan dans ton éditeur pour l'amender directement si besoin. C'est le point de contrôle le moins cher — corriger un plan coûte des secondes, corriger du code déjà écrit coûte des heures.
4. **Implémentation.** Sortir du mode plan. Demander explicitement à Claude de lancer les tests/le lint après chaque changement et d'itérer jusqu'à ce qu'ils passent — ne jamais accepter une affirmation de succès non vérifiée.
5. **Revue adversariale.** Invoquer le sous-agent `spec-reviewer` : *"Utilise le sous-agent spec-reviewer pour relire ce ticket contre son PLAN.md."* Il tourne dans un contexte neuf, sans le biais de celui qui vient d'écrire le code.
6. **Revue humaine du diff.** Rapide, puisque petit.
7. **Commit + mise à jour de `PROGRESS.md`.**
8. Retour à l'étape 1 pour le ticket suivant.

Si tu corriges le même point plus de deux fois dans un ticket, le contexte est pollué d'approches ratées — `/clear` et reformule le prompt initial avec ce que tu viens d'apprendre, plutôt que de t'entêter.

---

## 5. Séquencement (déroulé opérationnel de `spec-complete.md` §7)

| Ticket | Contenu | Dépend de |
|---|---|---|
| P0 | Scaffolding + CI (détaillé §8) | — |
| P1 | Modèles SQLModel + migrations + admin (détaillé §8) | P0 |
| P2 | Auth JWT, permissions objet | P1 |
| P3 | API CRUD du plan 2D (schémas Pydantic) | P2 |
| P4 | Éditeur 2D (Vue + Konva) | P3 |
| P5 | Catalogue `FurnitureType` paramétrique | P1 |
| P6 | Scene graph 3D côté backend (`numpy`) — fixtures de référence obligatoires | P3, P5 |
| P7 | Viewer 3D (TresJS) : caméras, isolement de face, transparence | P6 |
| P8 | Partage de vue (`SharedView`) | P7 |
| P9 | Export PDF/image + Celery | P4, P7 |
| P10 | Passe performance (cache, eager loading, indexation) | P3–P9 |
| P11 | Passe tests d'intégration / cas limites | P0–P10 |
| P12 | Durcissement déploiement | P0–P11 |

P5 peut démarrer en parallèle de P2–P4 (aucune dépendance). Tout le reste est séquentiel.

---

## 6. Garde-fous anti-hallucination

| Risque | Contre-mesure |
|---|---|
| L'agent invente une API ou un paramètre plausible mais faux | Aucun ticket n'est "fini" sur une auto-évaluation de l'agent — uniquement sur des tests qui passent. C'est la recommandation officielle : donner un check exécutable ferme la boucle, sans ça tu deviens la boucle de vérification toi-même. |
| FastAPI/SQLModel évoluent vite ; la mémoire d'entraînement de l'agent peut être dépassée | On l'a vu concrètement dans cette conversation : `fastapi-users` est passé en mode maintenance récemment. En cas de doute sur une API, l'agent doit vérifier dans le code installé (`pip show`, lire le package) ou la doc officielle — jamais se fier à sa mémoire seule. À inscrire dans `CLAUDE.md`. |
| Le frontend "devine" les routes ou le format de réponse de l'API | FastAPI génère automatiquement un schéma OpenAPI à jour (`/docs`, `/openapi.json`) — c'est la source de vérité pour le frontend, jamais une supposition. |
| Le calcul géométrique (scene graph 3D, P6) "a l'air correct" mais est subtilement faux | Fixtures de référence : couples entrée/sortie connus à l'avance dans `tests/geometry/fixtures/`, sur lesquels l'agent ne peut pas tricher en ajustant le test pour qu'il passe. |
| Le sous-agent de revue valide un travail bâclé parce qu'il partage le biais de celui qui l'a écrit | La revue tourne dans un contexte séparé (sous-agent), qui ne voit que le diff et les critères — pas le raisonnement qui a produit le changement. |

---

## 7. Garde-fous anti-dérive de scope

| Mode d'échec documenté | Correctif |
|---|---|
| *Kitchen sink session* — un ticket dérive vers des sujets non liés, le contexte se remplit de bruit | `/clear` entre tickets non liés, systématiquement. |
| *Infinite exploration* — "investigue X" sans périmètre, l'agent lit des centaines de fichiers | Toujours scoper l'exploration au périmètre du ticket ; utiliser un sous-agent dédié si une recherche large est nécessaire, pour ne pas polluer la session principale. |
| L'agent fait "pendant que j'y suis" un refactor, un renommage, ou ajoute une fonctionnalité voisine non demandée | Non-objectifs explicites dans chaque ticket (§3) ; la revue adversariale (§4 étape 5) vérifie spécifiquement que le diff ne dépasse pas le périmètre annoncé. |
| L'agent redécide silencieusement une architecture déjà tranchée | Décisions figées référencées dans `CLAUDE.md`, qui pointe vers `spec-complete.md` §6.1 et §8. |
| Sur les runs longs/non supervisés, aucun garde-fou ferme | Voir §9 (Stop hook) pour un verrou déterministe plutôt qu'une simple instruction. |

---

## 8. Deux premiers tickets, rédigés en détail

### Ticket P0 — Scaffolding

**Objectif** : poser un squelette de repo déployable, avant tout code métier.

**Référence spec** : `spec-complete.md` §6 (stack).

**Fichiers autorisés** : l'ensemble du repo (seul ticket avec un périmètre aussi large, puisque rien n'existe encore).

**Non-objectifs** : aucun modèle de données, aucune route métier, aucun composant Vue au-delà d'un écran de test.

**Critères d'acceptation** :
- `docker compose up` démarre Postgres + Redis + backend + frontend sans erreur
- `GET /health` sur le backend retourne `200 {"status": "ok"}`
- `cd backend && pytest` passe (au moins un test du health check)
- `cd backend && ruff check . && mypy .` sans erreur
- `cd frontend && npm run build` réussit
- Une CI (GitHub Actions) exécute ces mêmes checks sur chaque push

**Definition of done** : tous les critères ci-dessus verts + `README.md` avec instructions de démarrage local + `PROGRESS.md` créé.

---

### Ticket P1 — Modèle de données

**Objectif** : modèles SQLModel `Project`/`Room`/`Face`/`Element`/`FurnitureType`/`SharedView` + migration Alembic initiale + configuration SQLAdmin.

**Référence spec** : `spec-complete.md` §5 (ajouts au modèle de données) et §6 (choix ORM/migrations/admin).

**Fichiers autorisés** : `backend/app/models/`, `backend/alembic/`, `backend/app/admin.py`, `backend/tests/test_models.py`.

**Non-objectifs** : aucune route API (P3), aucune logique d'auth (P2), aucun seed du catalogue `FurnitureType` (P5).

**Critères d'acceptation** :
- `alembic upgrade head` sur une base vide crée toutes les tables sans erreur
- Un test pytest crée `Project → Room → Face → Element` et relit les relations correctement
- Un test vérifie qu'une contrainte FK bloque la création d'un `Element` référençant une `Face` inexistante
- L'admin SQLAdmin liste et permet d'éditer chaque modèle sur `/admin`

**Definition of done** : tous les critères ci-dessus verts + `PROGRESS.md` mis à jour.

---

## 9. Automatisation plus poussée (à activer plus tard, pas au démarrage)

Ces options existent dans Claude Code et ajoutent de l'autonomie — à n'introduire qu'une fois la boucle manuelle (§4) éprouvée sur plusieurs tickets, pas dès le premier jour :

- **`/goal`** : fixe une condition de sortie vérifiable, un évaluateur séparé re-vérifie après chaque tour. Utile pour un ticket bien cadré qu'on veut laisser tourner sans supervision continue.
- **Stop hook** : verrou déterministe qui bloque la fin de tour tant qu'un script de vérification n'est pas vert — plus strict qu'une simple instruction dans le prompt.
- **Mode non-interactif** (`claude -p`) : pour intégrer des tickets répétitifs dans un script ou la CI.
- **Fan-out** : utile spécifiquement pour P5 (catalogue `FurnitureType`) — générer plusieurs entrées du catalogue en parallèle via des invocations séparées, chacune scopée à une seule entrée.

---

## 10. Fichiers fournis avec ce plan

- `CLAUDE.md` — à placer à la racine du repo
- `agent-spec-reviewer.md` — à placer dans `.claude/agents/spec-reviewer.md`
