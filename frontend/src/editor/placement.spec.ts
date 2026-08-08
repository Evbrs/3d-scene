/**
 * Décision d'ancrage d'un meuble déposé, et emprise au sol.
 *
 * Deux garanties sont surveillées ici, dans cet ordre d'importance :
 *
 * 1. l'**emprise tournée** est calculée avec exactement la convention du backend
 *    (`services/faces.py::free_element_footprint`). S'en écarter dessinerait dans l'éditeur un
 *    meuble que la 3D placerait ailleurs — ou ferait refuser par le serveur un meuble que
 *    l'éditeur montre bien à l'intérieur ;
 * 2. le choix face / pièce est **irréversible** (spec §10, A4 : le changement d'ancrage n'existe
 *    pas). Un meuble mal ancré ne se rattrape pas, il se supprime et se recrée.
 */
import { describe, expect, it } from 'vitest'

import type { Face, PlanElement } from '@/api/types'
import { wallGeometries } from '@/editor/drawing'
import {
  clampToRoom,
  distanceToPolygon,
  footprintCorners,
  footprintFits,
  freeFootprint,
  pointInPolygon,
  projectOnSegment,
  resolveDrop,
} from '@/editor/placement'

const CARRE = [
  [0, 0],
  [400, 0],
  [400, 300],
  [0, 300],
]

/** Pièce en L : le renfoncement est *hors* de la pièce tout en étant dans la boîte englobante. */
const EN_L = [
  [0, 0],
  [400, 0],
  [400, 200],
  [200, 200],
  [200, 400],
  [0, 400],
]

/**
 * Pièce en arche : un poteau remonte du bas entre x = 150 et x = 250.
 *
 * C'est la forme qui piège un contrôle limité aux coins : un meuble posé à cheval sur le poteau a
 * ses quatre coins dans la pièce et son milieu dans le vide.
 */
const ARCHE = [
  [0, 0],
  [400, 0],
  [400, 300],
  [250, 300],
  [250, 100],
  [150, 100],
  [150, 300],
  [0, 300],
]

function face(id: number, label: string): Face {
  return {
    id,
    room_id: 1,
    label,
    kind: 'wall',
    start_x_cm: 0,
    start_y_cm: 0,
    end_x_cm: 0,
    end_y_cm: 0,
    covering: {},
    elements: [],
  }
}

const FACES = [face(11, 'A'), face(12, 'B'), face(13, 'C'), face(14, 'D')]

function element(overrides: Partial<PlanElement> = {}): PlanElement {
  return {
    id: 1,
    face_id: null,
    room_id: 7,
    kind: 'furniture',
    x_offset_cm: 0,
    y_offset_cm: 0,
    pos_x_cm: 200,
    pos_y_cm: 150,
    width_cm: 200,
    height_cm: 45,
    depth_cm: 80,
    rotation_deg: 0,
    furniture_type_id: 3,
    colors: {},
    variant_params: {},
    ...overrides,
  }
}

describe('emprise au sol', () => {
  it('sans rotation, la largeur est en X et la profondeur en Y', () => {
    const coins = footprintCorners({ x: 100, y: 100 }, 200, 80, 0)

    expect(coins).toEqual([
      { x: 0, y: 60 },
      { x: 200, y: 60 },
      { x: 200, y: 140 },
      { x: 0, y: 140 },
    ])
  })

  it('à 90°, largeur et profondeur échangent d’axe', () => {
    // La convention du backend : `R_y(a)` envoie la largeur sur (cos a, -sin a). À 90°, la
    // largeur part donc sur -Y. Contrôler avec les axes non échangés laisserait une table de
    // 2 m traverser le mur d'en face.
    const coins = footprintCorners({ x: 0, y: 0 }, 200, 80, 90)

    const xs = coins.map((coin) => Math.round(coin.x))
    const ys = coins.map((coin) => Math.round(coin.y))
    expect(Math.max(...xs) - Math.min(...xs)).toBe(80)
    expect(Math.max(...ys) - Math.min(...ys)).toBe(200)
  })

  it('reste centrée sur la position quelle que soit la rotation', () => {
    for (const angle of [0, 30, 45, 90, 137, 270]) {
      const coins = footprintCorners({ x: 50, y: 70 }, 120, 60, angle)
      const centre = coins.reduce(
        (total, coin) => ({ x: total.x + coin.x / 4, y: total.y + coin.y / 4 }),
        { x: 0, y: 0 },
      )
      expect(centre.x).toBeCloseTo(50, 6)
      expect(centre.y).toBeCloseTo(70, 6)
    }
  })

  it('n’existe pas pour un élément adossé à une face', () => {
    expect(freeFootprint(element({ face_id: 9, pos_x_cm: null, pos_y_cm: null }))).toBeNull()
  })
})

