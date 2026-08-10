/**
 * Session et erreurs du client HTTP.
 *
 * Le point sensible n'est pas le verbe ni l'URL — `contract.spec.ts` s'en charge — mais ce qui
 * arrive quand l'access token expire au milieu d'une session d'édition. Sans rejeu, chaque
 * enregistrement échouait en 401 brut au bout de trente minutes et le travail était perdu ; avec
 * un rejeu mal cadré, on obtient soit une boucle infinie, soit N rafraîchissements concurrents
 * qui s'invalident mutuellement à cause de la rotation du jeton.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

type Client = typeof import('@/api/client')

interface Call {
  url: string
  init: RequestInit | undefined
}

const calls: Call[] = []
let api: Client

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** Réponses servies dans l'ordre, par préfixe de chemin. */
function respondWith(plan: Record<string, (() => Response)[]>): void {
  const remaining: Record<string, (() => Response)[]> = {}
  for (const [path, responses] of Object.entries(plan)) remaining[path] = [...responses]

  vi.stubGlobal('fetch', (url: string, init?: RequestInit) => {
    calls.push({ url, init })
    const path = Object.keys(remaining).find((candidate) => url.includes(candidate))
    const queue = path ? remaining[path] : undefined
    const next = queue?.length === 1 ? queue[0] : queue?.shift()
    if (!next) throw new Error(`Aucune réponse prévue pour ${url}`)
    return Promise.resolve(next())
  })
}

function pathsCalled(fragment: string): Call[] {
  return calls.filter((call) => call.url.includes(fragment))
}

