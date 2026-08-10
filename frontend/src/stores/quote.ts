/**
 * Le vocabulaire de la chaîne commerciale : l'arithmétique des montants, et l'état du document.
 *
 * Deux règles gouvernent ce module, et aucune n'est cosmétique.
 *
 * **Les montants sont des centimes entiers, de bout en bout.** Le serveur les envoie ainsi, les
 * calcule en `Decimal` et les fige. Le navigateur ne recalcule donc jamais un total : il formate.
 * Un `parseFloat('8.29') * 100` rend `828.9999999999999`, et le centime perdu là est celui que le
 * comptable du client retrouve. Les deux fonctions de conversion ci-dessous n'emploient que des
 * entiers, et `quote.spec.ts` les met à l'épreuve sur les valeurs où le flottant décroche.
 *
 * **Un devis émis est figé.** `estFige` n'est pas un détail d'affichage : c'est la différence
 * entre un brouillon et un contrat parti chez un client. Le calculer ici plutôt que dans chaque
 * vue évite qu'un écran propose une modification que le serveur refusera en 409.
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import * as api from '@/api/client'
import type { Quote, QuoteStatus, QuoteSummary } from '@/api/types'

// --- Arithmétique des montants -------------------------------------------------------------------

/** Points de base : 10 000 = 100 %. C'est l'unité du serveur pour les taux de TVA. */
export const POINTS_DE_BASE = 10_000

const CENTIMES_PAR_EURO = 100

/**
 * Montant en centimes rendu lisible : « 1 234,56 € ».
 *
 * La division est exacte malgré le flottant : `abs - (abs % 100)` est un multiple de 100, et la
 * division IEEE 754 d'un multiple exact par 100 rend l'entier exact tant qu'on reste sous
 * `Number.MAX_SAFE_INTEGER` — ce que les bornes du serveur (10^12 centimes) garantissent. Un
 * `Math.round(cents / 100)` aurait, lui, arrondi le montant lui-même.
 */
export function formatCents(cents: number): string {
  const signe = cents < 0 ? '-' : ''
  const absolu = Math.abs(Math.trunc(cents))
  const centimes = absolu % CENTIMES_PAR_EURO
  const euros = (absolu - centimes) / CENTIMES_PAR_EURO
  const entiers = euros.toLocaleString('fr-FR', { useGrouping: true })
  return `${signe}${entiers},${String(centimes).padStart(2, '0')} €`
}

/**
 * Saisie utilisateur (« 12,34 », « 12.34 », « 12 ») convertie en centimes entiers.
 *
 * Rend `null` sur une saisie que l'on ne sait pas lire, pour que l'écran le dise au lieu
 * d'enregistrer un zéro silencieux. La conversion est faite sur les **chiffres**, pas sur un
 * flottant : `parseFloat('8.29') * 100` vaut `828.99…`, dont la troncature donne 828 centimes.
 */
export function centsFromInput(saisie: string): number | null {
  const nettoye = saisie.trim().replace(/\s/g, '').replace(',', '.')
  const trouve = /^([+-]?)(\d*)(?:\.(\d{0,2}))?$/.exec(nettoye)
  // Sans chiffre d'aucun côté du séparateur, il n'y a pas de montant : « . », « - » et « » sont
  // acceptés par le motif mais ne valent pas zéro, ils ne valent rien.
  if (!trouve || (trouve[2] === '' && (trouve[3] ?? '') === '')) return null

  const euros = trouve[2] === '' ? 0 : Number(trouve[2])
  // Les décimales sont complétées à droite : « 12,5 » vaut 50 centimes, pas 5.
  const centimes = Number((trouve[3] ?? '').padEnd(2, '0'))
  const total = euros * CENTIMES_PAR_EURO + centimes
  return trouve[1] === '-' ? -total : total
}

/**
 * L'inverse de `centsFromInput` : des centimes vers la saisie « 12,34 ».
 *
 * Sert à préremplir un formulaire de modification et à écrire un tableur. La division y est la
 * même que dans `formatCents`, et pour la même raison : `cents / 100` puis `toFixed(2)` arrondit
 * le montant qu'il prétend seulement afficher.
 *
 * Les points de base se lisent avec la même fonction : 1000 points de base sont à « 10,00 % » ce
 * que 1000 centimes sont à « 10,00 € ».
 */
