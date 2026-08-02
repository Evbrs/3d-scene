# Géométrie 3D — conventions

Ce module calcule le **scene graph** envoyé au frontend (`docs/spec-complete.md` §3.1). Le
frontend ne fait que traduire ce JSON en objets Three.js : aucune logique métier côté client.

## Repères et unités

Toutes les longueurs sont en **centimètres**, comme en base.

| Repère | Axes |
|---|---|
| Plan 2D (base de données) | `x` horizontal, `y` vertical *dans le plan vu du dessus* |
| Monde 3D (Three.js) | `X = x du plan`, `Y = hauteur (vers le haut)`, `Z = y du plan` |

C'est la convention Three.js habituelle (Y vers le haut). La correspondance `Z = y_plan` est
appliquée une seule fois, à la construction du scene graph.

## Orientation des polygones

Un polygone de pièce est normalisé en **sens trigonométrique** (aire signée positive) avant tout
calcul. L'utilisateur peut dessiner dans les deux sens ; sans normalisation, les normales
sortantes seraient inversées une fois sur deux et les vues « par face » regarderaient les murs
depuis l'extérieur du logement.

## Normale sortante d'un mur

Pour une arête allant de `P1` à `P2`, de direction `d` (normalisée) :

```
normale_sortante = normalize(Y_up × d)
```

Vérification sur le carré `[[0,0], [400,0], [400,300], [0,300]]` (trigonométrique) : l'arête A va
de `(0,0)` à `(400,0)`, donc `d = (1,0,0)` et `Y_up × d = (0,0,-1)`. L'intérieur de la pièce est
en `z > 0` : la normale pointe bien vers l'extérieur.

## Repère local d'une face

Chaque mur est décrit comme une forme 2D locale, extrudée selon son épaisseur — l'approche
« simple » de la spec §3.2 (`THREE.Shape` + `ExtrudeGeometry`), à préférer tant qu'elle suffit.

| Axe local | Signification |
|---|---|
| `u` | abscisse le long du mur, de 0 (départ) à sa longueur |
| `v` | hauteur, de 0 (sol) à la hauteur sous plafond |

Les ouvertures (portes, fenêtres) deviennent des **trous** dans cette forme, exprimés dans le
même repère `(u, v)`.

## Fixtures de référence

`backend/tests/geometry/fixtures/` contient des couples entrée/sortie calculés à la main. Elles
font foi : en cas de désaccord entre le code et une fixture, **c'est le code qui est corrigé**
(`CLAUDE.md`). Si un résultat attendu semble faux, il faut le signaler, pas l'ajuster.
