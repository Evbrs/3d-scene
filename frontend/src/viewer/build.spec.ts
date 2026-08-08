import { Box3, InstancedMesh, Mesh, MeshStandardMaterial, Vector3 } from 'three'
import { beforeAll, beforeEach, describe, expect, it } from 'vitest'

import type { FurnitureNode, HorizontalNode, Primitive, SceneRoom, WallNode } from '@/api/types'
import {
  FURNITURE_FALLBACK,
  applyVisibility,
  boundsOf,
  buildRoom,
  buildScene,
  drawCallCount,
  frameBox,
  needsCsg,
  placementMatrix,
  wallFacings,
} from '@/viewer/build'
import { clearCarvedCache, ensureCsgReady } from '@/viewer/csg'
import { ResourcePool } from '@/viewer/resources'
import { TRANSPARENT_OPACITY, effectiveVisibility, showEverything } from '@/viewer/visibility'

const primitive = (over: Partial<Primitive> = {}): Primitive => ({
  type: 'box',
  offset: [0, 0, 0],
  size: [40, 40, 40],
  color_slot: 'corps',
  color: null,
  operation: 'add',
  axis: 'y',
  ...over,
})

function wall(label: string, over: Partial<WallNode> = {}): WallNode {
  return {
    kind: 'wall',
    face_id: label.charCodeAt(0),
    face_label: label,
    length_cm: 400,
    height_cm: 250,
    origin: [0, 0, 0],
    rotation_y: 0,
    outward_normal: [0, 0, -1],
    outline: [
      [0, 0],
      [400, 0],
      [400, 250],
      [0, 250],
    ],
    holes: [],
    extrude_depth_cm: 10,
    extrude_offset_cm: -5,
    covering: {},
    ...over,
  }
}

const floor: HorizontalNode = {
  kind: 'floor',
  face_id: 900,
  face_label: 'SOL',
  origin: [0, 0, 0],
  rotation_x: -Math.PI / 2,
  outline: [
    [0, 0],
    [400, 0],
    [400, 300],
    [0, 300],
  ],
  holes: [],
  covering: {},
}

function furniture(over: Partial<FurnitureNode> = {}): FurnitureNode {
  return {
    kind: 'furniture',
    element_id: 1,
    face_label: 'A',
    furniture_type_slug: 'commode',
    position: [100, 0, 20],
    rotation_y: 0,
    size_cm: [90, 80, 45],
    primitives: [primitive()],
    requires_csg: false,
    variant_params: {},
    ...over,
  }
}

function room(over: Partial<SceneRoom> = {}): SceneRoom {
  return {
    id: 7,
    name: 'Salle de bain',
    wall_thickness_cm: 10,
    ceiling_height_cm: 250,
    floor_area_cm2: 120000,
    net_floor_area_cm2: 115000,
    nodes: [wall('A'), floor],
    cameras: [],
    ...over,
  }
}

let pool: ResourcePool

// La librairie booléenne est chargée à la demande : les tests de fidélité l'exigent, la scène de
// production ne la charge que si un meuble la réclame.
beforeAll(() => ensureCsgReady())

beforeEach(() => {
  pool = new ResourcePool()
  clearCarvedCache()
})

