<script setup lang="ts">
/**
 * Éditeur 2D (ticket P4).
 *
 * Orchestre le canvas et le panneau latéral. Toute écriture passe par le store, qui propage la
 * version du projet pour le verrouillage optimiste.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import * as api from '@/api/client'
import type { Face, FurnitureType, PlanElement } from '@/api/types'
import PlanCanvas from '@/editor/PlanCanvas.vue'
import { usePlanStore } from '@/stores/plan'

const props = defineProps<{ projectId: string }>()
const store = usePlanStore()
const route = useRoute()
const router = useRouter()

const mode = ref<'navigate' | 'draw' | 'edit'>('navigate')
const gridCm = ref(10)
const roomName = ref('Nouvelle pièce')
const catalog = ref<FurnitureType[]>([])
const canvas = ref<InstanceType<typeof PlanCanvas> | null>(null)
/** Erreurs de chargement du catalogue : distinctes des erreurs d'écriture portées par le store. */
const loadError = ref<string | null>(null)

/** Libellés lisibles : « window » n'a rien à faire dans une interface française. */
const KIND_LABELS: Record<string, string> = {
  window: 'Fenêtre',
  door_hinged: 'Porte battante',
  door_sliding: 'Porte coulissante',
  furniture: 'Mobilier',
}
const FACE_KIND_LABELS: Record<string, string> = {
  wall: 'Mur',
  floor: 'Sol',
  ceiling: 'Plafond',
}

const room = computed(() => store.currentRoom())

/**
 * Faces triées dans l'ordre de lecture du plan : les murs par leur lettre, puis sol et plafond.
 * L'ordre de création en base ne veut rien dire pour l'utilisateur.
 */
const faces = computed<Face[]>(() => {
  const rank = (label: string): number => {
    let value = 0
    for (const character of label) {
      if (!/[A-Z]/.test(character)) return 10 ** 6
      value = value * 26 + (character.charCodeAt(0) - 64)
    }
    return value - 1
  }
  return [...(room.value?.faces ?? [])].sort((a, b) => {
    if (a.kind !== b.kind) {
      const order = { wall: 0, floor: 1, ceiling: 2 }
      return order[a.kind] - order[b.kind]
    }
    return rank(a.label) - rank(b.label)
  })
})

const selectedFace = computed<Face | null>(
  () => faces.value.find((face) => face.label === store.selectedFaceLabel) ?? null,
)

const furnitureNames = computed<Record<number, string>>(() =>
  Object.fromEntries(catalog.value.map((entry) => [entry.id, entry.name])),
)

const wallLengthCm = computed(() => {
  const face = selectedFace.value
  if (!face || face.kind !== 'wall' || face.start_x_cm === null) return null
  return Math.round(
    Math.hypot(
      (face.end_x_cm ?? 0) - (face.start_x_cm ?? 0),
      (face.end_y_cm ?? 0) - (face.start_y_cm ?? 0),
    ),
  )
})

const draftElement = ref({
  kind: 'window',
  x_offset_cm: 0,
  y_offset_cm: 100,
  width_cm: 90,
  height_cm: 110,
  depth_cm: 12,
  furniture_type_id: null as number | null,
})

/** Valeurs par défaut cohérentes avec le type choisi : une porte part du sol, pas à 1 m. */
watch(
  () => draftElement.value.kind,
  (kind) => {
    if (kind === 'window') {
      Object.assign(draftElement.value, { y_offset_cm: 95, width_cm: 120, height_cm: 110, depth_cm: 12 })
    } else if (kind === 'door_hinged' || kind === 'door_sliding') {
      Object.assign(draftElement.value, { y_offset_cm: 0, width_cm: 90, height_cm: 204, depth_cm: 6 })
    } else {
      Object.assign(draftElement.value, { y_offset_cm: 0 })
    }
  },
)

watch(
  () => draftElement.value.furniture_type_id,
  (id) => {
    const type = catalog.value.find((entry) => entry.id === id)
    if (!type) return
    Object.assign(draftElement.value, {
      width_cm: type.default_width_cm,
      height_cm: type.default_height_cm,
      depth_cm: type.default_depth_cm,
    })
  },
)

/**
 * Sélection reflétée dans l'URL.
 *
 * Sans ça, revenir de la vue 3D, recharger la page ou envoyer le lien à un collègue ramenait
 * systématiquement sur la première pièce du projet, et la face en cours d'examen était perdue.
 */
