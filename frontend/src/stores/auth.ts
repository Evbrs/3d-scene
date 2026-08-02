/** Session utilisateur. Le jeton vit dans `sessionStorage`, jamais dans un état global exporté. */
import { defineStore } from 'pinia'
import { ref } from 'vue'

import * as api from '@/api/client'
import type { CurrentUser } from '@/api/client'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<CurrentUser | null>(null)
  const error = ref<string | null>(null)
  const pending = ref(false)

  async function signIn(email: string, password: string): Promise<boolean> {
    pending.value = true
    error.value = null
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

  async function signUp(email: string, password: string): Promise<boolean> {
    pending.value = true
    error.value = null
    try {
      await api.register(email, password)
      // L'inscription ne dit jamais si l'adresse était libre (anti-énumération) : on enchaîne
      // donc sur une connexion, qui est le seul retour fiable.
      return await signIn(email, password)
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : String(caught)
      return false
    } finally {
      pending.value = false
    }
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
  }

  return { user, error, pending, signIn, signUp, restore, signOut }
})
