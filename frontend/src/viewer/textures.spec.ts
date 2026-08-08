import { ExtrudeGeometry, ShapeGeometry } from 'three'
import { describe, expect, it } from 'vitest'

import type { Covering } from '@/api/types'
import { buildShape } from '@/viewer/geometry'
import {
  JOINT_CM,
  MAX_CANVAS_PX,
  MIN_CANVAS_PX,
  buildCoveringTexture,
  canvasPixels,
  coveringPattern,
  coveringTextureKey,
  jointColor,
  patternCell,
  textureRepeat,
  tileCount,
} from '@/viewer/textures'

const OUTLINE: number[][] = [
  [0, 0],
  [400, 0],
  [400, 250],
  [0, 250],
]

describe('les UV du backend sont en centimètres', () => {
  // C'est l'hypothèse qui porte tout le dimensionnement des textures : si elle tombe, le
  // `repeat` déduit de la taille d'unité ne veut plus rien dire.
  it('donne à la face avant d’un mur extrudé des UV égaux à ses cotes', () => {
    const geometry = new ExtrudeGeometry(buildShape(OUTLINE, []), {
      depth: 15,
      bevelEnabled: false,
    })
    const uv = geometry.getAttribute('uv')!
    let maxU = 0
    let maxV = 0
    for (let index = 0; index < uv.count; index += 1) {
      maxU = Math.max(maxU, uv.getX(index))
      maxV = Math.max(maxV, uv.getY(index))
    }

    // Un mur de 400 x 250 cm : les UV vont jusqu'à 400 et 250, pas jusqu'à 1.
    expect(maxU).toBeCloseTo(400, 3)
    expect(maxV).toBeCloseTo(250, 3)
  })

  it('en fait autant pour un sol', () => {
    const uv = new ShapeGeometry(buildShape(OUTLINE, [])).getAttribute('uv')!
    let maxU = 0
    for (let index = 0; index < uv.count; index += 1) maxU = Math.max(maxU, uv.getX(index))
    expect(maxU).toBeCloseTo(400, 3)
  })
})

describe('cellules de calepinage', () => {
  it('pose droite : une unité, une cellule à ses dimensions', () => {
    const cell = patternCell('straight', 30, 60)
    expect([cell.widthCm, cell.heightCm]).toEqual([30, 60])
    expect(cell.tiles).toHaveLength(1)
  })

  it('pose décalée : deux rangées, la seconde à demi-unité', () => {
    const cell = patternCell('staggered', 30, 60)
    // Une seule rangée dans la cellule perdrait le décalage à la répétition.
    expect(cell.heightCm).toBe(120)
    expect(cell.tiles.map((unit) => unit.xCm)).toEqual([15, 0, 30])
  })

  it('incline les lames des poses obliques', () => {
    expect(patternCell('chevron', 60, 10).tiles.map((unit) => unit.angleRad)).toEqual([
      Math.PI / 4,
      -Math.PI / 4,
    ])
    // Bâtons rompus : quatre lames, deux orientations, cellule carrée.
    const herringbone = patternCell('herringbone', 60, 10)
    expect(herringbone.widthCm).toBe(herringbone.heightCm)
    expect(herringbone.tiles).toHaveLength(4)
  })
})

describe('dimensionnement par la face réelle', () => {
  it('déduit la répétition de la cellule, pas d’une constante', () => {
    expect(textureRepeat(patternCell('straight', 30, 60))).toEqual([1 / 30, 1 / 60])
    // Deux rangées dans la cellule : la répétition verticale est deux fois plus lente.
    expect(textureRepeat(patternCell('staggered', 30, 60))[1]).toBe(1 / 120)
  })

  it('donne le nombre d’unités visibles sur une face donnée', () => {
    // Un mur de 400 cm carrelé en 30 : 13,3 carreaux, dont un coupé — ce que le métré du backend
    // compte de son côté.
    expect(tileCount(400, 30)).toBeCloseTo(13.333, 3)
    expect(tileCount(400, 0)).toBe(0)
  })

  it('reste raisonnable en résolution', () => {
    const petite = canvasPixels(patternCell('straight', 2, 2))
    expect(Math.min(petite.width, petite.height)).toBeGreaterThanOrEqual(MIN_CANVAS_PX - 1)

    const grande = canvasPixels(patternCell('herringbone', 200, 200))
    expect(Math.max(grande.width, grande.height)).toBeLessThanOrEqual(MAX_CANVAS_PX)
  })
})

