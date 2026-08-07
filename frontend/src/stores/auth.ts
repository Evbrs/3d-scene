/** Session utilisateur. Le jeton vit dans `sessionStorage`, jamais dans un état global exporté. */
import { defineStore } from 'pinia'
import { ref } from 'vue'

import * as api from '@/api/client'
import type { CurrentUser } from '@/api/client'

/**
 * Issue d'une inscription.
 *
 * Le serveur répond toujours 202 sans dire si l'adresse était libre (anti-énumération). La
 * connexion enchaînée est donc le seul retour fiable — mais son échec ne signifie pas
 * « identifiants invalides » : il signifie « cette adresse est probablement déjà inscrite ».
 * Confondre les deux enfermait l'utilisateur sur un écran « Créer un compte » lui reprochant des
 * identifiants qu'il venait de choisir.
 */
export type SignUpOutcome = 'connecte' | 'a-confirmer' | 'echec'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<CurrentUser | null>(null)
  const error = ref<string | null>(null)
  /** Message neutre et non bloquant, distinct d'une erreur (`role="status"` côté vue). */
  const notice = ref<string | null>(null)
  const pending = ref(false)

  async function signIn(email: string, password: string): Promise<boolean> {
    pending.value = true
    error.value = null
    notice.value = null
    try {
      await api.login(email, password)
      user.value = await api.currentUser()
      return true
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : String(caught)
      return false
    } finally {
      pending.value = false
    }
  }

  async function signUp(email: string, password: string): Promise<SignUpOutcome> {
    pending.value = true
    error.value = null
    notice.value = null
    let accepted: string
    try {
      accepted = await api.register(email, password)
    } catch (caught) {
      // Seul cas d'échec franc : refus de validation ou limitation de débit.
      error.value = caught instanceof Error ? caught.message : String(caught)
      pending.value = false
      return 'echec'
    }
    pending.value = false

    if (await signIn(email, password)) return 'connecte'
    // On rend le message du serveur, pas le « Identifiants invalides » de la connexion.
    error.value = null
    notice.value = accepted
    return 'a-confirmer'
  }

  async function restore(): Promise<void> {
    if (!api.storedToken()) return
    try {
      user.value = await api.currentUser()
    } catch {
      api.clearToken()
      user.value = null
    }
  }

  function signOut(): void {
    api.clearToken()
    user.value = null
    error.value = null
    notice.value = null
  }

  return { user, error, notice, pending, signIn, signUp, restore, signOut }
})
