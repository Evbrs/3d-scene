/**
 * L'entreprise et son équipe : deux choses qu'on ne pouvait pas faire sans ouvrir une console SQL.
 *
 * Ce qui est surveillé :
 *
 * 1. **les mentions légales manquantes sont nommées.** Le serveur accepte une entreprise sans
 *    SIRET ni décennale ; le devis qu'elle émettra ne sera pas valable pour autant, et personne
 *    ne le découvrira avant le client ;
 * 2. **le capital voyage en centimes entiers**, comme tout montant du produit ;
 * 3. **le jeton d'invitation est présenté pour ce qu'il est** : un secret affiché une seule fois ;
 * 4. **le refus du serveur est montré tel quel** — c'est lui qui protège le dernier propriétaire,
 *    pas une règle recopiée ici qui finirait par diverger.
 */
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import TeamView from '@/views/TeamView.vue'

const listOrganizations = vi.hoisted(() => vi.fn())
const readOrganization = vi.hoisted(() => vi.fn())
const updateOrganization = vi.hoisted(() => vi.fn())
const listMembers = vi.hoisted(() => vi.fn())
const updateMemberRole = vi.hoisted(() => vi.fn())
const removeMember = vi.hoisted(() => vi.fn())
const listInvitations = vi.hoisted(() => vi.fn())
const inviteMember = vi.hoisted(() => vi.fn())
const acceptInvitation = vi.hoisted(() => vi.fn())

vi.mock('@/api/client', () => ({
  listOrganizations,
  readOrganization,
  updateOrganization,
  listMembers,
  updateMemberRole,
  removeMember,
  listInvitations,
  inviteMember,
  acceptInvitation,
}))

/** Route factice : la vue n'en lit que `query.invitation`. */
const requete: { invitation?: string } = {}
vi.mock('vue-router', () => ({
  RouterLink: { template: '<a><slot /></a>' },
  useRoute: () => ({ query: requete }),
}))

const NUE = {
  id: 4,
  name: 'Bâti Rénov',
  slug: 'bati-renov',
  created_at: '2026-01-05T10:00:00Z',
}

const COMPLETE = {
  ...NUE,
  siret: '12345678901234',
  legal_form: 'SARL',
  share_capital_cents: 750_000,
  rcs: 'Versailles B 123 456 789',
  vat_number: 'FR12345678901',
  address_line1: '3 rue des Artisans',
  postal_code: '78000',
  city: 'Versailles',
  decennial_insurer: 'AXA',
  decennial_policy_number: 'POL-12345',
  decennial_coverage_area: 'France métropolitaine',
}

beforeEach(() => {
  delete requete.invitation
  for (const espion of [
    listOrganizations,
    readOrganization,
    updateOrganization,
    listMembers,
    updateMemberRole,
    removeMember,
    listInvitations,
    inviteMember,
    acceptInvitation,
  ]) {
    espion.mockReset()
  }
  listOrganizations.mockResolvedValue([NUE])
  readOrganization.mockResolvedValue(COMPLETE)
  listMembers.mockResolvedValue([
    { user_id: 1, email: 'patron@bati.fr', role: 'owner', accepted_at: '2026-01-05T10:00:00Z' },
    { user_id: 2, email: 'compagnon@bati.fr', role: 'editor', accepted_at: '2026-03-02T10:00:00Z' },
  ])
  listInvitations.mockResolvedValue([])
})

function monter() {
  return mount(TeamView)
}

describe('identité de l’entreprise', () => {
  it('remplit le formulaire depuis le serveur, capital compris', async () => {
    const page = monter()
    await flushPromises()

    expect(readOrganization).toHaveBeenCalledWith(4)
    expect((page.find('#ent-siret').element as HTMLInputElement).value).toBe('12345678901234')
    expect((page.find('#ent-capital').element as HTMLInputElement).value).toBe('7500,00')
    expect((page.find('#ent-assureur').element as HTMLInputElement).value).toBe('AXA')
  })

  it('nomme les mentions manquantes plutôt que de laisser émettre un devis invalide', async () => {
    readOrganization.mockResolvedValue(NUE)
    const page = monter()
    await flushPromises()

    const alerte = page.find('.manquantes')
    expect(alerte.attributes('role')).toBe('alert')
    const texte = alerte.text()
    for (const mention of ['SIRET', 'Forme juridique', 'RCS', 'Assureur décennale']) {
      expect(texte).toContain(mention)
    }
    expect(texte).toContain('pas valable')
  })

  it('fait disparaître l’alerte dès que tout est renseigné', async () => {
    const page = monter()
    await flushPromises()

    expect(page.find('.manquantes').exists()).toBe(false)
  })

  it('envoie le capital en centimes entiers et les champs vides à null', async () => {
    readOrganization.mockResolvedValue({ ...COMPLETE, decennial_coverage_area: null })
    updateOrganization.mockResolvedValue(COMPLETE)
    const page = monter()
    await flushPromises()

    await page.find('#ent-capital').setValue('7500,50')
    await page.find('form').trigger('submit')
    await flushPromises()

    const [identifiant, charge] = updateOrganization.mock.calls[0] as [
      number,
      Record<string, unknown>,
    ]
    expect(identifiant).toBe(4)
    expect(charge.share_capital_cents).toBe(750_050)
    // Une chaîne vide serait refusée par les champs à motif : c'est `null` qui retire la mention.
    expect(charge.decennial_coverage_area).toBeNull()
  })

  it('refuse un SIRET qui n’a pas quatorze chiffres, sur le champ concerné', async () => {
    const page = monter()
    await flushPromises()

    await page.find('#ent-siret').setValue('1234')
    await page.find('form').trigger('submit')
    await flushPromises()

    expect(updateOrganization).not.toHaveBeenCalled()
    const champ = page.find('#ent-siret')
    expect(champ.attributes('aria-invalid')).toBe('true')
    expect(page.find(`#${champ.attributes('aria-describedby')}`).text()).toContain('14 chiffres')
  })

  it('affiche l’erreur du serveur au lieu d’un écran muet', async () => {
    listOrganizations.mockRejectedValue(new Error('Ressource introuvable'))
    const page = monter()
    await flushPromises()

    expect(page.find('[role="alert"]').text()).toContain('Ressource introuvable')
  })
})

