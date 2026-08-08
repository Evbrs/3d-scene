<script setup lang="ts">
/**
 * Viewer 3D (ticket P7) : caméras, isolement de faces, transparence, coupe, capture, partage.
 *
 * Toute la géométrie vient du scene graph calculé par le backend (spec §3.1). Cette vue
 * n'orchestre que l'affichage — l'assemblage Three.js vit dans `viewer/build.ts`, l'éclairage
 * dans `viewer/ViewerStage.vue`.
 */
import { OrbitControls } from '@tresjs/cientos'
import { TresCanvas } from '@tresjs/core'
import { ACESFilmicToneMapping, type Camera, type Group, Vector3 } from 'three'
import { computed, onMounted, ref, shallowRef, watch, watchEffect } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import * as api from '@/api/client'
import type { CameraPreset, SceneGraph, SceneRoom } from '@/api/types'
import SceneRenderer from '@/viewer/SceneRenderer.vue'
import ViewerStage from '@/viewer/ViewerStage.vue'
import { type Framing, boundsOf, frameBox, wallFacings } from '@/viewer/build'
import {
  type ColorOverrides,
  type ColorTarget,
  applyColorOverrides,
  colorTargets,
  effectiveColor,
  mergedColors,
} from '@/viewer/colors'
import { capturePlan, captureFileName, downloadDataUrl, nextFrames } from '@/viewer/capture'
import { vec3 } from '@/viewer/vectors'
import {
  VISIBILITY_LABELS,
  type FaceVisibility,
  effectiveVisibility,
  faceKey,
  faceLabelOf,
  horizontalCut,
  isolate,
  nextVisibility,
  showEverything,
  toggleSelection,
  toViewState,
  unscope,
} from '@/viewer/visibility'

const props = defineProps<{ projectId: string }>()
const route = useRoute()
const router = useRouter()

const scene = shallowRef<SceneGraph | null>(null)
const roomIndex = ref(0)
const activeCamera = ref('isometrique')
const visibility = ref<Record<string, FaceVisibility>>({})
const selection = ref<string[]>([])
const error = ref<string | null>(null)
const loading = ref(false)
const canvasHost = ref<HTMLElement | null>(null)
const shareUrl = ref<string | null>(null)

/** Vue d'une pièce, ou du logement entier — les coordonnées du scene graph sont déjà absolues. */
const wholeDwelling = ref(false)
const autoHideFacing = ref(true)
const cutHeightCm = ref(Number.POSITIVE_INFINITY)
const colorOverrides = ref<ColorOverrides>({})
const cameraPosition = ref<[number, number, number] | null>(null)

const room = computed(() => scene.value?.rooms[roomIndex.value] ?? null)

/** Ce qui est effectivement construit : une pièce, ou toutes. */
const shownRooms = computed<SceneRoom[]>(() => {
  if (!scene.value) return []
  return wholeDwelling.value ? [...scene.value.rooms] : room.value ? [room.value] : []
})

const renderedRooms = computed(() => applyColorOverrides(shownRooms.value, colorOverrides.value))

/** Les étiquettes de face ne sont uniques que dans une pièce : on les préfixe dès qu'il y en a plusieurs. */
const roomScoped = computed(() => wholeDwelling.value)

interface FaceRow {
  key: string
  label: string
  roomName: string
}

/**
 * Les faces à lister, pièce par pièce.
 *
 * Le tri se fait **dans** chaque pièce puis les listes sont concaténées : un comparateur qui
 * renverrait 0 pour deux faces de pièces différentes ne serait pas transitif, et l'ordre obtenu
 * dépendrait de l'algorithme de tri.
 */
