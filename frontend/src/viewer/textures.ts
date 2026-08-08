/**
 * Textures procédurales de revêtement.
 *
 * Un aplat de couleur ne dit pas si un sol est carrelé en 30 x 60 droit ou en bâtons rompus. Le
 * revêtement porte déjà la matière, les dimensions d'unité et le motif de pose : on en dessine
 * une `CanvasTexture`, sans aucun fichier externe — le dépôt n'embarque pas d'image et la CSP de
 * production n'autorise pas d'origine tierce.
 *
 * **Le calepinage reste au backend.** `build_takeoff` compte les unités entières, les coupes et
 * les chutes ; ce module ne fait qu'en donner une lecture visuelle. Les motifs obliques sont
 * indicatifs : une pose en chevron réelle dépend d'un point de départ et d'un sens que la texture
 * ne connaît pas.
 *
 * Le point clé du dimensionnement : `ExtrudeGeometry` et `ShapeGeometry` dérivent leurs UV des
 * coordonnées de la `Shape`, qui sont en **centimètres** (le backend publie ses contours ainsi).
 * Un `repeat` de 1/dimension du motif donne donc directement le bon nombre d'unités sur la face,
 * quelle que soit sa taille — c'est la face réelle qui décide, pas une constante.
 */
import { CanvasTexture, RepeatWrapping, SRGBColorSpace, type Texture } from 'three'

import type { Covering, LayingPattern } from '@/api/types'

/** Largeur du joint, en centimètres. Un joint de carrelage courant fait 2 à 5 mm. */
export const JOINT_CM = 0.3

/** Une unité posée dans la cellule répétable : position du centre, taille, rotation. */
export interface PatternTile {
  xCm: number
  yCm: number
  widthCm: number
  heightCm: number
  angleRad: number
}

/** La plus petite cellule qui, répétée, reconstitue le calepinage. */
export interface PatternCell {
  widthCm: number
  heightCm: number
  tiles: PatternTile[]
}

const tile = (
  xCm: number,
  yCm: number,
  widthCm: number,
  heightCm: number,
  angleRad = 0,
): PatternTile => ({ xCm, yCm, widthCm, heightCm, angleRad })

/**
 * Cellule répétable d'un motif de pose.
 *
 * Les unités peuvent déborder de la cellule : la peinture les redessine sur les huit cellules
 * voisines, ce qui rend le raccord invisible quel que soit le motif. Sans ce report, une pose
 * oblique laisserait une couture visible à chaque bord.
 */
export function patternCell(
  pattern: LayingPattern,
  unitWidthCm: number,
  unitHeightCm: number,
): PatternCell {
  const width = unitWidthCm
  const height = unitHeightCm

  if (pattern === 'staggered') {
    // Pose à coupe de pierre : une rangée sur deux décalée d'une demi-unité. La cellule couvre
    // donc deux rangées, sans quoi le décalage se perdrait à la répétition.
    return {
      widthCm: width,
      heightCm: height * 2,
      tiles: [
        tile(width / 2, height / 2, width, height),
        tile(0, height * 1.5, width, height),
        tile(width, height * 1.5, width, height),
      ],
    }
  }

  if (pattern === 'chevron') {
    // Deux lames inclinées à 45° qui se rejoignent en V.
    return {
      widthCm: width * 2,
      heightCm: height * 2,
      tiles: [
        tile(width * 0.5, height, width, height, Math.PI / 4),
        tile(width * 1.5, height, width, height, -Math.PI / 4),
      ],
    }
  }

  if (pattern === 'herringbone') {
    // Bâtons rompus : les lames alternent d'un quart de tour, la cellule est carrée.
    const side = width + height
    return {
      widthCm: side,
      heightCm: side,
      tiles: [
        tile(side * 0.25, side * 0.25, width, height, Math.PI / 4),
        tile(side * 0.75, side * 0.75, width, height, Math.PI / 4),
        tile(side * 0.75, side * 0.25, width, height, -Math.PI / 4),
        tile(side * 0.25, side * 0.75, width, height, -Math.PI / 4),
      ],
    }
  }

  return { widthCm: width, heightCm: height, tiles: [tile(width / 2, height / 2, width, height)] }
}

/**
 * Répétition à donner à la texture.
 *
 * Les UV étant en centimètres, l'inverse de la cellule suffit : une face de 400 cm avec une
 * cellule de 30 cm affiche bien 13,3 unités, sans que personne ait à mesurer la face.
 */
export function textureRepeat(cell: PatternCell): [number, number] {
  return [1 / cell.widthCm, 1 / cell.heightCm]
}

/** Nombre d'unités visibles sur une face donnée — ce que la texture montre, en clair. */
export function tileCount(faceLengthCm: number, cellCm: number): number {
  return cellCm > 0 ? faceLengthCm / cellCm : 0
}

const HEX = /^#[0-9a-fA-F]{6}$/

function channels(hex: string): [number, number, number] {
  return [
    Number.parseInt(hex.slice(1, 3), 16),
    Number.parseInt(hex.slice(3, 5), 16),
    Number.parseInt(hex.slice(5, 7), 16),
  ]
}

