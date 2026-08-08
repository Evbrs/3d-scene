/**
 * Assemblage du scene graph en objets Three.js.
 *
 * Ce module ne calcule **aucune** géométrie métier : tout vient du backend (spec §3.1). Il
 * traduit, il place, et il regroupe.
 *
 * Il est écrit en impératif plutôt qu'en gabarit déclaratif pour trois raisons qui se tiennent :
 *
 * 1. **Les fuites.** Les géométries construites dans un `computed` vivaient hors de l'arbre
 *    TresJS, que son nettoyage ne couvrait donc pas. Ici, une scène possède son `ResourcePool` et
 *    le rend entièrement.
 * 2. **Le coût.** 142 appels de dessin pour une seule pièce venaient d'un maillage par primitive.
 *    Regrouper par (primitive, couleur) en `InstancedMesh` demande de connaître toutes les pièces
 *    à la fois, ce qu'un `v-for` ne permet pas.
 * 3. **La vérifiabilité.** Le rendu WebGL n'est pas testable ici ; l'arbre d'objets, si. Les tests
 *    de ce module vérifient qu'une soustraction est bien creusée, qu'une face est bien texturée
 *    et que le regroupement fait baisser le nombre d'appels de dessin.
 */
import {
  Box3,
  type BufferGeometry,
  DoubleSide,
  ExtrudeGeometry,
  FrontSide,
  Group,
  InstancedMesh,
  type Material,
  Matrix4,
  Mesh,
  MeshStandardMaterial,
  Object3D,
  Quaternion,
  ShapeGeometry,
  type Texture,
  Vector3,
} from 'three'

import type {
  Covering,
  FurnitureNode,
  HorizontalNode,
  JoineryNode,
  SceneNode,
  SceneRoom,
  WallNode,
} from '@/api/types'
import { type CsgPrimitive, additive, carveCached, csgCacheKey } from '@/viewer/csg'
import { buildShape, primitiveGeometry } from '@/viewer/geometry'
import { ResourcePool } from '@/viewer/resources'
import { buildCoveringTexture, coveringTextureKey } from '@/viewer/textures'
import {
  type FaceVisibility,
  type WallFacing,
  TRANSPARENT_OPACITY,
  faceKey,
  isVisible,
  materialFor,
  opacityFor,
} from '@/viewer/visibility'

export const WALL_FALLBACK = '#e4e4e4'
export const FLOOR_FALLBACK = '#b09371'
export const CEILING_FALLBACK = '#f2f2f2'
export const FURNITURE_FALLBACK = '#9aa0a6'

/**
 * À partir de combien de copies on passe en `InstancedMesh`.
 *
 * Deux suffisent : une instance de plus, c'est un appel de dessin de moins, et le surcoût d'un
 * `InstancedMesh` de deux éléments est négligeable devant celui d'un maillage supplémentaire.
 */
const INSTANCING_THRESHOLD = 2

export interface BuildOptions {
  pool: ResourcePool
  /** Préfixe les clés de face par la pièce : indispensable dès qu'on montre le logement entier. */
  roomScoped?: boolean
  /** Ombres portées. Coupées, la scène reste juste — seulement plus plate. */
  shadows?: boolean
}

function isWall(node: SceneNode): node is WallNode {
  return node.kind === 'wall'
}

function isHorizontal(node: SceneNode): node is HorizontalNode {
  return node.kind === 'floor' || node.kind === 'ceiling'
}

/**
 * Menuiseries et meubles se rendent à l'identique : une recette développée en primitives, posée
 * puis tournée. Seule leur provenance diffère.
 */
function isPlacedObject(node: SceneNode): node is FurnitureNode | JoineryNode {
  return node.kind === 'furniture' || node.kind === 'joinery'
}

// --- Matériaux et textures ------------------------------------------------------------------

interface Surface {
  color: string
  covering?: Covering | null
  roughness: number
  doubleSided: boolean
}

interface PooledTexture {
  key: string
  map: Texture | null
  dispose: () => void
}

