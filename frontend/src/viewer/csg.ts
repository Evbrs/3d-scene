/**
 * Opérations booléennes sur le mobilier (`docs/spec-complete.md` §3.2 et §4.2).
 *
 * Le backend marque `operation: "subtract"` sur les primitives creusées — vasque, baignoire, bac
 * de douche — et publie `requires_csg` pour désigner les meubles concernés. Le rendu jetait ces
 * primitives : la baignoire s'affichait pleine, la vasque bombée, le bac de douche massif. La
 * salle de bain, la pièce de rénovation la plus courante, n'avait donc aucun mobilier crédible.
 *
 * `three-bvh-csg` est expérimentale et exige des maillages **étanches** (two-manifold), ce que la
 * spec §3.2 rappelle explicitement. D'où deux garde-fous :
 *
 * 1. on n'évalue que sur les meubles marqués `requires_csg` — c'est son unique raison d'être ;
 * 2. `isWatertight` contrôle les deux opérandes avant l'évaluation. Un maillage ouvert donnerait
 *    un résultat troué, c'est-à-dire pire que l'approximation qu'on remplace : dans ce cas on
 *    garde la primitive pleine plutôt que de rendre un volume faux.
 */
import type { BufferAttribute, BufferGeometry, InterleavedBufferAttribute } from 'three'

import { type PrimitiveAxis, primitiveGeometry } from '@/viewer/geometry'

/** Ce que le CSG a besoin de connaître d'une primitive : sa forme, sa boîte, sa matière, son rôle. */
export interface CsgPrimitive {
  type: string
  axis: PrimitiveAxis
  offset: readonly number[]
  size: readonly number[]
  color_slot: string
  operation: string
}

/** Une primitive prête à être posée, que le CSG l'ait creusée ou non. */
export interface AssembledGeometry {
  geometry: BufferGeometry
  /**
   * Décalage restant à appliquer. Nul pour une géométrie creusée : l'évaluation booléenne se fait
   * dans le repère du meuble, la position y est donc déjà figée.
   */
  offset: readonly number[]
  /** Géométrie propre à ce meuble : ni mutualisable, ni instanciable, et jamais gérée par le pool. */
  carved: boolean
}

/**
 * Précision de soudure des sommets pour le contrôle d'étanchéité.
 *
 * Les géométries de Three.js dupliquent leurs sommets aux coutures (normales et UV différentes) :
 * compter les arêtes par indice conclurait toujours à un maillage ouvert. On les compte donc par
 * position arrondie. Au quatre-millième de centimètre, les résidus de rotation (de l'ordre de
 * 1e-17) disparaissent sans jamais confondre deux sommets distincts d'un meuble.
 */
const WELD_SCALE = 1e4

function positionKey(
  attribute: BufferAttribute | InterleavedBufferAttribute,
  index: number,
): string {
  const round = (value: number): number => Math.round(value * WELD_SCALE) / WELD_SCALE
  return `${round(attribute.getX(index))},${round(attribute.getY(index))},${round(attribute.getZ(index))}`
}

/**
 * Vrai si le maillage est étanche : chaque arête bordée par exactement deux triangles.
 *
 * C'est la condition posée par `three-bvh-csg`. Les boîtes et les sphères de Three.js la
 * satisfont, ainsi que les cylindres fermés ; un cylindre `openEnded` ne la satisfait pas, ses
 * arêtes de bord n'appartenant qu'à un seul triangle.
 */
export function isWatertight(geometry: BufferGeometry): boolean {
  const position = geometry.getAttribute('position')
  if (!position) return false

  const index = geometry.getIndex()
  const count = index ? index.count : position.count
  if (count === 0 || count % 3 !== 0) return false

  const vertexAt = (corner: number): number => (index ? index.getX(corner) : corner)
  const keys = new Map<number, string>()
  const keyOf = (vertex: number): string => {
    const known = keys.get(vertex)
    if (known !== undefined) return known
    const computed = positionKey(position, vertex)
    keys.set(vertex, computed)
    return computed
  }

  const edges = new Map<string, number>()
  for (let corner = 0; corner < count; corner += 3) {
    const triangle = [keyOf(vertexAt(corner)), keyOf(vertexAt(corner + 1)), keyOf(vertexAt(corner + 2))]
    for (let side = 0; side < 3; side += 1) {
      const from = triangle[side]!
      const to = triangle[(side + 1) % 3]!
      // Un triangle dégénéré (deux sommets confondus) n'a pas d'orientation exploitable : le
      // maillage ne peut pas servir d'opérande.
      if (from === to) return false
      const edge = from < to ? `${from}|${to}` : `${to}|${from}`
      edges.set(edge, (edges.get(edge) ?? 0) + 1)
    }
  }

  for (const shared of edges.values()) {
    if (shared !== 2) return false
  }
  return true
}

