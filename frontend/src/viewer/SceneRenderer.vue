<script setup lang="ts">
/**
 * Traduction du scene graph en objets Three.js (ticket P7).
 *
 * Ce composant ne calcule **aucune** géométrie métier : il consomme le JSON produit par le
 * backend (`docs/spec-complete.md` §3.1). L'assemblage lui-même vit dans `viewer/build.ts`, où il
 * est testable sans WebGL ; il ne reste ici que le cycle de vie.
 *
 * Ce cycle de vie est le point important. Les géométries naissaient dans des `computed`, donc
 * hors de l'arbre TresJS, que son nettoyage ne couvre pas : chaque rechargement de scène
 * abandonnait un jeu complet de tampons sur la carte graphique. Un `shallowRef` piloté par un
 * `watch` qui libère l'ancien lot, plus un `onUnmounted`, referment la question.
 */
import type { Group } from 'three'
import { nextTick, onUnmounted, shallowRef, watch } from 'vue'

import type { SceneRoom } from '@/api/types'
import { applyVisibility, buildScene, needsCsg } from '@/viewer/build'
import { ensureCsgReady, isCsgReady, releaseCarvedCache, retainCarvedCache } from '@/viewer/csg'
import { ResourcePool } from '@/viewer/resources'
import type { FaceVisibility } from '@/viewer/visibility'

const props = withDefaults(
  defineProps<{
    rooms: readonly SceneRoom[]
    visibility: Record<string, FaceVisibility>
    /** Préfixe les clés de face par la pièce : nécessaire dès qu'on montre le logement entier. */
    roomScoped?: boolean
    shadows?: boolean
  }>(),
  { roomScoped: false, shadows: true },
)

const emit = defineEmits<{ built: [Group] }>()

let pool = new ResourcePool()
const root = shallowRef<Group | null>(null)

/**
 * Numéro de la construction en cours.
 *
 * Le chargement du CSG est asynchrone : sans ce compteur, une scène demandée pendant l'attente
 * serait écrasée par la précédente au retour de la promesse.
 */
let generation = 0

retainCarvedCache()

async function rebuild(): Promise<void> {
  const mine = ++generation

  // La librairie booléenne n'est chargée que si la scène en a l'usage, et **avant** la
  // construction : la charger après ferait passer les meubles de pleins à creusés sous les yeux
  // de l'utilisateur.
  if (needsCsg(props.rooms) && !isCsgReady()) {
    await ensureCsgReady()
    if (mine !== generation) return
  }

  const stale = pool
  pool = new ResourcePool()
  const built = buildScene(props.rooms, {
    pool,
    roomScoped: props.roomScoped,
    shadows: props.shadows,
  })
  applyVisibility(built, props.visibility)
  root.value = built
  emit('built', built)

  // L'ancienne scène reste montée jusqu'au prochain flush de Vue. Libérer ses ressources tout de
  // suite ferait dessiner une trame avec des tampons déjà rendus au pilote — écran noir et
  // erreurs WebGL. On attend donc que le remplacement soit effectif.
  await nextTick()
  stale.dispose()
}

// La reconstruction ne dépend que de ce qui change la géométrie. La visibilité, elle, n'en
// change aucune : elle bascule des matériaux déjà construits (voir `applyVisibility`).
watch(
  () => [props.rooms, props.roomScoped, props.shadows],
  () => void rebuild(),
  { immediate: true },
)

watch(
  () => props.visibility,
  (visibility) => {
    if (root.value) applyVisibility(root.value, visibility)
  },
)

onUnmounted(() => {
  // Invalide une construction encore en vol : elle ne doit pas ressusciter un pool libéré.
  generation += 1
  pool.dispose()
  // Les géométries creusées survivent volontairement aux reconstructions de scène (l'évaluation
  // booléenne est chère) ; plus aucune scène montée, plus de raison de les garder.
  releaseCarvedCache()
})
</script>

<template>
  <primitive
    v-if="root"
    :object="root"
  />
</template>
