import { describe, expect, it } from 'vitest'

import type { FurnitureNode, Primitive, SceneRoom } from '@/api/types'
import {
  applyColorOverrides,
  colorTargets,
  effectiveColor,
  humanize,
  mergedColors,
} from '@/viewer/colors'
import { capturePlan, captureFileName } from '@/viewer/capture'

const primitive = (slot: string, color: string | null = null): Primitive => ({
  type: 'box',
  offset: [0, 0, 0],
  size: [10, 10, 10],
  color_slot: slot,
  color,
  operation: 'add',
  axis: 'y',
})

const vasque: FurnitureNode = {
  kind: 'furniture',
  element_id: 42,
  face_label: 'A',
  furniture_type_slug: 'meuble-sous-vasque',
  position: [0, 0, 0],
  rotation_y: 0,
  size_cm: [60, 18, 45],
  primitives: [primitive('ceramique'), primitive('ceramique'), primitive('robinet', '#c0c0c0')],
  requires_csg: true,
  variant_params: {},
}

const room: SceneRoom = {
  id: 3,
  name: 'Salle de bain',
  wall_thickness_cm: 10,
  ceiling_height_cm: 250,
  floor_area_cm2: 60000,
  net_floor_area_cm2: 58000,
  nodes: [vasque],
  cameras: [],
}

describe('emplacements couleur du mobilier (spec §4.1)', () => {
  it('ne liste qu’une fois un emplacement partagé par plusieurs primitives', () => {
    // C'est une matière qu'on choisit, pas une pièce du meuble : deux boîtes en céramique ne
    // donnent pas deux sélecteurs.
    const targets = colorTargets([room])
    expect(targets).toHaveLength(1)
    expect(targets[0]!.slots.map((entry) => entry.slot)).toEqual(['ceramique', 'robinet'])
  })

  it('rend le slug lisible, faute de libellé publié', () => {
    expect(humanize('meuble-sous-vasque')).toBe('Meuble sous vasque')
    expect(humanize('')).toBe('')
  })

  it('reprend la couleur déjà choisie sur l’instance', () => {
    const target = colorTargets([room])[0]!
    expect(effectiveColor(target, 'robinet', {})).toBe('#c0c0c0')
    expect(effectiveColor(target, 'ceramique', {})).toBeNull()
    expect(effectiveColor(target, 'inconnu', {})).toBeNull()
  })

  it('fait passer le choix en cours devant celui du serveur', () => {
    const target = colorTargets([room])[0]!
    expect(effectiveColor(target, 'robinet', { 42: { robinet: '#8b5a2b' } })).toBe('#8b5a2b')
  })
})

describe('écriture des couleurs', () => {
  it('renvoie le dictionnaire complet, pas seulement l’emplacement modifié', () => {
    // La route remplace `colors` au lieu de le fusionner : n'envoyer que « ceramique » effacerait
    // la couleur du robinet.
    const target = colorTargets([room])[0]!
    expect(mergedColors(target, { 42: { ceramique: '#f5f5f0' } })).toEqual({
      robinet: '#c0c0c0',
      ceramique: '#f5f5f0',
    })
  })

  it('n’invente pas d’entrée pour un emplacement jamais choisi', () => {
    const target = colorTargets([room])[0]!
    expect(mergedColors(target, {})).toEqual({ robinet: '#c0c0c0' })
  })
})

describe('teinte immédiate de la scène', () => {
  it('rend le scene graph inchangé quand rien n’est choisi', () => {
    // La scène 3D se reconstruit sur changement d'identité : copier pour rien la reconstruirait
    // à chaque rendu.
    const rooms = [room]
    expect(applyColorOverrides(rooms, {})).toBe(rooms)
  })

  it('teinte les primitives de l’emplacement choisi, et elles seules', () => {
    const teinte = applyColorOverrides([room], { 42: { ceramique: '#f5f5f0' } })
    const node = teinte[0]!.nodes[0] as FurnitureNode

    expect(node.primitives.map((entry) => entry.color)).toEqual([
      '#f5f5f0',
      '#f5f5f0',
      '#c0c0c0',
    ])
  })

  it('ne modifie pas le scene graph reçu', () => {
    applyColorOverrides([room], { 42: { ceramique: '#f5f5f0' } })
    expect((room.nodes[0] as FurnitureNode).primitives[0]!.color).toBeNull()
  })

  it('ignore un élément qui n’est plus dans la scène', () => {
    const teinte = applyColorOverrides([room], { 999: { corps: '#000000' } })
    expect((teinte[0]!.nodes[0] as FurnitureNode).primitives[0]!.color).toBeNull()
  })
})

describe('captures (spec §3.5)', () => {
  it('réduit un nom de pièce saisi par l’utilisateur à un nom de fichier sobre', () => {
    expect(captureFileName('Salle de bain (1er étage)', 'face-A')).toBe(
      'salle-de-bain-1er-etage-face-a.png',
    )
    expect(captureFileName('', '')).toBe('vue.png')
  })

  it('planifie une prise de vue par mur, et rien d’autre', () => {
    // Les vues d'ensemble ne documentent aucun mur : le dossier PDF a déjà son plan coté.
    const plan = capturePlan('Séjour', [
      { name: 'dessus', face_label: null },
      { name: 'isometrique', face_label: null },
      { name: 'face-A', face_label: 'A' },
      { name: 'face-B', face_label: 'B' },
    ])

    expect(plan.map((shot) => shot.cameraName)).toEqual(['face-A', 'face-B'])
    expect(plan[0]!.fileName).toBe('sejour-face-a.png')
    expect(plan[0]!.faceLabel).toBe('A')
  })
})
