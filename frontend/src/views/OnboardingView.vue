<script setup lang="ts">
/**
 * Accueil d'un espace vide.
 *
 * L'état vide était la chaîne « Aucun projet pour le moment » devant un canevas blanc. Depuis là,
 * atteindre le premier geste qui a de la valeur — un métré, un devis — suppose de deviner qu'il
 * faut créer un projet, puis une pièce, puis saisir un polygone. Personne ne le fait, et toute la
 * mécanique de conversion construite en aval reste inerte.
 *
 * Deux chemins, et un seul clic chacun :
 *
 * 1. **le chantier de démonstration** — une salle de bain habillée et chiffrable, construite par
 *    le serveur (`app/services/demo.py`). C'est le produit fini, tout de suite ;
 * 2. **les gabarits de pièces** — un projet neuf contenant déjà une pièce aux dimensions
 *    courantes du métier. Un artisan arrive avec un relevé, pas avec l'envie de dessiner un
 *    rectangle à la souris.
 *
 * Les gabarits sont posés avec les routes existantes (`POST /api/projects` puis
 * `POST /api/projects/{id}/rooms`) : ce sont des dimensions de départ, pas une notion nouvelle du
 * modèle, et les inscrire côté serveur aurait créé un objet de plus à maintenir.
 */
import { ref } from 'vue'
import { useRouter } from 'vue-router'

import * as api from '@/api/client'

const router = useRouter()

const emit = defineEmits<{ (event: 'cree'): void }>()

/**
 * Gabarits de pièces, en centimètres, sens trigonométrique.
 *
 * Les cotes sont des ordres de grandeur de rénovation en logement, choisies pour être plausibles
 * et immédiatement modifiables — pas des standards réglementaires. Le séjour en L est là pour une
 * raison précise : c'est la forme que personne ne saisit spontanément, et l'éditeur la gère.
 */
interface GabaritDePiece {
  cle: string
  nom: string
  description: string
  polygon: number[][]
  hauteurSousPlafondCm: number
}

const GABARITS: GabaritDePiece[] = [
  {
    cle: 'salle-de-bain',
    nom: 'Salle de bain',
    description: '2,40 m sur 2,00 m — la pièce la plus dense en faïence et en éléments.',
    polygon: [[0, 0], [240, 0], [240, 200], [0, 200]],
    hauteurSousPlafondCm: 250,
  },
  {
    cle: 'cuisine',
    nom: 'Cuisine',
    description: '3,00 m sur 3,50 m — implantation en L ou en U possible sur trois murs.',
    polygon: [[0, 0], [300, 0], [300, 350], [0, 350]],
    hauteurSousPlafondCm: 250,
  },
  {
    cle: 'chambre',
    nom: 'Chambre',
    description: '3,50 m sur 4,00 m — un lit double et un dégagement de circulation.',
    polygon: [[0, 0], [350, 0], [350, 400], [0, 400]],
    hauteurSousPlafondCm: 250,
  },
  {
    cle: 'sejour-en-l',
    nom: 'Séjour en L',
    description: '5,00 m sur 4,50 m avec un retour — pour un plan qui n’est pas rectangulaire.',
    polygon: [[0, 0], [500, 0], [500, 250], [250, 250], [250, 450], [0, 450]],
    hauteurSousPlafondCm: 250,
  },
  {
    cle: 'couloir',
    nom: 'Couloir',
    description: '1,00 m sur 4,00 m — le passage de 90 cm s’y vérifie tout de suite.',
    polygon: [[0, 0], [100, 0], [100, 400], [0, 400]],
    hauteurSousPlafondCm: 250,
  },
]

const busy = ref<string | null>(null)
const error = ref<string | null>(null)

function messageOf(caught: unknown): string {
  return caught instanceof Error ? caught.message : String(caught)
}

async function ouvrirLaDemonstration(): Promise<void> {
  busy.value = 'demonstration'
  error.value = null
  try {
    const cree = await api.createDemoProject()
    await router.push(`/projets/${cree.project_id}/plan`)
  } catch (caught) {
    // Un 409 signifie qu'un chantier existe déjà — deux onglets, ou une démonstration déjà posée.
    // La liste est alors la bonne destination, et le message serait inutilement inquiétant.
    if (caught instanceof api.ApiError && caught.status === 409) {
      emit('cree')
      await router.push('/projets')
      return
    }
    error.value = messageOf(caught)
  } finally {
    busy.value = null
  }
}