describe('structure de la scène (spec §3.4)', () => {
  it('range chaque face dans son groupe, taggé de son étiquette', () => {
    const built = buildRoom(room({ nodes: [wall('A'), wall('B'), floor] }), { pool })

    const keys = built.children.map((child) => child.userData.faceKey)
    expect(keys).toEqual(['A', 'B', 'SOL'])
  })

  it('préfixe les clés par la pièce en mode logement complet', () => {
    // Sans préfixe, la face A du séjour et celle de la chambre se confondent : masquer l'une
    // masquerait l'autre.
    const built = buildScene([room({ id: 3 }), room({ id: 5 })], { pool, roomScoped: true })

    const keys: string[] = []
    built.traverse((object) => {
      if (object.userData.faceKey) keys.push(object.userData.faceKey as string)
    })
    expect(keys).toContain('3:A')
    expect(keys).toContain('5:A')
  })

  it('sort le mobilier libre des groupes de face', () => {
    // Un meuble sans `face_label` est ancré à la pièce : aucun isolement de face ne doit le faire
    // disparaître. Son groupe ne porte donc aucune clé.
    const libre = { ...furniture(), face_label: null as unknown as string, element_id: 9 }
    const built = buildRoom(room({ nodes: [wall('A'), libre] }), { pool })

    const orphelin = built.children.find((child) => child.userData.faceKey === undefined)
    expect(orphelin?.name).toBe('mobilier-7')
    expect(orphelin?.children).toHaveLength(1)

    applyVisibility(built, { A: 'hidden', SOL: 'hidden' })
    expect(orphelin!.visible).toBe(true)
  })

  it('accroche le mobilier au groupe de la face qui le porte', () => {
    const built = buildRoom(
      room({ nodes: [wall('A'), wall('B'), furniture({ face_label: 'B' })] }),
      { pool },
    )

    const groupA = built.children.find((child) => child.userData.faceKey === 'A')!
    const groupB = built.children.find((child) => child.userData.faceKey === 'B')!
    expect(groupA.children).toHaveLength(1)
    expect(groupB.children).toHaveLength(2)
  })
})

describe('trois positions de visibilité, sans reconstruction', () => {
  it('masque un groupe entier, mobilier compris', () => {
    const built = buildRoom(room({ nodes: [wall('A'), furniture()] }), { pool })

    applyVisibility(built, { ...showEverything(['A', 'SOL']), A: 'hidden' })

    expect(built.children.find((child) => child.userData.faceKey === 'A')!.visible).toBe(false)
  })

  it('échange le matériau contre sa variante transparente', () => {
    const built = buildRoom(room(), { pool })
    const mur = built.children[0]!.children[0] as Mesh

    applyVisibility(built, { A: 'transparent', SOL: 'visible' })
    expect((mur.material as MeshStandardMaterial).opacity).toBe(TRANSPARENT_OPACITY)

    applyVisibility(built, { A: 'visible', SOL: 'visible' })
    expect((mur.material as MeshStandardMaterial).opacity).toBe(1)
  })

  it('ne fabrique que deux variantes par matériau, quel que soit le nombre de faces', () => {
    const avant = new ResourcePool()
    buildRoom(room({ nodes: [wall('A'), wall('B'), wall('C'), wall('D')] }), { pool: avant })

    // Quatre murs de même revêtement : une géométrie chacune (elles diffèrent), mais deux
    // matériaux en tout. Sans mutualisation, ce serait huit.
    expect(avant.reuseCount).toBeGreaterThanOrEqual(6)
    avant.dispose()
  })
})

describe('coût de rendu', () => {
  it('regroupe les primitives identiques en un seul appel de dessin', () => {
    // Une commode à quatre façades identiques : quatre maillages avant, un `InstancedMesh` après.
    const facades = [0, 1, 2, 3].map((rang) =>
      primitive({ size: [80, 15, 2], offset: [0, rang * 18, 22] }),
    )
    const built = buildRoom(
      room({ nodes: [wall('A'), furniture({ primitives: [primitive(), ...facades] })] }),
      { pool },
    )

    const groupA = built.children.find((child) => child.userData.faceKey === 'A')!
    const instances = groupA.children.filter((child) => child instanceof InstancedMesh)
    expect(instances).toHaveLength(1)
    expect((instances[0] as InstancedMesh).count).toBe(4)
    // Le mur, le caisson isolé, et le lot des quatre façades.
    expect(drawCallCount(groupA)).toBe(3)
  })

  it('ne compte plus les appels de dessin d’une face masquée', () => {
    const built = buildRoom(room({ nodes: [wall('A'), wall('B'), floor] }), { pool })
    const avant = drawCallCount(built)

    applyVisibility(built, { A: 'hidden', B: 'visible', SOL: 'visible' })

    expect(drawCallCount(built)).toBe(avant - 1)
  })

  it('mutualise la géométrie de deux meubles identiques', () => {
    const built = buildRoom(
      room({
        nodes: [
          wall('A'),
          furniture({ element_id: 1 }),
          furniture({ element_id: 2, position: [250, 0, 20] }),
        ],
      }),
      { pool },
    )

    const groupA = built.children.find((child) => child.userData.faceKey === 'A')!
    const instanced = groupA.children.find((child) => child instanceof InstancedMesh)
    expect((instanced as InstancedMesh).count).toBe(2)
  })
})