const faceRows = computed<FaceRow[]>(() => {
  const horizontal = (label: string): number => (label === 'SOL' || label === 'PLAFOND' ? 1 : 0)
  return shownRooms.value.flatMap((current) => {
    const seen = new Set<string>()
    const rows: FaceRow[] = []
    current.nodes.forEach((node) => {
      if (!('face_label' in node) || !node.face_label || seen.has(node.face_label)) return
      seen.add(node.face_label)
      rows.push({
        key: faceKey(node.face_label, roomScoped.value ? current.id : undefined),
        label: node.face_label,
        roomName: current.name,
      })
    })
    // Murs d'abord, dans l'ordre alphabétique, puis sol et plafond.
    return rows.sort((first, second) =>
      horizontal(first.label) !== horizontal(second.label)
        ? horizontal(first.label) - horizontal(second.label)
        : first.label.localeCompare(second.label),
    )
  })
})

const faceKeys = computed(() => faceRows.value.map((row) => row.key))

const walls = computed(() => wallFacings(shownRooms.value, roomScoped.value))

/**
 * L'état réellement appliqué : les réglages de l'utilisateur, plus le masquage automatique des
 * murs qui font écran. C'est une surcouche — les trois positions choisies restent intactes.
 */
const appliedVisibility = computed(() =>
  effectiveVisibility(
    visibility.value,
    walls.value,
    autoHideFacing.value ? cameraPosition.value : null,
  ),
)

/** Vrai si la face n'est masquée que parce qu'elle fait écran, et non par choix. */
function hiddenByCamera(key: string): boolean {
  return appliedVisibility.value[key] === 'hidden' && visibility.value[key] !== 'hidden'
}

const ceilingHeightCm = computed(() => {
  const heights = shownRooms.value.map((current) => current.ceiling_height_cm)
  return heights.length > 0 ? Math.max(...heights) : 250
})

const cut = computed(() => horizontalCut(cutHeightCm.value, ceilingHeightCm.value))

// --- Caméras --------------------------------------------------------------------------------

const overview = shallowRef<Framing | null>(null)
const focus = shallowRef<[number, number, number]>([0, 0, 0])
const radiusCm = shallowRef(400)
const cameraInstance = shallowRef<Camera | null>(null)

const preset = computed<CameraPreset | null>(
  () => room.value?.cameras.find((entry) => entry.name === activeCamera.value) ?? null,
)

/** En logement complet, aucun preset backend ne convient : on cadre l'emprise réellement construite. */
const camera = computed<CameraPreset | null>(() => {
  if (!wholeDwelling.value) return preset.value
  if (!overview.value) return null
  return {
    name: 'ensemble',
    kind: 'perspective',
    position: overview.value.position,
    target: overview.value.target,
    up: [0, 1, 0],
    face_label: null,
    fov_deg: 50,
  }
})

const isOrbit = computed(() => wholeDwelling.value || activeCamera.value === 'orbite')

const cameraLabels: Record<string, string> = {
  dessus: 'Vue du dessus',
  isometrique: 'Isométrique',
  orbite: 'Orbite libre',
}

function labelFor(entry: CameraPreset): string {
  return cameraLabels[entry.name] ?? `Élévation ${entry.face_label}`
}

/**
 * Recadre la scène sur ce qui vient d'être construit.
 *
 * L'emprise sert à deux choses : la caméra d'ensemble du mode logement, et le dimensionnement de
 * la carte d'ombre. La mesurer sur les objets construits évite de supposer quoi que ce soit des
 * cotes du plan.
 */
function onBuilt(group: Group): void {
  const box = boundsOf(group)
  if (box.isEmpty()) return
  const centre = box.getCenter(new Vector3())
  focus.value = [centre.x, centre.y, centre.z]
  radiusCm.value = Math.max(100, box.getSize(new Vector3()).length() / 2)
  overview.value = frameBox(box, 50, aspectRatio())
}

function aspectRatio(): number {
  const host = canvasHost.value
  return host && host.clientHeight > 0 ? host.clientWidth / host.clientHeight : 1.6
}

/**
 * Applique le point de vue à la caméra montée.
 *
 * Le canevas portait un `:key` sur la caméra active : chaque clic détruisait le contexte WebGL et
 * faisait recompiler tous les shaders. Sans lui, la même caméra est réutilisée — encore faut-il
 * lui pousser sa nouvelle position, ce que fait cet effet. L'orbite est exclue : les contrôles y
 * sont propriétaires de la caméra.
 */
