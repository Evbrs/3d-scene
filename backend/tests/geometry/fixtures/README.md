# Fixtures de référence du scene graph

Couples entrée/sortie **calculés à la main**, avant l'implémentation. Ils font foi
(`CLAUDE.md`) : si le code et une fixture divergent, c'est le code qui est corrigé. Si un
résultat attendu semble faux, il faut le signaler et discuter la fixture, jamais l'ajuster pour
faire passer un test.

C'est la contre-mesure prévue par `docs/plan-generation-ia.md` §6 contre le mode d'échec
« le calcul géométrique a l'air correct mais est subtilement faux ».

## Format

```json
{
  "description": "…",
  "reasoning": ["…comment les valeurs attendues ont été obtenues, à la main…"],
  "input": { "…plan 2D… " },
  "expected": { "…scene graph… " }
}
```

Le champ `reasoning` n'est pas décoratif : il permet de rejouer le calcul sans relire le code, et
c'est lui qui distingue une valeur *dérivée à la main* d'une valeur *recopiée depuis une sortie
de programme*.

## Pièce de référence

Sauf mention contraire, les fixtures utilisent la même pièce : un rectangle de **400 × 300 cm**,
sous **250 cm** de plafond, murs de **10 cm**, polygone `[[0,0], [400,0], [400,300], [0,300]]`
(sens trigonométrique).

| Mur | Segment | Direction | Normale sortante | `rotation_y` |
|---|---|---|---|---|
| A | (0,0) → (400,0) | (1, 0, 0) | (0, 0, −1) | 0 |
| B | (400,0) → (400,300) | (0, 0, 1) | (1, 0, 0) | −π/2 |
| C | (400,300) → (0,300) | (−1, 0, 0) | (0, 0, 1) | π |
| D | (0,300) → (0,0) | (0, 0, −1) | (−1, 0, 0) | π/2 |

La normale sortante vaut `Y_up × direction = (dz, 0, −dx)`. Vérification sur le mur A :
`(0,1,0) × (1,0,0) = (0,0,−1)`, et l'intérieur de la pièce est bien en `z > 0`.

## Pièces des fixtures 05 et 06

Ce rectangle a un défaut : **trois composantes sur quatre y sont nulles**. Aucune erreur de
quadrant d'`atan2`, aucune erreur d'orientation qui ne se voit que hors des axes, et aucun
polygone concave n'y sont détectables. Deux pièces s'y ajoutent donc.

| Fixture | Pièce | Ce qu'elle attrape |
|---|---|---|
| `05_mur_oblique.json` | Octogone : rectangle 600 × 400 aux quatre coins coupés à 45° | Les quatre murs obliques donnent un `rotation_y` par quadrant (`±π/4`, `±3π/4`) : une permutation ou une erreur de signe dans `atan2` n'y passe plus par accident. |
| `06_piece_en_L.json` | L : `(0,0) (600,0) (600,200) (200,200) (200,500) (0,500)` | Sommet **rentrant** : onglet d'angle concave, caméras d'élévation par lancer de rayon (la boîte englobante les posait hors de la pièce), et aire nette d'un contour non convexe. |

Les champs `axis` (primitives) et `net_floor_area_cm2` (pièce) ont été ajoutés au contrat après
l'écriture des fixtures 01 à 04. Ils sont figés **ici**, par 05 et 06, plutôt qu'en réécrivant les
fixtures d'origine.
