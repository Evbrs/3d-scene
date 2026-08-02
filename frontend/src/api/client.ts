/**
 * Client HTTP vers le backend FastAPI.
 *
 * Le schéma OpenAPI servi par le backend (`/openapi.json`) est la source de vérité des routes et
 * des formats de réponse — voir `docs/plan-generation-ia.md` §6. Aucune route n'est devinée :
 * chaque chemin ci-dessous existe dans ce schéma, et `api-contract.spec.ts` le vérifie.
 */

import type {
  FurnitureType,
  Page,
  Project,
  ProjectSummary,
  Room,
  SceneGraph,
} from '@/api/types'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

/** Clé de stockage du jeton. `sessionStorage` : le jeton disparaît à la fermeture de l'onglet. */
const TOKEN_KEY = 'renovation.access_token'

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
    readonly body: unknown = null,
  ) {
    super(detail)
    this.name = 'ApiError'
  }

  /** Conflit d'édition : le plan a changé depuis la dernière lecture (spec §8, cas 3). */
  get isConflict(): boolean {
    return this.status === 409
  }

  get isUnauthorized(): boolean {
    return this.status === 401
  }
}

export function storedToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY)
}

export function storeToken(token: string): void {
  sessionStorage.setItem(TOKEN_KEY, token)
}

export function clearToken(): void {
  sessionStorage.removeItem(TOKEN_KEY)
}

/**
 * Construit une URL en gardant le chemin **littéral**.
 *
 * Interpoler la chaîne de requête directement dans le chemin (`/api/x${query}`) rendrait le
 * contrat invérifiable statiquement : le test `contract.spec.ts` ne pourrait plus confronter les
 * chemins appelés au schéma OpenAPI.
 */
function withQuery(path: string, params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') search.set(key, String(value))
  }
  const query = search.toString()
  return query ? `${path}?${query}` : path
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  const token = storedToken()
  if (token) {
    headers.set('Authorization', `Bearer ${token}`)
  }
  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers })

  if (response.status === 204) {
    return undefined as T
  }

  const raw = await response.text()
  const body: unknown = raw ? JSON.parse(raw) : null

  if (!response.ok) {
    throw new ApiError(response.status, extractDetail(body, response.status), body)
  }
  return body as T
}

function extractDetail(body: unknown, status: number): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail
    if (typeof detail === 'string') return detail
    // Erreur de validation FastAPI : une liste d'objets. On rend le premier message lisible
    // plutôt que « [object Object] ».
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { loc?: unknown[]; msg?: string }
      const field = Array.isArray(first.loc) ? first.loc.slice(1).join('.') : ''
      return field ? `${field} : ${first.msg ?? 'valeur invalide'}` : (first.msg ?? 'Requête invalide')
    }
  }
  return `Erreur HTTP ${status}`
}

// --- Authentification -------------------------------------------------------------------------

export async function register(email: string, password: string): Promise<void> {
  await request<{ detail: string }>('/api/auth/register', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })
}

