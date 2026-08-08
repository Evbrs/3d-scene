/**
 * Traduction des gestes en opérations de lot, et surtout de leur **inverse**.
 *
 * C'est ce qui rend l'annulation vérifiable sans monter le moindre composant : l'inverse d'un
 * déplacement est un déplacement, et un test peut le dire. Le second point surveillé est le
 * traitement séparé des deux ancrages — un meuble adossé ne peut que glisser le long de son mur,
 * et lui appliquer le vecteur brut le décollerait, ce que le modèle interdit (spec §10, A4).
 */
import { describe, expect, it } from 'vitest'

import { MAX_BATCH_OPERATIONS } from '@/api/client'
import type { Face, PlanElement } from '@/api/types'
import { wallGeometries } from '@/editor/drawing'
import {
  chunkOperations,
  deleteOperations,
  describeCount,
  duplicateOperations,
  moveOperations,
  normalizeAngle,
  placementOf,
  recreateOperations,
  restoreOperations,
  rotateOperations,
} from '@/editor/operations'

const CARRE = [
  [0, 0],
  [400, 0],
  [400, 300],
  [0, 300],
]

function face(id: number, label: string): Face {
  return {
    id,
    room_id: 7,
    label,
    kind: 'wall',
    start_x_cm: 0,
    start_y_cm: 0,
    end_x_cm: 0,
    end_y_cm: 0,
    covering: {},
    elements: [],
  }
}

const WALLS = new Map(
  wallGeometries(CARRE, [face(11, 'A'), face(12, 'B'), face(13, 'C'), face(14, 'D')])
    .filter((wall) => wall.face)
    .map((wall) => [wall.face.id, wall]),
)

const CONTEXTE = { walls: WALLS, polygon: CARRE }

function libre(overrides: Partial<PlanElement> = {}): PlanElement {
  return {
    id: 1,
    face_id: null,
    room_id: 7,
    kind: 'furniture',
    x_offset_cm: 0,
    y_offset_cm: 0,
    pos_x_cm: 200,
    pos_y_cm: 150,
    width_cm: 100,
    height_cm: 50,
    depth_cm: 60,
    rotation_deg: 0,
    furniture_type_id: 3,
    colors: { corps: '#ffffff' },
    variant_params: {},
    ...overrides,
  }
}

function adosse(overrides: Partial<PlanElement> = {}): PlanElement {
  return {
    ...libre(),
    id: 2,
    face_id: 11,
    room_id: null,
    pos_x_cm: null,
    pos_y_cm: null,
    x_offset_cm: 100,
    ...overrides,
  }
}

describe('placement d’un élément', () => {
  it('lit le repère qui correspond à son ancrage', () => {
    expect(placementOf(libre())).toEqual({ pos_x_cm: 200, pos_y_cm: 150 })
    expect(placementOf(adosse())).toEqual({ x_offset_cm: 100, y_offset_cm: 0 })
  })
})

describe('déplacement', () => {
  it('applique le vecteur entier à un meuble libre', () => {
    const operations = moveOperations([libre()], { x: 50, y: -20 }, CONTEXTE)

    expect(operations).toEqual([
      { op: 'update_element', element_id: 1, changes: { pos_x_cm: 250, pos_y_cm: 130 } },
    ])
  })

  it('ne garde que la composante parallèle au mur pour un élément adossé', () => {
    // Le mur A va de (0,0) à (400,0) : seule la composante en X fait glisser le meuble. La
    // composante en Y le décollerait du mur, ce que le modèle interdit.
    const operations = moveOperations([adosse()], { x: 50, y: 200 }, CONTEXTE)

    expect(operations).toEqual([
      { op: 'update_element', element_id: 2, changes: { x_offset_cm: 150 } },
    ])
  })

  it('borne le glissement aux extrémités du mur', () => {
    const operations = moveOperations([adosse({ x_offset_cm: 280 })], { x: 200, y: 0 }, CONTEXTE)

    expect(operations[0]).toMatchObject({ changes: { x_offset_cm: 300 } })
  })

  it('ramène dans la pièce un meuble libre poussé dehors, plutôt que de refuser', () => {
    const operations = moveOperations([libre()], { x: 500, y: 0 }, CONTEXTE)

    expect(operations).toHaveLength(1)
    const changes = (operations[0] as { changes: { pos_x_cm: number } }).changes
    expect(changes.pos_x_cm).toBeLessThanOrEqual(350)
  })

  it('omet ce qui ne bouge pas au lieu d’envoyer une écriture vide', () => {
    expect(moveOperations([adosse({ x_offset_cm: 300 })], { x: 100, y: 0 }, CONTEXTE)).toEqual([])
  })

  it('omet un élément dont le mur a disparu au lieu d’annuler tout le geste', () => {
    const orphelin = adosse({ id: 99, face_id: 999 })
    const operations = moveOperations([orphelin, libre()], { x: 10, y: 10 }, CONTEXTE)

    expect(operations).toHaveLength(1)
    expect(operations[0]).toMatchObject({ element_id: 1 })
  })

  it('traite les deux ancrages dans un seul lot', () => {
    const operations = moveOperations([libre(), adosse()], { x: 20, y: 0 }, CONTEXTE)

    expect(operations).toHaveLength(2)
    expect(operations[0]).toMatchObject({ changes: { pos_x_cm: 220 } })
    expect(operations[1]).toMatchObject({ changes: { x_offset_cm: 120 } })
  })
})

