<script setup lang="ts">
/**
 * Pose du nouveau mot de passe à partir du jeton reçu.
 *
 * Le jeton arrive en paramètre de requête (`?jeton=…`) : c'est la forme qui survit aux clients de
 * messagerie qui réécrivent les URL, et elle évite d'inscrire un secret dans un segment de chemin
 * que le routeur pourrait journaliser en tant que nom de route.
 *
 * La confirmation est saisie deux fois et comparée **ici**. Le serveur ne peut pas le faire : il
 * ne reçoit qu'une valeur, et une faute de frappe sur un mot de passe qu'on ne relit pas enferme
 * l'utilisateur dehors une seconde fois — précisément la situation dont il essaie de sortir.
 */
import { computed, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import * as api from '@/api/client'

const route = useRoute()
const router = useRouter()

/** Longueur minimale, alignée sur la borne NIST appliquée par le serveur. */
const LONGUEUR_MINIMALE = 12

const jeton = computed(() => (typeof route.query.jeton === 'string' ? route.query.jeton : ''))

const motDePasse = ref('')
const confirmation = ref('')
const pending = ref(false)
const message = ref<string | null>(null)
const error = ref<string | null>(null)

const discordant = computed(
  () => confirmation.value.length > 0 && confirmation.value !== motDePasse.value,
)

async function submit(): Promise<void> {
  error.value = null
  message.value = null

  if (motDePasse.value !== confirmation.value) {
    error.value = 'Les deux mots de passe saisis sont différents.'
    return
  }

  pending.value = true
  try {
    const outcome = await api.resetPassword(jeton.value, motDePasse.value)
    message.value = outcome.detail
    // Vers la connexion, et pas vers les projets : la réinitialisation a fermé toutes les
    // sessions, y compris celle qu'on aurait pu croire ouverte dans cet onglet.
    await router.push({ name: 'connexion' })
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : String(caught)
  } finally {
    pending.value = false
  }
}
</script>

<template>
  <section class="panneau">
    <h1>Nouveau mot de passe</h1>

    <p
      v-if="!jeton"
      class="erreur"
      role="alert"
    >
      Ce lien ne contient aucun jeton de réinitialisation. Il a probablement été tronqué par votre
      messagerie.
      <RouterLink to="/mot-de-passe-oublie">
        Demandez-en un nouveau
      </RouterLink>.
    </p>

    <form
      v-else
      @submit.prevent="submit"
    >
      <div class="champ">
        <label for="nouveau">Nouveau mot de passe</label>
        <input
          id="nouveau"
          v-model="motDePasse"
          type="password"
          autocomplete="new-password"
          :minlength="LONGUEUR_MINIMALE"
          required
          aria-describedby="aide-nouveau"
        >
        <p
          id="aide-nouveau"
          class="aide"
        >
          {{ LONGUEUR_MINIMALE }} caractères minimum.
        </p>
      </div>

      <div class="champ">
        <label for="confirmation">Confirmez le mot de passe</label>
        <input
          id="confirmation"
          v-model="confirmation"
          type="password"
          autocomplete="new-password"
          :minlength="LONGUEUR_MINIMALE"
          required
          :aria-invalid="discordant ? 'true' : undefined"
          aria-describedby="aide-confirmation"
        >
        <p
          id="aide-confirmation"
          class="aide"
          :class="{ erreur: discordant }"
          role="status"
        >
          {{ discordant ? 'Les deux saisies diffèrent.' : 'Saisissez à nouveau le même mot de passe.' }}
        </p>
      </div>

      <p
        v-if="message"
        class="message"
        role="status"
      >
        {{ message }}
      </p>
      <p
        v-if="error"
        class="erreur"
        role="alert"
      >
        {{ error }}
      </p>

      <div class="actions">
        <button
          type="submit"
          data-variant="primary"
          :disabled="pending || discordant"
        >
          {{ pending ? 'Enregistrement…' : 'Changer le mot de passe' }}
        </button>
        <RouterLink to="/connexion">
          Retour à la connexion
        </RouterLink>
      </div>
    </form>
  </section>
</template>

<style scoped>
.panneau {
  max-width: 26rem;
  margin: 2rem auto;
}

.champ {
  margin-bottom: 1rem;
}

.aide {
  margin: 0.25rem 0 0;
  color: var(--texte-doux);
  font-size: 0.9rem;
}

.message {
  margin: 1rem 0 0;
  padding: 0.6rem 0.85rem;
  border-radius: 0.35rem;
  background: #eaf2ff;
  color: #0a3690;
  font-weight: 600;
}

.actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.75rem;
  margin-top: 1.25rem;
}
</style>
