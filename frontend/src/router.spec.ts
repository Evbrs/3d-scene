/**
 * Le parcours commercial est-il atteignable ?
 *
 * La question n'est pas rhétorique : la chaîne complète — métré, devis, émission, facture — a
 * longtemps existé côté serveur sans qu'aucune adresse du frontend n'y mène. Un écran qu'on ne
 * peut pas atteindre n'existe pas.
 *
 * Ces tests interrogent le **vrai** routeur de l'application, avec sa vraie garde de session :
 * un routeur reconstruit pour l'occasion ne prouverait que sa propre cohérence.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { clearToken, storeToken } from '@/api/client'
import { NOM_DU_PRODUIT, router } from '@/router'

beforeEach(async () => {
  clearToken()
  await router.push('/connexion')
  await router.isReady()
})

afterEach(() => {
  clearToken()
})

describe('adresses du parcours commercial', () => {
  it.each([
    ['/projets/12/metre', 'metre'],
    ['/projets/12/devis', 'devis-chantier'],
    ['/devis', 'devis'],
    ['/devis/5', 'devis-document'],
    ['/bareme', 'bareme'],
    ['/entreprise', 'entreprise'],
  ])('%s mène à la route « %s »', (chemin, nom) => {
    expect(router.resolve(chemin).name).toBe(nom)
  })

  it('passe l’identifiant du chantier et celui du document en propriétés', () => {
    expect(router.resolve('/projets/12/metre').params).toEqual({ projectId: '12' })
    expect(router.resolve('/devis/5').params).toEqual({ quoteId: '5' })
  })

  it('charge réellement les composants annoncés', async () => {
    // Une route qui pointe vers un fichier absent se résout sans broncher : c'est au chargement
    // que la promesse échoue, donc en production, sur un écran blanc.
    for (const chemin of ['/projets/12/metre', '/devis/5', '/bareme', '/entreprise']) {
      const enregistrement = router.resolve(chemin).matched[0]
      const chargeur = enregistrement?.components?.default as () => Promise<unknown>
      await expect(chargeur()).resolves.toBeDefined()
    }
  })
})

describe('garde de session', () => {
  it.each(['/devis', '/bareme', '/entreprise', '/projets/12/metre'])(
    'renvoie %s vers la connexion sans session, en gardant la destination',
    async (chemin) => {
      await router.push(chemin)

      expect(router.currentRoute.value.name).toBe('connexion')
      expect(router.currentRoute.value.query.suivant).toBe(chemin)
    },
  )

  it('laisse passer une session ouverte', async () => {
    storeToken('jeton-de-test')

    await router.push('/entreprise')

    expect(router.currentRoute.value.name).toBe('entreprise')
  })

  it('nomme l’onglet, faute de quoi quinze écrans partagent un seul titre', async () => {
    storeToken('jeton-de-test')

    await router.push('/bareme')

    expect(document.title).toBe(`Mon barème — ${NOM_DU_PRODUIT}`)
  })
})