describe('inverse d’un geste', () => {
  it('remet chaque élément là où il était, avec ses cotes', () => {
    const elements = [libre(), adosse()]
    const retour = restoreOperations(elements)

    expect(retour[0]).toEqual({
      op: 'update_element',
      element_id: 1,
      changes: { pos_x_cm: 200, pos_y_cm: 150, width_cm: 100, depth_cm: 60, rotation_deg: 0 },
    })
    expect(retour[1]).toMatchObject({ changes: { x_offset_cm: 100, y_offset_cm: 0 } })
  })

  it('l’inverse d’un déplacement, appliqué, rend le placement d’origine', () => {
    const depart = libre()
    const aller = moveOperations([depart], { x: 50, y: 50 }, CONTEXTE)
    const deplace = { ...depart, pos_x_cm: 250, pos_y_cm: 200 }
    const retour = restoreOperations([depart])

    expect(aller[0]).toMatchObject({ changes: { pos_x_cm: 250, pos_y_cm: 200 } })
    expect(placementOf(deplace)).not.toEqual(placementOf(depart))
    expect(retour[0]).toMatchObject({ changes: placementOf(depart) })
  })

  it('l’inverse d’une rotation est la rotation opposée', () => {
    const aller = rotateOperations([libre()], 15)
    const retour = rotateOperations([libre({ rotation_deg: 15 })], -15)

    expect(aller[0]).toMatchObject({ changes: { rotation_deg: 15 } })
    expect(retour[0]).toMatchObject({ changes: { rotation_deg: 0 } })
  })
})

describe('rotation', () => {
  it('ne s’applique qu’au mobilier posé au sol', () => {
    // Un élément adossé n'a pas d'orientation propre : il suit son mur.
    expect(rotateOperations([adosse()], 15)).toEqual([])
  })

  it('reste dans [0, 360[ pour ne pas sortir des bornes du serveur', () => {
    expect(normalizeAngle(375)).toBe(15)
    expect(normalizeAngle(-15)).toBe(345)
    expect(rotateOperations([libre({ rotation_deg: 350 })], 15)[0]).toMatchObject({
      changes: { rotation_deg: 5 },
    })
  })
})

describe('recréation et duplication', () => {
  it('recrée un élément adossé dans son repère de face', () => {
    const [operation] = recreateOperations([adosse()])

    expect(operation).toMatchObject({
      op: 'create_face_element',
      face_id: 11,
      element: { x_offset_cm: 100, y_offset_cm: 0, kind: 'furniture', width_cm: 100 },
    })
    // Le repère de pièce n'a rien à faire dans une création sur face : le serveur refuse.
    expect(JSON.stringify(operation)).not.toContain('pos_x_cm')
  })

  it('recrée un meuble libre dans le repère de la pièce', () => {
    const [operation] = recreateOperations([libre()])

    expect(operation).toMatchObject({
      op: 'create_room_element',
      room_id: 7,
      element: { pos_x_cm: 200, pos_y_cm: 150, colors: { corps: '#ffffff' } },
    })
    expect(JSON.stringify(operation)).not.toContain('x_offset_cm')
  })

  it('duplique en décalant, pour que la copie soit visible', () => {
    const [operation] = duplicateOperations([libre()], { x: 20, y: 20 }, CONTEXTE)

    expect(operation).toMatchObject({ element: { pos_x_cm: 220, pos_y_cm: 170 } })
  })

  it('ramène la copie dans la pièce plutôt que de la poser dehors', () => {
    // Un meuble qui remplit presque la pièce ne peut pas être décalé de 2 m : la copie bute
    // contre le contour, ce qui vaut mieux qu'un 422 après le geste.
    const enorme = libre({ width_cm: 390, depth_cm: 290 })
    const [operation] = duplicateOperations([enorme], { x: 200, y: 200 }, CONTEXTE)

    expect(operation).toMatchObject({ op: 'create_room_element' })
    const element = (operation as { element: { pos_x_cm: number; pos_y_cm: number } }).element
    expect(element.pos_x_cm).toBeLessThanOrEqual(205)
    expect(element.pos_y_cm).toBeLessThanOrEqual(155)
  })

  it('n’inclut pas ce qui ne tient nulle part dans la pièce', () => {
    const impossible = libre({ width_cm: 900, depth_cm: 60 })

    expect(duplicateOperations([impossible], { x: 20, y: 20 }, CONTEXTE)).toEqual([])
  })
})

describe('suppression', () => {
  it('produit une opération par identifiant', () => {
    expect(deleteOperations([4, 9])).toEqual([
      { op: 'delete_element', element_id: 4 },
      { op: 'delete_element', element_id: 9 },
    ])
  })
})

describe('découpage sous la borne du serveur', () => {
  it('laisse un lot court intact', () => {
    const operations = deleteOperations([1, 2, 3])

    expect(chunkOperations(operations)).toEqual([operations])
  })

  it('découpe au-delà de cent opérations', () => {
    const operations = deleteOperations(Array.from({ length: 250 }, (_, index) => index + 1))
    const paquets = chunkOperations(operations)

    expect(paquets.map((paquet) => paquet.length)).toEqual([100, 100, 50])
    expect(MAX_BATCH_OPERATIONS).toBe(100)
    expect(paquets.flat()).toEqual(operations)
  })

  it('refuse une taille de paquet absurde plutôt que de boucler à l’infini', () => {
    expect(() => chunkOperations(deleteOperations([1]), 0)).toThrow()
  })

  it('rend une liste vide sur un lot vide', () => {
    expect(chunkOperations([])).toEqual([])
  })
})

describe('libellés', () => {
  it('accorde le pluriel : on lit ces messages', () => {
    expect(describeCount(1, 'élément', 'éléments')).toBe('1 élément')
    expect(describeCount(3, 'élément', 'éléments')).toBe('3 éléments')
  })
})