/** Nombre de triangles réellement dessinés — indexés ou non. */
function triangleCount(mesh: Mesh): number {
  const index = mesh.geometry.getIndex()
  return (index ? index.count : mesh.geometry.getAttribute('position').count) / 3
}

describe('fidélité du mobilier creusé (spec §4.2)', () => {
  const baignoire = furniture({
    furniture_type_slug: 'baignoire',
    size_cm: [170, 55, 75],
    requires_csg: true,
    primitives: [
      primitive({ size: [170, 55, 75], color_slot: 'email' }),
      primitive({
        size: [149.6, 46.75, 64.5],
        offset: [0, 6.6, 0],
        color_slot: 'email',
        operation: 'subtract',
      }),
    ],
  })

  it('rend une cuve creuse et non une boîte pleine', () => {
    const built = buildRoom(room({ nodes: [wall('A'), baignoire] }), { pool })
    const groupA = built.children.find((child) => child.userData.faceKey === 'A')!
    const cuve = groupA.children[1] as Mesh

    // Une boîte pleine compte 12 triangles. Une cuve creusée en compte beaucoup plus.
    expect(triangleCount(cuve)).toBeGreaterThan(12)
  })

  it('ne réclame la librairie booléenne que si la scène en a l’usage', () => {
    // Elle pèse une trentaine de kilo-octets compressés : la page de partage, ouverte au
    // téléphone, n'a pas à les payer pour une chambre.
    expect(needsCsg([room({ nodes: [wall('A'), furniture()] })])).toBe(false)
    expect(needsCsg([room({ nodes: [wall('A'), baignoire] })])).toBe(true)
  })

  it('n’active le CSG que là où le backend le demande', () => {
    const plein = { ...baignoire, requires_csg: false }
    const built = buildRoom(room({ nodes: [wall('A'), plein] }), { pool })
    const groupA = built.children.find((child) => child.userData.faceKey === 'A')!
    const cuve = groupA.children[1] as Mesh

    // `requires_csg` est le seul déclencheur : la librairie est expérimentale (spec §3.2).
    expect(triangleCount(cuve)).toBe(12)
  })

  it('instancie deux meubles creusés identiques, qui partagent leur géométrie', () => {
    // Le creusement est mémoïsé sur la recette (slug, dimensions, variante), pas sur l'élément :
    // deux bacs de douche identiques ne paient qu'une évaluation booléenne et un appel de dessin.
    const built = buildRoom(
      room({
        nodes: [
          wall('A'),
          baignoire,
          { ...baignoire, element_id: 2, position: [300, 0, 20] } as FurnitureNode,
        ],
      }),
      { pool },
    )
    const groupA = built.children.find((child) => child.userData.faceKey === 'A')!
    const lot = groupA.children.find((child) => child instanceof InstancedMesh)
    expect((lot as InstancedMesh).count).toBe(2)
    expect(drawCallCount(groupA)).toBe(2)
  })

  it('ne mélange pas deux recettes creusées différentes', () => {
    // Une baignoire de 160 n'est pas une baignoire de 170 : les partager donnerait la mauvaise
    // forme à l'une des deux.
    const courte = {
      ...baignoire,
      element_id: 2,
      size_cm: [160, 55, 75],
      primitives: baignoire.primitives.map((entry) => ({ ...entry, size: [160, 55, 75] })),
    } as FurnitureNode
    const built = buildRoom(room({ nodes: [wall('A'), baignoire, courte] }), { pool })

    const groupA = built.children.find((child) => child.userData.faceKey === 'A')!
    expect(groupA.children.filter((child) => child instanceof InstancedMesh)).toHaveLength(0)
    expect(drawCallCount(groupA)).toBe(3)
  })
})

