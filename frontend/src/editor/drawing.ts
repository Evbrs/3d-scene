/**
 * Calculs de tracé du plan 2D.
 *
 * Séparés du composant : ce sont des fonctions pures, donc testables sans canvas. Elles ne
 * dupliquent aucune géométrie 3D (qui reste côté serveur, spec §3.1) — elles produisent ce qu'il
 * faut pour *dessiner* un plan lisible : murs épais, symboles d'ouverture, cotes déportées.
 */

import type { Face, PlanElement } from '@/api/types'
import { type Point, type Viewport, planToScreen, segmentLength } from '@/editor/geometry'
import { freeFootprint } from '@/editor/placement'

export interface WallGeometry {
  face: Face
  from: Point
  to: Point
  /** Vecteur unitaire le long du mur. */
  direction: Point
  /** Normale unitaire pointant vers l'extérieur de la pièce. */
  outward: Point
  lengthCm: number
}

/** Aire signée : positive en sens trigonométrique. */
function signedArea(polygon: number[][]): number {
  let total = 0
  for (let index = 0; index < polygon.length; index += 1) {
    const [x1, y1] = polygon[index] as [number, number]
    const [x2, y2] = polygon[(index + 1) % polygon.length] as [number, number]
    total += x1 * y2 - x2 * y1
  }
  return total / 2
}

/**
 * Géométrie de chaque mur, dans l'ordre du polygone.
 *
 * La normale sortante suit la même convention que le backend (`app/geometry/vectors.py`) : elle
 * dépend du sens de parcours du contour, pas seulement de la direction du segment. Un plan
 * dessiné dans l'autre sens verrait sinon ses cotes et ses symboles basculer à l'intérieur.
 */
export function wallGeometries(polygon: number[][], faces: Face[]): WallGeometry[] {
  if (polygon.length < 3) return []

  const walls = faces.filter((face) => face.kind === 'wall')
  const counterClockwise = signedArea(polygon) >= 0

  return polygon.map((vertex, index) => {
    const next = polygon[(index + 1) % polygon.length] as [number, number]
    const from = { x: vertex[0] as number, y: vertex[1] as number }
    const to = { x: next[0], y: next[1] }
    const length = segmentLength(from, to) || 1
    const direction = { x: (to.x - from.x) / length, y: (to.y - from.y) / length }
    // Perpendiculaire droite du segment ; inversée si le contour est décrit dans l'autre sens.
    // Le `+ 0` élimine les `-0` : ils ne changent rien au dessin, mais rendent la sortie non
    // canonique et les comparaisons imprévisibles — le même piège que `atan2` côté backend.
    const raw = { x: direction.y + 0, y: -direction.x + 0 }
    const outward = counterClockwise ? raw : { x: -raw.x + 0, y: -raw.y + 0 }

    return {
      face: walls[index] as Face,
      from,
      to,
      direction,
      outward,
      lengthCm: segmentLength(from, to),
    }
  })
}

/**
 * Identité stable d'un mur pour un `v-for`.
 *
 * La face quand elle existe (contour déjà enregistré), sinon le rang du segment dans le contour.
 * Les coordonnées ne conviennent pas : dans un rectangle, deux murs partagent la même abscisse de
 * départ et deux autres la même longueur — soit deux paires de clés en double, donc un réemploi
 * de nœuds Konva entre des murs qui n'ont rien à voir.
 */
export function wallKey(faceId: number | undefined, index: number): string {
  return faceId === undefined ? `rang-${index}` : `face-${faceId}`
}

/** Point situé à `alongCm` le long du mur, décalé de `offsetCm` vers l'extérieur. */
export function pointOnWall(
  wall: WallGeometry,
  alongCm: number,
  offsetCm = 0,
): Point {
  return {
    x: wall.from.x + wall.direction.x * alongCm + wall.outward.x * offsetCm,
    y: wall.from.y + wall.direction.y * alongCm + wall.outward.y * offsetCm,
  }
}

/** Polygone du mur épais (rectangle centré sur l'axe), en coordonnées écran aplaties. */
export function wallOutline(
  wall: WallGeometry,
  thicknessCm: number,
  viewport: Viewport,
): number[] {
  const half = thicknessCm / 2
  const corners = [
    pointOnWall(wall, 0, half),
    pointOnWall(wall, wall.lengthCm, half),
    pointOnWall(wall, wall.lengthCm, -half),
    pointOnWall(wall, 0, -half),
  ]
  return corners.flatMap((corner) => {
    const screen = planToScreen(corner, viewport)
    return [screen.x, screen.y]
  })
}

export type OpeningKind = 'door_hinged' | 'door_sliding' | 'window'

