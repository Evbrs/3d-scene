/**
 * Palette de mobilier : recherche, regroupement et charge utile du glisser.
 *
 * Ce qui compte : une recherche qui ne rend rien est indiscernable d'un catalogue vide, et une
 * charge utile mal relue pose un meuble par accident au milieu d'un plan.
 */
import { describe, expect, it } from 'vitest'

import type { FurnitureType } from '@/api/types'
import {
  dragPayloadOf,
  foldAccents,
  groupByCategory,
  humanizeCategory,
  parseDragPayload,
  searchFurniture,
} from '@/editor/palette'

function type(id: number, name: string, category: string, slug = `s${id}`): FurnitureType {
  return {
    id,
    slug,
    name,
    category,
    color_slots: ['corps'],
    parts: [],
    default_width_cm: 120,
    default_height_cm: 85,
    default_depth_cm: 60,
  }
}

const CATALOGUE = [
  type(1, 'Évier deux bacs', 'kitchen', 'evier-double'),
  type(2, 'Lit double', 'bedroom'),
  type(3, 'Baignoire', 'bathroom'),
  type(4, 'Canapé', 'living_room'),
  type(5, 'Armoire', 'bedroom'),
]

describe('recherche', () => {
  it('ignore les accents et la casse', () => {
    // Personne ne tape « évier » avec son accent sur un clavier tactile.
    expect(searchFurniture(CATALOGUE, 'evier').map((item) => item.id)).toEqual([1])
    expect(searchFurniture(CATALOGUE, 'ÉVIER').map((item) => item.id)).toEqual([1])
    expect(foldAccents('Évier')).toBe('evier')
  })

  it('interroge aussi la catégorie, en français', () => {
    expect(searchFurniture(CATALOGUE, 'chambre').map((item) => item.id)).toEqual([2, 5])
  })

  it('interroge le slug, pour qui connaît déjà le catalogue', () => {
    expect(searchFurniture(CATALOGUE, 'evier-double').map((item) => item.id)).toEqual([1])
  })

  it('exige tous les termes', () => {
    expect(searchFurniture(CATALOGUE, 'lit chambre').map((item) => item.id)).toEqual([2])
    expect(searchFurniture(CATALOGUE, 'lit cuisine')).toEqual([])
  })

  it('rend tout le catalogue sur une recherche vide', () => {
    expect(searchFurniture(CATALOGUE, '   ')).toHaveLength(CATALOGUE.length)
  })
})

describe('regroupement', () => {
  it('range par catégorie, dans l’ordre métier', () => {
    const groupes = groupByCategory(CATALOGUE)

    expect(groupes.map((groupe) => groupe.category)).toEqual([
      'kitchen',
      'bathroom',
      'bedroom',
      'living_room',
    ])
  })

  it('trie les meubles par nom, à la française', () => {
    const chambre = groupByCategory(CATALOGUE).find((groupe) => groupe.category === 'bedroom')

    expect(chambre?.items.map((item) => item.name)).toEqual(['Armoire', 'Lit double'])
  })

  it('n’émet pas de groupe vide', () => {
    expect(groupByCategory([]).length).toBe(0)
    expect(groupByCategory([type(9, 'Table', 'living_room')])).toHaveLength(1)
  })

  it('range en fin de liste une catégorie que le frontend ne connaît pas encore', () => {
    // Le backend peut en ajouter une avant que la traduction ne soit écrite : la masquer serait
    // pire que l'afficher telle quelle.
    const groupes = groupByCategory([...CATALOGUE, type(9, 'Établi', 'garage')])

    expect(groupes.at(-1)?.category).toBe('garage')
    expect(humanizeCategory('garage')).toBe('garage')
  })

  it('traduit les catégories connues', () => {
    expect(humanizeCategory('kitchen')).toBe('Cuisine')
    expect(humanizeCategory('living_room')).toBe('Séjour')
  })
})

describe('charge utile du glisser', () => {
  it('emporte les cotes par défaut, pour dessiner l’aperçu avant la dépose', () => {
    expect(dragPayloadOf(CATALOGUE[0]!)).toEqual({
      furnitureTypeId: 1,
      slug: 'evier-double',
      name: 'Évier deux bacs',
      width_cm: 120,
      height_cm: 85,
      depth_cm: 60,
    })
  })

  it('se relit à l’identique', () => {
    const charge = dragPayloadOf(CATALOGUE[1]!)

    expect(parseDragPayload(JSON.stringify(charge))).toEqual(charge)
  })

  it.each([
    [null],
    [''],
    ['ceci n’est pas du JSON'],
    ['null'],
    ['"une chaîne"'],
    ['{"furnitureTypeId":"trois"}'],
    ['{"furnitureTypeId":3}'],
  ])('ignore %s plutôt que de poser un meuble par accident', (raw) => {
    // Un glisser venu d'ailleurs — fichier, texte, onglet du navigateur — atterrit ici.
    expect(parseDragPayload(raw)).toBeNull()
  })

  it('rend un nom de repli plutôt que de refuser une charge utile complète', () => {
    const charge = parseDragPayload(
      '{"furnitureTypeId":3,"width_cm":10,"height_cm":10,"depth_cm":10}',
    )

    expect(charge).toMatchObject({ furnitureTypeId: 3, name: 'Meuble', slug: '' })
  })
})
