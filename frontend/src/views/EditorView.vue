<script setup lang="ts">
/**
 * Éditeur 2D (ticket P4).
 *
 * L'écran orchestre : le canvas Konva saisit le contour, le panneau latéral pose les
 * revêtements et les éléments. Toute écriture passe par le store, qui propage la version du
 * projet pour le verrouillage optimiste.
 */
import { computed, onMounted, ref, watch } from 'vue'

import * as api from '@/api/client'
import type { Face, FurnitureType } from '@/api/types'
import PlanCanvas from '@/editor/PlanCanvas.vue'
import { usePlanStore } from '@/stores/plan'

const props = defineProps<{ projectId: string }>()
const store = usePlanStore()

const mode = ref<'navigate' | 'draw' | 'edit'>('navigate')
const gridCm = ref(10)
const roomName = ref('Nouvelle pièce')
const catalog = ref<FurnitureType[]>([])

const room = computed(() => store.currentRoom())
const faces = computed(() => room.value?.faces ?? [])
const selectedFace = computed<Face | null>(
  () => faces.value.find((face) => face.label === store.selectedFaceLabel) ?? null,
)

/** Formulaire de pose d'un élément sur la face sélectionnée. */
const draftElement = ref({
  kind: 'window',
  x_offset_cm: 0,
  y_offset_cm: 100,
  width_cm: 90,
  height_cm: 110,
  depth_cm: 12,
  furniture_type_id: null as number | null,
})

onMounted(async () => {
  await store.load(Number(props.projectId))
  const page = await api.listFurnitureTypes()
  catalog.value = page.items
})

watch(
  () => draftElement.value.furniture_type_id,
  (id) => {
    const type = catalog.value.find((entry) => entry.id === id)
    if (!type) return
    draftElement.value.width_cm = type.default_width_cm
    draftElement.value.height_cm = type.default_height_cm
    draftElement.value.depth_cm = type.default_depth_cm
  },
)

async function addRoom(): Promise<void> {
  await store.write((version) =>
    api.createRoom(Number(props.projectId), { name: roomName.value, version }),
  )
  const rooms = store.project?.rooms ?? []
  store.selectedRoomId = rooms[rooms.length - 1]?.id ?? null
  mode.value = 'draw'
}

async function savePolygon(polygon: number[][], force = false): Promise<void> {
  if (!room.value) return
  await store.write((version) =>
    api.updateRoom(room.value!.id, { polygon, version, force }),
  )
}

async function saveCovering(color: string): Promise<void> {
  if (!selectedFace.value) return
  await store.write((version) =>
    api.updateFaceCovering(selectedFace.value!.id, { color }, version),
  )
}

async function addElement(): Promise<void> {
  if (!selectedFace.value) return
  await store.write((version) =>
    api.createElement(selectedFace.value!.id, { ...draftElement.value, version }),
  )
}

async function removeElement(elementId: number): Promise<void> {
  await store.write(() => api.deleteElement(elementId))
}

/** Le backend refuse de détruire des éléments sans confirmation explicite. */
async function confirmDestructivePolygon(polygon: number[][]): Promise<void> {
  if (window.confirm(store.error ?? 'Confirmer la suppression des éléments concernés ?')) {
    await savePolygon(polygon, true)
  }
}

const pendingPolygon = ref<number[][] | null>(null)

async function onPolygonChange(polygon: number[][]): Promise<void> {
  pendingPolygon.value = polygon
  await savePolygon(polygon)
  if (store.error?.includes('force')) {
    await confirmDestructivePolygon(polygon)
  }
}
</script>

