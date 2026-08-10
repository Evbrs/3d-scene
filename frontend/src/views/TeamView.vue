<script setup lang="ts">
/**
 * L'entreprise et son équipe.
 *
 * Deux sujets sur un seul écran, et c'est délibéré : ce sont les deux faces de la même entité.
 * L'identité légale est ce qui rend un devis valable, l'équipe est ce qui justifie le palier
 * multi-locataire. Les séparer obligerait à choisir entre deux menus pour une seule question
 * — « qui sommes-nous ? ».
 *
 * **L'identité légale n'est pas de l'administratif.** Sans SIRET, forme juridique, capital, RCS,
 * numéro de TVA et assurance décennale, un devis émis ne porte pas ses mentions obligatoires
 * (`app/services/facturx.py`). Le serveur accepte pourtant tous ces champs vides, parce qu'il ne
 * peut pas savoir quand l'entreprise sera prête à émettre : c'est donc à cet écran de dire ce qui
 * manque, avant que le document parte chez un client.
 *
 * **Le jeton d'invitation n'existe qu'une fois.** La base n'en garde que le hachage. L'écran
 * l'affiche en le disant, et n'offre aucun bouton « revoir le jeton » : il n'y a rien à revoir.
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import * as api from '@/api/client'
import type { Organization } from '@/api/client'
import type { Invitation, InvitationCreated, Member, OrganizationRole } from '@/api/types'
import { centsFromInput, formatCents, inputFromCents } from '@/stores/quote'

const route = useRoute()

const entreprise = ref<Organization | null>(null)
const membres = ref<Member[]>([])
const invitations = ref<Invitation[]>([])

const loading = ref(true)
const busy = ref(false)
const error = ref<string | null>(null)
const notice = ref<string | null>(null)

/** Formulaire d'identité. Une copie locale : on n'écrit qu'à l'enregistrement. */
const identite = ref({
  name: '',
  siret: '',
  legal_form: '',
  capital: '',
  rcs: '',
  vat_number: '',
  address_line1: '',
  address_line2: '',
  postal_code: '',
  city: '',
  country: '',
  phone: '',
  billing_email: '',
  decennial_insurer: '',
  decennial_policy_number: '',
  decennial_coverage_area: '',
})
const erreurSiret = ref<string | null>(null)
const erreurTva = ref<string | null>(null)
const erreurCapital = ref<string | null>(null)

const invitationEmail = ref('')
const invitationRole = ref<OrganizationRole>('editor')
const invitationJours = ref(7)
const jetonEmis = ref<InvitationCreated | null>(null)

const jetonSaisi = ref('')

const ROLES: { valeur: OrganizationRole; libelle: string }[] = [
  { valeur: 'owner', libelle: 'Propriétaire — peut tout faire, y compris fermer le compte' },
  { valeur: 'admin', libelle: "Administrateur — émet les devis et gère l'équipe" },
  { valeur: 'editor', libelle: 'Préparateur — dessine les plans et prépare les devis' },
  { valeur: 'viewer', libelle: 'Lecteur — consulte sans modifier' },
]

/**
 * Mentions sans lesquelles un devis n'est pas valable.
 *
 * La liste est celle que `app/services/facturx.py` inscrit sur le document : ce qui n'est pas
 * renseigné n'y figure tout simplement pas.
 */
const MENTIONS_OBLIGATOIRES: { cle: keyof typeof identite.value; libelle: string }[] = [
  { cle: 'siret', libelle: 'SIRET' },
  { cle: 'legal_form', libelle: 'Forme juridique' },
  { cle: 'rcs', libelle: 'RCS' },
  { cle: 'vat_number', libelle: 'Numéro de TVA intracommunautaire' },
  { cle: 'address_line1', libelle: 'Adresse' },
  { cle: 'postal_code', libelle: 'Code postal' },
  { cle: 'city', libelle: 'Ville' },
  { cle: 'decennial_insurer', libelle: 'Assureur décennale' },
  { cle: 'decennial_policy_number', libelle: 'Numéro de police décennale' },
]

