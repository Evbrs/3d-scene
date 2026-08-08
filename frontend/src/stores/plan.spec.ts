/**
 * Écritures du plan : application de la réponse, conflits, rejeu.
 *
 * Deux régressions coûteuses sont surveillées ici. La première : recharger l'arbre complet du
 * projet après chaque écriture, ce qui transformait un glisser de sommet en un GET de plusieurs
 * dizaines de kilo-octets. La seconde : confondre les deux sens du 409 — « le plan a bougé » et
 * « cette modification détruirait des éléments » — que l'ancienne implémentation distinguait sur
 * une sous-chaîne du message français.
 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/client'
import type { Face, PlanElement, Project, Room } from '@/api/types'
import { usePlanStore } from '@/stores/plan'

const readProject = vi.hoisted(() => vi.fn())
const applyBatch = vi.hoisted(() => vi.fn())

vi.mock('@/api/client', async (importOriginal) => ({
  // `ApiError` doit rester la vraie classe : le store la reconnaît par `instanceof`.
  ...(await importOriginal<typeof import('@/api/client')>()),
  readProject,
  applyBatch,
}))

function face(id: number, roomId: number, label: string): Face {
  return {
    id,
    room_id: roomId,
    label,
    kind: 'wall',
    start_x_cm: 0,
    start_y_cm: 0,
    end_x_cm: 400,
    end_y_cm: 0,
    covering: { color: '#ffffff', material: 'peinture' },
    elements: [],
  }
}

function room(id: number): Room {
  return {
    id,
    project_id: 1,
    name: `Pièce ${id}`,
    wall_thickness_cm: 10,
    ceiling_height_cm: 250,
    polygon: [
      [0, 0],
      [400, 0],
      [400, 300],
      [0, 300],
    ],
    background_url: null,
    background_scale_cm_per_px: null,
    background_offset_x_cm: 0,
    background_offset_y_cm: 0,
    background_rotation_deg: 0,
    background_opacity: 1,
    faces: [face(10, id, 'A'), face(11, id, 'B')],
    free_elements: [],
  }
}

function project(version = 3): Project {
  return {
    id: 1,
    name: 'Chantier',
    description: null,
    version,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    rooms: [room(5)],
  }
}

function conflict(code: string | undefined, currentVersion = 9): ApiError {
  return new ApiError(409, 'Le projet a été modifié entre-temps', {
    detail: 'Le projet a été modifié entre-temps',
    current_version: currentVersion,
    ...(code === undefined ? {} : { code }),
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  readProject.mockReset()
  applyBatch.mockReset()
  readProject.mockResolvedValue(project())
})

describe('écriture acceptée', () => {
  it('applique la réponse du serveur sans relire tout le projet', async () => {
    const store = usePlanStore()
    await store.load(1)
    expect(readProject).toHaveBeenCalledTimes(1)

    const renamed: Room = { ...room(5), name: 'Cuisine' }
    const result = await store.write(
      (version) => {
        expect(version).toBe(3)
        return Promise.resolve(renamed)
      },
      (updated) => store.applyRoom(updated),
    )

    expect(result).toBe(renamed)
    expect(store.project?.rooms[0]?.name).toBe('Cuisine')
    expect(readProject).toHaveBeenCalledTimes(1)
    // La version doit suivre l'incrément appliqué par le serveur, sans quoi l'écriture suivante
    // partirait avec une version périmée et se ferait refuser.
    expect(store.project?.version).toBe(4)
    expect(store.savedAt).toBeInstanceOf(Date)
    expect(store.error).toBeNull()
    expect(store.conflictKind).toBeNull()
  })

  it('recharge quand l’écriture restructure l’arbre', async () => {
    const store = usePlanStore()
    await store.load(1)

    await store.write(() => Promise.resolve(undefined))

    expect(readProject).toHaveBeenCalledTimes(2)
  })

  it('remplace un élément dans sa face et l’en retire à la suppression', async () => {
    const store = usePlanStore()
    await store.load(1)

    const element: PlanElement = {
      id: 42,
      face_id: 10,
      room_id: null,
      kind: 'window',
      x_offset_cm: 20,
      y_offset_cm: 95,
      pos_x_cm: null,
      pos_y_cm: null,
      width_cm: 120,
      height_cm: 110,
      depth_cm: 12,
      rotation_deg: 0,
      furniture_type_id: null,
      colors: {},
      variant_params: {},
    }

    await store.write(
      () => Promise.resolve(element),
      (created) => store.applyElement(created),
    )
    expect(store.project?.rooms[0]?.faces[0]?.elements).toHaveLength(1)

    await store.write(
      () => Promise.resolve(undefined),
      () => store.dropElement(42),
    )
    expect(store.project?.rooms[0]?.faces[0]?.elements).toHaveLength(0)
  })
})

describe('conflit 409', () => {
  it('reconnaît une version périmée et garde l’écriture rejouable', async () => {
    const store = usePlanStore()
    await store.load(1)

    const result = await store.write(() => Promise.reject(conflict('stale_version')))

    expect(result).toBeNull()
    expect(store.conflictKind).toBe('stale')
    expect(store.error).toBe('Le projet a été modifié entre-temps')
    expect(store.hasRefusedWrite).toBe(true)
    // La version locale n'est surtout pas alignée sur celle du serveur : le rejeu aveugle
    // écraserait alors le travail de l'autre onglet avec un arbre périmé.
    expect(store.project?.version).toBe(3)
  })

  it('reconnaît un refus destructif, qui n’est pas un problème de version', async () => {
    const store = usePlanStore()
    await store.load(1)

    await store.write(() => Promise.reject(conflict('destructive_change')))

    expect(store.conflictKind).toBe('destructive')
    expect(store.project?.version).toBe(3)
  })

  it('suppose un conflit de version quand le serveur ne fournit pas de code', async () => {
    const store = usePlanStore()
    await store.load(1)

    await store.write(() => Promise.reject(conflict(undefined)))

    expect(store.conflictKind).toBe('stale')
  })

  it('recharge puis réapplique la charge refusée', async () => {
    const store = usePlanStore()
    await store.load(1)

    const action = vi
      .fn()
      .mockRejectedValueOnce(conflict('stale_version'))
      .mockResolvedValueOnce({ ...room(5), name: 'Salon' })
    await store.write(action, (updated: Room) => store.applyRoom(updated))
    expect(store.conflictKind).toBe('stale')

    readProject.mockResolvedValue(project(9))
    await store.replayRefused()

    expect(readProject).toHaveBeenCalledTimes(2)
    // Rejouée avec la version fraîchement relue, pas avec celle qui avait été refusée.
    expect(action).toHaveBeenLastCalledWith(9)
    expect(store.conflictKind).toBeNull()
    expect(store.project?.rooms[0]?.name).toBe('Salon')
    expect(store.hasRefusedWrite).toBe(false)
  })

  it('ne masque pas une erreur qui n’est pas un conflit', async () => {
    const store = usePlanStore()
    await store.load(1)

    await store.write(() => Promise.reject(new ApiError(500, 'Panne serveur')))

    expect(store.conflictKind).toBeNull()
    expect(store.error).toBe('Panne serveur')
  })
})

/**
 * Écriture en lot (spec §10, amendement A6).
 *
 * Le piège central : un lot n'incrémente la version qu'**une fois**, quel que soit son nombre
 * d'opérations, et il renvoie la sienne. Le suivi local — « une écriture acceptée = une version
 * de plus » — doit donc s'effacer devant la valeur reçue au lieu d'ajouter un cran par-dessus.
 */