function textureOf(pool: ResourcePool, surface: Surface): PooledTexture | null {
  const key = coveringTextureKey(surface.covering, surface.color)
  if (!key) return null
  // Une texture indisponible (pas de contexte 2D) reste une entrée du pool : sans elle, on
  // retenterait de la peindre pour chaque face, pour rien.
  return pool.acquire(`texture|${key}`, () => {
    const map = buildCoveringTexture(surface.covering, surface.color)
    return { key, map, dispose: () => map?.dispose() }
  })
}

/**
 * Matériau mutualisé.
 *
 * La clé porte l'opacité : les trois états de visibilité n'en produisent que deux valeurs, donc
 * au plus deux variantes par couleur. C'est ce qui permet à `applyVisibility` de basculer une
 * face en transparence sans reconstruire quoi que ce soit, et sans que le changement déborde sur
 * les faces voisines qui partageraient le matériau.
 */
function surfaceMaterial(pool: ResourcePool, surface: Surface, opacity: number): Material {
  const texture = textureOf(pool, surface)
  const key = [
    'material',
    surface.color,
    opacity,
    surface.roughness,
    surface.doubleSided ? 'double' : 'front',
    texture?.map ? texture.key : '',
  ].join('|')

  return pool.acquire(key, () => {
    const material = new MeshStandardMaterial({
      // La couleur est déjà peinte dans la texture : la remultiplier assombrirait le revêtement.
      color: texture?.map ? '#ffffff' : surface.color,
      roughness: surface.roughness,
      metalness: 0,
      transparent: opacity < 1,
      opacity,
      side: surface.doubleSided ? DoubleSide : FrontSide,
    })
    if (texture?.map) material.map = texture.map
    return material
  })
}

/** Les deux variantes d'un matériau, prêtes à être échangées par `applyVisibility`. */
export interface MaterialVariants {
  visible: Material
  transparent: Material
}

function variantsFor(pool: ResourcePool, surface: Surface): MaterialVariants {
  return {
    visible: surfaceMaterial(pool, surface, 1),
    transparent: surfaceMaterial(pool, surface, TRANSPARENT_OPACITY),
  }
}

// --- Placement -------------------------------------------------------------------------------

const SCRATCH_POSITION = new Vector3()
const SCRATCH_ROTATION = new Quaternion()
const SCRATCH_SCALE = new Vector3(1, 1, 1)
const SCRATCH_AXIS = new Vector3(0, 1, 0)

/**
 * Matrice d'une primitive dans le monde : le meuble est posé puis tourné, la primitive est
 * décalée dans le repère du meuble.
 */
export function placementMatrix(
  position: readonly number[],
  rotationY: number,
  offset: readonly number[],
): Matrix4 {
  SCRATCH_POSITION.set(position[0] ?? 0, position[1] ?? 0, position[2] ?? 0)
  SCRATCH_ROTATION.setFromAxisAngle(SCRATCH_AXIS, rotationY)
  const node = new Matrix4().compose(SCRATCH_POSITION, SCRATCH_ROTATION, SCRATCH_SCALE)
  return node.multiply(
    new Matrix4().makeTranslation(offset[0] ?? 0, offset[1] ?? 0, offset[2] ?? 0),
  )
}

interface Placement {
  batchKey: string
  geometry: BufferGeometry
  color: string
  matrix: Matrix4
  elementId: number
}

function primitiveKey(primitive: CsgPrimitive): string {
  const size = [primitive.size[0] ?? 0, primitive.size[1] ?? 0, primitive.size[2] ?? 0].join('x')
  return `${primitive.type}|${primitive.axis}|${size}`
}

