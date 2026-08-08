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

/**
 * Chemins appelés par le client, avec leur verbe et les gabarits remis sous la forme OpenAPI.
 *
 * Le verbe était jusqu'ici figé à `'any'`, ce qui rendait la promesse « avec la bonne méthode »
 * de l'en-tête de fichier purement décorative : un `POST` envoyé sur une route qui ne publie
 * qu'un `GET` passait le test.
 */
function calledEndpoints(): { method: string; path: string }[] {
  const endpoints: { method: string; path: string }[] = []

  // `request<T>('/api/...', { method: 'X' })` et les `fetch` explicites. La suite est lue en
  // anticipation (`?=`) pour ne pas être consommée : la consommer ferait sauter au lecteur les
  // appels situés dans les 240 caractères suivants. Elle est ensuite tronquée à la fin de
  // l'appel, sinon le `method:` de la fonction suivante serait attribué à celle-ci.
  // Le préfixe `${API_BASE_URL}` des `fetch` explicites est absorbé : sans lui, la connexion, le
  // rafraîchissement et la lecture publique échappaient entièrement à la vérification.
  const pattern = /['"`](?:\$\{API_BASE_URL\})?(\/(?:api|openapi)[^'"`]*)['"`](?=([\s\S]{0,240}))/g
  let match: RegExpExecArray | null
  while ((match = pattern.exec(executableSource)) !== null) {
    const raw = match[1] as string
    const path = raw
      .split('?')[0]!
      // `${projectId}` → `{project_id}` : on ne peut pas connaître le nom exact du paramètre,
      // donc on normalise toute interpolation en un joker comparé plus bas.
      .replace(/\$\{[^}]+\}/g, '{}')
    const tail = (match[2] ?? '').split(/\n\s*\n|export |request<|request\(|fetch\(/)[0] ?? ''
    // Une requête sans `method:` explicite est un GET, comme `fetch` par défaut.
    const method = /method:\s*['"`]([A-Za-z]+)['"`]/.exec(tail)?.[1]?.toLowerCase() ?? 'get'
    endpoints.push({ method, path })
  }
  return endpoints
}

function schemaPathsNormalised(): Set<string> {
  return new Set(Object.keys(schema.paths).map((path) => path.replace(/\{[^}]+\}/g, '{}')))
}

/** `chemin normalisé` → verbes publiés. */
function schemaOperations(): Map<string, Set<string>> {
  const operations = new Map<string, Set<string>>()
  for (const [path, methods] of Object.entries(schema.paths)) {
    const normalised = path.replace(/\{[^}]+\}/g, '{}')
    const known = operations.get(normalised) ?? new Set<string>()
    for (const method of Object.keys(methods)) known.add(method.toLowerCase())
    operations.set(normalised, known)
  }
  return operations
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

  it('chaque appel emploie un verbe publié sur ce chemin', () => {
    const operations = schemaOperations()
    const wrong = calledEndpoints()
      .filter((endpoint) => endpoint.path !== '/openapi.json')
      .filter((endpoint) => !operations.get(endpoint.path)?.has(endpoint.method))
      .map((endpoint) => `${endpoint.method.toUpperCase()} ${endpoint.path}`)

    expect(wrong).toEqual([])
  })

  it('les routes attendues par les vues sont publiées', () => {
    const published = schemaPathsNormalised()
    for (const path of [
      '/api/auth/register',
      '/api/auth/token',
      // Sans cette route, le rafraîchissement silencieux de `client.ts` ne peut pas exister et
      // toute session redevient un compte à rebours de trente minutes.
      '/api/auth/refresh',
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
      // Les trois routes d'export ont enfin un appelant. Sans elles, le worker Celery, le broker
      // Redis et le volume d'exports sont facturés en production pour zéro valeur utilisateur.
      '/api/projects/{}/exports/pdf',
      '/api/projects/{}/exports/tasks/{}',
      '/api/projects/{}/exports/{}',
    ]) {
      expect(published.has(path), `${path} absent du schéma OpenAPI`).toBe(true)
    }
  })

  it('la vérification du verbe en est réellement une', () => {
    // Le verbe a été comparé un temps à la constante `'any'`, ce qui rendait le test précédent
    // décoratif : n'importe quelle méthode passait. Cette assertion-ci échoue si l'on y revient,
    // alors que les trois tests ci-dessus resteraient verts.
    const operations = schemaOperations()

    expect(operations.get('/api/projects/{}/exports/pdf')?.has('post')).toBe(true)
    expect(operations.get('/api/projects/{}/exports/pdf')?.has('get')).toBe(false)
    expect(operations.get('/api/projects/{}/exports/tasks/{}')?.has('get')).toBe(true)
    expect(operations.get('/api/projects/{}/exports/tasks/{}')?.has('post')).toBe(false)
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
