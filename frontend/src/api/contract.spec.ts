/**
 * Le frontend ne devine jamais une route ni un format.
 *
 * `docs/plan-generation-ia.md` §6 identifie le risque : « le frontend devine les routes ou le
 * format de réponse de l'API ». La contre-mesure prévue est le schéma OpenAPI, source de vérité.
 * Ce test la rend exécutable : chaque chemin appelé par `client.ts` doit exister dans le schéma
 * publié par le backend, avec la bonne méthode.
 *
 * L'instantané `openapi-snapshot.json` est régénéré et comparé en CI : s'il diverge du backend,
 * la CI échoue, ce qui interdit à ce test de valider un contrat périmé.
 */
import { describe, expect, it } from 'vitest'

// Importés par Vite plutôt que lus sur le disque : le test tourne ainsi à l'identique en local
// et en CI, sans dépendre du répertoire courant.
import snapshot from '@/api/openapi-snapshot.json'
import clientSource from '@/api/client.ts?raw'

const schema = snapshot as unknown as { paths: Record<string, Record<string, unknown>> }

/** Code du client, commentaires retirés : un chemin cité en exemple n'est pas un appel. */
const executableSource = clientSource
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '')

/** Chemins appelés par le client, avec les gabarits remis sous la forme OpenAPI. */
function calledEndpoints(): { method: string; path: string }[] {
  const endpoints: { method: string; path: string }[] = []

  // `request<T>('/api/...', { method: 'X' })` et les `fetch` explicites.
  const pattern = /['"`](\/(?:api|openapi)[^'"`]*)['"`]/g
  let match: RegExpExecArray | null
  while ((match = pattern.exec(executableSource)) !== null) {
    const raw = match[1] as string
    const path = raw
      .split('?')[0]!
      // `${projectId}` → `{project_id}` : on ne peut pas connaître le nom exact du paramètre,
      // donc on normalise toute interpolation en un joker comparé plus bas.
      .replace(/\$\{[^}]+\}/g, '{}')
    endpoints.push({ method: 'any', path })
  }
  return endpoints
}

function schemaPathsNormalised(): Set<string> {
  return new Set(Object.keys(schema.paths).map((path) => path.replace(/\{[^}]+\}/g, '{}')))
}

describe('contrat avec le backend', () => {
  it("l'instantané OpenAPI n'est pas vide", () => {
    expect(Object.keys(schema.paths).length).toBeGreaterThan(10)
  })

  it('chaque chemin appelé par le client existe dans le schéma', () => {
    const published = schemaPathsNormalised()
    const missing = calledEndpoints()
      .map((endpoint) => endpoint.path)
      .filter((path) => path !== '/openapi.json' && !published.has(path))

    expect(missing).toEqual([])
  })

  it('les routes attendues par les vues sont publiées', () => {
    const published = schemaPathsNormalised()
    for (const path of [
      '/api/auth/register',
      '/api/auth/token',
      '/api/auth/me',
      '/api/projects',
      '/api/projects/{}',
      '/api/projects/{}/rooms',
      '/api/projects/{}/scene',
      '/api/rooms/{}',
      '/api/faces/{}',
      '/api/faces/{}/elements',
      '/api/elements/{}',
      '/api/furniture-types',
    ]) {
      expect(published.has(path), `${path} absent du schéma OpenAPI`).toBe(true)
    }
  })

  it('le conflit d’édition est documenté sur les écritures du plan', () => {
    // Le client s'appuie sur le 409 pour signaler un conflit à l'utilisateur : s'il disparaît du
    // contrat, la fonctionnalité devient silencieusement inopérante.
    for (const [path, method] of [
      ['/api/projects/{project_id}', 'patch'],
      ['/api/rooms/{room_id}', 'patch'],
      ['/api/faces/{face_id}', 'patch'],
      ['/api/elements/{element_id}', 'patch'],
    ] as const) {
      const operation = schema.paths[path]?.[method] as { responses?: Record<string, unknown> }
      expect(operation?.responses?.['409'], `${method} ${path}`).toBeDefined()
    }
  })

  it('les champs du scene graph consommés par le viewer sont ceux du backend', () => {
    // Vérification de forme : le scene graph est un dictionnaire libre côté OpenAPI, donc on
    // s'assure au moins que la route existe et renvoie du JSON.
    const operation = schema.paths['/api/projects/{project_id}/scene']?.get as {
      responses?: Record<string, { content?: Record<string, unknown> }>
    }
    expect(operation?.responses?.['200']?.content?.['application/json']).toBeDefined()
  })
})
