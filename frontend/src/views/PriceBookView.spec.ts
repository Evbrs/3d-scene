/**
 * Le barème : là où l'artisan met ses prix, et là où il craint de les casser.
 *
 * Trois garanties sont surveillées ici :
 *
 * 1. **le code n'est jamais renvoyé au serveur lors d'une modification.** C'est la clé de
 *    rattachement du métré et des devis passés ; le PATCH qui le porterait romprait des
 *    correspondances déjà faites ;
 * 2. **un prix saisi devient des centimes entiers**, sans détour par un flottant ;
 * 3. **un import compte ses échecs et les nomme** au lieu d'écrire un barème à moitié rempli en
 *    silence.
 */
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import PriceBookView from '@/views/PriceBookView.vue'

const listOrganizations = vi.hoisted(() => vi.fn())
const listPriceBooks = vi.hoisted(() => vi.fn())
const createPriceBook = vi.hoisted(() => vi.fn())
const listPriceItems = vi.hoisted(() => vi.fn())
const createPriceItem = vi.hoisted(() => vi.fn())
const updatePriceItem = vi.hoisted(() => vi.fn())
const deletePriceItem = vi.hoisted(() => vi.fn())

vi.mock('@/api/client', () => ({
  listOrganizations,
  listPriceBooks,
  createPriceBook,
  listPriceItems,
  createPriceItem,
  updatePriceItem,
  deletePriceItem,
}))

const stubs = { RouterLink: { template: '<a><slot /></a>' } }

const LIGNE = {
  id: 90,
  price_book_id: 5,
  code: 'PEINT-MUR',
  label: 'Peinture acrylique sur murs, 2 couches',
  unit: 'm2',
  unit_price_cents: 2400,
  vat_rate_bp: 1000,
}

beforeEach(() => {
  for (const espion of [
    listOrganizations,
    listPriceBooks,
    createPriceBook,
    listPriceItems,
    createPriceItem,
    updatePriceItem,
    deletePriceItem,
  ]) {
    espion.mockReset()
  }
  listOrganizations.mockResolvedValue([{ id: 1, name: 'Entreprise', slug: 'entreprise' }])
  listPriceBooks.mockResolvedValue([{ id: 5, organization_id: 1, name: 'Standard', is_default: true }])
  listPriceItems.mockResolvedValue([LIGNE])
})

function monter() {
  return mount(PriceBookView, { global: { stubs } })
}

