/**
 * Fond de plan et calibrage à deux clics (spec §10, amendement A5).
 *
 * Un artisan n'arrive jamais devant un canevas vide : il arrive avec le plan de l'architecte, un
 * relevé de géomètre ou la photo du plan affiché dans la cage d'escalier. Poser cette image sous
 * le dessin et lui donner son échelle est le premier geste du métier, et le premier frein à
 * l'adoption s'il manque.
 *
 * Le calibrage : on clique deux points dont on connaît la distance réelle (une cote écrite sur le
 * plan, une porte de 83 cm), on saisit cette distance, l'échelle s'en déduit. **Le premier point
 * cliqué ne bouge pas** — sans ce point fixe, corriger l'échelle fait sauter l'image hors de
 * l'écran et il faut la retrouver au pan.
 *
 * Tout est pur : ni `Image`, ni canevas, ni requête. Ce qui se teste ici, c'est l'arithmétique du
 * calibrage, celle qui décide si le logement saisi mesure 40 m² ou 400.
 */
import type { Point } from '@/editor/geometry'

export interface BackgroundPlacement {
  /** `null` : image posée, **pas encore calibrée**. Jamais « échelle 1 » (spec §10, A5). */
  scaleCmPerPx: number | null
  offsetXCm: number
  offsetYCm: number
  rotationDeg: number
  opacity: number
}

/**
 * Échelle d'affichage d'une image non calibrée.
 *
 * Il en faut bien une pour la dessiner. Elle est **provisoire** et l'interface doit le dire :
 * confondre cette valeur avec une mesure fait dessiner un logement faux sans aucun avertissement.
 */
export const UNCALIBRATED_SCALE_CM_PER_PX = 1

export function effectiveScale(placement: BackgroundPlacement): number {
  return placement.scaleCmPerPx ?? UNCALIBRATED_SCALE_CM_PER_PX
}

export function isCalibrated(placement: BackgroundPlacement): boolean {
  return placement.scaleCmPerPx !== null && placement.scaleCmPerPx > 0
}

/** Pixel de l'image → point du plan, en appliquant rotation puis échelle puis translation. */
export function imageToPlan(pixel: Point, placement: BackgroundPlacement): Point {
  const scale = effectiveScale(placement)
  const angle = (placement.rotationDeg * Math.PI) / 180
  const cosine = Math.cos(angle)
  const sine = Math.sin(angle)
  return {
    x: placement.offsetXCm + (pixel.x * cosine - pixel.y * sine) * scale,
    y: placement.offsetYCm + (pixel.x * sine + pixel.y * cosine) * scale,
  }
}

/** L'inverse exact de `imageToPlan` : sert à retrouver le pixel visé par un clic sur le plan. */
export function planToImage(point: Point, placement: BackgroundPlacement): Point {
  const scale = effectiveScale(placement)
  const angle = (-placement.rotationDeg * Math.PI) / 180
  const cosine = Math.cos(angle)
  const sine = Math.sin(angle)
  const dx = (point.x - placement.offsetXCm) / scale
  const dy = (point.y - placement.offsetYCm) / scale
  return { x: dx * cosine - dy * sine, y: dx * sine + dy * cosine }
}

/** Borne haute du serveur (`BackgroundScale`), répétée pour refuser avant l'aller-retour. */
export const MAX_SCALE_CM_PER_PX = 10_000

export class CalibrationError extends Error {}

/**
 * Déduit l'échelle du fond de plan de deux points et de leur distance réelle.
 *
 * `a` et `b` sont donnés dans le repère du **plan**, tel qu'affiché à l'instant du clic — c'est
 * ce que l'utilisateur désigne, et lui demander des pixels d'image n'aurait aucun sens. Le
 * facteur appliqué est le rapport entre la distance annoncée et la distance actuellement
 * affichée ; l'offset est corrigé pour que `a` reste immobile.
 */
export function calibrate(
  placement: BackgroundPlacement,
  a: Point,
  b: Point,
  realDistanceCm: number,
): BackgroundPlacement {
  if (!(realDistanceCm > 0)) {
    throw new CalibrationError('la distance mesurée doit être strictement positive')
  }
  const shown = Math.hypot(b.x - a.x, b.y - a.y)
  if (shown <= 0) {
    throw new CalibrationError(
      'les deux points de calibrage sont confondus : écartez-les sur une cote connue',
    )
  }

  const factor = realDistanceCm / shown
  const scale = effectiveScale(placement) * factor
  if (!(scale > 0) || !Number.isFinite(scale) || scale > MAX_SCALE_CM_PER_PX) {
    throw new CalibrationError(
      `échelle hors bornes (${scale.toPrecision(3)} cm/px) : le serveur refuse au-delà de ` +
        `${MAX_SCALE_CM_PER_PX} cm par pixel`,
    )
  }

  return {
    ...placement,
    scaleCmPerPx: scale,
    // Point fixe en `a` : l'image se dilate autour du premier clic au lieu de partir au loin.
    offsetXCm: a.x - (a.x - placement.offsetXCm) * factor,
    offsetYCm: a.y - (a.y - placement.offsetYCm) * factor,
  }
}

/**
 * Vrai si l'adresse est acceptable comme fond de plan.
 *
 * Même règle que `RoomBase._validate_background_url` côté serveur, et pour la même raison : la
 * valeur est relue dans un attribut d'image, donc c'est une entrée utilisateur au sens OWASP A03.
 * Le serveur reste l'autorité — ce contrôle-ci évite d'envoyer un `data:` de trois mégaoctets
 * pour se voir répondre 422, et surtout il empêche l'éditeur d'afficher lui-même un `javascript:`.
 */
export function isBackgroundUrlAllowed(url: string): boolean {
  if (url.length === 0 || url.length > 500) return false
  // Espaces et caractères de contrôle : c'est par eux qu'on maquille un schéma actif.
  if (/[\s\u0000-\u001f]/.test(url)) return false
  if (url.startsWith('//') || url.startsWith('/\\')) return false
  if (url.startsWith('/')) return true
  return url.startsWith('https://') && url.length > 'https://'.length
}

export interface CalibrationDraft {
  /** Points cliqués, dans le repère du plan. Deux au plus. */
  points: Point[]
  realDistanceCm: number | null
}

export const EMPTY_DRAFT: CalibrationDraft = { points: [], realDistanceCm: null }

/**
 * Ajoute un point au calibrage en cours.
 *
 * Au troisième clic on recommence plutôt que d'ignorer : un utilisateur qui reclique a
 * manifestement raté son premier point, et un outil qui ne réagit plus paraît cassé.
 */
export function addCalibrationPoint(draft: CalibrationDraft, point: Point): CalibrationDraft {
  const points = draft.points.length >= 2 ? [point] : [...draft.points, point]
  return { ...draft, points }
}

/** Distance actuellement affichée entre les deux points cliqués, ou `null` s'il en manque un. */
export function draftDistanceCm(draft: CalibrationDraft): number | null {
  const [a, b] = draft.points
  if (!a || !b) return null
  return Math.hypot(b.x - a.x, b.y - a.y)
}