export function inputFromCents(cents: number): string {
  const signe = cents < 0 ? '-' : ''
  const absolu = Math.abs(Math.trunc(cents))
  const centimes = absolu % CENTIMES_PAR_EURO
  const euros = (absolu - centimes) / CENTIMES_PAR_EURO
  return `${signe}${euros},${String(centimes).padStart(2, '0')}`
}

/** Taux en points de base rendu lisible : 550 → « 5,5 % », 2000 → « 20 % ». */
export function formatRateBp(rateBp: number): string {
  const pourcent = rateBp / (POINTS_DE_BASE / 100)
  return `${pourcent.toLocaleString('fr-FR', { maximumFractionDigits: 2 })} %`
}

/**
 * Une mesure du métré, rendue lisible avec son unité.
 *
 * `null` devient un tiret et **jamais un zéro** : c'est le contrat de `build_takeoff`, où une
 * valeur non établie s'accompagne d'un avertissement. Un zéro se confondrait avec une mesure
 * réellement nulle et se perdrait dans une somme.
 */
export function formatMesure(valeur: number | null, unite = ''): string {
  if (valeur === null || valeur === undefined) return '—'
  const nombre = valeur.toLocaleString('fr-FR', { maximumFractionDigits: 3 })
  return unite ? `${nombre} ${unite}` : nombre
}

// --- Téléchargement d'un document ----------------------------------------------------------------

/**
 * Enregistre un fichier reçu du serveur.
 *
 * Rend `false` là où `URL.createObjectURL` n'existe pas plutôt que de lever : l'appelant affiche
 * alors un message, au lieu de laisser un bouton mort sans explication.
 */
export function saveBlob(blob: Blob, nomDuFichier: string): boolean {
  if (typeof URL.createObjectURL !== 'function') return false

  const url = URL.createObjectURL(blob)
  const lien = document.createElement('a')
  lien.href = url
  lien.download = nomDuFichier
  lien.click()
  // Révoqué au tour suivant seulement : révoquer dans la foulée du clic annule le téléchargement
  // avant qu'il ait commencé sur plusieurs navigateurs.
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  return true
}

// --- Cycle de vie du document --------------------------------------------------------------------

/** Libellés des cinq états. Écrits en français métier, pas en jargon de base de données. */
export const LIBELLES_STATUT: Record<QuoteStatus, string> = {
  draft: 'Brouillon',
  sent: 'Émis',
  accepted: 'Accepté par le client',
  refused: 'Refusé',
  invoiced: 'Facturé',
}

/**
 * Un devis sorti du brouillon ne se modifie plus.
 *
 * Le serveur refuse toute modification autre que le statut dès `sent` (409). L'interface doit le
 * montrer **avant** le refus : découvrir qu'un document est figé par un message d'erreur, c'est
 * l'apprendre trop tard.
 */
export function estFige(devis: Quote | null): boolean {
  return devis !== null && devis.status !== 'draft'
}

