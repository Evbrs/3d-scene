<script setup lang="ts">
/**
 * Le métré : ce qu'il y a à faire, mesuré, avant qu'il soit question de prix.
 *
 * Cet écran est délibérément **gratuit**. Le mur de paiement est un cran plus loin, sur le devis
 * chiffré (`docs/strategie-produit.md` §4) : on montre d'abord que le calcul est juste — surfaces
 * nettes percements déduits, linéaires de plinthe, calepinage avec ses coupes — et on demande de
 * payer ensuite. Un métré qu'on ne peut pas vérifier ne vaut rien.
 *
 * Deux points de rigueur, hérités du contrat de `app/geometry/quantities.py::build_takeoff` :
 *
 * - une valeur que le métré n'a pas su établir s'affiche en tiret, **jamais en zéro**, et les
 *   avertissements sont montrés en tête plutôt que relégués en bas de page. Des totaux partiels
 *   présentés comme complets donnent un devis trop bas, et c'est l'artisan qui paie l'écart ;
 * - le chiffrage imposé par face vit ici et pas dans le devis : c'est une décision de chantier
 *   (« ce mur-là est déjà peint, je ne refais que le bas »), prise devant les mesures.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import * as api from '@/api/client'
import type { FaceCosting, Takeoff, TakeoffFace } from '@/api/types'
import { centsFromInput, formatCents, formatMesure, saveBlob } from '@/stores/quote'

const props = defineProps<{ projectId: string }>()

const projet = computed(() => Number(props.projectId))

const metre = ref<Takeoff | null>(null)
const chiffrages = ref<FaceCosting[]>([])
const loading = ref(true)
const busy = ref(false)
const error = ref<string | null>(null)
const notice = ref<string | null>(null)

/** Formulaire du chiffrage imposé : une face, et jusqu'à trois décisions à son sujet. */
const faceChoisie = ref<number | null>(null)
const codeImpose = ref('')
const quantiteImposee = ref('')
const prixImpose = ref('')
const erreurPrix = ref<string | null>(null)

function messageOf(caught: unknown): string {
  return caught instanceof Error ? caught.message : String(caught)
}

/** Toutes les faces du projet, à plat : c'est ce que le sélecteur de chiffrage propose. */
const toutesLesFaces = computed<{ face: TakeoffFace; piece: string }[]>(() =>
  (metre.value?.rooms ?? []).flatMap((piece) =>
    piece.faces.map((face) => ({ face, piece: piece.name ?? 'Pièce sans nom' })),
  ),
)

const libellesDeFace = computed(() => {
  const table = new Map<number, string>()
  for (const { face, piece } of toutesLesFaces.value) {
    table.set(face.face_id, `${piece} — ${face.face_label}`)
  }
  return table
})

async function refresh(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    // Les deux lectures sont indépendantes : les enchaîner doublerait l'attente pour rien.
    const [releve, poses] = await Promise.all([
      api.readTakeoff(projet.value),
      api.listFaceCostings(projet.value),
    ])
    metre.value = releve
    chiffrages.value = poses
  } catch (caught) {
    error.value = messageOf(caught)
    metre.value = null
  } finally {
    loading.value = false
  }
}

onMounted(refresh)
watch(projet, refresh)

