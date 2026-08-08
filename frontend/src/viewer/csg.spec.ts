import { BoxGeometry, type BufferGeometry, CylinderGeometry, SphereGeometry } from 'three'
import { beforeAll, beforeEach, describe, expect, it } from 'vitest'

import {
  type CsgPrimitive,
  additive,
  carve,
  carveCached,
  carvedCacheSize,
  clearCarvedCache,
  csgCacheKey,
  ensureCsgReady,
  isCsgReady,
  isWatertight,
  overlaps,
  releaseCarvedCache,
  retainCarvedCache,
  targetsOf,
} from '@/viewer/csg'
import { primitiveGeometry } from '@/viewer/geometry'

/** Volume signé du maillage fermé, par la formule du tétraèdre. */
function volumeOf(geometry: BufferGeometry): number {
  const position = geometry.getAttribute('position')!
  const index = geometry.getIndex()
  const count = index ? index.count : position.count
  const vertex = (corner: number): number => (index ? index.getX(corner) : corner)

  let total = 0
  for (let corner = 0; corner < count; corner += 3) {
    const a = vertex(corner)
    const b = vertex(corner + 1)
    const c = vertex(corner + 2)
    total +=
      (position.getX(a) * (position.getY(b) * position.getZ(c) - position.getZ(b) * position.getY(c)) -
        position.getY(a) * (position.getX(b) * position.getZ(c) - position.getZ(b) * position.getX(c)) +
        position.getZ(a) * (position.getX(b) * position.getY(c) - position.getY(b) * position.getX(c))) /
      6
  }
  return Math.abs(total)
}

const primitive = (over: Partial<CsgPrimitive> = {}): CsgPrimitive => ({
  type: 'box',
  axis: 'y',
  offset: [0, 0, 0],
  size: [100, 50, 60],
  color_slot: 'corps',
  operation: 'add',
  ...over,
})

/**
 * La baignoire du catalogue, développée aux dimensions par défaut du backend (170 x 55 x 75).
 *
 * Recette `backend/app/services/catalog.py` : une cuve pleine, la boîte creusée à 0,88 x 0,85 x
 * 0,86 posée à 0,62 de hauteur, et le robinet à cheval sur le rebord gauche.
 */
const BAIGNOIRE: CsgPrimitive[] = [
  primitive({ size: [170, 55, 75], color_slot: 'email' }),
  primitive({
    size: [149.6, 46.75, 64.5],
    offset: [0, 6.6, 0],
    color_slot: 'email',
    operation: 'subtract',
  }),
  primitive({
    type: 'cylinder',
    size: [10.2, 13.75, 4.5],
    offset: [-74.8, 30.25, 0],
    color_slot: 'robinet',
  }),
]

describe('étanchéité des maillages (spec §3.2)', () => {
  it('reconnaît étanches les recettes que le CSG doit traiter', () => {
    // Les trois recettes concernées — vasque, baignoire, bac de douche — ne mettent en jeu que
    // des boîtes et une sphère. C'est la condition posée par `three-bvh-csg`.
    expect(isWatertight(new BoxGeometry(100, 50, 60))).toBe(true)
    expect(isWatertight(new SphereGeometry(0.5, 24, 16))).toBe(true)
    expect(isWatertight(primitiveGeometry([45, 54, 45], 'sphere'))).toBe(true)
    expect(isWatertight(primitiveGeometry([170, 55, 75], 'box'))).toBe(true)
  })

  it('reconnaît étanche un cylindre fermé, et ouvert un cylindre sans fond', () => {
    // L'avertissement de la spec porte précisément là-dessus. Nos cylindres sont fermés — le
    // robinet de la vasque, la bonde du bac —, mais le contrôle doit savoir dire non.
    expect(isWatertight(primitiveGeometry([8, 35, 8], 'cylinder'))).toBe(true)
    expect(isWatertight(new CylinderGeometry(0.5, 0.5, 1, 24, 1, true))).toBe(false)
  })

  it('refuse un maillage vide', () => {
    const geometry = new BoxGeometry(1, 1, 1)
    geometry.deleteAttribute('position')
    expect(isWatertight(geometry)).toBe(false)
  })
})

describe('recoupement des primitives', () => {
  it('voit se recouper une boîte et la boîte creusée à l’intérieur', () => {
    expect(overlaps(BAIGNOIRE[0]!, BAIGNOIRE[1]!)).toBe(true)
  })

  it('ignore une primitive posée à côté', () => {
    const loin = primitive({ size: [10, 10, 10], offset: [500, 0, 0], operation: 'subtract' })
    expect(overlaps(BAIGNOIRE[0]!, loin)).toBe(false)
  })

  it('ne compte comme additives que les primitives réellement posées', () => {
    expect(additive(BAIGNOIRE)).toHaveLength(2)
  })

  it('creuse la matière déclarée, pas le robinet qui la surplombe', () => {
    // La boîte du robinet recoupe bien celle de la cuve creusée — il est posé à cheval sur le
    // rebord. Le trancher en deux dans la longueur serait le défaut symétrique de celui qu'on
    // corrige : une soustraction creuse la matière dans laquelle elle est déclarée.
    const adds = additive(BAIGNOIRE)
    expect(overlaps(adds[1]!, BAIGNOIRE[1]!)).toBe(true)
    expect(targetsOf(BAIGNOIRE[1]!, adds)).toEqual([adds[0]])
  })

  it('retombe sur le recoupement quand aucune matière ne correspond', () => {
    // Une soustraction modélisée ne doit jamais être jetée en silence : c'est le défaut d'origine.
    const orphelin = primitive({ size: [50, 25, 30], color_slot: 'inconnu', operation: 'subtract' })
    const adds = [primitive({ color_slot: 'corps' })]
    expect(targetsOf(orphelin, adds)).toEqual(adds)
  })
})

