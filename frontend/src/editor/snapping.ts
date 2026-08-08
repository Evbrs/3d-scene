/**
 * Magnétisme et guides du tracé.
 *
 * Un plan de rénovation ne se saisit pas à la souris : il se saisit depuis un relevé au mètre
 * laser. Le magnétisme n'est donc pas un confort, c'est ce qui empêche un mur de 397,3 cm et deux
 * murs censés être alignés de ne l'être jamais tout à fait. Trois accroches, dans cet ordre de
 * priorité :
 *
 * 1. **un sommet existant** — refermer un contour ou repartir d'un angle connu est le geste le
 *    plus fréquent, et le plus coûteux à rater : un sommet à 2 cm du précédent crée un mur
 *    parasite que le backend refuse ;
 * 2. **le prolongement d'un sommet** — l'alignement vertical ou horizontal sur un sommet déjà
 *    posé, avec son guide visible. C'est ce qui rend un logement d'équerre saisissable sans
 *    calcul ;
 * 3. **la grille**, en dernier recours.
 *
 * La contrainte angulaire (Maj) court-circuite les deux premières : quand on la demande, on veut
 * une direction, pas un point.
 *
 * Fonctions pures, testables sans canvas — le composant n'y ajoute que la tolérance, qu'il
 * exprime en pixels d'écran pour qu'elle reste constante à l'œil quel que soit le zoom.
 */
import { type Point, snapPoint } from '@/editor/geometry'

/** Un trait d'aide affiché pendant le geste. Les extrémités sont en centimètres du plan. */
export interface Guide {
  from: Point
  to: Point
  /** `alignement` : prolongement d'un sommet. `angle` : rayon de la contrainte à 45°. */
  kind: 'alignement' | 'angle'
}

export interface SnapResult {
  point: Point
  guides: Guide[]
  /** Ce qui a décidé du point, pour l'annoncer — la cible d'une accroche doit être dite. */
  kind: 'sommet' | 'alignement' | 'angle' | 'grille'
  /** Message court destiné à `aria-live` et à l'étiquette du curseur. */
  libelle: string
}

export interface SnapOptions {
  /** Sommets candidats : ceux du contour en cours et ceux des pièces déjà posées. */
  vertices: Point[]
  gridCm: number
  /** Rayon d'accroche, en centimètres du plan (le composant le déduit du zoom). */
  toleranceCm: number
  /** Dernier sommet posé : origine de la contrainte angulaire. */
  origin?: Point | null
  /** Maj enfoncée. */
  constrainAngle?: boolean
  /** Longueur d'un guide d'alignement, pour le dessiner au-delà du point accroché. */
  guideLengthCm?: number
}

/** Multiples d'angle proposés par la contrainte : l'équerre et ses diagonales. */
export const ANGLE_STEP_DEG = 45

/**
 * Projette `target` sur le rayon issu de `origin` dont l'angle est le multiple de `stepDeg` le
 * plus proche.
 *
 * La distance conservée est celle **projetée** sur le rayon, pas la distance d'origine : garder
 * la distance brute ferait glisser le point vers l'avant dès qu'on s'écarte de l'axe, et le mur
 * s'allongerait tout seul pendant qu'on cherche son orientation.
 */
export function constrainToAngle(origin: Point, target: Point, stepDeg = ANGLE_STEP_DEG): Point {
  const dx = target.x - origin.x
  const dy = target.y - origin.y
  if (dx === 0 && dy === 0) return { ...origin }

  const step = (stepDeg * Math.PI) / 180
  const angle = Math.round(Math.atan2(dy, dx) / step) * step
  const projected = dx * Math.cos(angle) + dy * Math.sin(angle)
  // Garde-fou : avec un pas supérieur à 180°, le rayon retenu peut se trouver derrière le
  // curseur. On rabat alors sur l'origine plutôt que de produire un mur de longueur négative.
  const length = Math.max(projected, 0)

  return {
    x: clean(origin.x + Math.cos(angle) * length),
    y: clean(origin.y + Math.sin(angle) * length),
  }
}

/**
 * Efface la poussière flottante d'un cosinus d'angle droit.
 *
 * `Math.sin(Math.PI)` vaut 1,2 × 10⁻¹⁶, pas zéro. Sans ce nettoyage, un mur horizontal contraint
 * à 0° porte une ordonnée infinitésimale qui remonte jusqu'au polygone envoyé au serveur, et
 * deux sommets censés être alignés ne le sont jamais tout à fait — exactement ce que la
 * contrainte angulaire est censée garantir.
 */
function clean(value: number): number {
  return Math.abs(value) < 1e-9 ? 0 : value
}

/** Le sommet le plus proche dans la tolérance, ou `null`. */
export function nearestVertex(point: Point, vertices: Point[], toleranceCm: number): Point | null {
  let best: Point | null = null
  let bestDistance = toleranceCm
  for (const vertex of vertices) {
    const distance = Math.hypot(vertex.x - point.x, vertex.y - point.y)
    if (distance <= bestDistance) {
      best = vertex
      bestDistance = distance
    }
  }
  return best === null ? null : { ...best }
}

