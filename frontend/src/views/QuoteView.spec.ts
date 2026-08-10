/**
 * L'écran du devis : le seul du produit où une erreur d'affichage devient un litige.
 *
 * Ce qui est vérifié ici, dans l'ordre d'importance :
 *
 * 1. **un devis émis se voit comme figé.** Pas une nuance de gris : un encadré, un numéro, une
 *    phrase, et la disparition des gestes qui n'ont plus cours ;
 * 2. **les montants viennent du serveur, en centimes, et ne sont jamais recalculés.** Une saisie
 *    « 24,50 » part en 2450, et le total affiché est celui du serveur ;
 * 3. **les réserves de chiffrage sont dites.** Une face sans ligne rend un devis qui a l'air
 *    complet et ne l'est pas ;
 * 4. **le parcours va jusqu'au bout** : émettre, faire accepter, facturer, télécharger.
 */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import QuoteView from '@/views/QuoteView.vue'

const readQuote = vi.hoisted(() => vi.fn())
const listQuotes = vi.hoisted(() => vi.fn())
const listProjectQuotes = vi.hoisted(() => vi.fn())
const createQuote = vi.hoisted(() => vi.fn())
const issueQuote = vi.hoisted(() => vi.fn())
const updateQuote = vi.hoisted(() => vi.fn())
const convertToInvoice = vi.hoisted(() => vi.fn())
const downloadQuotePdf = vi.hoisted(() => vi.fn())
const downloadInvoicePdf = vi.hoisted(() => vi.fn())
const downloadInvoiceXml = vi.hoisted(() => vi.fn())
const listOrganizations = vi.hoisted(() => vi.fn())
const listPriceBooks = vi.hoisted(() => vi.fn())
const listPriceItems = vi.hoisted(() => vi.fn())

vi.mock('@/api/client', () => ({
  readQuote,
  listQuotes,
  listProjectQuotes,
  createQuote,
  issueQuote,
  updateQuote,
  convertToInvoice,
  downloadQuotePdf,
  downloadInvoicePdf,
  downloadInvoiceXml,
  listOrganizations,
  listPriceBooks,
  listPriceItems,
}))

const stubs = { RouterLink: { template: '<a><slot /></a>' } }

function devis(overrides: Record<string, unknown> = {}) {
  return {
    id: 12,
    organization_id: 1,
    project_id: 7,
    project_name: 'Appartement Dupont',
    status: 'draft',
    number: null,
    invoice_number: null,
    client_name: 'Mme Dupont',
    client_is_consumer: true,
    total_ht_cents: 82_900,
    total_tva_cents: 8290,
    total_ttc_cents: 91_190,
    late_penalty_rate_bp: 1050,
    recovery_indemnity_cents: 4000,
    vat_attestation_required: false,
    valid_until: '2026-11-05T00:00:00Z',
    lines: [
      {
        id: 1,
        position: 1,
        label: 'Peinture acrylique sur murs, 2 couches',
        unit: 'm2',
        quantity: '31.5',
        unit_price_cents: 2400,
        vat_rate_bp: 1000,
        total_ht_cents: 75_600,
        source_face_id: 700,
        source_price_item_code: 'PEINT-MUR',
      },
    ],
    vat_breakdown: [{ rate_bp: 1000, base_cents: 82_900, tax_cents: 8290 }],
    warnings: [],
    ...overrides,
  }
}

function monterDocument() {
  return mount(QuoteView, { props: { quoteId: '12' }, global: { stubs } })
}

beforeEach(() => {
  setActivePinia(createPinia())
  for (const espion of [
    readQuote,
    listQuotes,
    listProjectQuotes,
    createQuote,
    issueQuote,
    updateQuote,
    convertToInvoice,
    downloadQuotePdf,
    downloadInvoicePdf,
    downloadInvoiceXml,
    listOrganizations,
    listPriceBooks,
    listPriceItems,
  ]) {
    espion.mockReset()
  }
  listOrganizations.mockResolvedValue([{ id: 1, name: 'Entreprise', slug: 'entreprise' }])
  listPriceBooks.mockResolvedValue([{ id: 5, organization_id: 1, name: 'Standard', is_default: true }])
  listPriceItems.mockResolvedValue([
    {
      id: 90,
      price_book_id: 5,
      code: 'PEINT-MUR',
      label: 'Peinture acrylique sur murs',
      unit: 'm2',
      unit_price_cents: 2400,
      vat_rate_bp: 1000,
    },
  ])
})

