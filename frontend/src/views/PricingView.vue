<script setup lang="ts">
/**
 * Page tarifs, alimentée par `plan_catalog` et **jamais** par une grille codée ici.
 *
 * C'est la contrepartie visible de la décision d'architecture : les limites vivent en base
 * (`docs/strategie-produit.md` §4), donc accorder une remise ou déplacer un plafond doit être une
 * ligne SQL. Recopier la grille dans ce fichier aurait annulé tout le bénéfice — il aurait fallu
 * redéployer le frontend pour changer un prix.
 *
 * Les libellés des fonctionnalités et des limites arrivent avec le catalogue : une clé ajoutée en
 * base s'affiche sans qu'on touche à cette page, et à défaut de libellé elle s'affiche telle
 * quelle plutôt que de disparaître en silence.
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import * as api from '@/api/client'
import type { Plan, PlanCatalog } from '@/api/client'

const catalog = ref<PlanCatalog | null>(null)
const error = ref<string | null>(null)
const loading = ref(true)
/** Facturation annuelle (deux mois offerts) ou mensuelle. */
const yearly = ref(false)

onMounted(async () => {
  try {
    catalog.value = await api.readPlans()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : String(caught)
  } finally {
    loading.value = false
  }
})

const plans = computed<Plan[]>(() => catalog.value?.plans ?? [])

/**
 * Montants en **centimes entiers** côté serveur, formatés seulement ici.
 *
 * Aucun calcul n'est fait sur ces valeurs : les diviser pour en refaire des euros au moment de
 * l'affichage est la seule opération autorisée, et elle ne repart jamais vers le serveur.
 */
function euros(cents: number): string {
  return new Intl.NumberFormat('fr-FR', {
    style: 'currency',
    currency: 'EUR',
    minimumFractionDigits: cents % 100 === 0 ? 0 : 2,
  }).format(cents / 100)
}

function priceOf(plan: Plan): string {
  if (yearly.value) {
    // « Sur devis » : le palier n'a pas de tarif annoncé en engagement annuel.
    if (plan.yearly_price_cents === null) return 'Sur devis'
    return euros(plan.yearly_price_cents)
  }
  return euros(plan.monthly_price_cents)
}

function limitLabel(plan: Plan, key: string): string {
  const value = plan.limits[key]
  // `null` veut dire illimité, jamais zéro : afficher « 0 » transformerait un palier sans plafond
  // en palier qui refuse tout.
  return value === null || value === undefined ? 'Illimité' : String(value)
}

/** Clés de limite affichées, dans l'ordre où le serveur les a libellées. */
const limitKeys = computed(() => Object.keys(catalog.value?.limit_labels ?? {}))
const featureKeys = computed(() => Object.keys(catalog.value?.feature_labels ?? {}))

function labelOf(dictionary: Record<string, string> | undefined, key: string): string {
  return dictionary?.[key] ?? key
}
</script>