export async function login(email: string, password: string): Promise<string> {
  // `/token` attend un formulaire (contrainte du standard OAuth2), pas du JSON.
  const form = new URLSearchParams({ username: email, password })
  const response = await fetch(`${API_BASE_URL}/api/auth/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: form,
  })
  const body: unknown = await response.json()
  if (!response.ok) {
    throw new ApiError(response.status, extractDetail(body, response.status), body)
  }
  const token = (body as { access_token: string }).access_token
  storeToken(token)
  return token
}

export interface CurrentUser {
  id: number
  email: string
  is_active: boolean
  is_superuser: boolean
}

export function currentUser(): Promise<CurrentUser> {
  return request<CurrentUser>('/api/auth/me')
}

// --- Projets ----------------------------------------------------------------------------------

export function listProjects(limit = 20, offset = 0): Promise<Page<ProjectSummary>> {
  return request<Page<ProjectSummary>>(withQuery('/api/projects', { limit, offset }))
}

export function readProject(projectId: number): Promise<Project> {
  return request<Project>(`/api/projects/${projectId}`)
}

export function createProject(name: string, description?: string): Promise<Project> {
  return request<Project>('/api/projects', {
    method: 'POST',
    body: JSON.stringify({ name, description: description ?? null }),
  })
}

export function deleteProject(projectId: number): Promise<void> {
  return request<void>(`/api/projects/${projectId}`, { method: 'DELETE' })
}

// --- Pièces, faces, éléments --------------------------------------------------------------------

export interface RoomPayload {
  name: string
  polygon?: number[][]
  wall_thickness_cm?: number
  ceiling_height_cm?: number
  version?: number
  force?: boolean
}

export function createRoom(projectId: number, payload: RoomPayload): Promise<Room> {
  return request<Room>(`/api/projects/${projectId}/rooms`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateRoom(roomId: number, payload: Partial<RoomPayload>): Promise<Room> {
  return request<Room>(`/api/rooms/${roomId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export function deleteRoom(roomId: number): Promise<void> {
  return request<void>(`/api/rooms/${roomId}`, { method: 'DELETE' })
}

export function updateFaceCovering(
  faceId: number,
  covering: Record<string, unknown> | null,
  version?: number,
): Promise<unknown> {
  return request(`/api/faces/${faceId}`, {
    method: 'PATCH',
    body: JSON.stringify({ covering, version }),
  })
}

export interface ElementPayload {
  kind: string
  x_offset_cm?: number
  y_offset_cm?: number
  width_cm?: number
  height_cm?: number
  depth_cm?: number
  rotation_deg?: number
  furniture_type_id?: number | null
  colors?: Record<string, string>
  variant_params?: Record<string, unknown>
  version?: number
}

export function createElement(faceId: number, payload: ElementPayload): Promise<unknown> {
  return request(`/api/faces/${faceId}/elements`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateElement(
  elementId: number,
  payload: Partial<ElementPayload>,
): Promise<unknown> {
  return request(`/api/elements/${elementId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export function deleteElement(elementId: number): Promise<void> {
  return request<void>(`/api/elements/${elementId}`, { method: 'DELETE' })
}

// --- Catalogue et scène ---------------------------------------------------------------------------

export function listFurnitureTypes(category?: string): Promise<Page<FurnitureType>> {
  return request<Page<FurnitureType>>(
    withQuery('/api/furniture-types', { limit: 200, category }),
  )
}

export function readSceneGraph(projectId: number): Promise<SceneGraph> {
  return request<SceneGraph>(`/api/projects/${projectId}/scene`)
}

// --- Partage de vue (P8) --------------------------------------------------------------------

export interface SharedView {
  id: number
  project_id: number
  token: string
  state: Record<string, unknown>
  created_at: string
}

export interface PublicView {
  kind: 'shared-view'
  project_name: string
  state: Record<string, unknown>
  scene: SceneGraph
}

export function createSharedView(
  projectId: number,
  state: Record<string, unknown>,
  expiresInDays?: number,
): Promise<SharedView> {
  return request<SharedView>(`/api/projects/${projectId}/shared-views`, {
    method: 'POST',
    body: JSON.stringify({ state, expires_in_days: expiresInDays ?? null }),
  })
}

export function listSharedViews(projectId: number): Promise<SharedView[]> {
  return request<SharedView[]>(`/api/projects/${projectId}/shared-views`)
}

export function revokeSharedView(sharedViewId: number): Promise<void> {
  return request<void>(`/api/shared-views/${sharedViewId}`, { method: 'DELETE' })
}

/** Lecture publique : volontairement sans jeton, c'est tout l'intérêt du lien de partage. */
export async function readPublicView(token: string): Promise<PublicView> {
  const response = await fetch(`${API_BASE_URL}/api/public/views/${encodeURIComponent(token)}`)
  const body: unknown = await response.json()
  if (!response.ok) {
    throw new ApiError(response.status, extractDetail(body, response.status), body)
  }
  return body as PublicView
}

export function openApiSchema(): Promise<Record<string, unknown>> {
  return request<Record<string, unknown>>('/openapi.json')
}
