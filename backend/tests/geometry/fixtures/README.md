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

## Fixtures 07 à 10 — le métré

Elles vérifient `app/geometry/quantities.py::build_takeoff` et non le scene graph. La règle est la
même — valeurs calculées à la main, `reasoning` obligatoire, fixture jamais ajustée pour faire
passer un test — mais leur **format diffère**, parce qu'un métré ne se compare pas nœud par nœud :

| Fixture | Ce qu'elle fige | Clés attendues |
|---|---|---|
| `07_metre_piece_rectangulaire.json` | Pièce de référence nue : surfaces, linéaires, volume, calepinage en pose droite — et l'écart de l'aire médiane, qui est la raison d'être du champ `net_floor_area_cm2` | `expected`, `expected_median_floor_area_cm2`, `expected_median_overestimate_ratio` |
| `08_metre_piece_en_L.json` | Contour concave, une porte et une fenêtre. Le sol n'y admet aucune trame : la pièce n'est pas rectangulaire, donc `full_units` / `cut_units` valent `None` et non 0 | `expected_room`, `expected_faces`, `expected_coverings`, `expected_median_floor_area_cm2` |
| `09_metre_mur_deux_ouvertures.json` | Trame percée, position par position : 40 emplacements, 6 avalés par les percements, 13 coupes, 21 entiers | `expected_room`, `expected_faces`, `expected_trame_positions`, `expected_positions_swallowed_by_openings`, `expected_warning_count` |
| `10_metre_calepinage_motifs.json` | Les cinq motifs de pose et un motif inconnu sur la même face — taux de chute et repli sur le motif droit | `expected_by_pattern`, `expected_net_area_m2`, `expected_waste_ratio_orders_of_magnitude` |

Leur `input` est un **plan**, pas un scene graph : le test enchaîne `build_scene_graph` puis
`build_takeoff`, ce qui fait porter la fixture sur la chaîne réelle plutôt que sur une entrée
intermédiaire qu'aucun appelant ne produit.

Deux conventions à ne pas confondre en les relisant : `None` signifie « non établissable » et
jamais zéro (il s'accompagne toujours d'un avertissement), et les trois compteurs d'unités sont
distincts — `units_total` est ce qu'il faut commander, `full_units` ce qui se pose entier,
`cut_units` ce qu'il faut recouper. Leur somme n'a aucun sens.

## Fixture 11 — le mobilier libre

`11_mobilier_libre.json` reprend la pièce de référence et fige l'amendement A4 (`docs/spec-complete.md`
§10) : un élément s'ancre à une face **ou** au sol de la pièce. C'est la seule fixture où les deux
ancrages coexistent — un radiateur adossé au mur A, un lit libre au centre, une table libre tournée
à 90° — et c'est ce qui lui permet d'attraper les trois confusions possibles entre les deux repères :

- le placement d'un meuble libre est exprimé dans le repère du **plan**, celui de `Room.polygon`, et
  non relativement au coin de la boîte englobante comme pour un élément posé sur la face SOL ;
- `face_label` vaut `null` pour un meuble libre, ce qui le tient hors de tout groupe de face — sans
  quoi l'isolement de face (§3.4) le masquerait alors qu'il n'appartient à aucun mur ;
- l'**ordre d'émission** est figé : les faces, puis ce qui y est adossé, puis le mobilier libre. Cet
  ordre est stable par contrat, le cache de scène (P10) s'appuyant sur la sortie octet pour octet.

Elle porte une clé supplémentaire, `expected_footprints` : les quatre coins de l'emprise **après
rotation**, indexés par identifiant d'élément. Ils appartiennent à la fixture et non au test parce
que c'est sur eux que se prononce `element_fits_in_room` — la géométrie qui décide si un meuble tient
dans la pièce est la même que celle qui le dessine, et les faire diverger passerait inaperçu.
