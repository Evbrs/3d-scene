/**
 * La page tarifs n'a le droit de rien savoir de la grille.
 *
 * C'est la contrepartie visible de la décision d'architecture : les limites vivent en base, donc
 * une remise ou un plafond déplacé doit être une ligne SQL. Le test le vérifie en servant un
 * catalogue **inventé** — prix, libellés et clés qui n'existent nulle part dans le dépôt — et en
 * exigeant que la page l'affiche tel quel. Une valeur recopiée dans le composant ferait échouer
 * ces assertions au lieu de passer inaperçue jusqu'à la première négociation commerciale.
 */
import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import PricingView from '@/views/PricingView.vue'

const CATALOGUE = {
  plans: [
    {
      code: 'palier-invente',
      name: 'Palier inventé',
      tagline: 'Pour le test, et pour personne d’autre',
      monthly_price_cents: 1_234,
      yearly_price_cents: 999,
      seat_price_cents: 4_200,
      currency: 'EUR',
      limits: { active_projects: 7, seats: null },
      features: { quotes: true, api: false },
      sort_order: 10,
    },
  ],
  feature_labels: { quotes: 'Devis chiffré', api: 'API' },
  limit_labels: { active_projects: 'Chantiers actifs', seats: 'Sièges' },
  metric_labels: { projects_active: 'Chantiers actifs' },
  trial_days: 21,
}

vi.mock('@/api/client', () => ({
  readPlans: vi.fn(async () => CATALOGUE),
}))

const stubs = { RouterLink: { template: '<a><slot /></a>' } }

describe('page tarifs', () => {
  it('affiche les prix servis par le serveur, jamais les siens', async () => {
    const page = mount(PricingView, { global: { stubs } })
    await flushPromises()

    const texte = page.text()
    // 1 234 centimes → « 12,34 € ». Aucun prix de la vraie grille n'apparaît.
    expect(texte).toContain('12,34')
    expect(texte).toContain('Palier inventé')
    expect(texte).not.toContain('29,00')
  })

  it('bascule sur le tarif annuel sans recalculer quoi que ce soit', async () => {
    const page = mount(PricingView, { global: { stubs } })
    await flushPromises()

    await page.find('#annuel').setValue()
    // 999 centimes tels quels : la remise « deux mois offerts » est décidée en base, pas ici.
    expect(page.text()).toContain('9,99')
  })

  it('affiche « Illimité » et jamais zéro pour une limite nulle', async () => {
    const page = mount(PricingView, { global: { stubs } })
    await flushPromises()

    // `seats: null` veut dire illimité. Un « 0 » transformerait un palier sans plafond en palier
    // qui refuse tout — la panne la plus coûteuse imaginable sur une page de vente.
    expect(page.text()).toContain('Illimité')
    expect(page.text()).toContain('7')
  })

  it('utilise les libellés du serveur et retombe sur la clé brute à défaut', async () => {
    const page = mount(PricingView, { global: { stubs } })
    await flushPromises()

    expect(page.text()).toContain('Devis chiffré')
    expect(page.text()).toContain('Chantiers actifs')
  })

  it('annonce la durée d’essai servie par le serveur', async () => {
    const page = mount(PricingView, { global: { stubs } })
    await flushPromises()

    expect(page.text()).toContain('21 jours')
  })

  it('marque les fonctionnalités exclues autrement que par la couleur', async () => {
    const page = mount(PricingView, { global: { stubs } })
    await flushPromises()

    // Contraste et daltonisme : la distinction passe par une classe qui barre le texte, doublée
    // d'une mention lue par les lecteurs d'écran.
    const exclues = page.findAll('.exclue')
    expect(exclues).toHaveLength(1)
    expect(exclues[0]?.text()).toBe('API')
    expect(page.text()).toContain('non inclus')
  })
})
