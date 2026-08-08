/**
 * L'accueil d'un espace vide.
 *
 * C'est l'écran que **tout** utilisateur voit, et celui d'où personne ne repartait : la chaîne
 * « Aucun projet pour le moment » devant un canevas blanc. Ce qui est vérifié ici, c'est qu'aucun
 * des deux chemins ne mène nulle part — le chantier de démonstration et les gabarits ouvrent
 * réellement l'éditeur, et le seul refus attendu du serveur (409, l'espace n'est plus vide) est
 * traité comme une situation normale plutôt que comme une panne.
 */
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import OnboardingView from '@/views/OnboardingView.vue'

/**
 * `vi.mock` est remonté en tête de fichier : une classe déclarée normalement n'existerait pas
 * encore au moment où la fabrique s'exécute. `vi.hoisted` la remonte avec elle.
 */
const { FakeApiError } = vi.hoisted(() => {
  class FakeApiError extends Error {
    readonly status: number

    constructor(status: number, detail: string) {
      super(detail)
      this.status = status
    }
  }
  return { FakeApiError }
})

const createDemoProject = vi.fn()
const createProject = vi.fn()
const createRoom = vi.fn()
const push = vi.fn()

vi.mock('@/api/client', () => ({
  ApiError: FakeApiError,
  createDemoProject: () => createDemoProject(),
  createProject: (nom: string) => createProject(nom),
  createRoom: (projectId: number, payload: unknown) => createRoom(projectId, payload),
}))

vi.mock('vue-router', () => ({
  RouterLink: { template: '<a><slot /></a>' },
  useRouter: () => ({ push }),
}))

function render() {
  return mount(OnboardingView)
}

function boutonDemonstration(view: ReturnType<typeof render>) {
  return view.get('section[aria-labelledby="titre-demonstration"] button')
}

beforeEach(() => {
  createDemoProject.mockReset()
  createProject.mockReset()
  createRoom.mockReset()
  push.mockReset()
})

describe('chantier de démonstration', () => {
  it('ouvre directement le plan du chantier créé', async () => {
    createDemoProject.mockResolvedValue({ project_id: 42, name: 'Salle de bain — démonstration' })

    await boutonDemonstration(render()).trigger('click')
    await flushPromises()

    expect(push).toHaveBeenCalledWith('/projets/42/plan')
  })

  it('traite le 409 comme un espace déjà rempli, pas comme une erreur', async () => {
    // Deux onglets, ou une démonstration déjà posée : la liste est la bonne destination, et un
    // message d'erreur serait inutilement inquiétant.
    createDemoProject.mockRejectedValue(new FakeApiError(409, 'Cet espace contient déjà…'))

    const view = render()
    await boutonDemonstration(view).trigger('click')
    await flushPromises()

    expect(view.find('[role="alert"]').exists()).toBe(false)
    expect(push).toHaveBeenCalledWith('/projets')
    expect(view.emitted('cree')).toBeTruthy()
  })

  it('signale les autres refus', async () => {
    createDemoProject.mockRejectedValue(new FakeApiError(500, 'Erreur interne'))

    const view = render()
    await boutonDemonstration(view).trigger('click')
    await flushPromises()

    expect(view.get('[role="alert"]').text()).toContain('Erreur interne')
    expect(push).not.toHaveBeenCalled()
  })
})

describe('gabarits de pièces', () => {
  it('propose des formes que personne ne saisit spontanément', () => {
    const texte = render().text()

    expect(texte).toContain('Salle de bain')
    expect(texte).toContain('Cuisine')
    // Le séjour en L est là pour montrer que l'éditeur ne se limite pas au rectangle.
    expect(texte).toContain('Séjour en L')
    expect(texte).toContain('Couloir')
  })

  it('crée le projet puis la pièce, et ouvre le plan', async () => {
    createProject.mockResolvedValue({ id: 7 })
    createRoom.mockResolvedValue({ id: 3 })

    const view = render()
    await view.get('.gabarits li button').trigger('click')
    await flushPromises()

    expect(createProject).toHaveBeenCalledWith('Chantier — salle de bain')
    const [projectId, payload] = createRoom.mock.calls[0] as [number, { polygon: number[][] }]
    expect(projectId).toBe(7)
    expect(payload.polygon).toEqual([[0, 0], [240, 0], [240, 200], [0, 200]])
    expect(push).toHaveBeenCalledWith('/projets/7/plan')
  })

  it('ne laisse pas un polygone dégénéré passer dans un gabarit', () => {
    // Un gabarit à moins de trois sommets ne produirait aucun mur, et l'éditeur s'ouvrirait sur
    // une pièce vide sans que rien ne l'explique.
    const view = render()

    expect(view.findAll('.gabarits li').length).toBeGreaterThanOrEqual(4)
  })

  it('signale un refus du serveur au lieu de rester sur place en silence', async () => {
    createProject.mockRejectedValue(new Error('Quota de chantiers atteint'))

    const view = render()
    await view.get('.gabarits li button').trigger('click')
    await flushPromises()

    expect(view.get('[role="alert"]').text()).toContain('Quota de chantiers atteint')
    expect(createRoom).not.toHaveBeenCalled()
  })
})
