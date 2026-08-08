/**
 * Sélection multiple et presse-papier.
 *
 * Le piège surveillé ici est le collage d'un élément **adossé** dans une autre pièce : le
 * transformer en meuble libre changerait son repère en silence, ce que la spec interdit (§10,
 * A4). Il est reporté sur la face homonyme, ou refusé en disant pourquoi — jamais converti.
 */
import { describe, expect, it } from 'vitest'

import type { Face, PlanElement, Room } from '@/api/types'
import {
  copyToClipboard,
  elementsInRect,
  freeCenter,
  isNegligibleRect,
  normalizeRect,
  preparePaste,
  pruneSelection,
  rectContains,
  roomElements,
  toggleSelection,
} from '@/editor/selection'

function element(overrides: Partial<PlanElement> = {}): PlanElement {
  return {
    id: 1,
    face_id: null,
    room_id: 7,
    kind: 'furniture',
    x_offset_cm: 0,
    y_offset_cm: 0,
    pos_x_cm: 100,
    pos_y_cm: 100,
    width_cm: 80,
    height_cm: 50,
    depth_cm: 40,
    rotation_deg: 0,
    furniture_type_id: 2,
    colors: {},
    variant_params: {},
    ...overrides,
  }
}

function face(id: number, roomId: number, label: string, elements: PlanElement[] = []): Face {
  return {
    id,
    room_id: roomId,
    label,
    kind: 'wall',
    start_x_cm: 0,
    start_y_cm: 0,
    end_x_cm: 400,
    end_y_cm: 0,
    covering: {},
    elements,
  }
}

function room(id: number, name: string, faces: Face[], free: PlanElement[] = []): Room {
  return {
    id,
    project_id: 1,
    name,
    wall_thickness_cm: 10,
    ceiling_height_cm: 250,
    polygon: [
      [0, 0],
      [400, 0],
      [400, 300],
      [0, 300],
    ],
    background_url: null,
    background_scale_cm_per_px: null,
    background_offset_x_cm: 0,
    background_offset_y_cm: 0,
    background_rotation_deg: 0,
    background_opacity: 1,
    faces,
    free_elements: free,
  }
}

describe('rectangle d’encadrement', () => {
  it('se normalise quel que soit le sens du glisser', () => {
    const versLeHaut = normalizeRect({ x: 300, y: 200 }, { x: 100, y: 50 })

    expect(versLeHaut).toEqual({ minX: 100, minY: 50, maxX: 300, maxY: 200 })
  })

  it('retient les centres encadrés, pas les emprises entièrement dedans', () => {
    // Encadrer une rangée de meubles bas obligerait sinon à englober aussi le mur, donc la pièce
    // d'à côté. C'est la convention de tous les éditeurs de plan.
    const items = [
      { id: 1, center: { x: 100, y: 100 } },
      { id: 2, center: { x: 350, y: 100 } },
    ]

    expect(elementsInRect(items, normalizeRect({ x: 0, y: 0 }, { x: 200, y: 200 }))).toEqual([1])
  })

  it('inclut un centre posé exactement sur le bord', () => {
    expect(rectContains({ minX: 0, minY: 0, maxX: 10, maxY: 10 }, { x: 10, y: 0 })).toBe(true)
  })

  it('reconnaît un encadrement d’un pixel comme un clic', () => {
    const clic = normalizeRect({ x: 100, y: 100 }, { x: 100.5, y: 100.5 })

    expect(isNegligibleRect(clic, 2)).toBe(true)
    expect(isNegligibleRect(normalizeRect({ x: 0, y: 0 }, { x: 50, y: 50 }), 2)).toBe(false)
  })
})

describe('sélection', () => {
  it('Maj-clic ajoute puis retire, sans vider le reste', () => {
    expect(toggleSelection([1, 2], 3)).toEqual([1, 2, 3])
    expect(toggleSelection([1, 2, 3], 2)).toEqual([1, 3])
  })

  it('oublie ce qui n’existe plus', () => {
    // Une sélection qui survit à une suppression désigne des identifiants fantômes : le geste
    // suivant partirait en 404 sur la moitié du lot.
    expect(pruneSelection([1, 2, 3], [1, 3])).toEqual([1, 3])
  })

  it('rassemble les éléments d’une pièce, adossés et libres', () => {
    const piece = room(
      7,
      'Séjour',
      [face(11, 7, 'A', [element({ id: 10, face_id: 11, room_id: null })])],
      [element({ id: 20 })],
    )

    expect(roomElements(piece).map((item) => item.id)).toEqual([10, 20])
  })

  it('ne donne un centre qu’à ce qui est posé au sol', () => {
    expect(freeCenter(element())).toEqual({ x: 100, y: 100 })
    expect(freeCenter(element({ face_id: 11, pos_x_cm: null, pos_y_cm: null }))).toBeNull()
  })
})

describe('presse-papier', () => {
  const sejour = room(
    7,
    'Séjour',
    [
      face(11, 7, 'A', [element({ id: 10, face_id: 11, room_id: null, x_offset_cm: 50 })]),
      face(12, 7, 'B'),
    ],
    [element({ id: 20 })],
  )

  it('retient la lettre du mur de chaque élément adossé', () => {
    const presse = copyToClipboard(sejour, [10, 20])

    expect(presse.elements.map((item) => item.id)).toEqual([10, 20])
    expect(presse.labels.get(11)).toBe('A')
  })

  it('copie une photographie et non des références vivantes', () => {
    const presse = copyToClipboard(sejour, [20])
    presse.elements[0]!.pos_x_cm = 999

    expect(sejour.free_elements[0]!.pos_x_cm).toBe(100)
  })

  it('colle sur la même face quand on reste dans la pièce', () => {
    const presse = copyToClipboard(sejour, [10])
    const { elements, refuses } = preparePaste(presse, { room: sejour })

    expect(refuses).toEqual([])
    expect(elements[0]).toMatchObject({ face_id: 11, x_offset_cm: 50 })
  })

  it('reporte sur la face homonyme dans une autre pièce', () => {
    const chambre = room(8, 'Chambre', [face(21, 8, 'A'), face(22, 8, 'B')])
    const presse = copyToClipboard(sejour, [10])

    const { elements } = preparePaste(presse, { room: chambre })

    // « La même applique sur le mur A de la chambre » : c'est le geste réel.
    expect(elements[0]).toMatchObject({ face_id: 21, x_offset_cm: 50 })
  })

  it('refuse sans convertir quand la face homonyme n’existe pas', () => {
    const couloir = room(9, 'Couloir', [face(31, 9, 'C')])
    const presse = copyToClipboard(sejour, [10])

    const { elements, refuses } = preparePaste(presse, { room: couloir })

    expect(elements).toEqual([])
    expect(refuses).toHaveLength(1)
    expect(refuses[0]?.raison).toContain('mur A')
    expect(refuses[0]?.raison).toContain('Couloir')
  })

  it('colle un meuble libre partout, en le rattachant à la pièce cible', () => {
    const chambre = room(8, 'Chambre', [face(21, 8, 'A')])
    const presse = copyToClipboard(sejour, [20])

    const { elements } = preparePaste(presse, { room: chambre })

    // Aucun décalage ici : c'est `duplicateOperations` qui écarte la copie de l'original, parce
    // que lui seul sait le faire pour les deux ancrages. Le faire aux deux endroits décalerait
    // deux fois.
    expect(elements[0]).toMatchObject({ room_id: 8, pos_x_cm: 100, pos_y_cm: 100 })
    expect(elements[0]?.face_id).toBeNull()
  })
})
