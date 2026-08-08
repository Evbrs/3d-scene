/**
 * Client HTTP vers le backend FastAPI.
 *
 * Le schéma OpenAPI servi par le backend (`/openapi.json`) est la source de vérité des routes et
 * des formats de réponse — voir `docs/plan-generation-ia.md` §6. Aucune route n'est devinée :
 * chaque chemin ci-dessous existe dans ce schéma, et `api-contract.spec.ts` le vérifie.
 */

import type {
  Covering,
  Face,
  FurnitureType,
  Page,
  PlanElement,
  Project,
  ProjectSummary,
  Room,
  SceneGraph,
} from '@/api/types'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

/** Clé de stockage du jeton. `sessionStorage` : le jeton disparaît à la fermeture de l'onglet. */
const TOKEN_KEY = 'renovation.access_token'

/**
 * Nature d'un conflit d'écriture, lue dans le champ `code` du 409.
 *
 * Deux situations sans rapport partagent ce statut : le plan a bougé sous les pieds du client
 * (`stale`), ou le serveur refuse une modification qui détruirait des éléments tant qu'elle
 * n'est pas confirmée (`destructive`). L'interface ne propose pas la même chose dans les deux
 * cas. Les distinguer sur une sous-chaîne du message français cassait à la première
 * reformulation côté serveur.
 */
export type ConflictKind = 'stale' | 'destructive'

/** Un code inconnu retombe sur `stale`, le seul des deux qui ne détruit rien s'il est proposé à tort. */
function conflictKindOf(body: unknown): ConflictKind | null {
  const code = readString(body, 'code')
  if (code === null) return null
  return /destruct|force|lose|perte/i.test(code) ? 'destructive' : 'stale'
}

function readString(body: unknown, key: string): string | null {
  if (!body || typeof body !== 'object') return null
  const value = (body as Record<string, unknown>)[key]
  return typeof value === 'string' ? value : null
}

function readNumber(body: unknown, key: string): number | null {
  if (!body || typeof body !== 'object') return null
  const value = (body as Record<string, unknown>)[key]
  return typeof value === 'number' ? value : null
}

export class ApiError extends Error {
  /** Renseigné seulement sur un 409, et seulement si le serveur a nommé le conflit. */
  readonly conflictKind: ConflictKind | null
  /** Version du projet côté serveur au moment du refus, quand le serveur la joint. */
  readonly currentVersion: number | null

  constructor(
    readonly status: number,
    readonly detail: string,
    readonly body: unknown = null,
  ) {
    super(detail)
    this.name = 'ApiError'
    this.conflictKind = status === 409 ? conflictKindOf(body) : null
    this.currentVersion = readNumber(body, 'current_version')
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
  fallbackRefreshToken = null
}

// --- Session : rafraîchissement silencieux ------------------------------------------------------

/**
 * Jeton de rafraîchissement de repli.
 *
 * En production le backend le pose dans un cookie `httpOnly; Secure; SameSite=Lax` de chemin
 * `/api/auth` : il est alors invisible d'ici, et c'est précisément le but — un XSS ne peut pas
 * le voler. Certains déploiements de développement servent l'API sans cookie et le renvoient
 * dans le corps ; on le garde alors **en mémoire seulement**, jamais dans un stockage, pour que
 * la session tienne quand même la journée sans créer de secret persistant lisible en JavaScript.
 */
let fallbackRefreshToken: string | null = null

/** Rafraîchissement en cours. Voir `refreshSession` : une seule requête pour tous les appelants. */
let refreshInFlight: Promise<boolean> | null = null

let sessionLostHandler: (() => void) | null = null

/**
 * Branche ce qui doit arriver quand la session est définitivement perdue.
 *
 * Le client HTTP n'a pas à connaître le routeur : `main.ts` y accroche la déconnexion propre et
 * la redirection vers l'écran de connexion.
 */
export function onSessionLost(handler: (() => void) | null): void {
  sessionLostHandler = handler
}

/**
 * Renouvelle l'access token, une seule fois pour tous les appels concurrents.
 *
 * Sans cette mutualisation, les dix requêtes que l'éditeur émet en rafale se heurtent au même
 * 401 et déclenchent dix rafraîchissements. Le backend fait tourner le jeton à chaque appel :
 * les neuf réponses en retard porteraient des jetons déjà révoqués, et l'utilisateur serait
 * déconnecté au milieu de son travail.
 */
function refreshSession(): Promise<boolean> {
  refreshInFlight ??= performRefresh().finally(() => {
    refreshInFlight = null
  })
  return refreshInFlight
}

async function performRefresh(): Promise<boolean> {
  // Appelé sans corps quand le cookie fait foi ; le repli développement renvoie le jeton reçu.
  const carried = fallbackRefreshToken

  try {
    const response = await fetch(`${API_BASE_URL}/api/auth/refresh`, {
      method: 'POST',
      // Sans ce drapeau, le cookie `httpOnly` n'est pas joint sur une requête cross-origine —
      // et le frontend de développement tourne bien sur une autre origine que l'API.
      credentials: 'include',
      ...(carried
        ? {
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ refresh_token: carried }),
          }
        : {}),
    })
    if (!response.ok) return false
    const body: unknown = await response.json()
    const token = readString(body, 'access_token')
    if (!token) return false
    storeToken(token)
    fallbackRefreshToken = readString(body, 'refresh_token')
    return true
  } catch {
    return false
  }
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