/** Boîtes englobantes sécantes, dans le repère du meuble. */
export function overlaps(first: CsgPrimitive, second: CsgPrimitive): boolean {
  for (let axis = 0; axis < 3; axis += 1) {
    const firstHalf = (first.size[axis] ?? 0) / 2
    const secondHalf = (second.size[axis] ?? 0) / 2
    const gap = Math.abs((first.offset[axis] ?? 0) - (second.offset[axis] ?? 0))
    if (gap >= firstHalf + secondHalf) return false
  }
  return true
}

/** Les primitives réellement posées : les autres ne servent qu'à creuser. */
export function additive<T extends CsgPrimitive>(primitives: readonly T[]): T[] {
  return primitives.filter((primitive) => primitive.operation !== 'subtract')
}

/**
 * Les primitives qu'une soustraction donnée doit creuser.
 *
 * Le recoupement des boîtes ne suffit pas. Sur la baignoire du catalogue, le robinet est un
 * cylindre posé à cheval sur le rebord gauche : sa boîte recoupe celle de la cuve creusée, et une
 * soustraction aveugle le trancherait en deux dans la longueur. Or un robinet surplombe la cuve,
 * il n'en est pas absent.
 *
 * La règle retenue est celle que la recette exprime déjà : **une soustraction creuse la matière
 * dans laquelle elle est déclarée**. C'est vrai des trois recettes concernées (vasque et sa
 * sphère en « ceramique », baignoire et sa cuve en « email », bac de douche en « receveur »), et
 * ça préserve robinet et bonde, qui portent leur propre emplacement couleur.
 *
 * Repli si aucune matière ne correspond : on retombe sur le recoupement seul. Une soustraction
 * modélisée ne doit jamais être jetée en silence — c'est exactement le défaut qu'on corrige ici.
 */
export function targetsOf<T extends CsgPrimitive>(cutter: CsgPrimitive, adds: readonly T[]): T[] {
  const touched = adds.filter((add) => overlaps(add, cutter))
  const sameMaterial = touched.filter((add) => add.color_slot === cutter.color_slot)
  return sameMaterial.length > 0 ? sameMaterial : touched
}

function translated(primitive: CsgPrimitive): BufferGeometry {
  const geometry = primitiveGeometry(primitive.size, primitive.type, primitive.axis)
  geometry.translate(primitive.offset[0] ?? 0, primitive.offset[1] ?? 0, primitive.offset[2] ?? 0)
  return geometry
}

/**
 * `three-bvh-csg` et sa dépendance `three-mesh-bvh` pèsent une trentaine de kilo-octets
 * compressés. La page de partage — la vitrine, ouverte au téléphone par le client de l'artisan —
 * n'a aucune raison de les payer quand la pièce montrée n'a pas de meuble creusé. On les charge
 * donc à la demande, avant la construction de la scène et jamais pendant : une géométrie qui
 * changerait de forme en cours de route ferait un clignotement.
 */
type CsgModule = typeof import('three-bvh-csg')

let library: CsgModule | null = null
/**
 * Un évaluateur unique : il porte un pool de triangles réutilisé d'une évaluation à l'autre, en
 * recréer un par meuble annulerait ce bénéfice.
 */
let evaluator: InstanceType<CsgModule['Evaluator']> | null = null
let loading: Promise<void> | null = null

export function isCsgReady(): boolean {
  return evaluator !== null
}

export async function ensureCsgReady(): Promise<void> {
  if (evaluator) return
  loading ??= import('three-bvh-csg').then((module) => {
    const made = new module.Evaluator()
    // Les meubles sont rendus en couleur plate, sans texture : transporter les UV à travers
    // l'évaluation coûterait sans rien apporter. `useGroups` désactivé garantit un résultat à un
    // seul groupe de matériau, donc un seul appel de dessin.
    made.attributes = ['position', 'normal']
    made.useGroups = false
    library = module
    evaluator = made
  })
  await loading
}

/**
 * Creuse les primitives additives par les primitives soustraites qui les recoupent.
 *
 * Le résultat est aligné sur `additive(primitives)` : une entrée absente signifie « rien à
 * creuser ici », l'appelant garde alors sa géométrie de base.
 */