export const useQuoteStore = defineStore('quote', () => {
  const devis = ref<Quote | null>(null)
  const resumes = ref<QuoteSummary[]>([])
  const loading = ref(false)
  const busy = ref(false)
  const error = ref<string | null>(null)
  /** Message neutre, distinct d'une erreur : `role="status"` et non `role="alert"` côté vue. */
  const notice = ref<string | null>(null)

  const fige = computed(() => estFige(devis.value))

  function messageOf(caught: unknown): string {
    return caught instanceof Error ? caught.message : String(caught)
  }

  /**
   * Exécute une action de document en portant son erreur à l'écran.
   *
   * Sans cette enveloppe, chaque action recopiait le même `try/catch/finally` — et c'est celle
   * qu'on oublie qui laisse un bouton tourner indéfiniment après un refus du serveur.
   */
  async function run<T>(action: () => Promise<T>): Promise<T | null> {
    busy.value = true
    error.value = null
    // Le message de l'action précédente part avec elle : « Devis émis » affiché sous « Transition
    // impossible » laisserait croire que l'émission a quand même eu lieu.
    notice.value = null
    try {
      return await action()
    } catch (caught) {
      error.value = messageOf(caught)
      return null
    } finally {
      busy.value = false
    }
  }

  async function loadQuote(quoteId: number): Promise<void> {
    loading.value = true
    error.value = null
    try {
      devis.value = await api.readQuote(quoteId)
    } catch (caught) {
      error.value = messageOf(caught)
      devis.value = null
    } finally {
      loading.value = false
    }
  }

  /** Sans `projectId`, tous les devis des entreprises du compte. */
  async function loadList(projectId?: number): Promise<void> {
    loading.value = true
    error.value = null
    try {
      resumes.value =
        projectId === undefined ? await api.listQuotes() : await api.listProjectQuotes(projectId)
    } catch (caught) {
      error.value = messageOf(caught)
      resumes.value = []
    } finally {
      loading.value = false
    }
  }

  async function create(projectId: number, payload: api.QuotePayload): Promise<Quote | null> {
    const cree = await run(() => api.createQuote(projectId, payload))
    if (cree) {
      devis.value = cree
      notice.value = `Devis créé en brouillon, ${cree.lines.length} ligne(s) issues du métré.`
    }
    return cree
  }

  async function issue(): Promise<void> {
    const courant = devis.value
    if (!courant) return
    const emis = await run(() => api.issueQuote(courant.id))
    if (emis) {
      devis.value = emis
      notice.value = `Devis émis sous le numéro ${emis.number ?? '—'}. Son contenu est désormais figé.`
    }
  }

  /**
   * Fait avancer le devis dans son cycle de vie.
   *
   * Le serveur refuse les transitions qui remontent (« accepté » redevenu « brouillon ») : son
   * 409 remonte tel quel à l'écran, l'interface n'a pas à réimplémenter la table des transitions.
   */
  async function setStatus(status: QuoteStatus): Promise<void> {
    const courant = devis.value
    if (!courant) return
    const modifie = await run(() => api.updateQuote(courant.id, { status }))
    if (modifie) {
      devis.value = modifie
      notice.value = `Devis marqué « ${LIBELLES_STATUT[status]} ».`
    }
  }

  async function invoice(): Promise<void> {
    const courant = devis.value
    if (!courant) return
    const facture = await run(() => api.convertToInvoice(courant.id))
    if (facture) {
      devis.value = facture
      notice.value = `Facture n° ${facture.invoice_number ?? '—'} établie, aux mêmes lignes et aux mêmes prix.`
    }
  }

  /** Les trois fichiers du document. Le nom porte le numéro : un « document.pdf » ne se classe pas. */
  async function download(genre: 'devis' | 'facture-pdf' | 'facture-xml'): Promise<void> {
    const courant = devis.value
    if (!courant) return
    const reference =
      genre === 'devis' ? (courant.number ?? `brouillon-${courant.id}`) : (courant.invoice_number ?? `${courant.id}`)
    const nom =
      genre === 'facture-xml' ? `facture-${reference}.xml` : `${genre === 'devis' ? 'devis' : 'facture'}-${reference}.pdf`

    const blob = await run(() => {
      if (genre === 'devis') return api.downloadQuotePdf(courant.id)
      if (genre === 'facture-pdf') return api.downloadInvoicePdf(courant.id)
      return api.downloadInvoiceXml(courant.id)
    })
    if (!blob) return
    if (!saveBlob(blob, nom)) {
      error.value = "Ce navigateur ne sait pas enregistrer le fichier depuis cette page."
      return
    }
    notice.value = `${nom} téléchargé.`
  }

  function reset(): void {
    devis.value = null
    resumes.value = []
    error.value = null
    notice.value = null
  }

  return {
    devis,
    resumes,
    loading,
    busy,
    error,
    notice,
    fige,
    loadQuote,
    loadList,
    create,
    issue,
    setStatus,
    invoice,
    download,
    reset,
  }
})