describe('document commercial', () => {
  it('dit qu’un brouillon n’engage personne et propose de l’émettre', async () => {
    readQuote.mockResolvedValue(devis())
    const page = monterDocument()
    await flushPromises()

    expect(page.find('.brouillon').text()).toContain("il n'engage")
    expect(page.find('.fige').exists()).toBe(false)
    expect(page.findAll('button').some((b) => b.text().includes('Émettre'))).toBe(true)
  })

  it('rend visible qu’un devis émis est figé, et retire le bouton d’émission', async () => {
    readQuote.mockResolvedValue(
      devis({ status: 'sent', number: 'DEV-2026-0042', issued_at: '2026-08-07T09:00:00Z' }),
    )
    const page = monterDocument()
    await flushPromises()

    const encadre = page.find('.fige')
    expect(encadre.exists()).toBe(true)
    expect(encadre.text()).toContain('DEV-2026-0042')
    expect(encadre.text()).toContain('ne peut plus être modifié')
    // Le figement n'est pas qu'une phrase : le geste qui n'a plus cours disparaît.
    expect(page.findAll('button').some((b) => b.text().includes('Émettre'))).toBe(false)
  })

  it('affiche les montants tels que le serveur les a figés, en centimes', async () => {
    readQuote.mockResolvedValue(devis())
    const page = monterDocument()
    await flushPromises()

    const texte = page.text().replace(/[\u202f\u00a0]/g, ' ')
    expect(texte).toContain('24,00 €')
    expect(texte).toContain('756,00 €')
    expect(texte).toContain('829,00 €')
    expect(texte).toContain('82,90 €')
    expect(texte).toContain('911,90 €')
    // La quantité est rendue telle quelle : elle voyage en chaîne décimale.
    expect(texte).toContain('31.5')
  })

  it('rattache chaque ligne à la face dont elle vient', async () => {
    readQuote.mockResolvedValue(devis())
    const page = monterDocument()
    await flushPromises()

    expect(page.text()).toContain('n° 700')
  })

  it('nomme les réserves de chiffrage plutôt que de laisser croire à un devis complet', async () => {
    readQuote.mockResolvedValue(
      devis({ warnings: ["Séjour, face B : aucun prix ne correspond au revêtement « toile »."] }),
    )
    const page = monterDocument()
    await flushPromises()

    const alerte = page.find('.reserves')
    expect(alerte.attributes('role')).toBe('alert')
    expect(alerte.text()).toContain('toile')
    expect(alerte.text()).toContain("l'air complet")
  })

  it('mène le parcours jusqu’à la facture', async () => {
    readQuote.mockResolvedValue(devis({ status: 'sent', number: 'DEV-2026-0042' }))
    updateQuote.mockResolvedValue(devis({ status: 'accepted', number: 'DEV-2026-0042' }))
    convertToInvoice.mockResolvedValue(
      devis({ status: 'invoiced', number: 'DEV-2026-0042', invoice_number: 'FAC-2026-0007' }),
    )
    const page = monterDocument()
    await flushPromises()

    await page
      .findAll('button')
      .find((b) => b.text().includes('accepté'))
      ?.trigger('click')
    await flushPromises()
    expect(updateQuote).toHaveBeenCalledWith(12, { status: 'accepted' })

    await page
      .findAll('button')
      .find((b) => b.text() === 'Facturer')
      ?.trigger('click')
    await flushPromises()

    expect(convertToInvoice).toHaveBeenCalledWith(12)
    expect(page.text()).toContain('FAC-2026-0007')
    // Les deux formats de la facture n'apparaissent qu'une fois la facture établie.
    expect(page.findAll('button').some((b) => b.text().includes('Factur-X'))).toBe(true)
    expect(page.findAll('button').some((b) => b.text().includes('XML CII'))).toBe(true)
  })

  it('télécharge le devis en PDF', async () => {
    readQuote.mockResolvedValue(devis())
    downloadQuotePdf.mockResolvedValue(new Blob(['%PDF']))
    const page = monterDocument()
    await flushPromises()

    await page
      .findAll('button')
      .find((b) => b.text().includes('Télécharger le devis'))
      ?.trigger('click')
    await flushPromises()

    expect(downloadQuotePdf).toHaveBeenCalledWith(12)
  })

  it('rappelle que nous ne transmettons rien à l’administration', async () => {
    readQuote.mockResolvedValue(devis({ status: 'invoiced', invoice_number: 'FAC-2026-0007' }))
    const page = monterDocument()
    await flushPromises()

    expect(page.text()).toContain('pas une plateforme de dématérialisation agréée')
  })

  it('affiche l’erreur du serveur au lieu d’un écran muet', async () => {
    readQuote.mockRejectedValue(new Error('Ressource introuvable'))
    const page = monterDocument()
    await flushPromises()

    expect(page.find('[role="alert"]').text()).toContain('Ressource introuvable')
  })
})

