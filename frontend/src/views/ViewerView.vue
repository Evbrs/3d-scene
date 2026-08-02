<script setup lang="ts">
/**
 * Viewer 3D (ticket P7) : caméras, isolement de face, transparence, capture d'image.
 *
 * Toute la géométrie vient du scene graph calculé par le backend (spec §3.1). Cette vue ne fait
 * qu'orchestrer l'affichage.
 */
import { TresCanvas } from '@tresjs/core'
import { computed, onMounted, ref, shallowRef } from 'vue'

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
} from '@/viewer/visibility'

const props = defineProps<{ projectId: string }>()

const scene = shallowRef<SceneGraph | null>(null)
const roomIndex = ref(0)
const activeCamera = ref('isometrique')
const visibility = ref<Record<string, FaceVisibility>>({})
const error = ref<string | null>(null)
const canvasHost = ref<HTMLElement | null>(null)

const room = computed(() => scene.value?.rooms[roomIndex.value] ?? null)

const faceLabels = computed(() => {
  const labels = new Set<string>()
  room.value?.nodes.forEach((node) => {
    if ('face_label' in node && node.face_label) labels.add(node.face_label)
  })
  return [...labels]
})

const camera = computed<CameraPreset | null>(
  () => room.value?.cameras.find((preset) => preset.name === activeCamera.value) ?? null,
)

onMounted(async () => {
  try {
    scene.value = await api.readSceneGraph(Number(props.projectId))
    visibility.value = showEverything(faceLabels.value)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : String(caught)
  }
})

function cycle(label: string): void {
  visibility.value = { ...visibility.value, [label]: nextVisibility(visibility.value[label]) }
}

function isolateFace(label: string): void {
  visibility.value = isolate([label], faceLabels.value)
  const preset = room.value?.cameras.find((entry) => entry.face_label === label)
  if (preset) activeCamera.value = preset.name
}

function resetVisibility(): void {
  visibility.value = showEverything(faceLabels.value)
}

/**
 * Capture de la vue courante en PNG (spec §3.5).
 *
 * `preserveDrawingBuffer` est indispensable : sans lui, le canvas WebGL est vidé après chaque
 * rendu et `toDataURL` renvoie une image noire.
 */
function capture(): void {
  const canvas = canvasHost.value?.querySelector('canvas')
  if (!canvas) return
  const link = document.createElement('a')
  link.download = `vue-${activeCamera.value}.png`
  link.href = canvas.toDataURL('image/png')
  link.click()
}
</script>

<template>
  <section v-if="room">
    <header class="titre">
      <h1>Vue 3D — {{ room.name }}</h1>
      <p>{{ (room.floor_area_cm2 / 10000).toFixed(2) }} m² au sol</p>
    </header>

    <div class="disposition">
      <div
        ref="canvasHost"
        class="scene"
      >
        <TresCanvas
          v-if="camera"
          clear-color="#f0f2f5"
          :preserve-drawing-buffer="true"
          :window-size="false"
        >
          <TresPerspectiveCamera
            v-if="camera.kind === 'perspective'"
            :position="vec3(camera.position)"
            :look-at="vec3(camera.target)"
            :fov="camera.fov_deg ?? 50"
            :near="1"
            :far="20000"
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
            :near="-10000"
            :far="20000"
          />

          <TresAmbientLight :intensity="1.1" />
          <TresDirectionalLight
            :position="vec3([400, 800, 600])"
            :intensity="1.4"
          />

          <SceneRenderer
            :room="room"
            :visibility="visibility"
          />
        </TresCanvas>
      </div>

      <aside aria-label="Contrôles de la vue">
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
              {{ preset.name }}
            </button>
          </li>
        </ul>

        <h2>Faces</h2>
        <p class="aide">
          Trois états : visible, transparente, masquée. La transparence garde le contexte spatial
          au lieu de le supprimer.
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
                Isoler
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
            Tout afficher
          </button>
          <button
            type="button"
            data-variant="primary"
            @click="capture"
          >
            Capturer cette vue
          </button>
        </div>
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
.titre {
  display: flex;
  align-items: baseline;
  gap: 1rem;
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

.scene {
  height: 32rem;
  border: 1px solid var(--bordure);
  border-radius: 0.5rem;
  overflow: hidden;
}

.cameras {
  list-style: none;
  padding: 0;
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

.aide {
  color: var(--texte-doux);
  font-size: 0.9rem;
}

.actions {
  display: flex;
  gap: 0.75rem;
  margin-top: 1rem;
}
</style>
