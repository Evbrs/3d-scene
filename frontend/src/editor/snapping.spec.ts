/**
 * Magnétisme, guides et saisie numérique de la cote.
 *
 * Ce qui est vérifié ici décide de la justesse du plan saisi : une accroche qui rate d'un
 * centimètre produit un mur parasite que le backend refuse, et un alignement « presque » vrai ne
 * se voit qu'une fois la pièce extrudée en 3D.
 */
import { describe, expect, it } from 'vitest'

import {
  ANGLE_STEP_DEG,
  collectVertices,
  constrainToAngle,
  nearestVertex,
  pointAtDistance,
  resolveSnap,
  verticesExcept,
} from '@/editor/snapping'

const ORIGINE = { x: 0, y: 0 }

describe('contrainte angulaire', () => {
  it('rabat sur le multiple de 45° le plus proche', () => {
    // 10° au-dessus de l'horizontale : on veut l'horizontale, et exactement zéro en ordonnée —
    // `Math.sin(0)` est propre, mais `Math.sin(Math.PI)` ne l'est pas, d'où la vérification
    // stricte plutôt qu'un `toBeCloseTo` qui laisserait passer 1,2 × 10⁻¹⁶.
    const point = constrainToAngle(ORIGINE, { x: 100, y: 17.6 })

    expect(point.x).toBeCloseTo(100, 6)
    expect(point.y).toBe(0)
  })

  it('ne laisse aucune poussière flottante sur un angle droit', () => {
    // Un mur tracé vers la gauche passe par `Math.sin(Math.PI)`, qui ne vaut pas zéro. Sans
    // nettoyage, deux sommets censés être alignés ne le sont jamais tout à fait.
    const point = constrainToAngle(ORIGINE, { x: -400, y: 3 })

    expect(point).toEqual({ x: -400, y: 0 })
  })

  it('conserve la distance projetée, pas la distance brute', () => {
    // Un point à 45° d'un axe : sa projection vaut cos(45°) fois sa norme. Garder la norme
    // ferait s'allonger le mur pendant qu'on cherche son orientation.
    const point = constrainToAngle(ORIGINE, { x: 100, y: 100 }, 90)

    expect(Math.hypot(point.x, point.y)).toBeCloseTo(100, 6)
  })

  it('ne produit jamais un mur de longueur négative', () => {
    // Avec un pas de 360°, un seul rayon existe et le curseur peut se trouver derrière lui : le
    // garde-fou rabat sur l'origine au lieu de rendre un mur qui part à l'envers.
    expect(constrainToAngle(ORIGINE, { x: -5, y: 0 }, 360)).toEqual({ x: 0, y: 0 })
  })

  it('rend l’origine quand le curseur est dessus', () => {
    expect(constrainToAngle(ORIGINE, ORIGINE)).toEqual(ORIGINE)
  })

  it('propose bien les diagonales', () => {
    expect(ANGLE_STEP_DEG).toBe(45)
    const point = constrainToAngle(ORIGINE, { x: 100, y: 90 })

    expect(point.x).toBeCloseTo(point.y, 6)
  })
})

describe('accroche aux sommets', () => {
  const sommets = [
    { x: 0, y: 0 },
    { x: 400, y: 0 },
    { x: 400, y: 300 },
  ]

  it('retient le sommet le plus proche dans la tolérance', () => {
    expect(nearestVertex({ x: 397, y: 4 }, sommets, 12)).toEqual({ x: 400, y: 0 })
  })

  it('ne retient rien au-delà', () => {
    expect(nearestVertex({ x: 380, y: 0 }, sommets, 12)).toBeNull()
  })

  it('rend une copie : le sommet du contour ne doit pas être mutable par accident', () => {
    const trouve = nearestVertex({ x: 0, y: 0 }, sommets, 5)!
    trouve.x = 999

    expect(sommets[0]).toEqual({ x: 0, y: 0 })
  })
})

