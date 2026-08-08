<script setup lang="ts">
/**
 * Éclairage et réglages du moteur de rendu.
 *
 * L'éclairage précédent écrêtait : ambiante à 1,4 plus deux directionnelles à 2 et 0,8, en sortie
 * linéaire. Tout ce qui dépassait 1 était ramené à blanc — un beige clair et un blanc cassé
 * devenaient indiscernables. C'est rédhibitoire pour un outil dont le seul but est de choisir un
 * revêtement.
 *
 * Le remède est celui de la photographie : baisser la lumière, compenser par un environnement
 * qui remplit les ombres, et compresser le haut de la dynamique au lieu de le couper
 * (`ACESFilmicToneMapping`, réglé sur le canevas par la vue appelante). L'environnement est
 * `RoomEnvironment`, une scène procédurale livrée avec Three.js : aucun fichier à charger, donc
 * rien à autoriser dans la CSP de production.
 */
import {
  type Camera,
  DirectionalLight,
  PCFSoftShadowMap,
  PMREMGenerator,
  Plane,
  Vector3,
  WebGLRenderer,
} from 'three'
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js'
import { useLoop, useTresContext } from '@tresjs/core'
import { computed, onUnmounted, shallowRef, watch, watchEffect } from 'vue'

import { vec3 } from '@/viewer/vectors'

const props = withDefaults(
  defineProps<{
    /** Centre de ce qu'on éclaire, en coordonnées monde. */
    focus: readonly number[]
    /** Rayon de l'emprise : dimensionne la lumière et sa carte d'ombre. */
    radiusCm: number
    /** Hauteur de la coupe horizontale, ou `null` si la scène n'est pas coupée. */
    cutHeightCm?: number | null
    shadows?: boolean
  }>(),
  { cutHeightCm: null, shadows: true },
)

const emit = defineEmits<{ cameraMoved: [[number, number, number]] }>()

const { renderer, scene } = useTresContext()

/**
 * Le moteur de rendu, une fois réellement créé.
 *
 * `useTresContext().renderer.instance` n'est pas réactif : lu au montage, il peut précéder la
 * création du contexte WebGL, et l'environnement ne serait alors jamais généré. `isInitialized`,
 * lui, l'est — c'est le seul signal fiable.
 */
const webgl = shallowRef<WebGLRenderer | null>(null)

watchEffect(() => {
  if (!renderer.isInitialized.value) return
  webgl.value = renderer.instance instanceof WebGLRenderer ? renderer.instance : null
})

const invalidate = (): void => renderer.invalidate()

/** L'ambiante ne fait plus que déboucher les ombres : le reste vient de l'environnement. */
const AMBIENT_INTENSITY = 0.28
const KEY_INTENSITY = 1.7
const FILL_INTENSITY = 0.32
const ENVIRONMENT_INTENSITY = 0.62

const keyLight = shallowRef<DirectionalLight | null>(null)

const keyPosition = computed(() => {
  const distance = Math.max(props.radiusCm, 100) * 1.8
  return vec3([
    (props.focus[0] ?? 0) + distance * 0.55,
    (props.focus[1] ?? 0) + distance * 0.95,
    (props.focus[2] ?? 0) + distance * 0.6,
  ])
})

const fillPosition = computed(() => {
  const distance = Math.max(props.radiusCm, 100) * 1.4
  return vec3([
    (props.focus[0] ?? 0) - distance * 0.7,
    (props.focus[1] ?? 0) + distance * 0.4,
    (props.focus[2] ?? 0) - distance * 0.55,
  ])
})

/**
 * L'environnement est généré une fois pour le moteur de rendu courant.
 *
 * `PMREMGenerator` compile la scène de référence en une carte d'irradiance : c'est ce qui donne à
 * un mur blanc une teinte différente selon son orientation, et donc ce qui rend deux blancs
 * cassés distinguables sans ajouter de lumière.
 */
let environmentGenerator: PMREMGenerator | null = null