/**
 * Point placé à `lengthCm` de `origin`, dans la direction de `towards`.
 *
 * C'est la saisie numérique de la cote : on vise grossièrement la direction à la souris, on tape
 * la mesure relevée, et le sommet se pose exactement là. Sans direction utilisable (curseur sur
 * l'origine), on part vers la droite — un choix arbitraire mais stable, jamais un `NaN`.
 */
export function pointAtDistance(origin: Point, towards: Point, lengthCm: number): Point {
  const dx = towards.x - origin.x
  const dy = towards.y - origin.y
  const norm = Math.hypot(dx, dy)
  if (norm === 0) return { x: origin.x + lengthCm, y: origin.y }
  return { x: origin.x + (dx / norm) * lengthCm, y: origin.y + (dy / norm) * lengthCm }
}

/**
 * Résout la position d'un point de saisie.
 *
 * L'ordre des accroches est celui documenté en tête de fichier. L'alignement peut jouer sur les
 * deux axes à la fois — deux sommets différents, un pour l'abscisse et un pour l'ordonnée : c'est
 * exactement le cas d'un angle rentrant de couloir, et le seul moment où deux guides s'affichent.
 */
export function resolveSnap(raw: Point, options: SnapOptions): SnapResult {
  const {
    vertices,
    gridCm,
    toleranceCm,
    origin = null,
    constrainAngle = false,
    guideLengthCm = 400,
  } = options

  if (constrainAngle && origin) {
    const point = constrainToAngle(origin, raw)
    return {
      point,
      guides: [{ from: origin, to: point, kind: 'angle' }],
      kind: 'angle',
      libelle: `contrainte ${ANGLE_STEP_DEG}°`,
    }
  }

  const vertex = nearestVertex(raw, vertices, toleranceCm)
  if (vertex) {
    return { point: vertex, guides: [], kind: 'sommet', libelle: 'sommet existant' }
  }

  const alignedX = vertices.find((candidate) => Math.abs(candidate.x - raw.x) <= toleranceCm)
  const alignedY = vertices.find((candidate) => Math.abs(candidate.y - raw.y) <= toleranceCm)

  if (alignedX || alignedY) {
    // L'axe accroché garde la coordonnée du sommet **telle quelle** : la repasser par la grille
    // la décalerait de quelques centimètres et l'alignement promis par le guide serait faux.
    // L'axe libre, lui, suit la grille comme partout ailleurs.
    const point = {
      x: alignedX ? alignedX.x : snapPoint(raw, gridCm).x,
      y: alignedY ? alignedY.y : snapPoint(raw, gridCm).y,
    }
    const guides: Guide[] = []
    if (alignedX) {
      guides.push({
        from: { x: alignedX.x, y: alignedX.y },
        to: { x: alignedX.x, y: point.y + Math.sign(point.y - alignedX.y) * guideLengthCm },
        kind: 'alignement',
      })
    }
    if (alignedY) {
      guides.push({
        from: { x: alignedY.x, y: alignedY.y },
        to: { x: point.x + Math.sign(point.x - alignedY.x) * guideLengthCm, y: alignedY.y },
        kind: 'alignement',
      })
    }
    const axes = [alignedX ? 'vertical' : null, alignedY ? 'horizontal' : null].filter(Boolean)
    return { point, guides, kind: 'alignement', libelle: `aligné ${axes.join(' et ')}` }
  }

  return {
    point: snapPoint(raw, gridCm),
    guides: [],
    kind: 'grille',
    libelle: gridCm > 0 ? `grille ${gridCm} cm` : 'libre',
  }
}

/**
 * Sommets d'un contour, privés de celui qu'on est en train de déplacer.
 *
 * Un sommet laissé dans ses propres candidats s'accroche à sa position de départ **et** à ses
 * propres prolongements : le déplacer de moins d'un rayon d'accroche devient impossible, et un
 * contour ne peut plus être corrigé de quelques centimètres — précisément le geste que
 * l'aimantation est censée servir.
 */
export function verticesExcept(polygon: number[][], index: number): Point[] {
  return collectVertices([polygon.filter((_, position) => position !== index)])
}

/** Sommets du plan, dédoublonnés : un même angle partagé par deux pièces n'accroche qu'une fois. */
export function collectVertices(polygons: number[][][]): Point[] {
  const seen = new Set<string>()
  const points: Point[] = []
  for (const polygon of polygons) {
    for (const vertex of polygon) {
      const x = vertex[0] as number
      const y = vertex[1] as number
      const key = `${x}|${y}`
      if (seen.has(key)) continue
      seen.add(key)
      points.push({ x, y })
    }
  }
  return points
}