const mentionsManquantes = computed(() =>
  MENTIONS_OBLIGATOIRES.filter(({ cle }) => identite.value[cle].trim() === '').map(
    ({ libelle }) => libelle,
  ),
)

function messageOf(caught: unknown): string {
  return caught instanceof Error ? caught.message : String(caught)
}

function texteOuVide(valeur: string | null | undefined): string {
  return valeur ?? ''
}

function remplirIdentite(source: Organization): void {
  identite.value = {
    name: source.name,
    siret: texteOuVide(source.siret),
    legal_form: texteOuVide(source.legal_form),
    capital:
      source.share_capital_cents === null || source.share_capital_cents === undefined
        ? ''
        : inputFromCents(source.share_capital_cents),
    rcs: texteOuVide(source.rcs),
    vat_number: texteOuVide(source.vat_number),
    address_line1: texteOuVide(source.address_line1),
    address_line2: texteOuVide(source.address_line2),
    postal_code: texteOuVide(source.postal_code),
    city: texteOuVide(source.city),
    country: texteOuVide(source.country),
    phone: texteOuVide(source.phone),
    billing_email: texteOuVide(source.billing_email),
    decennial_insurer: texteOuVide(source.decennial_insurer),
    decennial_policy_number: texteOuVide(source.decennial_policy_number),
    decennial_coverage_area: texteOuVide(source.decennial_coverage_area),
  }
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
    // `listOrganizations` rend déjà tous les champs, mais la lecture unitaire est la route dont
    // dépend la page : la court-circuiter la rendrait fausse dès qu'un jour la liste s'allégera.
    const complete = await api.readOrganization(premiere.id)
    entreprise.value = complete
    remplirIdentite(complete)
    await chargerEquipe(complete.id)
  } catch (caught) {
    error.value = messageOf(caught)
  } finally {
    loading.value = false
  }
}

/**
 * Membres et invitations.
 *
 * Les invitations demandent le rôle `admin` : un préparateur qui ouvre la page doit y voir son
 * équipe, pas un message d'erreur sur une liste qui ne le concerne pas.
 */
async function chargerEquipe(organisation: number): Promise<void> {
  membres.value = await api.listMembers(organisation)
  try {
    invitations.value = await api.listInvitations(organisation)
  } catch {
    invitations.value = []
  }
}

onMounted(async () => {
  await refresh()
  const propose = route.query.invitation
  if (typeof propose === 'string') jetonSaisi.value = propose
})

const SIRET_VALIDE = /^[0-9]{14}$/
const TVA_VALIDE = /^[A-Za-z]{2}[0-9A-Za-z]{2,13}$/

