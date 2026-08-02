# PLAN.md — Ticket P10 : passe performance

> Plan du ticket **en cours**. Plans des tickets clos : `docs/plans/`.
> Source : `docs/plan-generation-ia.md` §5 (P10), `docs/spec-complete.md` §8 cas 4 et 6.

## Objectif

Mesurer puis traiter les deux points de performance que la spec désigne : le N+1 sur le
chargement des relations, et le recalcul du scene graph à chaque requête.

## Référence spec

- **§8 cas 4** : « **Mesurer** le N+1 en activant le logging SQL (`echo=True`) **avant**
  d'optimiser — sinon l'optimisation n'a pas de sens concret. »
- **§8 cas 6** : « Cache Redis, invalidé à la modification du plan. […] Bon terrain pour
  pratiquer l'invalidation de cache — un des rares vrais problèmes difficiles de l'informatique. »

## Fichiers autorisés

`backend/app/core/cache.py`, `backend/tests/test_performance.py`,
`backend/alembic/versions/` (index).

**Extensions signalées** : `backend/app/api/scene.py` et `backend/app/api/plan.py` (branchement
du cache), `backend/app/core/config.py`, `backend/app/models/plan.py` (index composite),
`backend/tests/conftest.py`.

## Non-objectifs

- Aucune optimisation non mesurée : c'est le sens même de l'arbitrage §8 cas 4
- Aucune dénormalisation de la géométrie (§8 cas 1 : rester en JSON tant qu'aucun besoin de
  requête n'apparaît — il n'en est apparu aucun)

## Décisions

- **Le N+1 est d'abord reproduit, puis corrigé.** Un test compte les requêtes du chargement naïf
  et démontre qu'il croît avec la taille du plan ; un autre démontre que le chargement anticipé
  le rend constant. Un compteur de requêtes remplace la lecture de logs à l'œil : la mesure
  devient une assertion.
- **La clé de cache porte la version du projet.** C'est le cœur de la conception : au lieu de
  supprimer une entrée à chaque écriture — ce qui suppose de n'oublier aucun chemin d'écriture —
  une modification *change la clé*. Comme toute écriture du plan incrémente `Project.version`
  (garanti depuis P3), l'invalidation devient structurelle plutôt que déclarative.
- **Un cache indisponible dégrade, il ne casse pas** : une panne Redis fait recalculer la scène,
  jamais échouer la requête. Sans ça, une optimisation devient un point de défaillance unique.
- **Purge explicite à la suppression d'un projet** : c'est le seul cas où aucune version future
  ne viendra rendre les anciennes clés inatteignables.
- **Un seul index ajouté**, calqué sur la requête réelle de la liste des projets. Indexer « au
  cas où » coûte à chaque écriture pour un gain hypothétique.

## Critères d'acceptation (exécutables)

| # | Critère | Vérification |
|---|---|---|
| A1 | Le N+1 est reproduit et mesuré | `test_the_naive_loading_really_produces_an_n_plus_one` |
| A2 | Le chargement anticipé rend le nombre de requêtes constant | `test_eager_loading_keeps_the_query_count_constant` |
| A3 | Le nombre de requêtes de l'API ne dépend pas de la taille du plan | 2 tests (projet, scène) |
| A4 | La clé de cache porte la version | `test_the_cache_key_carries_the_project_version` |
| A5 | Une édition rend immédiatement la nouvelle scène | `test_the_scene_reflects_an_edit_immediately` |
| A6 | Une panne Redis ne casse rien | `test_the_cache_degrades_gracefully_when_redis_fails` |

## Definition of done

Critères verts + mesure relevée sur la stack réelle avec Redis + revue `spec-reviewer` +
`PROGRESS.md` à jour.