async function exporterCsv(): Promise<void> {
  busy.value = true
  error.value = null
  try {
    const blob = await api.downloadTakeoffCsv(projet.value)
    if (!saveBlob(blob, `metre-chantier-${projet.value}.csv`)) {
      error.value = "Ce navigateur ne sait pas enregistrer le fichier depuis cette page."
      return
    }
    notice.value = 'Métré exporté au format tableur.'
  } catch (caught) {
    error.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}

async function enregistrerChiffrage(): Promise<void> {
  erreurPrix.value = null
  const face = faceChoisie.value
  // `typeof` et non `!== null` : un `<select>` dont la valeur ne correspond à aucune option rend
  // `undefined`, et l'envoyer produirait un appel sur `/api/faces/undefined/costing`.
  if (typeof face !== 'number') return

  let prixEnCentimes: number | null = null
  if (prixImpose.value.trim() !== '') {
    prixEnCentimes = centsFromInput(prixImpose.value)
    if (prixEnCentimes === null) {
      erreurPrix.value = 'Montant illisible. Attendu : un nombre en euros, par exemple 24,50.'
      return
    }
  }

  busy.value = true
  error.value = null
  try {
    await api.setFaceCosting(face, {
      price_item_code: codeImpose.value.trim() === '' ? null : codeImpose.value.trim().toUpperCase(),
      // La quantité part en chaîne telle qu'elle a été saisie : la faire transiter par un nombre
      // en perdrait les millièmes que le serveur, lui, sait conserver.
      override_quantity: quantiteImposee.value.trim() === '' ? null : quantiteImposee.value.trim(),
      override_unit_price_cents: prixEnCentimes,
    })
    codeImpose.value = ''
    quantiteImposee.value = ''
    prixImpose.value = ''
    notice.value = 'Chiffrage imposé enregistré pour cette face.'
    chiffrages.value = await api.listFaceCostings(projet.value)
  } catch (caught) {
    error.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}

async function retirerChiffrage(faceId: number): Promise<void> {
  busy.value = true
  error.value = null
  try {
    await api.deleteFaceCosting(faceId)
    notice.value = 'Chiffrage imposé retiré : cette face revient au calcul automatique.'
    chiffrages.value = await api.listFaceCostings(projet.value)
  } catch (caught) {
    error.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}

const NATURES: Record<string, string> = {
  wall: 'Mur',
  floor: 'Sol',
  ceiling: 'Plafond',
}

function nature(kind: string): string {
  return NATURES[kind] ?? kind
}

/** Le taux de chute, exprimé en pourcentage : c'est la forme dans laquelle il se négocie. */
function chute(ratio: number): string {
  return `${(ratio * 100).toLocaleString('fr-FR', { maximumFractionDigits: 1 })} %`
}
</script>

<template>
  <section class="metre">
    <h1>Métré du chantier</h1>

    <p class="raccourcis">
      <RouterLink to="/projets">
        Mes chantiers
      </RouterLink>
      <RouterLink :to="`/projets/${projet}/plan`">
        Plan 2D
      </RouterLink>
      <RouterLink :to="`/projets/${projet}/vue-3d`">
        Vue 3D
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
      Calcul du métré en cours…
    </p>

    <template v-else-if="metre">
      <!-- En tête et non en pied : un total partiel présenté comme complet donne un devis trop
           bas, et personne ne fait défiler la page pour aller chercher ses réserves. -->
      <div
        v-if="metre.warnings.length > 0"
        class="reserves"
        role="alert"
      >
        <h2>Métré incomplet : {{ metre.warnings.length }} réserve(s)</h2>
        <p>
          Les totaux ci-dessous <strong>ignorent</strong> ce que le métré n'a pas su établir. Un
          devis produit en l'état sera sous-évalué.
        </p>
        <ul>
          <li
            v-for="(message, rang) in metre.warnings"
            :key="rang"
          >
            {{ message }}
          </li>
        </ul>
      </div>

      <div class="actions-principales">
        <RouterLink
          class="bouton-lien"
          :to="`/projets/${projet}/devis`"
        >
          Créer le devis
        </RouterLink>
        <button
          type="button"
          :disabled="busy"
          @click="exporterCsv"
        >
          Exporter le métré (CSV)
        </button>
      </div>

      <h2>Totaux du chantier</h2>
      <div class="tableau">
        <table>
          <caption>
            {{ metre.totals.room_count }} pièce(s). Un tiret signale une valeur que le métré n'a
            pas su établir — ce n'est pas un zéro.
          </caption>
          <thead>
            <tr>
              <th scope="col">
                Grandeur
              </th>
              <th scope="col">
                Valeur
              </th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">
                Surface de sol
              </th>
              <td>{{ formatMesure(metre.totals.floor_area_m2, 'm²') }}</td>
            </tr>
            <tr>
              <th scope="row">
                Surface de plafond
              </th>
              <td>{{ formatMesure(metre.totals.ceiling_area_m2, 'm²') }}</td>
            </tr>
            <tr>
              <th scope="row">
                Surface de murs, brute
              </th>
              <td>{{ formatMesure(metre.totals.wall_gross_area_m2, 'm²') }}</td>
            </tr>
            <tr>
              <th scope="row">
                Percements déduits
              </th>
              <td>{{ formatMesure(metre.totals.wall_openings_area_m2, 'm²') }}</td>
            </tr>
            <tr>
              <th scope="row">
                Surface de murs, nette
              </th>
              <td class="fort">
                {{ formatMesure(metre.totals.wall_net_area_m2, 'm²') }}
              </td>
            </tr>
            <tr>
              <th scope="row">
                Volume
              </th>
              <td>{{ formatMesure(metre.totals.volume_m3, 'm³') }}</td>
            </tr>
            <tr>
              <th scope="row">
                Plinthe
              </th>
              <td>{{ formatMesure(metre.totals.skirting_ml, 'ml') }}</td>
            </tr>
            <tr>
              <th scope="row">
                Corniche
              </th>
              <td>{{ formatMesure(metre.totals.cornice_ml, 'ml') }}</td>
            </tr>
            <tr>
              <th scope="row">
                Portes / fenêtres
              </th>
              <td>{{ metre.totals.door_count }} / {{ metre.totals.window_count }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <h2>Commande de matériaux</h2>
      <p v-if="metre.totals.coverings.length === 0">
        Aucun revêtement ne déclare de dimensions d'unité : rien à calepiner pour l'instant.
      </p>
      <div
        v-else
        class="tableau"
      >
        <table>
          <caption>
            Les unités sont sommées face par face : les chutes d'un mur ne se réemploient pas sur
            un autre.
          </caption>
          <thead>
            <tr>
              <th scope="col">
                Revêtement
              </th>
              <th scope="col">
                Pose
              </th>
              <th scope="col">
                Unité
              </th>
              <th scope="col">
                Surface nette
              </th>
              <th scope="col">
                Chute
              </th>
              <th scope="col">
                À commander
              </th>
              <th scope="col">
                Unités
              </th>
              <th scope="col">
                Entières
              </th>
              <th scope="col">
                Coupées
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="(lot, rang) in metre.totals.coverings"
              :key="rang"
            >
              <th scope="row">
                {{ lot.material ?? 'non précisé' }}
              </th>
              <td>{{ lot.pattern }}</td>
              <td>{{ lot.unit_width_cm }} × {{ lot.unit_height_cm }} cm</td>
              <td>{{ formatMesure(lot.net_area_m2, 'm²') }}</td>
              <td>{{ chute(lot.waste_ratio) }}</td>
              <td>{{ formatMesure(lot.ordered_area_m2, 'm²') }}</td>
              <td class="fort">
                {{ lot.units_total }}
              </td>
              <td>{{ formatMesure(lot.full_units) }}</td>
              <td>{{ formatMesure(lot.cut_units) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <h2>Détail par pièce</h2>
      <p v-if="metre.rooms.length === 0">
        Ce chantier n'a encore aucune pièce. Dessinez-en une dans le plan 2D pour obtenir un métré.
      </p>

      <section
        v-for="piece in metre.rooms"
        :key="piece.room_id ?? piece.name ?? 'piece'"
        class="piece"
      >
        <h3>{{ piece.name ?? 'Pièce sans nom' }}</h3>
        <p class="resume-piece">
          Sol {{ formatMesure(piece.floor_area_m2, 'm²') }} ·
          murs nets {{ formatMesure(piece.wall_net_area_m2, 'm²') }} ·
          plinthe {{ formatMesure(piece.skirting_ml, 'ml') }} ·
          hauteur sous plafond {{ formatMesure(piece.ceiling_height_m, 'm') }}
        </p>

        <div class="tableau">
          <table>
            <caption>Faces de « {{ piece.name ?? 'Pièce sans nom' }} »</caption>
            <thead>
              <tr>
                <th scope="col">
                  Face
                </th>
                <th scope="col">
                  Nature
                </th>
                <th scope="col">
                  Longueur
                </th>
                <th scope="col">
                  Hauteur
                </th>
                <th scope="col">
                  Surface brute
                </th>
                <th scope="col">
                  Percements
                </th>
                <th scope="col">
                  Surface nette
                </th>
                <th scope="col">
                  Ouvertures
                </th>
                <th scope="col">
                  Matériau
                </th>
                <th scope="col">
                  Calepinage
                </th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="face in piece.faces"
                :key="face.face_id"
              >
                <th scope="row">
                  {{ face.face_label }}
                </th>
                <td>{{ nature(face.kind) }}</td>
                <td>{{ formatMesure(face.length_m, 'm') }}</td>
                <td>{{ formatMesure(face.height_m, 'm') }}</td>
                <td>{{ formatMesure(face.gross_area_m2, 'm²') }}</td>
                <td>{{ formatMesure(face.openings_area_m2, 'm²') }}</td>
                <td class="fort">
                  {{ formatMesure(face.net_area_m2, 'm²') }}
                </td>
                <td>{{ face.door_count }} porte(s), {{ face.window_count }} fenêtre(s)</td>
                <td>{{ face.material ?? '—' }}</td>
                <td>
                  <template v-if="face.tiling">
                    {{ face.tiling.units_total }} unités ·
                    {{ formatMesure(face.tiling.full_units) }} entière(s) ·
                    {{ formatMesure(face.tiling.cut_units) }} coupée(s) ·
                    chute {{ chute(face.tiling.waste_ratio) }}
                  </template>
                  <template v-else>
                    —
                  </template>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <p
          v-if="piece.warnings.length > 0"
          class="reserve-piece"
          role="status"
        >
          {{ piece.warnings.join(' ') }}
        </p>
      </section>

      <h2>Chiffrage imposé</h2>
      <p class="aide">
        Par défaut, chaque face est chiffrée depuis son revêtement. Un chiffrage imposé prime sur
        ce calcul : c'est là qu'on inscrit un tarif négocié ou une reprise partielle relevée sur
        place.
      </p>

      <form
        class="formulaire-chiffrage"
        @submit.prevent="enregistrerChiffrage"
      >
        <div class="champ">
          <label for="face-chiffree">Face concernée</label>
          <select
            id="face-chiffree"
            v-model="faceChoisie"
            required
          >
            <option :value="null">
              Choisir une face…
            </option>
            <option
              v-for="entree in toutesLesFaces"
              :key="entree.face.face_id"
              :value="entree.face.face_id"
            >
              {{ entree.piece }} — {{ entree.face.face_label }} ({{ nature(entree.face.kind) }})
            </option>
          </select>
        </div>

        <div class="champ">
          <label for="code-impose">Code du barème</label>
          <input
            id="code-impose"
            v-model="codeImpose"
            type="text"
            maxlength="40"
            aria-describedby="aide-code"
          >
          <span
            id="aide-code"
            class="aide"
          >Laisser vide pour garder le code déduit du revêtement.</span>
        </div>

        <div class="champ">
          <label for="quantite-imposee">Quantité imposée</label>
          <input
            id="quantite-imposee"
            v-model="quantiteImposee"
            type="text"
            inputmode="decimal"
            aria-describedby="aide-quantite"
          >
          <span
            id="aide-quantite"
            class="aide"
          >Dans l'unité de la ligne de barème. Vide = quantité du métré.</span>
        </div>

        <div class="champ">
          <label for="prix-impose">Prix unitaire négocié</label>
          <input
            id="prix-impose"
            v-model="prixImpose"
            type="text"
            inputmode="decimal"
            :aria-invalid="erreurPrix !== null"
            aria-describedby="aide-prix"
          >
          <span
            id="aide-prix"
            :class="erreurPrix ? 'erreur' : 'aide'"
            :role="erreurPrix ? 'alert' : undefined"
          >{{ erreurPrix ?? 'En euros, par exemple 24,50. Vide = prix du barème.' }}</span>
        </div>

        <button
          type="submit"
          data-variant="primary"
          :disabled="busy || faceChoisie === null"
        >
          Imposer ce chiffrage
        </button>
      </form>

      <div
        v-if="chiffrages.length > 0"
        class="tableau"
      >
        <table>
          <caption>{{ chiffrages.length }} face(s) au chiffrage imposé</caption>
          <thead>
            <tr>
              <th scope="col">
                Face
              </th>
              <th scope="col">
                Code
              </th>
              <th scope="col">
                Quantité
              </th>
              <th scope="col">
                Prix unitaire
              </th>
              <th scope="col">
                Action
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="pose in chiffrages"
              :key="pose.id"
            >
              <th scope="row">
                {{ libellesDeFace.get(pose.face_id) ?? `Face n° ${pose.face_id}` }}
              </th>
              <td>{{ pose.price_item_code ?? '—' }}</td>
              <td>{{ pose.override_quantity ?? '—' }}</td>
              <td>
                {{
                  pose.override_unit_price_cents === null
                    ? '—'
                    : formatCents(pose.override_unit_price_cents)
                }}
              </td>
              <td>
                <button
                  type="button"
                  :disabled="busy"
                  @click="retirerChiffrage(pose.face_id)"
                >
                  Retirer
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </section>
</template>

<style scoped>
.metre {
  max-width: 72rem;
}

.raccourcis {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
  margin: 0.25rem 0 1rem;
  font-size: 0.9rem;
}

/* Les réserves du métré sont un avertissement de chiffrage, pas une décoration : elles portent
   une bordure épaisse à gauche ET un intitulé, la couleur seule ne disant rien à un daltonien. */
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

.actions-principales {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 1.5rem;
}

.bouton-lien {
  display: inline-block;
  padding: 0.4rem 0.8rem;
  border: 1px solid var(--accent);
  border-radius: 0.35rem;
  background: var(--accent);
  color: #ffffff;
  text-decoration: none;
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

.fort {
  font-weight: 700;
}

.piece {
  margin-bottom: 1.5rem;
}

.piece h3 {
  margin-bottom: 0.2rem;
}

.resume-piece {
  margin: 0 0 0.6rem;
  color: var(--texte-doux);
}

.reserve-piece {
  color: var(--texte-doux);
  font-style: italic;
}

.formulaire-chiffrage {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: 0.9rem;
  margin-bottom: 1.25rem;
}

.champ {
  flex: 1;
  min-width: 12rem;
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
