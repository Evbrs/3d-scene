<script setup lang="ts">
/**
 * Lecture publique d'une vue partagée (ticket P8).
 *
 * Aucune authentification : cette page est accessible à qui possède le lien. Elle est donc en
 * lecture seule, et n'affiche que ce que l'API publique renvoie — jamais d'information sur le
 * propriétaire du projet.
 */
import { TresCanvas } from '@tresjs/core'
import { computed, onMounted, ref, shallowRef } from 'vue'

import * as api from '@/api/client'
import type { CameraPreset, SceneGraph } from '@/api/types'
import SceneRenderer from '@/viewer/SceneRenderer.vue'
import { vec3 } from '@/viewer/vectors'
import { type FaceVisibility, fromViewState, showEverything, type ViewState } from '@/viewer/visibility'

const props = defineProps<{ token: string }>()

const scene = shallowRef<SceneGraph | null>(null)
const projectName = ref('')
const visibility = ref<Record<string, FaceVisibility>>({})
const cameraName = ref('isometrique')
const error = ref<string | null>(null)

const room = computed(() => scene.value?.rooms[0] ?? null)
const camera = computed<CameraPreset | null>(
  () => room.value?.cameras.find((preset) => preset.name === cameraName.value) ?? null,
)

onMounted(async () => {
  try {
    const view = await api.readPublicView(props.token)
    scene.value = view.scene
    projectName.value = view.project_name

    const labels = new Set<string>()
    view.scene.rooms[0]?.nodes.forEach((node) => {
      if ('face_label' in node && node.face_label) labels.add(node.face_label)
    })
    const allLabels = [...labels]

    const state = view.state as unknown as ViewState
    visibility.value = state.visible_faces
      ? fromViewState(state, allLabels)
      : showEverything(allLabels)
    cameraName.value = state.camera_preset ?? 'isometrique'
  } catch (caught) {
    error.value =
      caught instanceof api.ApiError && caught.status === 404
        ? "Ce lien de partage n'est plus valide."
        : caught instanceof Error
          ? caught.message
          : String(caught)
  }
})
</script>

<template>
  <section v-if="room && camera">
    <h1>{{ projectName }}</h1>
    <p class="mention">
      Vue partagée en lecture seule.
    </p>

    <div class="scene">
      <TresCanvas
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
  </section>

  <p
    v-else-if="error"
    class="erreur"
    role="alert"
  >
    {{ error }}
  </p>
  <p v-else>
    Chargement de la vue partagée…
  </p>
</template>

<style scoped>
.mention {
  color: var(--texte-doux);
}

.scene {
  height: 34rem;
  border: 1px solid var(--bordure);
  border-radius: 0.5rem;
  overflow: hidden;
}
</style>
