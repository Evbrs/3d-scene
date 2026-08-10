/**
 * Le métré doit être vérifiable, sinon il ne sert à rien.
 *
 * Trois choses s'y jouent, et chacune a un coût en euros quand elle est fausse :
 *
 * 1. **une inconnue n'est pas un zéro.** `build_takeoff` sort `null` quand il n'a pas su établir
 *    une valeur, et joint un avertissement. Un écran qui affiche « 0 m² » fait disparaître la
 *    réserve dans une somme, et le devis part sous-évalué ;
 * 2. **les réserves se lisent avant les totaux**, pas en pied de page ;
 * 3. **le calepinage montre ses trois grandeurs** — commandé, entier, coupé. Ce sont trois
 *    nombres distincts, et c'est l'écart entre eux qui fait la marge sur le poste matériaux.
 */
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Takeoff } from '@/api/types'
import TakeoffView from '@/views/TakeoffView.vue'

const readTakeoff = vi.hoisted(() => vi.fn())
const listFaceCostings = vi.hoisted(() => vi.fn())
const setFaceCosting = vi.hoisted(() => vi.fn())
const deleteFaceCosting = vi.hoisted(() => vi.fn())
const downloadTakeoffCsv = vi.hoisted(() => vi.fn())

vi.mock('@/api/client', () => ({
  readTakeoff,
  listFaceCostings,
  setFaceCosting,
  deleteFaceCosting,
  downloadTakeoffCsv,
}))

const stubs = { RouterLink: { template: '<a><slot /></a>' } }

// Typée : sans elle, TypeScript déduit `number` de `full_units: 35` et refuse le `null` que le
// serveur envoie sur une pose en diagonale — c'est-à-dire le cas que ce fichier surveille.
function metre(overrides: Partial<Takeoff> = {}): Takeoff {
  return {
    units: { area: 'm2', length: 'ml', volume: 'm3' },
    project_id: 7,
    rooms: [
      {
        room_id: 70,
        name: 'Séjour',
        ceiling_height_m: 2.5,
        wall_thickness_m: 0.1,
        perimeter_ml: 14,
        net_perimeter_ml: 13.6,
        skirting_ml: 13.6,
        cornice_ml: 13.6,
        floor_area_m2: 11.31,
        ceiling_area_m2: 11.31,
        volume_m3: 28.275,
        wall_gross_area_m2: 35,
        wall_openings_area_m2: 3.5,
        wall_net_area_m2: 31.5,
        opening_count: 2,
        door_count: 1,
        window_count: 1,
        faces: [
          {
            face_id: 700,
            face_label: 'A',
            kind: 'wall',
            length_m: 4,
            height_m: 2.5,
            gross_area_m2: 10,
            openings_area_m2: 3.5,
            net_area_m2: 6.5,
            opening_count: 2,
            door_count: 1,
            window_count: 1,
            skirting_deduction_ml: 0.9,
            material: 'peinture',
            tiling: null,
          },
          {
            face_id: 704,
            face_label: 'SOL',
            kind: 'floor',
            length_m: null,
            height_m: null,
            gross_area_m2: 11.31,
            openings_area_m2: 0,
            net_area_m2: 11.31,
            opening_count: 0,
            door_count: 0,
            window_count: 0,
            skirting_deduction_ml: 0,
            material: 'carrelage',
            tiling: {
              pattern: 'straight',
              unit_width_cm: 50,
              unit_height_cm: 50,
              unit_area_m2: 0.25,
              waste_ratio: 0.08,
              ordered_area_m2: 12.215,
              units_total: 49,
              full_units: 35,
              cut_units: 13,
            },
          },
        ],
        coverings: [],
        warnings: [],
      },
    ],
    totals: {
      room_count: 1,
      floor_area_m2: 11.31,
      ceiling_area_m2: 11.31,
      wall_gross_area_m2: 35,
      wall_openings_area_m2: 3.5,
      wall_net_area_m2: 31.5,
      volume_m3: 28.275,
      perimeter_ml: 14,
      skirting_ml: 13.6,
      cornice_ml: 13.6,
      opening_count: 2,
      door_count: 1,
      window_count: 1,
      coverings: [
        {
          material: 'carrelage',
          pattern: 'straight',
          unit_width_cm: 50,
          unit_height_cm: 50,
          waste_ratio: 0.08,
          net_area_m2: 11.31,
          ordered_area_m2: 12.215,
          units_total: 49,
          full_units: 35,
          cut_units: 13,
        },
      ],
    },
    warnings: [],
    ...overrides,
  }
}

