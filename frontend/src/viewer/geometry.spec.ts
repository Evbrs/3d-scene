import { BoxGeometry, type BufferGeometry, ExtrudeGeometry, Shape } from 'three'
import { describe, expect, it } from 'vitest'

import { buildShape, primitiveGeometry } from '@/viewer/geometry'

const OUTLINE: number[][] = [
  [0, 0],
  [400, 0],
  [400, 250],
  [0, 250],
]

type Axis = 'x' | 'y' | 'z'

const along = (geometry: BufferGeometry, axis: Axis, index: number): number => {
  const position = geometry.attributes.position!
  if (axis === 'x') return position.getX(index)
  if (axis === 'y') return position.getY(index)
  return position.getZ(index)
}

/** Dimensions de la boîte englobante, arrondies : une rotation laisse des résidus à 1e-17. */
function boxOf(geometry: BufferGeometry): number[] {
  geometry.computeBoundingBox()
  const box = geometry.boundingBox!
  return [box.max.x - box.min.x, box.max.y - box.min.y, box.max.z - box.min.z].map((value) =>
    Number(value.toFixed(6)),
  )
}

/** Demi-étendue, mesurée sur `measure`, des seuls sommets posés sur un plan donné. */
function spreadAt(geometry: BufferGeometry, slice: Axis, at: number, measure: Axis): number {
  const position = geometry.attributes.position!
  let widest = 0
  for (let index = 0; index < position.count; index += 1) {
    if (Math.abs(along(geometry, slice, index) - at) > 1e-6) continue
    widest = Math.max(widest, Math.abs(along(geometry, measure, index)))
  }
  return widest
}

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

  it('donne au cylindre une section elliptique quand la boîte n’est pas carrée', () => {
    // Volontairement 40 x 20 : la version précédente de ce test utilisait 20 x 20, où l'erreur
    // corrigée ici — demi-largeur en haut, demi-profondeur en bas — était invisible.
    const geometry = primitiveGeometry([40, 60, 20], 'cylinder')

    expect(boxOf(geometry)).toEqual([40, 60, 20])
  })

  it('reste un cylindre et ne dégénère pas en cône', () => {
    // `CylinderGeometry(largeur/2, profondeur/2, ...)` donne un tronc de cône : le disque du haut
    // fait 40 de large et celui du bas 20. Cinq recettes du catalogue livré sont dans ce cas.
    const geometry = primitiveGeometry([40, 60, 20], 'cylinder')

    expect(spreadAt(geometry, 'y', 30, 'x')).toBeCloseTo(20, 6)
    expect(spreadAt(geometry, 'y', -30, 'x')).toBeCloseTo(20, 6)
  })

  it('couche le cylindre sur l’axe déclaré par la primitive', () => {
    // Une poignée de porte, une barre d'appui ou une tringle sont des cylindres couchés : sans
    // `axis`, ils sont dressés à la verticale au milieu du meuble.
    // La boîte englobante seule ne prouve rien — elle vaut 60 x 20 x 40 dans les trois cas. Ce
    // qui distingue l'orientation, c'est le disque de fermeture : sur l'extrémité de l'axe de
    // révolution, la section est pleine ; sur celle d'un autre axe, elle est réduite à une arête.
    expect(boxOf(primitiveGeometry([60, 20, 40], 'cylinder', 'x'))).toEqual([60, 20, 40])
    expect(spreadAt(primitiveGeometry([60, 20, 40], 'cylinder', 'x'), 'x', 30, 'z')).toBeCloseTo(20)
    expect(spreadAt(primitiveGeometry([60, 20, 40], 'cylinder', 'y'), 'x', 30, 'z')).toBeCloseTo(0)

    expect(boxOf(primitiveGeometry([60, 20, 40], 'cylinder', 'z'))).toEqual([60, 20, 40])
    expect(spreadAt(primitiveGeometry([60, 20, 40], 'cylinder', 'z'), 'z', 20, 'x')).toBeCloseTo(30)
    expect(spreadAt(primitiveGeometry([60, 20, 40], 'cylinder', 'y'), 'z', 20, 'x')).toBeCloseTo(0)
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


describe('extrusion réelle du mur', () => {
  const options = { depth: 15, bevelEnabled: false }

  it('perce vraiment la géométrie : un mur troué a plus de faces qu’un mur plein', () => {
    // Un trou n'ajoute pas seulement un contour : il crée le tableau (les faces intérieures du
    // percement). Si le nombre de sommets ne bouge pas, c'est que le trou a été ignoré.
    const plein = new ExtrudeGeometry(buildShape(OUTLINE, []), options)
    const perce = new ExtrudeGeometry(
      buildShape(OUTLINE, [[[80, 95], [230, 95], [230, 215], [80, 215]]]),
      options,
    )

    expect(perce.attributes.position!.count).toBeGreaterThan(plein.attributes.position!.count)
  })

  it('retire de la matière plutôt que d’en ajouter', () => {
    // La surface triangulée de la face avant doit diminuer de l'aire du trou.
    const aire = (geometry: ExtrudeGeometry): number => {
      const position = geometry.attributes.position!
      let total = 0
      for (let index = 0; index < position.count; index += 3) {
        const ax = position.getX(index), ay = position.getY(index), az = position.getZ(index)
        const bx = position.getX(index + 1), by = position.getY(index + 1), bz = position.getZ(index + 1)
        const cx = position.getX(index + 2), cy = position.getY(index + 2), cz = position.getZ(index + 2)
        // Seules les faces planes en z = 0 (face avant du mur) nous intéressent.
        if (az !== 0 || bz !== 0 || cz !== 0) continue
        total += Math.abs((bx - ax) * (cy - ay) - (cx - ax) * (by - ay)) / 2
      }
      return total
    }

    const plein = aire(new ExtrudeGeometry(buildShape(OUTLINE, []), options))
    const perce = aire(
      new ExtrudeGeometry(
        buildShape(OUTLINE, [[[80, 95], [230, 95], [230, 215], [80, 215]]]),
        options,
      ),
    )

    // Trou de 150 x 120 = 18 000 cm².
    expect(plein - perce).toBeCloseTo(18000, 0)
  })

  it('cumule plusieurs ouvertures sur le même mur', () => {
    const geometry = new ExtrudeGeometry(
      buildShape(OUTLINE, [
        [[80, 95], [230, 95], [230, 215], [80, 215]],
        [[420, 0], [510, 0], [510, 204], [420, 204]],
      ]),
      options,
    )
    const plein = new ExtrudeGeometry(buildShape(OUTLINE, []), options)

    expect(geometry.attributes.position!.count).toBeGreaterThan(
      plein.attributes.position!.count,
    )
  })
})
