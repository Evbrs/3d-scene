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
import type { BatchOperation, BatchResponse, ConflictKind } from '@/api/client'
import type { Face, PlanElement, Project, Room } from '@/api/types'
import { chunkOperations } from '@/editor/operations'

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
  async function write<T>(
    action: WriteAction<T>,
    apply?: (result: T) => void,
    // `bumpVersion` suppose « une écriture acceptée = une version de plus ». Un lot en applique
    // cent et n'en consomme qu'une, mais il **renvoie** sa version : le suivi local doit alors
    // s'effacer devant la valeur reçue plutôt que d'ajouter un cran par-dessus.
    options: { versionFromServer?: boolean } = {},
  ): Promise<T | null> {
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
        if (options.versionFromServer !== true) bumpVersion()
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

  /**
   * Intègre un élément renvoyé par le serveur, quel que soit son ancrage.
   *
   * `face_id` est le discriminant (spec §10, A4) : nul, l'élément vit dans `Room.free_elements`
   * et non dans une face. Le chercher malgré tout dans les faces le ferait disparaître de
   * l'affichage sans la moindre erreur — le meuble serait bien en base, et invisible.
   */
  function applyElement(element: PlanElement): void {
    const collection =
      element.face_id === null
        ? (project.value?.rooms.find((room) => room.id === element.room_id)?.free_elements ?? null)
        : (allFaces().find((face) => face.id === element.face_id)?.elements ?? null)
    if (!collection) return
    const index = collection.findIndex((candidate) => candidate.id === element.id)
    if (index === -1) collection.push(element)
    else collection[index] = element
  }

  function dropElement(elementId: number): void {
    for (const collection of allElementCollections()) {
      const index = collection.findIndex((candidate) => candidate.id === elementId)
      if (index !== -1) {
        collection.splice(index, 1)
        return
      }
    }
  }

  function allFaces(): Face[] {
    return (project.value?.rooms ?? []).flatMap((room) => room.faces)
  }

  /** Les deux endroits où vit un élément : les faces, et le mobilier libre de chaque pièce. */
  function allElementCollections(): PlanElement[][] {
    return (project.value?.rooms ?? []).flatMap((room) => [
      ...room.faces.map((face) => face.elements),
      room.free_elements,
    ])
  }

  /**
   * Applique la réponse d'un lot à l'arbre local.
   *
   * La version rendue par le serveur est **la sienne**, pas un incrément deviné : un lot ne
   * l'augmente que d'un cran quel que soit son nombre d'opérations, et `bumpVersion` — qui
   * suppose une écriture par requête — donnerait le bon résultat par accident. Écrire la valeur
   * reçue supprime l'accident.
   */
  function applyBatchResponse(response: BatchResponse): void {
    for (const result of response.results) {
      if (result.status === 'deleted') {
        if (result.element_id !== null) dropElement(result.element_id)
        if (result.room_id !== null) dropRoom(result.room_id)
        continue
      }
      if (result.room) applyRoom(result.room)
      if (result.element) applyElement(result.element)
    }
    if (project.value) project.value.version = response.version
  }

  function dropRoom(roomId: number): void {
    if (!project.value) return
    const index = project.value.rooms.findIndex((room) => room.id === roomId)
    if (index === -1) return
    project.value.rooms.splice(index, 1)
    if (selectedRoomId.value === roomId) {
      selectedRoomId.value = project.value.rooms[0]?.id ?? null
      selectedFaceLabel.value = null
    }
  }

  /**
   * Envoie un lot d'opérations, découpé sous la borne du serveur.
   *
   * Les paquets partent l'un après l'autre : au-delà de cent opérations, ils ne forment plus une
   * seule transaction. C'est le prix de la borne, assumé — deux transactions appliquées valent
   * mieux qu'un lot entier refusé. Chaque paquet part avec la version **mise à jour par le
   * précédent**, sans quoi le second serait systématiquement périmé.
   *
   * Passe par `write` pour hériter du traitement des conflits et du rejeu : un lot refusé se
   * rejoue comme n'importe quelle écriture.
   */
  async function writeBatch(operations: BatchOperation[]): Promise<BatchResponse[] | null> {
    if (operations.length === 0 || !project.value) return []
    const projectId = project.value.id
    const responses: BatchResponse[] = []

    for (const chunk of chunkOperations(operations)) {
      const response = await write(
        (version) => api.applyBatch(projectId, chunk, version),
        (result) => applyBatchResponse(result),
        { versionFromServer: true },
      )
      if (!response) return null
      responses.push(response)
    }
    return responses
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
    writeBatch,
    replayRefused,
    applyRoom,
    applyFace,
    applyElement,
    applyBatchResponse,
    dropElement,
    dropRoom,
    reset,
  }
})

function messageOf(caught: unknown): string {
  return caught instanceof Error ? caught.message : String(caught)
}