describe('chargement à la demande de la librairie booléenne', () => {
  it('ne creuse rien tant qu’elle n’est pas là, plutôt que de tomber', () => {
    // La page de partage ne charge `three-bvh-csg` que si la pièce montrée contient un meuble
    // creusé. Un appel prématuré doit rendre le volume plein, pas une exception.
    const parts = carve(BAIGNOIRE)
    if (!isCsgReady()) expect(parts).toEqual([undefined, undefined])
  })

  it('ne mémoïse pas ce repli', () => {
    // Mémoïsé, il serait servi ensuite à la place du vrai résultat et le meuble resterait plein
    // pour toute la session.
    clearCarvedCache()
    if (!isCsgReady()) {
      carveCached(csgCacheKey('baignoire', [170, 55, 75]), BAIGNOIRE)
      expect(carvedCacheSize()).toBe(0)
    }
  })
})

describe('creusement du mobilier (spec §4.2)', () => {
  beforeAll(() => ensureCsgReady())
  beforeEach(() => clearCarvedCache())

  it('creuse réellement la baignoire au lieu de jeter la soustraction', () => {
    const parts = carve(BAIGNOIRE)

    // Le résultat est aligné sur les primitives additives : la cuve et le robinet.
    expect(parts).toHaveLength(2)
    const cuve = parts[0]
    expect(cuve?.carved).toBe(true)

    const plein = 170 * 55 * 75
    const creux = 149.6 * 46.75 * 64.5
    // La soustraction dépasse par le haut : seule la part réellement contenue dans la cuve
    // disparaît. On borne donc le volume retiré par le volume de l'outil.
    const obtenu = volumeOf(cuve!.geometry)
    expect(obtenu).toBeLessThan(plein)
    expect(obtenu).toBeGreaterThan(plein - creux - 1)
  })

  it('laisse intactes les primitives qu’aucune soustraction ne concerne', () => {
    // Le robinet garde sa forme entière : il porte son propre emplacement couleur.
    expect(carve(BAIGNOIRE)[1]).toBeUndefined()
  })

  it('fige la position dans la géométrie creusée', () => {
    // L'évaluation booléenne se fait dans le repère du meuble : replacer la pièce ensuite la
    // décalerait une seconde fois.
    const decale: CsgPrimitive[] = [
      primitive({ size: [60, 18, 45], offset: [0, 9, 0] }),
      primitive({ type: 'sphere', size: [45, 16, 33], offset: [0, 15, 0], operation: 'subtract' }),
    ]

    const cuve = carve(decale)[0]!
    expect(cuve.offset).toEqual([0, 0, 0])
    cuve.geometry.computeBoundingBox()
    // La boîte englobante reste centrée sur y = 9, là où la primitive était posée.
    expect(cuve.geometry.boundingBox!.min.y).toBeCloseTo(0, 3)
  })

  it('ne touche pas un meuble sans soustraction', () => {
    const commode = [primitive(), primitive({ size: [90, 12, 2], offset: [0, 20, 22] })]
    expect(carve(commode)).toEqual([undefined, undefined])
  })
})

describe('mémoïsation par (slug, dimensions, variante)', () => {
  beforeAll(() => ensureCsgReady())
  beforeEach(() => clearCarvedCache())

  it('distingue deux dimensions et deux variantes du même meuble', () => {
    expect(csgCacheKey('baignoire', [170, 55, 75])).not.toBe(csgCacheKey('baignoire', [160, 55, 75]))
    expect(csgCacheKey('commode', [90, 80, 45], { tiroirs: 3 })).not.toBe(
      csgCacheKey('commode', [90, 80, 45], { tiroirs: 4 }),
    )
  })

  it('est insensible à l’ordre des clés de variante', () => {
    expect(csgCacheKey('lit', [1, 2, 3], { a: 1, b: 2 })).toBe(
      csgCacheKey('lit', [1, 2, 3], { b: 2, a: 1 }),
    )
  })

  it('rend la même géométrie sans réévaluer', () => {
    const key = csgCacheKey('baignoire', [170, 55, 75])
    const first = carveCached(key, BAIGNOIRE)
    const second = carveCached(key, BAIGNOIRE)

    expect(carvedCacheSize()).toBe(1)
    expect(second[0]!.geometry).toBe(first[0]!.geometry)
  })

  it('se vide sur demande', () => {
    carveCached(csgCacheKey('baignoire', [170, 55, 75]), BAIGNOIRE)
    clearCarvedCache()
    expect(carvedCacheSize()).toBe(0)
  })

  it('ne se vide qu’au démontage de la dernière scène', () => {
    // Pendant une transition de route, deux scènes coexistent le temps d'une trame : vider sur le
    // premier démontage libérerait des géométries encore affichées.
    retainCarvedCache()
    retainCarvedCache()
    carveCached(csgCacheKey('baignoire', [170, 55, 75]), BAIGNOIRE)

    releaseCarvedCache()
    expect(carvedCacheSize()).toBe(1)

    releaseCarvedCache()
    expect(carvedCacheSize()).toBe(0)
  })
})
