/**
 * Fond de plan et calibrage à deux clics.
 *
 * L'arithmétique testée ici décide si le logement saisi mesure 40 m² ou 400. Deux invariants :
 * l'échelle non calibrée n'est **jamais** confondue avec une mesure (spec §10, A5), et le premier
 * point cliqué ne bouge pas — sans point fixe, corriger l'échelle fait sauter l'image hors de
 * l'écran et il faut la retrouver au pan.
 */
import { describe, expect, it } from 'vitest'

import {
  type BackgroundPlacement,
  type CalibrationDraft,
  CalibrationError,
  EMPTY_DRAFT,
  MAX_SCALE_CM_PER_PX,
  UNCALIBRATED_SCALE_CM_PER_PX,
  addCalibrationPoint,
  calibrate,
  draftDistanceCm,
  effectiveScale,
  imageToPlan,
  isBackgroundUrlAllowed,
  isCalibrated,
  planToImage,
} from '@/editor/calibration'

function placement(overrides: Partial<BackgroundPlacement> = {}): BackgroundPlacement {
  return { scaleCmPerPx: null, offsetXCm: 0, offsetYCm: 0, rotationDeg: 0, opacity: 1, ...overrides }
}

describe('échelle', () => {
  it('distingue « pas encore calibrée » de « échelle 1 »', () => {
    expect(isCalibrated(placement())).toBe(false)
    expect(isCalibrated(placement({ scaleCmPerPx: UNCALIBRATED_SCALE_CM_PER_PX }))).toBe(true)
  })

  it('affiche quand même l’image avec une échelle provisoire', () => {
    expect(effectiveScale(placement())).toBe(UNCALIBRATED_SCALE_CM_PER_PX)
    expect(effectiveScale(placement({ scaleCmPerPx: 2.5 }))).toBe(2.5)
  })
})

describe('repère image / plan', () => {
  it('est réversible', () => {
    const pose = placement({ scaleCmPerPx: 1.7, offsetXCm: 40, offsetYCm: -30, rotationDeg: 23 })
    const pixel = { x: 137, y: 246 }

    const retour = planToImage(imageToPlan(pixel, pose), pose)

    expect(retour.x).toBeCloseTo(pixel.x, 9)
    expect(retour.y).toBeCloseTo(pixel.y, 9)
  })

  it('applique l’échelle puis la translation', () => {
    const pose = placement({ scaleCmPerPx: 2, offsetXCm: 100, offsetYCm: 50 })

    expect(imageToPlan({ x: 10, y: 20 }, pose)).toEqual({ x: 120, y: 90 })
  })

  it('tourne un plan photographié de travers', () => {
    const pose = placement({ scaleCmPerPx: 1, rotationDeg: 90 })
    const point = imageToPlan({ x: 100, y: 0 }, pose)

    expect(point.x).toBeCloseTo(0, 9)
    expect(point.y).toBeCloseTo(100, 9)
  })
})

