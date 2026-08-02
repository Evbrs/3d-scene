# PLAN.md — Ticket P6 : Scene graph 3D côté backend

> Plan du ticket **en cours**. Plans des tickets clos : `docs/plans/`.
> Source : `docs/plan-generation-ia.md` §5 (P6), `docs/spec-complete.md` §3 et §4.

## Objectif

Calculer côté serveur l'arbre de données décrivant la scène 3D (murs extrudés, ouvertures,
mobilier développé, presets de caméra), et l'exposer en JSON.

## Référence spec

`docs/spec-complete.md` §3.1 (principe : tout le calcul côté backend), §3.2 (découpe des
ouvertures, approche simple d'abord), §3.3 (presets de caméra), §4.1 et §4.2 (développement des
recettes, marquage CSG), §8 cas 2 (synchrone d'abord) et cas 5 (simple avant CSG).

## Fichiers autorisés

`backend/app/geometry/`, `backend/app/api/scene.py`, `backend/tests/geometry/`,
`backend/tests/test_scene_api.py`.

**Extension signalée** : `backend/app/main.py` (montage du routeur).

## Non-objectifs

- Aucun rendu Three.js (→ P7) : ce ticket produit des données, pas des objets 3D
- Aucun calcul asynchrone (→ P9) : la spec §8 cas 2 impose de construire en synchrone et de
  mesurer avant de migrer vers Celery
- Aucun cache (→ P10, spec §8 cas 6)
- Aucune opération CSG réelle : le scene graph se contente de **signaler** `requires_csg`

## Méthode — fixtures de référence

`docs/plan-generation-ia.md` §6 identifie le risque : « le calcul géométrique a l'air correct
mais est subtilement faux ». Contre-mesure appliquée à la lettre :

1. les fixtures de `backend/tests/geometry/fixtures/` ont été **calculées à la main, avant**
   l'implémentation, chacune accompagnée du raisonnement qui produit les valeurs attendues ;
2. elles font foi : en cas de désaccord, c'est le code qui est corrigé (`CLAUDE.md`).

## Décisions

- **Repère** : `X = x du plan`, `Y = hauteur`, `Z = y du plan` (convention Three.js).
- **Polygones normalisés en sens trigonométrique** avant tout calcul : sinon les normales
  sortantes s'inversent selon le sens de saisie de l'utilisateur.
- **Ouvertures = trous**, jamais des objets posés (§3.1). Approche `THREE.Shape` + trous (§3.2).
- **Recettes développées côté serveur** : `repeat_*` et `auto` produisent des primitives
  absolues. Le viewer ne fait qu'instancier.
- **Vue par face depuis l'intérieur** de la pièce : c'est l'élévation qu'on veut mesurer et
  exporter (§3.5). La spec parle de « l'axe de la normale sortante » — c'est bien l'axe utilisé,
  la caméra étant placée du côté intérieur.
- **Emplacements couleur non choisis laissés à `null`** : inventer une couleur la rendrait
  indiscernable d'un choix de l'utilisateur.
- **Sortie arrondie à 4 décimales et d'ordre stable** : condition nécessaire pour comparer aux
  fixtures et pour que le cache de P10 fasse mouche.

## Critères d'acceptation (exécutables)

| # | Critère | Vérification |
|---|---|---|
| A1 | La pièce de référence produit exactement le scene graph attendu | `test_a_bare_room_matches_its_reference_fixture` |
| A2 | Une ouverture devient un trou, et non un objet | `test_openings_become_holes_in_the_wall`, `test_openings_do_not_produce_furniture_nodes` |
| A3 | Une recette paramétrique se développe conformément à la fixture | `test_a_parametric_recipe_expands_to_its_reference_primitives` |
| A4 | Le sens de saisie du polygone n'a aucune conséquence | `test_the_scene_is_identical_whichever_way_the_room_was_drawn` |
| A5 | Un preset de caméra par face, plus les trois vues d'ensemble | `test_every_face_has_its_own_camera_preset` |
| A6 | Les vues par face regardent le mur depuis l'intérieur | `test_face_cameras_look_at_the_wall_from_inside_the_room` |
| A7 | `GET /api/projects/{id}/scene` est authentifié et cloisonné | `tests/test_scene_api.py` |
| A8 | Le JSON est stable entre deux appels identiques | `test_the_scene_graph_is_stable_between_two_calls` |

## Definition of done

Critères verts + `pytest` (SQLite **et** PostgreSQL) + `ruff` + `mypy --strict` + revue
`spec-reviewer` + `PROGRESS.md` à jour.
