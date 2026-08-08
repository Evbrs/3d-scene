<script setup lang="ts">
/**
 * Connexion et inscription.
 *
 * Les deux modes sont réellement distincts. Enchaîner `register` puis `signIn` faisait afficher
 * « Identifiants invalides » sur un écran « Créer un compte » à qui se réinscrivait avec une
 * adresse déjà prise : une impasse, sans même un lien « mot de passe oublié » pour en sortir.
 */
import { ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const email = ref('')
const password = ref('')
const consent = ref(false)
const mode = ref<'connexion' | 'inscription'>('connexion')

// Un message d'erreur qui survit au changement de mode se lit comme un reproche adressé au
// formulaire qu'on vient d'ouvrir.
watch(mode, () => {
  auth.error = null
  auth.notice = null
})

async function submit(): Promise<void> {
  if (mode.value === 'connexion') {
    if (await auth.signIn(email.value, password.value)) await goToNext()
    return
  }

  const outcome = await auth.signUp(email.value, password.value)
  if (outcome === 'connecte') {
    await goToNext()
  } else if (outcome === 'a-confirmer') {
    // Le compte existe peut-être déjà : on bascule sur la connexion, adresse conservée, plutôt
    // que de laisser l'utilisateur devant un formulaire d'inscription qui ne mènera nulle part.
    mode.value = 'connexion'
    password.value = ''
  }
}

async function goToNext(): Promise<void> {
  const next = typeof route.query.suivant === 'string' ? route.query.suivant : '/projets'
  await router.push(next)
}

function toggleMode(): void {
  mode.value = mode.value === 'connexion' ? 'inscription' : 'connexion'
}
</script>

<template>
  <section class="panneau">
    <h1>{{ mode === 'connexion' ? 'Connexion' : 'Créer un compte' }}</h1>

    <form @submit.prevent="submit">
      <div class="champ">
        <label for="email">Adresse e-mail</label>
        <input
          id="email"
          v-model="email"
          type="email"
          autocomplete="email"
          required
        >
      </div>

      <div class="champ">
        <label for="motdepasse">Mot de passe</label>
        <input
          id="motdepasse"
          v-model="password"
          type="password"
          :autocomplete="mode === 'connexion' ? 'current-password' : 'new-password'"
          minlength="12"
          required
          aria-describedby="aide-motdepasse"
        >
        <p
          id="aide-motdepasse"
          class="aide"
        >
          12 caractères minimum.
        </p>
      </div>

      <div
        v-if="mode === 'inscription'"
        class="champ consentement"
      >
        <input
          id="consentement"
          v-model="consent"
          type="checkbox"
          required
        >
        <label for="consentement">
          J'accepte les
          <RouterLink to="/legal/cgu">conditions générales d'utilisation</RouterLink>
          et j'ai pris connaissance de la
          <RouterLink to="/legal/confidentialite">politique de confidentialité</RouterLink>.
        </label>
      </div>

      <p
        v-if="auth.notice"
        class="message"
        role="status"
      >
        {{ auth.notice }}
      </p>
      <p
        v-if="auth.error"
        class="erreur"
        role="alert"
      >
        {{ auth.error }}
      </p>

      <div class="actions">
        <button
          type="submit"
          data-variant="primary"
          :disabled="auth.pending"
        >
          {{ auth.pending ? 'Envoi…' : mode === 'connexion' ? 'Se connecter' : "S'inscrire" }}
        </button>
        <button
          type="button"
          @click="toggleMode"
        >
          {{ mode === 'connexion' ? 'Créer un compte' : "J'ai déjà un compte" }}
        </button>
      </div>
    </form>

    <p
      v-if="mode === 'connexion'"
      class="aide"
    >
      <RouterLink to="/mot-de-passe-oublie">
        Mot de passe oublié ?
      </RouterLink>
    </p>

    <!-- Le premier écran du produit est aussi le seul que tout visiteur voit : les documents
         légaux y sont atteignables sans compte, y compris en mode connexion. -->
    <nav
      class="legal"
      aria-label="Informations légales"
    >
      <RouterLink to="/legal/mentions">
        Mentions légales
      </RouterLink>
      <RouterLink to="/legal/cgu">
        CGU
      </RouterLink>
      <RouterLink to="/legal/cgv">
        CGV
      </RouterLink>
      <RouterLink to="/legal/confidentialite">
        Confidentialité
      </RouterLink>
    </nav>
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

.consentement {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
}

.consentement input {
  width: auto;
  margin-top: 0.35rem;
}

.consentement label {
  font-weight: 400;
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
  flex-wrap: wrap;
  gap: 0.75rem;
  margin-top: 1.25rem;
}

.legal {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
  margin-top: 2.5rem;
  padding-top: 1rem;
  border-top: 1px solid var(--bordure);
  font-size: 0.9rem;
}
</style>