watchEffect(() => {
  const instance = cameraInstance.value
  const target = camera.value
  if (!instance || !target || isOrbit.value) return
  instance.position.set(target.position[0], target.position[1], target.position[2])
  instance.up.set(target.up[0], target.up[1], target.up[2])
  instance.lookAt(new Vector3(target.target[0], target.target[1], target.target[2]))
  instance.updateMatrixWorld()
})

// --- Chargement -----------------------------------------------------------------------------

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
    // Les couleurs choisies ont été écrites : la réponse du serveur fait désormais foi.
    colorOverrides.value = {}
    const keys = faceKeys.value
    visibility.value = preserveVisibility
      ? Object.fromEntries(keys.map((key) => [key, previous[key] ?? 'visible']))
      : defaultVisibility(keys)
    selection.value = []
    cutHeightCm.value = ceilingHeightCm.value
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
 * La sélection passe par l'URL pour survivre à un rechargement et pour qu'un lien désigne bien la
 * pièce montrée.
 */
function selectRoom(index: number): void {
  roomIndex.value = clampRoomIndex(index)
  // Les étiquettes de face et les élévations appartiennent à la pièce : les reprendre telles
  // quelles masquerait des murs au hasard dans la nouvelle.
  resetVisibility()
  cutHeightCm.value = ceilingHeightCm.value
  if (!room.value?.cameras.some((entry) => entry.name === activeCamera.value)) {
    activeCamera.value = 'isometrique'
  }
  void router.replace({ query: { ...route.query, piece: roomIndex.value } })
}

function toggleWholeDwelling(): void {
  wholeDwelling.value = !wholeDwelling.value
  // Les clés changent de forme en passant d'un mode à l'autre : un état repris tel quel
  // masquerait des faces au hasard.
  resetVisibility()
  cutHeightCm.value = ceilingHeightCm.value
}

/**
 * Le plafond est masqué au départ.
 *
 * Une pièce fermée par ses six faces ne montre rien de son intérieur : la vue d'ensemble
 * ressemble à un bloc plein. Retirer le plafond est la convention des vues de plan 3D, et il
 * reste réaffichable en un clic.
 */
function defaultVisibility(keys: string[]): Record<string, FaceVisibility> {
  const state = showEverything(keys)
  keys.forEach((key) => {
    if (faceLabelOf(key) === 'PLAFOND') state[key] = 'hidden'
  })
  return state
}

onMounted(() => load())

// Passer d'un projet à l'autre réutilise le composant : sans ça, la scène du projet précédent
// resterait affichée. Volontairement sur le projet et non sur l'URL complète, dont la chaîne de
// requête porte maintenant la pièce sélectionnée.
watch(() => props.projectId, () => load(true))

// --- Visibilité -----------------------------------------------------------------------------

function cycle(key: string): void {
  visibility.value = { ...visibility.value, [key]: nextVisibility(visibility.value[key]) }
}

function toggleSelected(key: string): void {
  selection.value = toggleSelection(selection.value, key)
}

/**
 * Isole la sélection : une face, deux, ou davantage (spec §3.4 — « la face A, ou A+B, ou
 * l'ensemble »). `isolate` acceptait déjà un tableau ; personne ne lui en donnait jamais qu'un.
 */
function isolateSelection(): void {
  visibility.value = isolate(selection.value, faceKeys.value)
  if (selection.value.length === 1) {
    const only = faceLabelOf(selection.value[0]!)
    const elevation = room.value?.cameras.find((entry) => entry.face_label === only)
    if (elevation && !wholeDwelling.value) activeCamera.value = elevation.name
  }
}

function resetVisibility(): void {
  visibility.value = defaultVisibility(faceKeys.value)
  selection.value = []
}

// --- Couleurs -------------------------------------------------------------------------------

const paletteTargets = computed<ColorTarget[]>(() => colorTargets(shownRooms.value))

function slotColor(target: ColorTarget, slot: string): string {
  return effectiveColor(target, slot, colorOverrides.value) ?? '#9aa0a6'
}