watch(
  () => [store.selectedRoomId, store.selectedFaceLabel] as const,
  ([roomId, faceLabel]) => {
    const query = { ...route.query, piece: roomId ?? undefined, face: faceLabel ?? undefined }
    if (String(query.piece ?? '') === String(route.query.piece ?? '') &&
      String(query.face ?? '') === String(route.query.face ?? '')) {
      return
    }
    void router.replace({ query })
  },
)

// Sélection lue une fois, avant tout chargement : `load` retient d'office la première pièce, ce
// qui déclencherait la surveillance ci-dessus et réécrirait la chaîne de requête avant qu'on ait
// pu la lire.
const requestedRoomId = Number(route.query.piece)
const requestedFace = typeof route.query.face === 'string' ? route.query.face : ''

function applyRequestedSelection(): void {
  if (store.project?.rooms.some((candidate) => candidate.id === requestedRoomId)) {
    store.selectedRoomId = requestedRoomId
  }
  if (requestedFace !== '') store.selectedFaceLabel = requestedFace
}

onMounted(async () => {
  await store.load(Number(props.projectId))
  applyRequestedSelection()
  try {
    catalog.value = (await api.listFurnitureTypes()).items
  } catch (caught) {
    // Le catalogue n'est pas vital pour tracer un plan : on signale sans bloquer l'éditeur.
    loadError.value = `Catalogue de mobilier indisponible : ${messageOf(caught)}`
  }
})

function messageOf(caught: unknown): string {
  return caught instanceof Error ? caught.message : String(caught)
}

async function addRoom(): Promise<void> {
  const created = await store.write(
    (version) => api.createRoom(Number(props.projectId), { name: roomName.value, version }),
    (room) => store.applyRoom(room),
  )
  if (!created) return
  store.selectedRoomId = created.id
  store.selectedFaceLabel = null
  mode.value = 'draw'
}

/**
 * Enregistre un contour.
 *
 * Un raccourcissement qui supprimerait des murs porteurs d'éléments est refusé par le serveur
 * (409, `code` destructif). On ne force qu'après confirmation explicite : perdre des meubles sur
 * un glisser de souris serait la pire chose que puisse faire un éditeur de plan.
 */
async function savePolygon(polygon: number[][]): Promise<void> {
  const roomId = room.value!.id
  await store.write(
    (version) => api.updateRoom(roomId, { polygon, version }),
    (updated) => store.applyRoom(updated),
  )

  if (store.conflictKind !== 'destructive') return

  if (window.confirm(`${store.error}\n\nConfirmer la suppression ?`)) {
    await store.write(
      (version) => api.updateRoom(roomId, { polygon, version, force: true }),
      (updated) => store.applyRoom(updated),
    )
  } else {
    // Le refus n'a rien écrit côté serveur ; on relit pour rendre au canvas le contour réel.
    await store.load(Number(props.projectId))
  }
}

/**
 * Le serveur remplace le revêtement au lieu de le fusionner : n'envoyer que la couleur effaçait
 * la matière, les dimensions d'unité et le motif de pose choisis auparavant.
 */
async function saveCovering(color: string): Promise<void> {
  const face = selectedFace.value!
  await store.write(
    (version) => api.updateFaceCovering(face.id, { ...face.covering, color }, version),
    (updated) => store.applyFace(updated),
  )
}

async function addElement(): Promise<void> {
  const payload = { ...draftElement.value }
  if (payload.kind !== 'furniture') payload.furniture_type_id = null
  await store.write(
    (version) => api.createElement(selectedFace.value!.id, { ...payload, version }),
    (element) => store.applyElement(element),
  )
}

async function removeElement(elementId: number): Promise<void> {
  await store.write(
    () => api.deleteElement(elementId),
    () => store.dropElement(elementId),
  )
}

/**
 * Export PDF du dossier (plan coté et une élévation par mur).
 *
 * Le fichier est produit par un worker Celery : on demande, on sonde, on télécharge. Le blob est
 * récupéré par le client HTTP et non par un lien direct, parce que la route exige l'en-tête
 * `Authorization` que le navigateur ne joint pas à une navigation.
 */
const exporting = ref(false)
const exportMessage = ref<string | null>(null)
const exportError = ref<string | null>(null)

async function exportPdf(): Promise<void> {
  if (exporting.value) return
  exporting.value = true
  exportError.value = null
  exportMessage.value = 'Génération du dossier PDF en cours…'
  const projectId = Number(props.projectId)

  try {
    const accepted = await api.requestPdfExport(projectId)
    const produced = await api.waitForPdfExport(projectId, accepted.task_id)
    const blob = await api.downloadExport(projectId, produced.filename)

    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = produced.filename
    link.click()
    // Révoqué au tour suivant seulement : révoquer dans la foulée du clic annule le
    // téléchargement avant qu'il ait commencé sur plusieurs navigateurs.
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)

    exportMessage.value = `Dossier téléchargé (${Math.round(produced.size_bytes / 1024)} Ko).`
  } catch (caught) {
    exportMessage.value = null
    exportError.value = `Export impossible : ${messageOf(caught)}`
  } finally {
    exporting.value = false
  }
}

