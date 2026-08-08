import { describe, expect, it } from 'vitest'

import {
  TRANSPARENT_OPACITY,
  faceKey,
  faceLabelOf,
  fromViewState,
  horizontalCut,
  isolate,
  isVisible,
  materialFor,
  nextVisibility,
  opacityFor,
  showEverything,
  toggleSelection,
  toViewState,
  unscope,
  wallMidpoint,
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

  it("n'isole rien quand la sélection est vide", () => {
    // Décocher la dernière case ne doit pas rendre la pièce entière transparente : personne ne
    // demande jamais ça d'un clic.
    expect(isolate([], LABELS)).toEqual(showEverything(LABELS))
  })

  it('coche et décoche une face de la sélection', () => {
    expect(toggleSelection([], 'A')).toEqual(['A'])
    expect(toggleSelection(['A'], 'B')).toEqual(['A', 'B'])
    expect(toggleSelection(['A', 'B'], 'A')).toEqual(['B'])
  })
})

describe('clés de face en logement complet', () => {
  it('garde l’étiquette nue pour une seule pièce, ce que le partage sérialise', () => {
    expect(faceKey('A')).toBe('A')
    expect(faceLabelOf('A')).toBe('A')
  })

  it('préfixe par la pièce dès qu’on montre le logement', () => {
    // Deux pièces ont chacune une face « A » : sans préfixe, masquer l'une masque l'autre.
    expect(faceKey('A', 12)).toBe('12:A')
    expect(faceLabelOf(faceKey('A', 12))).toBe('A')
    expect(faceKey('A', 12)).not.toBe(faceKey('A', 13))
  })

  it('rend des étiquettes nues pour le partage, pièce par pièce', () => {
    // La page publique n'affiche qu'une pièce et relit des étiquettes sans préfixe : lui envoyer
    // « 12:A » la ferait tout masquer, la clé n'étant dans aucune de ses deux listes.
    const logement = {
      '12:A': 'visible' as const,
      '12:PLAFOND': 'hidden' as const,
      '13:A': 'transparent' as const,
    }

    expect(unscope(logement, 12)).toEqual({ A: 'visible', PLAFOND: 'hidden' })
    expect(unscope(logement, 99)).toEqual({})
  })
})

describe('coupe horizontale', () => {
  it('se débranche quand elle ne retire rien', () => {
    // Un plan de clipping actif coûte une variante de shader sur chaque matériau : couper au ras
    // du plafond ne doit pas la payer.
    expect(horizontalCut(250, 250)).toBeNull()
    expect(horizontalCut(300, 250)).toBeNull()
    expect(horizontalCut(0, 250)).toBeNull()
    expect(horizontalCut(Number.NaN, 250)).toBeNull()
  })

  it('retient la hauteur demandée quand elle coupe vraiment', () => {
    expect(horizontalCut(120, 250)).toBe(120)
  })
})

describe('milieu d’un mur', () => {
  it('suit la rotation qui amène l’axe +X sur la direction du mur', () => {
    const droit = wallMidpoint({
      key: 'A',
      origin: [0, 0, 0],
      rotationY: 0,
      lengthCm: 400,
      outwardNormal: [0, 0, -1],
    })
    expect(droit).toEqual([200, 0, 0])

    const tourne = wallMidpoint({
      key: 'B',
      origin: [400, 0, 0],
      rotationY: Math.PI / 2,
      lengthCm: 300,
      outwardNormal: [1, 0, 0],
    })
    expect(tourne[0]).toBeCloseTo(400, 6)
    expect(tourne[2]).toBeCloseTo(-150, 6)
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
