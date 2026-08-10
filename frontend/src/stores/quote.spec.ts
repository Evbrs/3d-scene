/**
 * L'arithmétique de l'argent, et le figement d'un devis émis.
 *
 * Ce fichier ne surveille pas de l'affichage : il surveille deux endroits où une erreur se paie.
 *
 * 1. **Le centime.** Le serveur travaille en centimes entiers et en `Decimal`. Dès que le
 *    navigateur repasse par un flottant, il perd un centime sur des montants parfaitement banals
 *    — `parseFloat('8.29') * 100` vaut `828.9999999999999`. Les tests ci-dessous exigent la
 *    valeur exacte sur les saisies où le flottant décroche, et vérifient que le chemin fautif
 *    donnait bien un résultat faux : sans cette seconde assertion, le test passerait aussi avec
 *    l'implémentation cassée.
 * 2. **Le contrat.** Un devis émis est figé côté serveur. Une interface qui l'ignore propose une
 *    modification qui finira en 409, après que l'utilisateur a cru la faire.
 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Quote } from '@/api/types'
import {
  LIBELLES_STATUT,
  centsFromInput,
  estFige,
  formatCents,
  formatMesure,
  formatRateBp,
  inputFromCents,
  useQuoteStore,
} from '@/stores/quote'

const readQuote = vi.hoisted(() => vi.fn())
const issueQuote = vi.hoisted(() => vi.fn())
const updateQuote = vi.hoisted(() => vi.fn())
const convertToInvoice = vi.hoisted(() => vi.fn())
const listQuotes = vi.hoisted(() => vi.fn())
const listProjectQuotes = vi.hoisted(() => vi.fn())

vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  readQuote,
  issueQuote,
  updateQuote,
  convertToInvoice,
  listQuotes,
  listProjectQuotes,
}))

function devis(overrides: Partial<Quote> = {}): Quote {
  return {
    id: 12,
    organization_id: 1,
    status: 'draft',
    client_name: 'Mme Dupont',
    client_is_consumer: true,
    total_ht_cents: 100_000,
    total_tva_cents: 10_000,
    total_ttc_cents: 110_000,
    late_penalty_rate_bp: 1050,
    recovery_indemnity_cents: 4000,
    vat_attestation_required: false,
    lines: [],
    vat_breakdown: [],
    warnings: [],
    ...overrides,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  for (const espion of [
    readQuote,
    issueQuote,
    updateQuote,
    convertToInvoice,
    listQuotes,
    listProjectQuotes,
  ]) {
    espion.mockReset()
  }
})

describe('conversion des montants', () => {
  it.each([
    ['8,29', 829],
    ['0,29', 29],
    ['1234567,89', 123_456_789],
    ['12,5', 1250],
    ['12', 1200],
    ['0,01', 1],
    ['-24,50', -2450],
    ['1 234,56', 123_456],
  ])('lit « %s » comme %i centimes', (saisie, attendu) => {
    expect(centsFromInput(saisie)).toBe(attendu)
  })

  it('ne perd pas le centime que le passage par un flottant efface', () => {
    // Le chemin naïf, montré ici pour ce qu'il est : `Math.trunc(8.29 * 100)` vaut 828, et le
    // devis part un centime trop bas. Si cette assertion cessait d'être vraie un jour, c'est le
    // test lui-même qui aurait perdu son objet.
    expect(Math.trunc(Number.parseFloat('8.29') * 100)).toBe(828)
    expect(centsFromInput('8,29')).toBe(829)
  })

  it.each(['', 'gratuit', '12,345', '-', '1,2,3', '.'])(
    'refuse la saisie illisible « %s » plutôt que d’écrire zéro',
    (saisie) => {
      expect(centsFromInput(saisie)).toBeNull()
    },
  )

  it('formate un montant sans jamais l’arrondir', () => {
    // `toLocaleString` sépare les milliers par une espace fine insécable : on la ramène à une
    // espace ordinaire pour que l'attendu reste lisible dans le test.
    const lisible = (texte: string): string => texte.replace(/[\u202f\u00a0]/g, ' ')

    expect(lisible(formatCents(123_456_789))).toBe('1 234 567,89 €')
    expect(formatCents(1)).toBe('0,01 €')
    expect(formatCents(0)).toBe('0,00 €')
    expect(formatCents(-2450)).toBe('-24,50 €')
  })

  it('fait l’aller-retour saisie → centimes → saisie sans dérive', () => {
    for (const centimes of [1, 29, 829, 1250, 4000, 123_456_789]) {
      expect(centsFromInput(inputFromCents(centimes))).toBe(centimes)
    }
  })

  it('lit les taux en points de base et non en pourcentage', () => {
    expect(formatRateBp(550)).toBe('5,5 %')
    expect(formatRateBp(1000)).toBe('10 %')
    expect(formatRateBp(2000)).toBe('20 %')
  })
})

describe('mesures du métré', () => {
  it('affiche un tiret pour une valeur non établie, jamais un zéro', () => {
    expect(formatMesure(null, 'm²')).toBe('—')
    expect(formatMesure(0, 'm²')).toBe('0 m²')
    expect(formatMesure(11.31, 'm²')).toBe('11,31 m²')
  })
})

describe('figement du devis', () => {
  it('ne fige pas un brouillon', () => {
    expect(estFige(devis())).toBe(false)
  })

  it.each(['sent', 'accepted', 'refused', 'invoiced'] as const)(
    'fige un devis à l’état %s',
    (status) => {
      expect(estFige(devis({ status }))).toBe(true)
    },
  )

  it('nomme les cinq états en français', () => {
    expect(Object.keys(LIBELLES_STATUT)).toHaveLength(5)
    expect(LIBELLES_STATUT.draft).toBe('Brouillon')
    expect(LIBELLES_STATUT.invoiced).toBe('Facturé')
  })
})

describe('actions du document', () => {
  it('émet le devis et annonce le numéro attribué par le serveur', async () => {
    readQuote.mockResolvedValue(devis())
    issueQuote.mockResolvedValue(devis({ status: 'sent', number: 'DEV-2026-0001' }))
    const store = useQuoteStore()

    await store.loadQuote(12)
    expect(store.fige).toBe(false)

    await store.issue()

    expect(issueQuote).toHaveBeenCalledWith(12)
    expect(store.fige).toBe(true)
    expect(store.notice).toContain('DEV-2026-0001')
    expect(store.notice).toContain('figé')
  })

  it('n’envoie que le statut au serveur, jamais les lignes ni les totaux', async () => {
    readQuote.mockResolvedValue(devis({ status: 'sent', number: 'DEV-2026-0001' }))
    updateQuote.mockResolvedValue(devis({ status: 'accepted', number: 'DEV-2026-0001' }))
    const store = useQuoteStore()

    await store.loadQuote(12)
    await store.setStatus('accepted')

    // Un champ de plus dans ce corps et le serveur répond 409 sur un document émis : c'est
    // exactement la modification silencieuse d'un contrat que la route interdit.
    expect(updateQuote).toHaveBeenCalledWith(12, { status: 'accepted' })
  })

  it('porte le refus du serveur à l’écran au lieu de le taire', async () => {
    readQuote.mockResolvedValue(devis({ status: 'sent' }))
    updateQuote.mockRejectedValue(new Error('Transition impossible : « sent » → « draft ».'))
    const store = useQuoteStore()

    await store.loadQuote(12)
    await store.setStatus('draft')

    expect(store.error).toContain('Transition impossible')
    // Le document affiché reste celui que le serveur connaît, pas celui qu'on a tenté d'écrire.
    expect(store.devis?.status).toBe('sent')
    expect(store.busy).toBe(false)
  })

  it('convertit en facture et annonce son numéro', async () => {
    readQuote.mockResolvedValue(devis({ status: 'accepted' }))
    convertToInvoice.mockResolvedValue(
      devis({ status: 'invoiced', invoice_number: 'FAC-2026-0007' }),
    )
    const store = useQuoteStore()

    await store.loadQuote(12)
    await store.invoice()

    expect(convertToInvoice).toHaveBeenCalledWith(12)
    expect(store.devis?.status).toBe('invoiced')
    expect(store.notice).toContain('FAC-2026-0007')
  })

  it('liste les devis d’un chantier ou ceux de toute l’entreprise selon l’appel', async () => {
    listProjectQuotes.mockResolvedValue([{ id: 1 }])
    listQuotes.mockResolvedValue([{ id: 1 }, { id: 2 }])
    const store = useQuoteStore()

    await store.loadList(7)
    expect(listProjectQuotes).toHaveBeenCalledWith(7)
    expect(store.resumes).toHaveLength(1)

    await store.loadList()
    expect(listQuotes).toHaveBeenCalledTimes(1)
    expect(store.resumes).toHaveLength(2)
  })

  it('rend la liste vide et le message visible quand le serveur refuse', async () => {
    listQuotes.mockRejectedValue(new Error('Ressource introuvable'))
    const store = useQuoteStore()

    await store.loadList()

    expect(store.resumes).toEqual([])
    expect(store.error).toBe('Ressource introuvable')
    expect(store.loading).toBe(false)
  })
})
