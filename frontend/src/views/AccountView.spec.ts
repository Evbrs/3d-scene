/**
 * La page d'abonnement doit dire trois choses justes, et une quatrième sans ambiguïté.
 *
 * 1. la consommation confrontée au plafond, avec « illimité » et jamais « 0 » ;
 * 2. l'essai proposé **tant qu'il est disponible**, et disparu ensuite ;
 * 3. le déclassement expliqué pour ce qu'il est — une mise en lecture seule, pas une suppression.
 *    C'est la phrase qui décide si un client renouvelle ou s'il croit avoir perdu ses chantiers ;
 * 4. aucune règle de facturation recalculée ici : tout vient de la réponse du serveur, pour que la
 *    page ne puisse pas afficher un droit que le serveur refuse.
 */
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AccountView from '@/views/AccountView.vue'

const CATALOGUE = {
  plans: [],
  feature_labels: {},
  limit_labels: {},
  metric_labels: { projects_active: 'Chantiers actifs', exports_pdf: 'Exports PDF' },
  trial_days: 14,
}

function etat(overrides: Record<string, unknown> = {}) {
  return {
    organization_id: 3,
    plan: { code: 'decouverte', name: 'Découverte', tagline: 'Essayer', limits: {}, features: {} },
    subscription: null,
    period_start: '2026-08-01T00:00:00Z',
    period_end: '2026-09-01T00:00:00Z',
    trial_available: true,
    trial_ends_at: null,
    usage: [
      { metric: 'projects_active', value: 1, limit: 1 },
      { metric: 'exports_pdf', value: 4, limit: null },
    ],
    archived_project_ids: [],
    ...overrides,
  }
}

const readSubscription = vi.fn()
const startTrial = vi.fn()

vi.mock('@/api/client', () => ({
  listOrganizations: vi.fn(async () => [{ id: 3, name: 'Entreprise', slug: 'entreprise' }]),
  readPlans: vi.fn(async () => CATALOGUE),
  readSubscription: (...args: unknown[]) => readSubscription(...args),
  startTrial: (...args: unknown[]) => startTrial(...args),
}))

const stubs = { RouterLink: { template: '<a><slot /></a>' } }

beforeEach(() => {
  readSubscription.mockReset()
  startTrial.mockReset()
})

describe('page abonnement', () => {
  it('confronte la consommation au plafond, et dit « illimité » plutôt que zéro', async () => {
    readSubscription.mockResolvedValue(etat())
    const page = mount(AccountView, { global: { stubs } })
    await flushPromises()

    expect(page.text()).toContain('1 / 1')
    expect(page.text()).toContain('4 (illimité)')
    expect(page.text()).not.toContain('4 / 0')
  })

  it('signale un plafond atteint par un texte et pas seulement par une couleur', async () => {
    readSubscription.mockResolvedValue(etat())
    const page = mount(AccountView, { global: { stubs } })
    await flushPromises()

    expect(page.text()).toContain('plafond atteint')
    expect(page.findAll('.atteint')).toHaveLength(1)
  })

  it('propose l’essai tant qu’il est disponible, et l’ouvre sans carte', async () => {
    readSubscription.mockResolvedValue(etat())
    startTrial.mockResolvedValue(
      etat({
        trial_available: false,
        plan: { code: 'artisan', name: 'Artisan', tagline: 'Le solo', limits: {}, features: {} },
        subscription: {
          id: 1,
          plan_code: 'artisan',
          status: 'trialing',
          current_period_start: '2026-08-01T00:00:00Z',
          current_period_end: '2026-08-15T00:00:00Z',
          trial_ends_at: '2026-08-15T00:00:00Z',
          cancel_at: null,
          seats: 1,
        },
        trial_ends_at: '2026-08-15T00:00:00Z',
      }),
    )

    const page = mount(AccountView, { global: { stubs } })
    await flushPromises()

    const bouton = page.find('button')
    expect(bouton.text()).toContain('sans carte')

    await bouton.trigger('click')
    await flushPromises()

    expect(startTrial).toHaveBeenCalledWith(3)
    expect(page.text()).toContain('Artisan')
    expect(page.text()).toContain('Essai en cours')
    expect(page.find('button').exists()).toBe(false)
  })

  it('explique que le déclassement met en lecture seule et ne supprime rien', async () => {
    readSubscription.mockResolvedValue(etat({ archived_project_ids: [11, 12] }))
    const page = mount(AccountView, { global: { stubs } })
    await flushPromises()

    const texte = page.text()
    expect(texte).toContain('lecture seule')
    expect(texte).toContain('pas supprimés')
    expect(texte).toContain('Chantier n° 11')
    expect(texte).toContain('Chantier n° 12')
  })

  it('affiche une erreur du serveur au lieu d’un écran muet', async () => {
    readSubscription.mockRejectedValue(new Error('Ressource introuvable'))
    const page = mount(AccountView, { global: { stubs } })
    await flushPromises()

    expect(page.find('[role="alert"]').text()).toContain('Ressource introuvable')
  })
})