/**
 * Requête authentifiée, avec un unique rejeu après renouvellement du jeton.
 *
 * `allowRetry` est le garde-fou anti-boucle : la requête rejouée ne peut plus en déclencher une
 * autre. Un backend qui répondrait 401 même avec un jeton frais ferait sinon boucler le client
 * indéfiniment au lieu de rendre la main.
 */
async function request<T>(path: string, init: RequestInit = {}, allowRetry = true): Promise<T> {
  const headers = new Headers(init.headers)
  const token = storedToken()
  if (token) {
    headers.set('Authorization', `Bearer ${token}`)
  }
  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers })

  // Un 401 sur une session que l'on croyait valide, c'est un access token expiré : au bout de
  // trente minutes d'édition, l'utilisateur perdait chaque enregistrement sans rien y pouvoir.
  if (response.status === 401 && token) {
    if (allowRetry && (await refreshSession())) {
      return request<T>(path, init, false)
    }
    // Rejeu déjà tenté, ou rafraîchissement refusé : la session est perdue pour de bon.
    clearToken()
    sessionLostHandler?.()
  }

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

/**
 * Inscription. Renvoie le message du serveur, volontairement identique que l'adresse soit libre
 * ou déjà prise (anti-énumération) : c'est ce message-là qu'il faut afficher, pas une réussite
 * inventée par le frontend.
 */
export async function register(email: string, password: string): Promise<string> {
  const body = await request<{ detail: string }>('/api/auth/register', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })
  return body.detail
}