describe('calibrage à deux clics', () => {
  it('déduit l’échelle du rapport entre la mesure réelle et la distance affichée', () => {
    // Deux points distants de 200 cm à l'écran, dont on sait qu'ils font 400 cm en vrai : tout
    // ce qui est dessiné mesure le double de ce qu'on croyait.
    const calibre = calibrate(placement(), { x: 0, y: 0 }, { x: 200, y: 0 }, 400)

    expect(calibre.scaleCmPerPx).toBeCloseTo(2, 9)
  })

  it('compose avec une échelle déjà posée', () => {
    const calibre = calibrate(
      placement({ scaleCmPerPx: 3 }),
      { x: 0, y: 0 },
      { x: 100, y: 0 },
      50,
    )

    expect(calibre.scaleCmPerPx).toBeCloseTo(1.5, 9)
  })

  it('laisse le premier point cliqué exactement où il est', () => {
    const pose = placement({ scaleCmPerPx: 1, offsetXCm: 0, offsetYCm: 0 })
    const a = { x: 300, y: 200 }
    const calibre = calibrate(pose, a, { x: 400, y: 200 }, 400)

    // Le point du plan qui était sous le premier clic doit y rester : c'est ce qui empêche
    // l'image de partir au loin dès qu'on corrige l'échelle.
    const pixelAvant = planToImage(a, pose)
    const apres = imageToPlan(pixelAvant, calibre)
    expect(apres.x).toBeCloseTo(a.x, 6)
    expect(apres.y).toBeCloseTo(a.y, 6)
  })

  it('conserve rotation et opacité', () => {
    const calibre = calibrate(
      placement({ rotationDeg: 12, opacity: 0.4 }),
      { x: 0, y: 0 },
      { x: 100, y: 0 },
      100,
    )

    expect(calibre.rotationDeg).toBe(12)
    expect(calibre.opacity).toBe(0.4)
  })

  it('refuse deux points confondus', () => {
    expect(() => calibrate(placement(), { x: 5, y: 5 }, { x: 5, y: 5 }, 100)).toThrow(
      CalibrationError,
    )
  })

  it('refuse une distance nulle ou négative', () => {
    expect(() => calibrate(placement(), { x: 0, y: 0 }, { x: 10, y: 0 }, 0)).toThrow(
      CalibrationError,
    )
    expect(() => calibrate(placement(), { x: 0, y: 0 }, { x: 10, y: 0 }, -5)).toThrow(
      CalibrationError,
    )
  })

  it('refuse avant l’aller-retour ce que le serveur refuserait', () => {
    // Deux points à 1 mm l'un de l'autre annoncés à 100 m : l'échelle exploserait au-delà de la
    // borne du serveur, et le message serait un 422 sans explication.
    expect(() =>
      calibrate(placement(), { x: 0, y: 0 }, { x: 0.1, y: 0 }, 10_000),
    ).toThrow(/hors bornes/)
    expect(MAX_SCALE_CM_PER_PX).toBe(10_000)
  })
})

describe('points de calibrage', () => {
  it('accumule deux points puis recommence au troisième clic', () => {
    let brouillon: CalibrationDraft = { ...EMPTY_DRAFT }
    brouillon = addCalibrationPoint(brouillon, { x: 0, y: 0 })
    brouillon = addCalibrationPoint(brouillon, { x: 100, y: 0 })

    expect(draftDistanceCm(brouillon)).toBe(100)

    brouillon = addCalibrationPoint(brouillon, { x: 50, y: 50 })

    // Un utilisateur qui reclique a manifestement raté son premier point : un outil qui ne réagit
    // plus paraît cassé.
    expect(brouillon.points).toEqual([{ x: 50, y: 50 }])
    expect(draftDistanceCm(brouillon)).toBeNull()
  })
})

describe('adresse du fond de plan', () => {
  it('accepte un chemin du site et une URL https', () => {
    expect(isBackgroundUrlAllowed('/media/plans/rdc.png')).toBe(true)
    expect(isBackgroundUrlAllowed('https://exemple.fr/plan.png')).toBe(true)
  })

  it.each([
    ['javascript:alert(1)'],
    ['data:image/png;base64,AAAA'],
    ['//attaquant.example/plan.png'],
    ['/\\attaquant.example/plan.png'],
    ['http://exemple.fr/plan.png'],
    ['https://'],
    [''],
    ['/media/mon plan.png'],
  ])('refuse %s', (url) => {
    // Même règle que le serveur (`RoomBase._validate_background_url`) : la valeur est relue dans
    // un attribut d'image, donc c'est une entrée utilisateur au sens OWASP A03.
    expect(isBackgroundUrlAllowed(url)).toBe(false)
  })

  it('refuse au-delà de la longueur de la colonne', () => {
    expect(isBackgroundUrlAllowed(`/media/${'a'.repeat(600)}.png`)).toBe(false)
  })
})
