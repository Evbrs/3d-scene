/**
 * Comportements du canvas de plan qui ne relèvent pas de la géométrie pure.
 *
 * Konva est remplacé par des composants inertes : le moteur a besoin d'un vrai contexte 2D que
 * l'environnement de test ne fournit pas, et ce qui est vérifié ici — le filtrage des touches, la
 * survie du brouillon, la dépose depuis la palette — n'a rien à voir avec le rendu.
 */
import { mount } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { Face, PlanElement } from '@/api/types'
import PlanCanvas from '@/editor/PlanCanvas.vue'
import { wallKey } from '@/editor/drawing'
import { DRAG_MIME, type DragPayload } from '@/editor/palette'

vi.mock('vue-konva', () => {
  /**
   * Nœud inerte qui recopie le texte de sa configuration dans le DOM.
   *
   * Sans cette recopie, tout ce que Konva écrit — étiquettes de meubles, cotes, avertissements —
   * n'existe que dans un objet `config` invisible du test : on ne pourrait vérifier que les
   * éléments sont bien dessinés, seulement qu'ils ne plantent pas.
   */
  const inert = (name: string) =>
    defineComponent({
      name,
      setup: (_props, { attrs, slots }) => () => {
        const config = attrs.config as { text?: unknown } | undefined
        const text = typeof config?.text === 'string' ? config.text : ''
        return h('div', { 'data-konva': name }, [text, slots.default?.()])
      },
    })
  return {
    Arc: inert('Arc'),
    Circle: inert('Circle'),
    Group: inert('Group'),
    Image: inert('Image'),
    Label: inert('Label'),
    Layer: inert('Layer'),
    Line: inert('Line'),
    Rect: inert('Rect'),
    Stage: inert('Stage'),
    Tag: inert('Tag'),
    Text: inert('Text'),
  }
})

const CARRE = [
  [0, 0],
  [400, 0],
  [400, 300],
  [0, 300],
]

function wall(id: number, label: string): Face {
  return {
    id,
    room_id: 5,
    label,
    kind: 'wall',
    start_x_cm: 0,
    start_y_cm: 0,
    end_x_cm: 400,
    end_y_cm: 0,
    covering: {},
    elements: [],
  }
}

const MURS = [wall(1, 'A'), wall(2, 'B'), wall(3, 'C'), wall(4, 'D')]

function freeElement(overrides: Partial<PlanElement> = {}): PlanElement {
  return {
    id: 77,
    face_id: null,
    room_id: 5,
    kind: 'furniture',
    x_offset_cm: 0,
    y_offset_cm: 0,
    pos_x_cm: 200,
    pos_y_cm: 150,
    width_cm: 140,
    height_cm: 45,
    depth_cm: 80,
    rotation_deg: 0,
    furniture_type_id: 3,
    colors: {},
    variant_params: {},
    ...overrides,
  }
}

const CHARGE: DragPayload = {
  furnitureTypeId: 3,
  slug: 'lit-double',
  name: 'Lit double',
  width_cm: 140,
  height_cm: 45,
  depth_cm: 200,
}

/** Émet la touche depuis un élément réellement présent dans le document, pour qu'elle remonte. */
function pressFrom(target: HTMLElement, key: string): void {
  target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }))
}

