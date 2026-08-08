<script setup lang="ts">
/**
 * Panneau d'inspection : les anomalies de conformité du plan, cliquables.
 *
 * Le composant est **purement présentationnel**. Il ne va pas chercher le rapport lui-même : il le
 * reçoit, et il émet `recentrer` quand l'utilisateur clique une anomalie. Ce choix n'est pas de la
 * pureté d'architecture, c'est ce qui rend le panneau montable dans l'éditeur 2D comme dans le
 * viewer 3D, deux écrans qui ne recentrent pas la même chose — l'un déplace une caméra Konva,
 * l'autre une caméra Three.js. Le composant qui l'accueille sait faire ça ; lui, non.
 *
 * Trois exigences d'interface, et elles viennent du moteur :
 *
 * - **la sévérité gouverne l'ordre et la couleur.** Le backend rend déjà les anomalies triées :
 *   on ne retrie pas, on affiche. Retrier ici, c'est deux ordres qui divergeront un jour.
 * - **le message dit déjà QUOI et DE COMBIEN.** Le panneau ne recompose aucune phrase à partir de
 *   `measured_cm` et `threshold_cm` : la formulation vit côté serveur, en un seul endroit.
 * - **tout est atteignable au clavier.** Chaque anomalie est un vrai `<button>` dans une liste,
 *   pas une ligne cliquable : une div avec un `@click` est invisible pour un lecteur d'écran et
 *   inatteignable à la tabulation.
 *
 * Les types du rapport vivent dans `src/api/types.ts`, avec le reste du contrat de l'API : ils
 * décrivent ce que le serveur publie, pas ce que ce composant affiche.
 */
import { computed, ref } from 'vue'

import type { Anomaly, InspectionReport, Severity } from '@/api/types'

const props = withDefaults(
  defineProps<{
    report: InspectionReport | null
    loading?: boolean
    error?: string | null
  }>(),
  { loading: false, error: null },
)

const emit = defineEmits<{
  recentrer: [anomaly: Anomaly]
  rafraichir: []
}>()

const SEVERITIES: Severity[] = ['bloquant', 'avertissement', 'conseil']

const LIBELLES: Record<Severity, string> = {
  bloquant: 'Bloquant',
  avertissement: 'Avertissement',
  conseil: 'Conseil',
}

/**
 * Sévérités affichées. Toutes cochées au départ : un panneau qui masque par défaut ce qu'il vient
 * de trouver est un panneau qui ment.
 */
const shown = ref<Set<Severity>>(new Set(SEVERITIES))
const room = ref<number | 'toutes'>('toutes')

function toggle(severity: Severity): void {
  const next = new Set(shown.value)
  if (next.has(severity)) next.delete(severity)
  else next.add(severity)
  // Réaffectation et non mutation : un `Set` muté sur place ne déclenche aucune réactivité.
  shown.value = next
}

const anomalies = computed<Anomaly[]>(() =>
  (props.report?.anomalies ?? []).filter(
    (anomaly) =>
      shown.value.has(anomaly.severity) &&
      (room.value === 'toutes' || anomaly.room_id === room.value),
  ),
)

const counts = computed<Record<string, number>>(() => props.report?.counts ?? {})
const total = computed(() => SEVERITIES.reduce((sum, key) => sum + (counts.value[key] ?? 0), 0))

/** Le mode accessible change les seuils : le dire évite de discuter un chiffre sans son barème. */
const accessible = computed(() => props.report?.thresholds?.accessible === true)

/**
 * Une anomalie n'est recentrable que si elle désigne un endroit. « Pièce sans ouverture » n'en
 * désigne aucun : le bouton reste, désactivé, plutôt que de disparaître — une ligne qui change de
 * nature d'un rapport à l'autre déroute plus qu'elle n'aide.
 */
function locatable(anomaly: Anomaly): boolean {
  return anomaly.focus !== null || anomaly.element_ids.length > 0
}

function select(anomaly: Anomaly): void {
  if (locatable(anomaly)) emit('recentrer', anomaly)
}

/** Une mesure lue comme sur un chantier : au centimètre, virgule décimale française. */
function centimetres(value: number | null): string {
  if (value === null) return ''
  return `${value.toLocaleString('fr-FR', { maximumFractionDigits: 1 })} cm`
}
</script>