/** Aperçu immédiat, sans attendre le serveur : c'est ce qui rend le choix d'une teinte utilisable. */
function previewColor(target: ColorTarget, slot: string, color: string): void {
  colorOverrides.value = {
    ...colorOverrides.value,
    [target.elementId]: { ...(colorOverrides.value[target.elementId] ?? {}), [slot]: color },
  }
}

/**
 * Enregistre la teinte choisie.
 *
 * Le scene graph ne publie pas la version du projet : l'écriture part donc sans numéro de
 * version. Le serveur ne peut alors pas refuser une écriture périmée, mais il incrémente bien la
 * version — un éditeur ouvert en parallèle recevra son 409 à son prochain enregistrement, ce qui
 * est le comportement voulu.
 */
async function saveColor(target: ColorTarget, slot: string, color: string): Promise<void> {
  previewColor(target, slot, color)
  error.value = null
  try {
    await api.updateElement(target.elementId, {
      colors: mergedColors(target, colorOverrides.value),
    })
  } catch (caught) {
    error.value = `Couleur non enregistrée : ${caught instanceof Error ? caught.message : String(caught)}`
  }
}

// --- Captures et export ---------------------------------------------------------------------

function canvasElement(): HTMLCanvasElement | null {
  return canvasHost.value?.querySelector('canvas') ?? null
}

/**
 * Capture PNG de la vue courante (spec §3.5).
 *
 * `preserveDrawingBuffer` est indispensable : sans lui, le canvas WebGL est vidé après chaque
 * rendu et `toDataURL` renvoie une image noire.
 */
function capture(): void {
  const canvas = canvasElement()
  if (!canvas) return
  const name = wholeDwelling.value ? 'logement' : (room.value?.name ?? 'vue')
  downloadDataUrl(captureFileName(name, activeCamera.value), canvas.toDataURL('image/png'))
}

const capturing = ref(false)

/**
 * Une capture par mur (spec §3.5 : « ce mécanisme, appliqué à chaque vue par face, te donne
 * gratuitement les images pour l'export PDF détaillé par mur »).
 *
 * Chaque prise attend deux trames : changer de caméra ne redessine pas le canevas dans la foulée,
 * et lire le tampon trop tôt renverrait l'image précédente.
 */
async function captureFaces(): Promise<void> {
  const current = room.value
  const canvas = canvasElement()
  if (!current || !canvas || capturing.value) return

  capturing.value = true
  const restoreCamera = activeCamera.value
  const restoreVisibility = { ...visibility.value }
  try {
    for (const shot of capturePlan(current.name, current.cameras)) {
      activeCamera.value = shot.cameraName
      visibility.value = isolate(shot.faceLabel ? [shot.faceLabel] : [], faceKeys.value)
      await nextFrames(3)
      downloadDataUrl(shot.fileName, canvas.toDataURL('image/png'))
    }
  } finally {
    activeCamera.value = restoreCamera
    visibility.value = restoreVisibility
    capturing.value = false
  }
}

/**
 * Export PDF du dossier : plan coté et une élévation par mur (spec §3.5).
 *
 * Le fichier est produit par un worker Celery — on demande, on sonde, on télécharge. Le contenu
 * passe par le client HTTP et non par un lien direct : la route exige l'en-tête `Authorization`,
 * que le navigateur ne joint pas à une navigation.
 */
const exporting = ref(false)
const exportMessage = ref<string | null>(null)

async function exportPdf(): Promise<void> {
  if (exporting.value) return
  exporting.value = true
  error.value = null
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
    error.value = `Export impossible : ${caught instanceof Error ? caught.message : String(caught)}`
  } finally {
    exporting.value = false
  }
}

/**
 * L'état à partager, toujours en étiquettes nues.
 *
 * La page publique n'affiche qu'une pièce et relit des étiquettes sans préfixe : partager depuis
 * le mode logement complet lui enverrait des clés « 12:A » qu'elle ne reconnaîtrait dans aucune
 * de ses listes, et elle masquerait tout.
 */
function shareableVisibility(): Record<string, FaceVisibility> {
  const current = room.value
  return wholeDwelling.value && current ? unscope(visibility.value, current.id) : visibility.value
}

