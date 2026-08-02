/**
 * Aides géométriques de l'éditeur 2D.
 *
 * Volontairement séparées du composant Konva : ce sont des fonctions pures, donc testables sans
 * canvas ni rendu. Elles ne dupliquent aucun calcul du backend — la géométrie 3D reste
 * intégralement côté serveur (`docs/spec-complete.md` §3.1). Ici, uniquement ce qui sert à
 * *dessiner* et à *saisir* le plan.
 */

export interface Point {
  x: number
  y: number
}

export interface Viewport {
  /** Pixels par centimètre. */
  scale: number
  /** Décalage en pixels de l'origine du plan. */
  offsetX: number
  offsetY: number
}

export const DEFAULT_VIEWPORT: Viewport = { scale: 0.5, offsetX: 60, offsetY: 60 }

export function planToScreen(point: Point, viewport: Viewport): Point {
  return {
    x: point.x * viewport.scale + viewport.offsetX,
    y: point.y * viewport.scale + viewport.offsetY,
  }
}

export function screenToPlan(point: Point, viewport: Viewport): Point {
  return {
    x: (point.x - viewport.offsetX) / viewport.scale,
    y: (point.y - viewport.offsetY) / viewport.scale,
  }
}

/**
 * Aligne un point sur une grille.
 *
 * Sans magnétisme, un plan saisi à la souris produit des murs de 397,3 cm : les cotes deviennent
 * illisibles et deux murs censés être alignés ne le sont jamais tout à fait.
 */
export function snap(value: number, gridCm: number): number {
  if (gridCm <= 0) return value
  return Math.round(value / gridCm) * gridCm
}

export function snapPoint(point: Point, gridCm: number): Point {
  return { x: snap(point.x, gridCm), y: snap(point.y, gridCm) }
}

/** Longueur d'un segment, en cm. */
export function segmentLength(a: Point, b: Point): number {
  return Math.hypot(b.x - a.x, b.y - a.y)
}

export function midpoint(a: Point, b: Point): Point {
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }
}

/** Aire signée (formule du lacet). Positive en sens trigonométrique. */
export function signedArea(polygon: number[][]): number {
  if (polygon.length < 3) return 0
  let total = 0
  for (let index = 0; index < polygon.length; index += 1) {
    const [x1, y1] = polygon[index] as [number, number]
    const [x2, y2] = polygon[(index + 1) % polygon.length] as [number, number]
    total += x1 * y2 - x2 * y1
  }
  return total / 2
}

/** Aire en m², telle qu'affichée à l'utilisateur. */
export function areaInSquareMeters(polygon: number[][]): number {
  return Math.abs(signedArea(polygon)) / 10_000
}

/**
 * Étiquette d'un mur : A, B, … Z, AA, AB…
 *
 * Doit rester identique à `wall_label` du backend (`app/services/faces.py`) : c'est la même
 * règle métier, affichée ici avant l'aller-retour serveur pour que l'utilisateur voie
 * immédiatement le lettrage de son tracé.
 */
export function wallLabel(index: number): string {
  if (index < 0) throw new Error("l'index d'un mur ne peut pas être négatif")
  let label = ''
  let remaining = index + 1
  while (remaining > 0) {
    const remainder = (remaining - 1) % 26
    label = String.fromCharCode(65 + remainder) + label
    remaining = Math.floor((remaining - 1) / 26)
  }
  return label
}

/** Segments (mur) d'un polygone fermé, dans l'ordre du lettrage. */
export function wallSegments(polygon: number[][]): { from: Point; to: Point; label: string }[] {
  if (polygon.length < 3) return []
  return polygon.map((vertex, index) => {
    const next = polygon[(index + 1) % polygon.length] as [number, number]
    return {
      from: { x: vertex[0] as number, y: vertex[1] as number },
      to: { x: next[0], y: next[1] },
      label: wallLabel(index),
    }
  })
}

/**
 * Vrai si le polygone se recoupe lui-même.
 *
 * Un contour croisé n'a pas d'intérieur défini : l'extrusion 3D produirait des murs retournés et
 * une aire au sol absurde. Mieux vaut le signaler pendant la saisie que le découvrir en 3D.
 */
export function isSelfIntersecting(polygon: number[][]): boolean {
  const count = polygon.length
  if (count < 4) return false

  for (let i = 0; i < count; i += 1) {
    for (let j = i + 1; j < count; j += 1) {
      // Deux côtés adjacents partagent un sommet : leur intersection est normale.
      if (j === i || (j + 1) % count === i || (i + 1) % count === j) continue
      const a = polygon[i] as [number, number]
      const b = polygon[(i + 1) % count] as [number, number]
      const c = polygon[j] as [number, number]
      const d = polygon[(j + 1) % count] as [number, number]
      if (segmentsIntersect(a, b, c, d)) return true
    }
  }
  return false
}

function orientation(p: number[], q: number[], r: number[]): number {
  const value =
    ((q[1] as number) - (p[1] as number)) * ((r[0] as number) - (q[0] as number)) -
    ((q[0] as number) - (p[0] as number)) * ((r[1] as number) - (q[1] as number))
  if (Math.abs(value) < 1e-9) return 0
  return value > 0 ? 1 : 2
}

function segmentsIntersect(p1: number[], q1: number[], p2: number[], q2: number[]): boolean {
  const o1 = orientation(p1, q1, p2)
  const o2 = orientation(p1, q1, q2)
  const o3 = orientation(p2, q2, p1)
  const o4 = orientation(p2, q2, q1)
  return o1 !== o2 && o3 !== o4
}

/** Bornes du plan, en cm, pour cadrer la vue. */
export function boundingBox(polygon: number[][]): {
  minX: number
  minY: number
  maxX: number
  maxY: number
} {
  if (polygon.length === 0) return { minX: 0, minY: 0, maxX: 0, maxY: 0 }
  const xs = polygon.map((point) => point[0] as number)
  const ys = polygon.map((point) => point[1] as number)
  return {
    minX: Math.min(...xs),
    minY: Math.min(...ys),
    maxX: Math.max(...xs),
    maxY: Math.max(...ys),
  }
}

/** Cadre le polygone dans une zone donnée, avec une marge. */
export function fitViewport(
  polygon: number[][],
  widthPx: number,
  heightPx: number,
  paddingPx = 40,
): Viewport {
  const { minX, minY, maxX, maxY } = boundingBox(polygon)
  const planWidth = Math.max(maxX - minX, 1)
  const planHeight = Math.max(maxY - minY, 1)

  const scale = Math.min(
    (widthPx - 2 * paddingPx) / planWidth,
    (heightPx - 2 * paddingPx) / planHeight,
  )
  const usable = Number.isFinite(scale) && scale > 0 ? scale : DEFAULT_VIEWPORT.scale

  return {
    scale: usable,
    offsetX: paddingPx - minX * usable,
    offsetY: paddingPx - minY * usable,
  }
}
