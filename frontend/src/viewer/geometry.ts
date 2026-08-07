/**
 * Construction des géométries Three.js à partir du scene graph.
 *
 * Fonctions pures, séparées du composant de rendu : elles sont ainsi testables sans WebGL ni
 * canvas. Elles ne calculent aucune géométrie métier — tout vient du backend (spec §3.1) — elles
 * ne font que traduire des contours et des boîtes en objets Three.js.
 */
import {
  BoxGeometry,
  type BufferGeometry,
  CylinderGeometry,
  Path,
  Shape,
  SphereGeometry,
} from 'three'

/**
 * `Shape` d'un contour, avec ses trous.
 *
 * Approche « simple » retenue par la spec §3.2 : une forme plane trouée, extrudée. Le CSG
 * complet n'est mobilisé que là où elle ne suffit pas — les meubles marqués `requires_csg`.
 */
export function buildShape(outline: number[][], holes: number[][][]): Shape {
  const shape = new Shape()
  outline.forEach((vertex, index) => {
    const x = vertex[0] ?? 0
    const y = vertex[1] ?? 0
    if (index === 0) shape.moveTo(x, y)
    else shape.lineTo(x, y)
  })
  shape.closePath()

  holes.forEach((hole) => {
    const path = new Path()
    hole.forEach((vertex, index) => {
      const x = vertex[0] ?? 0
      const y = vertex[1] ?? 0
      if (index === 0) path.moveTo(x, y)
      else path.lineTo(x, y)
    })
    path.closePath()
    shape.holes.push(path)
  })

  return shape
}

/** Axe de révolution d'une primitive de révolution, tel que le backend le publie. */
export type PrimitiveAxis = 'x' | 'y' | 'z'

/** Géométrie d'une primitive, mise à l'échelle par sa boîte englobante. */
export function primitiveGeometry(
  size: readonly number[],
  type: string,
  axis: PrimitiveAxis = 'y',
): BufferGeometry {
  const [width, height, depth] = [size[0] ?? 1, size[1] ?? 1, size[2] ?? 1]

  if (type === 'cylinder') {
    // Un cylindre unitaire mis à l'échelle, comme la sphère plus bas : c'est ce qui donne une
    // section elliptique quand largeur et profondeur diffèrent. `CylinderGeometry` ne prend qu'un
    // rayon par extrémité — lui passer la demi-largeur en haut et la demi-profondeur en bas
    // produit un CÔNE dès que les deux ne sont pas égales, ce qui est le cas de cinq recettes du
    // catalogue (poignée de porte, alvéole de prise, barre d'appui, robinets).
    const geometry = new CylinderGeometry(0.5, 0.5, 1, 24)
    // La révolution est sur Y par défaut. On couche le cylindre AVANT la mise à l'échelle, pour
    // que largeur, hauteur et profondeur restent celles de la boîte englobante du monde.
    if (axis === 'x') geometry.rotateZ(Math.PI / 2)
    if (axis === 'z') geometry.rotateX(Math.PI / 2)
    geometry.scale(width, height, depth)
    return geometry
  }
  if (type === 'sphere') {
    // Une sphère unitaire mise à l'échelle : c'est ce qui permet un ellipsoïde quand la boîte
    // englobante n'est pas cubique.
    const geometry = new SphereGeometry(0.5, 24, 16)
    geometry.scale(width, height, depth)
    return geometry
  }
  return new BoxGeometry(width, height, depth)
}