beforeEach(async () => {
  calls.length = 0
  sessionStorage.clear()
  // Chaque test repart d'un module neuf : le jeton de repli et la promesse de rafraîchissement
  // mutualisée sont des états de module, et les faire fuiter d'un test à l'autre masquerait
  // exactement les bugs que ces tests surveillent.
  vi.resetModules()
  api = await import('@/api/client')
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('rafraîchissement silencieux du jeton', () => {
  it('rejoue la requête après avoir renouvelé un access token expiré', async () => {
    api.storeToken('expire')
    respondWith({
      '/api/auth/me': [
        () => jsonResponse(401, { detail: 'Jeton expiré' }),
        () => jsonResponse(200, { id: 1, email: 'a@b.fr', is_active: true, is_superuser: false }),
      ],
      '/api/auth/refresh': [() => jsonResponse(200, { access_token: 'frais' })],
    })

    const user = await api.currentUser()

    expect(user.email).toBe('a@b.fr')
    expect(api.storedToken()).toBe('frais')
    expect(pathsCalled('/api/auth/me')).toHaveLength(2)
    expect(pathsCalled('/api/auth/refresh')).toHaveLength(1)
    // Le rejeu porte bien le nouveau jeton, sinon il reprendrait un 401.
    const replay = pathsCalled('/api/auth/me')[1]
    expect(new Headers(replay?.init?.headers).get('Authorization')).toBe('Bearer frais')
  })

  it('ne déclenche qu’un seul rafraîchissement pour des requêtes concurrentes', async () => {
    api.storeToken('expire')
    let meCalls = 0
    vi.stubGlobal('fetch', (url: string, init?: RequestInit) => {
      calls.push({ url, init })
      if (url.includes('/api/auth/refresh')) {
        return Promise.resolve(jsonResponse(200, { access_token: 'frais' }))
      }
      meCalls += 1
      return Promise.resolve(
        meCalls <= 3
          ? jsonResponse(401, { detail: 'Jeton expiré' })
          : jsonResponse(200, { id: 1, email: 'a@b.fr', is_active: true, is_superuser: false }),
      )
    })

    const users = await Promise.all([api.currentUser(), api.currentUser(), api.currentUser()])

    expect(users).toHaveLength(3)
    expect(pathsCalled('/api/auth/refresh')).toHaveLength(1)
  })

  it('abandonne après un seul rejeu et signale la session perdue', async () => {
    const lost = vi.fn()
    api.onSessionLost(lost)
    api.storeToken('expire')
    respondWith({
      '/api/auth/me': [() => jsonResponse(401, { detail: 'Jeton expiré' })],
      '/api/auth/refresh': [() => jsonResponse(200, { access_token: 'frais' })],
    })

    await expect(api.currentUser()).rejects.toMatchObject({ status: 401 })

    // Deux appels seulement : l'original et son unique rejeu. Sans le drapeau anti-boucle, un
    // backend qui répond 401 en boucle ferait boucler le client avec lui.
    expect(pathsCalled('/api/auth/me')).toHaveLength(2)
    expect(pathsCalled('/api/auth/refresh')).toHaveLength(1)
    expect(api.storedToken()).toBeNull()
    expect(lost).toHaveBeenCalledTimes(1)
  })

  it('déconnecte proprement quand le rafraîchissement est refusé', async () => {
    const lost = vi.fn()
    api.onSessionLost(lost)
    api.storeToken('expire')
    respondWith({
      '/api/auth/me': [() => jsonResponse(401, { detail: 'Jeton expiré' })],
      '/api/auth/refresh': [() => jsonResponse(401, { detail: 'Identifiants invalides' })],
    })

    await expect(api.currentUser()).rejects.toBeInstanceOf(api.ApiError)

    expect(pathsCalled('/api/auth/me')).toHaveLength(1)
    expect(api.storedToken()).toBeNull()
    expect(lost).toHaveBeenCalledTimes(1)
  })

  it('ne tente rien quand aucune session n’est ouverte', async () => {
    respondWith({ '/api/auth/me': [() => jsonResponse(401, { detail: 'Non authentifié' })] })

    await expect(api.currentUser()).rejects.toMatchObject({ status: 401 })

    expect(pathsCalled('/api/auth/refresh')).toHaveLength(0)
  })
})

describe('connexion', () => {
  it('ne stocke jamais le jeton de rafraîchissement', async () => {
    respondWith({
      '/api/auth/token': [
        () => jsonResponse(200, { access_token: 'acces', refresh_token: 'secret-de-repli' }),
      ],
    })

    await api.login('a@b.fr', 'motdepasse1234')

    expect(api.storedToken()).toBe('acces')
    const stored = [
      ...Object.values(sessionStorage),
      ...Object.values(localStorage),
    ].join('|')
    expect(stored).not.toContain('secret-de-repli')
  })

  it('joint les cookies pour laisser le backend poser le jeton de rafraîchissement', async () => {
    respondWith({ '/api/auth/token': [() => jsonResponse(200, { access_token: 'acces' })] })

    await api.login('a@b.fr', 'motdepasse1234')

    expect(pathsCalled('/api/auth/token')[0]?.init?.credentials).toBe('include')
  })

  it('appelle le rafraîchissement sans corps quand le cookie fait foi', async () => {
    // Aucune connexion préalable dans ce test : aucun jeton de repli en mémoire.
    api.storeToken('expire')
    respondWith({
      '/api/auth/me': [
        () => jsonResponse(401, { detail: 'Jeton expiré' }),
        () => jsonResponse(200, { id: 1, email: 'a@b.fr', is_active: true, is_superuser: false }),
      ],
      '/api/auth/refresh': [() => jsonResponse(200, { access_token: 'frais' })],
    })

    await api.currentUser()

    const refresh = pathsCalled('/api/auth/refresh')[0]
    expect(refresh?.init?.body).toBeUndefined()
    expect(refresh?.init?.credentials).toBe('include')
  })

  it('retombe sur le jeton reçu dans le corps quand aucun cookie n’est posé', async () => {
    respondWith({
      '/api/auth/token': [
        () => jsonResponse(200, { access_token: 'acces', refresh_token: 'repli' }),
      ],
      '/api/auth/me': [
        () => jsonResponse(401, { detail: 'Jeton expiré' }),
        () => jsonResponse(200, { id: 1, email: 'a@b.fr', is_active: true, is_superuser: false }),
      ],
      '/api/auth/refresh': [() => jsonResponse(200, { access_token: 'frais' })],
    })

    await api.login('a@b.fr', 'motdepasse1234')
    await api.currentUser()

    expect(pathsCalled('/api/auth/refresh')[0]?.init?.body).toBe(
      JSON.stringify({ refresh_token: 'repli' }),
    )
  })
})

describe('lecture d’un conflit 409', () => {
  it.each([
    ['stale_version', 'stale'],
    ['destructive_change', 'destructive'],
    ['force_required', 'destructive'],
  ])('le code %s donne un conflit %s', (code, expected) => {
    const error = new api.ApiError(409, 'refusé', { detail: 'refusé', code, current_version: 7 })

    expect(error.conflictKind).toBe(expected)
    expect(error.currentVersion).toBe(7)
  })

  it('reste muet quand le serveur ne nomme pas le conflit', () => {
    const error = new api.ApiError(409, 'refusé', { detail: 'refusé' })

    expect(error.conflictKind).toBeNull()
    expect(error.currentVersion).toBeNull()
  })

  it('ne qualifie pas de conflit une erreur qui n’en est pas une', () => {
    const error = new api.ApiError(422, 'invalide', { code: 'destructive_change' })

    expect(error.conflictKind).toBeNull()
  })
})

describe('chaîne commerciale', () => {
  it('émet le devis par un POST, sans corps à inventer', async () => {
    respondWith({ '/api/quotes/12/issue': [() => jsonResponse(200, { id: 12, status: 'sent' })] })

    await api.issueQuote(12)

    const appel = pathsCalled('/api/quotes/12/issue')[0]
    expect(appel?.init?.method).toBe('POST')
    expect(appel?.init?.body).toBeUndefined()
  })

  it('remplace le chiffrage d’une face par un PUT, pas par un PATCH', async () => {
    respondWith({ '/api/faces/7/costing': [() => jsonResponse(200, { id: 1, face_id: 7 })] })

    await api.setFaceCosting(7, {
      price_item_code: 'PEINT-MUR',
      override_quantity: '12.5',
      override_unit_price_cents: 2400,
    })

    const appel = pathsCalled('/api/faces/7/costing')[0]
    // La route est un remplacement complet : un PATCH laisserait l'ambiguïté entre « ne touche
    // pas » et « efface » sur trois champs qui sont tous facultatifs.
    expect(appel?.init?.method).toBe('PUT')
    // La quantité reste une chaîne décimale de bout en bout : la faire transiter par un nombre
    // JavaScript perdrait les millièmes que le serveur, lui, conserve.
    expect(JSON.parse(String(appel?.init?.body)).override_quantity).toBe('12.5')
  })

  it('n’envoie au PATCH d’un devis que les champs qu’on veut changer', async () => {
    respondWith({ '/api/quotes/12': [() => jsonResponse(200, { id: 12, status: 'accepted' })] })

    await api.updateQuote(12, { status: 'accepted' })

    // Le serveur applique `exclude_unset` : un champ envoyé « pour la forme » serait écrit, et
    // sur un document déjà émis il vaudrait un 409.
    expect(JSON.parse(String(pathsCalled('/api/quotes/12')[0]?.init?.body))).toEqual({
      status: 'accepted',
    })
  })

  it('joint le jeton pour lire un PDF, qu’un simple lien n’aurait pas porté', async () => {
    api.storeToken('valide')
    respondWith({ '/api/quotes/12/pdf': [() => new Response('%PDF', { status: 200 })] })

    await api.downloadQuotePdf(12)

    const appel = pathsCalled('/api/quotes/12/pdf')[0]
    expect(new Headers(appel?.init?.headers).get('Authorization')).toBe('Bearer valide')
  })

  it('rejoue le téléchargement d’une facture après renouvellement du jeton', async () => {
    api.storeToken('expire')
    respondWith({
      '/api/quotes/12/invoice.pdf': [
        () => jsonResponse(401, { detail: 'Jeton expiré' }),
        () => new Response('%PDF', { status: 200 }),
      ],
      '/api/auth/refresh': [() => jsonResponse(200, { access_token: 'frais' })],
    })

    // Sans ce rejeu, une facture demandée en fin de session échouait juste après avoir été
    // produite — et rien ne distinguait ce 401 d'un refus de droits.
    await api.downloadInvoicePdf(12)

    expect(pathsCalled('/api/quotes/12/invoice.pdf')).toHaveLength(2)
    expect(api.storedToken()).toBe('frais')
  })

  it('n’abandonne pas le rejeu au premier échec du métré en CSV', async () => {
    api.storeToken('expire')
    respondWith({
      '/api/projects/3/takeoff.csv': [() => jsonResponse(401, { detail: 'Jeton expiré' })],
      '/api/auth/refresh': [() => jsonResponse(401, { detail: 'Session close' })],
    })

    await expect(api.downloadTakeoffCsv(3)).rejects.toMatchObject({ status: 401 })

    // Un seul appel : le rafraîchissement ayant été refusé, on ne rejoue pas dans le vide.
    expect(pathsCalled('/api/projects/3/takeoff.csv')).toHaveLength(1)
    expect(api.storedToken()).toBeNull()
  })

  it('retire un membre par un DELETE et un rôle par un PATCH', async () => {
    respondWith({
      '/api/organizations/4/members/9': [
        () => new Response(null, { status: 204 }),
        () => jsonResponse(200, { user_id: 9, email: 'a@b.fr', role: 'admin' }),
      ],
    })

    await api.removeMember(4, 9)
    await api.updateMemberRole(4, 9, 'admin')

    const appels = pathsCalled('/api/organizations/4/members/9')
    expect(appels[0]?.init?.method).toBe('DELETE')
    expect(appels[1]?.init?.method).toBe('PATCH')
    expect(JSON.parse(String(appels[1]?.init?.body))).toEqual({ role: 'admin' })
  })
})

describe('contrôle de conformité', () => {
  it('n’envoie le mode accessible que lorsqu’il est demandé', async () => {
    const rapport = {
      project_id: 1, thresholds: {}, rooms: [], anomalies: [], counts: {}, warnings: [],
    }
    respondWith({ '/api/projects/1/inspection': [() => jsonResponse(200, rapport)] })

    await api.readInspection(1)
    await api.readInspection(1, true)

    // Deux URL pour une seule requête, c'est deux entrées de cache et deux lignes de journal :
    // le défaut du serveur ne se recopie pas dans l'adresse.
    expect(pathsCalled('/inspection')[0]?.url).not.toContain('accessible')
    expect(pathsCalled('/inspection')[1]?.url).toContain('accessible=true')
  })
})