describe('équipe', () => {
  it('liste les membres avec leur rôle', async () => {
    const page = monter()
    await flushPromises()

    expect(page.text()).toContain('patron@bati.fr')
    expect(page.text()).toContain('compagnon@bati.fr')
    expect((page.find('#role-2').element as HTMLSelectElement).value).toBe('editor')
  })

  it('change un rôle et relit l’équipe', async () => {
    updateMemberRole.mockResolvedValue({ user_id: 2, email: 'compagnon@bati.fr', role: 'admin' })
    const page = monter()
    await flushPromises()

    await page.find('#role-2').setValue('admin')
    await flushPromises()

    expect(updateMemberRole).toHaveBeenCalledWith(4, 2, 'admin')
    expect(listMembers).toHaveBeenCalledTimes(2)
  })

  it('montre le refus du serveur et revient à ce qui fait foi', async () => {
    updateMemberRole.mockRejectedValue(
      new Error('Une organisation doit garder au moins un propriétaire.'),
    )
    const page = monter()
    await flushPromises()

    await page.find('#role-1').setValue('viewer')
    await flushPromises()

    expect(page.find('[role="alert"]').text()).toContain('au moins un propriétaire')
    // La liste est relue : le sélecteur revient au rôle que le serveur connaît.
    expect((page.find('#role-1').element as HTMLSelectElement).value).toBe('owner')
  })

  it('retire un membre après confirmation', async () => {
    vi.stubGlobal('confirm', vi.fn(() => true))
    removeMember.mockResolvedValue(undefined)
    const page = monter()
    await flushPromises()

    await page
      .findAll('button')
      .find((b) => b.text().includes('Retirer compagnon@bati.fr'))
      ?.trigger('click')
    await flushPromises()

    expect(removeMember).toHaveBeenCalledWith(4, 2)
    vi.unstubAllGlobals()
  })
})

describe('invitations', () => {
  it('affiche le jeton une seule fois, en le disant', async () => {
    inviteMember.mockResolvedValue({
      id: 8,
      organization_id: 4,
      email: 'nouveau@bati.fr',
      role: 'editor',
      expires_at: '2026-08-15T10:00:00Z',
      token: 'jeton-en-clair-abcdef',
    })
    const page = monter()
    await flushPromises()

    await page.find('#invitation-email').setValue('nouveau@bati.fr')
    await page.find('form.formulaire').trigger('submit')
    await flushPromises()

    expect(inviteMember).toHaveBeenCalledWith(4, 'nouveau@bati.fr', 'editor', 7)
    const encadre = page.find('.jeton')
    expect(encadre.text()).toContain('jeton-en-clair-abcdef')
    expect(encadre.text()).toContain("affiché qu'une fois")
    expect(encadre.text()).toContain('nouveau@bati.fr')
  })

  it('n’affiche aucun jeton tant qu’aucune invitation n’a été émise', async () => {
    const page = monter()
    await flushPromises()

    expect(page.find('.jeton').exists()).toBe(false)
  })

  it('préremplit le jeton reçu dans le lien d’invitation', async () => {
    requete.invitation = 'jeton-recu-par-courriel'
    const page = monter()
    await flushPromises()

    expect((page.find('#jeton-invitation').element as HTMLInputElement).value).toBe(
      'jeton-recu-par-courriel',
    )
  })

  it('accepte l’invitation et annonce le rôle obtenu', async () => {
    requete.invitation = 'jeton-recu-par-courriel'
    acceptInvitation.mockResolvedValue({ user_id: 3, email: 'moi@bati.fr', role: 'editor' })
    const page = monter()
    await flushPromises()

    const formulaires = page.findAll('form.formulaire')
    await formulaires[formulaires.length - 1]?.trigger('submit')
    await flushPromises()

    expect(acceptInvitation).toHaveBeenCalledWith('jeton-recu-par-courriel')
    expect(page.find('[role="status"]').text()).toContain('editor')
  })

  it('montre le refus d’un jeton périmé ou adressé à quelqu’un d’autre', async () => {
    requete.invitation = 'jeton-perime'
    acceptInvitation.mockRejectedValue(new Error('Invitation introuvable ou expirée'))
    const page = monter()
    await flushPromises()

    const formulaires = page.findAll('form.formulaire')
    await formulaires[formulaires.length - 1]?.trigger('submit')
    await flushPromises()

    expect(page.find('[role="alert"]').text()).toContain('introuvable ou expirée')
  })

  it('reste utilisable pour un préparateur qui ne voit pas les invitations', async () => {
    // La route des invitations demande le rôle `admin` : son 403 ne doit pas emporter l'écran.
    listInvitations.mockRejectedValue(new Error('Droits insuffisants'))
    const page = monter()
    await flushPromises()

    expect(page.text()).toContain('patron@bati.fr')
    expect(page.find('.erreur[role="alert"]').exists()).toBe(false)
  })
})
