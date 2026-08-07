<script setup lang="ts">
/**
 * Viewer 3D (ticket P7) : caméras, isolement de face, transparence, capture, partage.
 *
 * Toute la géométrie vient du scene graph calculé par le backend (spec §3.1). Cette vue
 * n'orchestre que l'affichage.
 */
import { OrbitControls } from '@tresjs/cientos'
import { TresCanvas } from '@tresjs/core'
import { computed, onMounted, ref, shallowRef, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import * as api from '@/api/client'
import type { CameraPreset, SceneGraph } from '@/api/types'
import SceneRenderer from '@/viewer/SceneRenderer.vue'
import { vec3 } from '@/viewer/vectors'
import {
  VISIBILITY_LABELS,
  type FaceVisibility,
  isolate,
  nextVisibility,
  showEverything,
  toViewState,
} from '@/viewer/visibility'

const props = defineProps<{ projectId: string }>()
const route = useRoute()
const router = useRouter()

const scene = shallowRef<SceneGraph | null>(null)
const roomIndex = ref(0)
const activeCamera = ref('isometrique')
const visibility = ref<Record<string, FaceVisibility>>({})
const error = ref<string | null>(null)
const loading = ref(false)
const canvasHost = ref<HTMLElement | null>(null)
const shareUrl = ref<string | null>(null)

const room = computed(() => scene.value?.rooms[roomIndex.value] ?? null)

const faceLabels = computed(() => {
  const labels: string[] = []
  room.value?.nodes.forEach((node) => {
    if ('face_label' in node && node.face_label && !labels.includes(node.face_label)) {
      labels.push(node.face_label)
    }
  })
  // Murs d'abord, dans l'ordre alphabétique, puis sol et plafond.
  return labels.sort((a, b) => {
    const horizontal = (label: string): number => (label === 'SOL' || label === 'PLAFOND' ? 1 : 0)
    if (horizontal(a) !== horizontal(b)) return horizontal(a) - horizontal(b)
    return a.localeCompare(b)
  })
})

const camera = computed<CameraPreset | null>(
  () => room.value?.cameras.find((preset) => preset.name === activeCamera.value) ?? null,
)

const isOrbit = computed(() => activeCamera.value === 'orbite')

const cameraLabels: Record<string, string> = {
  dessus: 'Vue du dessus',
  isometrique: 'Isométrique',
  orbite: 'Orbite libre',
}

function labelFor(preset: CameraPreset): string {
  return cameraLabels[preset.name] ?? `Élévation ${preset.face_label}`
}

/**
 * Charge la scène.
 *
 * `preserveVisibility` garde les réglages d'affichage lors d'un rechargement : sinon, toute
 * modification du plan remettrait à zéro l'isolement que l'utilisateur venait de composer.
 */
async function load(preserveVisibility = false): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const previous = { ...visibility.value }
    scene.value = await api.readSceneGraph(Number(props.projectId))
    // La pièce demandée peut avoir disparu ou l'URL être bricolée : sans borne, `room` devient
    // `undefined` et la vue reste bloquée sur « Chargement de la scène… ».
    roomIndex.value = clampRoomIndex(readRoomIndexFromUrl() ?? roomIndex.value)
    const labels = faceLabels.value
    visibility.value = preserveVisibility
      ? Object.fromEntries(labels.map((label) => [label, previous[label] ?? 'visible']))
      : defaultVisibility(labels)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : String(caught)
  } finally {
    loading.value = false
  }
}

function clampRoomIndex(index: number): number {
  const count = scene.value?.rooms.length ?? 0
  if (count === 0) return 0
  return Math.min(Math.max(index, 0), count - 1)
}

function readRoomIndexFromUrl(): number | null {
  const raw = Number(route.query.piece)
  return Number.isFinite(raw) ? raw : null
}

/**
 * Change de pièce.
 *
 * `roomIndex` n'était jamais réaffecté : le viewer restait figé sur la première pièce, et un
 * projet de plusieurs pièces n'était visible qu'au tiers. La sélection passe par l'URL pour
 * survivre à un rechargement et pour qu'un lien désigne bien la pièce montrée.
 */
function selectRoom(index: number): void {
  roomIndex.value = clampRoomIndex(index)
  // Les étiquettes de face et les élévations appartiennent à la pièce : les reprendre telles
  // quelles masquerait des murs au hasard dans la nouvelle.
  visibility.value = defaultVisibility(faceLabels.value)
  if (!room.value?.cameras.some((preset) => preset.name === activeCamera.value)) {
    activeCamera.value = 'isometrique'
  }
  void router.replace({ query: { ...route.query, piece: roomIndex.value } })
}

