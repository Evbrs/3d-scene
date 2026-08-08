/**
 * Où atterrit un meuble qu'on lâche sur le plan.
 *
 * C'est le cœur du glisser-déposer, et c'est aussi la décision la plus lourde de conséquences de
 * l'éditeur : lâché près d'un mur, le meuble s'**ancre à la face** et son décalage suivra le mur
 * quoi qu'il arrive ; lâché au milieu, il devient **libre** et s'ancre à la pièce (spec §10, A4).
 * Les deux repères n'ont pas la même signification et ne se convertissent pas après coup — le
 * choix fait ici est celui qu'on ne pourra plus changer sans supprimer puis recréer.
 *
 * L'emprise est calculée avec **exactement** la convention du backend
 * (`services/faces.py::free_element_footprint`) : `R_y(a)` envoie la largeur sur `(cos a, -sin a)`
 * et la profondeur sur `(sin a, cos a)`. S'en écarter dessinerait dans l'éditeur un meuble que la
 * 3D placerait ailleurs, ou ferait refuser par le serveur un meuble que l'éditeur montre dedans.
 */
import type { PlanElement } from '@/api/types'
import type { WallGeometry } from '@/editor/drawing'
import type { Point } from '@/editor/geometry'

/** Tolérance d'appartenance au contour, en cm : un meuble poussé contre le mur reste dedans. */
export const FIT_TOLERANCE_CM = 0.01

export interface Footprint {
  corners: Point[]
  center: Point
}

/** Les quatre coins de l'emprise au sol, après rotation, dans le repère du plan. */
export function footprintCorners(
  center: Point,
  widthCm: number,
  depthCm: number,
  rotationDeg: number,
): Point[] {
  const angle = (rotationDeg * Math.PI) / 180
  const cosine = Math.cos(angle)
  const sine = Math.sin(angle)
  const widthAxis = { x: cosine, y: -sine }
  const depthAxis = { x: sine, y: cosine }
  const halfWidth = widthCm / 2
  const halfDepth = depthCm / 2

  return (
    [
      [-1, -1],
      [1, -1],
      [1, 1],
      [-1, 1],
    ] as const
  ).map(([along, across]) => ({
    x: center.x + along * halfWidth * widthAxis.x + across * halfDepth * depthAxis.x,
    y: center.y + along * halfWidth * widthAxis.y + across * halfDepth * depthAxis.y,
  }))
}

/** Emprise d'un meuble libre déjà enregistré. Rend `null` si l'élément est adossé à une face. */
export function freeFootprint(element: PlanElement): Footprint | null {
  if (element.pos_x_cm === null || element.pos_y_cm === null) return null
  const center = { x: element.pos_x_cm, y: element.pos_y_cm }
  return {
    center,
    corners: footprintCorners(center, element.width_cm, element.depth_cm, element.rotation_deg),
  }
}

/**
 * Appartenance au contour, par parité des croisements d'un rayon horizontal.
 *
 * Même algorithme que `point_in_polygon` côté backend, et pour la même raison : une boîte
 * englobante mentirait sur une pièce en L, dont le renfoncement est hors de la pièce tout en
 * étant dans la boîte. Le verdict sur le bord lui-même n'est pas fiable — le rayon peut passer
 * par un sommet — d'où le traitement séparé de la distance au contour.
 */
export function pointInPolygon(polygon: number[][], point: Point): boolean {
  let inside = false
  for (let index = 0, previous = polygon.length - 1; index < polygon.length; previous = index++) {
    const [xi, yi] = polygon[index] as [number, number]
    const [xj, yj] = polygon[previous] as [number, number]
    const straddles = yi > point.y !== yj > point.y
    if (!straddles) continue
    const crossing = ((xj - xi) * (point.y - yi)) / (yj - yi) + xi
    if (point.x < crossing) inside = !inside
  }
  return inside
}

export interface Projection {
  /** Distance du point au segment, en cm. */
  distance: number
  /** Abscisse curviligne du projeté, bornée au segment. */
  alongCm: number
  point: Point
}

export function projectOnSegment(point: Point, from: Point, to: Point): Projection {
  const dx = to.x - from.x
  const dy = to.y - from.y
  const squared = dx * dx + dy * dy
  if (squared === 0) {
    return { distance: Math.hypot(point.x - from.x, point.y - from.y), alongCm: 0, point: from }
  }
  const t = Math.min(Math.max(((point.x - from.x) * dx + (point.y - from.y) * dy) / squared, 0), 1)
  const projected = { x: from.x + t * dx, y: from.y + t * dy }
  return {
    distance: Math.hypot(point.x - projected.x, point.y - projected.y),
    alongCm: t * Math.sqrt(squared),
    point: projected,
  }
}