const savedLabel = computed(() => {
  if (store.saving) return 'Enregistrement…'
  if (!store.savedAt) return 'Aucune modification enregistrée'
  return `Enregistré à ${store.savedAt.toLocaleTimeString('fr-FR')}`
})

function describe(element: PlanElement): string {
  const kind = KIND_LABELS[element.kind] ?? element.kind
  const name =
    element.kind === 'furniture' && element.furniture_type_id
      ? (furnitureNames.value[element.furniture_type_id] ?? kind)
      : kind
  return `${name} · ${Math.round(element.width_cm)} × ${Math.round(element.height_cm)} cm à ${Math.round(element.x_offset_cm)} cm`
}
</script>

<template>
  <section v-if="store.project">
    <header class="entete">
      <div>
        <h1>{{ store.project.name }}</h1>
        <p class="sous-titre">
          Plan 2D · version {{ store.project.version }}
        </p>
      </div>
      <div class="entete-actions">
        <p
          class="etat-enregistrement"
          :data-etat="store.saving ? 'en-cours' : 'repos'"
          aria-live="polite"
        >
          {{ savedLabel }}
        </p>
        <button
          type="button"
          :disabled="exporting"
          :aria-busy="exporting"
          @click="exportPdf"
        >
          {{ exporting ? 'Génération…' : '📄 Exporter en PDF' }}
        </button>
        <RouterLink
          class="bouton-lien"
          :to="`/projets/${props.projectId}/vue-3d`"
        >
          Voir en 3D →
        </RouterLink>
      </div>
    </header>

    <p
      v-if="exportMessage"
      class="message export-bloc"
      aria-live="polite"
    >
      {{ exportMessage }}
    </p>
    <p
      v-if="exportError"
      class="message erreur-bloc"
      role="alert"
    >
      {{ exportError }}
    </p>

    <p
      v-if="store.conflictKind === 'stale'"
      class="message erreur-bloc"
      role="alert"
    >
      Le plan a été modifié ailleurs. Vos dernières modifications n'ont pas été enregistrées.
      <button
        type="button"
        @click="store.replayRefused()"
      >
        Recharger et réappliquer
      </button>
      <button
        type="button"
        @click="store.load(Number(props.projectId))"
      >
        Recharger sans réappliquer
      </button>
    </p>
    <p
      v-else-if="store.error"
      class="message erreur-bloc"
      role="alert"
    >
      {{ store.error }}
    </p>
    <p
      v-if="loadError"
      class="message erreur-bloc"
      role="alert"
    >
      {{ loadError }}
    </p>

    <div class="disposition">
      <div>
        <div
          class="barre"
          role="toolbar"
          aria-label="Outils du plan"
        >
          <label for="piece">Pièce</label>
          <select
            id="piece"
            v-model.number="store.selectedRoomId"
            @change="store.selectedFaceLabel = null"
          >
            <option
              v-for="candidate in store.project.rooms"
              :key="candidate.id"
              :value="candidate.id"
            >
              {{ candidate.name }}
            </option>
          </select>

          <span class="separateur" />

          <button
            type="button"
            :aria-pressed="mode === 'draw'"
            :disabled="!room"
            @click="mode = mode === 'draw' ? 'navigate' : 'draw'"
          >
            ✏️ Tracer
          </button>
          <button
            type="button"
            :aria-pressed="mode === 'edit'"
            :disabled="!room"
            @click="mode = mode === 'edit' ? 'navigate' : 'edit'"
          >
            ⇕ Déformer
          </button>
          <button
            type="button"
            :disabled="!room"
            @click="canvas?.fit()"
          >
            ⤢ Recadrer
          </button>

          <span class="separateur" />

          <label for="grille">Grille</label>
          <select
            id="grille"
            v-model.number="gridCm"
          >
            <option :value="1">
              1 cm
            </option>
            <option :value="5">
              5 cm
            </option>
            <option :value="10">
              10 cm
            </option>
            <option :value="25">
              25 cm
            </option>
          </select>
        </div>

        <PlanCanvas
          v-if="room"
          ref="canvas"
          :key="room.id"
          :draft-key="`${props.projectId}:${room.id}`"
          :polygon="room.polygon"
          :faces="faces"
          :room-name="room.name"
          :wall-thickness-cm="room.wall_thickness_cm"
          :selected-face-label="store.selectedFaceLabel"
          :mode="mode"
          :grid-cm="gridCm"
          :furniture-names="furnitureNames"
          @update:polygon="savePolygon"
          @select-face="store.selectedFaceLabel = $event"
          @select-element="store.selectedFaceLabel = faces.find((f) => f.id === $event.face_id)?.label ?? null"
          @finish-drawing="mode = 'navigate'"
        />
        <p
          v-else
          class="vide"
        >
          Ce projet n'a pas encore de pièce. Ajoutez-en une pour commencer à tracer.
        </p>

        <form
          class="ajout"
          @submit.prevent="addRoom"
        >
          <div class="champ">
            <label for="nom-piece">Ajouter une pièce</label>
            <input
              id="nom-piece"
              v-model="roomName"
              type="text"
              required
            >
          </div>
          <button type="submit">
            Ajouter
          </button>
        </form>
      </div>

      <aside aria-label="Propriétés">
        <h2>Faces</h2>
        <ul class="faces">
          <li
            v-for="face in faces"
            :key="face.id"
          >
            <button
              type="button"
              class="face"
              :aria-pressed="face.label === store.selectedFaceLabel"
              @click="store.selectedFaceLabel = face.label"
            >
              <strong>{{ face.label }}</strong>
              <span class="type">{{ FACE_KIND_LABELS[face.kind] }}</span>
              <span
                v-if="face.covering.color"
                class="pastille"
                :style="{ background: face.covering.color }"
                aria-hidden="true"
              />
              <span
                v-if="face.elements.length"
                class="compteur"
              >{{ face.elements.length }}</span>
            </button>
          </li>
        </ul>

        <template v-if="selectedFace">
          <h2>
            Face {{ selectedFace.label }}
            <span
              v-if="wallLengthCm"
              class="sous-titre"
            >{{ wallLengthCm }} cm</span>
          </h2>

          <div class="champ">
            <label for="couleur">Revêtement</label>
            <input
              id="couleur"
              type="color"
              class="couleur"
              :value="selectedFace.covering.color ?? '#ffffff'"
              @change="saveCovering(($event.target as HTMLInputElement).value)"
            >
          </div>

          <h3>Poser un élément</h3>
          <form @submit.prevent="addElement">
            <div class="champ">
              <label for="type-element">Type</label>
              <select
                id="type-element"
                v-model="draftElement.kind"
              >
                <option value="window">
                  Fenêtre
                </option>
                <option value="door_hinged">
                  Porte battante
                </option>
                <option value="door_sliding">
                  Porte coulissante
                </option>
                <option value="furniture">
                  Mobilier
                </option>
              </select>
            </div>

            <div
              v-if="draftElement.kind === 'furniture'"
              class="champ"
            >
              <label for="type-mobilier">Meuble</label>
              <select
                id="type-mobilier"
                v-model.number="draftElement.furniture_type_id"
                required
              >
                <option
                  :value="null"
                  disabled
                >
                  Choisir…
                </option>
                <option
                  v-for="entry in catalog"
                  :key="entry.id"
                  :value="entry.id"
                >
                  {{ entry.name }}
                </option>
              </select>
            </div>

            <div class="grille-champs">
              <div class="champ">
                <label for="x">Distance au coin (cm)</label>
                <input
                  id="x"
                  v-model.number="draftElement.x_offset_cm"
                  type="number"
                  min="0"
                  :max="wallLengthCm ?? undefined"
                >
              </div>
              <div class="champ">
                <label for="y">Hauteur du bas (cm)</label>
                <input
                  id="y"
                  v-model.number="draftElement.y_offset_cm"
                  type="number"
                  min="0"
                >
              </div>
              <div class="champ">
                <label for="l">Largeur (cm)</label>
                <input
                  id="l"
                  v-model.number="draftElement.width_cm"
                  type="number"
                  min="1"
                >
              </div>
              <div class="champ">
                <label for="h">Hauteur (cm)</label>
                <input
                  id="h"
                  v-model.number="draftElement.height_cm"
                  type="number"
                  min="1"
                >
              </div>
            </div>

            <button
              type="submit"
              data-variant="primary"
            >
              Poser sur {{ selectedFace.label }}
            </button>
          </form>

          <h3>Éléments posés</h3>
          <p
            v-if="selectedFace.elements.length === 0"
            class="vide"
          >
            Aucun élément sur cette face.
          </p>
          <ul
            v-else
            class="elements"
          >
            <li
              v-for="element in selectedFace.elements"
              :key="element.id"
            >
              <span>{{ describe(element) }}</span>
              <button
                type="button"
                @click="removeElement(element.id)"
              >
                Retirer
              </button>
            </li>
          </ul>
        </template>
        <p
          v-else
          class="vide"
        >
          Cliquez un mur sur le plan pour le sélectionner.
        </p>
      </aside>
    </div>
  </section>
  <p v-else-if="store.loading">
    Chargement du plan…
  </p>
  <p
    v-else-if="store.error"
    class="message erreur-bloc"
    role="alert"
  >
    {{ store.error }}
  </p>
  <p
    v-else
    class="vide"
  >
    Ce projet est introuvable.
  </p>
