import { BoxGeometry, CylinderGeometry, Shape } from 'three'
import { describe, expect, it } from 'vitest'

import { buildShape, primitiveGeometry } from '@/viewer/geometry'

const OUTLINE = [
  [0, 0],
  [400, 0],
  [400, 250],
  [0, 250],
]

describe('construction des formes murales', () => {
  it('reproduit le contour reçu du backend', () => {
    const shape = buildShape(OUTLINE, [])

    expect(shape).toBeInstanceOf(Shape)
    const points = shape.getPoints()
    expect(points[0]?.x).toBe(0)
    expect(points[0]?.y).toBe(0)
    // Le contour est fermé : le dernier point rejoint le premier.
    expect(points[points.length - 1]?.x).toBeCloseTo(0)
  })

  it('transforme chaque ouverture en trou (spec §3.2)', () => {
    const holes = [
      [
        [80, 100],
        [170, 100],
        [170, 210],
        [80, 210],
      ],
    ]

    const shape = buildShape(OUTLINE, holes)

    expect(shape.holes).toHaveLength(1)
    const holePoints = shape.holes[0]!.getPoints()
    expect(holePoints[0]?.x).toBe(80)
    expect(holePoints[0]?.y).toBe(100)
  })

  it('accepte plusieurs ouvertures sur le même mur', () => {
    const shape = buildShape(OUTLINE, [
      [[80, 100], [170, 100], [170, 210], [80, 210]],
      [[250, 0], [340, 0], [340, 204], [250, 204]],
    ])

    expect(shape.holes).toHaveLength(2)
  })

  it("n'ajoute aucun trou quand le mur est plein", () => {
    expect(buildShape(OUTLINE, []).holes).toHaveLength(0)
  })
})

describe('primitives de mobilier', () => {
  it('met la boîte aux dimensions reçues', () => {
    const geometry = primitiveGeometry([100, 85, 45], 'box') as BoxGeometry

    expect(geometry.parameters.width).toBe(100)
    expect(geometry.parameters.height).toBe(85)
    expect(geometry.parameters.depth).toBe(45)
  })

  it('déduit les rayons du cylindre de sa boîte englobante', () => {
    const geometry = primitiveGeometry([20, 60, 20], 'cylinder') as CylinderGeometry

    expect(geometry.parameters.radiusTop).toBe(10)
    expect(geometry.parameters.radiusBottom).toBe(10)
    expect(geometry.parameters.height).toBe(60)
  })

  it('met la sphère à l’échelle pour suivre une boîte non cubique', () => {
    const geometry = primitiveGeometry([40, 20, 40], 'sphere')
    geometry.computeBoundingBox()

    const box = geometry.boundingBox!
    expect(box.max.x - box.min.x).toBeCloseTo(40, 1)
    expect(box.max.y - box.min.y).toBeCloseTo(20, 1)
  })

  it('retombe sur une boîte pour un type inconnu', () => {
    expect(primitiveGeometry([10, 10, 10], 'inconnu')).toBeInstanceOf(BoxGeometry)
  })
})
