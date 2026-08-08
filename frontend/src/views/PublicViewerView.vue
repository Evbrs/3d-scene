<script setup lang="ts">
/**
 * Lecture publique d'une vue partagée (ticket P8).
 *
 * Aucune authentification : cette page est accessible à qui possède le lien. Elle est donc en
 * lecture seule, et n'affiche que ce que l'API publique renvoie — jamais d'information sur le
 * propriétaire du projet.
 *
 * C'est la vitrine du produit, et son visiteur est le client de l'artisan : il l'ouvre depuis un
 * SMS, sur un téléphone. La mise en page en tient compte, et le rendu doit y être aussi fidèle
 * que dans l'éditeur — c'est sur cette image que le devis se signe.
 */
import { OrbitControls } from '@tresjs/cientos'
import { TresCanvas } from '@tresjs/core'
import { ACESFilmicToneMapping, type Group, Vector3 } from 'three'
import { computed, onMounted, ref, shallowRef } from 'vue'

import * as api from '@/api/client'
import type { CameraPreset, SceneGraph } from '@/api/types'
import SceneRenderer from '@/viewer/SceneRenderer.vue'
import ViewerStage from '@/viewer/ViewerStage.vue'
import { boundsOf } from '@/viewer/build'
import { vec3 } from '@/viewer/vectors'
import { type FaceVisibility, fromViewState, showEverything, type ViewState } from '@/viewer/visibility'

const props = defineProps<{ token: string }>()

const scene = shallowRef<SceneGraph | null>(null)
const projectName = ref('')
const visibility = ref<Record<string, FaceVisibility>>({})
const cameraName = ref('isometrique')
const roomIndex = ref(0)
const error = ref<string | null>(null)
const loading = ref(true)

const focus = shallowRef<[number, number, number]>([0, 0, 0])
const radiusCm = shallowRef(400)

const room = computed(() => scene.value?.rooms[roomIndex.value] ?? null)
const rooms = computed(() => (room.value ? [room.value] : []))
const camera = computed<CameraPreset | null>(
  () => room.value?.cameras.find((preset) => preset.name === cameraName.value) ?? null,
)
const isOrbit = computed(() => cameraName.value === 'orbite')

/** L'emprise réelle dimensionne la lumière et sa carte d'ombre : rien n'est supposé du plan. */
function onBuilt(group: Group): void {
  const box = boundsOf(group)
  if (box.isEmpty()) return
  const centre = box.getCenter(new Vector3())
  focus.value = [centre.x, centre.y, centre.z]
  radiusCm.value = Math.max(100, box.getSize(new Vector3()).length() / 2)
}

onMounted(async () => {
  try {
    const view = await api.readPublicView(props.token)
    scene.value = view.scene
    projectName.value = view.project_name

    const state = view.state as unknown as ViewState & { room_index?: number }
    // La vue partagée désigne une pièce précise ; l'ignorer montrait toujours la première.
    const shared = Number(state.room_index)
    roomIndex.value =
      Number.isInteger(shared) && shared >= 0 && shared < view.scene.rooms.length ? shared : 0

    const labels = new Set<string>()
    view.scene.rooms[roomIndex.value]?.nodes.forEach((node) => {
      if ('face_label' in node && node.face_label) labels.add(node.face_label)
    })
    const allLabels = [...labels]

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
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <section v-if="room && camera">
    <h1>{{ projectName }}</h1>
    <p class="mention">
      {{ room.name }} · vue partagée en lecture seule.
    </p>

    <div class="scene">
      <TresCanvas
        clear-color="#f0f2f5"
        :preserve-drawing-buffer="true"
        :window-size="false"
        :shadows="true"
        :tone-mapping="ACESFilmicToneMapping"
        :tone-mapping-exposure="1"
      >
        <TresPerspectiveCamera
          v-if="camera.kind === 'perspective'"
          :position="vec3(camera.position)"
          :look-at="vec3(camera.target)"
          :fov="camera.fov_deg ?? 50"
          :near="1"
          :far="20000"
        />
        <!-- `near` doit rester positif : à -10000, le plan de coupe passe derrière la caméra et
             le mur opposé se dessine par-dessus la face regardée. -->
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
          :far="20000"
        />

        <OrbitControls
          v-if="isOrbit"
          :target="camera.target"
          :enable-damping="true"
        />

        <ViewerStage
          :focus="focus"
          :radius-cm="radiusCm"
          :shadows="true"
        />

        <SceneRenderer
          :rooms="rooms"
          :visibility="visibility"
          :shadows="true"
          @built="onBuilt"
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
  <p v-else-if="loading">
    Chargement de la vue partagée…
  </p>
  <p v-else>
    Cette vue partagée ne contient aucune pièce à afficher.
  </p>
</template>

<style scoped>
.mention {
  color: var(--texte-doux);
}

.scene {
  /* Prend la hauteur réellement disponible. Une hauteur figée de 34 rem réduisait la scène à une
     vignette sur un téléphone tenu à l'horizontale, et la faisait déborder à la verticale. */
  height: clamp(16rem, 72vh, 44rem);
  border: 1px solid var(--bordure);
  border-radius: 0.5rem;
  overflow: hidden;
}

@media (max-width: 40rem) {
  h1 {
    font-size: 1.4rem;
  }

  .scene {
    /* Le lien de partage s'ouvre en pleine page : on rend au dessin la marge de `main`. */
    margin-inline: -0.75rem;
    border-inline: 0;
    border-radius: 0;
  }
}
</style>
