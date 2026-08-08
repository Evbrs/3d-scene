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
 * transparentes (spec §3.4 : « afficher uniquement la face A, ou A+B, ou l'ensemble », et
 * « on garde les murs voisins comme repère visuel »).
 *
 * Isoler un ensemble vide n'isole rien : sans ce garde-fou, décocher la dernière case rendrait
 * toute la pièce transparente, ce qu'aucun clic ne demande jamais.
 */
export function isolate(labels: string[], allLabels: string[]): Record<string, FaceVisibility> {
  if (labels.length === 0) return showEverything(allLabels)
  const retained = new Set(labels)
  return Object.fromEntries(
    allLabels.map((label) => [label, retained.has(label) ? 'visible' : 'transparent']),
  )
}

/** Coche ou décoche une face dans la sélection d'isolement. */
export function toggleSelection(selection: readonly string[], label: string): string[] {
  return selection.includes(label)
    ? selection.filter((entry) => entry !== label)
    : [...selection, label]
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

// --- Logement complet ---------------------------------------------------------------------------

/**
 * Clé d'une face dans l'état d'affichage.
 *
 * Les étiquettes de face (« A », « B », « SOL »…) sont propres à une pièce : en mode logement
 * complet, la face A du séjour et celle de la chambre se confondraient. On préfixe donc par la
 * pièce — mais **seulement** dans ce mode. En vue d'une seule pièce la clé reste l'étiquette nue,
 * qui est ce que le partage de vue sérialise (`ViewState`) et ce que la vue publique relit.
 */
export function faceKey(faceLabel: string, roomId?: number): string {
  return roomId === undefined ? faceLabel : `${roomId}:${faceLabel}`
}

/** L'étiquette lisible d'une clé de face, pièce comprise ou non. */
export function faceLabelOf(key: string): string {
  const separator = key.indexOf(':')
  return separator === -1 ? key : key.slice(separator + 1)
}

/**
 * Ramène un état préfixé par la pièce à des étiquettes nues, pour une pièce donnée.
 *
 * Le partage de vue (`ViewState`) sérialise des étiquettes nues, et la page publique n'affiche
 * qu'une pièce. Partager depuis le mode logement complet enverrait sinon des clés « 12:A » que
 * la page publique ne reconnaîtrait dans aucune de ses deux listes — elle masquerait donc tout.
 */
export function unscope(
  visibility: Record<string, FaceVisibility>,
  roomId: number,
): Record<string, FaceVisibility> {
  const prefix = `${roomId}:`
  return Object.fromEntries(
    Object.entries(visibility)
      .filter(([key]) => key.startsWith(prefix))
      .map(([key, state]) => [key.slice(prefix.length), state]),
  )
}

// --- Murs qui font écran ------------------------------------------------------------------------

/** Ce qu'il faut connaître d'un mur pour décider s'il fait écran. */
export interface WallFacing {
  key: string
  origin: readonly number[]
  rotationY: number
  lengthCm: number
  outwardNormal: readonly number[]
}

/**
 * Milieu du mur, dans le monde.
 *
 * L'origine publiée est le **départ** du mur : sur un mur de six mètres, le tester depuis son
 * départ conclut de travers dès que la caméra est en biais. Le milieu est le bon point d'appui.
 * La rotation `rotation_y` amène l'axe `+X` local sur la direction du mur, d'où `(cos, 0, -sin)`.
 */
export function wallMidpoint(wall: WallFacing): [number, number, number] {
  const half = wall.lengthCm / 2
  return [
    (wall.origin[0] ?? 0) + Math.cos(wall.rotationY) * half,
    wall.origin[1] ?? 0,
    (wall.origin[2] ?? 0) - Math.sin(wall.rotationY) * half,
  ]
}

/**
 * Marge angulaire avant de masquer un mur.
 *
 * Un mur vu par la tranche ne cache rien : le masquer ferait clignoter la pièce au moindre
 * mouvement d'orbite. On exige que la caméra soit franchement du côté sortant.
 */
const FACING_MARGIN = 0.15

/**
 * Vrai si la caméra regarde la face **extérieure** du mur : il s'interpose alors entre elle et
 * l'intérieur de la pièce. `outward_normal` est publié par le backend depuis la vague 1 et
 * n'était lu par personne — c'est exactement à ça qu'il sert.
 */
export function facesTheCamera(wall: WallFacing, cameraPosition: readonly number[]): boolean {
  const middle = wallMidpoint(wall)
  const toCamera = [
    (cameraPosition[0] ?? 0) - middle[0],
    (cameraPosition[1] ?? 0) - middle[1],
    (cameraPosition[2] ?? 0) - middle[2],
  ]
  const distance = Math.hypot(toCamera[0]!, toCamera[1]!, toCamera[2]!)
  if (distance < 1e-6) return false
  const dot =
    ((wall.outwardNormal[0] ?? 0) * toCamera[0]! +
      (wall.outwardNormal[1] ?? 0) * toCamera[1]! +
      (wall.outwardNormal[2] ?? 0) * toCamera[2]!) /
    distance
  return dot > FACING_MARGIN
}

/**
 * L'état d'affichage réellement appliqué, masquage automatique compris.
 *
 * Le masquage est une **surcouche**, pas une écriture : l'utilisateur retrouve ses trois
 * positions intactes dès qu'il coupe l'option ou tourne autour de la pièce. Une face qu'il a
 * explicitement laissée visible peut donc disparaître le temps qu'elle fasse écran, sans que son
 * réglage soit perdu.
 */
export function effectiveVisibility(
  chosen: Record<string, FaceVisibility>,
  walls: readonly WallFacing[],
  cameraPosition: readonly number[] | null,
): Record<string, FaceVisibility> {
  if (!cameraPosition) return chosen
  const applied = { ...chosen }
  walls.forEach((wall) => {
    if (facesTheCamera(wall, cameraPosition)) applied[wall.key] = 'hidden'
  })
  return applied
}

// --- Coupe horizontale --------------------------------------------------------------------------

/**
 * Hauteur de coupe retenue, ou `null` quand la coupe ne retire rien.
 *
 * Couper au ras du plafond revient à ne pas couper : plutôt que de laisser un plan de clipping
 * actif pour rien (il coûte une variante de shader sur chaque matériau), on le débranche.
 */
export function horizontalCut(heightCm: number, ceilingHeightCm: number): number | null {
  if (!Number.isFinite(heightCm) || heightCm <= 0) return null
  return heightCm >= ceilingHeightCm ? null : heightCm
}