export async function login(email: string, password: string): Promise<string> {
  // `/token` attend un formulaire (contrainte du standard OAuth2), pas du JSON.
  const form = new URLSearchParams({ username: email, password })
  const response = await fetch(`${API_BASE_URL}/api/auth/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: form,
    // Laisse le backend poser le cookie de rafraîchissement (voir `performRefresh`).
    credentials: 'include',
  })
  const body: unknown = await response.json()
  if (!response.ok) {
    throw new ApiError(response.status, extractDetail(body, response.status), body)
  }
  const token = (body as { access_token: string }).access_token
  storeToken(token)
  fallbackRefreshToken = readString(body, 'refresh_token')
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
  /**
   * Fond de plan (spec §10, A5). `background_url` n'accepte qu'un chemin du site commençant par
   * un seul `/` ou une URL `https://` : le serveur refuse tout le reste en 422, et
   * `editor/calibration.ts` applique la même règle avant l'envoi pour ne pas faire un
   * aller-retour rien que pour apprendre qu'un `data:` est refusé.
   */
  background_url?: string | null
  background_scale_cm_per_px?: number | null
  background_offset_x_cm?: number
  background_offset_y_cm?: number
  background_rotation_deg?: number
  background_opacity?: number
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

/**
 * Le serveur **remplace** le dictionnaire de revêtement, il ne le fusionne pas : n'envoyer que
 * la couleur effacerait la matière, les dimensions d'unité et le motif de pose. L'appelant est
 * donc tenu de passer le revêtement complet.
 */
export function updateFaceCovering(
  faceId: number,
  covering: Covering | null,
  version?: number,
): Promise<Face> {
  return request<Face>(`/api/faces/${faceId}`, {
    method: 'PATCH',
    body: JSON.stringify({ covering, version }),
  })
}

/** Ce qu'un élément **est**, sans dire où il est posé : commun aux deux ancrages (spec §10, A4). */
export interface ElementShape {
  kind?: string
  width_cm?: number
  height_cm?: number
  depth_cm?: number
  rotation_deg?: number
  furniture_type_id?: number | null
  colors?: Record<string, string>
  variant_params?: Record<string, unknown>
}

/** Élément adossé à une face : les décalages sont mesurés dans le plan de cette face. */
export interface ElementPayload extends ElementShape {
  kind: string
  x_offset_cm?: number
  y_offset_cm?: number
  version?: number
}

/**
 * Meuble posé au sol de la pièce.
 *
 * `pos_x_cm` / `pos_y_cm` sont obligatoires et désignent le **centre** de l'emprise dans le
 * repère du plan. Les décalages de face y sont refusés par le serveur (`extra=forbid`) : ce ne
 * sont pas les mêmes coordonnées, et les accepter en silence poserait le meuble ailleurs.
 */
export interface RoomElementPayload extends ElementShape {
  pos_x_cm: number
  pos_y_cm: number
  version?: number
}

export function createElement(faceId: number, payload: ElementPayload): Promise<PlanElement> {
  return request<PlanElement>(`/api/faces/${faceId}/elements`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

/** Pose un meuble libre au sol d'une pièce (spec §10, A4). */
export function createRoomElement(
  roomId: number,
  payload: RoomElementPayload,
): Promise<PlanElement> {
  return request<PlanElement>(`/api/rooms/${roomId}/elements`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

/**
 * Modification d'un élément.
 *
 * `pos_x_cm` / `pos_y_cm` ne valent que pour un meuble libre, `x_offset_cm` / `y_offset_cm` que
 * pour un élément adossé : mélanger les deux rend un 422. `face_id` et `room_id` ne sont pas
 * modifiables — changer d'ancrage, c'est supprimer puis recréer (spec §10, A4).
 */
export function updateElement(
  elementId: number,
  payload: Partial<ElementPayload & RoomElementPayload>,
): Promise<PlanElement> {
  return request<PlanElement>(`/api/elements/${elementId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export function deleteElement(elementId: number): Promise<void> {
  return request<void>(`/api/elements/${elementId}`, { method: 'DELETE' })
}

// --- Écriture en lot (spec §10, amendement A6) --------------------------------------------------

/**
 * Borne du serveur, répétée ici pour découper les gestes qui la dépassent.
 *
 * Sélectionner 150 meubles et les déplacer d'un coup est un geste banal ; le serveur refuse le
 * lot entier au-delà de 100 opérations. `editor/operations.ts` découpe donc en paquets successifs
 * plutôt que de laisser l'utilisateur découvrir la limite par un 422.
 */
export const MAX_BATCH_OPERATIONS = 100

/**
 * Aucune opération ne porte de `version` : c'est le lot qui la porte, une fois.
 *
 * Ce n'est pas un détail de style. Les corps d'opération sont validés par des modèles Pydantic en
 * `extra="forbid"` : un `version` oublié dans le corps d'un élément ne serait pas ignoré, il
 * ferait refuser le lot entier en 422. Le retirer ici fait porter cette règle au compilateur
 * plutôt qu'à la vigilance de l'appelant.
 */
type WithoutVersion<T> = Omit<T, 'version'>

export type BatchOperation =
  | { op: 'create_face_element'; face_id: number; element: WithoutVersion<ElementPayload> }
  | { op: 'create_room_element'; room_id: number; element: WithoutVersion<RoomElementPayload> }
  | {
      op: 'update_element'
      element_id: number
      changes: Partial<WithoutVersion<ElementPayload & RoomElementPayload>>
    }
  | { op: 'delete_element'; element_id: number }
  // `force` non plus : il confirme la suppression de murs portant des éléments, ce qu'une
  // création de pièce ne fait jamais. `RoomBase` le refuse, `RoomPatch` l'accepte.
  | { op: 'create_room'; room: WithoutVersion<Omit<RoomPayload, 'force'>> }
  | { op: 'update_room'; room_id: number; changes: Partial<WithoutVersion<RoomPayload>> }
  | { op: 'delete_room'; room_id: number }

export interface BatchOperationResult {
  op: string
  status: 'created' | 'updated' | 'deleted'
  element_id: number | null
  room_id: number | null
  element: PlanElement | null
  room: Room | null
}

export interface BatchResponse {
  /** Nouvelle version du projet : **une seule** incrémentation pour tout le lot. */
  version: number
  /** Même longueur et même ordre que les opérations envoyées. */
  results: BatchOperationResult[]
}

/**
 * Applique un lot d'écritures en une transaction.
 *
 * Tout ou rien : une opération refusée annule le lot, nomme son rang (base 1) et n'écrit rien.
 * C'est ce qui rend un glisser-déposer de quinze meubles possible — quinze appels unitaires
 * seraient strictement sériels, chacun invalidant la version que le client détient.
 */
export function applyBatch(
  projectId: number,
  operations: BatchOperation[],
  version?: number,
): Promise<BatchResponse> {
  return request<BatchResponse>(`/api/projects/${projectId}/batch`, {
    method: 'POST',
    body: JSON.stringify({ version, operations }),
  })
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

// --- Export PDF (P9) --------------------------------------------------------------------------

/** Réponse du 202 : la génération part en tâche de fond, la requête rend la main tout de suite. */
export interface ExportAccepted {
  task_id: string
  status: string
  poll_url: string
}

/** Descriptif de l'export produit. Le PDF lui-même ne transite jamais par le backend de résultats. */
export interface ExportResult {
  project_id: number
  filename: string
  size_bytes: number
  generated_at: string
}

export interface ExportStatus {
  task_id: string
  state: string
  ready: boolean
  result: ExportResult | null
  error: string | null
}

/** Cadence de sondage et durée au-delà de laquelle on cesse d'attendre, en millisecondes. */
export const EXPORT_POLL_MS = 1500
export const EXPORT_TIMEOUT_MS = 60_000

export function requestPdfExport(projectId: number): Promise<ExportAccepted> {
  return request<ExportAccepted>(`/api/projects/${projectId}/exports/pdf`, {
    method: 'POST',
  })
}

export function readExportStatus(projectId: number, taskId: string): Promise<ExportStatus> {
  return request<ExportStatus>(
    `/api/projects/${projectId}/exports/tasks/${encodeURIComponent(taskId)}`,
  )
}

/**
 * URL de téléchargement d'un export.
 *
 * Écrite en toutes lettres et à un seul endroit : c'est ce que `contract.spec.ts` confronte au
 * schéma OpenAPI. Une URL assemblée morceau par morceau échapperait à cette vérification.
 */
export function exportDownloadUrl(projectId: number, filename: string): string {
  return `${API_BASE_URL}/api/projects/${projectId}/exports/${encodeURIComponent(filename)}`
}

/**
 * Attend qu'un export soit prêt, en sondant le serveur.
 *
 * L'attente est bornée : au-delà de `EXPORT_TIMEOUT_MS`, on rend la main avec un message plutôt
 * que de sonder indéfiniment. Un worker arrêté ou un broker injoignable laisserait sinon
 * l'interface tourner en boucle jusqu'à la fermeture de l'onglet, sans jamais rien dire.
 */
export async function waitForPdfExport(
  projectId: number,
  taskId: string,
): Promise<ExportResult> {
  const deadline = Date.now() + EXPORT_TIMEOUT_MS

  while (Date.now() < deadline) {
    const status = await readExportStatus(projectId, taskId)
    if (status.ready) {
      if (status.result) return status.result
      throw new Error(status.error ?? "La génération du PDF a échoué côté serveur.")
    }
    await new Promise((resolve) => setTimeout(resolve, EXPORT_POLL_MS))
  }

  throw new Error(
    `Le PDF n'est toujours pas prêt après ${EXPORT_TIMEOUT_MS / 1000} s. ` +
      'La génération continue côté serveur : réessayez dans un instant.',
  )
}

/**
 * Récupère le PDF produit.
 *
 * Un simple lien ne suffit pas : la route exige l'en-tête `Authorization`, que le navigateur ne
 * joint pas à une navigation. Le fichier est donc lu ici, avec le même unique rejeu après
 * renouvellement du jeton que les autres appels — sans lui, un export lancé en fin de session
 * échouerait juste après avoir été généré.
 */
export async function downloadExport(
  projectId: number,
  filename: string,
  allowRetry = true,
): Promise<Blob> {
  const headers = new Headers()
  const token = storedToken()
  if (token) {
    headers.set('Authorization', `Bearer ${token}`)
  }

  const response = await fetch(exportDownloadUrl(projectId, filename), { headers })

  if (response.status === 401 && token) {
    if (allowRetry && (await refreshSession())) {
      return downloadExport(projectId, filename, false)
    }
    clearToken()
    sessionLostHandler?.()
  }
  if (!response.ok) {
    throw new ApiError(response.status, `Erreur HTTP ${response.status}`)
  }
  return response.blob()
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