/**
 * Couleur du joint, déduite de celle du revêtement.
 *
 * Assombrir marche sur un carrelage clair et disparaît sur un sol foncé — d'où l'inversion en
 * dessous du gris moyen. Le joint doit rester visible sur les deux, c'est lui qui donne l'échelle.
 */
export function jointColor(color: string): string {
  if (!HEX.test(color)) return '#9a9a9a'
  const [red, green, blue] = channels(color)
  const luminance = (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255
  const shift = luminance > 0.45 ? -52 : 46
  const clamp = (value: number): number => Math.min(255, Math.max(0, Math.round(value + shift)))
  return `#${[clamp(red), clamp(green), clamp(blue)]
    .map((value) => value.toString(16).padStart(2, '0'))
    .join('')}`
}

/** Résolution du canevas : assez fine pour voir le joint, assez petite pour ne rien coûter. */
export const MIN_CANVAS_PX = 64
export const MAX_CANVAS_PX = 512
const PIXELS_PER_CM = 6

export function canvasPixels(cell: PatternCell): { width: number; height: number; scale: number } {
  const longest = Math.max(cell.widthCm, cell.heightCm)
  const shortest = Math.min(cell.widthCm, cell.heightCm)
  const scale = Math.min(
    Math.max(PIXELS_PER_CM, shortest > 0 ? MIN_CANVAS_PX / shortest : PIXELS_PER_CM),
    longest > 0 ? MAX_CANVAS_PX / longest : PIXELS_PER_CM,
  )
  return {
    width: Math.max(2, Math.round(cell.widthCm * scale)),
    height: Math.max(2, Math.round(cell.heightCm * scale)),
    scale,
  }
}

/** Le revêtement décrit-il assez le calepinage pour qu'on puisse le dessiner ? */
export function coveringPattern(
  covering: Covering | null | undefined,
): { pattern: LayingPattern; unitWidthCm: number; unitHeightCm: number } | null {
  const unitWidthCm = covering?.unit_width_cm ?? 0
  const unitHeightCm = covering?.unit_height_cm ?? 0
  if (!(unitWidthCm > 0) || !(unitHeightCm > 0)) return null
  // Des dimensions d'unité sans motif décrivent une pose droite : c'est la pose par défaut du
  // backend, pas une absence d'information.
  return { pattern: covering?.pattern ?? 'straight', unitWidthCm, unitHeightCm }
}

/** Identité d'une texture : deux faces qui la partagent partagent l'objet, pas une copie. */
export function coveringTextureKey(
  covering: Covering | null | undefined,
  color: string,
): string | null {
  const laying = coveringPattern(covering)
  if (!laying) return null
  return `${laying.pattern}|${laying.unitWidthCm}x${laying.unitHeightCm}|${color}`
}

function paint(
  context: CanvasRenderingContext2D,
  cell: PatternCell,
  scale: number,
  color: string,
): void {
  context.save()
  context.scale(scale, scale)
  context.fillStyle = jointColor(color)
  context.fillRect(0, 0, cell.widthCm, cell.heightCm)
  context.fillStyle = color

  const inset = JOINT_CM / 2
  // Report sur les huit cellules voisines : une unité à cheval sur un bord réapparaît en face,
  // ce qui rend le raccord invisible même sur les poses obliques.
  for (const shiftX of [-cell.widthCm, 0, cell.widthCm]) {
    for (const shiftY of [-cell.heightCm, 0, cell.heightCm]) {
      for (const unit of cell.tiles) {
        context.save()
        context.translate(unit.xCm + shiftX, unit.yCm + shiftY)
        context.rotate(unit.angleRad)
        context.fillRect(
          -unit.widthCm / 2 + inset,
          -unit.heightCm / 2 + inset,
          Math.max(0, unit.widthCm - JOINT_CM),
          Math.max(0, unit.heightCm - JOINT_CM),
        )
        context.restore()
      }
    }
  }
  context.restore()
}

/**
 * Texture du revêtement, ou `null` si le calepinage n'est pas décrit — auquel cas l'appelant s'en
 * tient à l'aplat de couleur, qui reste une réponse juste.
 *
 * `null` est aussi la réponse quand le contexte 2D est indisponible : le viewer ne doit pas
 * tomber parce qu'un environnement ne sait pas peindre un canevas.
 */
export function buildCoveringTexture(
  covering: Covering | null | undefined,
  color: string,
  host: Pick<Document, 'createElement'> | undefined = globalThis.document,
): Texture | null {
  const laying = coveringPattern(covering)
  if (!laying || !host) return null

  const cell = patternCell(laying.pattern, laying.unitWidthCm, laying.unitHeightCm)
  const pixels = canvasPixels(cell)
  const canvas = host.createElement('canvas')
  canvas.width = pixels.width
  canvas.height = pixels.height

  const context = canvas.getContext('2d')
  if (!context) return null
  paint(context, cell, pixels.scale, color)

  const texture = new CanvasTexture(canvas)
  texture.wrapS = RepeatWrapping
  texture.wrapT = RepeatWrapping
  const [repeatX, repeatY] = textureRepeat(cell)
  texture.repeat.set(repeatX, repeatY)
  // Carte de couleur : sans cet espace, le rendu tonemappé délave le revêtement.
  texture.colorSpace = SRGBColorSpace
  return texture
}