/**
 * Le plafond est masqué au départ.
 *
 * Une pièce fermée par ses six faces ne montre rien de son intérieur : la vue d'ensemble
 * ressemble à un bloc plein. Retirer le plafond est la convention des vues de plan 3D, et il
 * reste réaffichable en un clic.
 */
function defaultVisibility(labels: string[]): Record<string, FaceVisibility> {
  const state = showEverything(labels)
  if ('PLAFOND' in state) state.PLAFOND = 'hidden'
  return state
}

onMounted(() => load())

// Passer d'un projet à l'autre réutilise le composant : sans ça, la scène du projet précédent
// resterait affichée. Volontairement sur le projet et non sur l'URL complète, dont la chaîne de
// requête porte maintenant la pièce sélectionnée — la relire déclencherait un rechargement
// complet de la scène à chaque changement de pièce.
watch(() => props.projectId, () => load(true))

function cycle(label: string): void {
  visibility.value = { ...visibility.value, [label]: nextVisibility(visibility.value[label]) }
}

function isolateFace(label: string): void {
  visibility.value = isolate([label], faceLabels.value)
  const preset = room.value?.cameras.find((entry) => entry.face_label === label)
  if (preset) activeCamera.value = preset.name
}

function resetVisibility(): void {
  visibility.value = defaultVisibility(faceLabels.value)
}

/**
 * Capture PNG de la vue courante (spec §3.5).
 *
 * `preserveDrawingBuffer` est indispensable : sans lui, le canvas WebGL est vidé après chaque
 * rendu et `toDataURL` renvoie une image noire.
 */
function capture(): void {
  const canvas = canvasHost.value?.querySelector('canvas')
  if (!canvas) return
  const link = document.createElement('a')
  link.download = `${room.value?.name ?? 'vue'}-${activeCamera.value}.png`
  link.href = canvas.toDataURL('image/png')
  link.click()
}

async function share(): Promise<void> {
  error.value = null
  try {
    const created = await api.createSharedView(Number(props.projectId), {
      ...toViewState(visibility.value, activeCamera.value),
      room_index: roomIndex.value,
    })
    shareUrl.value = `${window.location.origin}/partage/${created.token}`
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : String(caught)
  }
}
</script>

