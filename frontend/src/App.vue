<script setup lang="ts">
import { onMounted } from 'vue'
import { RouterLink, RouterView, useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()

onMounted(() => auth.restore())

function signOut(): void {
  auth.signOut()
  void router.push({ name: 'connexion' })
}
</script>

<template>
  <a
    class="skip-link"
    href="#contenu"
  >Aller au contenu principal</a>

  <header class="app-header">
    <RouterLink
      class="brand"
      to="/projets"
    >
      Plan de rénovation
    </RouterLink>
    <nav
      v-if="auth.user"
      aria-label="Navigation principale"
    >
      <RouterLink to="/projets">
        Projets
      </RouterLink>
      <button
        type="button"
        @click="signOut"
      >
        Se déconnecter ({{ auth.user.email }})
      </button>
    </nav>
  </header>

  <main id="contenu">
    <RouterView />
  </main>
</template>

<style>
:root {
  --texte: #14181d;
  --texte-doux: #41474f;
  --fond: #ffffff;
  --bordure: #c9ced6;
  --accent: #0b4fd6;
  --erreur: #8a0f18;
  --succes: #0a5c2c;
}

*,
*::before,
*::after {
  box-sizing: border-box;
}

body {
  margin: 0;
  font-family: system-ui, -apple-system, 'Segoe UI', sans-serif;
  color: var(--texte);
  background: var(--fond);
  line-height: 1.5;
}

/* Contraste AAA (7:1) et focus toujours visible : conventions d'accessibilité du projet. */
:focus-visible {
  outline: 3px solid var(--accent);
  outline-offset: 2px;
}

.skip-link {
  position: absolute;
  left: -9999px;
}

.skip-link:focus {
  left: 0.5rem;
  top: 0.5rem;
  z-index: 10;
  background: var(--fond);
  padding: 0.5rem 0.75rem;
  border: 2px solid var(--accent);
  border-radius: 0.25rem;
}

.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.75rem 1.25rem;
  border-bottom: 1px solid var(--bordure);
}

.app-header nav {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.brand {
  font-weight: 700;
  font-size: 1.05rem;
}

a {
  color: var(--accent);
}

main {
  padding: 1.25rem;
  margin: 0 auto;
  max-width: 78rem;
}

button {
  font: inherit;
  padding: 0.4rem 0.8rem;
  border: 1px solid var(--bordure);
  border-radius: 0.35rem;
  background: var(--fond);
  color: var(--texte);
  cursor: pointer;
}

button:hover:not(:disabled) {
  border-color: var(--accent);
}

button:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

button[data-variant='primary'] {
  background: var(--accent);
  border-color: var(--accent);
  color: #ffffff;
}

label {
  display: block;
  font-weight: 600;
  margin-bottom: 0.25rem;
}

input,
select {
  font: inherit;
  padding: 0.4rem 0.5rem;
  border: 1px solid var(--bordure);
  border-radius: 0.35rem;
  width: 100%;
}

.erreur {
  color: var(--erreur);
  font-weight: 600;
}
</style>
