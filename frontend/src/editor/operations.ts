/**
 * Traduction des gestes de l'éditeur en opérations de lot (spec §10, amendement A6).
 *
 * Déplacer quinze meubles en quinze appels unitaires est strictement sériel : chaque écriture
 * incrémente la version du projet et invalide celle que le client détient. Un glisser-déposer
 * produit naturellement ce genre de rafales — d'où la route de lot, et d'où ce module, qui
 * transforme un geste en liste d'opérations sans jamais toucher au réseau.
 *
 * Tout est pur : le geste, son inverse, et le découpage sous la borne du serveur. C'est ce qui
 * rend l'annulation vérifiable — l'inverse d'un déplacement est un déplacement, et un test peut
 * le dire sans monter de composant.
 */
import { type BatchOperation, MAX_BATCH_OPERATIONS } from '@/api/client'
import type { PlanElement } from '@/api/types'
import type { WallGeometry } from '@/editor/drawing'
import type { Point } from '@/editor/geometry'
import { clampToRoom } from '@/editor/placement'

/** Le mur porteur de chaque face, indexé par identifiant de face. */
export type WallIndex = Map<number, WallGeometry>

export interface MoveContext {
  walls: WallIndex
  polygon: number[][]
}

/**
 * Placement d'un élément, sous la forme exacte attendue par `update_element`.
 *
 * Sert deux fois : à décrire où il va, et à décrire où il était. C'est ce second usage qui fait
 * l'annulation — l'inverse d'un déplacement n'est pas un état à restaurer, c'est un déplacement.
 */
export function placementOf(element: PlanElement): Record<string, number> {
  if (element.face_id === null) {
    return { pos_x_cm: element.pos_x_cm ?? 0, pos_y_cm: element.pos_y_cm ?? 0 }
  }
  return { x_offset_cm: element.x_offset_cm, y_offset_cm: element.y_offset_cm }
}

/** Opérations qui remettent chaque élément exactement là où il est aujourd'hui, avec sa taille. */
export function restoreOperations(elements: PlanElement[]): BatchOperation[] {
  return elements.map((element) => ({
    op: 'update_element' as const,
    element_id: element.id,
    changes: {
      ...placementOf(element),
      width_cm: element.width_cm,
      depth_cm: element.depth_cm,
      rotation_deg: element.rotation_deg,
    },
  }))
}

export function deleteOperations(elementIds: number[]): BatchOperation[] {
  return elementIds.map((element_id) => ({ op: 'delete_element' as const, element_id }))
}

/**
 * Recrée des éléments supprimés, dans leur repère d'origine.
 *
 * L'annulation d'une suppression rend des éléments **équivalents**, pas les mêmes : le serveur
 * attribue de nouveaux identifiants. C'est assumé — restaurer l'identifiant exigerait une
 * corbeille côté base, donc un amendement de la spec. Ce qui compte pour l'utilisateur, c'est que
 * son meuble revienne au même endroit avec les mêmes cotes.
 */
export function recreateOperations(elements: PlanElement[]): BatchOperation[] {
  return elements.map((element) => {
    const shape = {
      kind: element.kind,
      width_cm: element.width_cm,
      height_cm: element.height_cm,
      depth_cm: element.depth_cm,
      rotation_deg: element.rotation_deg,
      furniture_type_id: element.furniture_type_id,
      colors: element.colors,
      variant_params: element.variant_params,
    }
    if (element.face_id !== null) {
      return {
        op: 'create_face_element' as const,
        face_id: element.face_id,
        element: {
          ...shape,
          x_offset_cm: element.x_offset_cm,
          y_offset_cm: element.y_offset_cm,
        },
      }
    }
    return {
      op: 'create_room_element' as const,
      room_id: element.room_id as number,
      element: { ...shape, pos_x_cm: element.pos_x_cm ?? 0, pos_y_cm: element.pos_y_cm ?? 0 },
    }
  })
}

/**
 * Déplace un ensemble d'éléments d'un même vecteur, exprimé dans le repère du plan.
 *
 * Les deux ancrages ne bougent pas de la même façon, et c'est irréductible : un meuble libre suit
 * le vecteur ; un meuble adossé ne peut que **glisser le long de son mur**, donc seule la
 * composante parallèle au mur compte. Décomposer autrement le décollerait du mur, ce que le
 * modèle interdit — et ce que l'utilisateur ne demande pas : il glisse un plan de travail le long
 * du mur, il ne le décroche pas.
 *
 * Un élément qu'on ne peut pas déplacer (mur inconnu, sortie de pièce) est simplement omis :
 * refuser tout le geste parce qu'un meuble sur quinze bute contre un mur serait pire.
 */