/** Distance d'un point au contour (à son bord, pas à son intérieur). */
export function distanceToPolygon(polygon: number[][], point: Point): number {
  let best = Number.POSITIVE_INFINITY
  for (let index = 0; index < polygon.length; index += 1) {
    const current = polygon[index] as [number, number]
    const next = polygon[(index + 1) % polygon.length] as [number, number]
    const projection = projectOnSegment(
      point,
      { x: current[0], y: current[1] },
      { x: next[0], y: next[1] },
    )
    best = Math.min(best, projection.distance)
  }
  return best
}

/**
 * Vrai si les deux segments se croisent **franchement**.
 *
 * Franchement : chaque segment a ses deux extrémités strictement de part et d'autre de la droite
 * portant l'autre. Deux segments colinéaires qui se recouvrent, ou une extrémité posée sur
 * l'autre segment, ne comptent donc pas — c'est la même règle que `_segments_cross` côté backend,
 * et c'est elle qui laisse passer un meuble poussé pile contre le mur. Un test d'intersection
 * ordinaire (« les orientations diffèrent ») refuse ce geste, qui est le plus courant du métier.
 */
function segmentsCross(a: Point, b: Point, c: Point, d: Point): boolean {
  const side = (p: Point, q: Point, r: Point): number => {
    const value = (q.y - p.y) * (r.x - q.x) - (q.x - p.x) * (r.y - q.y)
    if (Math.abs(value) < 1e-9) return 0
    return value > 0 ? 1 : -1
  }
  return side(a, b, c) * side(a, b, d) < 0 && side(c, d, a) * side(c, d, b) < 0
}

/**
 * Message d'erreur si l'emprise ne tient pas dans le contour, `null` si elle tient.
 *
 * Miroir client de `element_fits_in_room`. Le doubler ici n'est pas de la redondance : c'est la
 * différence entre un refus **pendant** le geste, à l'endroit où l'utilisateur regarde, et un 422
 * qui arrive après le lâcher, une fois le meuble déjà dessiné à un endroit qu'il ne gardera pas.
 * Le serveur reste l'autorité — ce contrôle-ci ne remplace rien, il anticipe.
 */
export function footprintFits(polygon: number[][], corners: Point[]): string | null {
  if (polygon.length < 3) return null

  let worst: { corner: Point; gap: number } | null = null
  for (const corner of corners) {
    if (pointInPolygon(polygon, corner)) continue
    const gap = distanceToPolygon(polygon, corner)
    if (gap <= FIT_TOLERANCE_CM) continue
    if (worst === null || gap > worst.gap) worst = { corner, gap }
  }
  if (worst) {
    return (
      `le meuble sort de la pièce : son coin (${Math.round(worst.corner.x)}, ` +
      `${Math.round(worst.corner.y)}) est à ${Math.round(worst.gap)} cm à l'extérieur`
    )
  }

  // Les quatre coins dedans ne suffisent pas sur une pièce en L : l'emprise peut enjamber un
  // renfoncement sans qu'aucun coin n'en sorte.
  for (let index = 0; index < corners.length; index += 1) {
    const a = corners[index] as Point
    const b = corners[(index + 1) % corners.length] as Point
    for (let edge = 0; edge < polygon.length; edge += 1) {
      const current = polygon[edge] as [number, number]
      const next = polygon[(edge + 1) % polygon.length] as [number, number]
      if (segmentsCross(a, b, { x: current[0], y: current[1] }, { x: next[0], y: next[1] })) {
        return 'le meuble traverse le contour de la pièce'
      }
    }
  }
  return null
}

export type DropTarget =
  | {
      kind: 'face'
      faceId: number
      label: string
      xOffsetCm: number
      /** Ce qui sera montré pendant le survol : « adossé au mur B, à 120 cm du coin ». */
      libelle: string
    }
  | { kind: 'room'; roomId: number; posXCm: number; posYCm: number; libelle: string }
  | { kind: 'refuse'; raison: string }

export interface DropContext {
  roomId: number
  polygon: number[][]
  walls: WallGeometry[]
  wallThicknessCm: number
  widthCm: number
  depthCm: number
  rotationDeg?: number
  /** Distance à la face intérieure du mur en deçà de laquelle on s'adosse, en cm. */
  wallSnapCm?: number
  /** Une ouverture n'a de sens que dans un mur (spec §3.1) : elle ne peut pas devenir libre. */
  needsWall?: boolean
}

/** Rayon d'adossement par défaut : la profondeur d'un meuble bas, l'ordre de grandeur du geste. */
export const WALL_SNAP_CM = 45

/**
 * Décide de l'ancrage d'un meuble lâché en `point`.
 *
 * L'adossement gagne sur la pose libre quand les deux sont possibles : un meuble lâché contre un
 * mur doit y rester collé si le contour bouge, et c'est précisément ce que l'ancrage à la face
 * garantit. Le seuil se mesure depuis la **face intérieure** du mur, pas depuis son axe : c'est
 * la surface que l'utilisateur voit, et un mur de 30 cm ne doit pas rendre le geste deux fois
 * moins précis qu'un mur de 10.
 */
