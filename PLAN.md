# PLAN.md — Ticket P8 : partage de vue (`SharedView`)

> Plan du ticket **en cours**. Plans des tickets clos : `docs/plans/`.
> Source : `docs/plan-generation-ia.md` §5 (P8), `docs/spec-complete.md` §3.5.

## Objectif

Lien permalien exposant une vue 3D en lecture seule, sans authentification.

## Référence spec

`docs/spec-complete.md` §3.5 : « un modèle `SharedView` (`project_id`, `state` en JSON) exposé
par un endpoint public en lecture seule, sans authentification. C'est un bon exercice d'API
"publique mais restreinte" (rate limiting, pas d'info sensible exposée) ».

## Fichiers autorisés

`backend/app/api/share.py`, `backend/app/schemas/share.py`, `backend/tests/test_share_api.py`,
`frontend/src/views/PublicViewerView.vue`.

**Extensions signalées** : `backend/app/main.py` (routeur), `frontend/src/api/client.ts`,
`frontend/src/router.ts`, `frontend/src/views/ViewerView.vue` (bouton de partage),
`frontend/src/api/openapi-snapshot.json` (régénéré).

## Non-objectifs

- Aucun export PDF (→ P9)
- Aucune édition depuis le lien public : il est strictement en lecture
- Pas de mot de passe sur le lien : la spec demande un endpoint public, pas un partage protégé

## Décisions — les trois contraintes de la spec, traitées comme la fonctionnalité elle-même

- **Jeton imprévisible** : `secrets.token_urlsafe(32)`, 256 bits. Un identifiant séquentiel
  rendrait tous les projets partagés énumérables.
- **Aucune information sensible** : la réponse publique ne contient ni propriétaire, ni adresse
  e-mail, ni identifiant interne de projet, ni version, ni date de modification.
- **Limitation de débit** sur l'endpoint public : sans elle, c'est un amplificateur, chaque appel
  déclenchant un calcul de scene graph complet.
- **Expiration optionnelle** : un lien éternel est un risque qui ne se referme jamais. Un lien
  expiré et un lien inexistant donnent la même réponse — les distinguer confirmerait qu'un lien
  a existé.
- **`state` fermé et borné** (`extra="forbid"`, longueurs limitées) : il est écrit par un client
  et relu par un endpoint public ; un JSON libre en ferait un stockage arbitraire servi sans
  authentification.
- **Révocation** possible par le propriétaire : un partage qu'on ne peut pas retirer n'est pas un
  partage maîtrisé.

## Critères d'acceptation (exécutables)

| # | Critère | Vérification |
|---|---|---|
| A1 | Le propriétaire crée, liste et révoque un lien | `test_the_owner_can_create_and_revoke_a_share` |
| A2 | La vue est lisible sans aucune authentification | `test_the_public_view_is_readable_without_any_token` |
| A3 | La réponse publique ne fuite aucune information sur le propriétaire | `test_the_public_response_leaks_no_owner_information` |
| A4 | Le jeton est imprévisible | `test_the_token_is_unpredictable` |
| A5 | L'endpoint public est limité en débit | `test_the_public_endpoint_is_rate_limited` |
| A6 | Un lien expiré est indiscernable d'un lien inexistant | `test_an_expired_share_is_indistinguishable_from_a_missing_one` |
| A7 | Un compte tiers ne peut ni partager ni révoquer | `test_another_account_cannot_share_or_revoke` |
| A8 | Un `state` invalide est refusé | 5 cas paramétrés |

## Definition of done

Critères verts + `pytest` (SQLite **et** PostgreSQL) + `ruff` + `mypy --strict` + build frontend
+ revue `spec-reviewer` + `PROGRESS.md` à jour.
