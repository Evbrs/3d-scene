import { describe, expect, it } from 'vitest'

import {
  TRANSPARENT_OPACITY,
  fromViewState,
  isolate,
  isVisible,
  materialFor,
  nextVisibility,
  opacityFor,
  showEverything,
  toViewState,
} from '@/viewer/visibility'

const LABELS = ['A', 'B', 'C', 'D', 'SOL', 'PLAFOND']

describe('états de visibilité (spec §3.4)', () => {
  it('propose bien trois positions', () => {
    expect(nextVisibility('visible')).toBe('transparent')
    expect(nextVisibility('transparent')).toBe('hidden')
    expect(nextVisibility('hidden')).toBe('visible')
  })

  it('part de « visible » quand rien n’est défini', () => {
    expect(nextVisibility(undefined)).toBe('transparent')
    expect(isVisible(undefined)).toBe(true)
    expect(opacityFor(undefined)).toBe(1)
  })

  it("garde une face transparente affichée, contrairement à une face masquée", () => {
    // C'est tout l'intérêt du troisième état : conserver le repère spatial (spec §3.4).
    expect(isVisible('transparent')).toBe(true)
    expect(opacityFor('transparent')).toBe(TRANSPARENT_OPACITY)
    expect(isVisible('hidden')).toBe(false)
  })
})

describe('isolement de face', () => {
  it('rend les autres faces transparentes plutôt que masquées', () => {
    const state = isolate(['A'], LABELS)

    expect(state.A).toBe('visible')
    expect(state.B).toBe('transparent')
    expect(Object.values(state).filter((value) => value === 'hidden')).toHaveLength(0)
  })

  it('sait isoler plusieurs faces à la fois', () => {
    const state = isolate(['A', 'B'], LABELS)
    expect([state.A, state.B]).toEqual(['visible', 'visible'])
    expect(state.C).toBe('transparent')
  })

  it('sait tout réafficher', () => {
    expect(Object.values(showEverything(LABELS))).toEqual(LABELS.map(() => 'visible'))
  })
})

describe('sérialisation pour le partage de vue (P8)', () => {
  it('fait un aller-retour fidèle', () => {
    const original = {
      A: 'visible' as const,
      B: 'transparent' as const,
      C: 'hidden' as const,
      D: 'visible' as const,
      SOL: 'visible' as const,
      PLAFOND: 'hidden' as const,
    }

    const restored = fromViewState(toViewState(original, 'face-A'), LABELS)

    expect(restored).toEqual(original)
  })

  it("conserve le preset de caméra et la position libre", () => {
    const state = toViewState({ A: 'visible' }, 'orbite', [10, 20, 30])
    expect(state.camera_preset).toBe('orbite')
    expect(state.camera_position).toEqual([10, 20, 30])
  })
})

describe('couleurs', () => {
  it("retombe sur la couleur par défaut quand l'emplacement n'a pas été choisi", () => {
    expect(materialFor(null, '#cccccc')).toBe('#cccccc')
    expect(materialFor(undefined, '#cccccc')).toBe('#cccccc')
  })

  it('rejette une couleur mal formée plutôt que de la passer à Three.js', () => {
    expect(materialFor('rouge', '#cccccc')).toBe('#cccccc')
    expect(materialFor('#zzzzzz', '#cccccc')).toBe('#cccccc')
  })

  it('utilise la couleur choisie quand elle est valide', () => {
    expect(materialFor('#8b5a2b', '#cccccc')).toBe('#8b5a2b')
  })
})