export interface OpeningSymbol {
  element: PlanElement
  kind: OpeningKind
  /** Rectangle de la trémie : le trou percé dans le mur. */
  gap: number[]
  /** Traits du symbole (vitrage, vantail, rail…). */
  strokes: number[][]
  /** Arc de débattement d'une porte battante, en degrés. */
  arc: { x: number; y: number; radius: number; from: number; to: number } | null
  labelAt: Point
  /**
   * Milieu de la trémie, sur l'axe du mur.
   *
   * Distinct de `labelAt`, qui est déporté à l'extérieur pour rester lisible : encadrer une
   * ouverture au rectangle de sélection doit répondre à l'endroit où elle est percée, pas à
   * l'endroit où son étiquette a été poussée.
   */
  center: Point
}

const OPENING_KINDS = new Set<string>(['door_hinged', 'door_sliding', 'window'])

export function isOpening(element: PlanElement): boolean {
  return OPENING_KINDS.has(element.kind)
}

/**
 * Symbole architectural d'une ouverture, aux conventions du dessin de bâtiment.
 *
 * - **fenêtre** : trémie vide traversée d'un trait fin figurant le vitrage ;
 * - **porte battante** : vantail perpendiculaire au mur + arc de débattement ;
 * - **porte coulissante** : vantail décalé le long du mur, sans débattement.
 *
 * Sans ces symboles, un plan n'est qu'un contour : impossible de distinguer une porte d'une
 * fenêtre, ni de voir de quel côté la porte s'ouvre.
 */
export function openingSymbol(
  wall: WallGeometry,
  element: PlanElement,
  thicknessCm: number,
  viewport: Viewport,
): OpeningSymbol {
  const start = element.x_offset_cm
  const end = start + element.width_cm
  const half = thicknessCm / 2

  const toScreen = (point: Point): number[] => {
    const screen = planToScreen(point, viewport)
    return [screen.x, screen.y]
  }

  const gap = [
    pointOnWall(wall, start, half),
    pointOnWall(wall, end, half),
    pointOnWall(wall, end, -half),
    pointOnWall(wall, start, -half),
  ].flatMap(toScreen)

  const strokes: number[][] = []
  let arc: OpeningSymbol['arc'] = null

  if (element.kind === 'window') {
    // Vitrage : un trait dans l'axe du mur, plus les deux tableaux.
    strokes.push([...toScreen(pointOnWall(wall, start, 0)), ...toScreen(pointOnWall(wall, end, 0))])
    strokes.push([...toScreen(pointOnWall(wall, start, half)), ...toScreen(pointOnWall(wall, start, -half))])
    strokes.push([...toScreen(pointOnWall(wall, end, half)), ...toScreen(pointOnWall(wall, end, -half))])
  } else if (element.kind === 'door_hinged') {
    // Vantail ouvert à 90° vers l'intérieur, depuis le gond situé au début de la trémie.
    const hinge = pointOnWall(wall, start, 0)
    const leafTip = {
      x: hinge.x - wall.outward.x * element.width_cm,
      y: hinge.y - wall.outward.y * element.width_cm,
    }
    strokes.push([...toScreen(hinge), ...toScreen(leafTip)])

    const hingeScreen = planToScreen(hinge, viewport)
    // Konva compte les angles en degrés, sens horaire, depuis l'axe +x de l'écran.
    const toAngle = (vector: Point): number =>
      (Math.atan2(vector.y, vector.x) * 180) / Math.PI
    const alongAngle = toAngle(wall.direction)
    const inwardAngle = toAngle({ x: -wall.outward.x, y: -wall.outward.y })
    let sweep = inwardAngle - alongAngle
    while (sweep <= -180) sweep += 360
    while (sweep > 180) sweep -= 360

    arc = {
      x: hingeScreen.x,
      y: hingeScreen.y,
      radius: element.width_cm * viewport.scale,
      from: alongAngle,
      to: sweep,
    }
  } else {
    // Coulissante : le vantail glisse le long du mur, figuré en retrait.
    const offset = half * 1.6
    strokes.push([
      ...toScreen(pointOnWall(wall, start, -offset)),
      ...toScreen(pointOnWall(wall, end, -offset)),
    ])
    strokes.push([...toScreen(pointOnWall(wall, start, half)), ...toScreen(pointOnWall(wall, start, -half))])
    strokes.push([...toScreen(pointOnWall(wall, end, half)), ...toScreen(pointOnWall(wall, end, -half))])
  }

  return {
    element,
    kind: element.kind as OpeningKind,
    gap,
    strokes,
    arc,
    labelAt: pointOnWall(wall, (start + end) / 2, half + thicknessCm * 1.2),
    center: pointOnWall(wall, (start + end) / 2, 0),
  }
}

export interface FurnitureFootprint {
  element: PlanElement
  /** Rectangle au sol, en coordonnées écran aplaties. */
  outline: number[]
  center: Point
  label: string
}

