/**
 * L'écran de demande de réinitialisation.
 *
 * Ce qui est vérifié ici n'est pas « le formulaire marche » mais « le formulaire ne trahit pas ».
 * Le serveur répond 202 avec le même corps que l'adresse soit inscrite ou non ; une vue qui
 * distinguerait les deux cas — un « compte introuvable », un état d'erreur, une redirection —
 * réintroduirait dans l'interface l'oracle d'énumération que l'API refuse d'offrir.
 */
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ForgotPasswordView from '@/views/ForgotPasswordView.vue'

const forgotPassword = vi.fn()

vi.mock('@/api/client', () => ({
  forgotPassword: (email: string) => forgotPassword(email),
}))

const MESSAGE_CONSTANT =
  'Si cette adresse correspond à un compte, un lien de réinitialisation vient d’être envoyé.'

function render() {
  return mount(ForgotPasswordView, {
    global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
  })
}

async function submit(email: string) {
  const view = render()
  await view.get('#email-oubli').setValue(email)
  await view.get('form').trigger('submit')
  await flushPromises()
  return view
}

beforeEach(() => {
  forgotPassword.mockReset()
})

describe('mot de passe oublié', () => {
  it('affiche le message du serveur, quel que soit le sort de l’adresse', async () => {
    forgotPassword.mockResolvedValue({ detail: MESSAGE_CONSTANT, reset_token: null })

    const connue = await submit('titulaire@exemple.fr')
    const inconnue = await submit('personne@exemple.fr')

    expect(connue.text()).toContain(MESSAGE_CONSTANT)
    expect(inconnue.text()).toContain(MESSAGE_CONSTANT)
    expect(connue.find('[role="alert"]').exists()).toBe(false)
  })

  it('rend le message en `status` et non en `alert`', async () => {
    // Une réponse normale annoncée comme une erreur par le lecteur d'écran fait croire à un échec
    // à la seule personne qui ne peut pas vérifier visuellement le contraire.
    forgotPassword.mockResolvedValue({ detail: MESSAGE_CONSTANT, reset_token: null })

    const view = await submit('titulaire@exemple.fr')

    expect(view.get('[role="status"]').text()).toContain(MESSAGE_CONSTANT)
  })

  it('propose le lien de poursuite quand le serveur rend un jeton de développement', async () => {
    forgotPassword.mockResolvedValue({ detail: MESSAGE_CONSTANT, reset_token: 'jeton-de-test' })

    const view = await submit('titulaire@exemple.fr')

    expect(view.text()).toContain('Environnement de développement')
    expect(view.text()).toContain('Poursuivre la réinitialisation')
  })

  it('ne montre rien de tel en production, où le serveur ne rend aucun jeton', async () => {
    forgotPassword.mockResolvedValue({ detail: MESSAGE_CONSTANT, reset_token: null })

    const view = await submit('titulaire@exemple.fr')

    expect(view.text()).not.toContain('Environnement de développement')
  })

  it('signale une limitation de débit comme une vraie erreur', async () => {
    // C'est le seul échec franc de cette route : il concerne l'appelant, pas l'existence du
    // compte, et le taire laisserait l'utilisateur réessayer en boucle.
    forgotPassword.mockRejectedValue(new Error('Trop de tentatives'))

    const view = await submit('titulaire@exemple.fr')

    expect(view.get('[role="alert"]').text()).toContain('Trop de tentatives')
  })
})
