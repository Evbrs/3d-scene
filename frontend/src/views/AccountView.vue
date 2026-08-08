<script setup lang="ts">
/**
 * Abonnement et consommation de l'entreprise.
 *
 * Une seule route la sert (`GET /api/organizations/{id}/subscription`), et c'est la même que les
 * boîtes de dialogue des murs de paiement : deux vérités affichées côte à côte finissent toujours
 * par diverger, et celle qui compte est celle qui décide côté serveur.
 *
 * Rien n'est codé en dur ici non plus. Les libellés des métriques et des limites viennent du
 * catalogue, et la comparaison « consommé / plafond » se lit directement dans la réponse : le
 * frontend n'a aucune règle de facturation à connaître, ce qui lui interdit d'en afficher une
 * fausse.
 *
 * C'est aussi cette page qui applique le **déclassement** : les chantiers excédentaires y passent
 * en lecture seule. Elle le dit, et elle dit surtout qu'ils ne sont pas supprimés — c'est la
 * différence entre une limite et une perte de données.
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import * as api from '@/api/client'
import type { Entitlement, PlanCatalog } from '@/api/client'

const catalog = ref<PlanCatalog | null>(null)
const state = ref<Entitlement | null>(null)
const error = ref<string | null>(null)
const loading = ref(true)
const busy = ref(false)

function messageOf(caught: unknown): string {
  return caught instanceof Error ? caught.message : String(caught)
}

async function refresh(): Promise<void> {
  error.value = null
  try {
    const organizations = await api.listOrganizations()
    const organization = organizations[0]
    if (!organization) {
      error.value = "Aucune entreprise n'est rattachée à ce compte."
      return
    }
    // Le catalogue n'est demandé qu'ici, pour les libellés : la page ne s'en sert pas pour décider
    // quoi que ce soit, seulement pour nommer les clés que le serveur lui renvoie.
    ;[catalog.value, state.value] = await Promise.all([
      api.readPlans(),
      api.readSubscription(organization.id),
    ])
  } catch (caught) {
    error.value = messageOf(caught)
  } finally {
    loading.value = false
  }
}

async function openTrial(): Promise<void> {
  if (!state.value) return
  busy.value = true
  error.value = null
  try {
    state.value = await api.startTrial(state.value.organization_id)
  } catch (caught) {
    error.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}

onMounted(refresh)

const statusLabels: Record<string, string> = {
  trialing: "Essai en cours",
  active: 'Actif',
  past_due: 'Paiement en retard',
  canceled: 'Résilié',
}

const statusLabel = computed(() => {
  const status = state.value?.subscription?.status
  return status ? (statusLabels[status] ?? status) : 'Aucun abonnement'
})

function labelOf(dictionary: Record<string, string> | undefined, key: string): string {
  return dictionary?.[key] ?? key
}

function dateFr(iso: string | null): string {
  if (!iso) return '—'
  return new Intl.DateTimeFormat('fr-FR', { dateStyle: 'long' }).format(new Date(iso))
}

/** `limit` à `null` veut dire illimité : « 3 / 0 » n'a aucun sens à l'écran. */
function quotaLabel(value: number, limit: number | null): string {
  return limit === null ? `${value} (illimité)` : `${value} / ${limit}`
}

function atLimit(value: number, limit: number | null): boolean {
  return limit !== null && value >= limit
}
</script>

