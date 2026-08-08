<script setup lang="ts">
/**
 * Demande d'un lien de réinitialisation.
 *
 * Écran manquant jusqu'ici : le lien « Mot de passe oublié ? » de la connexion menait à une page
 * qui annonçait que la procédure n'existait pas. Un mot de passe perdu signifiait donc le compte
 * et tous les chantiers perdus définitivement — SQLAdmin exclut le mot de passe de son formulaire
 * et la CLI ne l'expose pas.
 *
 * La vue affiche **toujours le même message**, que l'adresse soit inscrite ou non. Ce n'est pas
 * une approximation d'interface : le serveur répond 202 dans les deux cas (anti-énumération), et
 * une vue qui distinguerait les deux réintroduirait ici l'oracle que l'API refuse.
 */
import { ref } from 'vue'
import { RouterLink } from 'vue-router'

import * as api from '@/api/client'

const email = ref('')
const pending = ref(false)
const message = ref<string | null>(null)
const error = ref<string | null>(null)
/**
 * Jeton rendu par le serveur **en développement seulement**, aucun service d'acheminement de
 * messages n'existant encore dans le dépôt. Affiché tel quel et annoncé comme tel : le masquer
 * rendrait le parcours intestable à la main, le présenter comme normal ferait oublier qu'il
 * manque un transport de courriel avant la mise en production.
 */
const jetonDeDeveloppement = ref<string | null>(null)

async function submit(): Promise<void> {
  pending.value = true
  error.value = null
  message.value = null
  jetonDeDeveloppement.value = null
  try {
    const outcome = await api.forgotPassword(email.value)
    message.value = outcome.detail
    jetonDeDeveloppement.value = outcome.reset_token
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : String(caught)
  } finally {
    pending.value = false
  }
}
</script>

<template>
  <section class="panneau">
    <h1>Mot de passe oublié</h1>
    <p>
      Indiquez l'adresse de votre compte. Si elle correspond à un compte, un lien de
      réinitialisation valable une heure vous est envoyé.
    </p>

    <form @submit.prevent="submit">
      <div class="champ">
        <label for="email-oubli">Adresse e-mail</label>
        <input
          id="email-oubli"
          v-model="email"
          type="email"
          autocomplete="email"
          required
        >
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

      <p
        v-if="jetonDeDeveloppement"
        class="developpement"
        role="status"
      >
        Environnement de développement : aucun courriel n'est envoyé.
        <RouterLink :to="`/mot-de-passe/reinitialiser?jeton=${encodeURIComponent(jetonDeDeveloppement)}`">
          Poursuivre la réinitialisation
        </RouterLink>
      </p>

      <div class="actions">
        <button
          type="submit"
          data-variant="primary"
          :disabled="pending"
        >
          {{ pending ? 'Envoi…' : 'Envoyer le lien' }}
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

.message {
  margin: 1rem 0 0;
  padding: 0.6rem 0.85rem;
  border-radius: 0.35rem;
  background: #eaf2ff;
  color: #0a3690;
  font-weight: 600;
}

.developpement {
  margin: 0.75rem 0 0;
  padding: 0.6rem 0.85rem;
  border: 1px dashed var(--bordure);
  border-radius: 0.35rem;
  color: var(--texte-doux);
  font-size: 0.9rem;
  overflow-wrap: anywhere;
}

.actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.75rem;
  margin-top: 1.25rem;
}
</style>
