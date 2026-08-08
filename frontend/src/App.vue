<script setup lang="ts">
import { onMounted } from 'vue'
import { RouterLink, RouterView, useRouter } from 'vue-router'

import { useAppStore } from '@/stores/app'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const application = useAppStore()
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
      <!-- Deux entrées et non une : `/abonnement` porte le contrat de l'ENTREPRISE (palier,
           consommation, essai), `/compte` porte la personne (mot de passe, export RGPD, fermeture).
           Les fondre ferait cohabiter « changer de palier » et « supprimer mon compte » sur le même
           écran, ce qui est exactement l'endroit où on ne veut pas d'ambiguïté. -->
      <RouterLink to="/abonnement">
        Abonnement
      </RouterLink>
      <RouterLink to="/compte">
        Mon compte
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
    <p
      v-if="application.fatalError"
      class="erreur-globale"
      role="alert"
    >
      Une erreur inattendue s'est produite : {{ application.fatalError }}
      <button
        type="button"
        @click="application.dismiss()"
      >
        Masquer
      </button>
    </p>
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

.erreur-globale {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.75rem;
  margin: 0 0 1rem;
  padding: 0.6rem 0.85rem;
  border-radius: 0.35rem;
  background: #fdecea;
  color: #7a1010;
  font-weight: 600;
}

/* Téléphone : l'en-tête à deux colonnes déborde dès que l'adresse e-mail est un peu longue. */
@media (max-width: 40rem) {
  main {
    padding: 1rem 0.75rem;
  }

  .app-header {
    flex-wrap: wrap;
    padding: 0.6rem 0.75rem;
  }

  .app-header nav {
    flex-wrap: wrap;
    gap: 0.6rem;
  }
}
</style>
