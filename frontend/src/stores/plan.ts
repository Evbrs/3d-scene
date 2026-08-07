/**
 * État du plan en cours d'édition.
 *
 * Porte la version du projet lue au dernier chargement : c'est elle qui est renvoyée à chaque
 * écriture pour activer le verrouillage optimiste du backend (`docs/spec-complete.md` §8, cas 3).
 * Sans ça, deux onglets ouverts sur le même plan s'écraseraient silencieusement.
 */
import { defineStore } from 'pinia'
import { computed, ref, shallowRef } from 'vue'

import * as api from '@/api/client'
import type { ConflictKind } from '@/api/client'
import type { Face, PlanElement, Project, Room } from '@/api/types'

/** Écriture paramétrée par la version du projet connue au moment de l'appel. */
type WriteAction<T> = (version: number) => Promise<T>

export const usePlanStore = defineStore('plan', () => {
  const project = ref<Project | null>(null)
  const selectedRoomId = ref<number | null>(null)
  const selectedFaceLabel = ref<string | null>(null)
  const loading = ref(false)
  const saving = ref(false)
  /** Horodatage du dernier enregistrement accepté, affiché à l'utilisateur. */
  const savedAt = ref<Date | null>(null)
  const error = ref<string | null>(null)
  /** Nature du dernier conflit : `null` tant qu'aucune écriture n'a été refusée. */
  const conflictKind = ref<ConflictKind | null>(null)
  /**
   * Écriture refusée, conservée sous forme rejouable.
   *
   * C'est ce qui permet de proposer « recharger et réappliquer » : sans la charge d'origine, la
   * seule issue offerte à l'utilisateur après un conflit était de refaire sa modification.
   */
  const refused = shallowRef<(() => Promise<unknown>) | null>(null)
  const hasRefusedWrite = computed(() => refused.value !== null)

  function currentRoom(): Room | null {
    if (!project.value) return null
    return project.value.rooms.find((room) => room.id === selectedRoomId.value) ?? null
  }

  async function load(projectId: number): Promise<void> {
    loading.value = true
    error.value = null
    conflictKind.value = null
    refused.value = null
    try {
      project.value = await api.readProject(projectId)
      const rooms = project.value.rooms
      // La pièce sélectionnée peut avoir disparu (suppression dans un autre onglet) : y rester
      // afficherait un plan vide sans rien expliquer.
      if (!rooms.some((room) => room.id === selectedRoomId.value)) {
        selectedRoomId.value = rooms[0]?.id ?? null
        selectedFaceLabel.value = null
      }
    } catch (caught) {
      error.value = messageOf(caught)
    } finally {
      loading.value = false
    }
  }

  /**
   * Exécute une écriture en propageant la version connue.
   *
   * `apply` intègre la réponse du serveur à l'arbre local. Sans lui, chaque sommet déplacé
   * déclenchait un GET complet du projet — 37,9 Ko mesurés sur dix pièces, à chaque relâchement
   * de souris. Les écritures qui restructurent l'arbre (suppressions) l'omettent volontairement
   * et repassent par un rechargement.
   *
   * Un 409 n'est pas transformé en erreur générique : il a une signification métier précise, et
   * l'interface doit pouvoir distinguer « quelqu'un a modifié le plan » de « cette modification
   * détruirait des éléments ».
   */
  async function write<T>(action: WriteAction<T>, apply?: (result: T) => void): Promise<T | null> {
    if (!project.value) return null
    const projectId = project.value.id
    saving.value = true
    error.value = null
    conflictKind.value = null
    refused.value = null

    let result: T
    try {
      result = await action(project.value.version)
    } catch (caught) {
      saving.value = false
      // Seule l'écriture refusée est rejouable. Enregistrer aussi les échecs survenus *après*
      // l'acceptation du serveur ferait, au rejeu, une seconde écriture bien réelle.
      refused.value = () => write(action, apply)
      if (caught instanceof api.ApiError && caught.isConflict) {
        // Un serveur antérieur au champ `code` ne dit rien : on suppose le cas non destructif.
        conflictKind.value = caught.conflictKind ?? 'stale'
      }
      error.value = messageOf(caught)
      return null
    }

    try {
      if (apply) {
        apply(result)
        bumpVersion()
      } else {
        await load(projectId)
      }
      savedAt.value = new Date()
      return result
    } finally {
      saving.value = false
    }
  }

  /**
   * Recharge le plan puis rejoue l'écriture refusée.
   *
   * Resynchronise au passage la version du projet : c'est aussi le filet de `bumpVersion`, dont
   * l'hypothèse (« toute écriture acceptée incrémente la version de un ») ne survivrait pas à un
   * changement de comportement du backend sans que l'utilisateur ait un moyen de s'en sortir.
   */
  async function replayRefused(): Promise<void> {
    const replay = refused.value
    if (!replay || !project.value) return
    // `load` remet `refused` à zéro : la relecture doit précéder, la référence est déjà capturée.
    await load(project.value.id)
    if (error.value !== null) return
    await replay()
  }

  /**
   * Suit localement l'incrément de version que `_claim_project` applique côté serveur à chaque
   * écriture acceptée. C'est le prix à payer pour ne plus recharger l'arbre : les réponses
   * d'écriture ne portent pas la version du projet.
   */
  function bumpVersion(): void {
    if (project.value) project.value.version += 1
  }

  function applyRoom(room: Room): void {
    if (!project.value) return
    const index = project.value.rooms.findIndex((candidate) => candidate.id === room.id)
    if (index === -1) project.value.rooms.push(room)
    else project.value.rooms[index] = room
  }

  function applyFace(face: Face): void {
    const room = project.value?.rooms.find((candidate) => candidate.id === face.room_id)
    if (!room) return
    const index = room.faces.findIndex((candidate) => candidate.id === face.id)
    if (index !== -1) room.faces[index] = face
  }

  function applyElement(element: PlanElement): void {
    const face = allFaces().find((candidate) => candidate.id === element.face_id)
    if (!face) return
    const index = face.elements.findIndex((candidate) => candidate.id === element.id)
    if (index === -1) face.elements.push(element)
    else face.elements[index] = element
  }

  function dropElement(elementId: number): void {
    for (const face of allFaces()) {
      const index = face.elements.findIndex((candidate) => candidate.id === elementId)
      if (index !== -1) {
        face.elements.splice(index, 1)
        return
      }
    }
  }

  function allFaces(): Face[] {
    return (project.value?.rooms ?? []).flatMap((room) => room.faces)
  }

  function reset(): void {
    project.value = null
    selectedRoomId.value = null
    selectedFaceLabel.value = null
    savedAt.value = null
    error.value = null
    conflictKind.value = null
    refused.value = null
  }

  return {
    project,
    selectedRoomId,
    selectedFaceLabel,
    loading,
    saving,
    savedAt,
    error,
    conflictKind,
    hasRefusedWrite,
    currentRoom,
    load,
    write,
    replayRefused,
    applyRoom,
    applyFace,
    applyElement,
    dropElement,
    reset,
  }
})

function messageOf(caught: unknown): string {
  return caught instanceof Error ? caught.message : String(caught)
}