beforeEach(() => {
  localStorage.clear()
  document.body.innerHTML = ''
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('montage', () => {
  it('affiche le décompte du contour, des murs, la surface et le périmètre', () => {
    const wrapper = mount(PlanCanvas, { props: { polygon: CARRE, faces: MURS } })

    expect(wrapper.text()).toContain('4 sommet(s)')
    expect(wrapper.text()).toContain('4 mur(s)')
    expect(wrapper.text()).toContain('12.00 m²')
    // Le périmètre est ce qu'on recopie sur un devis de plinthes : il est affiché en permanence.
    expect(wrapper.text()).toContain('1400 cm')
  })

  it('signale un contour qui se recoupe', () => {
    const wrapper = mount(PlanCanvas, {
      props: {
        polygon: [
          [0, 0],
          [400, 0],
          [0, 300],
          [400, 300],
        ],
        faces: [],
      },
    })

    expect(wrapper.find('[role="alert"]').exists()).toBe(true)
  })

  it('se présente comme une application au lecteur d’écran', () => {
    const wrapper = mount(PlanCanvas, {
      props: { polygon: CARRE, faces: MURS, roomName: 'Séjour' },
    })
    const surface = wrapper.find('.surface')

    expect(surface.attributes('role')).toBe('application')
    expect(surface.attributes('aria-label')).toContain('Séjour')
  })
})

describe('clés de mur', () => {
  it('reste unique sur un rectangle non encore enregistré', () => {
    // C'est le cas qui produisait deux paires de doublons : les quatre murs d'un rectangle
    // partagent deux abscisses de départ et deux longueurs.
    const keys = [0, 1, 2, 3].map((index) => wallKey(undefined, index))

    expect(new Set(keys).size).toBe(4)
  })

  it('suit la face dès qu’elle existe, pas le rang', () => {
    expect(wallKey(12, 0)).toBe(wallKey(12, 3))
    expect(wallKey(12, 0)).not.toBe(wallKey(13, 0))
  })
})

describe('raccourcis clavier', () => {
  it('ignore la touche quand la saisie a lieu dans un champ', async () => {
    localStorage.setItem('renovation.brouillon.7:5', JSON.stringify([[0, 0], [100, 0], [100, 80]]))
    const wrapper = mount(PlanCanvas, {
      props: { polygon: [], faces: [], mode: 'draw', draftKey: '7:5' },
      attachTo: document.body,
    })
    // Le brouillon est relu au montage : le rendu correspondant arrive au tick suivant.
    await nextTick()
    expect(wrapper.text()).toContain('3 sommet(s)')

    const champ = document.createElement('input')
    document.body.appendChild(champ)
    pressFrom(champ, 'Backspace')
    await nextTick()

    // Corriger le nom d'une pièce au clavier ne doit pas amputer le tracé.
    expect(wrapper.text()).toContain('3 sommet(s)')

    pressFrom(document.body, 'Backspace')
    await nextTick()

    expect(wrapper.text()).toContain('2 sommet(s)')
    wrapper.unmount()
  })

  it('ne réagit pas hors du mode tracé', async () => {
    localStorage.setItem('renovation.brouillon.7:5', JSON.stringify([[0, 0], [100, 0], [100, 80]]))
    const wrapper = mount(PlanCanvas, {
      props: { polygon: CARRE, faces: [], mode: 'navigate', draftKey: '7:5' },
      attachTo: document.body,
    })

    pressFrom(document.body, 'Backspace')
    await nextTick()

    expect(wrapper.text()).toContain('4 sommet(s)')
    wrapper.unmount()
  })
})

describe('brouillon de tracé', () => {
  it('se restaure depuis le stockage local, par pièce', async () => {
    localStorage.setItem('renovation.brouillon.7:5', JSON.stringify([[0, 0], [100, 0]]))
    localStorage.setItem('renovation.brouillon.7:6', JSON.stringify([[0, 0], [50, 0], [50, 50]]))

    const autre = mount(PlanCanvas, {
      props: { polygon: [], faces: [], mode: 'draw', draftKey: '7:6' },
    })
    await nextTick()

    // Le brouillon suit la pièce : c'est ce qui l'empêche de ressortir dans la mauvaise.
    expect(autre.text()).toContain('3 sommet(s)')
  })

  it('efface l’entrée du stockage quand le tracé est abandonné', async () => {
    localStorage.setItem('renovation.brouillon.7:5', JSON.stringify([[0, 0], [100, 0]]))
    const wrapper = mount(PlanCanvas, {
      props: { polygon: [], faces: [], mode: 'draw', draftKey: '7:5' },
      attachTo: document.body,
    })

    pressFrom(document.body, 'Escape')
    await nextTick()

    expect(localStorage.getItem('renovation.brouillon.7:5')).toBeNull()
    expect(wrapper.emitted('finish-drawing')).toHaveLength(1)
    wrapper.unmount()
  })

  it('résiste à un brouillon illisible', async () => {
    localStorage.setItem('renovation.brouillon.7:5', '{ceci n’est pas du JSON')

    const wrapper = mount(PlanCanvas, {
      props: { polygon: [], faces: [], mode: 'draw', draftKey: '7:5' },
    })
    await nextTick()

    expect(wrapper.text()).toContain('0 sommet(s)')
  })
})

describe('saisie numérique de la cote', () => {
  it('n’apparaît qu’une fois le premier sommet posé', async () => {
    localStorage.setItem('renovation.brouillon.7:5', JSON.stringify([[0, 0]]))
    const vide = mount(PlanCanvas, {
      props: { polygon: [], faces: [], mode: 'navigate', draftKey: '7:9' },
    })
    await nextTick()

    expect(vide.find('#cote-mur').exists()).toBe(false)

    const trace = mount(PlanCanvas, {
      props: { polygon: [], faces: [], mode: 'draw', draftKey: '7:5' },
    })
    await nextTick()

    // Un vrai champ, donc un chemin clavier complet et un focus visible : c'est la seule façon
    // d'obtenir un mur de 347 cm sans viser le pixel exact.
    expect(trace.find('#cote-mur').exists()).toBe(true)
    expect(trace.find('label[for="cote-mur"]').exists()).toBe(true)
  })

  it('pose le sommet à la distance saisie', async () => {
    localStorage.setItem('renovation.brouillon.7:5', JSON.stringify([[0, 0]]))
    const wrapper = mount(PlanCanvas, {
      props: { polygon: [], faces: [], mode: 'draw', draftKey: '7:5' },
    })
    await nextTick()

    await wrapper.find('#cote-mur').setValue('347')
    await wrapper.find('#cote-mur').trigger('keydown.enter')
    await nextTick()

    expect(wrapper.text()).toContain('2 sommet(s)')
    expect(JSON.parse(localStorage.getItem('renovation.brouillon.7:5') ?? '[]')).toEqual([
      [0, 0],
      [347, 0],
    ])
  })
})

describe('mobilier posé au sol', () => {
  it('le rend sans qu’aucune face ne le porte', () => {
    const wrapper = mount(PlanCanvas, {
      props: {
        polygon: CARRE,
        faces: MURS,
        freeElements: [freeElement()],
        furnitureNames: { 3: 'Lit double' },
      },
    })

    // Un meuble libre n'appartient à aucune face : s'il fallait une face pour le dessiner, il
    // serait invisible dans l'éditeur tout en existant en base.
    expect(wrapper.text()).toContain('Lit double')
  })
})

describe('dépose depuis la palette', () => {
  function dropAt(wrapper: ReturnType<typeof mount>, payload: string | null): Promise<void> {
    return wrapper.find('.surface').trigger('drop', {
      clientX: 300,
      clientY: 240,
      dataTransfer: {
        types: [DRAG_MIME],
        getData: () => payload ?? '',
        setData: () => {},
      },
    })
  }

  it('émet l’intention avec l’ancrage résolu', async () => {
    const wrapper = mount(PlanCanvas, {
      props: { polygon: CARRE, faces: MURS, roomId: 5, dragPayload: CHARGE },
    })

    await dropAt(wrapper, JSON.stringify(CHARGE))

    const emis = wrapper.emitted('drop-furniture')
    expect(emis).toHaveLength(1)
    const drop = (emis![0] as [{ payload: DragPayload; target: { kind: string } }])[0]
    expect(drop.payload.furnitureTypeId).toBe(3)
    expect(['face', 'room', 'refuse']).toContain(drop.target.kind)
  })

  it('n’émet rien quand la charge utile n’en est pas une', async () => {
    const wrapper = mount(PlanCanvas, {
      props: { polygon: CARRE, faces: MURS, roomId: 5 },
    })

    // Un glisser venu d'ailleurs — fichier, texte, onglet — ne doit pas poser un meuble.
    await dropAt(wrapper, 'ceci n’est pas du JSON')

    expect(wrapper.emitted('drop-furniture')).toBeUndefined()
  })

  it('refuse la dépose quand aucune pièce n’est désignée', async () => {
    const wrapper = mount(PlanCanvas, {
      props: { polygon: CARRE, faces: MURS, roomId: null, dragPayload: CHARGE },
    })

    await dropAt(wrapper, JSON.stringify(CHARGE))

    const drop = (wrapper.emitted('drop-furniture')![0] as [{ target: { kind: string } }])[0]
    expect(drop.target.kind).toBe('refuse')
  })
})

describe('fond de plan', () => {
  it('avertit tant que l’échelle n’a pas été mesurée', () => {
    const wrapper = mount(PlanCanvas, {
      props: {
        polygon: CARRE,
        faces: MURS,
        backgroundUrl: '/media/plan.png',
        background: {
          scaleCmPerPx: null,
          offsetXCm: 0,
          offsetYCm: 0,
          rotationDeg: 0,
          opacity: 1,
        },
      },
    })

    // « Image posée, pas encore calibrée » est un état réel : le confondre avec une mesure ferait
    // dessiner un logement faux sans le moindre avertissement.
    expect(wrapper.text()).toContain('non calibré')
  })

  it('se tait une fois l’échelle mesurée', () => {
    const wrapper = mount(PlanCanvas, {
      props: {
        polygon: CARRE,
        faces: MURS,
        backgroundUrl: '/media/plan.png',
        background: {
          scaleCmPerPx: 1.8,
          offsetXCm: 0,
          offsetYCm: 0,
          rotationDeg: 0,
          opacity: 0.6,
        },
      },
    })

    expect(wrapper.text()).not.toContain('non calibré')
  })
})
