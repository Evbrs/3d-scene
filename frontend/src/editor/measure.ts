/**
 * Cotation du plan : ce qu'on affiche en permanence et ce qu'on peut saisir au clavier.
 *
 * L'affichage permanent des cotes, de la surface et du périmètre n'est pas décoratif : c'est ce
 * que l'artisan recopie sur son devis. Le laisser derrière une sélection ou un survol obligerait
 * à cliquer chaque mur pour relever un logement.
 *
 * `resizeWall` est le pendant clavier du glisser de sommet — un mur se corrige au chiffre relevé
 * au laser, pas en visant un pixel.
 */
import { type Point, areaInSquareMeters, segmentLength, wallLabel } from '@/editor/geometry'

export interface WallMeasure {
  index: number
  label: string
  lengthCm: number
  from: Point
  to: Point
}

export function wallMeasures(polygon: number[][]): WallMeasure[] {
  if (polygon.length < 3) return []
  return polygon.map((vertex, index) => {
    const next = polygon[(index + 1) % polygon.length] as [number, number]
    const from = { x: vertex[0] as number, y: vertex[1] as number }
    const to = { x: next[0], y: next[1] }
    return { index, label: wallLabel(index), lengthCm: segmentLength(from, to), from, to }
  })
}

/** Périmètre du contour, en centimètres. Mesuré sur l'axe des murs, comme les cotes affichées. */
export function perimeterCm(polygon: number[][]): number {
  return wallMeasures(polygon).reduce((total, wall) => total + wall.lengthCm, 0)
}

export interface RoomMetrics {
  areaM2: number
  perimeterCm: number
  wallCount: number
}

export function roomMetrics(polygon: number[][]): RoomMetrics {
  const walls = wallMeasures(polygon)
  return {
    areaM2: areaInSquareMeters(polygon),
    perimeterCm: walls.reduce((total, wall) => total + wall.lengthCm, 0),
    wallCount: walls.length,
  }
}

/**
 * Donne au mur `index` la longueur demandée, en déplaçant son sommet d'arrivée.
 *
 * Le sommet d'arrivée et non les deux extrémités : corriger un mur relevé au laser part toujours
 * d'un coin qu'on considère juste. Les deux murs voisins suivent — c'est inévitable sur un
 * contour fermé, et c'est le comportement qu'attend quiconque a déjà étiré une cote sur un plan.
 *
 * Le contour rendu peut être invalide (auto-sécant, mur trop court) : ce n'est pas à cette
 * fonction d'en juger, `isSelfIntersecting` et le serveur le font déjà, chacun avec son message.
 */
export function resizeWall(polygon: number[][], index: number, lengthCm: number): number[][] {
  if (index < 0 || index >= polygon.length) {
    throw new Error(`mur ${index} inexistant sur un contour de ${polygon.length} sommets`)
  }
  if (!(lengthCm > 0)) {
    throw new Error('la longueur d’un mur doit être strictement positive')
  }

  const start = polygon[index] as [number, number]
  const endIndex = (index + 1) % polygon.length
  const end = polygon[endIndex] as [number, number]

  const dx = end[0] - start[0]
  const dy = end[1] - start[1]
  const current = Math.hypot(dx, dy)
  // Un mur de longueur nulle n'a pas de direction : l'allonger reviendrait à inventer un angle.
  if (current === 0) return polygon.map((vertex) => [...vertex])

  const moved: [number, number] = [
    start[0] + (dx / current) * lengthCm,
    start[1] + (dy / current) * lengthCm,
  ]
  return polygon.map((vertex, position) => (position === endIndex ? moved : [...vertex]))
}

/**
 * Longueur formatée pour l'affichage.
 *
 * Le centimètre entier est l'unité du métier : un mur ne se relève pas au millimètre au mètre
 * laser, et afficher 397,3 cm laisse croire à une précision qu'on n'a pas. Le mètre n'apparaît
 * qu'au-delà de 100 cm, en second rang, parce que c'est l'ordre de grandeur qu'on lit d'un coup.
 */
export function formatLengthCm(lengthCm: number): string {
  const rounded = Math.round(lengthCm)
  if (Math.abs(rounded) < 100) return `${rounded} cm`
  return `${rounded} cm (${(rounded / 100).toFixed(2).replace('.', ',')} m)`
}

export function formatAreaM2(areaM2: number): string {
  return `${areaM2.toFixed(2).replace('.', ',')} m²`
}