async function partirDunGabarit(gabarit: GabaritDePiece): Promise<void> {
  busy.value = gabarit.cle
  error.value = null
  try {
    const projet = await api.createProject(`Chantier — ${gabarit.nom.toLowerCase()}`)
    await api.createRoom(projet.id, {
      name: gabarit.nom,
      polygon: gabarit.polygon,
      ceiling_height_cm: gabarit.hauteurSousPlafondCm,
    })
    await router.push(`/projets/${projet.id}/plan`)
  } catch (caught) {
    error.value = messageOf(caught)
  } finally {
    busy.value = null
  }
}
</script>

<template>
  <!-- Titres en `h2` et non en `h1` : ce composant est l'état vide de `ProjectsView`, dont le
       `h1` est déjà « Mes projets ». Deux `h1` sur une page cassent la navigation par titres, qui
       est la façon dont un lecteur d'écran parcourt un écran inconnu. -->
  <section class="accueil">
    <h2>Par où commencer</h2>
    <p class="chapeau">
      Votre espace est vide. Deux façons d'en sortir en un clic : ouvrir un chantier de
      démonstration déjà chiffré, ou partir d'une pièce aux cotes courantes que vous ajusterez à
      votre relevé.
    </p>

    <p
      v-if="error"
      class="erreur"
      role="alert"
    >
      {{ error }}
    </p>

    <section
      class="demonstration"
      aria-labelledby="titre-demonstration"
    >
      <h3 id="titre-demonstration">
        Voir le produit fini
      </h3>
      <p>
        Une salle de bain de 2,40 m sur 2,00 m : murs habillés de faïence, sol carrelé, porte,
        fenêtre, meuble sous-vasque, WC et bac de douche. Le métré et le devis se calculent
        immédiatement dessus. Vous pourrez la supprimer, elle ne reviendra pas.
      </p>
      <button
        type="button"
        data-variant="primary"
        :disabled="busy !== null"
        @click="ouvrirLaDemonstration"
      >
        {{ busy === 'demonstration' ? 'Création…' : 'Ouvrir le chantier de démonstration' }}
      </button>
    </section>

    <section aria-labelledby="titre-gabarits">
      <h3 id="titre-gabarits">
        Partir d'un gabarit de pièce
      </h3>
      <ul class="gabarits">
        <li
          v-for="gabarit in GABARITS"
          :key="gabarit.cle"
        >
          <h4>{{ gabarit.nom }}</h4>
          <p>{{ gabarit.description }}</p>
          <button
            type="button"
            :disabled="busy !== null"
            @click="partirDunGabarit(gabarit)"
          >
            {{ busy === gabarit.cle ? 'Création…' : `Créer un chantier ${gabarit.nom.toLowerCase()}` }}
          </button>
        </li>
      </ul>
    </section>

    <p class="aide">
      Vous préférez repartir de zéro ? Le formulaire « Nom du nouveau projet », en haut de cette
      page, crée un chantier vide.
    </p>
  </section>
</template>

<style scoped>
.accueil {
  max-width: 52rem;
  margin: 0 auto 3rem;
}

.chapeau {
  color: var(--texte-doux);
  font-size: 1.05rem;
}

.accueil > section {
  margin-top: 2rem;
}

.demonstration {
  padding: 1.25rem;
  border: 2px solid var(--accent);
  border-radius: 0.5rem;
}

.demonstration h3 {
  margin-top: 0;
}

.gabarits {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(15rem, 1fr));
  gap: 1rem;
  padding: 0;
  margin: 0;
  list-style: none;
}

.gabarits li {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  padding: 1rem;
  border: 1px solid var(--bordure);
  border-radius: 0.5rem;
}

.gabarits h4 {
  margin: 0;
  font-size: 1.05rem;
}

.gabarits p {
  flex: 1;
  margin: 0;
  color: var(--texte-doux);
  font-size: 0.9rem;
}

.aide {
  margin-top: 2rem;
  color: var(--texte-doux);
}
</style>