/**
 * Emprise au sol d'un meuble posé sur un mur.
 *
 * Le meuble est adossé au mur et s'étend vers l'intérieur de sa profondeur : c'est exactement la
 * projection de ce que le scene graph place en 3D (`app/geometry/scene.py`), pour que le plan et
 * la vue 3D racontent la même chose.
 */
export function furnitureFootprint(
  wall: WallGeometry,
  element: PlanElement,
  thicknessCm: number,
  viewport: Viewport,
  label: string,
): FurnitureFootprint {
  const half = thicknessCm / 2
  const start = element.x_offset_cm
  const end = start + element.width_cm
  const depth = element.depth_cm

  const inner = (along: number, into: number): Point => ({
    x: wall.from.x + wall.direction.x * along - wall.outward.x * (half + into),
    y: wall.from.y + wall.direction.y * along - wall.outward.y * (half + into),
  })

  const corners = [inner(start, 0), inner(end, 0), inner(end, depth), inner(start, depth)]
  const outline = corners.flatMap((corner) => {
    const screen = planToScreen(corner, viewport)
    return [screen.x, screen.y]
  })

  return {
    element,
    outline,
    center: inner((start + end) / 2, depth / 2),
    label,
  }
}

/**
 * Emprise au sol d'un meuble **libre**, posé dans le repère de la pièce (spec §10, A4).
 *
 * Rien de commun avec `furnitureFootprint` : il n'y a pas de mur porteur, la position est le
 * centre de l'emprise et la rotation est libre autour de la verticale. Les quatre coins viennent
 * de `placement.footprintCorners`, qui applique **exactement** la convention du backend — le plan
 * et la 3D doivent montrer le même meuble au même endroit.
 */
export function freeFurnitureFootprint(
  element: PlanElement,
  viewport: Viewport,
  label: string,
): FurnitureFootprint | null {
  const footprint = freeFootprint(element)
  if (!footprint) return null

  return {
    element,
    outline: footprint.corners.flatMap((corner) => {
      const screen = planToScreen(corner, viewport)
      return [screen.x, screen.y]
    }),
    center: footprint.center,
    label,
  }
}

/** Cote déportée à l'extérieur du mur, avec ses lignes d'attache. */
export function dimensionLine(
  wall: WallGeometry,
  offsetCm: number,
  viewport: Viewport,
): { line: number[]; ticks: number[][]; labelAt: Point; text: string } {
  const from = pointOnWall(wall, 0, offsetCm)
  const to = pointOnWall(wall, wall.lengthCm, offsetCm)
  const toScreen = (point: Point): number[] => {
    const screen = planToScreen(point, viewport)
    return [screen.x, screen.y]
  }

  return {
    line: [...toScreen(from), ...toScreen(to)],
    ticks: [
      [...toScreen(pointOnWall(wall, 0, offsetCm * 0.25)), ...toScreen(from)],
      [...toScreen(pointOnWall(wall, wall.lengthCm, offsetCm * 0.25)), ...toScreen(to)],
    ],
    labelAt: pointOnWall(wall, wall.lengthCm / 2, offsetCm),
    text: `${Math.round(wall.lengthCm)}`,
  }
}

/** Lignes de la grille, en coordonnées écran, bornées à la zone visible. */
export function gridLines(
  widthPx: number,
  heightPx: number,
  viewport: Viewport,
  stepCm: number,
): number[][] {
  const lines: number[][] = []
  const stepPx = stepCm * viewport.scale
  if (stepPx < 6) return lines // en dessous, la grille devient un aplat illisible

  const firstX = viewport.offsetX % stepPx
  for (let x = firstX; x < widthPx; x += stepPx) lines.push([x, 0, x, heightPx])
  const firstY = viewport.offsetY % stepPx
  for (let y = firstY; y < heightPx; y += stepPx) lines.push([0, y, widthPx, y])

  return lines
}

/**
 * Grille à deux niveaux : le pas de saisie, et le mètre.
 *
 * Un seul niveau oblige à choisir entre « je vois où j'accroche » (pas fin, illisible dès qu'on
 * dézoome) et « je vois l'échelle » (pas large, magnétisme aveugle). Deux niveaux tranchent : le
 * trait fin disparaît quand il devient un aplat, le trait fort reste et donne le mètre — la seule
 * référence dont on a besoin pour juger d'un coup d'œil qu'une pièce fait bien 4 m de long.
 */
export const COARSE_GRID_CM = 100

export function twoLevelGrid(
  widthPx: number,
  heightPx: number,
  viewport: Viewport,
  stepCm: number,
): { fine: number[][]; coarse: number[][] } {
  const fine = gridLines(widthPx, heightPx, viewport, stepCm)
  // Inutile de superposer deux fois le même quadrillage quand le pas de saisie est déjà le mètre.
  const coarse =
    stepCm >= COARSE_GRID_CM
      ? []
      : gridLines(widthPx, heightPx, viewport, COARSE_GRID_CM)
  return { fine, coarse }
}
