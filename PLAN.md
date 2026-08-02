# PLAN.md — Ticket P3 : API CRUD du plan 2D

> Plan du ticket **en cours**. Les plans des tickets clos sont archivés dans `docs/plans/`, pour
> qu'une revue puisse toujours être rejouée contre le plan qui avait été validé.
>
> Source : `docs/plan-generation-ia.md` §5 (P3), `docs/spec-complete.md` §7 (phase P3) et §8.

## Objectif

Exposer le plan 2D (projets, pièces, faces, éléments) via une API REST typée, avec schémas
Pydantic imbriqués et validation serveur.

## Référence spec

`docs/spec-complete.md` §7 (P3 : « Schémas Pydantic imbriqués, validation → API CRUD du plan
2D »), §8 (cas 3 : verrouillage optimiste ; cas 4 : eager loading), §1 et §2 (faces lettrées
automatiquement, plafond en face à part entière).

## Fichiers autorisés

`backend/app/api/plan.py`, `backend/app/schemas/`, `backend/app/services/faces.py`,
`backend/tests/test_plan_api.py`.

**Extensions signalées** : `backend/app/main.py` (montage du routeur), `backend/tests/conftest.py`
(fixtures `auth_client` / `other_client`).

## Non-objectifs

- Aucun composant Vue (→ P4)
- Aucun endpoint de catalogue `FurnitureType` (→ P5) — P3 se contente de **valider** qu'un
  `furniture_type_id` référencé existe
- Aucun calcul de scene graph 3D (→ P6), aucun partage de vue (→ P8)
- Aucune optimisation mesurée (→ P10) : l'eager loading est posé dès maintenant parce qu'il est
  structurant pour la forme des requêtes, mais la mesure et le cache relèvent de P10

## Décisions

- **Les faces ne sont ni créées ni supprimées par le client** : elles découlent du polygone de la
  pièce. Le client ne peut modifier que leur revêtement. Exposer `POST /faces` permettrait des
  faces incohérentes avec le plan.
- **Resynchronisation non destructive** : modifier un polygone conserve les faces existantes
  (et donc les revêtements et éléments posés) ; seuls les murs surnuméraires sont supprimés et
  les manquants ajoutés.
- **Lettrage au-delà de Z** : A…Z puis AA, AB… Une pièce en L ou en U peut dépasser 26 murs, et
  un lettrage qui recommencerait violerait la contrainte d'unicité `(room_id, label)`.
- **Verrouillage optimiste opt-in** : si le client envoie `version`, une divergence donne un 409
  avec l'en-tête `X-Current-Version` ; s'il ne l'envoie pas, l'écriture passe. Cela respecte
  l'arbitrage §8 sans rendre l'API inutilisable pour un client simple.
- **`extra="forbid"` sur tous les schémas d'entrée** : empêche l'assignation en masse d'un
  `owner_id` ou d'un `id` (OWASP A08).
- **404 et non 403** sur les objets d'autrui (décision reprise de P2).

## Critères d'acceptation (exécutables)

| # | Critère | Vérification |
|---|---|---|
| A1 | CRUD complet projet / pièce / face / élément | `tests/test_plan_api.py` |
| A2 | Créer une pièce génère les murs lettrés A, B, C… + sol + plafond | `test_creating_a_room_generates_lettered_walls_plus_floor_and_ceiling` |
| A3 | Modifier le polygone préserve revêtements et éléments existants | `test_growing_the_polygon_adds_walls_and_keeps_the_existing_ones` |
| A4 | Une écriture sur une version périmée renvoie 409 sans écraser | `test_a_stale_version_is_rejected_with_409` |
| A5 | Toutes les routes exigent un jeton ; aucun objet d'autrui n'est atteignable | `test_every_route_is_authenticated`, `test_another_account_cannot_reach_the_whole_tree` |
| A6 | Les entrées invalides sont refusées côté serveur (422) | polygones, couleurs, dimensions, champs interdits |
| A7 | Lire un projet renvoie l'arbre complet en une requête | `test_reading_a_project_returns_the_whole_nested_tree` |

## Definition of done

Critères verts + `pytest` (SQLite **et** PostgreSQL) + `ruff` + `mypy --strict` + revue
`spec-reviewer` + `PROGRESS.md` à jour.
