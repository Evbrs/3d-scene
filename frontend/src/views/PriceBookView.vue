<script setup lang="ts">
/**
 * Le barème : les prix de l'entreprise, et rien d'autre.
 *
 * C'est la seule donnée du produit qui est une politique commerciale et non une mesure. D'où
 * trois partis pris, tous hérités du serveur (`app/api/quotes.py`, `app/services/seed_prices.py`) :
 *
 * - **le code ne se modifie pas.** C'est la clé de rattachement du métré et des devis déjà émis ;
 *   le renommer casserait silencieusement les correspondances faites. L'écran l'affiche donc en
 *   lecture seule et propose de supprimer puis recréer ;
 * - **modifier un prix ne touche aucun document existant.** Les lignes d'un devis sont des copies.
 *   L'écran le dit, parce que l'inverse serait la crainte légitime de qui hésite à corriger un
 *   tarif ;
 * - **rien ne s'importe sans être relu.** L'import CSV remplit le formulaire ligne à ligne et
 *   compte ses échecs, il n'écrase pas un barème en silence.
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import * as api from '@/api/client'
import type { Organization, PriceItemPayload } from '@/api/client'
import type { PriceBook, PriceItem, PriceUnit } from '@/api/types'
import { centsFromInput, formatCents, formatRateBp, inputFromCents, saveBlob } from '@/stores/quote'

const entreprise = ref<Organization | null>(null)
const livres = ref<PriceBook[]>([])
const livreChoisi = ref<number | null>(null)
const lignes = ref<PriceItem[]>([])

const loading = ref(true)
const busy = ref(false)
const error = ref<string | null>(null)
const notice = ref<string | null>(null)

const nouveauCode = ref('')
const nouveauLibelle = ref('')
const nouvelleUnite = ref<PriceUnit>('m2')
const nouveauPrix = ref('')
const nouveauTaux = ref(1000)
const erreurNouvelleLigne = ref<string | null>(null)

const nouveauLivre = ref('')

/** Ligne en cours de modification. `null` : le tableau est en lecture. */
const lignEnEdition = ref<number | null>(null)
const editionLibelle = ref('')
const editionUnite = ref<PriceUnit>('m2')
const editionPrix = ref('')
const editionTaux = ref(1000)
const erreurEdition = ref<string | null>(null)

const UNITES: { valeur: PriceUnit; libelle: string }[] = [
  { valeur: 'm2', libelle: 'm² — surfaces' },
  { valeur: 'ml', libelle: 'ml — linéaires' },
  { valeur: 'u', libelle: 'unité' },
  { valeur: 'forfait', libelle: 'forfait' },
]

const TAUX_COURANTS = [
  { valeur: 550, libelle: '5,5 % — rénovation énergétique' },
  { valeur: 1000, libelle: '10 % — rénovation de plus de deux ans' },
  { valeur: 2000, libelle: '20 % — taux plein' },
]

function messageOf(caught: unknown): string {
  return caught instanceof Error ? caught.message : String(caught)
}

const livreCourant = computed(
  () => livres.value.find((livre) => livre.id === livreChoisi.value) ?? null,
)

async function chargerLignes(): Promise<void> {
  const livre = livreChoisi.value
  if (livre === null) {
    lignes.value = []
    return
  }
  lignes.value = await api.listPriceItems(livre)
}

async function refresh(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const entreprises = await api.listOrganizations()
    const premiere = entreprises[0]
    if (!premiere) {
      error.value = "Aucune entreprise n'est rattachée à ce compte."
      return
    }
    entreprise.value = premiere
    livres.value = await api.listPriceBooks(premiere.id)
    const defaut = livres.value.find((livre) => livre.is_default) ?? livres.value[0]
    livreChoisi.value = defaut?.id ?? null
    await chargerLignes()
  } catch (caught) {
    error.value = messageOf(caught)
  } finally {
    loading.value = false
  }
}

onMounted(refresh)