describe('appartenance au contour', () => {
  it('distingue le renfoncement d’une pièce en L de sa boîte englobante', () => {
    // (300, 300) est dans la boîte [0,400]×[0,400] et pourtant hors de la pièce.
    expect(pointInPolygon(EN_L, { x: 300, y: 300 })).toBe(false)
    expect(pointInPolygon(EN_L, { x: 100, y: 300 })).toBe(true)
    expect(pointInPolygon(EN_L, { x: 300, y: 100 })).toBe(true)
  })

  it('mesure la distance au bord et non à l’intérieur', () => {
    expect(distanceToPolygon(CARRE, { x: 450, y: 150 })).toBeCloseTo(50, 6)
    expect(distanceToPolygon(CARRE, { x: 200, y: 150 })).toBeCloseTo(150, 6)
  })
})

describe('projection sur un segment', () => {
  it('borne le projeté aux extrémités', () => {
    const avant = projectOnSegment({ x: -50, y: 10 }, { x: 0, y: 0 }, { x: 100, y: 0 })

    expect(avant.alongCm).toBe(0)
    expect(avant.distance).toBeCloseTo(Math.hypot(50, 10), 6)
  })

  it('rend l’abscisse curviligne en centimètres', () => {
    const projection = projectOnSegment({ x: 30, y: 12 }, { x: 0, y: 0 }, { x: 100, y: 0 })

    expect(projection.alongCm).toBeCloseTo(30, 6)
    expect(projection.distance).toBeCloseTo(12, 6)
  })

  it('ne divise pas par zéro sur un segment dégénéré', () => {
    const projection = projectOnSegment({ x: 3, y: 4 }, { x: 0, y: 0 }, { x: 0, y: 0 })

    expect(projection.alongCm).toBe(0)
    expect(projection.distance).toBe(5)
  })
})

describe('encombrement', () => {
  it('accepte un meuble poussé pile contre le mur', () => {
    // Le geste le plus courant du métier. Le polygone saisi est la ligne médiane des murs, donc
    // la limite que l'utilisateur voit dans l'éditeur.
    const coins = footprintCorners({ x: 200, y: 40 }, 200, 80, 0)

    expect(footprintFits(CARRE, coins)).toBeNull()
  })

  it('refuse un meuble qui déborde, en nommant le coin et le débordement', () => {
    const coins = footprintCorners({ x: 380, y: 150 }, 200, 80, 0)
    const message = footprintFits(CARRE, coins)

    expect(message).toContain('sort de la pièce')
    expect(message).toContain('480')
    expect(message).toContain('80 cm')
  })

  it('refuse une emprise qui enjambe un poteau sans qu’aucun coin n’en sorte', () => {
    const coins = [
      { x: 100, y: 50 },
      { x: 300, y: 50 },
      { x: 300, y: 150 },
      { x: 100, y: 150 },
    ]

    // Les quatre coins sont bien dans la pièce…
    for (const coin of coins) expect(pointInPolygon(ARCHE, coin)).toBe(true)
    // …et pourtant le milieu du meuble est dans le vide du poteau.
    expect(pointInPolygon(ARCHE, { x: 200, y: 150 })).toBe(false)
    expect(footprintFits(ARCHE, coins)).toContain('traverse')
  })

  it('ne juge rien tant que la pièce n’a pas de contour', () => {
    expect(footprintFits([], footprintCorners({ x: 0, y: 0 }, 100, 100, 0))).toBeNull()
  })
})

describe('recentrage', () => {
  it('laisse en place ce qui tient déjà', () => {
    expect(clampToRoom({ x: 200, y: 150 }, CARRE, 100, 50, 0)).toEqual({ x: 200, y: 150 })
  })

  it('ramène dans la pièce ce qui vient d’en sortir', () => {
    const place = clampToRoom({ x: 600, y: 150 }, CARRE, 100, 50, 0)

    expect(place).not.toBeNull()
    expect(footprintFits(CARRE, footprintCorners(place!, 100, 50, 0))).toBeNull()
  })

  it('rend null quand aucun placement ne convient', () => {
    // Un meuble plus large que la pièce : ce n'est pas un buttage, c'est un vrai refus.
    expect(clampToRoom({ x: 200, y: 150 }, CARRE, 900, 50, 0)).toBeNull()
  })
})

