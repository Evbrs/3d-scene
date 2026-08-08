/**
 * La pose du nouveau mot de passe.
 *
 * Deux défauts enferment l'utilisateur dehors une seconde fois, et aucun des deux ne peut être
 * rattrapé côté serveur : un jeton tronqué par la messagerie, et une faute de frappe sur un mot
 * de passe qu'on ne relit jamais. Ce sont les deux cas couverts ici.
 */
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ResetPasswordView from '@/views/ResetPasswordView.vue'

const resetPassword = vi.fn()
const push = vi.fn()
const query: { jeton?: string } = {}

vi.mock('@/api/client', () => ({
  resetPassword: (token: string, motDePasse: string) => resetPassword(token, motDePasse),
}))

vi.mock('vue-router', () => ({
  RouterLink: { template: '<a><slot /></a>' },
  useRoute: () => ({ query }),
  useRouter: () => ({ push }),
}))

const MOT_DE_PASSE = 'un-nouveau-mot-de-passe-2026'

function render() {
  return mount(ResetPasswordView)
}

beforeEach(() => {
  resetPassword.mockReset()
  push.mockReset()
  query.jeton = 'jeton-valide'
})

describe('réinitialisation du mot de passe', () => {
  it('refuse d’afficher le formulaire sans jeton et renvoie en demander un', () => {
    delete query.jeton

    const view = render()

    expect(view.find('form').exists()).toBe(false)
    expect(view.get('[role="alert"]').text()).toContain('tronqué')
  })

  it('n’appelle pas le serveur quand les deux saisies diffèrent', async () => {
    const view = render()
    await view.get('#nouveau').setValue(MOT_DE_PASSE)
    await view.get('#confirmation').setValue('autre-mot-de-passe-2026')
    await view.get('form').trigger('submit')
    await flushPromises()

    expect(resetPassword).not.toHaveBeenCalled()
    expect(view.text()).toContain('Les deux saisies diffèrent')
  })

  it('envoie le jeton de l’URL et le mot de passe saisi', async () => {
    resetPassword.mockResolvedValue({ detail: 'Mot de passe mis à jour.' })

    const view = render()
    await view.get('#nouveau').setValue(MOT_DE_PASSE)
    await view.get('#confirmation').setValue(MOT_DE_PASSE)
    await view.get('form').trigger('submit')
    await flushPromises()

    expect(resetPassword).toHaveBeenCalledWith('jeton-valide', MOT_DE_PASSE)
  })

  it('renvoie vers la connexion et non vers les projets', async () => {
    // La réinitialisation ferme toutes les sessions, y compris celle qu'on aurait pu croire
    // ouverte dans cet onglet : aller aux projets afficherait un 401 immédiat.
    resetPassword.mockResolvedValue({ detail: 'Mot de passe mis à jour.' })

    const view = render()
    await view.get('#nouveau').setValue(MOT_DE_PASSE)
    await view.get('#confirmation').setValue(MOT_DE_PASSE)
    await view.get('form').trigger('submit')
    await flushPromises()

    expect(push).toHaveBeenCalledWith({ name: 'connexion' })
  })

  it('affiche le refus du serveur pour un lien expiré', async () => {
    resetPassword.mockRejectedValue(
      new Error('Lien de réinitialisation invalide ou expiré. Demandez-en un nouveau.'),
    )

    const view = render()
    await view.get('#nouveau').setValue(MOT_DE_PASSE)
    await view.get('#confirmation').setValue(MOT_DE_PASSE)
    await view.get('form').trigger('submit')
    await flushPromises()

    expect(view.get('[role="alert"]').text()).toContain('invalide ou expiré')
    expect(push).not.toHaveBeenCalled()
  })
})
