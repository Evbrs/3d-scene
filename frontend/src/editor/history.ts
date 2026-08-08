/**
 * Pile annuler / refaire de l'éditeur.
 *
 * Le modèle s'y prête sans effort : toute écriture du plan est une opération discrète, et les
 * routes renvoient l'entité écrite. Une entrée porte donc son geste (`refaire`) et son inverse
 * (`annuler`), tous deux exprimés en appels serveur — jamais en restauration d'un état local.
 * Restaurer un instantané côté client réécrirait par-dessus ce qu'un collègue vient d'enregistrer,
 * alors que rejouer l'inverse du geste laisse le verrouillage optimiste faire son travail.
 *
 * Volontairement hors composant : la pile est de la logique pure et se teste sans DOM.
 */
import { type ComputedRef, computed, ref, shallowRef } from 'vue'

/** Un geste réversible. `libelle` est montré à l'utilisateur (« Annuler : déplacer 3 meubles »). */
export interface HistoryEntry {
  libelle: string
  refaire: () => Promise<unknown>
  annuler: () => Promise<unknown>
}

/**
 * Profondeur de la pile.
 *
 * Cinquante gestes couvrent largement une session de relevé, et bornent la mémoire retenue : une
 * entrée capture les charges utiles des écritures, pas seulement des identifiants.
 */
export const HISTORY_LIMIT = 50

export interface History {
  /** Empile un geste déjà appliqué. Efface la branche « refaire », devenue caduque. */
  push: (entry: HistoryEntry) => void
  annuler: () => Promise<HistoryEntry | null>
  refaire: () => Promise<HistoryEntry | null>
  /** Vide la pile. Appelé après un conflit : les inverses mémorisés ne décrivent plus la réalité. */
  clear: () => void
  peutAnnuler: ComputedRef<boolean>
  peutRefaire: ComputedRef<boolean>
  libelleAnnuler: ComputedRef<string | null>
  libelleRefaire: ComputedRef<string | null>
  /** Vrai pendant l'exécution d'un annuler/refaire : sert à ne pas réempiler ce qu'on rejoue. */
  enCours: ComputedRef<boolean>
  taille: ComputedRef<number>
}

export function createHistory(limit: number = HISTORY_LIMIT): History {
  // `shallowRef` : ce sont des tableaux de fermetures, jamais parcourus en profondeur par le
  // rendu. Les rendre réactifs en profondeur ferait proxifier chaque charge utile capturée.
  const passe = shallowRef<HistoryEntry[]>([])
  const futur = shallowRef<HistoryEntry[]>([])
  const occupe = ref(false)

  function push(entry: HistoryEntry): void {
    // Un geste posé pendant un annuler/refaire est le rejeu lui-même : l'empiler ferait boucler
    // la pile sur elle-même et rendrait Ctrl+Z sans effet visible.
    if (occupe.value) return
    passe.value = [...passe.value, entry].slice(-limit)
    futur.value = []
  }

  /**
   * Exécute un mouvement de pile.
   *
   * Le drapeau `occupe` sert deux buts : écarter le réempilement, et sérialiser les appels. Deux
   * Ctrl+Z maintenus enfoncés lanceraient sinon deux écritures concurrentes portant la même
   * version du projet — la seconde partirait en conflit à coup sûr.
   */
  async function deplacer(
    source: typeof passe,
    cible: typeof passe,
    sens: 'annuler' | 'refaire',
  ): Promise<HistoryEntry | null> {
    if (occupe.value) return null
    const entry = source.value[source.value.length - 1]
    if (!entry) return null

    occupe.value = true
    try {
      await entry[sens]()
    } catch (caught) {
      // Le geste inverse a échoué : l'entrée reste où elle est, sinon la pile prétendrait qu'un
      // état a été atteint alors que le serveur a refusé.
      occupe.value = false
      throw caught
    }
    source.value = source.value.slice(0, -1)
    cible.value = [...cible.value, entry].slice(-limit)
    occupe.value = false
    return entry
  }

  return {
    push,
    annuler: () => deplacer(passe, futur, 'annuler'),
    refaire: () => deplacer(futur, passe, 'refaire'),
    clear: () => {
      passe.value = []
      futur.value = []
    },
    peutAnnuler: computed(() => passe.value.length > 0 && !occupe.value),
    peutRefaire: computed(() => futur.value.length > 0 && !occupe.value),
    libelleAnnuler: computed(() => passe.value[passe.value.length - 1]?.libelle ?? null),
    libelleRefaire: computed(() => futur.value[futur.value.length - 1]?.libelle ?? null),
    enCours: computed(() => occupe.value),
    taille: computed(() => passe.value.length),
  }
}