describe('placement', () => {
  it('pose la primitive dans le repère du meuble, puis le meuble dans le monde', () => {
    // Un meuble en (100, 0, 20), tourné d'un quart de tour, dont la primitive est décalée de 30
    // sur son axe local X : la rotation amène +X sur -Z.
    const matrix = placementMatrix([100, 0, 20], Math.PI / 2, [30, 0, 0])
    const point = new Vector3(0, 0, 0).applyMatrix4(matrix)

    expect(point.x).toBeCloseTo(100, 5)
    expect(point.z).toBeCloseTo(-10, 5)
  })

  it('donne au mobilier la couleur de repli quand l’emplacement n’est pas choisi', () => {
    const built = buildRoom(room({ nodes: [wall('A'), furniture()] }), { pool })
    const groupA = built.children.find((child) => child.userData.faceKey === 'A')!
    const meuble = groupA.children[1] as Mesh

    expect((meuble.material as MeshStandardMaterial).color.getHexString()).toBe(
      FURNITURE_FALLBACK.slice(1),
    )
  })
})

describe('logement complet', () => {
  it('place chaque pièce à ses coordonnées absolues, sans rien recalculer', () => {
    const sejour = room({ id: 1, nodes: [wall('A')] })
    const chambre = room({
      id: 2,
      nodes: [wall('A', { origin: [1000, 0, 0] })],
    })

    const built = buildScene([sejour, chambre], { pool, roomScoped: true })
    const bounds = boundsOf(built)

    expect(bounds.min.x).toBeCloseTo(0, 3)
    expect(bounds.max.x).toBeCloseTo(1400, 3)
  })

  it('cadre l’emprise réelle plutôt qu’une cote supposée', () => {
    const box = new Box3(new Vector3(0, 0, 0), new Vector3(1200, 250, 800))
    const framing = frameBox(box, 50)

    expect(framing.target).toEqual([600, 125, 400])
    // La caméra est en trois quarts, au-dessus, et assez loin pour tout contenir.
    const distance = Math.hypot(
      framing.position[0] - 600,
      framing.position[1] - 125,
      framing.position[2] - 400,
    )
    expect(distance).toBeGreaterThan(box.getSize(new Vector3()).length() / 2)
    expect(framing.position[1]).toBeGreaterThan(125)
  })
})

describe('murs qui font écran (outward_normal)', () => {
  it('masque le mur que la caméra regarde par l’extérieur', () => {
    // Mur en z = 0, normale sortante vers -Z : une caméra en z = -800 le voit de dos et cache
    // toute la pièce derrière lui.
    const facings = wallFacings([room({ nodes: [wall('A')] })], false)
    const chosen = showEverything(['A', 'SOL'])

    expect(effectiveVisibility(chosen, facings, [200, 300, -800]).A).toBe('hidden')
    expect(effectiveVisibility(chosen, facings, [200, 300, 800]).A).toBe('visible')
  })

  it('laisse le réglage de l’utilisateur intact', () => {
    // Le masquage est une surcouche : l'état choisi doit être retrouvé tel quel dès que la
    // caméra repasse de l'autre côté.
    const facings = wallFacings([room({ nodes: [wall('A')] })], false)
    const chosen = showEverything(['A'])

    effectiveVisibility(chosen, facings, [200, 300, -800])

    expect(chosen.A).toBe('visible')
    expect(effectiveVisibility(chosen, facings, null).A).toBe('visible')
  })

  it('juge depuis le milieu du mur, pas depuis son départ', () => {
    // Un mur de six mètres jugé depuis son départ conclut de travers dès que la caméra est en
    // biais au-delà de son extrémité.
    const long = wall('A', { length_cm: 600, outward_normal: [0, 0, -1] })
    const facings = wallFacings([room({ nodes: [long] })], false)

    expect(effectiveVisibility(showEverything(['A']), facings, [300, 200, 50]).A).toBe('visible')
  })
})

describe('libération des ressources', () => {
  it('rend tout ce qu’une scène a alloué', () => {
    const built = buildRoom(room({ nodes: [wall('A'), wall('B'), floor, furniture()] }), { pool })
    expect(pool.size).toBeGreaterThan(0)

    const geometries: unknown[] = []
    built.traverse((object) => {
      if (object instanceof Mesh) geometries.push(object.geometry)
    })
    expect(geometries.length).toBeGreaterThan(0)

    pool.dispose()
    expect(pool.size).toBe(0)
  })
})
