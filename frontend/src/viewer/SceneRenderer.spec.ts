/**
 * Cycle de vie de la scène 3D.
 *
 * Le rendu WebGL n'est pas testable ici, mais ce qui fuyait, si : les géométries naissaient dans
 * des `computed`, hors de l'arbre TresJS, et personne ne les libérait. Ce fichier vérifie que
 * chaque reconstruction rend le lot précédent, que le démontage rend le dernier, et qu'un simple
 * changement de visibilité ne reconstruit rien.
 *
 * `primitive` est déclaré élément personnalisé : c'est le rôle que joue `templateCompilerOptions`
 * de TresJS dans l'application, et sans lui Vue avertirait d'un composant introuvable.
 */
import { mount } from '@vue/test-utils'
import { Group, Mesh } from 'three'
import { nextTick } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { HorizontalNode, SceneRoom, WallNode } from '@/api/types'
import SceneRenderer from '@/viewer/SceneRenderer.vue'
import { clearCarvedCache } from '@/viewer/csg'
import { showEverything } from '@/viewer/visibility'

const mountOptions = {
  global: { config: { compilerOptions: { isCustomElement: (tag: string) => tag === 'primitive' } } },
}

const wall: WallNode = {
  kind: 'wall',
  face_id: 1,
  face_label: 'A',
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
}

const floor: HorizontalNode = {
  kind: 'floor',
  face_id: 2,
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

const room = (id: number): SceneRoom => ({
  id,
  name: `Pièce ${id}`,
  wall_thickness_cm: 10,
  ceiling_height_cm: 250,
  floor_area_cm2: 120000,
  net_floor_area_cm2: 115000,
  nodes: [wall, floor],
  cameras: [],
})

/** Surveille la libération de toutes les ressources d'une scène déjà construite. */
function watchDisposal(group: Group): ReturnType<typeof vi.spyOn>[] {
  const spies: ReturnType<typeof vi.spyOn>[] = []
  group.traverse((object) => {
    if (!(object instanceof Mesh)) return
    spies.push(vi.spyOn(object.geometry, 'dispose'))
    const materials = Array.isArray(object.material) ? object.material : [object.material]
    materials.forEach((material) => spies.push(vi.spyOn(material, 'dispose')))
  })
  return spies
}

beforeEach(() => clearCarvedCache())

describe('cycle de vie de la scène', () => {
  it('construit la scène et la publie', async () => {
    const wrapper = mount(SceneRenderer, {
      ...mountOptions,
      props: { rooms: [room(1)], visibility: showEverything(['A', 'SOL']) },
    })
    await nextTick()

    const built = wrapper.emitted('built')
    expect(built).toHaveLength(1)
    expect(built![0]![0]).toBeInstanceOf(Group)
    wrapper.unmount()
  })

  it('rend les ressources du lot précédent à chaque reconstruction', async () => {
    const wrapper = mount(SceneRenderer, {
      ...mountOptions,
      props: { rooms: [room(1)], visibility: showEverything(['A', 'SOL']) },
    })
    await nextTick()
    const spies = watchDisposal(wrapper.emitted('built')![0]![0] as Group)
    expect(spies.length).toBeGreaterThan(0)

    await wrapper.setProps({ rooms: [room(2)] })
    await nextTick()
    await nextTick()

    // C'est exactement ce qui manquait : la scène remplacée abandonnait ses tampons sur la carte.
    spies.forEach((spy) => expect(spy).toHaveBeenCalled())
    wrapper.unmount()
  })

  it('rend tout au démontage', async () => {
    const wrapper = mount(SceneRenderer, {
      ...mountOptions,
      props: { rooms: [room(1)], visibility: showEverything(['A', 'SOL']) },
    })
    await nextTick()
    const spies = watchDisposal(wrapper.emitted('built')![0]![0] as Group)

    wrapper.unmount()

    spies.forEach((spy) => expect(spy).toHaveBeenCalled())
  })

  it('ne reconstruit rien quand seule la visibilité change', async () => {
    // Trois positions à faire varier au clic : reconstruire la géométrie à chaque fois
    // annulerait tout le bénéfice de la mémoïsation.
    const wrapper = mount(SceneRenderer, {
      ...mountOptions,
      props: { rooms: [room(1)], visibility: showEverything(['A', 'SOL']) },
    })
    await nextTick()

    await wrapper.setProps({ visibility: { A: 'transparent', SOL: 'visible' } })
    await nextTick()

    expect(wrapper.emitted('built')).toHaveLength(1)
    wrapper.unmount()
  })

  it('applique la visibilité reçue sans attendre un second rendu', async () => {
    const wrapper = mount(SceneRenderer, {
      ...mountOptions,
      props: { rooms: [room(1)], visibility: { A: 'hidden', SOL: 'visible' } },
    })
    await nextTick()

    const group = wrapper.emitted('built')![0]![0] as Group
    const face = group.children[0]!.children.find((child) => child.userData.faceKey === 'A')
    expect(face!.visible).toBe(false)
    wrapper.unmount()
  })
})
