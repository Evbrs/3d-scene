/**
 * Client HTTP minimal vers le backend FastAPI.
 *
 * Le schéma OpenAPI servi par le backend (`/openapi.json`) est la source de vérité des routes
 * et des formats de réponse — voir docs/plan-generation-ia.md §6. Aucune route n'est devinée.
 */

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

export interface HealthResponse {
  status: 'ok'
}

export async function fetchHealth(): Promise<HealthResponse> {
  const response = await fetch(`${API_BASE_URL}/health`)
  if (!response.ok) {
    throw new Error(`GET /health a répondu ${response.status}`)
  }
  return (await response.json()) as HealthResponse
}
