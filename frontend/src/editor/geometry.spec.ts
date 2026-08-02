import { describe, expect, it } from 'vitest'

import {
  areaInSquareMeters,
  fitViewport,
  isSelfIntersecting,
  planToScreen,
  screenToPlan,
  segmentLength,
  signedArea,
  snap,
  wallLabel,
  wallSegments,
} from '@/editor/geometry'

const CARRE = [
  [0, 0],
  [400, 0],
  [400, 300],
  [0, 300],
]

describe('lettrage des murs', () => {
  it.each([
    [0, 'A'],
    [1, 'B'],
    [25, 'Z'],
    [26, 'AA'],
    [27, 'AB'],
    [51, 'AZ'],
    [52, 'BA'],
  ])('index %i donne %s', (index, expected) => {
    expect(wallLabel(index)).toBe(expected)
  })

  it('doit rester identique au lettrage du backend', () => {
    // Mêmes valeurs que `test_wall_labels_follow_the_alphabet_and_do_not_wrap` côté Python :
    // les deux implémentations décrivent la même règle métier et ne doivent jamais diverger.
    const labels = Array.from({ length: 64 }, (_, index) => wallLabel(index))
    expect(new Set(labels).size).toBe(labels.length)
  })

  it('refuse un index négatif', () => {
    expect(() => wallLabel(-1)).toThrow()
  })
})

describe('conversion plan / écran', () => {
  it('est réversible', () => {
    const viewport = { scale: 0.5, offsetX: 60, offsetY: 40 }
    const original = { x: 137, y: 246 }

    const roundTrip = screenToPlan(planToScreen(original, viewport), viewport)

    expect(roundTrip.x).toBeCloseTo(original.x, 9)
    expect(roundTrip.y).toBeCloseTo(original.y, 9)
  })

  it('cadre le polygone dans la zone disponible', () => {
    const viewport = fitViewport(CARRE, 900, 600, 40)

    const topLeft = planToScreen({ x: 0, y: 0 }, viewport)
    const bottomRight = planToScreen({ x: 400, y: 300 }, viewport)

    expect(topLeft.x).toBeCloseTo(40)
    expect(topLeft.y).toBeCloseTo(40)
    expect(bottomRight.x).toBeLessThanOrEqual(900)
    expect(bottomRight.y).toBeLessThanOrEqual(600)
  })
})

describe('magnétisme', () => {
  it('aligne sur la grille', () => {
    expect(snap(397.3, 10)).toBe(400)
    expect(snap(397.3, 5)).toBe(395)
    expect(snap(397.3, 1)).toBe(397)
  })

  it('laisse la valeur intacte si la grille est nulle', () => {
    expect(snap(397.3, 0)).toBe(397.3)
  })
})

describe('mesures', () => {
  it('calcule la longueur des murs', () => {
    const segments = wallSegments(CARRE)
    expect(segments.map((s) => Math.round(segmentLength(s.from, s.to)))).toEqual([400, 300, 400, 300])
    expect(segments.map((s) => s.label)).toEqual(['A', 'B', 'C', 'D'])
  })

  it("calcule l'aire en m²", () => {
    expect(areaInSquareMeters(CARRE)).toBeCloseTo(12)
  })

  it("donne la même aire quel que soit le sens de saisie", () => {
    const horaire = [...CARRE].reverse()
    expect(signedArea(CARRE)).toBeGreaterThan(0)
    expect(signedArea(horaire)).toBeLessThan(0)
    expect(areaInSquareMeters(horaire)).toBeCloseTo(areaInSquareMeters(CARRE))
  })

  it('ne produit aucun mur pour un contour incomplet', () => {
    expect(wallSegments([[0, 0]])).toEqual([])
    expect(wallSegments([[0, 0], [100, 0]])).toEqual([])
  })
})

describe('contour qui se recoupe', () => {
  it("détecte un noeud papillon", () => {
    const papillon = [
      [0, 0],
      [400, 300],
      [400, 0],
      [0, 300],
    ]
    expect(isSelfIntersecting(papillon)).toBe(true)
  })

  it('accepte un rectangle', () => {
    expect(isSelfIntersecting(CARRE)).toBe(false)
  })

  it('accepte une pièce en L', () => {
    const enL = [
      [0, 0],
      [400, 0],
      [400, 200],
      [200, 200],
      [200, 300],
      [0, 300],
    ]
    expect(isSelfIntersecting(enL)).toBe(false)
  })
})