/** Les primitives d'un meuble, creusées si le backend l'a demandé, prêtes à être posées. */
function placementsOf(node: FurnitureNode | JoineryNode, pool: ResourcePool): Placement[] {
  const primitives = node.primitives
  const adds = additive(primitives)
  const recipe = csgCacheKey(
    node.furniture_type_slug,
    node.size_cm,
    'variant_params' in node ? node.variant_params : {},
  )
  // Le CSG n'est mobilisé que sur `requires_csg` : c'est son unique raison d'être (spec §3.2), et
  // la librairie est expérimentale. Ailleurs, la primitive brute est la bonne réponse.
  const carved = node.requires_csg ? carveCached(recipe, primitives) : []

  return adds.map((primitive, index) => {
    const hollow = carved[index]
    const geometry =
      hollow?.geometry ??
      pool.acquire(`geometry|${primitiveKey(primitive)}`, () =>
        primitiveGeometry(primitive.size, primitive.type, primitive.axis),
      )
    const color = materialFor(primitive.color, FURNITURE_FALLBACK)
    return {
      // Une géométrie creusée est mémoïsée sur la recette, pas sur l'instance : deux bacs de
      // douche identiques partagent la leur, et méritent donc le même lot d'instances qu'une
      // primitive ordinaire. C'est la recette qui les distingue, pas l'élément.
      batchKey: hollow
        ? `carved|${recipe}|${index}|${color}`
        : `${primitiveKey(primitive)}|${color}`,
      geometry,
      color,
      matrix: placementMatrix(node.position, node.rotation_y, hollow?.offset ?? primitive.offset),
      elementId: node.element_id,
    }
  })
}

/**
 * Regroupe les primitives identiques d'une même face.
 *
 * Le regroupement s'arrête à la face, pas à la pièce : l'isolement et la transparence se pilotent
 * par groupe de face (spec §3.4), et un `InstancedMesh` à cheval sur deux faces ne saurait plus
 * en masquer une seule.
 */
export function batch(placements: readonly Placement[]): Map<string, Placement[]> {
  const batches = new Map<string, Placement[]>()
  placements.forEach((placement) => {
    const known = batches.get(placement.batchKey)
    if (known) known.push(placement)
    else batches.set(placement.batchKey, [placement])
  })
  return batches
}

// --- Construction ------------------------------------------------------------------------------

function faceGroup(key: string, faceLabel: string, roomId: number): Group {
  const group = new Group()
  group.name = `face-${key}`
  group.userData = { faceKey: key, faceLabel, roomId }
  return group
}

function addWall(parent: Group, node: WallNode, options: BuildOptions): void {
  const geometry = options.pool.own(
    new ExtrudeGeometry(buildShape(node.outline, node.holes), {
      depth: node.extrude_depth_cm,
      bevelEnabled: false,
    }),
  )
  // `extrude_offset_cm` recule l'extrusion d'une demi-épaisseur : sans lui le mur est posé d'un
  // seul côté de son axe, et les angles de la pièce ne se rejoignent pas.
  geometry.translate(0, 0, node.extrude_offset_cm)

  const color = materialFor(node.covering.color, WALL_FALLBACK)
  const variants = variantsFor(options.pool, {
    color,
    covering: node.covering,
    roughness: 0.92,
    doubleSided: true,
  })

  const mesh = new Mesh(geometry, variants.visible)
  mesh.position.set(node.origin[0], node.origin[1], node.origin[2])
  mesh.rotation.y = node.rotation_y
  mesh.castShadow = options.shadows ?? false
  mesh.receiveShadow = options.shadows ?? false
  mesh.userData = { variants }
  parent.add(mesh)
}

function addHorizontal(parent: Group, node: HorizontalNode, options: BuildOptions): void {
  const geometry = options.pool.own(new ShapeGeometry(buildShape(node.outline, node.holes)))
  const fallback = node.kind === 'floor' ? FLOOR_FALLBACK : CEILING_FALLBACK
  const color = materialFor(node.covering.color, fallback)
  const variants = variantsFor(options.pool, {
    color,
    covering: node.covering,
    roughness: node.kind === 'floor' ? 0.72 : 0.95,
    doubleSided: true,
  })

  const mesh = new Mesh(geometry, variants.visible)
  mesh.position.set(node.origin[0], node.origin[1], node.origin[2])
  mesh.rotation.x = node.rotation_x
  // Un sol qui projette une ombre sur lui-même produit un moirage : il la reçoit, il ne la jette
  // pas. Le plafond, lui, est bien ce qui fait de l'ombre dans une pièce.
  mesh.castShadow = (options.shadows ?? false) && node.kind === 'ceiling'
  mesh.receiveShadow = options.shadows ?? false
  mesh.userData = { variants }
  parent.add(mesh)
}

