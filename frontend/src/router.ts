/** Routes de l'application. Les vues protégées exigent une session valide. */
import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import { storedToken } from '@/api/client'

/**
 * Nom du produit, suffixe de tous les titres d'onglet.
 *
 * Écrit ici et pas seulement dans `index.html` : à partir de la première navigation, c'est le
 * routeur qui possède `document.title`, et deux libellés divergents se verraient au premier
 * clic.
 */
export const NOM_DU_PRODUIT = 'Éditeur de plan de rénovation'

/**
 * Documents légaux, servis par une seule vue.
 *
 * La clé est le paramètre d'URL : `/legal/cgv` charge le document `cgv`. Une route par document
 * plutôt qu'une page unique à onglets, parce qu'un lien vers les CGV doit pouvoir être cité dans
 * un devis et retomber exactement au même endroit des années plus tard.
 */
export const DOCUMENTS_LEGAUX = ['mentions', 'cgu', 'cgv', 'confidentialite'] as const
export type DocumentLegal = (typeof DOCUMENTS_LEGAUX)[number]

const routes: RouteRecordRaw[] = [
  { path: '/', redirect: '/projets' },
  {
    path: '/connexion',
    name: 'connexion',
    component: () => import('@/views/SignInView.vue'),
    meta: { titre: 'Connexion' },
  },
  {
    path: '/mot-de-passe-oublie',
    name: 'mot-de-passe-oublie',
    component: () => import('@/views/ForgotPasswordView.vue'),
    meta: { titre: 'Mot de passe oublié' },
  },
  {
    // Le jeton voyage en paramètre de requête et non dans le chemin : c'est la forme qu'un lien
    // reçu par courriel doit avoir pour survivre aux clients de messagerie qui réécrivent les
    // URL, et elle laisse la vue le récupérer sans que le routeur le fige dans un nom de route.
    path: '/mot-de-passe/reinitialiser',
    name: 'reinitialiser-mot-de-passe',
    component: () => import('@/views/ResetPasswordView.vue'),
    meta: { titre: 'Nouveau mot de passe' },
  },
  {
    path: '/legal/:document',
    name: 'legal',
    component: () => import('@/views/LegalView.vue'),
    props: true,
    meta: { titre: 'Informations légales' },
  },
  // Les anciens liens restent valides : `/conditions` était l'adresse posée par le formulaire
  // d'inscription avant que les documents existent, et elle circule déjà.
  { path: '/conditions', redirect: '/legal/cgu' },
  {
    path: '/compte',
    name: 'compte',
    component: () => import('@/views/AccountSettingsView.vue'),
    meta: { requiresAuth: true, titre: 'Mon compte' },
  },
  {
    // Hors `requiresAuth` : une grille de prix doit s'afficher avant l'inscription, sinon elle ne
    // sert à rien. La route publique correspondante (`GET /api/plans`) l'est pour la même raison.
    path: '/tarifs',
    name: 'tarifs',
    component: () => import('@/views/PricingView.vue'),
    meta: { titre: 'Tarifs' },
  },
  {
    // Distincte de `/compte`, qui porte les réglages du **compte** (mot de passe, données
    // personnelles) : celle-ci porte l'abonnement de l'**entreprise** et sa consommation.
    path: '/abonnement',
    name: 'abonnement',
    component: () => import('@/views/AccountView.vue'),
    meta: { requiresAuth: true, titre: 'Abonnement' },
  },
  // `OnboardingView` n'a volontairement **pas** de route à elle : c'est l'état vide de la liste
  // des chantiers, rendu par `ProjectsView`. Une route séparée aurait produit deux `<h1>` sur la
  // même page une fois le composant embarqué, et n'aurait rien montré à qui a déjà un chantier —
  // la route de démonstration refuse alors de créer quoi que ce soit.
  {
    path: '/projets',
    name: 'projets',
    component: () => import('@/views/ProjectsView.vue'),
    meta: { requiresAuth: true, titre: 'Mes projets' },
  },
  {
    path: '/projets/:projectId/plan',
    name: 'editeur',
    component: () => import('@/views/EditorView.vue'),
    props: true,
    meta: { requiresAuth: true, titre: 'Plan 2D' },
  },
  {
    // Volontairement hors `requiresAuth` : c'est le principe du lien de partage (spec §3.5).
    path: '/partage/:token',
    name: 'partage',
    component: () => import('@/views/PublicViewerView.vue'),
    props: true,
    meta: { titre: 'Plan partagé' },
  },
  {
    path: '/projets/:projectId/vue-3d',
    name: 'viewer',
    component: () => import('@/views/ViewerView.vue'),
    props: true,
    meta: { requiresAuth: true, titre: 'Vue 3D' },
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

/**
 * Titre de l'onglet, reconstruit à chaque navigation.
 *
 * Une application à page unique ne change jamais `document.title` toute seule : sans ce crochet,
 * l'onglet affichait « Éditeur de plan de rénovation » sur les quinze écrans, historique de
 * navigation compris. Les vues qui connaissent mieux leur sujet — le plan partagé, qui porte un
 * nom de chantier — écrasent la valeur après leur chargement ; l'ordre le permet, `afterEach`
 * s'exécutant avant que leurs requêtes aboutissent.
 */
router.afterEach((to) => {
  const titre = typeof to.meta.titre === 'string' ? to.meta.titre : ''
  document.title = titre ? `${titre} — ${NOM_DU_PRODUIT}` : NOM_DU_PRODUIT
})
