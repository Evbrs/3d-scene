/**
 * État du plan en cours d'édition.
 *
 * Porte la version du projet lue au dernier chargement : c'est elle qui est renvoyée à chaque
 * écriture pour activer le verrouillage optimiste du backend (`docs/spec-complete.md` §8, cas 3).
 * Sans ça, deux onglets ouverts sur le même plan s'écraseraient silencieusement.
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'

import * as api from '@/api/client'
import type { Project, Room } from '@/api/types'

export const usePlanStore = defineStore('plan', () => {
  const project = ref<Project | null>(null)
  const selectedRoomId = ref<number | null>(null)
  const selectedFaceLabel = ref<string | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  /** Vrai quand une écriture a été refusée pour cause de version périmée. */
  const conflict = ref(false)

  function currentRoom(): Room | null {
    if (!project.value) return null
    return project.value.rooms.find((room) => room.id === selectedRoomId.value) ?? null
  }

  async function load(projectId: number): Promise<void> {
    loading.value = true
    error.value = null
    conflict.value = false
    try {
      project.value = await api.readProject(projectId)
      if (selectedRoomId.value === null && project.value.rooms.length > 0) {
        selectedRoomId.value = project.value.rooms[0]?.id ?? null
      }
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : String(caught)
    } finally {
      loading.value = false
    }
  }

  /**
   * Exécute une écriture en propageant la version connue, puis recharge.
   *
   * Un 409 n'est pas transformé en erreur générique : il a une signification métier précise
   * (« quelqu'un a modifié le plan »), et l'interface doit proposer de recharger.
   */
  async function write<T>(action: (version: number) => Promise<T>): Promise<T | null> {
    if (!project.value) return null
    error.value = null
    conflict.value = false
    try {
      const result = await action(project.value.version)
      await load(project.value.id)
      return result
    } catch (caught) {
      if (caught instanceof api.ApiError && caught.isConflict) {
        conflict.value = true
        error.value = caught.detail
      } else {
        error.value = caught instanceof Error ? caught.message : String(caught)
      }
      return null
    }
  }

  function reset(): void {
    project.value = null
    selectedRoomId.value = null
    selectedFaceLabel.value = null
    error.value = null
    conflict.value = false
  }

  return {
    project,
    selectedRoomId,
    selectedFaceLabel,
    loading,
    error,
    conflict,
    currentRoom,
    load,
    write,
    reset,
  }
})