function addFurniture(
  parent: Group,
  nodes: readonly (FurnitureNode | JoineryNode)[],
  options: BuildOptions,
): void {
  const placements = nodes.flatMap((node) => placementsOf(node, options.pool))

  batch(placements).forEach((group) => {
    const first = group[0]!
    const variants = variantsFor(options.pool, {
      color: first.color,
      roughness: 0.78,
      doubleSided: false,
    })

    const object =
      group.length >= INSTANCING_THRESHOLD
        ? (() => {
            const instanced = new InstancedMesh(first.geometry, variants.visible, group.length)
            group.forEach((placement, index) => instanced.setMatrixAt(index, placement.matrix))
            instanced.instanceMatrix.needsUpdate = true
            instanced.computeBoundingSphere()
            return instanced as Mesh
          })()
        : (() => {
            const mesh = new Mesh(first.geometry, variants.visible)
            mesh.applyMatrix4(first.matrix)
            return mesh
          })()

    object.castShadow = options.shadows ?? false
    object.receiveShadow = options.shadows ?? false
    object.userData = { variants, elementIds: group.map((placement) => placement.elementId) }
    parent.add(object)
  })
}

/** Une pièce entière : ses faces, chacune portant son mobilier. */
export function buildRoom(room: SceneRoom, options: BuildOptions): Group {
  const roomGroup = new Group()
  roomGroup.name = `piece-${room.id}`
  roomGroup.userData = { roomId: room.id, roomName: room.name }

  const scope = options.roomScoped ? room.id : undefined
  const groups = new Map<string, Group>()
  const groupFor = (faceLabel: string): Group => {
    const key = faceKey(faceLabel, scope)
    let group = groups.get(key)
    if (!group) {
      group = faceGroup(key, faceLabel, room.id)
      groups.set(key, group)
      roomGroup.add(group)
    }
    return group
  }

  room.nodes.filter(isWall).forEach((node) => addWall(groupFor(node.face_label), node, options))
  room.nodes.filter(isHorizontal).forEach((node) => {
    addHorizontal(groupFor(node.face_label), node, options)
  })

  // Le mobilier suit la visibilité de la face qui le porte : on le regroupe donc par face avant
  // de l'instancier.
  const byFace = new Map<string, (FurnitureNode | JoineryNode)[]>()
  const unanchored: (FurnitureNode | JoineryNode)[] = []
  room.nodes.filter(isPlacedObject).forEach((node) => {
    // Un meuble libre n'a pas de face : il est ancré à la pièce. Il ne peut donc pas suivre un
    // isolement de face, et son groupe ne porte volontairement aucune clé — `applyVisibility` ne
    // le touche jamais, il reste visible quel que soit le mur qu'on met en avant.
    if (!node.face_label) {
      unanchored.push(node)
      return
    }
    const known = byFace.get(node.face_label)
    if (known) known.push(node)
    else byFace.set(node.face_label, [node])
  })
  byFace.forEach((nodes, faceLabel) => addFurniture(groupFor(faceLabel), nodes, options))

  if (unanchored.length > 0) {
    const free = new Group()
    free.name = `mobilier-${room.id}`
    roomGroup.add(free)
    addFurniture(free, unanchored, options)
  }

  return roomGroup
}

/**
 * La scène contient-elle un meuble que le backend marque comme creusé ?
 *
 * C'est la seule condition qui justifie de charger `three-bvh-csg`. La plupart des pièces n'en
 * ont aucun : leur faire payer la librairie serait gratuit au sens propre.
 */
