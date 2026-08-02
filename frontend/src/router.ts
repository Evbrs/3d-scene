/** Routes de l'application. Les vues protégées exigent une session valide. */
import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import { storedToken } from '@/api/client'

const routes: RouteRecordRaw[] = [
  { path: '/', redirect: '/projets' },
  {
    path: '/connexion',
    name: 'connexion',
    component: () => import('@/views/SignInView.vue'),
  },
  {
    path: '/projets',
    name: 'projets',
    component: () => import('@/views/ProjectsView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/projets/:projectId/plan',
    name: 'editeur',
    component: () => import('@/views/EditorView.vue'),
    props: true,
    meta: { requiresAuth: true },
  },
  {
    // Volontairement hors `requiresAuth` : c'est le principe du lien de partage (spec §3.5).
    path: '/partage/:token',
    name: 'partage',
    component: () => import('@/views/PublicViewerView.vue'),
    props: true,
  },
  {
    path: '/projets/:projectId/vue-3d',
    name: 'viewer',
    component: () => import('@/views/ViewerView.vue'),
    props: true,
    meta: { requiresAuth: true },
  },
]

export const router = createRouter({ history: createWebHistory(), routes })

router.beforeEach((to) => {
  // Garde purement ergonomique : la vraie autorisation est côté serveur, qui revérifie le jeton
  // à chaque requête. Une garde de routeur seule ne protège rien.
  if (to.meta.requiresAuth && !storedToken()) {
    return { name: 'connexion', query: { suivant: to.fullPath } }
  }
  return true
})