<template>
  <section class="tarifs">
    <h1>Tarifs</h1>
    <p class="intro">
      Du relevé de la pièce au devis chiffré par mur, avec les élévations cotées et le lien 3D à
      envoyer au client. Prix hors taxes par mois et par entreprise.
    </p>

    <p
      v-if="error"
      class="erreur"
      role="alert"
    >
      {{ error }}
    </p>
    <p v-if="loading">
      Chargement de la grille tarifaire…
    </p>

    <template v-else-if="catalog">
      <fieldset class="bascule">
        <legend>Rythme de facturation</legend>
        <label for="mensuel">
          <input
            id="mensuel"
            v-model="yearly"
            type="radio"
            name="rythme"
            :value="false"
          >
          Au mois
        </label>
        <label for="annuel">
          <input
            id="annuel"
            v-model="yearly"
            type="radio"
            name="rythme"
            :value="true"
          >
          À l'année (deux mois offerts)
        </label>
      </fieldset>

      <div class="grille">
        <article
          v-for="plan in plans"
          :key="plan.code"
          class="palier"
        >
          <h2>{{ plan.name }}</h2>
          <p class="prix">
            <strong>{{ priceOf(plan) }}</strong>
            <span
              v-if="plan.monthly_price_cents > 0 || plan.yearly_price_cents !== null"
              class="unite"
            >HT / mois</span>
          </p>
          <p
            v-if="plan.seat_price_cents > 0"
            class="siege"
          >
            + {{ euros(plan.seat_price_cents) }} HT par siège supplémentaire
          </p>
          <p class="pour-qui">
            {{ plan.tagline }}
          </p>

          <h3>Limites</h3>
          <dl class="limites">
            <template
              v-for="key in limitKeys"
              :key="key"
            >
              <dt>{{ labelOf(catalog.limit_labels, key) }}</dt>
              <dd>{{ limitLabel(plan, key) }}</dd>
            </template>
          </dl>

          <h3>Fonctionnalités</h3>
          <ul class="fonctionnalites">
            <li
              v-for="key in featureKeys"
              :key="key"
              :data-incluse="plan.features[key] ? 'oui' : 'non'"
            >
              <span aria-hidden="true">{{ plan.features[key] ? '✓' : '·' }}</span>
              <span :class="{ exclue: !plan.features[key] }">
                {{ labelOf(catalog.feature_labels, key) }}
              </span>
              <span class="lecteur-decran">{{ plan.features[key] ? ' : inclus' : ' : non inclus' }}</span>
            </li>
          </ul>
        </article>
      </div>

      <p class="essai">
        L'essai du palier Artisan dure {{ catalog.trial_days }} jours, sans carte bancaire. Il ne
        démarre pas à l'inscription mais au premier geste qui en a besoin — votre premier devis,
        votre premier export sans filigrane, votre troisième pièce, votre deuxième chantier — et ce
        geste-là aboutit.
      </p>
      <p class="honnetete">
        Cette grille ne liste que des fonctionnalités déjà livrées : chaque ligne est refusée par le
        serveur lorsqu'elle n'est pas comprise dans votre palier. Rien n'y figure au titre de ce qui
        est prévu.
      </p>

      <p class="raccourcis">
        <RouterLink to="/abonnement">
          Mon abonnement
        </RouterLink>
        <RouterLink to="/legal/cgv">
          Conditions générales de vente
        </RouterLink>
      </p>
    </template>
  </section>
</template>

<style scoped>
.tarifs {
  max-width: 76rem;
}

.intro {
  max-width: 48rem;
  color: var(--texte-doux);
}

.bascule {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
  align-items: center;
  border: 1px solid var(--bordure);
  border-radius: 0.35rem;
  padding: 0.6rem 0.85rem;
  margin: 1rem 0 1.5rem;
}

.bascule legend {
  font-weight: 600;
  padding: 0 0.35rem;
}

.bascule label {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin: 0;
  font-weight: 400;
}

.bascule input {
  width: auto;
}

.grille {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr));
  gap: 1rem;
}

.palier {
  border: 1px solid var(--bordure);
  border-radius: 0.5rem;
  padding: 1rem;
}

.palier h2 {
  margin: 0;
  font-size: 1.15rem;
}

.palier h3 {
  font-size: 0.85rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--texte-doux);
  margin: 1rem 0 0.35rem;
}

.prix {
  margin: 0.35rem 0 0;
  font-size: 1.6rem;
}

.prix .unite {
  font-size: 0.85rem;
  color: var(--texte-doux);
  margin-left: 0.35rem;
}

.siege,
.pour-qui {
  margin: 0.2rem 0 0;
  color: var(--texte-doux);
  font-size: 0.9rem;
}

.limites {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 0.15rem 0.75rem;
  margin: 0;
  font-size: 0.9rem;
}

.limites dt {
  color: var(--texte-doux);
}

.limites dd {
  margin: 0;
  font-weight: 600;
  text-align: right;
}

.fonctionnalites {
  list-style: none;
  padding: 0;
  margin: 0;
  font-size: 0.9rem;
}

.fonctionnalites li {
  display: flex;
  gap: 0.45rem;
  align-items: baseline;
}

/* Barré **et** grisé : la couleur seule ne distingue rien pour un daltonien, et le texte reste
   au-dessus de 7:1 sur fond blanc. */
.exclue {
  text-decoration: line-through;
  color: var(--texte-doux);
}

.essai {
  max-width: 48rem;
  margin-top: 1.5rem;
}

/* Une page de vente qui annonce ce qui n'existe pas n'est pas seulement un défaut produit
   (`docs/spec-complete.md` §10, amendement A14) : le dire ici est ce qui rend la promesse
   vérifiable par le lecteur. */
.honnetete {
  max-width: 48rem;
  color: var(--texte-doux);
  font-size: 0.9rem;
}

.raccourcis {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
}

/* Le symbole « ✓ » est décoratif : l'information passe par ce texte, lu par le lecteur d'écran. */
.lecteur-decran {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}
</style>
