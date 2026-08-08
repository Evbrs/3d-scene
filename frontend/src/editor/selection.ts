/**
 * Sélection multiple et presse-papier de l'éditeur.
 *
 * Un relevé de chantier, c'est une cuisine où l'on pose douze éléments bas identiques puis on
 * décale la rangée entière. Sans sélection multiple, ce geste est douze fois le même clic ; avec
 * elle et la route de lot, c'est un seul aller-retour.
 *
 * La sélection est une liste d'identifiants et non d'objets : les éléments sont remplacés par les
 * réponses du serveur à chaque écriture, et une sélection tenant des références les garderait
 * périmés — on déplacerait ce qui n'existe plus.
 */
import type { PlanElement, Room } from '@/api/types'
import type { Point } from '@/editor/geometry'
import { freeFootprint } from '@/editor/placement'

export interface Rect {
  minX: number
  minY: number
  maxX: number
  maxY: number
}

/** Rectangle normalisé à partir de deux coins quelconques : on encadre dans les quatre sens. */
export function normalizeRect(a: Point, b: Point): Rect {
  return {
    minX: Math.min(a.x, b.x),
    minY: Math.min(a.y, b.y),
    maxX: Math.max(a.x, b.x),
    maxY: Math.max(a.y, b.y),
  }
}

export function rectContains(rect: Rect, point: Point): boolean {
  return point.x >= rect.minX && point.x <= rect.maxX && point.y >= rect.minY && point.y <= rect.maxY
}

/** Vrai si la surface encadrée est trop petite pour être un encadrement : c'était un clic. */
export function isNegligibleRect(rect: Rect, minimumCm: number): boolean {
  return rect.maxX - rect.minX < minimumCm && rect.maxY - rect.minY < minimumCm
}

export interface Selectable {
  id: number
  /** Centre de l'emprise au sol, dans le repère du plan. */
  center: Point
}

/**
 * Éléments encadrés.
 *
 * Le critère est le **centre** dans le rectangle, et non l'emprise entièrement dedans : encadrer
 * une rangée de meubles bas oblige sinon à englober aussi le mur, ce qu'on ne peut pas faire sans
 * attraper la pièce d'à côté. C'est la convention de tous les éditeurs de plan.
 */
export function elementsInRect(items: Selectable[], rect: Rect): number[] {
  return items.filter((item) => rectContains(rect, item.center)).map((item) => item.id)
}

/** Maj-clic : ajoute ou retire, sans jamais vider le reste. */
export function toggleSelection(selection: readonly number[], id: number): number[] {
  return selection.includes(id) ? selection.filter((candidate) => candidate !== id) : [...selection, id]
}

/** Retire de la sélection ce qui n'existe plus (suppression, changement de pièce). */
export function pruneSelection(selection: readonly number[], alive: Iterable<number>): number[] {
  const known = new Set(alive)
  return selection.filter((id) => known.has(id))
}

/** Tous les éléments d'une pièce, adossés et libres, dans l'ordre de lecture du plan. */
export function roomElements(room: Room): PlanElement[] {
  return [...room.faces.flatMap((face) => face.elements), ...room.free_elements]
}

/**
 * Centre de l'emprise au sol d'un élément, dans le repère du plan.
 *
 * Un meuble libre le porte déjà. Un élément adossé n'a que son décalage le long du mur : son
 * centre se reconstruit à partir de la géométrie du mur, ce que fait `drawing.furnitureFootprint`.
 * Cette fonction ne traite donc que le cas libre et rend `null` pour l'autre, à charge de
 * l'appelant de fournir le centre calculé — dupliquer ici le calcul du mur ferait diverger deux
 * implémentations de la même chose.
 */
export function freeCenter(element: PlanElement): Point | null {
  return freeFootprint(element)?.center ?? null
}

export interface Clipboard {
  /** Pièce d'origine : sert à décider ce qui est collable ailleurs. */
  roomId: number
  elements: PlanElement[]
  /**
   * Lettre du mur de chaque élément adossé, retenue **au moment de la copie**.
   *
   * Au collage, la pièce d'origine peut avoir été redessinée : remonter à sa face par
   * identifiant ne donnerait plus rien, et le collage échouerait sans rien pouvoir expliquer.
   */
  labels: Map<number, string>
}

export interface PasteTarget {
  room: Room
}

export interface PasteOutcome {
  /** Éléments prêts à être recréés, déjà replacés dans le repère de la pièce cible. */
  elements: PlanElement[]
  /** Ce qui n'a pas pu être collé, avec la raison — jamais silencieusement perdu. */
  refuses: { element: PlanElement; raison: string }[]
}

/**
 * Prépare un collage dans une pièce.
 *
 * Un meuble libre se colle partout : ses coordonnées ont le même sens dans toutes les pièces.
 * Un élément **adossé** est ancré à une face précise ; collé dans une autre pièce, il est reporté
 * sur la face **portant la même lettre**, ce qui est le geste réel (« la même applique sur le mur
 * A de la chambre »). Sans face homonyme, il est refusé et dit pourquoi : le transformer en
 * meuble libre changerait son repère en silence, ce que la spec interdit (§10, A4).
 *
 * Aucun décalage n'est appliqué ici. Un doublon posé exactement sur l'original est certes
 * indiscernable, mais l'écarter suppose de connaître la géométrie du mur pour un élément adossé :
 * c'est le travail de `operations.duplicateOperations`, et le faire à deux endroits ferait
 * décaler deux fois.
 */
export function preparePaste(clipboard: Clipboard, target: PasteTarget): PasteOutcome {
  const facesByLabel = new Map(target.room.faces.map((face) => [face.label, face]))
  const facesById = new Map(target.room.faces.map((face) => [face.id, face]))
  const elements: PlanElement[] = []
  const refuses: PasteOutcome['refuses'] = []

  for (const element of clipboard.elements) {
    if (element.face_id === null) {
      elements.push({ ...element, room_id: target.room.id })
      continue
    }

    if (facesById.has(element.face_id)) {
      elements.push(element)
      continue
    }

    const label = clipboard.labels.get(element.face_id) ?? null
    const twin = label === null ? undefined : facesByLabel.get(label)
    if (!twin) {
      refuses.push({
        element,
        raison: `adossé au mur ${label ?? '?'}, qui n'existe pas dans ${target.room.name}`,
      })
      continue
    }
    elements.push({ ...element, face_id: twin.id })
  }

  return { elements, refuses }
}

/** Construit un presse-papier en retenant la lettre du mur de chaque élément adossé. */
export function copyToClipboard(room: Room, selection: readonly number[]): Clipboard {
  const chosen = new Set(selection)
  const labels = new Map<number, string>()
  const elements: PlanElement[] = []

  for (const face of room.faces) {
    for (const element of face.elements) {
      if (!chosen.has(element.id)) continue
      labels.set(face.id, face.label)
      elements.push({ ...element })
    }
  }
  for (const element of room.free_elements) {
    if (chosen.has(element.id)) elements.push({ ...element })
  }

  return { roomId: room.id, elements, labels }
}
