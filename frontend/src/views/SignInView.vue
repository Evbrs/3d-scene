<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const email = ref('')
const password = ref('')
const mode = ref<'connexion' | 'inscription'>('connexion')

async function submit(): Promise<void> {
  const ok =
    mode.value === 'connexion'
      ? await auth.signIn(email.value, password.value)
      : await auth.signUp(email.value, password.value)

  if (ok) {
    const next = typeof route.query.suivant === 'string' ? route.query.suivant : '/projets'
    await router.push(next)
  }
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
          @click="mode = mode === 'connexion' ? 'inscription' : 'connexion'"
        >
          {{ mode === 'connexion' ? 'Créer un compte' : "J'ai déjà un compte" }}
        </button>
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

.actions {
  display: flex;
  gap: 0.75rem;
  margin-top: 1.25rem;
}
</style>