describe('écran du barème', () => {
  it('annonce le chargement avant de rien affirmer', () => {
    listOrganizations.mockReturnValue(new Promise(() => {}))
    const page = monter()

    expect(page.text()).toContain('Chargement du barème')
  })

  it('affiche les lignes avec leur prix en euros et leur taux en pourcentage', async () => {
    const page = monter()
    await flushPromises()

    expect(page.text()).toContain('PEINT-MUR')
    expect(page.text()).toContain('24,00 €')
    expect(page.text()).toContain('10 %')
  })

  it('dit que modifier un prix ne touche aucun devis déjà établi', async () => {
    const page = monter()
    await flushPromises()

    expect(page.text()).toContain('aucun devis déjà établi')
  })

  it('ajoute une ligne en centimes entiers, code mis en majuscules', async () => {
    createPriceItem.mockResolvedValue(LIGNE)
    const page = monter()
    await flushPromises()

    await page.find('#nouveau-code').setValue('protection')
    await page.find('#nouveau-libelle').setValue('Protection des sols')
    await page.find('#nouvelle-unite').setValue('forfait')
    await page.find('#nouveau-prix').setValue('8,29')
    await page.find('form.formulaire').trigger('submit')
    await flushPromises()

    expect(createPriceItem).toHaveBeenCalledWith(5, {
      code: 'PROTECTION',
      label: 'Protection des sols',
      unit: 'forfait',
      unit_price_cents: 829,
      vat_rate_bp: 1000,
    })
  })

  it('refuse un code que le serveur rejetterait, et le dit sur le champ', async () => {
    const page = monter()
    await flushPromises()

    await page.find('#nouveau-code').setValue('peinture murale !')
    await page.find('#nouveau-libelle').setValue('Peinture')
    await page.find('#nouveau-prix').setValue('24,00')
    await page.find('form.formulaire').trigger('submit')
    await flushPromises()

    expect(createPriceItem).not.toHaveBeenCalled()
    const champ = page.find('#nouveau-code')
    expect(champ.attributes('aria-invalid')).toBe('true')
    expect(page.find(`#${champ.attributes('aria-describedby')}`).text()).toContain('Code invalide')
  })

  it('modifie une ligne sans jamais renvoyer son code', async () => {
    updatePriceItem.mockResolvedValue({ ...LIGNE, unit_price_cents: 2600 })
    const page = monter()
    await flushPromises()

    await page
      .findAll('button')
      .find((b) => b.text().startsWith('Modifier'))
      ?.trigger('click')
    // Le formulaire est prérempli depuis les centimes du serveur, sans arrondi de sortie.
    expect((page.find('#edition-prix-90').element as HTMLInputElement).value).toBe('24,00')

    await page.find('#edition-prix-90').setValue('26,00')
    await page
      .findAll('button')
      .find((b) => b.text() === 'Enregistrer')
      ?.trigger('click')
    await flushPromises()

    const [identifiant, charge] = updatePriceItem.mock.calls[0] as [number, Record<string, unknown>]
    expect(identifiant).toBe(90)
    expect(charge).not.toHaveProperty('code')
    expect(charge.unit_price_cents).toBe(2600)
  })

  it('supprime une ligne après confirmation', async () => {
    // `window.confirm` n'existe pas dans l'environnement de test : sans ce doublon, la
    // suppression s'interrompt avant même d'appeler le serveur.
    vi.stubGlobal('confirm', vi.fn(() => true))
    deletePriceItem.mockResolvedValue(undefined)
    const page = monter()
    await flushPromises()

    await page
      .findAll('button')
      .find((b) => b.text().startsWith('Supprimer'))
      ?.trigger('click')
    await flushPromises()

    expect(deletePriceItem).toHaveBeenCalledWith(90)
    vi.unstubAllGlobals()
  })

  it('crée un autre barème et bascule dessus', async () => {
    createPriceBook.mockResolvedValue({ id: 6, organization_id: 1, name: 'Gros œuvre', is_default: false })
    listPriceBooks.mockResolvedValueOnce([{ id: 5, organization_id: 1, name: 'Standard', is_default: true }])
    const page = monter()
    await flushPromises()

    listPriceBooks.mockResolvedValue([
      { id: 5, organization_id: 1, name: 'Standard', is_default: true },
      { id: 6, organization_id: 1, name: 'Gros œuvre', is_default: false },
    ])
    await page.find('#nouveau-bareme').setValue('Gros œuvre')
    await page
      .findAll('button')
      .find((b) => b.text() === 'Créer')
      ?.trigger('click')
    await flushPromises()

    expect(createPriceBook).toHaveBeenCalledWith(1, 'Gros œuvre')
    expect(listPriceItems).toHaveBeenLastCalledWith(6)
  })

  it('importe un fichier ligne à ligne et nomme celles qu’il refuse', async () => {
    createPriceItem.mockImplementation(async (_livre: number, charge: { code: string }) => {
      if (charge.code === 'MAUVAIS') throw new Error('Code déjà utilisé')
      return { ...LIGNE, code: charge.code }
    })
    const page = monter()
    await flushPromises()

    const contenu = [
      'code;libelle;unite;prix_unitaire_ht;taux_tva_pourcent',
      'CARRELAGE-SOL;Carrelage de sol;m2;95,00;10,00',
      'MAUVAIS;Ligne refusée par le serveur;m2;10,00;10,00',
      'code invalide;Sans code lisible;m2;10,00;10,00',
    ].join('\r\n')

    const champ = page.find('#import-bareme')
    // Un `input[type=file]` ne se remplit pas depuis un test : on lui fournit directement le
    // fichier que le navigateur y aurait mis, ce que la vue lit par `event.target.files`.
    Object.defineProperty(champ.element, 'files', {
      configurable: true,
      value: [new File([contenu], 'bareme.csv', { type: 'text/csv' })],
    })
    await champ.trigger('change')
    await flushPromises()

    expect(createPriceItem).toHaveBeenCalledTimes(2)
    // Les points de base sont relus tels quels : « 10,00 » % vaut 1000, pas 10.
    expect(createPriceItem.mock.calls[0]?.[1]).toMatchObject({
      code: 'CARRELAGE-SOL',
      unit_price_cents: 9500,
      vat_rate_bp: 1000,
    })
    const rapport = page.find('#rapport-import').text()
    expect(rapport).toContain('1 ligne(s) importées')
    expect(rapport).toContain('MAUVAIS')
    expect(rapport).toContain('CODE INVALIDE')
  })

  it('affiche l’erreur du serveur au lieu d’un écran muet', async () => {
    listOrganizations.mockRejectedValue(new Error('Ressource introuvable'))
    const page = monter()
    await flushPromises()

    expect(page.find('[role="alert"]').text()).toContain('Ressource introuvable')
  })

  it('explique un barème vide au lieu de l’afficher nu', async () => {
    listPriceItems.mockResolvedValue([])
    const page = monter()
    await flushPromises()

    expect(page.text()).toContain('Ce barème est vide')
  })
})