export function resolveDrop(point: Point, context: DropContext): DropTarget {
  const {
    roomId,
    polygon,
    walls,
    wallThicknessCm,
    widthCm,
    depthCm,
    rotationDeg = 0,
    wallSnapCm = WALL_SNAP_CM,
    needsWall = false,
  } = context

  // Dans un couloir de 90 cm, un seuil fixe de 45 cm couvre *toute* la surface : plus aucune
  // dépose libre n'y serait possible, et le mobilier d'un couloir se retrouverait collé aux murs
  // sans qu'on comprenne pourquoi. Le seuil ne prend donc jamais plus du tiers de la plus petite
  // dimension de la pièce.
  const boundedSnap = Math.min(wallSnapCm, shortestExtent(polygon) / 3)

  let best: { wall: WallGeometry; projection: Projection } | null = null
  for (const wall of walls) {
    if (!wall.face) continue
    const projection = projectOnSegment(point, wall.from, wall.to)
    // Distance à la face intérieure, jamais négative : un point posé dans l'épaisseur du mur est
    // au contact, pas « avant » lui.
    const fromInnerFace = Math.max(projection.distance - wallThicknessCm / 2, 0)
    if (fromInnerFace > boundedSnap) continue
    if (best === null || projection.distance < best.projection.distance) {
      best = { wall, projection }
    }
  }

  if (best) {
    const length = best.wall.lengthCm
    const maximum = Math.max(length - widthCm, 0)
    const xOffsetCm = Math.round(Math.min(Math.max(best.projection.alongCm - widthCm / 2, 0), maximum))
    return {
      kind: 'face',
      faceId: best.wall.face.id,
      label: best.wall.face.label,
      xOffsetCm,
      libelle: `adossé au mur ${best.wall.face.label}, à ${xOffsetCm} cm du coin`,
    }
  }

  if (needsWall) {
    return {
      kind: 'refuse',
      raison: 'une ouverture doit être posée sur un mur : rapprochez-la du contour',
    }
  }
  if (polygon.length >= 3 && !pointInPolygon(polygon, point)) {
    return { kind: 'refuse', raison: 'hors de la pièce' }
  }

  const center = { x: Math.round(point.x), y: Math.round(point.y) }
  const problem = footprintFits(polygon, footprintCorners(center, widthCm, depthCm, rotationDeg))
  if (problem) return { kind: 'refuse', raison: problem }

  return {
    kind: 'room',
    roomId,
    posXCm: center.x,
    posYCm: center.y,
    libelle: `posé au sol en (${center.x}, ${center.y})`,
  }
}

/**
 * Ramène un centre de meuble à l'intérieur du contour, s'il en est possible.
 *
 * Utilisé par le déplacement au clavier et par le collage : refuser sèchement une flèche du
 * clavier parce que le meuble touche le mur serait incompréhensible ; le laisser buter contre le
 * contour est le comportement attendu. Rend `null` quand aucun recentrage ne convient — c'est
 * alors un vrai refus, pas un buttage.
 */
export function clampToRoom(
  center: Point,
  polygon: number[][],
  widthCm: number,
  depthCm: number,
  rotationDeg: number,
): Point | null {
  if (footprintFits(polygon, footprintCorners(center, widthCm, depthCm, rotationDeg)) === null) {
    return center
  }
  // Recherche par dichotomie sur le segment centre-barycentre : le barycentre d'un contour simple
  // n'est pas toujours dedans (pièce en L), on ne le retient donc que s'il l'est.
  const anchor = centroid(polygon)
  if (!pointInPolygon(polygon, anchor)) return null

  let low = 0
  let high = 1
  for (let step = 0; step < 24; step += 1) {
    const middle = (low + high) / 2
    const candidate = {
      x: center.x + (anchor.x - center.x) * middle,
      y: center.y + (anchor.y - center.y) * middle,
    }
    if (footprintFits(polygon, footprintCorners(candidate, widthCm, depthCm, rotationDeg)) === null) {
      high = middle
    } else {
      low = middle
    }
  }
  const resolved = {
    x: Math.round(center.x + (anchor.x - center.x) * high),
    y: Math.round(center.y + (anchor.y - center.y) * high),
  }
  return footprintFits(polygon, footprintCorners(resolved, widthCm, depthCm, rotationDeg)) === null
    ? resolved
    : null
}

/** Plus petite dimension de la boîte englobante, en cm. Infinie sur un contour non tracé. */
export function shortestExtent(polygon: number[][]): number {
  if (polygon.length < 3) return Number.POSITIVE_INFINITY
  const xs = polygon.map((vertex) => vertex[0] as number)
  const ys = polygon.map((vertex) => vertex[1] as number)
  return Math.min(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys))
}

export function centroid(polygon: number[][]): Point {
  if (polygon.length === 0) return { x: 0, y: 0 }
  const sum = polygon.reduce(
    (total, vertex) => ({ x: total.x + (vertex[0] as number), y: total.y + (vertex[1] as number) }),
    { x: 0, y: 0 },
  )
  return { x: sum.x / polygon.length, y: sum.y / polygon.length }
}
