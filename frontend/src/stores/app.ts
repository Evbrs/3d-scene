/**
 * État transverse de l'application.
 *
 * Ne porte pour l'instant que l'erreur non rattrapée remontée par `app.config.errorHandler` :
 * une exception dans un composant laissait jusqu'ici une page à moitié rendue et silencieuse,
 * la console du navigateur ne valant pas interface utilisateur.
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useAppStore = defineStore('app', () => {
  const fatalError = ref<string | null>(null)

  function report(caught: unknown, origin: string): void {
    fatalError.value = `${caught instanceof Error ? caught.message : String(caught)} (${origin})`
  }

  function dismiss(): void {
    fatalError.value = null
  }

  return { fatalError, report, dismiss }
})
