/**
 * Palette de mobilier : regroupement et recherche.
 *
 * Le catalogue est paramétrique et générique (spec §4.1, décision de cadrage : aucun catalogue de
 * marque). Il tient donc en quelques dizaines d'entrées — mais une liste plate de quarante lignes
 * reste inutilisable sur un chantier, une main sur la tablette. D'où le regroupement par pièce et
 * la recherche.
 *
 * La recherche est **insensible aux accents** : personne ne tape « évier » avec son accent sur un
 * clavier tactile, et une recherche qui ne rend rien est indiscernable d'un catalogue vide.
 */
import type { FurnitureType } from '@/api/types'

/** Traduction des catégories du backend (`FurnitureCategory`). Une inconnue reste telle quelle. */
const CATEGORY_LABELS: Record<string, string> = {
  general: 'Général',
  bathroom: 'Salle de bains',
  bedroom: 'Chambre',
  living_room: 'Séjour',
  kitchen: 'Cuisine',
}

/** Ordre d'affichage : les pièces d'eau d'abord, c'est là que se joue le second œuvre. */
const CATEGORY_ORDER = ['kitchen', 'bathroom', 'bedroom', 'living_room', 'general']

export function humanizeCategory(category: string): string {
  return CATEGORY_LABELS[category] ?? category
}

export interface PaletteGroup {
  category: string
  label: string
  items: FurnitureType[]
}

/** Retire accents et casse : `foldAccents('Évier')` et `foldAccents('evier')` se rejoignent. */
export function foldAccents(value: string): string {
  return value
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .trim()
}

/**
 * Filtre le catalogue.
 *
 * Le nom, le slug **et** la catégorie sont interrogés : taper « cuisine » doit rendre la cuisine
 * entière, et taper le slug est ce que fait quelqu'un qui connaît déjà le catalogue.
 */
export function searchFurniture(types: FurnitureType[], query: string): FurnitureType[] {
  const needle = foldAccents(query)
  if (needle === '') return types
  const terms = needle.split(/\s+/)
  return types.filter((type) => {
    const haystack = foldAccents(
      `${type.name} ${type.slug} ${type.category} ${humanizeCategory(type.category)}`,
    )
    return terms.every((term) => haystack.includes(term))
  })
}

/** Regroupe par catégorie, dans l'ordre métier ; les groupes vides ne sont pas rendus. */
export function groupByCategory(types: FurnitureType[]): PaletteGroup[] {
  const groups = new Map<string, FurnitureType[]>()
  for (const type of types) {
    const bucket = groups.get(type.category) ?? []
    bucket.push(type)
    groups.set(type.category, bucket)
  }

  const rank = (category: string): number => {
    const index = CATEGORY_ORDER.indexOf(category)
    // Une catégorie que le frontend ne connaît pas encore passe à la fin plutôt que d'être
    // masquée : le backend peut en ajouter une avant que cette liste ne soit mise à jour.
    return index === -1 ? CATEGORY_ORDER.length : index
  }

  return [...groups.entries()]
    .sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b, 'fr'))
    .map(([category, items]) => ({
      category,
      label: humanizeCategory(category),
      items: [...items].sort((a, b) => a.name.localeCompare(b.name, 'fr')),
    }))
}

/** Les cotes par défaut d'un meuble, telles qu'elles partent dans la requête de création. */
export function defaultDimensions(type: FurnitureType): {
  width_cm: number
  height_cm: number
  depth_cm: number
} {
  return {
    width_cm: type.default_width_cm,
    height_cm: type.default_height_cm,
    depth_cm: type.default_depth_cm,
  }
}

/**
 * Charge utile d'un glisser-déposer natif.
 *
 * Sérialisée en JSON dans le `DataTransfer` plutôt que retenue dans une variable de module : un
 * glisser peut traverser deux fenêtres, et un état global se désynchronise dès qu'on abandonne le
 * geste hors du canevas.
 */
export const DRAG_MIME = 'application/x-plan-mobilier'

export interface DragPayload {
  furnitureTypeId: number
  slug: string
  name: string
  width_cm: number
  height_cm: number
  depth_cm: number
}

export function dragPayloadOf(type: FurnitureType): DragPayload {
  return { furnitureTypeId: type.id, slug: type.slug, name: type.name, ...defaultDimensions(type) }
}

/** Relit une charge utile de glisser. Rend `null` sur tout ce qui n'en est pas une. */
export function parseDragPayload(raw: string | null | undefined): DragPayload | null {
  if (!raw) return null
  try {
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object') return null
    const candidate = parsed as Partial<DragPayload>
    if (
      typeof candidate.furnitureTypeId !== 'number' ||
      typeof candidate.width_cm !== 'number' ||
      typeof candidate.height_cm !== 'number' ||
      typeof candidate.depth_cm !== 'number'
    ) {
      return null
    }
    return {
      furnitureTypeId: candidate.furnitureTypeId,
      slug: typeof candidate.slug === 'string' ? candidate.slug : '',
      name: typeof candidate.name === 'string' ? candidate.name : 'Meuble',
      width_cm: candidate.width_cm,
      height_cm: candidate.height_cm,
      depth_cm: candidate.depth_cm,
    }
  } catch {
    // Un glisser venu d'ailleurs (fichier, texte, onglet du navigateur) atterrit ici : on ignore,
    // on ne pose pas un meuble par accident.
    return null
  }
}
