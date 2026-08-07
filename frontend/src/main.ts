import { createPinia } from 'pinia'
import { createApp } from 'vue'

import { onSessionLost } from '@/api/client'
import App from '@/App.vue'
import { router } from '@/router'
import { useAppStore } from '@/stores/app'
import { useAuthStore } from '@/stores/auth'

// Konva n'est plus installé globalement : `PlanCanvas.vue` importe les composants dont il a
// besoin. Enregistré ici, le moteur (55 Ko gzip) partait dans le chunk d'entrée, donc sur
// l'écran de connexion et sur la page de partage publique, qui n'affichent aucun plan 2D.
const app = createApp(App)
const pinia = createPinia()

app.use(pinia).use(router)

/**
 * Dernier filet des erreurs non rattrapées.
 *
 * Sans lui, une exception dans un composant laisse une page à moitié rendue, sans un mot : la
 * console du navigateur n'est pas une interface utilisateur.
 */
app.config.errorHandler = (caught, _instance, info) => {
  console.error(`Erreur non rattrapée (${info})`, caught)
  useAppStore(pinia).report(caught, info)
}

/** Session définitivement perdue : le rafraîchissement silencieux a échoué (voir `client.ts`). */
onSessionLost(() => {
  useAuthStore(pinia).signOut()
  const current = router.currentRoute.value
  if (current.name === 'connexion') return
  void router.replace({ name: 'connexion', query: { suivant: current.fullPath } })
})

app.mount('#app')
