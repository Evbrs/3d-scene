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

vi.mock('@/api/client', async (importOriginal) => ({
  // `ApiError` doit rester la vraie classe : le store la reconnaît par `instanceof`.
  ...(await importOriginal<typeof import('@/api/client')>()),
  readProject,
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
    faces: [face(10, id, 'A'), face(11, id, 'B')],
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
      kind: 'window',
      x_offset_cm: 20,
      y_offset_cm: 95,
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