describe('écriture en lot', () => {
  const element = (id: number, faceId: number | null, roomId: number | null): PlanElement => ({
    id,
    face_id: faceId,
    room_id: roomId,
    kind: 'furniture',
    x_offset_cm: 10,
    y_offset_cm: 0,
    pos_x_cm: roomId === null ? null : 120,
    pos_y_cm: roomId === null ? null : 90,
    width_cm: 60,
    height_cm: 80,
    depth_cm: 40,
    rotation_deg: 0,
    furniture_type_id: 2,
    colors: {},
    variant_params: {},
  })

  it('prend la version rendue par le serveur au lieu de l’incrémenter', async () => {
    const store = usePlanStore()
    await store.load(1)
    applyBatch.mockResolvedValue({ version: 4, results: [] })

    await store.writeBatch([{ op: 'delete_element', element_id: 1 }])

    expect(applyBatch).toHaveBeenCalledWith(1, [{ op: 'delete_element', element_id: 1 }], 3)
    // 4 et non 5 : le lot en consomme une, `bumpVersion` en ajouterait une seconde.
    expect(store.project?.version).toBe(4)
  })

  it('range un meuble libre dans la pièce et non dans une face', async () => {
    const store = usePlanStore()
    await store.load(1)
    const libre = element(42, null, 5)
    applyBatch.mockResolvedValue({
      version: 4,
      results: [
        { op: 'create_room_element', status: 'created', element_id: 42, room_id: null, element: libre, room: null },
      ],
    })

    await store.writeBatch([
      { op: 'create_room_element', room_id: 5, element: { pos_x_cm: 120, pos_y_cm: 90 } },
    ])

    // Le chercher dans les faces le ferait disparaître de l'affichage sans la moindre erreur :
    // le meuble serait bien en base, et invisible.
    expect(store.project?.rooms[0]?.free_elements).toEqual([libre])
    expect(store.project?.rooms[0]?.faces.flatMap((candidate) => candidate.elements)).toEqual([])
  })

  it('retire un élément supprimé, quel que soit son ancrage', async () => {
    const store = usePlanStore()
    await store.load(1)
    store.applyElement(element(7, 10, null))
    store.applyElement(element(8, null, 5))
    applyBatch.mockResolvedValue({
      version: 4,
      results: [
        { op: 'delete_element', status: 'deleted', element_id: 7, room_id: null, element: null, room: null },
        { op: 'delete_element', status: 'deleted', element_id: 8, room_id: null, element: null, room: null },
      ],
    })

    await store.writeBatch([
      { op: 'delete_element', element_id: 7 },
      { op: 'delete_element', element_id: 8 },
    ])

    expect(store.project?.rooms[0]?.faces[0]?.elements).toEqual([])
    expect(store.project?.rooms[0]?.free_elements).toEqual([])
  })

  it('découpe au-delà de cent opérations et enchaîne sur la version reçue', async () => {
    const store = usePlanStore()
    await store.load(1)
    applyBatch
      .mockResolvedValueOnce({ version: 4, results: [] })
      .mockResolvedValueOnce({ version: 5, results: [] })

    const operations = Array.from({ length: 150 }, (_, index) => ({
      op: 'delete_element' as const,
      element_id: index + 1,
    }))
    const responses = await store.writeBatch(operations)

    expect(responses).toHaveLength(2)
    expect(applyBatch.mock.calls[0]?.[1]).toHaveLength(100)
    expect(applyBatch.mock.calls[1]?.[1]).toHaveLength(50)
    // Le second paquet part avec la version que le premier a rendue, sinon il est périmé d'office.
    expect(applyBatch.mock.calls[1]?.[2]).toBe(4)
    expect(store.project?.version).toBe(5)
  })

  it('abandonne les paquets suivants dès qu’un lot est refusé', async () => {
    const store = usePlanStore()
    await store.load(1)
    applyBatch.mockRejectedValue(conflict('stale_version'))

    const operations = Array.from({ length: 150 }, (_, index) => ({
      op: 'delete_element' as const,
      element_id: index + 1,
    }))

    expect(await store.writeBatch(operations)).toBeNull()
    expect(applyBatch).toHaveBeenCalledTimes(1)
    expect(store.conflictKind).toBe('stale')
    expect(store.project?.version).toBe(3)
  })

  it('n’appelle pas le serveur pour un lot vide', async () => {
    const store = usePlanStore()
    await store.load(1)

    expect(await store.writeBatch([])).toEqual([])
    expect(applyBatch).not.toHaveBeenCalled()
  })
})

describe('chargement', () => {
  it('expose l’erreur au lieu de laisser la vue muette', async () => {
    readProject.mockRejectedValue(new ApiError(404, 'Projet introuvable'))
    const store = usePlanStore()

    await store.load(1)

    expect(store.project).toBeNull()
    expect(store.error).toBe('Projet introuvable')
    expect(store.loading).toBe(false)
  })

  it('abandonne une sélection de pièce qui n’existe plus', async () => {
    const store = usePlanStore()
    await store.load(1)
    store.selectedRoomId = 999
    store.selectedFaceLabel = 'Z'

    await store.load(1)

    expect(store.selectedRoomId).toBe(5)
    expect(store.selectedFaceLabel).toBeNull()
  })
})
