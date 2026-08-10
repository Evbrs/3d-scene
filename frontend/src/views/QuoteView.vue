<script setup lang="ts">
/**
 * Devis et facture : l'écran où le chantier devient un contrat.
 *
 * Trois usages, une seule vue, parce que ce sont trois moments d'un même document :
 * la liste de tous les devis (`/devis`), la préparation depuis un chantier
 * (`/projets/:id/devis`) et le document lui-même (`/devis/:id`).
 *
 * **Ce qui est figé se voit.** Un devis émis porte un numéro, il est parti chez le client, et le
 * serveur refuse toute modification autre que son statut. L'écran le dit en tête du document et
 * retire les gestes correspondants : découvrir qu'un document est figé par un 409, c'est
 * l'apprendre après avoir cru le modifier.
 *
 * **Les lignes se composent à la création, elles ne se rafistolent pas.** C'est le choix du
 * serveur (`app/api/quotes.py`) et il est bon : les lignes déduites du métré et une saisie
 * manuelle réconciliées après coup, ce sont deux vérités pour un seul prix. Un devis à corriger
 * se recrée — le métré est déjà là, l'opération coûte un clic.
 *
 * **Aucun montant n'est recalculé ici.** Les totaux, la ventilation de TVA et le total de chaque
 * ligne viennent du serveur, qui les calcule en `Decimal` et les fige en centimes entiers. Le
 * navigateur formate, il ne compte pas.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import * as api from '@/api/client'
import type { CostedFaceKind, PriceItem, QuoteStatus } from '@/api/types'
import {
  LIBELLES_STATUT,
  centsFromInput,
  formatCents,
  formatRateBp,
  useQuoteStore,
} from '@/stores/quote'

const props = defineProps<{ projectId?: string; quoteId?: string }>()

const store = useQuoteStore()

const projet = computed(() => (props.projectId === undefined ? null : Number(props.projectId)))
// Volontairement pas nommé `document` : le nom est déjà celui du DOM, et le confondre dans une
// vue qui manipule des fichiers téléchargés est une erreur qu'on ne voit qu'à l'exécution.
const documentId = computed(() => (props.quoteId === undefined ? null : Number(props.quoteId)))

/** Lignes saisies à la main, en cours de composition. Les montants y sont des chaînes : la
 *  conversion en centimes entiers n'a lieu qu'à l'envoi, une fois validée. */
interface LigneSaisie {
  label: string
  unit: api.QuoteLineInput['unit']
  quantity: string
  prix: string
  vat_rate_bp: number
}

const clientNom = ref('')
const clientParticulier = ref(true)
const clientEmail = ref('')
const clientTelephone = ref('')
const clientAdresse = ref('')
const clientCodePostal = ref('')
const clientVille = ref('')
const chantierAdresse = ref('')
const chantierCodePostal = ref('')
const chantierVille = ref('')
const remarques = ref('')
const avecPlinthe = ref(true)
const avecCorniche = ref(false)
const avecOuvertures = ref(false)
const baremeChoisi = ref<number | null>(null)
const codesParDefaut = ref<Record<CostedFaceKind, string>>({ wall: '', floor: '', ceiling: '' })
const lignesSaisies = ref<LigneSaisie[]>([])
/**
 * Deux messages et non un seul : chacun est rattaché au champ qu'il concerne par
 * `aria-describedby`. Un message global en tête de formulaire oblige un utilisateur de lecteur
 * d'écran à retrouver lui-même le champ fautif, ce que la règle WCAG 3.3.1 demande d'éviter.
 */
const erreurNom = ref<string | null>(null)
const erreurLignes = ref<string | null>(null)

const bareme = ref<PriceItem[]>([])
const chargementBareme = ref(false)

const NATURES: { cle: CostedFaceKind; libelle: string }[] = [
  { cle: 'wall', libelle: 'Murs' },
  { cle: 'floor', libelle: 'Sols' },
  { cle: 'ceiling', libelle: 'Plafonds' },
]

/** Les trois taux de la rénovation en métropole. Ce ne sont pas les seuls légaux : la Corse et
 *  l'outre-mer en connaissent d'autres, donc le champ reste ouvert à la saisie. */
const TAUX_COURANTS = [550, 1000, 2000]