describe('résolution d’une dépose', () => {
  const walls = wallGeometries(CARRE, FACES)
  const base = { roomId: 7, polygon: CARRE, walls, wallThicknessCm: 10 }

  it('adosse à la face quand on lâche près d’un mur', () => {
    const cible = resolveDrop({ x: 150, y: 20 }, { ...base, widthCm: 60, depthCm: 40 })

    expect(cible.kind).toBe('face')
    if (cible.kind !== 'face') return
    expect(cible.label).toBe('A')
    // Le décalage est celui du **coin** du meuble, pas de son centre.
    expect(cible.xOffsetCm).toBe(120)
    expect(cible.libelle).toContain('mur A')
  })

  it('borne le décalage pour que le meuble tienne sur le mur', () => {
    // Lâché à 380 cm le long d'un mur de 400, un meuble de 200 ne peut commencer qu'à 200.
    const cible = resolveDrop({ x: 380, y: 15 }, { ...base, widthCm: 200, depthCm: 40 })

    expect(cible.kind).toBe('face')
    if (cible.kind !== 'face') return
    expect(cible.label).toBe('A')
    expect(cible.xOffsetCm).toBe(200)
  })

  it('retient le mur le plus proche quand deux se disputent le coin', () => {
    // Lâché à 5 cm du mur B et 20 du mur A : c'est B qui gagne, pas l'ordre du polygone.
    const cible = resolveDrop({ x: 395, y: 20 }, { ...base, widthCm: 60, depthCm: 40 })

    expect(cible.kind).toBe('face')
    if (cible.kind !== 'face') return
    expect(cible.label).toBe('B')
  })

  it('pose librement quand on lâche au milieu', () => {
    const cible = resolveDrop({ x: 200, y: 150 }, { ...base, widthCm: 200, depthCm: 80 })

    expect(cible.kind).toBe('room')
    if (cible.kind !== 'room') return
    expect(cible).toMatchObject({ roomId: 7, posXCm: 200, posYCm: 150 })
  })

  it('adosse en priorité, même quand la pose libre serait possible', () => {
    // Un meuble lâché contre un mur doit y **rester collé** si le contour bouge : c'est
    // exactement ce que l'ancrage à la face garantit et que le repère de pièce ne fait pas.
    const cible = resolveDrop({ x: 200, y: 45 }, { ...base, widthCm: 60, depthCm: 40 })

    expect(cible.kind).toBe('face')
  })

  it('mesure le seuil depuis la face intérieure et non depuis l’axe du mur', () => {
    // Sur un mur de 60 cm, un point à 50 cm de l'axe est à 20 cm de la surface visible : il doit
    // s'adosser. Mesuré depuis l'axe, il tomberait tout juste hors du seuil.
    const epais = { ...base, wallThicknessCm: 60, widthCm: 60, depthCm: 40, wallSnapCm: 45 }

    expect(resolveDrop({ x: 200, y: 50 }, epais).kind).toBe('face')
  })

  it('laisse une dépose libre possible dans un couloir étroit', () => {
    // Un couloir de 90 cm : un seuil d'adossement fixe de 45 cm couvre toute la surface et rend
    // la pose libre impossible. Le meuble du milieu du couloir doit rester posable au sol.
    const couloir = [
      [0, 0],
      [600, 0],
      [600, 90],
      [0, 90],
    ]
    const murs = wallGeometries(couloir, FACES)
    const cible = resolveDrop(
      { x: 300, y: 45 },
      { roomId: 7, polygon: couloir, walls: murs, wallThicknessCm: 10, widthCm: 60, depthCm: 40 },
    )

    expect(cible.kind).toBe('room')
  })

  it('refuse une dépose hors de la pièce', () => {
    const cible = resolveDrop({ x: 900, y: 900 }, { ...base, widthCm: 60, depthCm: 40 })

    expect(cible.kind).toBe('refuse')
  })

  it('refuse un meuble trop grand pour l’endroit visé', () => {
    const cible = resolveDrop({ x: 200, y: 150 }, { ...base, widthCm: 900, depthCm: 80 })

    expect(cible.kind).toBe('refuse')
    if (cible.kind !== 'refuse') return
    expect(cible.raison).toContain('sort de la pièce')
  })

  it('refuse de rendre libre une ouverture', () => {
    const cible = resolveDrop(
      { x: 200, y: 150 },
      { ...base, widthCm: 90, depthCm: 12, needsWall: true },
    )

    expect(cible.kind).toBe('refuse')
    if (cible.kind !== 'refuse') return
    // Un percement flottant au milieu d'une pièce n'a aucun sens (spec §3.1).
    expect(cible.raison).toContain('mur')
  })
})