async function share(): Promise<void> {
  error.value = null
  try {
    const created = await api.createSharedView(Number(props.projectId), {
      ...toViewState(shareableVisibility(), activeCamera.value),
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
        <h1>{{ wholeDwelling ? 'Logement complet' : room.name }}</h1>
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
        <!-- Aucun `:key` sur le canevas : il détruisait le contexte WebGL à chaque changement de
             point de vue, et faisait recompiler tous les shaders. -->
        <TresCanvas
          v-if="camera"
          clear-color="#eef1f5"
          :preserve-drawing-buffer="true"
          :window-size="false"
          :shadows="true"
          :tone-mapping="ACESFilmicToneMapping"
          :tone-mapping-exposure="1"
        >
          <TresPerspectiveCamera
            v-if="camera.kind === 'perspective'"
            ref="cameraInstance"
            :position="vec3(camera.position)"
            :look-at="vec3(camera.target)"
            :fov="camera.fov_deg ?? 50"
            :near="1"
            :far="40000"
          />
          <TresOrthographicCamera
            v-else
            ref="cameraInstance"
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

          <ViewerStage
            :focus="focus"
            :radius-cm="radiusCm"
            :cut-height-cm="cut"
            :shadows="true"
            @camera-moved="cameraPosition = $event"
          />

          <SceneRenderer
            :rooms="renderedRooms"
            :visibility="appliedVisibility"
            :room-scoped="roomScoped"
            :shadows="true"
            @built="onBuilt"
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
            :disabled="wholeDwelling"
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

        <p
          v-if="scene && scene.rooms.length > 1"
          class="champ"
        >
          <button
            type="button"
            :aria-pressed="wholeDwelling"
            @click="toggleWholeDwelling"
          >
            🏠 Logement complet
          </button>
        </p>

        <h2>Point de vue</h2>
        <ul
          v-if="!wholeDwelling"
          class="cameras"
        >
          <li
            v-for="entry in room.cameras"
            :key="entry.name"
          >
            <button
              type="button"
              :aria-pressed="entry.name === activeCamera"
              @click="activeCamera = entry.name"
            >
              {{ labelFor(entry) }}
            </button>
          </li>
        </ul>
        <p
          v-if="isOrbit"
          class="aide"
        >
          Glisser pour tourner, molette pour zoomer.
        </p>

        <h2>Coupe et murs</h2>
        <div class="champ">
          <label for="coupe">
            Coupe horizontale :
            {{ cut === null ? 'aucune' : `${Math.round(cut)} cm` }}
          </label>
          <input
            id="coupe"
            v-model.number="cutHeightCm"
            type="range"
            min="20"
            :max="ceilingHeightCm"
            step="5"
          >
          <p class="aide">
            Tout ce qui dépasse cette hauteur est retiré du rendu. Poussée au maximum, la coupe est
            débranchée.
          </p>
        </div>
        <div class="champ">
          <label class="case">
            <input
              v-model="autoHideFacing"
              type="checkbox"
            >
            Masquer les murs qui font face à la caméra
          </label>
          <p class="aide">
            Réglage d'affichage seulement : vos trois positions restent celles du tableau.
          </p>
        </div>

        <h2>Faces</h2>
        <p class="aide">
          Trois états : visible, transparente, masquée. La transparence garde le contexte spatial
          au lieu de le supprimer. Cochez plusieurs faces pour les isoler ensemble.
        </p>
        <table>
          <thead>
            <tr>
              <th scope="col">
                <span class="sr">Sélection</span>
              </th>
              <th scope="col">
                Face
              </th>
              <th scope="col">
                État
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in faceRows"
              :key="row.key"
            >
              <td>
                <input
                  :id="`selection-${row.key}`"
                  type="checkbox"
                  :checked="selection.includes(row.key)"
                  :aria-label="`Sélectionner la face ${row.label}${wholeDwelling ? ` de ${row.roomName}` : ''}`"
                  @change="toggleSelected(row.key)"
                >
              </td>
              <th scope="row">
                <label :for="`selection-${row.key}`">
                  {{ row.label }}
                  <small v-if="wholeDwelling">{{ row.roomName }}</small>
                  <!-- Le masquage automatique ne change pas l'état choisi : on le signale au lieu
                       de laisser croire que le réglage a bougé tout seul. -->
                  <small v-if="hiddenByCamera(row.key)">fait écran, masqué</small>
                </label>
              </th>
              <td>
                <button
                  type="button"
                  class="etat"
                  :data-etat="visibility[row.key] ?? 'visible'"
                  :aria-label="`Face ${row.label} : ${VISIBILITY_LABELS[visibility[row.key] ?? 'visible']}. Changer d'état.`"
                  @click="cycle(row.key)"
                >
                  {{ VISIBILITY_LABELS[visibility[row.key] ?? 'visible'] }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>

        <div class="actions">
          <button
            type="button"
            data-variant="primary"
            :disabled="selection.length === 0"
            @click="isolateSelection"
          >
            Isoler la sélection ({{ selection.length }})
          </button>
          <button
            type="button"
            @click="resetVisibility"
          >
            Réinitialiser
          </button>
        </div>

        <template v-if="paletteTargets.length > 0">
          <h2>Couleurs</h2>
          <p class="aide">
            Une teinte par emplacement du meuble. Le changement s'applique tout de suite et est
            enregistré au relâchement.
          </p>
          <ul class="palette">
            <li
              v-for="target in paletteTargets"
              :key="target.elementId"
            >
              <span class="palette-nom">{{ target.label }}</span>
              <span
                v-for="entry in target.slots"
                :key="entry.slot"
                class="palette-slot"
              >
                <label :for="`couleur-${target.elementId}-${entry.slot}`">{{ entry.slot }}</label>
                <input
                  :id="`couleur-${target.elementId}-${entry.slot}`"
                  type="color"
                  :value="slotColor(target, entry.slot)"
                  @input="previewColor(target, entry.slot, ($event.target as HTMLInputElement).value)"
                  @change="saveColor(target, entry.slot, ($event.target as HTMLInputElement).value)"
                >
              </span>
            </li>
          </ul>
        </template>

        <h2>Sortie</h2>
        <div class="actions">
          <button
            type="button"
            data-variant="primary"
            @click="capture"
          >
            📷 Capturer
          </button>
          <button
            type="button"
            :disabled="capturing || wholeDwelling"
            :aria-busy="capturing"
            @click="captureFaces"
          >
            {{ capturing ? 'Captures…' : '🖼 Une image par mur' }}
          </button>
          <button
            type="button"
            @click="share"
          >
            🔗 Partager
          </button>
          <button
            type="button"
            :disabled="exporting"
            :aria-busy="exporting"
            @click="exportPdf"
          >
            {{ exporting ? 'Génération…' : '📄 Exporter en PDF' }}
          </button>
        </div>

        <p
          v-if="exportMessage"
          class="export"
          aria-live="polite"
        >
          {{ exportMessage }}
        </p>

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

.champ input[type='range'] {
  width: 100%;
}

.case {
  display: flex;
  align-items: center;
  gap: 0.45rem;
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

th small {
  display: block;
  font-weight: 400;
  color: var(--texte-doux);
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

.palette {
  list-style: none;
  padding: 0;
  margin: 0;
}

.palette li {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
  padding: 0.4rem 0;
  border-bottom: 1px solid var(--bordure);
}

.palette-nom {
  flex: 1 1 8rem;
  font-weight: 600;
}

.palette-slot {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.85rem;
}

.palette-slot input[type='color'] {
  width: 2.2rem;
  height: 1.8rem;
  padding: 0;
  border: 1px solid var(--bordure);
  border-radius: 0.25rem;
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

/* Contraste 7:1 minimum sur fond clair (WCAG AAA) : c'est un message d'état, il doit rester
   lisible pour tout le monde. */
.export {
  margin-top: 0.75rem;
  padding: 0.5rem 0.7rem;
  border-radius: 0.35rem;
  background: #e8f1fb;
  color: #10365e;
  font-weight: 600;
}

.sr {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
}
</style>
