/**
 * Le socle légal est du contenu, pas de la logique — ce qui ne veut pas dire qu'il n'y a rien à
 * vérifier. Trois propriétés le rendent utilisable, et chacune se casse silencieusement :
 *
 * 1. les quatre documents existent réellement et se distinguent. Une route vers un document
 *    absent afficherait une page vide, et personne ne s'en rendrait compte avant un litige ;
 * 2. l'avertissement de relecture juridique est en tête de **chaque** document. Il n'est pas
 *    décoratif : tant qu'il est là, aucun de ces textes n'est opposable, et l'oublier sur une
 *    page serait pire que de ne pas l'avoir du tout ;
 * 3. la politique de confidentialité porte ce que le RGPD exige d'y trouver — durées de
 *    conservation, registre des sous-traitants, autorité de contrôle. C'est le seul document dont
 *    le contenu est imposé par un texte.
 */
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import LegalView from '@/views/LegalView.vue'

function render(document: string): string {
  return mount(LegalView, {
    props: { document },
    global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
  }).text()
}

describe('socle légal', () => {
  it('publie les quatre documents, et ils sont distincts', () => {
    const titres = ['mentions', 'cgu', 'cgv', 'confidentialite'].map(
      (cle) => mount(LegalView, {
        props: { document: cle },
        global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
      }).get('h1').text(),
    )

    expect(titres).toEqual([
      'Mentions légales',
      'Conditions générales d’utilisation',
      'Conditions générales de vente',
      'Politique de confidentialité',
    ])
    expect(new Set(titres).size).toBe(4)
  })

  it('avertit sur chaque document qu’il n’a pas été relu par un juriste', () => {
    for (const cle of ['mentions', 'cgu', 'cgv', 'confidentialite']) {
      expect(render(cle)).toContain('relire par un juriste')
    }
  })

  it('laisse les valeurs de l’exploitant en marqueurs explicites plutôt qu’inventées', () => {
    // Une raison sociale plausible dans des mentions légales a l'air valide, donc personne ne la
    // corrige. Le marqueur, lui, se voit.
    expect(render('mentions')).toContain('[SIRET]')
    expect(render('mentions')).toContain('[HÉBERGEUR]')
  })

  it('les CGV portent ce qui rend un abonnement vendable', () => {
    const cgv = render('cgv')

    expect(cgv).toContain('reconduit')
    expect(cgv).toContain('indemnité forfaitaire de recouvrement de 40 €')
    expect(cgv).toContain('rétractation')
    expect(cgv).toContain('médiateur')
    // Le dépassement de quota déclasse et ne supprime jamais : c'est une promesse commerciale
    // autant qu'une règle technique, elle doit figurer dans le contrat.
    expect(cgv).toContain('lecture seule')
  })

  it('la politique de confidentialité porte les mentions exigées par le RGPD', () => {
    const politique = render('confidentialite')

    expect(politique).toContain('Durées de conservation')
    expect(politique).toContain('Registre des sous-traitants')
    expect(politique).toContain('CNIL')
    expect(politique).toContain('portabilité')
    // Dix ans : l'obligation comptable est la seule qui prime sur une demande d'effacement, et
    // c'est exactement ce qu'un utilisateur doit pouvoir lire avant de demander la sienne.
    expect(politique).toContain('dix ans')
  })

  it('annonce que le service n’est pas une plateforme de dématérialisation agréée', () => {
    // Prétendre le contraire serait faux et juridiquement dangereux
    // (`docs/strategie-produit.md` §2).
    expect(render('cgu')).toContain('plateforme de dématérialisation agréée')
  })

  it('un document inconnu retombe sur les mentions plutôt que sur un écran vide', () => {
    expect(render('inexistant')).toContain('Mentions légales')
  })
})
