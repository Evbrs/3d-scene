# PLAN.md — Ticket P5 : Catalogue `FurnitureType` paramétrique

> Plan du ticket **en cours**. Plans des tickets clos : `docs/plans/`.
> Source : `docs/plan-generation-ia.md` §5 (P5), `docs/spec-complete.md` §4 et §7.

## Objectif

Catalogue de mobilier générique paramétrique : recettes de composition, API de consultation et
d'administration, chargement en base.

## Référence spec

`docs/spec-complete.md` §4.1 (composition par primitives), §4.2 (meubles qui bénéficient du CSG),
§4.3 (catalogue cible), §4.4 (personnalisation à l'instanciation), §7 (phase P5).

## Ordre

P5 est traité avant P4 : `plan-generation-ia.md` §5 précise que « P5 peut démarrer en parallèle
de P2–P4 (aucune dépendance) », et P6 en dépend. P4 (éditeur 2D) suit.

## Fichiers autorisés

`backend/app/schemas/furniture.py`, `backend/app/services/catalog.py`,
`backend/app/services/seed.py`, `backend/app/api/furniture.py`, `backend/app/cli.py`,
`backend/tests/test_furniture_catalog.py`.

**Extensions signalées** : `backend/app/main.py` (montage du routeur), `backend/tests/conftest.py`
(fixture `superuser_client`), `docker-compose.yml` (chargement du catalogue au démarrage).

**Corrections P2 incluses** : les 8 écarts de la revue `spec-reviewer` sur P2 (voir PROGRESS.md).

## Non-objectifs

- Aucun rendu 3D des recettes (→ P7) : P5 décrit la géométrie, il ne la construit pas
- Aucun composant Vue de sélection de meuble (→ P4)
- Aucune opération CSG réelle (→ P7) : les primitives déclarent `operation: subtract`, le moteur
  de rendu l'interprétera

## Décisions

- **Le catalogue est global et partagé**, pas rattaché à un compte : lecture pour tout
  utilisateur authentifié, écriture réservée aux superutilisateurs. Sinon n'importe quel compte
  modifierait une recette utilisée par les plans de tous les autres.
- **Validation stricte des recettes à l'écriture** : emplacement couleur non déclaré, `auto` sur
  un axe non répété, recette vide. Une recette incohérente ne se voit sinon qu'au rendu 3D, très
  loin du point d'insertion.
- **Le catalogue de référence est du code** (`services/catalog.py`), pas un fichier de données :
  il est validé par les mêmes schémas que l'API et couvert par les tests.
- **Seed idempotent**, avec `--no-overwrite` pour ne pas écraser une recette ajustée à la main.
- **Supprimer une recette ne détruit pas les plans** : la FK est en `ON DELETE SET NULL` (P1).

## Critères d'acceptation (exécutables)

| # | Critère | Vérification |
|---|---|---|
| A1 | Chaque ligne du tableau §4.3 a une entrée dans le catalogue | `test_the_catalog_covers_the_whole_spec_table` |
| A2 | Toutes les recettes du catalogue sont valides | `test_every_catalog_entry_is_a_valid_recipe` |
| A3 | Vasque et baignoire déclarent une soustraction (§4.2) | `test_the_bathroom_pieces_that_need_csg_declare_a_subtraction` |
| A4 | La commode reste fidèle à l'exemple canonique §4.1 | `test_the_commode_matches_the_spec_example` |
| A5 | Le seed est idempotent et rejouable | `test_seeding_twice_creates_no_duplicate` |
| A6 | Lecture authentifiée, écriture réservée aux superutilisateurs | `test_a_regular_user_cannot_write_the_shared_catalog` |
| A7 | Une recette invalide est refusée par l'API | `test_an_invalid_recipe_is_refused_by_the_api` |

## Definition of done

Critères verts + `pytest` (SQLite **et** PostgreSQL) + `ruff` + `mypy --strict` + revue
`spec-reviewer` + `PROGRESS.md` à jour.