</template>

<style scoped>
.entete {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 1rem;
  margin-bottom: 0.75rem;
}

.entete h1 {
  margin: 0;
}

.entete-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.75rem;
}

.etat-enregistrement {
  margin: 0;
  color: var(--texte-doux);
  font-size: 0.9rem;
  font-variant-numeric: tabular-nums;
}

.etat-enregistrement[data-etat='en-cours'] {
  color: var(--accent);
  font-weight: 600;
}

.sous-titre {
  margin: 0;
  color: var(--texte-doux);
  font-size: 0.95rem;
  font-weight: 400;
}

.bouton-lien {
  padding: 0.45rem 0.9rem;
  border: 1px solid var(--accent);
  border-radius: 0.35rem;
  text-decoration: none;
  font-weight: 600;
}

.message {
  padding: 0.6rem 0.85rem;
  border-radius: 0.35rem;
}

.erreur-bloc {
  background: #fdecea;
  color: #7a1010;
  font-weight: 600;
}

/* Contraste 7:1 minimum sur fond clair (WCAG AAA) : le message d'export est une information
   d'état, il doit rester lisible pour tout le monde. */
.export-bloc {
  background: #e8f1fb;
  color: #10365e;
  font-weight: 600;
}

.disposition {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 23rem;
  gap: 1.5rem;
  align-items: start;
}