describe('préparation d’un devis depuis un chantier', () => {
  function monterChantier() {
    return mount(QuoteView, { props: { projectId: '7' }, global: { stubs } })
  }

  it('convertit la saisie en centimes entiers avant de créer', async () => {
    listProjectQuotes.mockResolvedValue([])
    createQuote.mockResolvedValue(devis())
    const page = monterChantier()
    await flushPromises()

    await page.find('#client-nom').setValue('Mme Dupont')
    await page.find('#code-wall').setValue('PEINT-MUR')
    await page
      .findAll('button')
      .find((b) => b.text() === 'Ajouter une ligne')
      ?.trigger('click')
    await page.find('#ligne-libelle-0').setValue('Protection des sols')
    await page.find('#ligne-prix-0').setValue('8,29')
    await page.find('#ligne-quantite-0').setValue('1')
    await page.find('form').trigger('submit')
    await flushPromises()

    expect(createQuote).toHaveBeenCalledTimes(1)
    const [chantier, charge] = createQuote.mock.calls[0] as [number, Record<string, unknown>]
    expect(chantier).toBe(7)
    expect(charge.default_price_codes).toEqual({ wall: 'PEINT-MUR' })
    // 8,29 € valent 829 centimes. Le chemin par un flottant en aurait rendu 828.
    expect(charge.extra_lines).toEqual([
      {
        label: 'Protection des sols',
        unit: 'forfait',
        quantity: '1',
        unit_price_cents: 829,
        vat_rate_bp: 1000,
      },
    ])
  })

  it('refuse de créer sans nom de client, et le dit sur le champ', async () => {
    listProjectQuotes.mockResolvedValue([])
    const page = monterChantier()
    await flushPromises()

    await page.find('form').trigger('submit')
    await flushPromises()

    expect(createQuote).not.toHaveBeenCalled()
    const champ = page.find('#client-nom')
    expect(champ.attributes('aria-invalid')).toBe('true')
    expect(page.find(`#${champ.attributes('aria-describedby')}`).text()).toContain('obligatoire')
  })

  it('propose les codes du barème plutôt qu’une saisie à l’aveugle', async () => {
    listProjectQuotes.mockResolvedValue([])
    const page = monterChantier()
    await flushPromises()

    expect(listPriceItems).toHaveBeenCalledWith(5)
    expect(page.find('#code-wall').text()).toContain('PEINT-MUR')
  })

  it('dit qu’aucun devis n’existe encore, sans passer pour une panne', async () => {
    listProjectQuotes.mockResolvedValue([])
    const page = monterChantier()
    await flushPromises()

    expect(page.text()).toContain('Aucun devis pour ce chantier')
  })
})

describe('liste de tous les devis', () => {
  it('affiche les documents de l’entreprise avec leurs totaux', async () => {
    listQuotes.mockResolvedValue([
      {
        id: 12,
        status: 'invoiced',
        number: 'DEV-2026-0042',
        invoice_number: 'FAC-2026-0007',
        client_name: 'Mme Dupont',
        total_ht_cents: 82_900,
        total_ttc_cents: 91_190,
      },
    ])
    const page = mount(QuoteView, { global: { stubs } })
    await flushPromises()

    const texte = page.text().replace(/[\u202f\u00a0]/g, ' ')
    expect(texte).toContain('FAC-2026-0007')
    expect(texte).toContain('Facturé')
    expect(texte).toContain('911,90 €')
  })

  it('explique l’espace vide au lieu de l’afficher nu', async () => {
    listQuotes.mockResolvedValue([])
    const page = mount(QuoteView, { global: { stubs } })
    await flushPromises()

    expect(page.text()).toContain('Un devis se crée depuis le métré')
  })
})