function monter() {
  return mount(TakeoffView, { props: { projectId: '7' }, global: { stubs } })
}

beforeEach(() => {
  for (const espion of [
    readTakeoff,
    listFaceCostings,
    setFaceCosting,
    deleteFaceCosting,
    downloadTakeoffCsv,
  ]) {
    espion.mockReset()
  }
  listFaceCostings.mockResolvedValue([])
})

describe('écran du métré', () => {
  it('annonce le chargement avant de rien affirmer', () => {
    readTakeoff.mockReturnValue(new Promise(() => {}))
    const page = monter()

    expect(page.text()).toContain('Calcul du métré en cours')
  })

  it('affiche les surfaces nettes, les linéaires et les totaux du projet', async () => {
    readTakeoff.mockResolvedValue(metre())
    const page = monter()
    await flushPromises()

    const texte = page.text()
    expect(texte).toContain('31,5 m²')
    expect(texte).toContain('13,6 ml')
    expect(texte).toContain('28,275 m³')
    expect(texte).toContain('6,5 m²')
  })

  it('montre le calepinage avec ses entières, ses coupes et son taux de chute', async () => {
    readTakeoff.mockResolvedValue(metre())
    const page = monter()
    await flushPromises()

    const texte = page.text()
    // Trois grandeurs distinctes : ce qu'on commande, ce qu'on pose entier, ce qu'on coupe.
    expect(texte).toContain('49')
    expect(texte).toContain('35')
    expect(texte).toContain('13')
    expect(texte).toContain('8 %')
  })

  it('affiche un tiret pour une valeur non établie et jamais un zéro', async () => {
    const partiel = metre()
    // Une pose en diagonale ne se découpe ni en colonnes ni en rangs : le serveur rend `null`.
    partiel.totals.coverings[0]!.full_units = null
    partiel.totals.coverings[0]!.cut_units = null
    readTakeoff.mockResolvedValue(partiel)

    const page = monter()
    await flushPromises()

    const ligne = page.findAll('tbody tr').find((rangee) => rangee.text().includes('carrelage'))
    expect(ligne?.text()).toContain('—')
    expect(ligne?.text()).not.toContain(' 0 ')
  })

  it('met les réserves en tête et dit que les totaux sont partiels', async () => {
    readTakeoff.mockResolvedValue(
      metre({ warnings: ['Séjour, face B : hauteur inconnue, surface non établie.'] }),
    )
    const page = monter()
    await flushPromises()

    const alerte = page.find('.reserves')
    expect(alerte.attributes('role')).toBe('alert')
    expect(alerte.text()).toContain('hauteur inconnue')
    expect(alerte.text()).toContain('ignorent')
    // La réserve précède les totaux dans le document, elle n'est pas reléguée en pied de page.
    expect(page.html().indexOf('reserves')).toBeLessThan(page.html().indexOf('Totaux du chantier'))
  })

  it('propose un chantier sans pièce sans faire croire à une panne', async () => {
    readTakeoff.mockResolvedValue(metre({ rooms: [], totals: { ...metre().totals, room_count: 0 } }))
    const page = monter()
    await flushPromises()

    expect(page.text()).toContain("Ce chantier n'a encore aucune pièce")
    expect(page.find('[role="alert"]').exists()).toBe(false)
  })

  it('affiche l’erreur du serveur au lieu d’un écran muet', async () => {
    readTakeoff.mockRejectedValue(new Error('Ressource introuvable'))
    const page = monter()
    await flushPromises()

    expect(page.find('[role="alert"]').text()).toContain('Ressource introuvable')
  })

  it('exporte le métré au format tableur', async () => {
    readTakeoff.mockResolvedValue(metre())
    downloadTakeoffCsv.mockResolvedValue(new Blob(['piece;face']))
    const page = monter()
    await flushPromises()

    const bouton = page
      .findAll('button')
      .find((candidat) => candidat.text().includes('Exporter le métré'))
    await bouton?.trigger('click')
    await flushPromises()

    expect(downloadTakeoffCsv).toHaveBeenCalledWith(7)
  })

  it('envoie la quantité imposée en chaîne, sans passer par un flottant', async () => {
    readTakeoff.mockResolvedValue(metre())
    setFaceCosting.mockResolvedValue({ id: 1, face_id: 700 })
    const page = monter()
    await flushPromises()

    await page.find('#face-chiffree').setValue('700')
    await page.find('#code-impose').setValue('peint-mur')
    await page.find('#quantite-imposee').setValue('6.125')
    await page.find('#prix-impose').setValue('24,50')
    await page.find('form.formulaire-chiffrage').trigger('submit')
    await flushPromises()

    expect(setFaceCosting).toHaveBeenCalledWith(700, {
      // Le serveur exige des majuscules : les imposer ici évite un 422 sur une saisie légitime.
      price_item_code: 'PEINT-MUR',
      override_quantity: '6.125',
      // 24,50 € font 2450 centimes entiers, pas 2449 ni 2450.0000001.
      override_unit_price_cents: 2450,
    })
  })

  it('refuse un montant illisible et le dit sur le champ concerné', async () => {
    readTakeoff.mockResolvedValue(metre())
    const page = monter()
    await flushPromises()

    await page.find('#face-chiffree').setValue('700')
    await page.find('#prix-impose').setValue('gratuit')
    await page.find('form.formulaire-chiffrage').trigger('submit')
    await flushPromises()

    expect(setFaceCosting).not.toHaveBeenCalled()
    const champ = page.find('#prix-impose')
    expect(champ.attributes('aria-invalid')).toBe('true')
    // Le message est rattaché au champ par `aria-describedby`, pas jeté en tête de page.
    expect(page.find(`#${champ.attributes('aria-describedby')}`).text()).toContain('illisible')
  })

  it('retire un chiffrage imposé et recharge la liste', async () => {
    readTakeoff.mockResolvedValue(metre())
    listFaceCostings.mockResolvedValue([
      { id: 3, face_id: 700, price_item_code: 'PEINT-MUR', override_quantity: null, override_unit_price_cents: 2450 },
    ])
    deleteFaceCosting.mockResolvedValue(undefined)
    const page = monter()
    await flushPromises()

    expect(page.text()).toContain('24,50 €')
    expect(page.text()).toContain('Séjour — A')

    const bouton = page.findAll('button').find((candidat) => candidat.text() === 'Retirer')
    await bouton?.trigger('click')
    await flushPromises()

    expect(deleteFaceCosting).toHaveBeenCalledWith(700)
    expect(listFaceCostings).toHaveBeenCalledTimes(2)
  })

  it('lie chaque colonne à son en-tête, faute de quoi le tableau est illisible au clavier', async () => {
    readTakeoff.mockResolvedValue(metre())
    const page = monter()
    await flushPromises()

    const tableaux = page.findAll('table')
    expect(tableaux.length).toBeGreaterThan(0)
    for (const tableau of tableaux) {
      expect(tableau.find('caption').exists()).toBe(true)
      for (const entete of tableau.findAll('thead th')) {
        expect(entete.attributes('scope')).toBe('col')
      }
    }
  })
})