const UNITES: { valeur: api.QuoteLineInput['unit']; libelle: string }[] = [
  { valeur: 'm2', libelle: 'm²' },
  { valeur: 'ml', libelle: 'ml' },
  { valeur: 'u', libelle: 'unité' },
  { valeur: 'forfait', libelle: 'forfait' },
]

async function chargerBareme(): Promise<void> {
  chargementBareme.value = true
  try {
    const entreprises = await api.listOrganizations()
    const entreprise = entreprises[0]
    if (!entreprise) return
    const livres = await api.listPriceBooks(entreprise.id)
    const livre = livres.find((candidat) => candidat.is_default) ?? livres[0]
    if (!livre) return
    baremeChoisi.value = livre.id
    bareme.value = await api.listPriceItems(livre.id)
  } catch {
    // Un barème indisponible ne doit pas empêcher de faire un devis : les codes par défaut
    // redeviennent alors une saisie libre, et le serveur tranchera.
    bareme.value = []
  } finally {
    chargementBareme.value = false
  }
}

/**
 * Codes proposables pour une nature de face : ceux du barème qui se comptent au mètre carré.
 *
 * Une face se chiffre à la surface. Proposer une ligne au mètre linéaire ou au forfait produirait
 * une quantité qui ne veut rien dire — le métré lui donnerait des m² et le barème facturerait des
 * mètres de plinthe.
 */
const codesAuMetreCarre = computed(() => bareme.value.filter((ligne) => ligne.unit === 'm2'))

function ajouterLigne(): void {
  lignesSaisies.value.push({ label: '', unit: 'forfait', quantity: '1', prix: '', vat_rate_bp: 1000 })
}

function retirerLigne(rang: number): void {
  lignesSaisies.value.splice(rang, 1)
}

async function creer(): Promise<void> {
  erreurNom.value = null
  erreurLignes.value = null
  const chantier = projet.value
  if (chantier === null) return
  if (clientNom.value.trim() === '') {
    erreurNom.value = 'Le nom du client est obligatoire : un devis est nominatif.'
    return
  }

  const extra: api.QuoteLineInput[] = []
  for (const [rang, ligne] of lignesSaisies.value.entries()) {
    if (ligne.label.trim() === '') {
      erreurLignes.value = `Ligne ${rang + 1} : le libellé est obligatoire.`
      return
    }
    const centimes = ligne.prix.trim() === '' ? 0 : centsFromInput(ligne.prix)
    if (centimes === null) {
      erreurLignes.value = `Ligne ${rang + 1} : montant illisible. Attendu par exemple 24,50.`
      return
    }
    extra.push({
      label: ligne.label.trim(),
      unit: ligne.unit,
      quantity: ligne.quantity.trim() === '' ? '1' : ligne.quantity.trim(),
      unit_price_cents: centimes,
      vat_rate_bp: ligne.vat_rate_bp,
    })
  }

  const codes: Partial<Record<CostedFaceKind, string>> = {}
  for (const { cle } of NATURES) {
    const code = codesParDefaut.value[cle].trim()
    if (code !== '') codes[cle] = code.toUpperCase()
  }

  const cree = await store.create(chantier, {
    client_name: clientNom.value.trim(),
    client_is_consumer: clientParticulier.value,
    client_email: clientEmail.value.trim() || null,
    client_phone: clientTelephone.value.trim() || null,
    client_address_line1: clientAdresse.value.trim() || null,
    client_postal_code: clientCodePostal.value.trim() || null,
    client_city: clientVille.value.trim() || null,
    site_address_line1: chantierAdresse.value.trim() || null,
    site_postal_code: chantierCodePostal.value.trim() || null,
    site_city: chantierVille.value.trim() || null,
    notes: remarques.value.trim() || null,
    price_book_id: baremeChoisi.value,
    default_price_codes: codes,
    include_skirting: avecPlinthe.value,
    include_cornice: avecCorniche.value,
    include_openings: avecOuvertures.value,
    extra_lines: extra,
  })

  if (cree) {
    lignesSaisies.value = []
    await store.loadList(chantier)
  }
}