<template>
  <section
    class="inspection"
    aria-labelledby="inspection-titre"
  >
    <header>
      <h2 id="inspection-titre">
        Contrôle du plan
      </h2>
      <button
        type="button"
        :disabled="loading"
        @click="emit('rafraichir')"
      >
        Actualiser
      </button>
    </header>

    <p
      v-if="error"
      class="erreur"
      role="alert"
    >
      {{ error }}
    </p>

    <p
      v-else-if="loading"
      role="status"
    >
      Analyse du plan en cours…
    </p>

    <template v-else-if="report">
      <!-- `role="status"` et non `alert` : le compte se met à jour à chaque relecture, et une
           alerte répétée finit par être coupée par l'utilisateur. -->
      <p
        class="resume"
        role="status"
      >
        <template v-if="total === 0">
          Aucune anomalie détectée sur ce plan.
        </template>
        <template v-else>
          {{ total }} anomalie(s) :
          <span
            v-for="severity in SEVERITIES"
            :key="severity"
            class="compteur"
            :data-severite="severity"
          >{{ counts[severity] ?? 0 }} {{ LIBELLES[severity].toLowerCase() }}</span>
        </template>
      </p>

      <p
        v-if="accessible"
        class="barème"
      >
        Seuils du logement accessible appliqués (couloir de
        {{ centimetres(Number(report.thresholds.accessible_passage_min_cm)) }}).
      </p>

      <fieldset class="filtres">
        <legend>Filtrer</legend>
        <label
          v-for="severity in SEVERITIES"
          :key="severity"
          class="filtre"
        >
          <input
            type="checkbox"
            :checked="shown.has(severity)"
            @change="toggle(severity)"
          >
          {{ LIBELLES[severity] }} ({{ counts[severity] ?? 0 }})
        </label>

        <label
          v-if="report.rooms.length > 1"
          class="filtre"
        >
          <span>Pièce</span>
          <select v-model="room">
            <option value="toutes">
              Toutes les pièces
            </option>
            <option
              v-for="entry in report.rooms"
              :key="String(entry.room_id)"
              :value="entry.room_id"
            >
              {{ entry.name }}
            </option>
          </select>
        </label>
      </fieldset>

      <ul
        v-if="anomalies.length"
        class="anomalies"
      >
        <li
          v-for="(anomaly, index) in anomalies"
          :key="`${anomaly.rule_id}-${index}`"
          :data-severite="anomaly.severity"
          :data-regle="anomaly.rule_id"
        >
          <button
            type="button"
            class="anomalie"
            :disabled="!locatable(anomaly)"
            @click="select(anomaly)"
          >
            <span class="etiquette">{{ LIBELLES[anomaly.severity] }}</span>
            <span class="titre">{{ anomaly.title }}</span>
            <span class="message">{{ anomaly.message }}</span>
            <span
              v-if="anomaly.room_name"
              class="lieu"
            >
              {{ anomaly.room_name }}<template v-if="anomaly.face_labels.length">
                — mur {{ anomaly.face_labels.join(', ') }}</template>
            </span>
          </button>
        </li>
      </ul>

      <p v-else-if="total > 0">
        Aucune anomalie ne correspond au filtre courant.
      </p>

      <!-- Un rapport vide accompagné d'un avertissement ne veut pas dire « conforme » : ce qui
           n'a pas pu être contrôlé doit se voir, sinon le silence passe pour une garantie. -->
      <details
        v-if="report.warnings.length"
        class="reserves"
      >
        <summary>{{ report.warnings.length }} point(s) non contrôlé(s)</summary>
        <ul>
          <li
            v-for="(warning, index) in report.warnings"
            :key="index"
          >
            {{ warning }}
          </li>
        </ul>
      </details>
    </template>

    <p v-else>
      Le plan n'a pas encore été analysé.
    </p>
  </section>
</template>

<style scoped>
.inspection header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 1rem;
}

.resume {
  margin: 0.5rem 0;
}

.compteur {
  margin-left: 0.75rem;
  white-space: nowrap;
}

/* Les couleurs ne portent jamais l'information seules : chaque ligne écrit aussi sa sévérité en
   toutes lettres (`.etiquette`). WCAG 1.4.1 — un daltonien doit lire le même plan. */
.compteur[data-severite='bloquant'],
.anomalies li[data-severite='bloquant'] .etiquette {
  color: #8a0d0d;
  font-weight: 700;
}

.compteur[data-severite='avertissement'],
.anomalies li[data-severite='avertissement'] .etiquette {
  color: #7a4a00;
  font-weight: 700;
}

.compteur[data-severite='conseil'],
.anomalies li[data-severite='conseil'] .etiquette {
  color: #1f4b6e;
  font-weight: 700;
}

.filtres {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  align-items: center;
  border: 1px solid currentcolor;
  padding: 0.5rem 0.75rem;
  margin: 0.5rem 0;
}

.filtre {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
}

.anomalies {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.anomalie {
  display: grid;
  gap: 0.15rem;
  width: 100%;
  text-align: left;
  padding: 0.5rem 0.65rem;
  border: 1px solid currentcolor;
  background: none;
  cursor: pointer;
}

.anomalie:disabled {
  cursor: default;
  opacity: 0.85;
}

/* Le focus doit rester visible en permanence (WCAG 2.4.11) : un contour épais et décalé survit
   aux fonds clairs comme aux fonds sombres. */
.anomalie:focus-visible {
  outline: 3px solid currentcolor;
  outline-offset: 2px;
}

.etiquette {
  font-size: 0.8rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.titre {
  font-weight: 700;
}

.message {
  font-size: 0.95rem;
}

.lieu {
  font-size: 0.85rem;
}

.reserves {
  margin-top: 0.75rem;
}

.erreur {
  font-weight: 700;
}
</style>