<template>
  <section class="abonnement">
    <h1>Abonnement et consommation</h1>

    <p
      v-if="error"
      class="erreur"
      role="alert"
    >
      {{ error }}
    </p>
    <p v-if="loading">
      Chargement de votre abonnement…
    </p>

    <template v-else-if="state">
      <div class="palier">
        <h2>{{ state.plan.name }}</h2>
        <p class="tagline">
          {{ state.plan.tagline }}
        </p>
        <dl>
          <dt>État</dt>
          <dd>{{ statusLabel }}</dd>
          <dt>Période en cours</dt>
          <dd>du {{ dateFr(state.period_start) }} au {{ dateFr(state.period_end) }}</dd>
          <template v-if="state.trial_ends_at">
            <dt>Fin de l'essai</dt>
            <dd>{{ dateFr(state.trial_ends_at) }}</dd>
          </template>
        </dl>

        <p v-if="state.trial_available">
          <button
            type="button"
            data-variant="primary"
            :disabled="busy"
            @click="openTrial"
          >
            Démarrer l'essai de {{ catalog?.trial_days ?? 14 }} jours, sans carte
          </button>
          <span class="aide">
            Il retire le filigrane des exports, ouvre le devis chiffré et lève la limite de
            chantiers.
          </span>
        </p>
        <p v-else-if="!state.subscription">
          Votre essai a été utilisé. Le palier gratuit reste ouvert : vos chantiers, vos plans et
          vos exports filigranés restent accessibles.
        </p>
      </div>

      <h2>Consommation de la période</h2>
      <div class="tableau">
        <table>
          <caption>
            Les compteurs se remettent à zéro à la date anniversaire de votre abonnement, pas le
            1er du mois.
          </caption>
          <thead>
            <tr>
              <th scope="col">
                Métrique
              </th>
              <th scope="col">
                Consommé
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="ligne in state.usage"
              :key="ligne.metric"
            >
              <th scope="row">
                {{ labelOf(catalog?.metric_labels, ligne.metric) }}
              </th>
              <td :class="{ atteint: atLimit(ligne.value, ligne.limit) }">
                {{ quotaLabel(ligne.value, ligne.limit) }}
                <span v-if="atLimit(ligne.value, ligne.limit)"> — plafond atteint</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div
        v-if="state.archived_project_ids.length > 0"
        class="declassement"
        role="status"
      >
        <h2>Chantiers passés en lecture seule</h2>
        <p>
          {{ state.archived_project_ids.length }} chantier(s) dépassent le nombre autorisé par
          votre palier. Ils ne sont <strong>pas supprimés</strong> : ils restent lisibles,
          exportables et partageables, mais ne sont plus modifiables. Ils redeviennent modifiables
          dès que le palier le permet.
        </p>
        <ul>
          <li
            v-for="identifiant in state.archived_project_ids"
            :key="identifiant"
          >
            <RouterLink :to="`/projets/${identifiant}/plan`">
              Chantier n° {{ identifiant }}
            </RouterLink>
          </li>
        </ul>
      </div>

      <p class="raccourcis">
        <RouterLink to="/tarifs">
          Comparer les paliers
        </RouterLink>
        <RouterLink to="/projets">
          Mes chantiers
        </RouterLink>
        <!-- Cette page porte l'abonnement de l'ENTREPRISE ; le mot de passe, l'export RGPD et la
             fermeture du compte sont personnels et vivent sur `/compte`. Le lien évite qu'un
             utilisateur cherche l'un en croyant être au bon endroit. -->
        <RouterLink to="/compte">
          Mon compte
        </RouterLink>
      </p>
    </template>
  </section>
</template>

<style scoped>
.abonnement {
  max-width: 60rem;
}

.palier {
  border: 1px solid var(--bordure);
  border-radius: 0.5rem;
  padding: 1rem;
  margin-bottom: 1.5rem;
}

.palier h2 {
  margin: 0;
  font-size: 1.2rem;
}

.tagline {
  margin: 0.2rem 0 0.75rem;
  color: var(--texte-doux);
}

.palier dl {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 0.2rem 0.9rem;
  margin: 0;
}

.palier dt {
  color: var(--texte-doux);
}

.palier dd {
  margin: 0;
  font-weight: 600;
}

.aide {
  display: block;
  margin-top: 0.35rem;
  color: var(--texte-doux);
  font-size: 0.9rem;
}

.tableau {
  overflow-x: auto;
}

table {
  border-collapse: collapse;
  width: 100%;
}

caption {
  text-align: left;
  color: var(--texte-doux);
  padding-bottom: 0.5rem;
}

th,
td {
  text-align: left;
  padding: 0.4rem 0.6rem;
  border-bottom: 1px solid var(--bordure);
}

/* Le plafond atteint est signalé par la couleur **et** par un texte : la couleur seule ne dit
   rien à un daltonien ni à un lecteur d'écran. */
.atteint {
  color: var(--erreur);
  font-weight: 600;
}

.declassement {
  border: 1px solid var(--bordure);
  border-left: 4px solid var(--erreur);
  border-radius: 0.35rem;
  padding: 0.75rem 1rem;
  margin: 1.5rem 0;
}

.declassement h2 {
  margin-top: 0;
  font-size: 1.05rem;
}

.raccourcis {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
}
</style>