export function moveOperations(
  elements: PlanElement[],
  delta: Point,
  context: MoveContext,
): BatchOperation[] {
  const operations: BatchOperation[] = []

  for (const element of elements) {
    if (element.face_id === null) {
      const center = { x: (element.pos_x_cm ?? 0) + delta.x, y: (element.pos_y_cm ?? 0) + delta.y }
      const placed = clampToRoom(
        center,
        context.polygon,
        element.width_cm,
        element.depth_cm,
        element.rotation_deg,
      )
      if (!placed) continue
      if (placed.x === element.pos_x_cm && placed.y === element.pos_y_cm) continue
      operations.push({
        op: 'update_element',
        element_id: element.id,
        changes: { pos_x_cm: placed.x, pos_y_cm: placed.y },
      })
      continue
    }

    const wall = context.walls.get(element.face_id)
    if (!wall) continue
    const along = delta.x * wall.direction.x + delta.y * wall.direction.y
    const maximum = Math.max(wall.lengthCm - element.width_cm, 0)
    const offset = Math.round(Math.min(Math.max(element.x_offset_cm + along, 0), maximum))
    if (offset === Math.round(element.x_offset_cm)) continue
    operations.push({
      op: 'update_element',
      element_id: element.id,
      changes: { x_offset_cm: offset },
    })
  }

  return operations
}

/** Tourne des meubles libres. Un élément adossé n'a pas d'orientation propre : il suit son mur. */
export function rotateOperations(elements: PlanElement[], stepDeg: number): BatchOperation[] {
  return elements
    .filter((element) => element.face_id === null)
    .map((element) => ({
      op: 'update_element' as const,
      element_id: element.id,
      // Ramené dans [0, 360[ : le serveur borne `rotation_deg` à ±360, et une rotation qui
      // s'accumule finit par sortir de la borne au bout de vingt-cinq quarts de tour.
      changes: { rotation_deg: normalizeAngle(element.rotation_deg + stepDeg) },
    }))
}

export function normalizeAngle(deg: number): number {
  const wrapped = deg % 360
  return wrapped < 0 ? wrapped + 360 : wrapped
}

/**
 * Duplique des éléments avec un décalage.
 *
 * Le décalage n'est pas décoratif : un doublon posé exactement sur l'original est indiscernable,
 * et l'utilisateur croit que rien ne s'est passé jusqu'à ce qu'il en déplace un.
 */
export function duplicateOperations(
  elements: PlanElement[],
  delta: Point,
  context: MoveContext,
): BatchOperation[] {
  const moved = elements
    .map((element) => shiftElement(element, delta, context))
    .filter((element): element is PlanElement => element !== null)
  return recreateOperations(moved)
}

/** Copie décalée d'un élément, dans son propre repère. `null` si le décalage le sort de la pièce. */
export function shiftElement(
  element: PlanElement,
  delta: Point,
  context: MoveContext,
): PlanElement | null {
  if (element.face_id === null) {
    const placed = clampToRoom(
      { x: (element.pos_x_cm ?? 0) + delta.x, y: (element.pos_y_cm ?? 0) + delta.y },
      context.polygon,
      element.width_cm,
      element.depth_cm,
      element.rotation_deg,
    )
    if (!placed) return null
    return { ...element, pos_x_cm: placed.x, pos_y_cm: placed.y }
  }

  const wall = context.walls.get(element.face_id)
  if (!wall) return null
  const along = delta.x * wall.direction.x + delta.y * wall.direction.y
  const maximum = Math.max(wall.lengthCm - element.width_cm, 0)
  return {
    ...element,
    x_offset_cm: Math.round(Math.min(Math.max(element.x_offset_cm + along, 0), maximum)),
  }
}

/**
 * Découpe un lot sous la borne du serveur.
 *
 * Sélectionner cent cinquante meubles et les déplacer d'un coup est banal ; au-delà de cent
 * opérations le serveur refuse le lot **entier**. Les paquets sont envoyés l'un après l'autre :
 * ils ne forment plus une seule transaction, et c'est le prix de la borne — mieux vaut deux
 * transactions appliquées que zéro.
 */
export function chunkOperations(
  operations: BatchOperation[],
  size: number = MAX_BATCH_OPERATIONS,
): BatchOperation[][] {
  if (size < 1) throw new Error('la taille d’un paquet doit être au moins 1')
  const chunks: BatchOperation[][] = []
  for (let index = 0; index < operations.length; index += size) {
    chunks.push(operations.slice(index, index + size))
  }
  return chunks
}

/** Libellé d'un geste, tel qu'il apparaît dans « Annuler : … ». Le pluriel compte, on le lit. */
export function describeCount(count: number, singulier: string, pluriel: string): string {
  return count === 1 ? `1 ${singulier}` : `${count} ${pluriel}`
}
