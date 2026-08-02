/**
 * États de visibilité des faces (`docs/spec-complete.md` §3.4).
 *
 * Trois positions, comme le demande la spec : visible, transparente, masquée. La position
 * intermédiaire est le vrai apport — masquer complètement un mur fait perdre le repère spatial,
 * alors qu'un mur semi-transparent met une face en avant sans supprimer son contexte.
 */

export type FaceVisibility = 'visible' | 'transparent' | 'hidden'

export const VISIBILITY_CYCLE: FaceVisibility[] = ['visible', 'transparent', 'hidden']

export const VISIBILITY_LABELS: Record<FaceVisibility, string> = {
  visible: 'Visible',
  transparent: 'Transparente',
  hidden: 'Masquée',
}

/** Opacité d'un mur semi-transparent : assez basse pour voir au travers, assez haute pour situer. */
export const TRANSPARENT_OPACITY = 0.25

export function isVisible(state: FaceVisibility | undefined): boolean {
  return state !== 'hidden'
}

export function opacityFor(state: FaceVisibility | undefined): number {
  return state === 'transparent' ? TRANSPARENT_OPACITY : 1
}

export function nextVisibility(state: FaceVisibility | undefined): FaceVisibility {
  const current = state ?? 'visible'
  const index = VISIBILITY_CYCLE.indexOf(current)
  return VISIBILITY_CYCLE[(index + 1) % VISIBILITY_CYCLE.length] as FaceVisibility
}

/** Couleur d'un matériau, avec repli si l'emplacement n'a pas été choisi par l'utilisateur. */
export function materialFor(color: string | null | undefined, fallback: string): string {
  return color && /^#[0-9a-fA-F]{6}$/.test(color) ? color : fallback
}

/**
 * Isole une ou plusieurs faces : les faces retenues restent visibles, les autres deviennent
 * transparentes (spec §3.4 : « on garde les murs voisins comme repère visuel »).
 */
export function isolate(labels: string[], allLabels: string[]): Record<string, FaceVisibility> {
  const retained = new Set(labels)
  return Object.fromEntries(
    allLabels.map((label) => [label, retained.has(label) ? 'visible' : 'transparent']),
  )
}

export function showEverything(allLabels: string[]): Record<string, FaceVisibility> {
  return Object.fromEntries(allLabels.map((label) => [label, 'visible' as FaceVisibility]))
}

/** État sérialisable pour le partage de vue (P8). */
export interface ViewState {
  camera_preset: string
  visible_faces: string[]
  transparent_faces: string[]
  camera_position?: [number, number, number]
}

export function toViewState(
  visibility: Record<string, FaceVisibility>,
  cameraPreset: string,
  cameraPosition?: [number, number, number],
): ViewState {
  const entries = Object.entries(visibility)
  return {
    camera_preset: cameraPreset,
    visible_faces: entries.filter(([, state]) => state === 'visible').map(([label]) => label),
    transparent_faces: entries
      .filter(([, state]) => state === 'transparent')
      .map(([label]) => label),
    ...(cameraPosition ? { camera_position: cameraPosition } : {}),
  }
}

export function fromViewState(
  state: ViewState,
  allLabels: string[],
): Record<string, FaceVisibility> {
  const visible = new Set(state.visible_faces)
  const transparent = new Set(state.transparent_faces)
  return Object.fromEntries(
    allLabels.map((label) => {
      if (transparent.has(label)) return [label, 'transparent' as FaceVisibility]
      if (visible.has(label)) return [label, 'visible' as FaceVisibility]
      // Une face absente des deux listes était masquée au moment du partage.
      return [label, 'hidden' as FaceVisibility]
    }),
  )
}