@media (max-width: 68rem) {
  .disposition {
    grid-template-columns: 1fr;
  }
}

.barre {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.6rem;
  margin-bottom: 0.75rem;
  padding: 0.5rem 0.6rem;
  border: 1px solid var(--bordure);
  border-radius: 0.4rem;
  background: #fafbfc;
}

.barre label {
  margin: 0;
  font-size: 0.9rem;
}

.barre select {
  width: auto;
}

.separateur {
  width: 1px;
  height: 1.5rem;
  background: var(--bordure);
}

button[aria-pressed='true'] {
  background: var(--accent);
  border-color: var(--accent);
  color: #ffffff;
}

.ajout {
  display: flex;
  align-items: flex-end;
  flex-wrap: wrap;
  gap: 0.75rem;
  margin-top: 1rem;
  max-width: 32rem;
}

/* Téléphone : la grille à deux colonnes du panneau de propriétés devient illisible, et les
   boutons de la barre d'outils passent sous la taille de cible tactile recommandée. */
@media (max-width: 40rem) {
  .grille-champs {
    grid-template-columns: 1fr;
  }

  .barre button,
  .elements button {
    min-height: 2.75rem;
  }

  .ajout {
    align-items: stretch;
  }
}

.ajout .champ {
  flex: 1;
  margin: 0;
}

.champ {
  margin-bottom: 0.75rem;
}

.couleur {
  height: 2.4rem;
  padding: 0.15rem;
}

.grille-champs {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.5rem 0.75rem;
}

.faces {
  list-style: none;
  padding: 0;
  margin: 0 0 1.25rem;
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
}

.face {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
}

.type {
  color: var(--texte-doux);
  font-size: 0.85rem;
}

button[aria-pressed='true'] .type {
  color: #dbe6ff;
}

.pastille {
  width: 0.85rem;
  height: 0.85rem;
  border-radius: 50%;
  border: 1px solid rgba(0, 0, 0, 0.25);
}

.compteur {
  min-width: 1.15rem;
  padding: 0 0.25rem;
  border-radius: 0.6rem;
  background: #e2e8f0;
  color: #1b222b;
  font-size: 0.78rem;
  text-align: center;
}

.elements {
  list-style: none;
  padding: 0;
  margin: 0;
}

.elements li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.4rem 0;
  border-bottom: 1px solid var(--bordure);
}

.vide {
  color: var(--texte-doux);
}

h2,
h3 {
  margin-bottom: 0.5rem;
}
</style>