watchEffect(() => {
  const instance = webgl.value
  if (!instance || environmentGenerator) return

  instance.shadowMap.enabled = props.shadows
  instance.shadowMap.type = PCFSoftShadowMap

  environmentGenerator = new PMREMGenerator(instance)
  const reference = new RoomEnvironment()
  scene.value.environment = environmentGenerator.fromScene(reference, 0.04).texture
  scene.value.environmentIntensity = ENVIRONMENT_INTENSITY
  reference.dispose()
  invalidate()
})

/**
 * Coupe horizontale (spec §3.4, « coupe de plan »).
 *
 * Le plan est posé sur le moteur de rendu et non sur chaque matériau : le clipping global n'exige
 * pas `localClippingEnabled` et surtout ne dépend pas du pool de matériaux, qui est reconstruit à
 * chaque changement de scène.
 */
watchEffect(() => {
  const instance = webgl.value
  if (!instance) return
  instance.clippingPlanes =
    props.cutHeightCm === null || props.cutHeightCm === undefined
      ? []
      : [new Plane(new Vector3(0, -1, 0), props.cutHeightCm)]
  invalidate()
})

/**
 * Dimensionne la carte d'ombre sur l'emprise réelle.
 *
 * Une caméra d'ombre trop large étale ses texels et l'ombre devient une tache ; trop étroite, et
 * les ombres s'arrêtent net au milieu de la pièce.
 */
watch(
  [keyLight, () => props.radiusCm, () => props.focus, () => props.shadows],
  () => {
    const light = keyLight.value
    if (!light) return
    const radius = Math.max(props.radiusCm, 100)
    light.castShadow = props.shadows
    light.target.position.set(props.focus[0] ?? 0, props.focus[1] ?? 0, props.focus[2] ?? 0)
    light.target.updateMatrixWorld()

    const camera = light.shadow.camera
    camera.left = -radius * 1.25
    camera.right = radius * 1.25
    camera.top = radius * 1.25
    camera.bottom = -radius * 1.25
    camera.near = 1
    camera.far = radius * 6
    camera.updateProjectionMatrix()

    light.shadow.mapSize.set(2048, 2048)
    // Décalages exprimés en centimètres, l'unité de la scène : un `normalBias` de 1 correspond à
    // un centimètre, ce qui suffit à effacer le moirage sans détacher l'ombre de son objet.
    light.shadow.bias = -0.0006
    light.shadow.normalBias = 1
    invalidate()
  },
  { immediate: true },
)

/**
 * Suit la caméra pour le masquage automatique des murs de face.
 *
 * Le calcul n'a lieu que si la caméra a bougé de plus d'un pas : en orbite, un mouvement continu
 * déclencherait sinon une mise à jour par trame pour rien. Le seuil est en centimètres.
 */
const MOVE_THRESHOLD_CM = 12
let lastReported: Vector3 | null = null

useLoop().onBeforeRender((context) => {
  const active: Camera | undefined = context.camera.value
  if (!active) return
  if (lastReported && lastReported.distanceTo(active.position) < MOVE_THRESHOLD_CM) return
  lastReported = active.position.clone()
  emit('cameraMoved', [active.position.x, active.position.y, active.position.z])
})

onUnmounted(() => {
  environmentGenerator?.dispose()
  environmentGenerator = null
  if (webgl.value) webgl.value.clippingPlanes = []
  scene.value.environment?.dispose()
  scene.value.environment = null
})
</script>

<template>
  <TresAmbientLight :intensity="AMBIENT_INTENSITY" />
  <TresDirectionalLight
    ref="keyLight"
    :position="keyPosition"
    :intensity="KEY_INTENSITY"
  />
  <!-- Lumière d'appoint sans ombre : elle sert à ne pas laisser les faces opposées dans le noir,
       pas à dessiner une seconde ombre qui contredirait la première. -->
  <TresDirectionalLight
    :position="fillPosition"
    :intensity="FILL_INTENSITY"
  />
</template>