describe('couleur du joint', () => {
  it('assombrit un revêtement clair et éclaircit un revêtement foncé', () => {
    // Un joint noir sur un carrelage noir ne donne aucune échelle : c'est le joint qui fait lire
    // les dimensions d'unité.
    expect(jointColor('#ffffff')).toBe('#cbcbcb')
    expect(jointColor('#101010')).toBe('#3e3e3e')
  })

  it('retombe sur un gris quand la couleur est mal formée', () => {
    expect(jointColor('rouge')).toBe('#9a9a9a')
  })

  it('laisse assez de place au joint dans l’unité', () => {
    expect(JOINT_CM).toBeGreaterThan(0)
    expect(JOINT_CM).toBeLessThan(1)
  })
})

describe('lecture du revêtement', () => {
  const carrelage: Covering = {
    color: '#e8e4dc',
    material: 'carrelage',
    unit_width_cm: 30,
    unit_height_cm: 60,
    pattern: 'staggered',
  }

  it('traite des dimensions d’unité sans motif comme une pose droite', () => {
    expect(coveringPattern({ unit_width_cm: 30, unit_height_cm: 30 })?.pattern).toBe('straight')
  })

  it('ne dessine rien sans dimensions d’unité', () => {
    // Le motif seul ne suffit pas : sans taille d'unité, on ne sait pas à quelle échelle poser.
    expect(coveringPattern({ pattern: 'herringbone' })).toBeNull()
    expect(coveringPattern(null)).toBeNull()
    expect(coveringPattern({ unit_width_cm: 0, unit_height_cm: 30 })).toBeNull()
  })

  it('distingue deux revêtements et mutualise deux identiques', () => {
    expect(coveringTextureKey(carrelage, '#e8e4dc')).toBe(
      coveringTextureKey({ ...carrelage, material: 'grès' }, '#e8e4dc'),
    )
    expect(coveringTextureKey(carrelage, '#e8e4dc')).not.toBe(
      coveringTextureKey(carrelage, '#ffffff'),
    )
    expect(coveringTextureKey(carrelage, '#e8e4dc')).not.toBe(
      coveringTextureKey({ ...carrelage, unit_width_cm: 45 }, '#e8e4dc'),
    )
    expect(coveringTextureKey({ color: '#ffffff' }, '#ffffff')).toBeNull()
  })

  it('rend l’aplat plutôt que de tomber quand le canevas ne sait pas peindre', () => {
    // `happy-dom` ne fournit pas de contexte 2D ; un navigateur sans accélération non plus. Le
    // viewer doit s'en accommoder, pas s'arrêter.
    expect(buildCoveringTexture(carrelage, '#e8e4dc')).toBeNull()
    expect(buildCoveringTexture(carrelage, '#e8e4dc', undefined)).toBeNull()
  })

  it('sait dessiner quand un contexte 2D existe', () => {
    const calls: string[] = []
    const context = {
      save: () => calls.push('save'),
      restore: () => calls.push('restore'),
      scale: () => calls.push('scale'),
      translate: () => calls.push('translate'),
      rotate: () => calls.push('rotate'),
      fillRect: () => calls.push('fillRect'),
      fillStyle: '',
    }
    const host = {
      createElement: () => ({ width: 0, height: 0, getContext: () => context }),
    } as unknown as Document

    const texture = buildCoveringTexture(carrelage, '#e8e4dc', host)

    expect(texture).not.toBeNull()
    // Fond de joint, puis trois unités reportées sur les neuf cellules voisines.
    expect(calls.filter((call) => call === 'fillRect')).toHaveLength(1 + 3 * 9)
    expect(texture!.repeat.x).toBe(1 / 30)
    expect(texture!.repeat.y).toBe(1 / 120)
  })
})