async function enregistrerIdentite(): Promise<void> {
  erreurSiret.value = null
  erreurTva.value = null
  erreurCapital.value = null
  const cible = entreprise.value
  if (!cible) return

  const saisie = identite.value
  if (saisie.siret.trim() !== '' && !SIRET_VALIDE.test(saisie.siret.trim())) {
    erreurSiret.value = 'Le SIRET compte exactement 14 chiffres, sans espace.'
    return
  }
  if (saisie.vat_number.trim() !== '' && !TVA_VALIDE.test(saisie.vat_number.trim())) {
    erreurTva.value = 'Numéro de TVA attendu sous la forme FR12345678901.'
    return
  }
  let capital: number | null = null
  if (saisie.capital.trim() !== '') {
    capital = centsFromInput(saisie.capital)
    if (capital === null || capital < 0) {
      erreurCapital.value = 'Capital illisible. Attendu : un montant en euros, par exemple 7500.'
      return
    }
  }

  busy.value = true
  error.value = null
  try {
    // Les champs vides partent à `null` et non en chaîne vide : c'est ainsi qu'une mention se
    // retire du document, et le serveur refuse une chaîne vide sur les champs à motif.
    const videEstNul = (valeur: string): string | null => (valeur.trim() === '' ? null : valeur.trim())
    const misAJour = await api.updateOrganization(cible.id, {
      name: saisie.name.trim(),
      siret: videEstNul(saisie.siret),
      legal_form: videEstNul(saisie.legal_form),
      share_capital_cents: capital,
      rcs: videEstNul(saisie.rcs),
      vat_number: videEstNul(saisie.vat_number),
      address_line1: videEstNul(saisie.address_line1),
      address_line2: videEstNul(saisie.address_line2),
      postal_code: videEstNul(saisie.postal_code),
      city: videEstNul(saisie.city),
      country: videEstNul(saisie.country),
      phone: videEstNul(saisie.phone),
      billing_email: videEstNul(saisie.billing_email),
      decennial_insurer: videEstNul(saisie.decennial_insurer),
      decennial_policy_number: videEstNul(saisie.decennial_policy_number),
      decennial_coverage_area: videEstNul(saisie.decennial_coverage_area),
    })
    entreprise.value = misAJour
    remplirIdentite(misAJour)
    notice.value = "Identité de l'entreprise enregistrée."
  } catch (caught) {
    error.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}

async function changerRole(membre: Member, role: OrganizationRole): Promise<void> {
  const cible = entreprise.value
  if (!cible) return
  busy.value = true
  error.value = null
  try {
    await api.updateMemberRole(cible.id, membre.user_id, role)
    await chargerEquipe(cible.id)
    notice.value = `${membre.email} est désormais ${role}.`
  } catch (caught) {
    // Le serveur refuse de retirer le dernier propriétaire, et de déléguer plus haut que soi. Son
    // message est plus précis que tout ce qu'on pourrait deviner ici : on le montre tel quel, et
    // on relit l'équipe pour que le sélecteur revienne à la valeur qui fait foi.
    error.value = messageOf(caught)
    await chargerEquipe(cible.id)
  } finally {
    busy.value = false
  }
}

async function retirerMembre(membre: Member): Promise<void> {
  const cible = entreprise.value
  if (!cible) return
  if (!window.confirm(`Retirer ${membre.email} de l'entreprise ?`)) return
  busy.value = true
  error.value = null
  try {
    await api.removeMember(cible.id, membre.user_id)
    await chargerEquipe(cible.id)
    notice.value = `${membre.email} ne fait plus partie de l'entreprise.`
  } catch (caught) {
    error.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}

async function inviter(): Promise<void> {
  const cible = entreprise.value
  if (!cible || invitationEmail.value.trim() === '') return
  busy.value = true
  error.value = null
  jetonEmis.value = null
  try {
    jetonEmis.value = await api.inviteMember(
      cible.id,
      invitationEmail.value.trim(),
      invitationRole.value,
      invitationJours.value,
    )
    invitationEmail.value = ''
    await chargerEquipe(cible.id)
  } catch (caught) {
    error.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}

async function accepter(): Promise<void> {
  if (jetonSaisi.value.trim() === '') return
  busy.value = true
  error.value = null
  try {
    const membre = await api.acceptInvitation(jetonSaisi.value.trim())
    jetonSaisi.value = ''
    notice.value = `Invitation acceptée : vous êtes ${membre.role} de cette entreprise.`
    await refresh()
  } catch (caught) {
    error.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}

/** Le lien à transmettre à l'invité : il atterrit sur cette page, jeton prérempli. */
function lienDInvitation(jeton: string): string {
  return `${window.location.origin}/entreprise?invitation=${encodeURIComponent(jeton)}`
}

function dateFr(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Intl.DateTimeFormat('fr-FR', { dateStyle: 'long' }).format(new Date(iso))
}
</script>

<template>
  <section class="entreprise">
    <h1>Mon entreprise</h1>

    <p class="raccourcis">
      <RouterLink to="/projets">
        Mes chantiers
      </RouterLink>
      <RouterLink to="/devis">
        Mes devis
      </RouterLink>
      <RouterLink to="/bareme">
        Mon barème
      </RouterLink>
      <RouterLink to="/abonnement">
        Abonnement
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
      Chargement de votre entreprise…
    </p>

    <template v-else-if="entreprise">
      <div
        v-if="mentionsManquantes.length > 0"
        class="manquantes"
        role="alert"
      >
        <h2>{{ mentionsManquantes.length }} mention(s) obligatoire(s) manquante(s)</h2>
        <p>
          Un devis ou une facture émis en l'état ne portera pas ces mentions, et ne sera
          <strong>pas valable</strong>. Complétez-les avant d'émettre un document.
        </p>
        <ul>
          <li
            v-for="mention in mentionsManquantes"
            :key="mention"
          >
            {{ mention }}
          </li>
        </ul>
      </div>

      <h2>Identité légale</h2>
      <form @submit.prevent="enregistrerIdentite">
        <div class="grille">
          <div class="champ">
            <label for="ent-nom">Raison sociale</label>
            <input
              id="ent-nom"
              v-model="identite.name"
              type="text"
              maxlength="200"
              required
            >
          </div>
          <div class="champ">
            <label for="ent-forme">Forme juridique</label>
            <input
              id="ent-forme"
              v-model="identite.legal_form"
              type="text"
              maxlength="50"
              aria-describedby="aide-forme"
            >
            <span
              id="aide-forme"
              class="aide"
            >SARL, SAS, EI, EURL…</span>
          </div>
          <div class="champ">
            <label for="ent-siret">SIRET</label>
            <input
              id="ent-siret"
              v-model="identite.siret"
              type="text"
              inputmode="numeric"
              maxlength="14"
              :aria-invalid="erreurSiret !== null"
              aria-describedby="erreur-siret"
            >
            <span
              id="erreur-siret"
              :class="erreurSiret ? 'erreur' : 'aide'"
              role="alert"
            >{{ erreurSiret ?? '14 chiffres, sans espace.' }}</span>
          </div>
          <div class="champ">
            <label for="ent-capital">Capital social (€)</label>
            <input
              id="ent-capital"
              v-model="identite.capital"
              type="text"
              inputmode="decimal"
              :aria-invalid="erreurCapital !== null"
              aria-describedby="erreur-capital"
            >
            <span
              id="erreur-capital"
              :class="erreurCapital ? 'erreur' : 'aide'"
              role="alert"
            >{{
              erreurCapital ??
                (entreprise.share_capital_cents
                  ? `Actuellement ${formatCents(entreprise.share_capital_cents)}.`
                  : 'Laisser vide pour une entreprise individuelle.')
            }}</span>
          </div>
          <div class="champ">
            <label for="ent-rcs">RCS</label>
            <input
              id="ent-rcs"
              v-model="identite.rcs"
              type="text"
              maxlength="100"
            >
          </div>
          <div class="champ">
            <label for="ent-tva">Numéro de TVA intracommunautaire</label>
            <input
              id="ent-tva"
              v-model="identite.vat_number"
              type="text"
              maxlength="15"
              :aria-invalid="erreurTva !== null"
              aria-describedby="erreur-tva"
            >
            <span
              id="erreur-tva"
              :class="erreurTva ? 'erreur' : 'aide'"
              role="alert"
            >{{ erreurTva ?? 'Par exemple FR12345678901.' }}</span>
          </div>
        </div>

        <h3>Coordonnées</h3>
        <div class="grille">
          <div class="champ">
            <label for="ent-adresse1">Adresse</label>
            <input
              id="ent-adresse1"
              v-model="identite.address_line1"
              type="text"
              maxlength="200"
            >
          </div>
          <div class="champ">
            <label for="ent-adresse2">Complément d'adresse</label>
            <input
              id="ent-adresse2"
              v-model="identite.address_line2"
              type="text"
              maxlength="200"
            >
          </div>
          <div class="champ">
            <label for="ent-cp">Code postal</label>
            <input
              id="ent-cp"
              v-model="identite.postal_code"
              type="text"
              maxlength="20"
            >
          </div>
          <div class="champ">
            <label for="ent-ville">Ville</label>
            <input
              id="ent-ville"
              v-model="identite.city"
              type="text"
              maxlength="100"
            >
          </div>
          <div class="champ">
            <label for="ent-pays">Pays</label>
            <input
              id="ent-pays"
              v-model="identite.country"
              type="text"
              maxlength="100"
            >
          </div>
          <div class="champ">
            <label for="ent-telephone">Téléphone</label>
            <input
              id="ent-telephone"
              v-model="identite.phone"
              type="tel"
              maxlength="30"
            >
          </div>
          <div class="champ">
            <label for="ent-facturation">Adresse e-mail de facturation</label>
            <input
              id="ent-facturation"
              v-model="identite.billing_email"
              type="email"
              maxlength="200"
            >
          </div>
        </div>

        <h3>Assurance décennale</h3>
        <p class="aide">
          Obligatoire sur tous vos devis et factures de travaux de bâtiment (article L. 241-1 du
          code des assurances).
        </p>
        <div class="grille">
          <div class="champ">
            <label for="ent-assureur">Assureur</label>
            <input
              id="ent-assureur"
              v-model="identite.decennial_insurer"
              type="text"
              maxlength="200"
            >
          </div>
          <div class="champ">
            <label for="ent-police">Numéro de police</label>
            <input
              id="ent-police"
              v-model="identite.decennial_policy_number"
              type="text"
              maxlength="100"
            >
          </div>
          <div class="champ">
            <label for="ent-couverture">Couverture géographique</label>
            <input
              id="ent-couverture"
              v-model="identite.decennial_coverage_area"
              type="text"
              maxlength="200"
            >
          </div>
        </div>

        <p>
          <button
            type="submit"
            data-variant="primary"
            :disabled="busy"
          >
            Enregistrer l'identité
          </button>
        </p>
      </form>

      <h2>Équipe</h2>
      <div class="tableau">
        <table>
          <caption>
            {{ membres.length }} membre(s). Le dernier propriétaire ne peut être ni retiré ni
            rétrogradé : une entreprise sans propriétaire n'a plus personne pour payer ni fermer
            le compte.
          </caption>
          <thead>
            <tr>
              <th scope="col">
                Adresse e-mail
              </th>
              <th scope="col">
                Rôle
              </th>
              <th scope="col">
                Membre depuis
              </th>
              <th scope="col">
                Action
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="membre in membres"
              :key="membre.user_id"
            >
              <th scope="row">
                {{ membre.email }}
              </th>
              <td>
                <label
                  class="cache"
                  :for="`role-${membre.user_id}`"
                >Rôle de {{ membre.email }}</label>
                <select
                  :id="`role-${membre.user_id}`"
                  :value="membre.role"
                  :disabled="busy"
                  @change="
                    changerRole(membre, ($event.target as HTMLSelectElement).value as OrganizationRole)
                  "
                >
                  <option
                    v-for="role in ROLES"
                    :key="role.valeur"
                    :value="role.valeur"
                  >
                    {{ role.libelle }}
                  </option>
                </select>
              </td>
              <td>{{ dateFr(membre.accepted_at) }}</td>
              <td>
                <button
                  type="button"
                  :disabled="busy"
                  @click="retirerMembre(membre)"
                >
                  Retirer {{ membre.email }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <h2>Inviter un collègue</h2>
      <form
        class="formulaire"
        @submit.prevent="inviter"
      >
        <div class="champ">
          <label for="invitation-email">Adresse e-mail</label>
          <input
            id="invitation-email"
            v-model="invitationEmail"
            type="email"
            maxlength="200"
            required
          >
        </div>
        <div class="champ">
          <label for="invitation-role">Rôle</label>
          <select
            id="invitation-role"
            v-model="invitationRole"
          >
            <option
              v-for="role in ROLES"
              :key="role.valeur"
              :value="role.valeur"
            >
              {{ role.libelle }}
            </option>
          </select>
        </div>
        <div class="champ court">
          <label for="invitation-jours">Valable (jours)</label>
          <input
            id="invitation-jours"
            v-model.number="invitationJours"
            type="number"
            min="1"
            max="30"
          >
        </div>
        <button
          type="submit"
          data-variant="primary"
          :disabled="busy"
        >
          Inviter
        </button>
      </form>

      <div
        v-if="jetonEmis"
        class="jeton"
        role="status"
      >
        <h3>Invitation de {{ jetonEmis.email }}</h3>
        <p>
          <strong>Copiez ce lien maintenant.</strong> Il n'est affiché qu'une fois : nous n'en
          gardons qu'une empreinte, et personne — pas même nous — ne peut le relire. Une invitation
          perdue se réémet.
        </p>
        <p class="lien-invitation">
          {{ lienDInvitation(jetonEmis.token) }}
        </p>
        <p class="aide">
          Valable jusqu'au {{ dateFr(jetonEmis.expires_at) }}. Seule l'adresse
          {{ jetonEmis.email }} pourra s'en servir.
        </p>
      </div>

      <div
        v-if="invitations.length > 0"
        class="tableau"
      >
        <table>
          <caption>{{ invitations.length }} invitation(s) émise(s)</caption>
          <thead>
            <tr>
              <th scope="col">
                Adresse e-mail
              </th>
              <th scope="col">
                Rôle proposé
              </th>
              <th scope="col">
                Expire le
              </th>
              <th scope="col">
                Acceptée le
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="invitation in invitations"
              :key="invitation.id"
            >
              <th scope="row">
                {{ invitation.email }}
              </th>
              <td>{{ invitation.role }}</td>
              <td>{{ dateFr(invitation.expires_at) }}</td>
              <td>{{ invitation.accepted_at ? dateFr(invitation.accepted_at) : 'en attente' }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <h2>Rejoindre une entreprise</h2>
      <form
        class="formulaire"
        @submit.prevent="accepter"
      >
        <div class="champ large">
          <label for="jeton-invitation">Jeton d'invitation reçu</label>
          <input
            id="jeton-invitation"
            v-model="jetonSaisi"
            type="text"
            maxlength="128"
            aria-describedby="aide-jeton"
          >
          <span
            id="aide-jeton"
            class="aide"
          >
            Il doit avoir été émis pour l'adresse e-mail de ce compte : un lien transféré n'ouvre
            rien.
          </span>
        </div>
        <button
          type="submit"
          :disabled="busy || jetonSaisi.trim() === ''"
        >
          Rejoindre
        </button>
      </form>
    </template>
  </section>
</template>

<style scoped>
.entreprise {
  max-width: 72rem;
}

.raccourcis {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
  margin: 0.25rem 0 1rem;
  font-size: 0.9rem;
}

/* Bordure épaisse ET titre chiffré : la couleur seule ne dit rien à un daltonien, et « il manque
   des mentions » sans savoir lesquelles ne fait pas avancer. */
.manquantes {
  border: 1px solid var(--bordure);
  border-left: 4px solid var(--erreur);
  border-radius: 0.35rem;
  padding: 0.75rem 1rem;
  margin-bottom: 1.5rem;
}

.manquantes h2 {
  margin-top: 0;
  font-size: 1.05rem;
  color: var(--erreur);
}

.grille {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr));
  gap: 0.9rem;
  margin-bottom: 1.25rem;
}

.formulaire {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: 0.9rem;
  margin-bottom: 1.25rem;
}

.champ {
  flex: 1;
  min-width: 13rem;
}

.champ.court {
  max-width: 8rem;
  flex: 0 0 auto;
}

.champ.large {
  min-width: 22rem;
}

.tableau {
  overflow-x: auto;
  margin-bottom: 1.5rem;
}

table {
  border-collapse: collapse;
  width: 100%;
  min-width: 36rem;
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

.jeton {
  border: 1px solid var(--bordure);
  border-left: 4px solid var(--accent);
  border-radius: 0.35rem;
  padding: 0.75rem 1rem;
  margin-bottom: 1.5rem;
}

.jeton h3 {
  margin-top: 0;
}

/* Le lien doit pouvoir être sélectionné d'un trait : il n'est affiché qu'une fois. */
.lien-invitation {
  font-family: ui-monospace, 'SFMono-Regular', 'Consolas', monospace;
  word-break: break-all;
  user-select: all;
  background: #f2f4f7;
  padding: 0.5rem 0.6rem;
  border-radius: 0.3rem;
}

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
