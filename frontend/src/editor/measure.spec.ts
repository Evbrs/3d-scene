/**
 * Cotation permanente et correction d'une longueur de mur au chiffre.
 *
 * Les valeurs affichées ici sont celles que l'artisan recopie sur son devis : une erreur de
 * périmètre est une erreur de linéaire de plinthe, donc une erreur de prix.
 */
import { describe, expect, it } from 'vitest'

import { segmentLength } from '@/editor/geometry'
import {
  formatAreaM2,
  formatLengthCm,
  perimeterCm,
  resizeWall,
  roomMetrics,
  wallMeasures,
} from '@/editor/measure'

const CARRE = [
  [0, 0],
  [400, 0],
  [400, 300],
  [0, 300],
]

describe('cotes des murs', () => {
  it('donne une cote par côté, lettrée dans l’ordre du contour', () => {
    const mesures = wallMeasures(CARRE)

    expect(mesures.map((mesure) => mesure.label)).toEqual(['A', 'B', 'C', 'D'])
    expect(mesures.map((mesure) => mesure.lengthCm)).toEqual([400, 300, 400, 300])
  })

  it('referme le contour : le dernier mur revient au premier sommet', () => {
    const dernier = wallMeasures(CARRE).at(-1)!

    expect(dernier.to).toEqual({ x: 0, y: 0 })
  })

  it('ne coter rien tant que le contour n’est pas une pièce', () => {
    expect(wallMeasures([[0, 0], [100, 0]])).toEqual([])
    expect(perimeterCm([])).toBe(0)
  })

  it('mesure le périmètre sur l’axe des murs, comme les cotes affichées', () => {
    expect(perimeterCm(CARRE)).toBe(1400)
  })

  it('rend surface, périmètre et nombre de murs d’un coup', () => {
    expect(roomMetrics(CARRE)).toEqual({ areaM2: 12, perimeterCm: 1400, wallCount: 4 })
  })
})

describe('correction d’une longueur de mur', () => {
  it('déplace le sommet d’arrivée le long du mur', () => {
    const corrige = resizeWall(CARRE, 0, 347)

    expect(corrige[1]).toEqual([347, 0])
    expect(segmentLength({ x: 0, y: 0 }, { x: corrige[1]![0]!, y: corrige[1]![1]! })).toBe(347)
  })

  it('laisse le sommet de départ intact — on part toujours d’un coin qu’on croit juste', () => {
    const corrige = resizeWall(CARRE, 1, 250)

    expect(corrige[1]).toEqual([400, 0])
    expect(corrige[2]).toEqual([400, 250])
  })

  it('suit la direction du mur, même en oblique', () => {
    const oblique = [
      [0, 0],
      [300, 400],
      [0, 400],
    ]
    const corrige = resizeWall(oblique, 0, 250)

    expect(corrige[1]![0]).toBeCloseTo(150, 6)
    expect(corrige[1]![1]).toBeCloseTo(200, 6)
  })

  it('referme sur le premier sommet quand on corrige le dernier mur', () => {
    const corrige = resizeWall(CARRE, 3, 100)

    expect(corrige[0]).toEqual([0, 200])
  })

  it('ne modifie pas le contour reçu', () => {
    const original = CARRE.map((sommet) => [...sommet])
    resizeWall(CARRE, 0, 999)

    expect(CARRE).toEqual(original)
  })

  it('refuse une longueur nulle ou négative', () => {
    expect(() => resizeWall(CARRE, 0, 0)).toThrow()
    expect(() => resizeWall(CARRE, 0, -10)).toThrow()
  })

  it('refuse un mur qui n’existe pas', () => {
    expect(() => resizeWall(CARRE, 9, 100)).toThrow('inexistant')
  })

  it('n’invente pas d’angle sur un mur de longueur nulle', () => {
    const degenere = [
      [0, 0],
      [0, 0],
      [100, 100],
    ]

    expect(resizeWall(degenere, 0, 200)).toEqual(degenere)
  })
})

describe('affichage', () => {
  it('reste au centimètre entier : le mètre laser ne donne pas le millimètre', () => {
    expect(formatLengthCm(397.3)).toContain('397 cm')
  })

  it('rappelle le mètre au-delà de cent centimètres', () => {
    expect(formatLengthCm(347)).toBe('347 cm (3,47 m)')
    expect(formatLengthCm(83)).toBe('83 cm')
  })

  it('écrit les surfaces à la française', () => {
    expect(formatAreaM2(12.5)).toBe('12,50 m²')
  })
})