<template>
  <section v-if="store.project">
    <header class="titre">
      <h1>{{ store.project.name }} — plan 2D</h1>
      <p class="version">
        Version {{ store.project.version }}
      </p>
    </header>

    <p
      v-if="store.conflict"
      class="erreur"
      role="alert"
    >
      Le plan a été modifié ailleurs. Vos dernières modifications n'ont pas été enregistrées.
      <button
        type="button"
        @click="store.load(Number(props.projectId))"
      >
        Recharger
      </button>
    </p>
    <p
      v-else-if="store.error"
      class="erreur"
      role="alert"
    >
      {{ store.error }}
    </p>

    <div class="disposition">
      <div>
        <div
          class="barre-outils"
          role="toolbar"
          aria-label="Outils du plan"
        >
          <label for="piece">Pièce</label>
          <select
            id="piece"
            v-model.number="store.selectedRoomId"
          >
            <option
              v-for="candidate in store.project.rooms"
              :key="candidate.id"
              :value="candidate.id"
            >
              {{ candidate.name }}
            </option>
          </select>

          <button
            type="button"
            :aria-pressed="mode === 'draw'"
            :disabled="!room"
            @click="mode = mode === 'draw' ? 'navigate' : 'draw'"
          >
            Tracer le contour
          </button>
          <button
            type="button"
            :aria-pressed="mode === 'edit'"
            :disabled="!room"
            @click="mode = mode === 'edit' ? 'navigate' : 'edit'"
          >
            Déplacer les sommets
          </button>

          <label for="grille">Grille (cm)</label>
          <select
            id="grille"
            v-model.number="gridCm"
          >
            <option :value="1">
              1
            </option>
            <option :value="5">
              5
            </option>
            <option :value="10">
              10
            </option>
            <option :value="25">
              25
            </option>
          </select>
        </div>

        <PlanCanvas
          v-if="room"
          :polygon="room.polygon"
          :selected-face-label="store.selectedFaceLabel"
          :mode="mode"
          :grid-cm="gridCm"
          @update:polygon="onPolygonChange"
          @select-face="store.selectedFaceLabel = $event"
          @finish-drawing="mode = 'navigate'"
        />
        <p v-else>
          Ce projet n'a pas encore de pièce.
        </p>

        <form
          class="ajout-piece"
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

      <aside aria-label="Propriétés de la face">
        <h2>Faces</h2>
        <ul class="faces">
          <li
            v-for="face in faces"
            :key="face.id"
          >
            <button
              type="button"
              :aria-pressed="face.label === store.selectedFaceLabel"
              @click="store.selectedFaceLabel = face.label"
            >
              {{ face.label }} ({{ face.kind }}) — {{ face.elements.length }} élément(s)
            </button>
          </li>
        </ul>

        <template v-if="selectedFace">
          <h2>Revêtement de {{ selectedFace.label }}</h2>
          <div class="champ">
            <label for="couleur">Couleur</label>
            <input
              id="couleur"
              type="color"
              :value="selectedFace.covering.color ?? '#ffffff'"
              @change="saveCovering(($event.target as HTMLInputElement).value)"
            >
          </div>

          <h2>Poser un élément</h2>
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
              >
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
                <label for="x">Position X (cm)</label>
                <input
                  id="x"
                  v-model.number="draftElement.x_offset_cm"
                  type="number"
                  min="0"
                >
              </div>
              <div class="champ">
                <label for="y">Hauteur (cm)</label>
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
                <label for="h">Hauteur objet (cm)</label>
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

          <h2>Éléments posés</h2>
          <ul>
            <li
              v-for="element in selectedFace.elements"
              :key="element.id"
            >
              {{ element.kind }} — {{ element.width_cm }}×{{ element.height_cm }} cm
              <button
                type="button"
                @click="removeElement(element.id)"
              >
                Retirer
              </button>
            </li>
          </ul>
        </template>
      </aside>
    </div>
  </section>
  <p v-else-if="store.loading">
    Chargement du plan…
  </p>
</template>

<style scoped>
.titre {
  display: flex;
  align-items: baseline;
  gap: 1rem;
}

.version {
  color: var(--texte-doux);
}

.disposition {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 22rem;
  gap: 1.5rem;
  align-items: start;
}

@media (max-width: 60rem) {
  .disposition {
    grid-template-columns: 1fr;
  }
}

.barre-outils {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 0.75rem;
}

.barre-outils label {
  margin: 0;
}

.barre-outils select {
  width: auto;
}

button[aria-pressed='true'] {
  background: var(--accent);
  border-color: var(--accent);
  color: #ffffff;
}

.ajout-piece {
  display: flex;
  align-items: flex-end;
  gap: 0.75rem;
  margin-top: 1rem;
  max-width: 30rem;
}

.ajout-piece .champ {
  flex: 1;
}

.champ {
  margin-bottom: 0.75rem;
}

.grille-champs {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.5rem;
}

.faces {
  list-style: none;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
}
</style>
