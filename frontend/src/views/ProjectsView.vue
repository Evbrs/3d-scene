<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import * as api from '@/api/client'
import type { ProjectSummary } from '@/api/types'

const projects = ref<ProjectSummary[]>([])
const total = ref(0)
const newName = ref('')
const error = ref<string | null>(null)
const busy = ref(false)

const loading = ref(true)

function messageOf(caught: unknown): string {
  return caught instanceof Error ? caught.message : String(caught)
}

async function refresh(): Promise<void> {
  error.value = null
  try {
    const page = await api.listProjects(50)
    projects.value = page.items
    total.value = page.total
  } catch (caught) {
    error.value = messageOf(caught)
  } finally {
    loading.value = false
  }
}

async function create(): Promise<void> {
  if (!newName.value.trim()) return
  busy.value = true
  error.value = null
  try {
    await api.createProject(newName.value.trim())
    newName.value = ''
    await refresh()
  } catch (caught) {
    error.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}

async function remove(project: ProjectSummary): Promise<void> {
  if (!window.confirm(`Supprimer « ${project.name} » et tout son plan ?`)) return
  error.value = null
  try {
    // Une suppression refusée (droits, projet déjà supprimé ailleurs) partait en promesse
    // rejetée : la ligne restait affichée et rien n'expliquait pourquoi.
    await api.deleteProject(project.id)
  } catch (caught) {
    error.value = messageOf(caught)
  }
  await refresh()
}

onMounted(refresh)
</script>

<template>
  <section>
    <h1>Mes projets</h1>

    <form
      class="creation"
      @submit.prevent="create"
    >
      <div class="champ">
        <label for="nouveau-projet">Nom du nouveau projet</label>
        <input
          id="nouveau-projet"
          v-model="newName"
          type="text"
          maxlength="200"
          required
        >
      </div>
      <button
        type="submit"
        data-variant="primary"
        :disabled="busy"
      >
        Créer
      </button>
    </form>

    <p
      v-if="error"
      class="erreur"
      role="alert"
    >
      {{ error }}
    </p>

    <p v-if="loading">
      Chargement des projets…
    </p>
    <p v-else-if="projects.length === 0">
      Aucun projet pour le moment.
    </p>

    <div
      v-else
      class="tableau"
    >
      <table>
        <caption>{{ total }} projet(s)</caption>
        <thead>
          <tr>
            <th scope="col">
              Nom
            </th>
            <th scope="col">
              Version
            </th>
            <th scope="col">
              Modifié le
            </th>
            <th scope="col">
              Actions
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="project in projects"
            :key="project.id"
          >
            <th scope="row">
              {{ project.name }}
            </th>
            <td>{{ project.version }}</td>
            <td>{{ new Date(project.updated_at).toLocaleString('fr-FR') }}</td>
            <td class="actions">
              <RouterLink :to="`/projets/${project.id}/plan`">
                Plan 2D
              </RouterLink>
              <RouterLink :to="`/projets/${project.id}/vue-3d`">
                Vue 3D
              </RouterLink>
              <button
                type="button"
                @click="remove(project)"
              >
                Supprimer
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.creation {
  display: flex;
  align-items: flex-end;
  flex-wrap: wrap;
  gap: 0.75rem;
  margin: 1rem 0 1.5rem;
}

.champ {
  flex: 1;
  min-width: 14rem;
  max-width: 28rem;
}

/* Le tableau garde ses colonnes : il défile dans son propre conteneur plutôt que de pousser
   toute la page vers la droite sur un téléphone. */
.tableau {
  overflow-x: auto;
}

table {
  border-collapse: collapse;
  width: 100%;
  min-width: 34rem;
}

caption {
  text-align: left;
  color: var(--texte-doux);
  margin-bottom: 0.5rem;
}

th,
td {
  border-bottom: 1px solid var(--bordure);
  padding: 0.5rem 0.6rem;
  text-align: left;
}

.actions {
  display: flex;
  gap: 0.75rem;
  align-items: center;
}
</style>
