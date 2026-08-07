<script setup lang="ts">
/**
 * Page d'information sans logique (conditions d'utilisation, mot de passe oublié).
 *
 * Elle existe pour qu'aucun lien de l'écran de connexion ne mène à une 404 : une impasse à
 * l'entrée du produit coûte l'utilisateur. Le contenu définitif viendra avec les tickets
 * correspondants ; en attendant, la page dit clairement où on en est.
 */
import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

const route = useRoute()

const title = computed(() => String(route.meta.titre ?? 'Information'))
const body = computed(() => String(route.meta.texte ?? ''))
</script>

<template>
  <section class="panneau">
    <h1>{{ title }}</h1>
    <p>{{ body }}</p>
    <p>
      <RouterLink to="/connexion">
        ← Retour à la connexion
      </RouterLink>
    </p>
  </section>
</template>

<style scoped>
.panneau {
  max-width: 40rem;
  margin: 2rem auto;
}
</style>
