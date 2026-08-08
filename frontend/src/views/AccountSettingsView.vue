<script setup lang="ts">
/**
 * Mon compte : mot de passe, portabilité RGPD, fermeture.
 *
 * Trois gestes que le produit ne savait pas faire, et dont l'absence bloquait la vente : changer
 * son mot de passe, récupérer ses données, partir. Le troisième n'est pas une formalité — sans
 * lui, le droit à l'effacement (RGPD art. 17) suppose d'écrire à un humain.
 *
 * Les trois sont regroupés sur un seul écran et **séparés visuellement**, la fermeture étant
 * isolée en bas dans un encadré de danger. Un bouton destructeur au milieu d'un formulaire de mot
 * de passe se clique par erreur.
 */
import { computed, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import * as api from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()

const LONGUEUR_MINIMALE = 12

function messageOf(caught: unknown): string {
  return caught instanceof Error ? caught.message : String(caught)
}

// --- Mot de passe ------------------------------------------------------------------------------

const actuel = ref('')
const nouveau = ref('')
const confirmation = ref('')
const motDePassePending = ref(false)
const motDePasseMessage = ref<string | null>(null)
const motDePasseErreur = ref<string | null>(null)

const discordant = computed(
  () => confirmation.value.length > 0 && confirmation.value !== nouveau.value,
)

async function changerLeMotDePasse(): Promise<void> {
  motDePasseErreur.value = null
  motDePasseMessage.value = null
  if (nouveau.value !== confirmation.value) {
    motDePasseErreur.value = 'Les deux mots de passe saisis sont différents.'
    return
  }

  motDePassePending.value = true
  try {
    await api.changePassword(actuel.value, nouveau.value)
    actuel.value = ''
    nouveau.value = ''
    confirmation.value = ''
    motDePasseMessage.value =
      'Mot de passe modifié. Toutes vos autres sessions ont été fermées.'
  } catch (caught) {
    motDePasseErreur.value = messageOf(caught)
  } finally {
    motDePassePending.value = false
  }
}

// --- Portabilité -------------------------------------------------------------------------------

const exportPending = ref(false)
const exportErreur = ref<string | null>(null)
const exportJson = ref<string | null>(null)

/**
 * Récupère l'export et le propose au téléchargement.
 *
 * Le contenu est aussi gardé en mémoire : dans un contexte où `URL.createObjectURL` n'existe pas
 * — un navigateur ancien, un environnement de test — l'utilisateur doit tout de même pouvoir
 * copier ses données. Un droit d'accès qui dépend d'une API de navigateur n'en est pas un.
 */
async function exporterMesDonnees(): Promise<void> {
  exportPending.value = true
  exportErreur.value = null
  try {
    const donnees = await api.exportAccount()
    const texte = JSON.stringify(donnees, null, 2)
    exportJson.value = texte
    telecharger(texte)
  } catch (caught) {
    exportErreur.value = messageOf(caught)
  } finally {
    exportPending.value = false
  }
}

function telecharger(texte: string): void {
  if (typeof URL.createObjectURL !== 'function') return
  const lien = document.createElement('a')
  const url = URL.createObjectURL(new Blob([texte], { type: 'application/json' }))
  lien.href = url
  lien.download = 'mes-donnees.json'
  lien.click()
  URL.revokeObjectURL(url)
}

// --- Fermeture du compte -----------------------------------------------------------------------

const CONFIRMATION_ATTENDUE = 'SUPPRIMER'

const motDePasseSuppression = ref('')
const confirmationSuppression = ref('')
const suppressionPending = ref(false)
const suppressionErreur = ref<string | null>(null)

/**
 * Le mot magique à recopier, en plus du mot de passe.
 *
 * Le mot de passe prouve l'identité, il ne prouve pas l'intention : il est enregistré dans le
 * gestionnaire du navigateur et se saisit sans y penser. Recopier un mot fait relire la phrase.
 */
const suppressionArmee = computed(
  () =>
    confirmationSuppression.value.trim().toUpperCase() === CONFIRMATION_ATTENDUE
    && motDePasseSuppression.value.length > 0,
)

async function fermerLeCompte(): Promise<void> {
  suppressionErreur.value = null
  suppressionPending.value = true
  try {
    await api.deleteAccount(motDePasseSuppression.value)
    auth.signOut()
    await router.push({ name: 'connexion' })
  } catch (caught) {
    suppressionErreur.value = messageOf(caught)
  } finally {
    suppressionPending.value = false
  }
}
</script>

<template>
  <section class="compte">
    <h1>Mon compte</h1>
    <p
      v-if="auth.user"
      class="identite"
    >
      Connecté en tant que <strong>{{ auth.user.email }}</strong>.
    </p>

    <section aria-labelledby="titre-motdepasse">
      <h2 id="titre-motdepasse">
        Mot de passe
      </h2>
      <form @submit.prevent="changerLeMotDePasse">
        <div class="champ">
          <label for="actuel">Mot de passe actuel</label>
          <input
            id="actuel"
            v-model="actuel"
            type="password"
            autocomplete="current-password"
            required
          >
        </div>
        <div class="champ">
          <label for="nouveau-motdepasse">Nouveau mot de passe</label>
          <input
            id="nouveau-motdepasse"
            v-model="nouveau"
            type="password"
            autocomplete="new-password"
            :minlength="LONGUEUR_MINIMALE"
            required
            aria-describedby="aide-longueur"
          >
          <p
            id="aide-longueur"
            class="aide"
          >
            {{ LONGUEUR_MINIMALE }} caractères minimum.
          </p>
        </div>
        <div class="champ">
          <label for="confirmation-motdepasse">Confirmez le nouveau mot de passe</label>
          <input
            id="confirmation-motdepasse"
            v-model="confirmation"
            type="password"
            autocomplete="new-password"
            :minlength="LONGUEUR_MINIMALE"
            required
            :aria-invalid="discordant ? 'true' : undefined"
          >
          <p
            v-if="discordant"
            class="erreur"
            role="status"
          >
            Les deux saisies diffèrent.
          </p>
        </div>

        <p
          v-if="motDePasseMessage"
          class="message"
          role="status"
        >
          {{ motDePasseMessage }}
        </p>
        <p
          v-if="motDePasseErreur"
          class="erreur"
          role="alert"
        >
          {{ motDePasseErreur }}
        </p>

        <button
          type="submit"
          data-variant="primary"
          :disabled="motDePassePending || discordant"
        >
          {{ motDePassePending ? 'Enregistrement…' : 'Changer le mot de passe' }}
        </button>
      </form>
    </section>

    <section aria-labelledby="titre-donnees">
      <h2 id="titre-donnees">
        Mes données
      </h2>
      <p>
        Vous pouvez récupérer à tout moment l'intégralité des données de votre compte et des
        entreprises dont vous êtes membre, au format JSON (RGPD, articles 15 et 20). L'export ne
        dépend d'aucune offre.
      </p>
      <button
        type="button"
        :disabled="exportPending"
        @click="exporterMesDonnees"
      >
        {{ exportPending ? 'Préparation…' : 'Exporter mes données (JSON)' }}
      </button>
      <p
        v-if="exportErreur"
        class="erreur"
        role="alert"
      >
        {{ exportErreur }}
      </p>
      <details v-if="exportJson">
        <summary>Afficher l'export</summary>
        <pre>{{ exportJson }}</pre>
      </details>
      <p class="aide">
        Voir aussi la
        <RouterLink to="/legal/confidentialite">
          politique de confidentialité
        </RouterLink>
        : durées de conservation et registre des sous-traitants.
      </p>
      <!-- Cette page est personnelle ; le palier, la consommation et l'essai appartiennent à
           l'entreprise et vivent sur `/abonnement`. -->
      <p class="aide">
        L'abonnement de votre entreprise se gère sur
        <RouterLink to="/abonnement">
          la page abonnement
        </RouterLink>.
      </p>
    </section>

    <section
      class="danger"
      aria-labelledby="titre-fermeture"
    >
      <h2 id="titre-fermeture">
        Fermer mon compte
      </h2>
      <p>
        La fermeture supprime définitivement votre compte et les chantiers que vous avez créés.
        Cette action est irréversible : pensez à exporter vos données d'abord.
      </p>
      <p>
        Si vous êtes le dernier propriétaire d'une entreprise comptant d'autres membres, la
        fermeture est refusée tant que vous n'avez pas nommé un autre propriétaire — sinon les
        chantiers de vos collègues partiraient avec vous.
      </p>

      <form @submit.prevent="fermerLeCompte">
        <div class="champ">
          <label for="motdepasse-suppression">Votre mot de passe</label>
          <input
            id="motdepasse-suppression"
            v-model="motDePasseSuppression"
            type="password"
            autocomplete="current-password"
            required
          >
        </div>
        <div class="champ">
          <label for="confirmation-suppression">
            Recopiez « {{ CONFIRMATION_ATTENDUE }} » pour confirmer
          </label>
          <input
            id="confirmation-suppression"
            v-model="confirmationSuppression"
            type="text"
            autocomplete="off"
            required
          >
        </div>

        <p
          v-if="suppressionErreur"
          class="erreur"
          role="alert"
        >
          {{ suppressionErreur }}
        </p>

        <button
          type="submit"
          :disabled="!suppressionArmee || suppressionPending"
        >
          {{ suppressionPending ? 'Suppression…' : 'Fermer définitivement mon compte' }}
        </button>
      </form>
    </section>
  </section>
</template>

<style scoped>
.compte {
  max-width: 40rem;
  margin: 0 auto 3rem;
}

.identite {
  color: var(--texte-doux);
}

.compte > section {
  margin-top: 2.5rem;
  padding-top: 1.25rem;
  border-top: 1px solid var(--bordure);
}

.champ {
  margin-bottom: 1rem;
}

.aide {
  margin: 0.25rem 0 0;
  color: var(--texte-doux);
  font-size: 0.9rem;
}

.message {
  margin: 1rem 0;
  padding: 0.6rem 0.85rem;
  border-radius: 0.35rem;
  background: #eaf2ff;
  color: #0a3690;
  font-weight: 600;
}

/* La zone destructrice est encadrée et décalée : elle ne doit pas se confondre avec le reste du
   formulaire, où toutes les actions sont réversibles. */
.danger {
  margin-top: 3rem;
  padding: 1.25rem;
  border: 2px solid var(--erreur);
  border-radius: 0.5rem;
}

.danger h2 {
  color: var(--erreur);
}

pre {
  max-height: 20rem;
  overflow: auto;
  padding: 0.75rem;
  border: 1px solid var(--bordure);
  border-radius: 0.35rem;
  font-size: 0.85rem;
}
</style>
