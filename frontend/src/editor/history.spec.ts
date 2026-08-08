/**
 * Pile annuler / refaire.
 *
 * Le point de rupture face aux concurrents, donc le point à ne pas rater. Deux pièges y sont
 * surveillés : le rejeu qui se réempile lui-même (Ctrl+Z sans effet visible, la pile bouclant sur
 * elle-même), et les appels concurrents qui partiraient avec la même version du projet.
 */
import { describe, expect, it, vi } from 'vitest'

import { HISTORY_LIMIT, createHistory } from '@/editor/history'

function trace(): { entries: string[]; entry: (libelle: string) => ReturnType<typeof build> } {
  const entries: string[] = []
  const build = (libelle: string) => ({
    libelle,
    refaire: async () => {
      entries.push(`refaire:${libelle}`)
    },
    annuler: async () => {
      entries.push(`annuler:${libelle}`)
    },
  })
  return { entries, entry: build }
}

describe('pile d’annulation', () => {
  it('annule dans l’ordre inverse et refait dans l’ordre', async () => {
    const { entries, entry } = trace()
    const history = createHistory()

    history.push(entry('un'))
    history.push(entry('deux'))
    await history.annuler()
    await history.annuler()
    await history.refaire()

    expect(entries).toEqual(['annuler:deux', 'annuler:un', 'refaire:un'])
  })

  it('expose le libellé du prochain geste dans les deux sens', async () => {
    const { entry } = trace()
    const history = createHistory()
    history.push(entry('poser un évier'))

    expect(history.libelleAnnuler.value).toBe('poser un évier')
    expect(history.peutRefaire.value).toBe(false)

    await history.annuler()

    expect(history.peutAnnuler.value).toBe(false)
    expect(history.libelleRefaire.value).toBe('poser un évier')
  })

  it('efface la branche « refaire » dès qu’un nouveau geste est posé', async () => {
    const { entry } = trace()
    const history = createHistory()
    history.push(entry('un'))
    await history.annuler()

    history.push(entry('deux'))

    // C'est la règle universelle des éditeurs : repartir d'un état antérieur abandonne la
    // branche qu'on avait quittée. La garder proposerait de « refaire » un geste incompatible.
    expect(history.peutRefaire.value).toBe(false)
    expect(history.libelleAnnuler.value).toBe('deux')
  })

  it('n’empile pas le geste rejoué par un annuler', async () => {
    const history = createHistory()
    history.push({
      libelle: 'création',
      refaire: async () => {},
      annuler: async () => {
        // Ce que ferait un appelant naïf : traiter l'inverse comme une écriture de plus. Empilé,
        // il rendrait Ctrl+Z sans effet visible — la pile bouclerait sur elle-même.
        history.push({ libelle: 'suppression', refaire: async () => {}, annuler: async () => {} })
      },
    })

    await history.annuler()

    expect(history.peutAnnuler.value).toBe(false)
    expect(history.libelleRefaire.value).toBe('création')
  })

  it('refuse deux annulations concurrentes', async () => {
    const history = createHistory()
    let libere: (() => void) | null = null
    const appels = vi.fn()
    history.push({
      libelle: 'lent',
      refaire: async () => {},
      annuler: () => {
        appels()
        return new Promise<void>((resolve) => {
          libere = resolve
        })
      },
    })

    const premier = history.annuler()
    const second = history.annuler()

    expect(await second).toBeNull()
    expect(history.enCours.value).toBe(true)
    libere!()
    await premier
    // Deux écritures concurrentes porteraient la même version du projet : la seconde partirait
    // en conflit à coup sûr, et viderait la pile pour rien.
    expect(appels).toHaveBeenCalledTimes(1)
  })

  it('laisse l’entrée en place quand son inverse échoue', async () => {
    const history = createHistory()
    history.push({
      libelle: 'écriture refusée',
      refaire: async () => {},
      annuler: () => Promise.reject(new Error('409')),
    })

    await expect(history.annuler()).rejects.toThrow('409')
    // La pile ne doit pas prétendre qu'un état a été atteint alors que le serveur a refusé.
    expect(history.peutAnnuler.value).toBe(true)
    expect(history.peutRefaire.value).toBe(false)
  })

  it('borne la profondeur et oublie les gestes les plus anciens', () => {
    const { entry } = trace()
    const history = createHistory()
    for (let index = 0; index < HISTORY_LIMIT + 10; index += 1) history.push(entry(`g${index}`))

    expect(history.taille.value).toBe(HISTORY_LIMIT)
    expect(history.libelleAnnuler.value).toBe(`g${HISTORY_LIMIT + 9}`)
  })

  it('se vide entièrement après un conflit', async () => {
    const { entry } = trace()
    const history = createHistory()
    history.push(entry('un'))
    await history.annuler()

    history.clear()

    expect(history.peutAnnuler.value).toBe(false)
    expect(history.peutRefaire.value).toBe(false)
  })

  it('rend null quand il n’y a rien à annuler', async () => {
    const history = createHistory()

    expect(await history.annuler()).toBeNull()
    expect(await history.refaire()).toBeNull()
  })
})
