import { describe, expect, it } from 'vitest'

import type { Face, PlanElement } from '@/api/types'
import {
  furnitureFootprint,
  gridLines,
  isOpening,
  openingSymbol,
  wallGeometries,
  wallOutline,
} from '@/editor/drawing'
import type { Viewport } from '@/editor/geometry'

const CARRE = [
  [0, 0],
  [400, 0],
  [400, 300],
  [0, 300],
]

const VIEWPORT: Viewport = { scale: 1, offsetX: 0, offsetY: 0 }

function face(label: string, elements: PlanElement[] = []): Face {
  return {
    id: label.charCodeAt(0),
    room_id: 1,
    label,
    kind: 'wall',
    start_x_cm: 0,
    start_y_cm: 0,
    end_x_cm: 0,
    end_y_cm: 0,
    covering: {},
    elements,
  }
}

function element(kind: string, overrides: Partial<PlanElement> = {}): PlanElement {
  return {
    id: 1,
    face_id: 65,
    room_id: null,
    kind: kind as PlanElement['kind'],
    x_offset_cm: 100,
    y_offset_cm: 0,
    pos_x_cm: null,
    pos_y_cm: null,
    width_cm: 90,
    height_cm: 200,
    depth_cm: 40,
    rotation_deg: 0,
    furniture_type_id: null,
    colors: {},
    variant_params: {},
    ...overrides,
  }
}

const FACES = [face('A'), face('B'), face('C'), face('D')]

describe('géométrie des murs', () => {
  it('produit un mur par côté, dans l’ordre du polygone', () => {
    const walls = wallGeometries(CARRE, FACES)

    expect(walls).toHaveLength(4)
    expect(walls.map((w) => w.face.label)).toEqual(['A', 'B', 'C', 'D'])
    expect(walls[0]?.lengthCm).toBe(400)
    expect(walls[1]?.lengthCm).toBe(300)
  })

  it('oriente les normales vers l’extérieur de la pièce', () => {
    // Contour trigonométrique : l'intérieur du carré est en y > 0 pour le mur A.
    const walls = wallGeometries(CARRE, FACES)
    expect(walls[0]?.outward).toEqual({ x: 0, y: -1 })
    expect(walls[1]?.outward).toEqual({ x: 1, y: 0 })
  })

  it('garde les normales sortantes quand le contour est décrit dans l’autre sens', () => {
    // Sans cette correction, cotes et symboles basculeraient à l'intérieur de la pièce.
    const horaire = [...CARRE].reverse()
    const walls = wallGeometries(horaire, FACES)

    for (const wall of walls) {
      const middle = { x: (wall.from.x + wall.to.x) / 2, y: (wall.from.y + wall.to.y) / 2 }
      const outside = { x: middle.x + wall.outward.x * 10, y: middle.y + wall.outward.y * 10 }
      const inside = { x: middle.x - wall.outward.x * 10, y: middle.y - wall.outward.y * 10 }
      // Le point « extérieur » doit être plus loin du centre (200, 150) que le point intérieur.
      const distance = (p: { x: number; y: number }): number => Math.hypot(p.x - 200, p.y - 150)
      expect(distance(outside)).toBeGreaterThan(distance(inside))
    }
  })

  it('donne au mur une épaisseur centrée sur son axe', () => {
    const walls = wallGeometries(CARRE, FACES)
    const outline = wallOutline(walls[0]!, 20, VIEWPORT)

    // 4 coins = 8 coordonnées ; le mur A est horizontal, donc il occupe y ∈ [-10, 10].
    expect(outline).toHaveLength(8)
    const ys = outline.filter((_, index) => index % 2 === 1)
    expect(Math.min(...ys)).toBe(-10)
    expect(Math.max(...ys)).toBe(10)
  })
})

describe('symboles d’ouverture', () => {
  it('reconnaît les ouvertures et laisse le mobilier de côté', () => {
    expect(isOpening(element('window'))).toBe(true)
    expect(isOpening(element('door_hinged'))).toBe(true)
    expect(isOpening(element('door_sliding'))).toBe(true)
    expect(isOpening(element('furniture'))).toBe(false)
  })

  it('perce une trémie à la bonne place sur le mur', () => {
    const wall = wallGeometries(CARRE, FACES)[0]!
    const symbol = openingSymbol(wall, element('window', { x_offset_cm: 100, width_cm: 90 }), 20, VIEWPORT)

    const xs = symbol.gap.filter((_, index) => index % 2 === 0)
    expect(Math.min(...xs)).toBe(100)
    expect(Math.max(...xs)).toBe(190)
  })

  it('donne un arc de débattement à une porte battante, et pas aux autres', () => {
    const wall = wallGeometries(CARRE, FACES)[0]!

    expect(openingSymbol(wall, element('door_hinged'), 20, VIEWPORT).arc).not.toBeNull()
    expect(openingSymbol(wall, element('window'), 20, VIEWPORT).arc).toBeNull()
    expect(openingSymbol(wall, element('door_sliding'), 20, VIEWPORT).arc).toBeNull()
  })

  it('fait battre la porte vers l’intérieur de la pièce', () => {
    const wall = wallGeometries(CARRE, FACES)[0]!
    const symbol = openingSymbol(wall, element('door_hinged', { x_offset_cm: 100 }), 20, VIEWPORT)

    // Le vantail part du gond et va vers l'intérieur (y croissant pour le mur A).
    const [, , , tipY] = symbol.strokes[0] as number[]
    expect(tipY).toBeGreaterThan(0)
  })

  it('trace un vitrage pour une fenêtre', () => {
    const wall = wallGeometries(CARRE, FACES)[0]!
    expect(openingSymbol(wall, element('window'), 20, VIEWPORT).strokes.length).toBeGreaterThan(0)
  })
})

describe('emprise du mobilier', () => {
  it('adosse le meuble au mur et l’étend vers l’intérieur', () => {
    const wall = wallGeometries(CARRE, FACES)[0]!
    const footprint = furnitureFootprint(
      wall,
      element('furniture', { x_offset_cm: 50, width_cm: 100, depth_cm: 45 }),
      20,
      VIEWPORT,
      'Commode',
    )

    const ys = footprint.outline.filter((_, index) => index % 2 === 1)
    // Face intérieure du mur à y = 10 ; le meuble occupe donc y ∈ [10, 55].
    expect(Math.min(...ys)).toBeCloseTo(10)
    expect(Math.max(...ys)).toBeCloseTo(55)
    expect(footprint.label).toBe('Commode')
  })
})

describe('grille', () => {
  it('disparaît quand le pas devient illisible', () => {
    expect(gridLines(800, 600, { scale: 0.001, offsetX: 0, offsetY: 0 }, 100)).toEqual([])
  })

  it('couvre toute la surface visible', () => {
    const lines = gridLines(800, 600, VIEWPORT, 100)
    expect(lines.length).toBeGreaterThan(10)
  })
})