<template>
  <section v-if="room">
    <header class="entete">
      <div>
        <h1>{{ room.name }}</h1>
        <p class="sous-titre">
          Vue 3D · {{ (room.floor_area_cm2 / 10000).toFixed(2) }} m² au sol
        </p>
      </div>
      <div class="entete-actions">
        <button
          type="button"
          :disabled="loading"
          @click="load(true)"
        >
          ⟳ Recharger
        </button>
        <RouterLink
          class="bouton-lien"
          :to="`/projets/${props.projectId}/plan`"
        >
          ← Retour au plan 2D
        </RouterLink>
      </div>
    </header>

    <div class="disposition">
      <div
        ref="canvasHost"
        class="scene"
      >
        <TresCanvas
          v-if="camera"
          :key="activeCamera"
          clear-color="#eef1f5"
          :preserve-drawing-buffer="true"
          :window-size="false"
        >
          <TresPerspectiveCamera
            v-if="camera.kind === 'perspective'"
            :position="vec3(camera.position)"
            :look-at="vec3(camera.target)"
            :fov="camera.fov_deg ?? 50"
            :near="1"
            :far="40000"
          />
          <TresOrthographicCamera
            v-else
            :position="vec3(camera.position)"
            :look-at="vec3(camera.target)"
            :up="vec3(camera.up)"
            :left="-(camera.half_width_cm ?? 100)"
            :right="camera.half_width_cm ?? 100"
            :top="camera.half_height_cm ?? 100"
            :bottom="-(camera.half_height_cm ?? 100)"
            :near="0.1"
            :far="40000"
          />

          <OrbitControls
            v-if="isOrbit"
            :target="camera.target"
            :enable-damping="true"
          />

          <TresAmbientLight :intensity="1.4" />
          <TresDirectionalLight
            :position="vec3([600, 1200, 900])"
            :intensity="2"
          />
          <TresDirectionalLight
            :position="vec3([-600, 500, -400])"
            :intensity="0.8"
          />

          <SceneRenderer
            :room="room"
            :visibility="visibility"
          />
        </TresCanvas>
      </div>

      <aside aria-label="Contrôles de la vue">
        <div
          v-if="scene && scene.rooms.length > 1"
          class="champ"
        >
          <label for="piece-3d">Pièce</label>
          <select
            id="piece-3d"
            :value="roomIndex"
            @change="selectRoom(Number(($event.target as HTMLSelectElement).value))"
          >
            <option
              v-for="(candidate, index) in scene.rooms"
              :key="candidate.id"
              :value="index"
            >
              {{ candidate.name }}
            </option>
          </select>
        </div>

        <h2>Point de vue</h2>
        <ul class="cameras">
          <li
            v-for="preset in room.cameras"
            :key="preset.name"
          >
            <button
              type="button"
              :aria-pressed="preset.name === activeCamera"
              @click="activeCamera = preset.name"
            >
              {{ labelFor(preset) }}
            </button>
          </li>
        </ul>
        <p
          v-if="isOrbit"
          class="aide"
        >
          Glisser pour tourner, molette pour zoomer.
        </p>

        <h2>Faces</h2>
        <p class="aide">
          Trois états : visible, transparente, masquée. La transparence garde le contexte spatial
          au lieu de le supprimer. Le plafond est masqué au départ pour laisser voir l'intérieur.
        </p>
        <table>
          <thead>
            <tr>
              <th scope="col">
                Face
              </th>
              <th scope="col">
                État
              </th>
              <th scope="col">
                <span class="sr">Action</span>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="label in faceLabels"
              :key="label"
            >
              <th scope="row">
                {{ label }}
              </th>
              <td>
                <button
                  type="button"
                  class="etat"
                  :data-etat="visibility[label] ?? 'visible'"
                  :aria-label="`Face ${label} : ${VISIBILITY_LABELS[visibility[label] ?? 'visible']}. Changer d'état.`"
                  @click="cycle(label)"
                >
                  {{ VISIBILITY_LABELS[visibility[label] ?? 'visible'] }}
                </button>
              </td>
              <td>
                <button
                  type="button"
                  @click="isolateFace(label)"
                >
                  Isoler
                </button>
              </td>
            </tr>
          </tbody>
        </table>

        <div class="actions">
          <button
            type="button"
            @click="resetVisibility"
          >
            Réinitialiser
          </button>
          <button
            type="button"
            data-variant="primary"
            @click="capture"
          >
            📷 Capturer
          </button>
          <button
            type="button"
            @click="share"
          >
            🔗 Partager
          </button>
        </div>

        <p
          v-if="shareUrl"
          class="partage"
        >
          <label for="lien-partage">Lien public (lecture seule, sans compte)</label>
          <input
            id="lien-partage"
            :value="shareUrl"
            readonly
            @focus="($event.target as HTMLInputElement).select()"
          >
        </p>
        <p
          v-if="error"
          class="erreur"
          role="alert"
        >
          {{ error }}
        </p>
      </aside>
    </div>
  </section>

  <p
    v-else-if="error"
    class="erreur"
    role="alert"
  >
    {{ error }}
  </p>
  <p v-else>
    Chargement de la scène…
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
  gap: 0.6rem;
}

.sous-titre {
  margin: 0;
  color: var(--texte-doux);
}

.bouton-lien {
  padding: 0.45rem 0.9rem;
  border: 1px solid var(--bordure);
  border-radius: 0.35rem;
  text-decoration: none;
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

.scene {
  /* Suit la hauteur disponible : 38 rem figés couvraient plus que l'écran d'un téléphone. */
  height: clamp(20rem, 68vh, 38rem);
  border: 1px solid var(--bordure);
  border-radius: 0.5rem;
  overflow: hidden;
  background: #eef1f5;
}

.champ {
  margin-bottom: 1rem;
}

.cameras {
  list-style: none;
  padding: 0;
  margin: 0 0 0.5rem;
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
}

button[aria-pressed='true'] {
  background: var(--accent);
  border-color: var(--accent);
  color: #ffffff;
}

table {
  border-collapse: collapse;
  width: 100%;
}

th,
td {
  border-bottom: 1px solid var(--bordure);
  padding: 0.35rem 0.4rem;
  text-align: left;
}

.etat[data-etat='visible'] {
  border-color: #0a5c2c;
  color: #0a5c2c;
}

.etat[data-etat='transparent'] {
  border-color: #8a6d0f;
  color: #8a6d0f;
}

.etat[data-etat='hidden'] {
  border-color: var(--bordure);
  color: var(--texte-doux);
}

.aide {
  color: var(--texte-doux);
  font-size: 0.9rem;
}

.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem;
  margin-top: 1rem;
}

.partage {
  margin-top: 0.75rem;
}

.sr {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
}
</style>
