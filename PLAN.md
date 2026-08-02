# PLAN.md — Tickets P11 et P12 : tests d'intégration et durcissement

> Plan du ticket **en cours**. Plans des tickets clos : `docs/plans/`.
> Source : `docs/plan-generation-ia.md` §5 (P11, P12).

## Objectif

P11 : couvrir les parcours complets et les cas limites, là où se logent les défauts d'assemblage.
P12 : durcir le déploiement — configuration qui échoue plutôt que de démarrer mal, en-têtes de
sécurité, image de production.

## Fichiers autorisés

`backend/tests/test_integration.py`, `backend/tests/test_hardening.py`,
`backend/app/core/security_headers.py`, `frontend/Dockerfile.prod`, `frontend/nginx.conf`,
`docker-compose.prod.yml`.

**Extensions signalées** : `backend/app/main.py` (middlewares, fermeture de `/docs`),
`backend/app/core/config.py` (analyse de `CORS_ORIGINS`), `.github/workflows/ci.yml`, `README.md`.

## Non-objectifs

- Aucune nouvelle fonctionnalité
- Aucune infrastructure hors dépôt (TLS, sauvegardes, supervision) : listée dans le README comme
  reste-à-faire explicite plutôt que passée sous silence

## Décisions

- **La configuration échoue plutôt que de démarrer mal** : en production, `SECRET_KEY`,
  `CORS_ORIGINS` et les identifiants de base sont obligatoires, et la substitution
  `${VAR:?message}` de Docker Compose refuse le démarrage s'ils manquent.
- **`/docs`, `/redoc` et `/openapi.json` fermés en production** : ils décrivent l'intégralité de
  la surface d'attaque. Le frontend s'appuie sur l'instantané versionné, pas sur l'endpoint.
- **HSTS uniquement hors développement** : en local le service est en clair, et HSTS y bloquerait
  le navigateur pour un an.
- **Deux politiques CSP** : stricte pour l'API (`default-src 'none'`), plus permissive pour le
  back-office qui sert ses propres feuilles et scripts. Une seule politique casserait l'un ou
  affaiblirait l'autre.
- **`CORS_ORIGINS` accepte la liste séparée par des virgules** en plus du JSON : c'est la forme
  qu'on écrit spontanément, et `pydantic-settings` seul la rejetterait au démarrage.
- **Frontend de production servi par nginx en non-root**, port 8080, `index.html` non mis en
  cache pour qu'un déploiement soit immédiatement visible.

## Critères d'acceptation (exécutables)

| # | Critère | Vérification |
|---|---|---|
| A1 | Un parcours complet inscription → export fonctionne | `test_a_full_journey_from_signup_to_export` |
| A2 | Pièce en L, >26 murs, coordonnées négatives, pièce éloignée | 4 tests |
| A3 | La suppression d'un compte efface tout ce qu'il possède | `test_deleting_a_user_removes_everything_they_own` |
| A4 | Projet vide et pièce sans contour traversent tout le pipeline | 2 tests |
| A5 | Lectures simultanées cohérentes, écritures concurrentes arbitrées | 2 tests |
| A6 | Le plan et la scène décrivent toujours les mêmes faces | `test_the_scene_always_matches_the_plan` |
| A7 | La production refuse de démarrer sans secret fort | tests + job CI dédié |
| A8 | Les en-têtes de sécurité sont présents | 6 tests + vérification sur la stack |

## Definition of done

Critères verts + vérification sur la stack + revue `spec-reviewer` + `PROGRESS.md` à jour.