export function needsCsg(rooms: readonly SceneRoom[]): boolean {
  return rooms.some((room) =>
    room.nodes.some((node) => isPlacedObject(node) && node.requires_csg),
  )
}

/**
 * Le logement complet, ou une seule pièce.
 *
 * Aucun changement backend n'est nécessaire : le scene graph positionne déjà chaque pièce en
 * coordonnées monde absolues (`to_world` sur les coordonnées du plan). Il suffit de boucler.
 */
export function buildScene(rooms: readonly SceneRoom[], options: BuildOptions): Group {
  const root = new Group()
  root.name = 'scene'
  rooms.forEach((room) => root.add(buildRoom(room, options)))
  return root
}

// --- Affichage ---------------------------------------------------------------------------------

/**
 * Applique les trois positions de visibilité sans rien reconstruire.
 *
 * Un groupe masqué n'est pas parcouru par le rendu ; un groupe transparent voit ses maillages
 * échanger leur matériau contre la variante à faible opacité, prise dans le même pool.
 */
export function applyVisibility(
  root: Object3D,
  visibility: Record<string, FaceVisibility>,
): void {
  root.traverse((object) => {
    const key = object.userData.faceKey as string | undefined
    if (key !== undefined) {
      const state = visibility[key] ?? 'visible'
      object.visible = isVisible(state)
      const opacity = opacityFor(state)
      object.traverse((child) => {
        const variants = child.userData.variants as MaterialVariants | undefined
        if (variants && child instanceof Mesh) {
          child.material = opacity < 1 ? variants.transparent : variants.visible
        }
      })
    }
  })
}

/** Les murs d'une scène construite, sous la forme que `effectiveVisibility` attend. */
export function wallFacings(rooms: readonly SceneRoom[], roomScoped: boolean): WallFacing[] {
  return rooms.flatMap((room) =>
    room.nodes.filter(isWall).map((node) => ({
      key: faceKey(node.face_label, roomScoped ? room.id : undefined),
      origin: node.origin,
      rotationY: node.rotation_y,
      lengthCm: node.length_cm,
      outwardNormal: node.outward_normal,
    })),
  )
}

// --- Caméra d'ensemble ---------------------------------------------------------------------------

export interface Framing {
  position: [number, number, number]
  target: [number, number, number]
}

/**
 * Cadre une boîte englobante dans une perspective, en trois quarts.
 *
 * Le mode logement complet n'a pas de preset backend : les caméras publiées cadrent une pièce.
 * Ce calcul remplace ce que `isometric_view` fait pour une pièce, mais sur l'emprise réelle de la
 * scène construite — donc sans supposer quoi que ce soit des cotes du plan.
 */
export function frameBox(box: Box3, fovDeg: number, aspect = 1.6): Framing {
  const center = box.getCenter(new Vector3())
  const size = box.getSize(new Vector3())
  const radius = Math.max(1, size.length() / 2)

  const vertical = (fovDeg * Math.PI) / 180
  // Le champ horizontal est le plus contraignant sur un logement large que haut.
  const horizontal = 2 * Math.atan(Math.tan(vertical / 2) * aspect)
  const distance = (radius / Math.sin(Math.min(vertical, horizontal) / 2)) * 1.05

  const direction = new Vector3(0.72, 0.62, 0.72).normalize()
  const position = center.clone().add(direction.multiplyScalar(distance))
  return {
    position: [position.x, position.y, position.z],
    target: [center.x, center.y, center.z],
  }
}

/** Emprise réelle de ce qui a été construit. */
export function boundsOf(root: Object3D): Box3 {
  return new Box3().setFromObject(root)
}

/** Nombre d'appels de dessin : un par maillage visible. Les tests s'en servent comme repère. */
export function drawCallCount(root: Object3D): number {
  let total = 0
  root.traverse((object) => {
    if (object instanceof Mesh && object.visible) {
      let ancestor: Object3D | null = object.parent
      while (ancestor) {
        if (!ancestor.visible) return
        ancestor = ancestor.parent
      }
      total += 1
    }
  })
  return total
}