async function changerDeLivre(): Promise<void> {
  lignEnEdition.value = null
  busy.value = true
  error.value = null
  try {
    await chargerLignes()
  } catch (caught) {
    error.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}

async function creerLivre(): Promise<void> {
  const proprietaire = entreprise.value
  if (!proprietaire || nouveauLivre.value.trim() === '') return
  busy.value = true
  error.value = null
  try {
    const cree = await api.createPriceBook(proprietaire.id, nouveauLivre.value.trim())
    nouveauLivre.value = ''
    livres.value = await api.listPriceBooks(proprietaire.id)
    livreChoisi.value = cree.id
    await chargerLignes()
    notice.value = `Barème « ${cree.name} » créé.`
  } catch (caught) {
    error.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}

/** Le serveur exige des majuscules, des chiffres, des tirets et des soulignés. */
const MOTIF_CODE = /^[A-Z0-9][A-Z0-9_-]{0,39}$/

function ligneDepuisLaSaisie(): PriceItemPayload | null {
  const code = nouveauCode.value.trim().toUpperCase()
  if (!MOTIF_CODE.test(code)) {
    erreurNouvelleLigne.value =
      'Code invalide : majuscules, chiffres, tirets et soulignés, 40 caractères au plus.'
    return null
  }
  if (nouveauLibelle.value.trim() === '') {
    erreurNouvelleLigne.value =
      "Le libellé est obligatoire : c'est lui qui figure sur le devis."
    return null
  }
  const centimes = centsFromInput(nouveauPrix.value)
  if (centimes === null || centimes < 0) {
    erreurNouvelleLigne.value = 'Prix illisible. Attendu : un nombre en euros, par exemple 24,50.'
    return null
  }
  return {
    code,
    label: nouveauLibelle.value.trim(),
    unit: nouvelleUnite.value,
    unit_price_cents: centimes,
    vat_rate_bp: nouveauTaux.value,
  }
}

async function ajouterLigne(): Promise<void> {
  erreurNouvelleLigne.value = null
  const livre = livreChoisi.value
  if (livre === null) return
  const charge = ligneDepuisLaSaisie()
  if (!charge) return

  busy.value = true
  error.value = null
  try {
    await api.createPriceItem(livre, charge)
    nouveauCode.value = ''
    nouveauLibelle.value = ''
    nouveauPrix.value = ''
    await chargerLignes()
    notice.value = `Ligne ${charge.code} ajoutée au barème.`
  } catch (caught) {
    error.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}

function ouvrirEdition(ligne: PriceItem): void {
  lignEnEdition.value = ligne.id
  editionLibelle.value = ligne.label
  editionUnite.value = ligne.unit
  editionPrix.value = inputFromCents(ligne.unit_price_cents)
  editionTaux.value = ligne.vat_rate_bp
  erreurEdition.value = null
}

async function enregistrerEdition(): Promise<void> {
  erreurEdition.value = null
  const identifiant = lignEnEdition.value
  if (identifiant === null) return
  const centimes = centsFromInput(editionPrix.value)
  if (centimes === null || centimes < 0) {
    erreurEdition.value = 'Prix illisible. Attendu : un nombre en euros, par exemple 24,50.'
    return
  }

  busy.value = true
  error.value = null
  try {
    await api.updatePriceItem(identifiant, {
      label: editionLibelle.value.trim(),
      unit: editionUnite.value,
      unit_price_cents: centimes,
      vat_rate_bp: editionTaux.value,
    })
    lignEnEdition.value = null
    await chargerLignes()
    notice.value = 'Ligne modifiée. Les devis déjà établis gardent leurs prix.'
  } catch (caught) {
    error.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}

async function supprimerLigne(ligne: PriceItem): Promise<void> {
  if (!window.confirm(`Supprimer la ligne ${ligne.code} du barème ?`)) return
  busy.value = true
  error.value = null
  try {
    await api.deletePriceItem(ligne.id)
    await chargerLignes()
    notice.value = `Ligne ${ligne.code} supprimée.`
  } catch (caught) {
    error.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}

// --- Import et export ----------------------------------------------------------------------------

const SEPARATEUR = ';'
const ENTETE_CSV = ['code', 'libelle', 'unite', 'prix_unitaire_ht', 'taux_tva_pourcent']

/**
 * Le barème au format tableur.
 *
 * Point-virgule et virgule décimale, comme le métré : c'est ce qu'attend un tableur configuré en
 * français, et la BOM évite qu'« Faïence » y devienne « FaÃ¯ence ».
 */
function exporterCsv(): void {
  const cellules = (valeurs: string[]): string =>
    valeurs.map((valeur) => `"${valeur.replace(/"/g, '""')}"`).join(SEPARATEUR)

  const corps = lignes.value.map((ligne) =>
    cellules([
      ligne.code,
      ligne.label,
      ligne.unit,
      inputFromCents(ligne.unit_price_cents),
      inputFromCents(ligne.vat_rate_bp),
    ]),
  )
  const contenu = `﻿${[cellules(ENTETE_CSV), ...corps].join('\r\n')}\r\n`

  if (!saveBlob(new Blob([contenu], { type: 'text/csv;charset=utf-8' }), 'bareme.csv')) {
    error.value = "Ce navigateur ne sait pas enregistrer le fichier depuis cette page."
    return
  }
  notice.value = `${lignes.value.length} ligne(s) exportées.`
}

const rapportImport = ref<string | null>(null)

function decoupe(ligne: string): string[] {
  return ligne
    .split(SEPARATEUR)
    .map((cellule) => cellule.trim().replace(/^"(.*)"$/s, '$1').replace(/""/g, '"'))
}

/**
 * Importe un barème, ligne par ligne, en comptant ses échecs.
 *
 * Aucune écriture en lot : le serveur n'expose pas de route d'import, et en fabriquer une côté
 * client — cent requêtes lancées ensemble — se solderait par un barème à moitié écrit sans que
 * personne sache où. Les lignes partent donc en série, et le rapport nomme celles qui ont échoué.
 */
async function importerCsv(evenement: Event): Promise<void> {
  const entree = evenement.target as HTMLInputElement
  const fichier = entree.files?.[0]
  const livre = livreChoisi.value
  if (!fichier || livre === null) return

  busy.value = true
  error.value = null
  rapportImport.value = null
  try {
    const texte = await fichier.text()
    const toutes = texte
      .replace(/^﻿/, '')
      .split(/\r?\n/)
      .filter((ligne) => ligne.trim() !== '')
    // La première ligne est un en-tête dès qu'elle commence par « code » : les tableurs
    // l'écrivent, et l'importer produirait une ligne de barème nommée « libelle ».
    const premieres = decoupe(toutes[0] ?? '')
    const corps = premieres[0]?.toLowerCase() === 'code' ? toutes.slice(1) : toutes

    let ecrites = 0
    const refusees: string[] = []
    for (const brute of corps) {
      const cellules = decoupe(brute)
      const code = (cellules[0] ?? '').toUpperCase()
      const centimes = centsFromInput(cellules[3] ?? '')
      const pourcent = centsFromInput(cellules[4] ?? '')
      if (!MOTIF_CODE.test(code) || centimes === null || pourcent === null) {
        refusees.push(code || brute.slice(0, 20))
        continue
      }
      try {
        await api.createPriceItem(livre, {
          code,
          label: cellules[1] ?? code,
          unit: (cellules[2] as PriceUnit) || 'm2',
          unit_price_cents: centimes,
          // Le pourcentage relu en centièmes est déjà en points de base : 10,00 % → 1000.
          vat_rate_bp: pourcent,
        })
        ecrites += 1
      } catch {
        refusees.push(code)
      }
    }

    await chargerLignes()
    rapportImport.value =
      refusees.length === 0
        ? `${ecrites} ligne(s) importées.`
        : `${ecrites} ligne(s) importées, ${refusees.length} refusée(s) : ${refusees.join(', ')}.`
  } catch (caught) {
    error.value = messageOf(caught)
  } finally {
    busy.value = false
    // Sans cette remise à zéro, réimporter le même fichier après correction ne déclenche aucun
    // événement : le navigateur considère que la valeur du champ n'a pas changé.
    entree.value = ''
  }
}
</script>

<template>
  <section class="bareme">
    <h1>Mon barème</h1>

    <p class="raccourcis">
      <RouterLink to="/projets">
        Mes chantiers
      </RouterLink>
      <RouterLink to="/devis">
        Mes devis
      </RouterLink>
      <RouterLink to="/entreprise">
        Mon entreprise
      </RouterLink>
    </p>

    <p
      v-if="error"
      class="erreur"
      role="alert"
    >
      {{ error }}
    </p>
    <p
      v-if="notice"
      class="notice"
      role="status"
    >
      {{ notice }}
    </p>

    <p v-if="loading">
      Chargement du barème…
    </p>

    <template v-else-if="entreprise">
      <p class="aide">
        Ces prix servent à chiffrer le métré. Les modifier ne change
        <strong>aucun devis déjà établi</strong> : les lignes d'un devis sont des copies figées au
        moment de sa création.
      </p>

      <div class="barre">
        <div class="champ">
          <label for="choix-bareme">Barème utilisé</label>
          <select
            id="choix-bareme"
            v-model.number="livreChoisi"
            @change="changerDeLivre"
          >
            <option
              v-for="livre in livres"
              :key="livre.id"
              :value="livre.id"
            >
              {{ livre.name }}{{ livre.is_default ? ' (par défaut)' : '' }}
            </option>
          </select>
        </div>

        <div class="champ">
          <label for="nouveau-bareme">Créer un autre barème</label>
          <input
            id="nouveau-bareme"
            v-model="nouveauLivre"
            type="text"
            maxlength="200"
            @keyup.enter="creerLivre"
          >
        </div>
        <button
          type="button"
          :disabled="busy || nouveauLivre.trim() === ''"
          @click="creerLivre"
        >
          Créer
        </button>
      </div>

      <div class="barre">
        <button
          type="button"
          :disabled="busy || lignes.length === 0"
          @click="exporterCsv"
        >
          Exporter le barème (CSV)
        </button>
        <div class="champ">
          <label for="import-bareme">Importer un barème (CSV)</label>
          <input
            id="import-bareme"
            type="file"
            accept=".csv,text/csv"
            :disabled="busy"
            aria-describedby="rapport-import"
            @change="importerCsv"
          >
          <span
            id="rapport-import"
            class="aide"
            role="status"
          >
            {{ rapportImport ?? `Colonnes attendues : ${ENTETE_CSV.join(', ')}.` }}
          </span>
        </div>
      </div>

      <h2>Lignes de {{ livreCourant?.name ?? 'ce barème' }}</h2>
      <p v-if="lignes.length === 0">
        Ce barème est vide. Ajoutez une première ligne ci-dessous, ou importez un fichier.
      </p>
      <div
        v-else
        class="tableau"
      >
        <table>
          <caption>
            {{ lignes.length }} ligne(s). Le code n'est pas modifiable : c'est la clé de
            rattachement du métré et des devis déjà établis.
          </caption>
          <thead>
            <tr>
              <th scope="col">
                Code
              </th>
              <th scope="col">
                Désignation
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
                Actions
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="ligne in lignes"
              :key="ligne.id"
            >
              <th scope="row">
                {{ ligne.code }}
              </th>
              <template v-if="lignEnEdition === ligne.id">
                <td>
                  <label
                    class="cache"
                    :for="`edition-libelle-${ligne.id}`"
                  >Désignation de {{ ligne.code }}</label>
                  <input
                    :id="`edition-libelle-${ligne.id}`"
                    v-model="editionLibelle"
                    type="text"
                    maxlength="200"
                  >
                </td>
                <td>
                  <label
                    class="cache"
                    :for="`edition-unite-${ligne.id}`"
                  >Unité de {{ ligne.code }}</label>
                  <select
                    :id="`edition-unite-${ligne.id}`"
                    v-model="editionUnite"
                  >
                    <option
                      v-for="unite in UNITES"
                      :key="unite.valeur"
                      :value="unite.valeur"
                    >
                      {{ unite.libelle }}
                    </option>
                  </select>
                </td>
                <td>
                  <label
                    class="cache"
                    :for="`edition-prix-${ligne.id}`"
                  >Prix unitaire de {{ ligne.code }}, en euros</label>
                  <input
                    :id="`edition-prix-${ligne.id}`"
                    v-model="editionPrix"
                    type="text"
                    inputmode="decimal"
                    :aria-invalid="erreurEdition !== null"
                    :aria-describedby="`erreur-edition-${ligne.id}`"
                  >
                  <span
                    :id="`erreur-edition-${ligne.id}`"
                    class="erreur"
                    role="alert"
                  >{{ erreurEdition }}</span>
                </td>
                <td>
                  <label
                    class="cache"
                    :for="`edition-tva-${ligne.id}`"
                  >Taux de TVA de {{ ligne.code }}</label>
                  <select
                    :id="`edition-tva-${ligne.id}`"
                    v-model.number="editionTaux"
                  >
                    <option
                      v-for="taux in TAUX_COURANTS"
                      :key="taux.valeur"
                      :value="taux.valeur"
                    >
                      {{ taux.libelle }}
                    </option>
                  </select>
                </td>
                <td class="actions">
                  <button
                    type="button"
                    data-variant="primary"
                    :disabled="busy"
                    @click="enregistrerEdition"
                  >
                    Enregistrer
                  </button>
                  <button
                    type="button"
                    @click="lignEnEdition = null"
                  >
                    Annuler
                  </button>
                </td>
              </template>
              <template v-else>
                <td>{{ ligne.label }}</td>
                <td>{{ ligne.unit }}</td>
                <td class="montant">
                  {{ formatCents(ligne.unit_price_cents) }}
                </td>
                <td>{{ formatRateBp(ligne.vat_rate_bp) }}</td>
                <td class="actions">
                  <button
                    type="button"
                    :disabled="busy"
                    @click="ouvrirEdition(ligne)"
                  >
                    Modifier {{ ligne.code }}
                  </button>
                  <button
                    type="button"
                    :disabled="busy"
                    @click="supprimerLigne(ligne)"
                  >
                    Supprimer {{ ligne.code }}
                  </button>
                </td>
              </template>
            </tr>
          </tbody>
        </table>
      </div>

      <h2>Ajouter une ligne</h2>
      <form
        class="formulaire"
        @submit.prevent="ajouterLigne"
      >
        <div class="champ">
          <label for="nouveau-code">Code</label>
          <input
            id="nouveau-code"
            v-model="nouveauCode"
            type="text"
            maxlength="40"
            required
            :aria-invalid="erreurNouvelleLigne !== null"
            aria-describedby="erreur-nouvelle-ligne"
          >
        </div>
        <div class="champ">
          <label for="nouveau-libelle">Désignation</label>
          <input
            id="nouveau-libelle"
            v-model="nouveauLibelle"
            type="text"
            maxlength="200"
            required
          >
        </div>
        <div class="champ">
          <label for="nouvelle-unite">Unité</label>
          <select
            id="nouvelle-unite"
            v-model="nouvelleUnite"
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
        <div class="champ">
          <label for="nouveau-prix">Prix unitaire HT (€)</label>
          <input
            id="nouveau-prix"
            v-model="nouveauPrix"
            type="text"
            inputmode="decimal"
            required
          >
        </div>
        <div class="champ">
          <label for="nouveau-taux">Taux de TVA</label>
          <select
            id="nouveau-taux"
            v-model.number="nouveauTaux"
          >
            <option
              v-for="taux in TAUX_COURANTS"
              :key="taux.valeur"
              :value="taux.valeur"
            >
              {{ taux.libelle }}
            </option>
          </select>
        </div>
        <button
          type="submit"
          data-variant="primary"
          :disabled="busy"
        >
          Ajouter
        </button>
      </form>
      <p
        id="erreur-nouvelle-ligne"
        class="erreur"
        role="alert"
      >
        {{ erreurNouvelleLigne }}
      </p>
    </template>
  </section>
</template>

<style scoped>
.bareme {
  max-width: 72rem;
}

.raccourcis {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
  margin: 0.25rem 0 1rem;
  font-size: 0.9rem;
}

.barre {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 0.9rem;
  margin-bottom: 1.25rem;
}

.champ {
  flex: 1;
  min-width: 12rem;
}

.formulaire {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 0.9rem;
  margin-bottom: 0.5rem;
}

.tableau {
  overflow-x: auto;
  margin-bottom: 1.5rem;
}

table {
  border-collapse: collapse;
  width: 100%;
  min-width: 40rem;
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
  vertical-align: top;
}

.montant {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}

.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

/* Visible d'un lecteur d'écran, absente de l'écran : dans un tableau éditable, l'en-tête de
   colonne ne suffit pas à nommer un champ, et un `aria-label` seul se perd à la traduction. */
.cache {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}

.aide {
  display: block;
  margin-top: 0.25rem;
  color: var(--texte-doux);
  font-size: 0.85rem;
}

.notice {
  color: var(--succes);
  font-weight: 600;
}
</style>