describe('résolution du point de saisie', () => {
  const sommets = [
    { x: 0, y: 0 },
    { x: 413, y: 0 },
    { x: 413, y: 297 },
  ]

  it('l’accroche à un sommet gagne sur la grille', () => {
    const resultat = resolveSnap({ x: 410, y: 3 }, { vertices: sommets, gridCm: 10, toleranceCm: 12 })

    expect(resultat.kind).toBe('sommet')
    expect(resultat.point).toEqual({ x: 413, y: 0 })
  })

  it('aligne sur le prolongement d’un sommet et rend le guide', () => {
    const resultat = resolveSnap(
      { x: 415, y: 180 },
      { vertices: sommets, gridCm: 10, toleranceCm: 12 },
    )

    expect(resultat.kind).toBe('alignement')
    // L'axe accroché garde la coordonnée exacte du sommet : la repasser par la grille donnerait
    // 410 et l'alignement promis par le guide serait faux.
    expect(resultat.point.x).toBe(413)
    expect(resultat.point.y).toBe(180)
    expect(resultat.guides).toHaveLength(1)
    expect(resultat.guides[0]?.kind).toBe('alignement')
  })

  it('affiche deux guides quand les deux axes s’alignent sur des sommets différents', () => {
    const resultat = resolveSnap(
      { x: 415, y: 295 },
      { vertices: sommets, gridCm: 10, toleranceCm: 12 },
    )

    expect(resultat.point).toEqual({ x: 413, y: 297 })
    expect(resultat.kind).toBe('sommet')

    const eloigne = resolveSnap(
      { x: 5, y: 295 },
      { vertices: [{ x: 0, y: 0 }, { x: 900, y: 297 }], gridCm: 10, toleranceCm: 12 },
    )

    expect(eloigne.kind).toBe('alignement')
    expect(eloigne.point).toEqual({ x: 0, y: 297 })
    expect(eloigne.guides).toHaveLength(2)
  })

  it('retombe sur la grille quand rien n’accroche', () => {
    const resultat = resolveSnap({ x: 197, y: 143 }, { vertices: sommets, gridCm: 10, toleranceCm: 4 })

    expect(resultat.kind).toBe('grille')
    expect(resultat.point).toEqual({ x: 200, y: 140 })
  })

  it('la contrainte angulaire court-circuite les accroches', () => {
    const resultat = resolveSnap(
      { x: 410, y: 3 },
      {
        vertices: sommets,
        gridCm: 10,
        toleranceCm: 40,
        origin: ORIGINE,
        constrainAngle: true,
      },
    )

    // Un sommet est à portée, et pourtant : quand on demande une direction, on veut la direction.
    expect(resultat.kind).toBe('angle')
    expect(resultat.point.y).toBeCloseTo(0, 6)
    expect(resultat.guides[0]?.kind).toBe('angle')
  })

  it('annonce ce qui a décidé du point', () => {
    const grille = resolveSnap({ x: 7, y: 7 }, { vertices: [], gridCm: 5, toleranceCm: 1 })

    expect(grille.libelle).toContain('5 cm')
  })
})

describe('saisie numérique de la cote', () => {
  it('place le sommet à la distance exacte, dans la direction visée', () => {
    const point = pointAtDistance(ORIGINE, { x: 3, y: 4 }, 347)

    expect(Math.hypot(point.x, point.y)).toBeCloseTo(347, 6)
    expect(point.x / point.y).toBeCloseTo(3 / 4, 6)
  })

  it('ne rend jamais NaN quand le curseur est sur l’origine', () => {
    const point = pointAtDistance(ORIGINE, ORIGINE, 250)

    expect(point).toEqual({ x: 250, y: 0 })
  })
})

describe('sommet en cours de déplacement', () => {
  const oblique = [
    [0, 0],
    [397, 53],
    [0, 300],
  ]

  it('ne figure pas dans ses propres candidats', () => {
    const candidats = verticesExcept(oblique, 1)

    expect(candidats).not.toContainEqual({ x: 397, y: 53 })
    expect(candidats).toHaveLength(2)
  })

  it('peut donc être corrigé de quelques centimètres', () => {
    const cible = { x: 395, y: 55 }

    // Avec le sommet dans ses propres candidats, il se rattrape à sa position de départ et la
    // correction est tout bonnement impossible.
    expect(
      resolveSnap(cible, { vertices: collectVertices([oblique]), gridCm: 5, toleranceCm: 12 })
        .point,
    ).toEqual({ x: 397, y: 53 })

    // Retiré du jeu, il suit la souris et retombe sur la grille comme n'importe quel point.
    expect(
      resolveSnap(cible, { vertices: verticesExcept(oblique, 1), gridCm: 5, toleranceCm: 12 })
        .point,
    ).toEqual({ x: 395, y: 55 })
  })

  it('reste rattrapé par les prolongements de ses voisins, et c’est voulu', () => {
    // Sur un rectangle, le coin est l'intersection des prolongements des deux murs adjacents :
    // le retenir là est ce qui garde le plan d'équerre. Pour l'en écarter vraiment, on sort du
    // rayon d'accroche — ou on saisit la cote au clavier.
    const carre = [
      [0, 0],
      [400, 0],
      [400, 300],
      [0, 300],
    ]
    const resultat = resolveSnap(
      { x: 395, y: 5 },
      { vertices: verticesExcept(carre, 1), gridCm: 5, toleranceCm: 12 },
    )

    expect(resultat.point).toEqual({ x: 400, y: 0 })
  })
})

describe('collecte des sommets', () => {
  it('dédoublonne les angles partagés par deux pièces', () => {
    const sommets = collectVertices([
      [
        [0, 0],
        [400, 0],
      ],
      [
        [400, 0],
        [400, 300],
      ],
    ])

    expect(sommets).toEqual([
      { x: 0, y: 0 },
      { x: 400, y: 0 },
      { x: 400, y: 300 },
    ])
  })
})