export function carve(primitives: readonly CsgPrimitive[]): (AssembledGeometry | undefined)[] {
  const adds = additive(primitives)
  const cutters = primitives.filter((primitive) => primitive.operation === 'subtract')
  // Sans la librairie, la primitive pleine reste la seule réponse possible. L'appelant est censé
  // avoir attendu `ensureCsgReady()` ; ce repli évite qu'un oubli fasse tomber le rendu entier.
  // Les deux liaisons locales figent l'affinage de type, que le passage en fermeture perdrait.
  const csg = library
  const evaluate = evaluator
  if (cutters.length === 0 || !csg || !evaluate) return adds.map(() => undefined)

  return adds.map((add) => {
    const involved = cutters.filter((cutter) => targetsOf(cutter, adds).includes(add))
    if (involved.length === 0) return undefined

    let geometry = translated(add)
    if (!isWatertight(geometry)) {
      // Repli volontaire : mieux vaut le volume plein d'avant que le résultat troué d'une
      // évaluation sur un maillage ouvert (spec §3.2).
      geometry.dispose()
      return undefined
    }

    let carved = false
    for (const cutter of involved) {
      const tool = translated(cutter)
      if (!isWatertight(tool)) {
        tool.dispose()
        continue
      }
      const target = new csg.Brush(geometry)
      const knife = new csg.Brush(tool)
      target.updateMatrixWorld()
      knife.updateMatrixWorld()
      const result = evaluate.evaluate(target, knife, csg.SUBTRACTION)
      // `evaluate` produit une nouvelle géométrie : les opérandes intermédiaires sont à nous.
      geometry.dispose()
      tool.dispose()
      geometry = result.geometry
      carved = true
    }

    if (!carved) {
      geometry.dispose()
      return undefined
    }
    return { geometry, offset: [0, 0, 0], carved: true }
  })
}

/**
 * Mémoïsation des géométries creusées.
 *
 * La clé est (slug, dimensions, variante) : les primitives publiées par le backend sont une
 * fonction pure de ces trois entrées, donc le résultat booléen aussi. Sans ce cache, poser douze
 * fois le même bac de douche paierait douze fois l'évaluation.
 *
 * Le cache **possède** ses géométries et survit aux reconstructions de scène : elles ne sont donc
 * jamais confiées au pool de ressources, qui les libérerait sous ses pieds.
 */
const CARVED_CACHE_MAX = 48
const carvedCache = new Map<string, (AssembledGeometry | undefined)[]>()

export function csgCacheKey(
  slug: string,
  size: readonly number[],
  variantParams: Record<string, unknown> = {},
): string {
  const dimensions = [size[0] ?? 0, size[1] ?? 0, size[2] ?? 0].join('x')
  const variant = Object.keys(variantParams)
    .sort()
    .map((name) => `${name}=${String(variantParams[name])}`)
    .join(',')
  return `${slug}|${dimensions}|${variant}`
}

function evict(): void {
  while (carvedCache.size > CARVED_CACHE_MAX) {
    const oldest = carvedCache.keys().next()
    if (oldest.done) return
    carvedCache.get(oldest.value)?.forEach((part) => part?.geometry.dispose())
    carvedCache.delete(oldest.value)
  }
}

export function carveCached(
  key: string,
  primitives: readonly CsgPrimitive[],
): (AssembledGeometry | undefined)[] {
  const known = carvedCache.get(key)
  if (known) return known
  const computed = carve(primitives)
  // Un repli faute de librairie ne se mémoïse pas : il serait servi ensuite à la place du vrai
  // résultat, et le meuble resterait plein pour toute la session.
  if (!isCsgReady()) return computed
  carvedCache.set(key, computed)
  evict()
  return computed
}

/** Libère les géométries creusées. À appeler quand plus aucune scène n'est montée. */
export function clearCarvedCache(): void {
  carvedCache.forEach((parts) => parts.forEach((part) => part?.geometry.dispose()))
  carvedCache.clear()
}

/**
 * Compteur de scènes montées.
 *
 * Le cache survit délibérément aux reconstructions, mais pas à la fermeture du viewer. Vider sur
 * le premier démontage venu libérerait des géométries encore affichées par une seconde scène —
 * pendant une transition de route, les deux coexistent le temps d'une trame.
 */
let mountedScenes = 0

export function retainCarvedCache(): void {
  mountedScenes += 1
}

export function releaseCarvedCache(): void {
  mountedScenes = Math.max(0, mountedScenes - 1)
  if (mountedScenes === 0) clearCarvedCache()
}

/** Nombre d'entrées mémoïsées — pour les tests, qui vérifient que le cache sert. */
export function carvedCacheSize(): number {
  return carvedCache.size
}
