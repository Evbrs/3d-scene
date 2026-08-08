/**
 * Choix des couleurs de mobilier, emplacement par emplacement (`docs/spec-complete.md` §4.1 et
 * §4.4).
 *
 * Le catalogue modélise une centaine d'emplacements couleur — « corps », « façade », « poignée »,
 * « robinet »… — et l'instance porte déjà un dictionnaire `colors`. Faute d'interface, ils
 * tombaient tous sur le gris de repli : le produit savait décrire un meuble bicolore et ne savait
 * pas le montrer.
 *
 * Les fonctions ici sont pures : elles lisent le scene graph et en produisent une version teintée
 * sans y toucher. C'est ce qui permet à la vue de rendre la couleur immédiatement, avant même que
 * l'écriture serveur soit confirmée, tout en gardant la réponse du serveur comme référence.
 */
import type { FurnitureNode, JoineryNode, SceneNode, SceneRoom } from '@/api/types'

/** Un emplacement couleur d'une instance, avec la couleur actuellement retenue. */
export interface ColorSlot {
  slot: string
  color: string | null
}

/** Un meuble posé, et les emplacements couleur qu'il expose. */
export interface ColorTarget {
  elementId: number
  roomId: number
  slug: string
  label: string
  slots: ColorSlot[]
}

/** Couleurs choisies, par élément puis par emplacement. */
export type ColorOverrides = Record<number, Record<string, string>>

function isPlacedObject(node: SceneNode): node is FurnitureNode | JoineryNode {
  return node.kind === 'furniture' || node.kind === 'joinery'
}

/** « meuble-sous-vasque » → « Meuble sous vasque ». Le backend ne publie pas de libellé ici. */
export function humanize(slug: string): string {
  const words = slug.replace(/[-_]+/g, ' ').trim()
  return words.length === 0 ? '' : words.charAt(0).toUpperCase() + words.slice(1)
}

/**
 * Les emplacements couleur de la scène, dans l'ordre où ils sont posés.
 *
 * Un emplacement n'apparaît qu'une fois par élément même si plusieurs primitives le partagent :
 * c'est bien une matière qu'on choisit, pas une pièce du meuble.
 */
export function colorTargets(rooms: readonly SceneRoom[]): ColorTarget[] {
  return rooms.flatMap((room) =>
    room.nodes.filter(isPlacedObject).map((node) => {
      const slots: ColorSlot[] = []
      node.primitives.forEach((primitive) => {
        if (slots.some((known) => known.slot === primitive.color_slot)) return
        slots.push({ slot: primitive.color_slot, color: primitive.color })
      })
      return {
        elementId: node.element_id,
        roomId: room.id,
        slug: node.furniture_type_slug,
        label: humanize(node.furniture_type_slug),
        slots,
      }
    }),
  )
}

/**
 * Le dictionnaire complet à renvoyer au serveur pour un élément.
 *
 * La route **remplace** `colors`, elle ne le fusionne pas : n'envoyer que l'emplacement modifié
 * effacerait tous les autres. C'est le même piège que sur le revêtement d'une face.
 */
export function mergedColors(
  target: ColorTarget,
  overrides: ColorOverrides,
): Record<string, string> {
  const merged: Record<string, string> = {}
  target.slots.forEach((entry) => {
    if (entry.color) merged[entry.slot] = entry.color
  })
  Object.entries(overrides[target.elementId] ?? {}).forEach(([slot, color]) => {
    merged[slot] = color
  })
  return merged
}

/** La couleur affichée pour un emplacement : le choix en cours l'emporte sur celui du serveur. */
export function effectiveColor(
  target: ColorTarget,
  slot: string,
  overrides: ColorOverrides,
): string | null {
  return overrides[target.elementId]?.[slot] ?? target.slots.find((entry) => entry.slot === slot)?.color ?? null
}

/**
 * Le scene graph teinté des choix en cours.
 *
 * Rend le tableau reçu **tel quel** quand rien n'est choisi : la scène 3D se reconstruit sur
 * changement d'identité de `rooms`, et fabriquer une copie à chaque rendu la reconstruirait pour
 * rien.
 */
export function applyColorOverrides(
  rooms: readonly SceneRoom[],
  overrides: ColorOverrides,
): readonly SceneRoom[] {
  if (Object.keys(overrides).length === 0) return rooms
  return rooms.map((room) => ({
    ...room,
    nodes: room.nodes.map((node) => {
      if (!isPlacedObject(node)) return node
      const chosen = overrides[node.element_id]
      if (!chosen) return node
      return {
        ...node,
        primitives: node.primitives.map((primitive) =>
          primitive.color_slot in chosen
            ? { ...primitive, color: chosen[primitive.color_slot] ?? primitive.color }
            : primitive,
        ),
      }
    }),
  }))
}
