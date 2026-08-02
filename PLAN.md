# PLAN.md — Ticket P9 : export PDF et tâches Celery

> Plan du ticket **en cours**. Plans des tickets clos : `docs/plans/`.
> Source : `docs/plan-generation-ia.md` §5 (P9), `docs/spec-complete.md` §1, §3.5, §6, §8 cas 2.

## Objectif

Export PDF d'un projet, généré en tâche de fond par Celery, avec le chemin synchrone conservé
comme référence de mesure.

## Référence spec

§1 (« Export PDF et image »), §3.5 (« export PDF détaillé par mur »), §6 (Celery + Redis),
**§8 cas 2** : « Construire en synchrone (P6), migrer vers Celery (P9) **en mesurant le gain
avant/après** ».

## Fichiers autorisés

`backend/app/services/export_pdf.py`, `backend/app/core/celery_app.py`,
`backend/app/tasks/exports.py`, `backend/app/api/exports.py`,
`backend/tests/test_export_api.py`.

**Extensions signalées** : `backend/app/main.py`, `backend/app/core/config.py`,
`backend/pyproject.toml` (reportlab), `backend/tests/conftest.py`, `backend/Dockerfile`,
`docker-compose.yml` (service `worker` + volume partagé).

## Non-objectifs

- Aucun devis chiffré (§1 le mentionne dans la vision, aucune phase ne le porte)
- Aucun export d'image côté serveur : §3.5 confie la capture PNG au canvas Three.js, déjà fait
  en P7
- Aucune optimisation mesurée du reste de l'API (→ P10)

## Décisions

- **Les deux chemins sont exposés**, synchrone et asynchrone. Ce n'est pas de l'indécision :
  §8 cas 2 exige une mesure avant/après, et supprimer le chemin synchrone la rendrait
  impossible à rejouer. Il sert aussi de repli si le broker est indisponible.
- **PDF vectoriel** (reportlab) plutôt qu'assemblage de captures : le plan reste net à
  l'impression et l'export ne dépend d'aucun navigateur.
- **Horodatage injecté** dans le moteur de rendu : la sortie devient reproductible, donc
  testable.
- **La tâche ne renvoie jamais le PDF**, seulement un descriptif : faire transiter des
  mégaoctets par le backend de résultats Redis serait un contresens.
- **Le worker crée son propre moteur de base** : les connexions ne survivent pas à un fork.
- **Téléchargement re-vérifié** : nom de fichier verrouillé par motif, chemin reconstruit depuis
  le répertoire d'export, propriété du projet revérifiée à chaque téléchargement.

## Critères d'acceptation (exécutables)

| # | Critère | Vérification |
|---|---|---|
| A1 | Le rendu produit un PDF valide et reproductible | `tests/test_export_api.py` |
| A2 | L'export asynchrone rend la main immédiatement et produit un fichier | test + stack réelle |
| A3 | Le téléchargement est cloisonné et résiste à la traversée de chemin | 4 cas paramétrés |
| A4 | Le gain synchrone/asynchrone est mesuré | `test_celery_shortens_the_perceived_latency` |
| A5 | Toutes les routes sont authentifiées et cloisonnées | tests dédiés |

## Definition of done

Critères verts + vérification sur la stack réelle avec un **vrai** worker Celery + revue
`spec-reviewer` + `PROGRESS.md` à jour.
