/**
 * La page « Mon compte » porte le seul bouton irréversible du produit.
 *
 * Ce qui est vérifié ici tient en une phrase : **on ne ferme pas un compte par accident**. Le mot
 * de passe est enregistré dans le gestionnaire du navigateur et se saisit sans y penser ; il
 * prouve l'identité, pas l'intention. Le mot à recopier, lui, oblige à relire la phrase.
 *
 * Le reste — changement de mot de passe, export de portabilité — est vérifié pour ce qu'il
 * promet : la confirmation double, et le fait que l'export soit lisible même quand le
 * téléchargement n'est pas possible.
 */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AccountSettingsView from '@/views/AccountSettingsView.vue'

const changePassword = vi.fn()
const exportAccount = vi.fn()
const deleteAccount = vi.fn()
const push = vi.fn()

vi.mock('@/api/client', () => ({
  changePassword: (actuel: string, nouveau: string) => changePassword(actuel, nouveau),
  exportAccount: () => exportAccount(),
  deleteAccount: (motDePasse: string) => deleteAccount(motDePasse),
  // Le store de session s'en sert dans `signOut`, appelé juste après la fermeture du compte.
  clearToken: () => undefined,
  storedToken: () => null,
}))

vi.mock('vue-router', () => ({
  RouterLink: { template: '<a><slot /></a>' },
  useRouter: () => ({ push }),
}))

const ANCIEN = 'motdepasse-utilisateur-2026'
const NOUVEAU = 'un-nouveau-mot-de-passe-2026'

function render() {
  return mount(AccountSettingsView)
}

beforeEach(() => {
  setActivePinia(createPinia())
  changePassword.mockReset()
  exportAccount.mockReset()
  deleteAccount.mockReset()
  push.mockReset()
})

describe('mot de passe', () => {
  it('n’envoie rien quand la confirmation ne correspond pas', async () => {
    const view = render()
    await view.get('#actuel').setValue(ANCIEN)
    await view.get('#nouveau-motdepasse').setValue(NOUVEAU)
    await view.get('#confirmation-motdepasse').setValue('autre-mot-de-passe-2026')
    await view.get('form').trigger('submit')
    await flushPromises()

    expect(changePassword).not.toHaveBeenCalled()
  })

  it('annonce que les autres sessions ont été fermées', async () => {
    // C'est la conséquence la plus importante et la moins attendue du geste : sans le dire,
    // l'utilisateur croit à une panne en voyant son téléphone se déconnecter.
    changePassword.mockResolvedValue(undefined)

    const view = render()
    await view.get('#actuel').setValue(ANCIEN)
    await view.get('#nouveau-motdepasse').setValue(NOUVEAU)
    await view.get('#confirmation-motdepasse').setValue(NOUVEAU)
    await view.get('form').trigger('submit')
    await flushPromises()

    expect(changePassword).toHaveBeenCalledWith(ANCIEN, NOUVEAU)
    expect(view.text()).toContain('autres sessions ont été fermées')
  })

  it('affiche le refus du serveur quand le mot de passe actuel est faux', async () => {
    changePassword.mockRejectedValue(new Error('Mot de passe actuel incorrect'))

    const view = render()
    await view.get('#actuel').setValue('faux')
    await view.get('#nouveau-motdepasse').setValue(NOUVEAU)
    await view.get('#confirmation-motdepasse').setValue(NOUVEAU)
    await view.get('form').trigger('submit')
    await flushPromises()

    expect(view.get('[role="alert"]').text()).toContain('Mot de passe actuel incorrect')
  })
})

describe('portabilité', () => {
  it('rend l’export lisible même sans téléchargement possible', async () => {
    // Un droit d'accès qui dépend de `URL.createObjectURL` n'en est pas un.
    exportAccount.mockResolvedValue({ compte: { email: 'titulaire@exemple.fr' } })

    const view = render()
    await view.get('section[aria-labelledby="titre-donnees"] button').trigger('click')
    await flushPromises()

    expect(view.get('pre').text()).toContain('titulaire@exemple.fr')
  })

  it('signale un export refusé plutôt que de rester silencieux', async () => {
    exportAccount.mockRejectedValue(new Error('Erreur HTTP 500'))

    const view = render()
    await view.get('section[aria-labelledby="titre-donnees"] button').trigger('click')
    await flushPromises()

    expect(view.text()).toContain('Erreur HTTP 500')
  })
})

describe('fermeture du compte', () => {
  function zoneDeDanger(view: ReturnType<typeof render>) {
    return view.get('section[aria-labelledby="titre-fermeture"]')
  }

  it('garde le bouton inactif tant que le mot de confirmation n’est pas recopié', async () => {
    const view = render()
    await view.get('#motdepasse-suppression').setValue(ANCIEN)

    expect(zoneDeDanger(view).get('button').attributes('disabled')).toBeDefined()
  })

  it('garde le bouton inactif tant que le mot de passe n’est pas saisi', async () => {
    const view = render()
    await view.get('#confirmation-suppression').setValue('SUPPRIMER')

    expect(zoneDeDanger(view).get('button').attributes('disabled')).toBeDefined()
  })

  it('ferme le compte et renvoie à la connexion quand les deux conditions sont réunies', async () => {
    deleteAccount.mockResolvedValue(undefined)

    const view = render()
    await view.get('#motdepasse-suppression').setValue(ANCIEN)
    await view.get('#confirmation-suppression').setValue('supprimer')
    await zoneDeDanger(view).get('form').trigger('submit')
    await flushPromises()

    expect(deleteAccount).toHaveBeenCalledWith(ANCIEN)
    expect(push).toHaveBeenCalledWith({ name: 'connexion' })
  })

  it('affiche en clair le refus du dernier propriétaire', async () => {
    // Le message du serveur nomme les organisations à transmettre : le remplacer par un texte
    // générique retirerait la seule information dont l'utilisateur a besoin pour agir.
    deleteAccount.mockRejectedValue(
      new Error('Vous êtes le dernier propriétaire de : Entreprise Dupont.'),
    )

    const view = render()
    await view.get('#motdepasse-suppression').setValue(ANCIEN)
    await view.get('#confirmation-suppression').setValue('SUPPRIMER')
    await zoneDeDanger(view).get('form').trigger('submit')
    await flushPromises()

    expect(view.text()).toContain('Entreprise Dupont')
    expect(push).not.toHaveBeenCalled()
  })
})
