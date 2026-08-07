/**
 * Comportements du canvas de plan qui ne relèvent pas de la géométrie pure.
 *
 * Konva est remplacé par des composants inertes : le moteur a besoin d'un vrai contexte 2D que
 * l'environnement de test ne fournit pas, et ce qui est vérifié ici — le filtrage des touches et
 * la survie du brouillon — n'a rien à voir avec le rendu.
 */
import { mount } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { Face } from '@/api/types'
import PlanCanvas from '@/editor/PlanCanvas.vue'
import { wallKey } from '@/editor/drawing'

vi.mock('vue-konva', () => {
  const inert = (name: string) =>
    defineComponent({
      name,
      setup: (_props, { slots }) => () => h('div', { 'data-konva': name }, slots.default?.()),
    })
  return {
    Arc: inert('Arc'),
    Circle: inert('Circle'),
    Label: inert('Label'),
    Layer: inert('Layer'),
    Line: inert('Line'),
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
  it('affiche le décompte du contour et des murs', () => {
    const wrapper = mount(PlanCanvas, {
      props: { polygon: CARRE, faces: [wall(1, 'A'), wall(2, 'B'), wall(3, 'C'), wall(4, 'D')] },
    })

    expect(wrapper.text()).toContain('4 sommet(s)')
    expect(wrapper.text()).toContain('4 mur(s)')
    expect(wrapper.text()).toContain('12.00 m²')
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
