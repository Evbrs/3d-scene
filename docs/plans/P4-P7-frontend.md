# PLAN.md — Tickets P4 et P7 : éditeur 2D et viewer 3D

> Plan du ticket **en cours**. Plans des tickets clos : `docs/plans/`.
> Source : `docs/plan-generation-ia.md` §5 (P4, P7), `docs/spec-complete.md` §1, §3, §6.

## Objectif

P4 : éditeur 2D interactif (Vue + Konva) pour saisir le plan, poser revêtements et éléments.
P7 : viewer 3D (TresJS) avec presets de caméra, isolement de face, transparence et capture.

Les deux tickets sont traités ensemble parce qu'ils partagent l'ossature du frontend (client
HTTP, routeur, stores, styles) : les livrer séparément aurait imposé de construire cette
ossature deux fois, ou de la faire passer en contrebande dans l'un des deux.

## Fichiers autorisés

`frontend/src/` (client API, routeur, stores, vues, éditeur, viewer), `frontend/src/**/*.spec.ts`.

**Extensions signalées** : `frontend/package.json` (ajout de `vue-router` et `pinia`),
`backend/scripts/dump_openapi.py` et `.github/workflows/ci.yml` (vérification anti-dérive du
contrat OpenAPI — voir Décisions).

## Non-objectifs

- Aucun partage de vue (→ P8) : `visibility.ts` prépare la sérialisation, la route n'existe pas
- Aucun export PDF (→ P9) : seule la capture PNG côté navigateur, prévue par §3.5, est faite
- Aucune opération CSG réelle (§3.2) : les primitives soustraites sont ignorées à l'affichage
  plutôt que rendues en plein, ce qui donnerait un volume faux
- Aucun calcul géométrique métier côté client (§3.1)

## Décisions

- **Le frontend ne devine aucune route.** `plan-generation-ia.md` §6 désigne l'OpenAPI comme
  source de vérité ; ici c'est rendu **exécutable** : un instantané du schéma est versionné, un
  test confronte chaque chemin appelé par le client à cet instantané, et la CI régénère
  l'instantané pour échouer s'il a dérivé. Sans ce dernier maillon, le test validerait un
  contrat périmé.
- **Les chemins de l'API restent des littéraux** : interpoler une chaîne de requête dans le
  chemin rendrait le contrat invérifiable statiquement. D'où le helper `withQuery`.
- **Le verrouillage optimiste est porté par le store** : toute écriture renvoie la version lue,
  et un 409 est traité comme un cas métier (« quelqu'un a modifié le plan »), pas comme une
  erreur générique.
- **Trois états de visibilité** (§3.4), la transparence étant l'état intéressant : masquer
  complètement un mur fait perdre le repère spatial.
- **Le mobilier suit la visibilité de sa face** : isoler un mur sans ses meubles n'aurait pas de
  sens pour une élévation.
- **Accessibilité** : navigation clavier, `aria-pressed` sur les bascules, `role="alert"` sur les
  erreurs, lien d'évitement, focus visible, contrastes conformes aux conventions du projet.

## Critères d'acceptation (exécutables)

| # | Critère | Vérification |
|---|---|---|
| A1 | Le lettrage des murs côté client est identique au backend | `editor/geometry.spec.ts` |
| A2 | Conversion plan ↔ écran réversible, magnétisme sur grille | `editor/geometry.spec.ts` |
| A3 | Un contour qui se recoupe est détecté | `isSelfIntersecting` |
| A4 | Les trois états de visibilité se comportent comme la spec §3.4 | `viewer/visibility.spec.ts` |
| A5 | L'isolement rend les autres faces transparentes, pas masquées | `viewer/visibility.spec.ts` |
| A6 | Les ouvertures deviennent des trous dans la forme Three.js | `viewer/geometry.spec.ts` |
| A7 | Aucun chemin appelé par le client n'est absent de l'OpenAPI | `api/contract.spec.ts` + job CI |
| A8 | `npm run build`, `npm run test`, `npm run lint` verts | CI |

## Definition of done

Critères verts + revue `spec-reviewer` + `PROGRESS.md` à jour.