async function recharger(): Promise<void> {
  store.reset()
  if (documentId.value !== null) {
    await store.loadQuote(documentId.value)
    return
  }
  if (projet.value !== null) {
    await Promise.all([store.loadList(projet.value), chargerBareme()])
    return
  }
  await store.loadList()
}

onMounted(recharger)
watch([projet, documentId], recharger)

function dateFr(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Intl.DateTimeFormat('fr-FR', { dateStyle: 'long' }).format(new Date(iso))
}

/** Les gestes possibles dépendent du statut, et c'est le serveur qui en fixe la table. */
const peutEmettre = computed(() => store.devis?.status === 'draft')
const peutStatuer = computed(() => store.devis?.status === 'sent')
const peutFacturer = computed(() => store.devis?.status === 'accepted')
const estFacture = computed(() => store.devis?.status === 'invoiced')

function statuer(statut: QuoteStatus): void {
  void store.setStatus(statut)
}
</script>

<template>
  <section class="devis">
    <h1 v-if="documentId !== null">
      Document commercial
    </h1>
    <h1 v-else-if="projet !== null">
      Devis du chantier
    </h1>
    <h1 v-else>
      Tous mes devis
    </h1>

    <p class="raccourcis">
      <RouterLink to="/projets">
        Mes chantiers
      </RouterLink>
      <RouterLink
        v-if="projet !== null"
        :to="`/projets/${projet}/metre`"
      >
        Métré du chantier
      </RouterLink>
      <RouterLink to="/devis">
        Tous mes devis
      </RouterLink>
      <RouterLink to="/bareme">
        Mon barème
      </RouterLink>
      <RouterLink to="/entreprise">
        Mon entreprise
      </RouterLink>
    </p>

    <p
      v-if="store.error"
      class="erreur"
      role="alert"
    >
      {{ store.error }}
    </p>
    <p
      v-if="store.notice"
      class="notice"
      role="status"
    >
      {{ store.notice }}
    </p>

    <p v-if="store.loading">
      Chargement…
    </p>

    <!-- ------------------------------------------------------------------ Document -->
    <template v-else-if="documentId !== null && store.devis">
      <div
        v-if="store.fige"
        class="fige"
        role="status"
      >
        <h2>Document figé</h2>
        <p>
          Émis le {{ dateFr(store.devis.issued_at) }} sous le numéro
          <strong>{{ store.devis.number ?? '—' }}</strong>. Son contenu ne peut plus être modifié :
          c'est l'exemplaire qui est parti chez le client. Pour corriger une information, émettez
          un nouveau devis depuis le chantier.
        </p>
      </div>
      <p
        v-else
        class="brouillon"
        role="status"
      >
        <strong>Brouillon.</strong> Aucun numéro ne lui est encore attribué et il n'engage
        personne. L'émission le figera et lui donnera son numéro.
      </p>

      <dl class="entete">
        <dt>Statut</dt>
        <dd>{{ LIBELLES_STATUT[store.devis.status] }}</dd>
        <dt>Client</dt>
        <dd>{{ store.devis.client_name }}</dd>
        <dt>Chantier</dt>
        <dd>{{ store.devis.project_name ?? '—' }}</dd>
        <dt>Valable jusqu'au</dt>
        <dd>{{ dateFr(store.devis.valid_until) }}</dd>
        <template v-if="store.devis.invoice_number">
          <dt>Numéro de facture</dt>
          <dd>{{ store.devis.invoice_number }}</dd>
          <dt>Échéance de paiement</dt>
          <dd>{{ dateFr(store.devis.due_date) }}</dd>
        </template>
      </dl>

      <div
        v-if="store.devis.warnings.length > 0"
        class="reserves"
        role="alert"
      >
        <h2>{{ store.devis.warnings.length }} réserve(s) de chiffrage</h2>
        <p>
          Ces faces n'ont produit <strong>aucune ligne</strong> : le devis a l'air complet et ne
          l'est pas. Complétez le barème ou imposez un chiffrage, puis recréez le devis.
        </p>
        <ul>
          <li
            v-for="(message, rang) in store.devis.warnings"
            :key="rang"
          >
            {{ message }}
          </li>
        </ul>
      </div>

      <h2>Lignes</h2>
      <div class="tableau">
        <table>
          <caption>
            {{ store.devis.lines.length }} ligne(s). Les prix sont recopiés au moment de la
            création : modifier le barème ensuite ne touche pas ce document.
          </caption>
          <thead>
            <tr>
              <th scope="col">
                Désignation
              </th>
              <th scope="col">
                Face
              </th>
              <th scope="col">
                Quantité
              </th>
              <th scope="col">
                Unité
              </th>
              <th scope="col">
                Prix unitaire HT
              </th>
              <th scope="col">
                TVA
              </th>
              <th scope="col">
                Total HT
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="ligne in store.devis.lines"
              :key="ligne.id"
            >
              <th scope="row">
                {{ ligne.label }}
              </th>
              <td>{{ ligne.source_face_id ? `n° ${ligne.source_face_id}` : '—' }}</td>
              <td>{{ ligne.quantity }}</td>
              <td>{{ ligne.unit }}</td>
              <td>{{ formatCents(ligne.unit_price_cents) }}</td>
              <td>{{ formatRateBp(ligne.vat_rate_bp) }}</td>
              <td class="montant">
                {{ formatCents(ligne.total_ht_cents) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <h2>Totaux</h2>
      <div class="tableau">
        <table>
          <caption>
            La TVA est calculée par taux sur la somme des bases, jamais ligne à ligne : c'est la
            méthode des documents comptables français.
          </caption>
          <thead>
            <tr>
              <th scope="col">
                Base
              </th>
              <th scope="col">
                Taux
              </th>
              <th scope="col">
                TVA
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="tranche in store.devis.vat_breakdown"
              :key="tranche.rate_bp"
            >
              <th scope="row">
                {{ formatCents(tranche.base_cents) }}
              </th>
              <td>{{ formatRateBp(tranche.rate_bp) }}</td>
              <td class="montant">
                {{ formatCents(tranche.tax_cents) }}
              </td>
            </tr>
          </tbody>
          <tfoot>
            <tr>
              <th scope="row">
                Total HT
              </th>
              <td />
              <td class="montant">
                {{ formatCents(store.devis.total_ht_cents) }}
              </td>
            </tr>
            <tr>
              <th scope="row">
                Total TVA
              </th>
              <td />
              <td class="montant">
                {{ formatCents(store.devis.total_tva_cents) }}
              </td>
            </tr>
            <tr class="total-ttc">
              <th scope="row">
                Total TTC
              </th>
              <td />
              <td class="montant">
                {{ formatCents(store.devis.total_ttc_cents) }}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>

      <h2>Suite à donner</h2>
      <div class="actions">
        <button
          v-if="peutEmettre"
          type="button"
          data-variant="primary"
          :disabled="store.busy"
          @click="store.issue()"
        >
          Émettre le devis (il sera figé)
        </button>
        <template v-if="peutStatuer">
          <button
            type="button"
            data-variant="primary"
            :disabled="store.busy"
            @click="statuer('accepted')"
          >
            Le client a accepté
          </button>
          <button
            type="button"
            :disabled="store.busy"
            @click="statuer('refused')"
          >
            Le client a refusé
          </button>
        </template>
        <button
          v-if="peutFacturer"
          type="button"
          data-variant="primary"
          :disabled="store.busy"
          @click="store.invoice()"
        >
          Facturer
        </button>
        <button
          type="button"
          :disabled="store.busy"
          @click="store.download('devis')"
        >
          Télécharger le devis (PDF)
        </button>
        <template v-if="estFacture">
          <button
            type="button"
            :disabled="store.busy"
            @click="store.download('facture-pdf')"
          >
            Facture Factur-X (PDF)
          </button>
          <button
            type="button"
            :disabled="store.busy"
            @click="store.download('facture-xml')"
          >
            Facture (XML CII)
          </button>
        </template>
      </div>
      <p class="aide">
        Nous ne sommes pas une plateforme de dématérialisation agréée : le fichier produit est
        conforme, sa transmission à l'administration reste à votre charge.
      </p>
    </template>

    <!-- ------------------------------------------------------ Préparation depuis un chantier -->
    <template v-else-if="projet !== null">
      <p class="aide">
        Le devis est établi à partir du métré du chantier. Chaque face y devient une ligne, avec
        sa surface nette et le prix de votre barème.
        <RouterLink :to="`/projets/${projet}/metre`">
          Vérifier le métré d'abord
        </RouterLink>
      </p>

      <form @submit.prevent="creer">
        <h2>Le client</h2>
        <div class="grille">
          <div class="champ">
            <label for="client-nom">Nom du client</label>
            <input
              id="client-nom"
              v-model="clientNom"
              type="text"
              maxlength="200"
              required
              :aria-invalid="erreurNom !== null"
              aria-describedby="erreur-nom"
            >
            <span
              id="erreur-nom"
              class="erreur"
              role="alert"
            >{{ erreurNom }}</span>
          </div>
          <div class="champ">
            <label for="client-email">Adresse e-mail</label>
            <input
              id="client-email"
              v-model="clientEmail"
              type="email"
              maxlength="200"
            >
          </div>
          <div class="champ">
            <label for="client-telephone">Téléphone</label>
            <input
              id="client-telephone"
              v-model="clientTelephone"
              type="tel"
              maxlength="30"
            >
          </div>
          <div class="champ">
            <label for="client-adresse">Adresse de facturation</label>
            <input
              id="client-adresse"
              v-model="clientAdresse"
              type="text"
              maxlength="200"
            >
          </div>
          <div class="champ">
            <label for="client-cp">Code postal</label>
            <input
              id="client-cp"
              v-model="clientCodePostal"
              type="text"
              maxlength="20"
            >
          </div>
          <div class="champ">
            <label for="client-ville">Ville</label>
            <input
              id="client-ville"
              v-model="clientVille"
              type="text"
              maxlength="100"
            >
          </div>
        </div>
        <p class="case">
          <input
            id="client-particulier"
            v-model="clientParticulier"
            type="checkbox"
          >
          <label for="client-particulier">
            Le client est un particulier (les mentions du code de la consommation s'appliquent)
          </label>
        </p>

        <h2>Le chantier</h2>
        <div class="grille">
          <div class="champ">
            <label for="chantier-adresse">Adresse des travaux</label>
            <input
              id="chantier-adresse"
              v-model="chantierAdresse"
              type="text"
              maxlength="200"
            >
          </div>
          <div class="champ">
            <label for="chantier-cp">Code postal</label>
            <input
              id="chantier-cp"
              v-model="chantierCodePostal"
              type="text"
              maxlength="20"
            >
          </div>
          <div class="champ">
            <label for="chantier-ville">Ville</label>
            <input
              id="chantier-ville"
              v-model="chantierVille"
              type="text"
              maxlength="100"
            >
          </div>
        </div>

        <h2>Ce que le métré ne devine pas</h2>
        <p class="aide">
          Un code par nature de face évite de rattacher soixante faces à la main. Il ne s'applique
          qu'aux faces dont le revêtement ne désigne aucune ligne de barème.
          <span v-if="chargementBareme">Chargement du barème…</span>
        </p>
        <div class="grille">
          <div
            v-for="entree in NATURES"
            :key="entree.cle"
            class="champ"
          >
            <label :for="`code-${entree.cle}`">{{ entree.libelle }} — code par défaut</label>
            <select
              :id="`code-${entree.cle}`"
              v-model="codesParDefaut[entree.cle]"
            >
              <option value="">
                Aucun
              </option>
              <option
                v-for="ligne in codesAuMetreCarre"
                :key="ligne.id"
                :value="ligne.code"
              >
                {{ ligne.code }} — {{ ligne.label }} ({{ formatCents(ligne.unit_price_cents) }})
              </option>
            </select>
          </div>
        </div>

        <fieldset class="options">
          <legend>Ouvrages annexes</legend>
          <p class="case">
            <input
              id="option-plinthe"
              v-model="avecPlinthe"
              type="checkbox"
            >
            <label for="option-plinthe">Chiffrer les plinthes</label>
          </p>
          <p class="case">
            <input
              id="option-corniche"
              v-model="avecCorniche"
              type="checkbox"
            >
            <label for="option-corniche">Chiffrer les corniches</label>
          </p>
          <p class="case">
            <input
              id="option-ouvertures"
              v-model="avecOuvertures"
              type="checkbox"
            >
            <label for="option-ouvertures">Chiffrer la pose des portes et fenêtres</label>
          </p>
        </fieldset>

        <h2>Lignes ajoutées à la main</h2>
        <p class="aide">
          Les lignes issues du métré sont calculées ; celles-ci sont les vôtres — protection des
          sols, évacuation des déchets, ouvrage particulier. Elles ne se modifient plus une fois
          le devis créé : un devis se régénère plutôt qu'il ne se rafistole.
        </p>

        <div
          v-for="(ligne, rang) in lignesSaisies"
          :key="rang"
          class="ligne-saisie"
        >
          <div class="champ">
            <label :for="`ligne-libelle-${rang}`">Ligne {{ rang + 1 }} — désignation</label>
            <input
              :id="`ligne-libelle-${rang}`"
              v-model="ligne.label"
              type="text"
              maxlength="300"
            >
          </div>
          <div class="champ court">
            <label :for="`ligne-quantite-${rang}`">Quantité</label>
            <input
              :id="`ligne-quantite-${rang}`"
              v-model="ligne.quantity"
              type="text"
              inputmode="decimal"
            >
          </div>
          <div class="champ court">
            <label :for="`ligne-unite-${rang}`">Unité</label>
            <select
              :id="`ligne-unite-${rang}`"
              v-model="ligne.unit"
            >
              <option
                v-for="unite in UNITES"
                :key="unite.valeur"
                :value="unite.valeur"
              >
                {{ unite.libelle }}
              </option>
            </select>
          </div>
          <div class="champ court">
            <label :for="`ligne-prix-${rang}`">Prix unitaire HT (€)</label>
            <input
              :id="`ligne-prix-${rang}`"
              v-model="ligne.prix"
              type="text"
              inputmode="decimal"
            >
          </div>
          <div class="champ court">
            <label :for="`ligne-tva-${rang}`">TVA</label>
            <select
              :id="`ligne-tva-${rang}`"
              v-model.number="ligne.vat_rate_bp"
            >
              <option
                v-for="taux in TAUX_COURANTS"
                :key="taux"
                :value="taux"
              >
                {{ formatRateBp(taux) }}
              </option>
            </select>
          </div>
          <button
            type="button"
            @click="retirerLigne(rang)"
          >
            Retirer la ligne {{ rang + 1 }}
          </button>
        </div>

        <p>
          <button
            type="button"
            aria-describedby="erreur-lignes"
            @click="ajouterLigne"
          >
            Ajouter une ligne
          </button>
          <span
            id="erreur-lignes"
            class="erreur"
            role="alert"
          >{{ erreurLignes }}</span>
        </p>

        <h2>Remarques portées sur le devis</h2>
        <div class="champ large">
          <label for="devis-remarques">Remarques</label>
          <textarea
            id="devis-remarques"
            v-model="remarques"
            maxlength="2000"
            rows="3"
          />
        </div>

        <p>
          <button
            type="submit"
            data-variant="primary"
            :disabled="store.busy"
          >
            Créer le devis
          </button>
        </p>
      </form>

      <h2>Devis de ce chantier</h2>
      <p v-if="store.resumes.length === 0">
        Aucun devis pour ce chantier.
      </p>
      <div
        v-else
        class="tableau"
      >
        <table>
          <caption>{{ store.resumes.length }} document(s)</caption>
          <thead>
            <tr>
              <th scope="col">
                Numéro
              </th>
              <th scope="col">
                Client
              </th>
              <th scope="col">
                Statut
              </th>
              <th scope="col">
                Total TTC
              </th>
              <th scope="col">
                Ouvrir
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="resume in store.resumes"
              :key="resume.id"
            >
              <th scope="row">
                {{ resume.number ?? 'brouillon' }}
              </th>
              <td>{{ resume.client_name }}</td>
              <td>{{ LIBELLES_STATUT[resume.status] }}</td>
              <td class="montant">
                {{ formatCents(resume.total_ttc_cents) }}
              </td>
              <td>
                <RouterLink :to="`/devis/${resume.id}`">
                  Ouvrir le devis n° {{ resume.id }}
                </RouterLink>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <!-- ------------------------------------------------------------------- Liste globale -->
    <template v-else>
      <p v-if="store.resumes.length === 0">
        Aucun devis pour l'instant. Un devis se crée depuis le métré d'un chantier.
      </p>
      <div
        v-else
        class="tableau"
      >
        <table>
          <caption>{{ store.resumes.length }} document(s), le plus récent d'abord</caption>
          <thead>
            <tr>
              <th scope="col">
                Numéro
              </th>
              <th scope="col">
                Client
              </th>
              <th scope="col">
                Statut
              </th>
              <th scope="col">
                Total HT
              </th>
              <th scope="col">
                Total TTC
              </th>
              <th scope="col">
                Ouvrir
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="resume in store.resumes"
              :key="resume.id"
            >
              <th scope="row">
                {{ resume.invoice_number ?? resume.number ?? 'brouillon' }}
              </th>
              <td>{{ resume.client_name }}</td>
              <td>{{ LIBELLES_STATUT[resume.status] }}</td>
              <td class="montant">
                {{ formatCents(resume.total_ht_cents) }}
              </td>
              <td class="montant">
                {{ formatCents(resume.total_ttc_cents) }}
              </td>
              <td>
                <RouterLink :to="`/devis/${resume.id}`">
                  Ouvrir le devis n° {{ resume.id }}
                </RouterLink>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </section>
</template>

<style scoped>
.devis {
  max-width: 72rem;
}

.raccourcis {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
  margin: 0.25rem 0 1rem;
  font-size: 0.9rem;
}

/* Le figement se signale par un encadré, un titre et une phrase — pas par une nuance de gris.
   C'est l'information qui distingue un brouillon d'un contrat. */
.fige {
  border: 1px solid var(--bordure);
  border-left: 4px solid var(--succes);
  border-radius: 0.35rem;
  padding: 0.75rem 1rem;
  margin-bottom: 1.25rem;
}

.fige h2 {
  margin-top: 0;
  font-size: 1.05rem;
  color: var(--succes);
}

.brouillon {
  border: 1px dashed var(--bordure);
  border-radius: 0.35rem;
  padding: 0.6rem 0.9rem;
  margin-bottom: 1.25rem;
}

.reserves {
  border: 1px solid var(--bordure);
  border-left: 4px solid var(--erreur);
  border-radius: 0.35rem;
  padding: 0.75rem 1rem;
  margin-bottom: 1.25rem;
}

.reserves h2 {
  margin-top: 0;
  font-size: 1.05rem;
  color: var(--erreur);
}

.entete {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 0.2rem 0.9rem;
  margin: 0 0 1.5rem;
}

.entete dt {
  color: var(--texte-doux);
}

.entete dd {
  margin: 0;
  font-weight: 600;
}

.tableau {
  overflow-x: auto;
  margin-bottom: 1.5rem;
}

table {
  border-collapse: collapse;
  width: 100%;
  min-width: 34rem;
}

caption {
  text-align: left;
  color: var(--texte-doux);
  padding-bottom: 0.5rem;
}

th,
td {
  text-align: left;
  padding: 0.4rem 0.6rem;
  border-bottom: 1px solid var(--bordure);
}

/* Les montants restent à gauche comme le reste du tableau mais en chiffres tabulaires : alignés
   verticalement, ils se comparent d'un coup d'œil sans dépendre de la police. */
.montant {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}

.total-ttc th,
.total-ttc td {
  font-size: 1.1rem;
  border-top: 2px solid var(--texte);
}

.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  margin-bottom: 0.75rem;
}

.grille {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
  gap: 0.9rem;
  margin-bottom: 1rem;
}

.champ.large {
  max-width: 44rem;
  margin-bottom: 1rem;
}

.champ.court {
  max-width: 9rem;
}

.case {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
}

.case input {
  width: auto;
}

.case label {
  font-weight: 400;
}

.options {
  border: 1px solid var(--bordure);
  border-radius: 0.35rem;
  padding: 0.5rem 1rem;
  margin-bottom: 1rem;
}

.ligne-saisie {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 0.75rem;
  padding: 0.6rem 0;
  border-bottom: 1px solid var(--bordure);
}

.aide {
  color: var(--texte-doux);
  font-size: 0.9rem;
}

.notice {
  color: var(--succes);
  font-weight: 600;
}
</style>
