<script setup lang="ts">
/**
 * Éditeur 2D.
 *
 * Orchestre le canevas, la palette et le panneau latéral. Toute écriture passe par le store, qui
 * propage la version du projet pour le verrouillage optimiste. Les gestes multiples passent par
 * la **route de lot** (spec §10, A6) : déplacer quinze meubles en quinze appels serait
 * strictement sériel, chacun invalidant la version que le client détient.
 *
 * C'est ici que vit la pile annuler/refaire, parce que c'est ici qu'on sait à la fois ce qui a
 * été envoyé et ce qui le défait. Le canevas, lui, n'émet que des intentions.
 *
 * Accessibilité (WCAG AAA) : chaque geste souris a son pendant clavier — la palette pose au
 * centre de la pièce, la sélection s'édite au chiffre, les cotes de mur sont des champs
 * numériques, et les raccourcis sont listés dans une aide atteignable au clavier.
 */
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import * as api from '@/api/client'
import type { BatchOperation, BatchResponse } from '@/api/client'
import type {
  Anomaly,
  Face,
  FurnitureType,
  InspectionReport,
  PlanElement,
  Room,
} from '@/api/types'
import PlanCanvas from '@/editor/PlanCanvas.vue'
import {
  type BackgroundPlacement,
  CalibrationError,
  calibrate,
  isBackgroundUrlAllowed,
  isCalibrated,
} from '@/editor/calibration'
import { type WallGeometry, wallGeometries } from '@/editor/drawing'
import type { Point } from '@/editor/geometry'
import { createHistory } from '@/editor/history'
import { formatLengthCm, resizeWall, wallMeasures } from '@/editor/measure'
import {
  deleteOperations,
  describeCount,
  duplicateOperations,
  moveOperations,
  recreateOperations,
  restoreOperations,
  rotateOperations,
} from '@/editor/operations'
import {
  type DragPayload,
  DRAG_MIME,
  dragPayloadOf,
  groupByCategory,
  searchFurniture,
} from '@/editor/palette'
import { type DropTarget, centroid, clampToRoom } from '@/editor/placement'
import {
  type Clipboard,
  copyToClipboard,
  preparePaste,
  pruneSelection,
  roomElements,
} from '@/editor/selection'
import {
  SHORTCUTS,
  arrowStep,
  isTypingTarget,
  matchesCopy,
  matchesDelete,
  matchesDuplicate,
  matchesPaste,
  matchesRedo,
  matchesSelectAll,
  matchesUndo,
} from '@/editor/shortcuts'
import { usePlanStore } from '@/stores/plan'
import InspectorPanel from '@/views/InspectorPanel.vue'

const props = defineProps<{ projectId: string }>()
const store = usePlanStore()
const route = useRoute()
const router = useRouter()

const mode = ref<'navigate' | 'draw' | 'edit' | 'calibrate'>('navigate')
const gridCm = ref(10)
const roomName = ref('Nouvelle pièce')
const catalog = ref<FurnitureType[]>([])
const canvas = ref<InstanceType<typeof PlanCanvas> | null>(null)
/** Erreurs de chargement du catalogue : distinctes des erreurs d'écriture portées par le store. */
const loadError = ref<string | null>(null)
const history = createHistory()
const selection = ref<number[]>([])
const clipboard = ref<Clipboard | null>(null)
const dragPayload = ref<DragPayload | null>(null)
const search = ref('')
const helpOpen = ref(false)
const helpButton = ref<HTMLButtonElement | null>(null)
const helpDialog = ref<HTMLDivElement | null>(null)
/** Calque de fond déverrouillé : ses réglages de calage deviennent modifiables. */
const backgroundUnlocked = ref(false)
/** Message de l'éditeur lui-même (geste refusé, collage partiel), distinct des erreurs du store. */
const notice = ref<string | null>(null)

/** Libellés lisibles : « window » n'a rien à faire dans une interface française. */
const KIND_LABELS: Record<string, string> = {
  window: 'Fenêtre',
  door_hinged: 'Porte battante',
  door_sliding: 'Porte coulissante',
  furniture: 'Mobilier',
}
const FACE_KIND_LABELS: Record<string, string> = {
  wall: 'Mur',
  floor: 'Sol',
  ceiling: 'Plafond',
}

const room = computed(() => store.currentRoom())

/**
 * Faces triées dans l'ordre de lecture du plan : les murs par leur lettre, puis sol et plafond.
 * L'ordre de création en base ne veut rien dire pour l'utilisateur.
 */
const faces = computed<Face[]>(() => {
  const rank = (label: string): number => {
    let value = 0
    for (const character of label) {
      if (!/[A-Z]/.test(character)) return 10 ** 6
      value = value * 26 + (character.charCodeAt(0) - 64)
    }
    return value - 1
  }
  return [...(room.value?.faces ?? [])].sort((a, b) => {
    if (a.kind !== b.kind) {
      const order = { wall: 0, floor: 1, ceiling: 2 }
      return order[a.kind] - order[b.kind]
    }
    return rank(a.label) - rank(b.label)
  })
})

const selectedFace = computed<Face | null>(
  () => faces.value.find((face) => face.label === store.selectedFaceLabel) ?? null,
)

const furnitureNames = computed<Record<number, string>>(() =>
  Object.fromEntries(catalog.value.map((entry) => [entry.id, entry.name])),
)

/** Géométrie des murs de la pièce courante, indexée par face : socle de tous les gestes de lot. */
const walls = computed<WallGeometry[]>(() =>
  room.value ? wallGeometries(room.value.polygon, room.value.faces) : [],
)

const wallIndex = computed(
  () => new Map(walls.value.filter((wall) => wall.face).map((wall) => [wall.face.id, wall])),
)

const moveContext = computed(() => ({
  walls: wallIndex.value,
  polygon: room.value?.polygon ?? [],
}))

const measures = computed(() => (room.value ? wallMeasures(room.value.polygon) : []))

const wallLengthCm = computed(() => {
  const face = selectedFace.value
  if (!face || face.kind !== 'wall' || face.start_x_cm === null) return null
  return Math.round(
    Math.hypot(
      (face.end_x_cm ?? 0) - (face.start_x_cm ?? 0),
      (face.end_y_cm ?? 0) - (face.start_y_cm ?? 0),
    ),
  )
})

/** Tous les éléments de la pièce courante, adossés et libres. */
const allElements = computed<PlanElement[]>(() => (room.value ? roomElements(room.value) : []))

const selectedElements = computed<PlanElement[]>(() =>
  allElements.value.filter((element) => selection.value.includes(element.id)),
)

// Une sélection qui survit à une suppression ou à un changement de pièce désigne des éléments
// disparus : le geste suivant partirait alors en 404 sur des identifiants fantômes. L'affectation
// est conditionnelle — `allElements` est recalculé à chaque écriture, et réécrire un tableau
// identique redessinerait le panneau de sélection pour rien.
watch(allElements, (elements) => {
  const survivants = pruneSelection(
    selection.value,
    elements.map((element) => element.id),
  )
  if (survivants.length !== selection.value.length) selection.value = survivants
})

const draftElement = ref({
  kind: 'window',
  x_offset_cm: 0,
  y_offset_cm: 100,
  width_cm: 90,
  height_cm: 110,
  depth_cm: 12,
  furniture_type_id: null as number | null,
})

/** Valeurs par défaut cohérentes avec le type choisi : une porte part du sol, pas à 1 m. */
watch(
  () => draftElement.value.kind,
  (kind) => {
    if (kind === 'window') {
      Object.assign(draftElement.value, { y_offset_cm: 95, width_cm: 120, height_cm: 110, depth_cm: 12 })
    } else if (kind === 'door_hinged' || kind === 'door_sliding') {
      Object.assign(draftElement.value, { y_offset_cm: 0, width_cm: 90, height_cm: 204, depth_cm: 6 })
    } else {
      Object.assign(draftElement.value, { y_offset_cm: 0 })
    }
  },
)

watch(
  () => draftElement.value.furniture_type_id,
  (id) => {
    const type = catalog.value.find((entry) => entry.id === id)
    if (!type) return
    Object.assign(draftElement.value, {
      width_cm: type.default_width_cm,
      height_cm: type.default_height_cm,
      depth_cm: type.default_depth_cm,
    })
  },
)

/**
 * Sélection reflétée dans l'URL.
 *
 * Sans ça, revenir de la vue 3D, recharger la page ou envoyer le lien à un collègue ramenait
 * systématiquement sur la première pièce du projet, et la face en cours d'examen était perdue.
 */
watch(
  () => [store.selectedRoomId, store.selectedFaceLabel] as const,
  ([roomId, faceLabel]) => {
    const query = { ...route.query, piece: roomId ?? undefined, face: faceLabel ?? undefined }
    if (String(query.piece ?? '') === String(route.query.piece ?? '') &&
      String(query.face ?? '') === String(route.query.face ?? '')) {
      return
    }
    void router.replace({ query })
  },
)

// Sélection lue une fois, avant tout chargement : `load` retient d'office la première pièce, ce
// qui déclencherait la surveillance ci-dessus et réécrirait la chaîne de requête avant qu'on ait
// pu la lire.
const requestedRoomId = Number(route.query.piece)
const requestedFace = typeof route.query.face === 'string' ? route.query.face : ''

function applyRequestedSelection(): void {
  if (store.project?.rooms.some((candidate) => candidate.id === requestedRoomId)) {
    store.selectedRoomId = requestedRoomId
  }
  if (requestedFace !== '') store.selectedFaceLabel = requestedFace
}

onMounted(async () => {
  window.addEventListener('keydown', onKeydown)
  await store.load(Number(props.projectId))
  applyRequestedSelection()
  try {
    catalog.value = (await api.listFurnitureTypes()).items
  } catch (caught) {
    // Le catalogue n'est pas vital pour tracer un plan : on signale sans bloquer l'éditeur.
    loadError.value = `Catalogue de mobilier indisponible : ${messageOf(caught)}`
  }
})

onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
  releaseLocalBackground()
})

function messageOf(caught: unknown): string {
  return caught instanceof Error ? caught.message : String(caught)
}

// --- Écriture, pile d'annulation -----------------------------------------------------------------

/**
 * Envoie un lot et empile son inverse.
 *
 * Un lot refusé **vide la pile** : les inverses mémorisés décrivent un état que le serveur n'a
 * plus, et les rejouer écraserait le travail de quelqu'un d'autre. Perdre l'historique après un
 * conflit est le seul comportement honnête.
 */
async function runBatch(operations: BatchOperation[]): Promise<BatchResponse[] | null> {
  notice.value = null
  const responses = await store.writeBatch(operations)
  if (!responses) history.clear()
  return responses
}

/**
 * Même chose, mais qui **lève** en cas de refus.
 *
 * C'est la version que doivent employer les mouvements de pile. `history.annuler()` juge de la
 * réussite sur l'absence d'exception : une inverse qui échouerait en silence verrait son entrée
 * glisser dans la branche « refaire », et l'interface proposerait de rejouer un geste que le
 * serveur vient de refuser — exactement ce que le vidage après conflit doit empêcher.
 */
async function requireBatch(operations: BatchOperation[]): Promise<BatchResponse[]> {
  const responses = await runBatch(operations)
  if (!responses) throw new Error(store.error ?? 'écriture refusée par le serveur')
  return responses
}

function createdIdsOf(responses: BatchResponse[]): number[] {
  return responses.flatMap((response) =>
    response.results
      .filter((result) => result.status === 'created' && result.element_id !== null)
      .map((result) => result.element_id as number),
  )
}

/** Geste réversible dont l'inverse est connu d'avance : déplacement, rotation, redimensionnement. */
async function commitUpdate(
  libelle: string,
  forward: BatchOperation[],
  backward: BatchOperation[],
): Promise<void> {
  if (forward.length === 0) return
  if (!(await runBatch(forward))) return
  history.push({
    libelle,
    refaire: () => requireBatch(forward),
    annuler: () => requireBatch(backward),
  })
}

/**
 * Création. L'inverse ne peut être écrit qu'après coup : les identifiants viennent du serveur.
 *
 * Ils sont donc relus à chaque rejeu — refaire une création produit de nouveaux identifiants, et
 * une annulation qui viserait ceux du premier essai supprimerait des éléments déjà disparus.
 */
async function commitCreate(libelle: string, operations: BatchOperation[]): Promise<void> {
  if (operations.length === 0) return
  let created: number[] = []

  const responses = await runBatch(operations)
  if (!responses) return
  created = createdIdsOf(responses)

  history.push({
    libelle,
    refaire: async () => {
      created = createdIdsOf(await requireBatch(operations))
    },
    annuler: () => requireBatch(deleteOperations(created)),
  })
}

/**
 * Suppression. Annuler recrée des éléments **équivalents**, pas les mêmes : le serveur attribue
 * de nouveaux identifiants. Ce sont eux que le prochain « refaire » supprimera.
 */
async function commitDelete(libelle: string, elements: PlanElement[]): Promise<void> {
  if (elements.length === 0) return
  let living = elements.map((element) => element.id)

  if (!(await runBatch(deleteOperations(living)))) return
  selection.value = []

  history.push({
    libelle,
    refaire: () => requireBatch(deleteOperations(living)),
    annuler: async () => {
      living = createdIdsOf(await requireBatch(recreateOperations(elements)))
    },
  })
}

/**
 * Les deux mouvements de pile.
 *
 * L'échec est avalé ici, et c'est volontaire : le store a déjà posé le message du serveur, et la
 * pile a déjà été vidée par `runBatch`. Une exception qui remonterait n'apporterait qu'une trace
 * dans la console à un utilisateur qui a déjà lu ce qui s'est passé.
 */
async function undo(): Promise<void> {
  try {
    const entry = await history.annuler()
    if (entry) notice.value = `Annulé : ${entry.libelle}`
  } catch {
    notice.value = null
  }
}

async function redo(): Promise<void> {
  try {
    const entry = await history.refaire()
    if (entry) notice.value = `Refait : ${entry.libelle}`
  } catch {
    notice.value = null
  }
}

// --- Gestes du canevas ---------------------------------------------------------------------------

async function addRoom(): Promise<void> {
  const created = await store.write(
    (version) => api.createRoom(Number(props.projectId), { name: roomName.value, version }),
    (created) => store.applyRoom(created),
  )
  if (!created) return
  store.selectedRoomId = created.id
  store.selectedFaceLabel = null
  mode.value = 'draw'
}

/**
 * Enregistre un contour.
 *
 * Un raccourcissement qui supprimerait des murs porteurs d'éléments est refusé par le serveur
 * (409, `code` destructif). On ne force qu'après confirmation explicite : perdre des meubles sur
 * un glisser de souris serait la pire chose que puisse faire un éditeur de plan.
 */
async function savePolygon(polygon: number[][]): Promise<void> {
  const roomId = room.value!.id
  const previous = room.value!.polygon.map((vertex) => [...vertex])

  await store.write(
    (version) => api.updateRoom(roomId, { polygon, version }),
    (updated) => store.applyRoom(updated),
  )

  if (store.conflictKind === 'destructive') {
    if (!window.confirm(`${store.error}\n\nConfirmer la suppression ?`)) {
      // Le refus n'a rien écrit côté serveur ; on relit pour rendre au canvas le contour réel.
      await store.load(Number(props.projectId))
      return
    }
    const forced = await store.write(
      (version) => api.updateRoom(roomId, { polygon, version, force: true }),
      (updated) => store.applyRoom(updated),
    )
    // Forcer a détruit des murs et ce qu'ils portaient : aucun inverse ne les ramène, et
    // prétendre le contraire serait mentir. L'historique repart de zéro.
    history.clear()
    if (!forced) return
    return
  }
  if (store.error !== null) {
    history.clear()
    return
  }

  history.push({
    libelle: 'modifier le contour',
    refaire: () => requirePolygon(roomId, polygon),
    annuler: () => requirePolygon(roomId, previous),
  })
}

/** Écriture de contour qui lève en cas de refus — voir `requireBatch` pour le pourquoi. */
async function requirePolygon(roomId: number, polygon: number[][]): Promise<void> {
  const updated = await store.write(
    (version) => api.updateRoom(roomId, { polygon, version }),
    (result) => store.applyRoom(result),
  )
  if (!updated) {
    history.clear()
    throw new Error(store.error ?? 'contour refusé par le serveur')
  }
}

/**
 * Le serveur remplace le revêtement au lieu de le fusionner : n'envoyer que la couleur effaçait
 * la matière, les dimensions d'unité et le motif de pose choisis auparavant.
 */
async function saveCovering(color: string): Promise<void> {
  const face = selectedFace.value!
  const previous = { ...face.covering }
  await store.write(
    (version) => api.updateFaceCovering(face.id, { ...face.covering, color }, version),
    (updated) => store.applyFace(updated),
  )
  if (store.error !== null) return history.clear()

  const write = async (covering: typeof previous): Promise<void> => {
    const updated = await store.write(
      (version) => api.updateFaceCovering(face.id, covering, version),
      (result) => store.applyFace(result),
    )
    if (!updated) {
      history.clear()
      throw new Error(store.error ?? 'revêtement refusé par le serveur')
    }
  }

  history.push({
    libelle: `revêtement de la face ${face.label}`,
    refaire: () => write({ ...previous, color }),
    annuler: () => write(previous),
  })
}

async function addElement(): Promise<void> {
  const payload = { ...draftElement.value }
  if (payload.kind !== 'furniture') payload.furniture_type_id = null
  await commitCreate(`poser ${KIND_LABELS[payload.kind] ?? payload.kind}`, [
    { op: 'create_face_element', face_id: selectedFace.value!.id, element: payload },
  ])
}

async function removeElement(elementId: number): Promise<void> {
  const element = allElements.value.find((candidate) => candidate.id === elementId)
  if (element) await commitDelete('retirer un élément', [element])
}

/** Dépose depuis la palette : c'est le point où l'ancrage se décide (spec §10, A4). */
async function onDropFurniture(drop: { payload: DragPayload; target: DropTarget }): Promise<void> {
  dragPayload.value = null
  if (drop.target.kind === 'refuse') {
    notice.value = `Dépose refusée : ${drop.target.raison}`
    return
  }

  const shape = {
    kind: 'furniture',
    furniture_type_id: drop.payload.furnitureTypeId,
    width_cm: drop.payload.width_cm,
    height_cm: drop.payload.height_cm,
    depth_cm: drop.payload.depth_cm,
  }

  const operation: BatchOperation =
    drop.target.kind === 'face'
      ? {
          op: 'create_face_element',
          face_id: drop.target.faceId,
          element: { ...shape, x_offset_cm: drop.target.xOffsetCm, y_offset_cm: 0 },
        }
      : {
          op: 'create_room_element',
          room_id: drop.target.roomId,
          element: { ...shape, pos_x_cm: drop.target.posXCm, pos_y_cm: drop.target.posYCm },
        }

  await commitCreate(`poser ${drop.payload.name}`, [operation])
}

/** Pose au clavier : le pendant strict du glisser, au centre de la pièce. */
async function placeAtCentre(type: FurnitureType): Promise<void> {
  const current = room.value
  if (!current) return
  const centre = clampToRoom(
    centroid(current.polygon),
    current.polygon,
    type.default_width_cm,
    type.default_depth_cm,
    0,
  )
  if (!centre) {
    notice.value = `${type.name} ne tient pas dans ${current.name}.`
    return
  }
  await commitCreate(`poser ${type.name}`, [
    {
      op: 'create_room_element',
      room_id: current.id,
      element: {
        kind: 'furniture',
        furniture_type_id: type.id,
        width_cm: type.default_width_cm,
        height_cm: type.default_height_cm,
        depth_cm: type.default_depth_cm,
        pos_x_cm: centre.x,
        pos_y_cm: centre.y,
      },
    },
  ])
}

/**
 * Rotation ou redimensionnement à la poignée.
 *
 * Les champs absents sont **omis** et non envoyés à `undefined` : le serveur refuse un champ nul
 * sans signification (`PartialUpdate`), et « ne touche pas » s'exprime par l'absence.
 */
async function onTransformElement(change: {
  id: number
  rotation_deg?: number
  width_cm?: number
  depth_cm?: number
}): Promise<void> {
  const element = allElements.value.find((candidate) => candidate.id === change.id)
  if (!element) return

  const changes: Record<string, number> = {}
  if (change.rotation_deg !== undefined) changes.rotation_deg = change.rotation_deg
  if (change.width_cm !== undefined) changes.width_cm = change.width_cm
  if (change.depth_cm !== undefined) changes.depth_cm = change.depth_cm
  if (Object.keys(changes).length === 0) return

  await commitUpdate(
    change.rotation_deg === undefined ? 'redimensionner un meuble' : 'tourner un meuble',
    [{ op: 'update_element', element_id: change.id, changes }],
    restoreOperations([element]),
  )
}

async function moveSelection(delta: Point): Promise<void> {
  await moveElements(selectedElements.value, delta)
}

/**
 * Déplace les éléments désignés.
 *
 * Le canevas rend les identifiants qu'il a effectivement déplacés plutôt que la sélection
 * courante : elle peut avoir changé pendant le glisser — un Maj-clic, une réponse serveur qui
 * retire un élément — et on déplacerait alors autre chose que ce que l'utilisateur a saisi.
 */
async function moveElements(elements: PlanElement[], delta: Point): Promise<void> {
  const forward = moveOperations(elements, delta, moveContext.value)
  if (forward.length === 0) {
    notice.value = 'Aucun de ces éléments ne peut aller plus loin dans cette direction.'
    return
  }
  const touched = new Set(forward.map((operation) => ('element_id' in operation ? operation.element_id : 0)))
  await commitUpdate(
    `déplacer ${describeCount(forward.length, 'élément', 'éléments')}`,
    forward,
    restoreOperations(elements.filter((element) => touched.has(element.id))),
  )
}

/** Décalage saisi au chiffre : le pendant clavier du glisser, sur un seul axe à la fois. */
async function shiftBy(event: Event, axis: 'x' | 'y'): Promise<void> {
  const field = event.target as HTMLInputElement
  const value = Number(field.value)
  field.value = '0'
  if (!Number.isFinite(value) || value === 0) return
  await moveSelection(axis === 'x' ? { x: value, y: 0 } : { x: 0, y: value })
}

async function rotateSelection(stepDeg: number): Promise<void> {
  const elements = selectedElements.value
  const forward = rotateOperations(elements, stepDeg)
  if (forward.length === 0) {
    notice.value = 'Seul un meuble posé au sol peut être tourné : un élément adossé suit son mur.'
    return
  }
  await commitUpdate(
    `tourner ${describeCount(forward.length, 'meuble', 'meubles')}`,
    forward,
    rotateOperations(elements, -stepDeg),
  )
}

async function deleteSelection(): Promise<void> {
  const elements = selectedElements.value
  if (elements.length === 0) return
  await commitDelete(`supprimer ${describeCount(elements.length, 'élément', 'éléments')}`, elements)
}

/** Duplication sur place, décalée d'un pas de grille pour que la copie soit visible. */
async function duplicateSelection(): Promise<void> {
  const elements = selectedElements.value
  if (elements.length === 0) return
  const delta = { x: gridCm.value * 2, y: gridCm.value * 2 }
  const operations = duplicateOperations(elements, delta, moveContext.value)
  if (operations.length === 0) {
    notice.value = 'La copie ne tient pas dans la pièce à cet endroit.'
    return
  }
  await commitCreate(`dupliquer ${describeCount(operations.length, 'élément', 'éléments')}`, operations)
}

function copySelection(): void {
  if (!room.value || selection.value.length === 0) return
  clipboard.value = copyToClipboard(room.value, selection.value)
  notice.value = `${describeCount(clipboard.value.elements.length, 'élément copié', 'éléments copiés')}.`
}

async function pasteClipboard(): Promise<void> {
  const source = clipboard.value
  const target = room.value
  if (!source || !target) return

  // Le report sur la pièce cible et le décalage sont deux choses distinctes : le premier décide
  // de l'ancrage, le second seulement de l'endroit. `duplicateOperations` sait décaler les deux
  // repères et ramener une copie qui déborde, ce que le collage n'a pas à savoir refaire.
  const delta = { x: gridCm.value * 2, y: gridCm.value * 2 }
  const outcome = preparePaste(source, { room: target })
  const operations = duplicateOperations(outcome.elements, delta, moveContext.value)

  if (outcome.refuses.length > 0) {
    notice.value = `${describeCount(outcome.refuses.length, 'élément non collé', 'éléments non collés')} : ${outcome.refuses[0]?.raison}`
  }
  await commitCreate(`coller ${describeCount(operations.length, 'élément', 'éléments')}`, operations)
}

/** Corrige la longueur d'un mur au chiffre relevé — le geste central d'un relevé au laser. */
async function applyWallLength(index: number, value: number): Promise<void> {
  const current = room.value
  if (!current || !Number.isFinite(value) || value <= 0) return
  await savePolygon(resizeWall(current.polygon, index, Math.round(value)))
}

// --- Fond de plan --------------------------------------------------------------------------------

const backgroundUrlField = ref('')
/** Aperçu local : un fichier choisi sur la tablette, tant qu'aucune route de téléversement n'existe. */
const localBackgroundUrl = ref<string | null>(null)
const calibrationPoints = ref<Point[]>([])
const calibrationDistance = ref<number | null>(null)

const background = computed<BackgroundPlacement | null>(() => {
  const current = room.value
  if (!current) return null
  return {
    scaleCmPerPx: current.background_scale_cm_per_px,
    offsetXCm: current.background_offset_x_cm,
    offsetYCm: current.background_offset_y_cm,
    rotationDeg: current.background_rotation_deg,
    opacity: current.background_opacity,
  }
})

/** L'aperçu local prime : c'est l'image que l'utilisateur vient de choisir, sous ses yeux. */
const shownBackgroundUrl = computed(() => localBackgroundUrl.value ?? room.value?.background_url ?? null)

function releaseLocalBackground(): void {
  if (localBackgroundUrl.value) URL.revokeObjectURL(localBackgroundUrl.value)
  localBackgroundUrl.value = null
}

function chooseLocalBackground(event: Event): void {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  releaseLocalBackground()
  localBackgroundUrl.value = URL.createObjectURL(file)
  notice.value =
    'Aperçu local : l’image n’est pas enregistrée (aucune route de téléversement). Le calibrage, lui, est enregistré.'
}

async function saveBackground(changes: Partial<Room>): Promise<void> {
  const current = room.value
  if (!current) return
  await store.write(
    (version) => api.updateRoom(current.id, { ...changes, version }),
    (updated) => store.applyRoom(updated),
  )
}

async function applyBackgroundUrl(): Promise<void> {
  const url = backgroundUrlField.value.trim()
  if (url !== '' && !isBackgroundUrlAllowed(url)) {
    notice.value =
      'Adresse refusée : attendu un chemin du site commençant par « / » ou une URL « https:// ».'
    return
  }
  // L'aperçu local masquerait l'image qu'on vient d'enregistrer : le fond affiché doit être
  // celui que le projet garde, sinon on calibre sur une image et on en publie une autre.
  releaseLocalBackground()
  await saveBackground({ background_url: url === '' ? null : url })
}

function onCalibrationPoints(points: Point[]): void {
  calibrationPoints.value = points
}

// Deux points à moitié posés qui ressortiraient à la prochaine ouverture de l'outil donneraient
// une échelle absurde sans que personne ne comprenne d'où elle vient.
watch(mode, (courant) => {
  if (courant !== 'calibrate') calibrationPoints.value = []
})

/** Applique le calibrage à deux clics : l'échelle du fond se déduit d'une distance connue. */
async function applyCalibration(): Promise<void> {
  const [a, b] = calibrationPoints.value
  const placement = background.value
  const distance = calibrationDistance.value
  if (!a || !b || !placement || distance === null) {
    notice.value = 'Cliquez deux points sur le fond, puis saisissez la distance réelle.'
    return
  }
  try {
    const calibrated = calibrate(placement, a, b, distance)
    await saveBackground({
      background_scale_cm_per_px: calibrated.scaleCmPerPx,
      background_offset_x_cm: calibrated.offsetXCm,
      background_offset_y_cm: calibrated.offsetYCm,
    })
    calibrationPoints.value = []
    mode.value = 'navigate'
    notice.value = `Fond calibré à ${calibrated.scaleCmPerPx?.toFixed(3)} cm par pixel.`
  } catch (caught) {
    notice.value =
      caught instanceof CalibrationError ? `Calibrage impossible : ${caught.message}` : messageOf(caught)
  }
}

// --- Clavier ---------------------------------------------------------------------------------------

/**
 * Raccourcis globaux.
 *
 * L'écoute est posée sur `window` parce qu'un canevas Konva ne prend pas le focus : le filtre
 * `isTypingTarget` est donc obligatoire, sans quoi taper « 250 » dans un champ de cote
 * supprimerait la sélection au passage de la touche Retour arrière.
 */
function onKeydown(event: KeyboardEvent): void {
  if (isTypingTarget(event.target)) return

  if (matchesUndo(event)) {
    event.preventDefault()
    void undo()
    return
  }
  if (matchesRedo(event)) {
    event.preventDefault()
    void redo()
    return
  }
  if (matchesSelectAll(event)) {
    event.preventDefault()
    selection.value = allElements.value.map((element) => element.id)
    return
  }
  if (matchesCopy(event)) {
    event.preventDefault()
    copySelection()
    return
  }
  if (matchesPaste(event)) {
    event.preventDefault()
    void pasteClipboard()
    return
  }
  if (matchesDuplicate(event)) {
    event.preventDefault()
    void duplicateSelection()
    return
  }
  if (mode.value === 'draw') return

  if (matchesDelete(event) && selection.value.length > 0) {
    event.preventDefault()
    void deleteSelection()
    return
  }
  if (event.key === 'Escape') {
    selection.value = []
    return
  }
  if (event.key.toLowerCase() === 'r' && selection.value.length > 0) {
    event.preventDefault()
    void rotateSelection(event.shiftKey ? -15 : 15)
    return
  }

  const step = arrowStep(event)
  if (step && selection.value.length > 0) {
    event.preventDefault()
    const amplitude = gridCm.value * (event.shiftKey ? 10 : 1)
    void moveSelection({ x: step.dx * amplitude, y: step.dy * amplitude })
  }
}

// --- Palette ---------------------------------------------------------------------------------------

const paletteGroups = computed(() => groupByCategory(searchFurniture(catalog.value, search.value)))

function onDragStart(type: FurnitureType, event: DragEvent): void {
  const payload = dragPayloadOf(type)
  dragPayload.value = payload
  event.dataTransfer?.setData(DRAG_MIME, JSON.stringify(payload))
  if (event.dataTransfer) event.dataTransfer.effectAllowed = 'copy'
}

// --- Export ------------------------------------------------------------------------------------

/**
 * Export PDF du dossier (plan coté et une élévation par mur).
 *
 * Le fichier est produit par un worker Celery : on demande, on sonde, on télécharge. Le blob est
 * récupéré par le client HTTP et non par un lien direct, parce que la route exige l'en-tête
 * `Authorization` que le navigateur ne joint pas à une navigation.
 */
const exporting = ref(false)
const exportMessage = ref<string | null>(null)
const exportError = ref<string | null>(null)

async function exportPdf(): Promise<void> {
  if (exporting.value) return
  exporting.value = true
  exportError.value = null
  exportMessage.value = 'Génération du dossier PDF en cours…'
  const projectId = Number(props.projectId)

  try {
    const accepted = await api.requestPdfExport(projectId)
    const produced = await api.waitForPdfExport(projectId, accepted.task_id)
    const blob = await api.downloadExport(projectId, produced.filename)

    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = produced.filename
    link.click()
    // Révoqué au tour suivant seulement : révoquer dans la foulée du clic annule le
    // téléchargement avant qu'il ait commencé sur plusieurs navigateurs.
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)

    exportMessage.value = `Dossier téléchargé (${Math.round(produced.size_bytes / 1024)} Ko).`
  } catch (caught) {
    exportMessage.value = null
    exportError.value = `Export impossible : ${messageOf(caught)}`
  } finally {
    exporting.value = false
  }
}

/**
 * Contrôle de conformité du plan (`docs/strategie-produit.md` §3.8).
 *
 * L'analyse n'est **pas** lancée au montage ni à chaque écriture : elle relit le scene graph
 * complet, et la relancer à chaque meuble déplacé ferait payer un calcul de plan entier à un geste
 * de souris. C'est l'utilisateur qui demande, et le rapport porte la version sur laquelle il a été
 * établi — un rapport plus vieux que le plan doit se dire tel, pas se taire.
 */
const inspection = ref<InspectionReport | null>(null)
const inspectionVersion = ref<number | null>(null)
const inspecting = ref(false)
const inspectionError = ref<string | null>(null)
const accessible = ref(false)

const inspectionStale = computed(
  () => inspection.value !== null && inspectionVersion.value !== (store.project?.version ?? null),
)

async function inspect(): Promise<void> {
  if (inspecting.value) return
  inspecting.value = true
  inspectionError.value = null
  try {
    inspection.value = await api.readInspection(Number(props.projectId), accessible.value)
    inspectionVersion.value = store.project?.version ?? null
  } catch (caught) {
    inspection.value = null
    inspectionError.value = `Contrôle impossible : ${messageOf(caught)}`
  } finally {
    inspecting.value = false
  }
}

/**
 * Amène le plan sur l'anomalie cliquée.
 *
 * Le panneau ne sait pas recentrer — il ne connaît ni Konva ni la pièce courante ; c'est ici que
 * les deux se rencontrent. L'ordre compte : changer de pièce d'abord, laisser le canevas se
 * redessiner, puis seulement recentrer, sinon le déplacement s'applique à la pièce qu'on quitte.
 */
async function recentrerSur(anomaly: Anomaly): Promise<void> {
  if (anomaly.room_id !== null && anomaly.room_id !== store.selectedRoomId) {
    store.selectedRoomId = anomaly.room_id
    await nextTick()
  }
  // Les identifiants viennent du serveur : on ne garde que ceux que la pièce affichée porte
  // vraiment, sinon la sélection désigne des fantômes et le geste suivant part en 404.
  const connus = new Set(allElements.value.map((element) => element.id))
  const cibles = anomaly.element_ids.filter((id) => connus.has(id))
  if (cibles.length) selection.value = cibles
  if (anomaly.focus) canvas.value?.centerOn({ x: anomaly.focus[0], y: anomaly.focus[1] })
}

const savedLabel = computed(() => {
  if (store.saving) return 'Enregistrement…'
  if (!store.savedAt) return 'Aucune modification enregistrée'
  return `Enregistré à ${store.savedAt.toLocaleTimeString('fr-FR')}`
})

function describe(element: PlanElement): string {
  const kind = KIND_LABELS[element.kind] ?? element.kind
  const name =
    element.kind === 'furniture' && element.furniture_type_id
      ? (furnitureNames.value[element.furniture_type_id] ?? kind)
      : kind
  const cotes = `${Math.round(element.width_cm)} × ${Math.round(element.height_cm)} cm`
  if (element.face_id === null) {
    return `${name} · ${cotes} · posé en (${Math.round(element.pos_x_cm ?? 0)}, ${Math.round(element.pos_y_cm ?? 0)})`
  }
  return `${name} · ${cotes} à ${Math.round(element.x_offset_cm)} cm`
}

function closeHelp(): void {
  helpOpen.value = false
  // Le focus revient d'où il vient : sans ça, fermer l'aide au clavier le renvoie en tête de
  // document et l'utilisateur reprend sa navigation depuis le début de la page.
  helpButton.value?.focus()
}

// Le focus entre dans la boîte de dialogue à l'ouverture : un `aria-modal` que le clavier n'a
// pas atteint est un piège — le lecteur d'écran annonce un dialogue et la frappe continue
// derrière lui.
watch(helpOpen, async (open) => {
  if (!open) return
  await nextTick()
  helpDialog.value?.focus()
})
</script>

<template>
  <section v-if="store.project">
    <header class="entete">
      <div>
        <h1>{{ store.project.name }}</h1>
        <p class="sous-titre">
          Plan 2D · version {{ store.project.version }}
        </p>
      </div>
      <div class="entete-actions">
        <p
          class="etat-enregistrement"
          :data-etat="store.saving ? 'en-cours' : 'repos'"
          aria-live="polite"
        >
          {{ savedLabel }}
        </p>
        <button
          type="button"
          :disabled="!history.peutAnnuler.value"
          :title="history.libelleAnnuler.value ? `Annuler : ${history.libelleAnnuler.value}` : 'Rien à annuler'"
          @click="undo"
        >
          ↶ Annuler
        </button>
        <button
          type="button"
          :disabled="!history.peutRefaire.value"
          :title="history.libelleRefaire.value ? `Refaire : ${history.libelleRefaire.value}` : 'Rien à refaire'"
          @click="redo"
        >
          ↷ Refaire
        </button>
        <button
          ref="helpButton"
          type="button"
          aria-haspopup="dialog"
          @click="helpOpen = true"
        >
          ? Raccourcis
        </button>
        <button
          type="button"
          :disabled="exporting"
          :aria-busy="exporting"
          @click="exportPdf"
        >
          {{ exporting ? 'Génération…' : '📄 Exporter en PDF' }}
        </button>
        <RouterLink
          class="bouton-lien"
          :to="`/projets/${props.projectId}/vue-3d`"
        >
          Voir en 3D →
        </RouterLink>
      </div>
    </header>

    <p
      v-if="exportMessage"
      class="message export-bloc"
      aria-live="polite"
    >
      {{ exportMessage }}
    </p>
    <p
      v-if="exportError"
      class="message erreur-bloc"
      role="alert"
    >
      {{ exportError }}
    </p>

    <p
      v-if="store.conflictKind === 'stale'"
      class="message erreur-bloc"
      role="alert"
    >
      Le plan a été modifié ailleurs. Vos dernières modifications n'ont pas été enregistrées, et
      l'historique d'annulation a été vidé : il décrivait un plan qui n'existe plus.
      <button
        type="button"
        @click="store.replayRefused()"
      >
        Recharger et réappliquer
      </button>
      <button
        type="button"
        @click="store.load(Number(props.projectId))"
      >
        Recharger sans réappliquer
      </button>
    </p>
    <p
      v-else-if="store.error"
      class="message erreur-bloc"
      role="alert"
    >
      {{ store.error }}
    </p>
    <p
      v-if="loadError"
      class="message erreur-bloc"
      role="alert"
    >
      {{ loadError }}
    </p>
    <p
      v-if="notice"
      class="message export-bloc"
      aria-live="polite"
    >
      {{ notice }}
    </p>

    <div class="disposition">
      <div>
        <div
          class="barre"
          role="toolbar"
          aria-label="Outils du plan"
        >
          <label for="piece">Pièce</label>
          <select
            id="piece"
            v-model.number="store.selectedRoomId"
            @change="store.selectedFaceLabel = null; selection = []"
          >
            <option
              v-for="candidate in store.project.rooms"
              :key="candidate.id"
              :value="candidate.id"
            >
              {{ candidate.name }}
            </option>
          </select>

          <span class="separateur" />

          <button
            type="button"
            :aria-pressed="mode === 'draw'"
            :disabled="!room"
            @click="mode = mode === 'draw' ? 'navigate' : 'draw'"
          >
            ✏️ Tracer
          </button>
          <button
            type="button"
            :aria-pressed="mode === 'edit'"
            :disabled="!room"
            @click="mode = mode === 'edit' ? 'navigate' : 'edit'"
          >
            ⇕ Déformer
          </button>
          <button
            type="button"
            :aria-pressed="mode === 'calibrate'"
            :disabled="!room || !shownBackgroundUrl"
            @click="mode = mode === 'calibrate' ? 'navigate' : 'calibrate'"
          >
            📐 Calibrer le fond
          </button>
          <button
            type="button"
            :disabled="!room"
            @click="canvas?.fit()"
          >
            ⤢ Recadrer
          </button>

          <span class="separateur" />

          <label for="grille">Grille</label>
          <select
            id="grille"
            v-model.number="gridCm"
          >
            <option :value="1">
              1 cm
            </option>
            <option :value="5">
              5 cm
            </option>
            <option :value="10">
              10 cm
            </option>
            <option :value="25">
              25 cm
            </option>
          </select>
        </div>

        <PlanCanvas
          v-if="room"
          ref="canvas"
          :key="room.id"
          :draft-key="`${props.projectId}:${room.id}`"
          :polygon="room.polygon"
          :faces="faces"
          :free-elements="room.free_elements"
          :room-id="room.id"
          :room-name="room.name"
          :wall-thickness-cm="room.wall_thickness_cm"
          :selected-face-label="store.selectedFaceLabel"
          :selection="selection"
          :mode="mode"
          :grid-cm="gridCm"
          :furniture-names="furnitureNames"
          :background-url="shownBackgroundUrl"
          :background="background"
          :drag-payload="dragPayload"
          @update:polygon="savePolygon"
          @update:selection="selection = $event"
          @select-face="store.selectedFaceLabel = $event"
          @select-element="store.selectedFaceLabel = faces.find((f) => f.id === $event.face_id)?.label ?? store.selectedFaceLabel"
          @finish-drawing="mode = 'navigate'"
          @drop-furniture="onDropFurniture"
          @move-elements="moveElements(allElements.filter((element) => $event.ids.includes(element.id)), $event.delta)"
          @transform-element="onTransformElement"
          @calibrate="onCalibrationPoints"
        />
        <p
          v-else
          class="vide"
        >
          Ce projet n'a pas encore de pièce. Ajoutez-en une pour commencer à tracer.
        </p>

        <form
          class="ajout"
          @submit.prevent="addRoom"
        >
          <div class="champ">
            <label for="nom-piece">Ajouter une pièce</label>
            <input
              id="nom-piece"
              v-model="roomName"
              type="text"
              required
            >
          </div>
          <button type="submit">
            Ajouter
          </button>
        </form>

        <section
          v-if="room && measures.length"
          class="cotes"
          aria-labelledby="titre-cotes"
        >
          <h2 id="titre-cotes">
            Cotes des murs
          </h2>
          <p class="sous-titre">
            Un plan de rénovation se saisit au mètre laser : corrigez la mesure, le sommet suit.
          </p>
          <ul class="liste-cotes">
            <li
              v-for="measure in measures"
              :key="measure.index"
            >
              <label :for="`cote-${measure.index}`">Mur {{ measure.label }}</label>
              <input
                :id="`cote-${measure.index}`"
                type="number"
                min="1"
                step="1"
                :value="Math.round(measure.lengthCm)"
                @change="applyWallLength(measure.index, Number(($event.target as HTMLInputElement).value))"
              >
              <span class="unite">cm</span>
            </li>
          </ul>
        </section>
      </div>

      <aside aria-label="Propriétés">
        <h2>Palette</h2>
        <div class="champ">
          <label for="recherche-mobilier">Rechercher un meuble</label>
          <input
            id="recherche-mobilier"
            v-model="search"
            type="search"
            placeholder="évier, lit, cuisine…"
            autocomplete="off"
          >
        </div>
        <p
          v-if="paletteGroups.length === 0"
          class="vide"
        >
          Aucun meuble ne correspond à « {{ search }} ».
        </p>
        <div
          v-for="group in paletteGroups"
          :key="group.category"
          class="groupe"
        >
          <h3>{{ group.label }}</h3>
          <ul class="palette">
            <li
              v-for="entry in group.items"
              :key="entry.id"
            >
              <!-- Poignée de glisser, masquée au lecteur d'écran : elle n'est ni focalisable ni
                   actionnable au clavier, et un `aria-label` posé dessus annoncerait une cible
                   qu'on ne peut pas atteindre. Le chemin clavier est le bouton « Poser ». -->
              <span
                class="poignee"
                draggable="true"
                aria-hidden="true"
                :title="`Glisser ${entry.name} sur le plan`"
                @dragstart="onDragStart(entry, $event)"
                @dragend="dragPayload = null"
              >⠿</span>
              <span class="nom">{{ entry.name }}</span>
              <span class="cote">{{ Math.round(entry.default_width_cm) }}×{{ Math.round(entry.default_depth_cm) }}</span>
              <button
                type="button"
                :disabled="!room"
                :title="`Poser ${entry.name} au centre de la pièce`"
                @click="placeAtCentre(entry)"
              >
                Poser
              </button>
            </li>
          </ul>
        </div>

        <h2>Sélection</h2>
        <p
          v-if="selectedElements.length === 0"
          class="vide"
        >
          Rien de sélectionné. Cliquez un meuble, ou encadrez-en plusieurs.
        </p>
        <template v-else>
          <p aria-live="polite">
            <strong>{{ describeCount(selectedElements.length, 'élément sélectionné', 'éléments sélectionnés') }}</strong>
          </p>
          <div
            class="actions-selection"
            role="group"
            aria-label="Actions sur la sélection"
          >
            <button
              type="button"
              @click="rotateSelection(15)"
            >
              ⟳ Tourner 15°
            </button>
            <button
              type="button"
              @click="duplicateSelection"
            >
              ⧉ Dupliquer
            </button>
            <button
              type="button"
              @click="copySelection"
            >
              Copier
            </button>
            <button
              type="button"
              :disabled="!clipboard"
              @click="pasteClipboard"
            >
              Coller
            </button>
            <button
              type="button"
              data-variant="danger"
              @click="deleteSelection"
            >
              Supprimer
            </button>
          </div>
          <!-- Le champ retombe à zéro après chaque application : c'est un **décalage**, pas une
               position. Le laisser garni empêcherait de répéter deux fois le même pas, l'évènement
               `change` ne se déclenchant pas sur une valeur inchangée. -->
          <div class="grille-champs">
            <div class="champ">
              <label for="pas-x">Décaler en X (cm)</label>
              <input
                id="pas-x"
                type="number"
                step="1"
                value="0"
                @change="shiftBy($event, 'x')"
              >
            </div>
            <div class="champ">
              <label for="pas-y">Décaler en Y (cm)</label>
              <input
                id="pas-y"
                type="number"
                step="1"
                value="0"
                @change="shiftBy($event, 'y')"
              >
            </div>
          </div>
          <ul class="elements">
            <li
              v-for="element in selectedElements"
              :key="element.id"
            >
              <span>{{ describe(element) }}</span>
              <button
                type="button"
                @click="removeElement(element.id)"
              >
                Retirer
              </button>
            </li>
          </ul>
        </template>

        <h2>Fond de plan</h2>
        <div class="champ">
          <label for="fond-url">Adresse de l'image</label>
          <input
            id="fond-url"
            v-model="backgroundUrlField"
            type="text"
            placeholder="/media/plan.png ou https://…"
            @change="applyBackgroundUrl"
          >
        </div>
        <div class="champ">
          <label for="fond-fichier">…ou un fichier de l'appareil (aperçu, non enregistré)</label>
          <input
            id="fond-fichier"
            type="file"
            accept="image/*"
            @change="chooseLocalBackground"
          >
        </div>
        <template v-if="room && shownBackgroundUrl">
          <div class="champ">
            <label for="fond-opacite">Opacité du calque</label>
            <input
              id="fond-opacite"
              type="range"
              min="0"
              max="1"
              step="0.05"
              :value="room.background_opacity"
              @change="saveBackground({ background_opacity: Number(($event.target as HTMLInputElement).value) })"
            >
          </div>
          <div class="champ champ-case">
            <input
              id="fond-verrou"
              v-model="backgroundUnlocked"
              type="checkbox"
            >
            <label for="fond-verrou">Déverrouiller le calage du calque</label>
          </div>
          <!-- Le calage se règle au chiffre et non au glisser : un fond qu'on déplace à la souris
               se décale d'un pixel à chaque clic manqué, et le plan tracé dessus devient faux
               sans qu'on sache quand. Au clavier, c'est aussi le chemin accessible. -->
          <div
            v-if="backgroundUnlocked"
            class="grille-champs"
          >
            <div class="champ">
              <label for="fond-x">Décalage X (cm)</label>
              <input
                id="fond-x"
                type="number"
                step="1"
                :value="Math.round(room.background_offset_x_cm)"
                @change="saveBackground({ background_offset_x_cm: Number(($event.target as HTMLInputElement).value) })"
              >
            </div>
            <div class="champ">
              <label for="fond-y">Décalage Y (cm)</label>
              <input
                id="fond-y"
                type="number"
                step="1"
                :value="Math.round(room.background_offset_y_cm)"
                @change="saveBackground({ background_offset_y_cm: Number(($event.target as HTMLInputElement).value) })"
              >
            </div>
            <div class="champ">
              <label for="fond-rotation">Rotation (°)</label>
              <input
                id="fond-rotation"
                type="number"
                min="-360"
                max="360"
                step="0.5"
                :value="room.background_rotation_deg"
                @change="saveBackground({ background_rotation_deg: Number(($event.target as HTMLInputElement).value) })"
              >
            </div>
          </div>
          <p
            v-if="!isCalibrated({ scaleCmPerPx: room.background_scale_cm_per_px, offsetXCm: 0, offsetYCm: 0, rotationDeg: 0, opacity: 1 })"
            class="vide"
          >
            Échelle inconnue. Passez en mode « Calibrer le fond », cliquez deux points dont vous
            connaissez la distance, puis saisissez-la.
          </p>
          <p
            v-else
            class="vide"
          >
            Échelle : {{ room.background_scale_cm_per_px?.toFixed(3) }} cm par pixel.
          </p>
          <div
            v-if="mode === 'calibrate'"
            class="champ"
          >
            <label for="distance-reelle">
              Distance réelle entre les {{ calibrationPoints.length }} point(s) cliqué(s) (cm)
            </label>
            <input
              id="distance-reelle"
              v-model.number="calibrationDistance"
              type="number"
              min="1"
              step="1"
            >
            <button
              type="button"
              data-variant="primary"
              :disabled="calibrationPoints.length < 2 || !calibrationDistance"
              @click="applyCalibration"
            >
              Appliquer l'échelle
            </button>
          </div>
        </template>

        <h2>Faces</h2>
        <ul class="faces">
          <li
            v-for="face in faces"
            :key="face.id"
          >
            <button
              type="button"
              class="face"
              :aria-pressed="face.label === store.selectedFaceLabel"
              @click="store.selectedFaceLabel = face.label"
            >
              <strong>{{ face.label }}</strong>
              <span class="type">{{ FACE_KIND_LABELS[face.kind] }}</span>
              <span
                v-if="face.covering.color"
                class="pastille"
                :style="{ background: face.covering.color }"
                aria-hidden="true"
              />
              <span
                v-if="face.elements.length"
                class="compteur"
              >{{ face.elements.length }}</span>
            </button>
          </li>
        </ul>

        <template v-if="selectedFace">
          <h2>
            Face {{ selectedFace.label }}
            <span
              v-if="wallLengthCm"
              class="sous-titre"
            >{{ formatLengthCm(wallLengthCm) }}</span>
          </h2>

          <div class="champ">
            <label for="couleur">Revêtement</label>
            <input
              id="couleur"
              type="color"
              class="couleur"
              :value="selectedFace.covering.color ?? '#ffffff'"
              @change="saveCovering(($event.target as HTMLInputElement).value)"
            >
          </div>

          <h3>Poser un élément</h3>
          <form @submit.prevent="addElement">
            <div class="champ">
              <label for="type-element">Type</label>
              <select
                id="type-element"
                v-model="draftElement.kind"
              >
                <option value="window">
                  Fenêtre
                </option>
                <option value="door_hinged">
                  Porte battante
                </option>
                <option value="door_sliding">
                  Porte coulissante
                </option>
                <option value="furniture">
                  Mobilier
                </option>
              </select>
            </div>

            <div
              v-if="draftElement.kind === 'furniture'"
              class="champ"
            >
              <label for="type-mobilier">Meuble</label>
              <select
                id="type-mobilier"
                v-model.number="draftElement.furniture_type_id"
                required
              >
                <option
                  :value="null"
                  disabled
                >
                  Choisir…
                </option>
                <option
                  v-for="entry in catalog"
                  :key="entry.id"
                  :value="entry.id"
                >
                  {{ entry.name }}
                </option>
              </select>
            </div>

            <div class="grille-champs">
              <div class="champ">
                <label for="x">Distance au coin (cm)</label>
                <input
                  id="x"
                  v-model.number="draftElement.x_offset_cm"
                  type="number"
                  min="0"
                  :max="wallLengthCm ?? undefined"
                >
              </div>
              <div class="champ">
                <label for="y">Hauteur du bas (cm)</label>
                <input
                  id="y"
                  v-model.number="draftElement.y_offset_cm"
                  type="number"
                  min="0"
                >
              </div>
              <div class="champ">
                <label for="l">Largeur (cm)</label>
                <input
                  id="l"
                  v-model.number="draftElement.width_cm"
                  type="number"
                  min="1"
                >
              </div>
              <div class="champ">
                <label for="h">Hauteur (cm)</label>
                <input
                  id="h"
                  v-model.number="draftElement.height_cm"
                  type="number"
                  min="1"
                >
              </div>
            </div>

            <button
              type="submit"
              data-variant="primary"
            >
              Poser sur {{ selectedFace.label }}
            </button>
          </form>

          <h3>Éléments posés</h3>
          <p
            v-if="selectedFace.elements.length === 0"
            class="vide"
          >
            Aucun élément sur cette face.
          </p>
          <ul
            v-else
            class="elements"
          >
            <li
              v-for="element in selectedFace.elements"
              :key="element.id"
            >
              <button
                type="button"
                class="lien-element"
                :aria-pressed="selection.includes(element.id)"
                @click="selection = [element.id]"
              >
                {{ describe(element) }}
              </button>
              <button
                type="button"
                @click="removeElement(element.id)"
              >
                Retirer
              </button>
            </li>
          </ul>
        </template>

        <template v-if="room && room.free_elements.length">
          <h2>Mobilier posé au sol</h2>
          <ul class="elements">
            <li
              v-for="element in room.free_elements"
              :key="element.id"
            >
              <button
                type="button"
                class="lien-element"
                :aria-pressed="selection.includes(element.id)"
                @click="selection = [element.id]"
              >
                {{ describe(element) }}
              </button>
              <button
                type="button"
                @click="removeElement(element.id)"
              >
                Retirer
              </button>
            </li>
          </ul>
        </template>

        <div class="controle">
          <label class="filtre">
            <input
              v-model="accessible"
              type="checkbox"
            >
            Appliquer les seuils du logement accessible
          </label>
          <!-- Un rapport plus vieux que le plan ne se tait pas : il le dit. Le vider en silence
               à chaque écriture ferait clignoter le panneau pendant qu'on déplace un meuble. -->
          <p
            v-if="inspectionStale"
            class="perime"
            role="status"
          >
            Le plan a changé depuis ce contrôle.
          </p>
          <InspectorPanel
            :report="inspection"
            :loading="inspecting"
            :error="inspectionError"
            @rafraichir="inspect"
            @recentrer="recentrerSur"
          />
        </div>
      </aside>
    </div>

    <div
      v-if="helpOpen"
      class="voile"
      @click.self="closeHelp"
    >
      <div
        ref="helpDialog"
        class="aide-clavier"
        role="dialog"
        aria-modal="true"
        aria-labelledby="titre-aide"
        tabindex="-1"
        @keydown.esc="closeHelp"
      >
        <h2 id="titre-aide">
          Raccourcis clavier
        </h2>
        <dl>
          <template
            v-for="shortcut in SHORTCUTS"
            :key="`${shortcut.groupe}-${shortcut.touches}`"
          >
            <dt><kbd>{{ shortcut.touches }}</kbd></dt>
            <dd>{{ shortcut.libelle }} <em>({{ shortcut.groupe }})</em></dd>
          </template>
        </dl>
        <button
          type="button"
          data-variant="primary"
          @click="closeHelp"
        >
          Fermer
        </button>
      </div>
    </div>
  </section>
  <p v-else-if="store.loading">
    Chargement du plan…
  </p>
  <p
    v-else-if="store.error"
    class="message erreur-bloc"
    role="alert"
  >
    {{ store.error }}
  </p>
  <p
    v-else
    class="vide"
  >
    Ce projet est introuvable.
  </p>
</template>

<style scoped>
.entete {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 1rem;
  margin-bottom: 0.75rem;
}

.entete h1 {
  margin: 0;
}

.entete-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.75rem;
}

.etat-enregistrement {
  margin: 0;
  color: var(--texte-doux);
  font-size: 0.9rem;
  font-variant-numeric: tabular-nums;
}

.etat-enregistrement[data-etat='en-cours'] {
  color: var(--accent);
  font-weight: 600;
}

.sous-titre {
  margin: 0;
  color: var(--texte-doux);
  font-size: 0.95rem;
  font-weight: 400;
}

.bouton-lien {
  padding: 0.45rem 0.9rem;
  border: 1px solid var(--accent);
  border-radius: 0.35rem;
  text-decoration: none;
  font-weight: 600;
}

.message {
  padding: 0.6rem 0.85rem;
  border-radius: 0.35rem;
}

.erreur-bloc {
  background: #fdecea;
  color: #7a1010;
  font-weight: 600;
}

/* Contraste 7:1 minimum sur fond clair (WCAG AAA) : le message d'export est une information
   d'état, il doit rester lisible pour tout le monde. */
.export-bloc {
  background: #e8f1fb;
  color: #10365e;
  font-weight: 600;
}

.disposition {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 23rem;
  gap: 1.5rem;
  align-items: start;
}

@media (max-width: 68rem) {
  .disposition {
    grid-template-columns: 1fr;
  }
}

.barre {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.6rem;
  margin-bottom: 0.75rem;
  padding: 0.5rem 0.6rem;
  border: 1px solid var(--bordure);
  border-radius: 0.4rem;
  background: #fafbfc;
}

.barre label {
  margin: 0;
  font-size: 0.9rem;
}

.barre select {
  width: auto;
}

.separateur {
  width: 1px;
  height: 1.5rem;
  background: var(--bordure);
}

button[aria-pressed='true'] {
  background: var(--accent);
  border-color: var(--accent);
  color: #ffffff;
}

.ajout {
  display: flex;
  align-items: flex-end;
  flex-wrap: wrap;
  gap: 0.75rem;
  margin-top: 1rem;
  max-width: 32rem;
}

/* Téléphone : la grille à deux colonnes du panneau de propriétés devient illisible, et les
   boutons de la barre d'outils passent sous la taille de cible tactile recommandée. */
@media (max-width: 40rem) {
  .grille-champs {
    grid-template-columns: 1fr;
  }

  .barre button,
  .elements button,
  .palette button {
    min-height: 2.75rem;
  }

  .ajout {
    align-items: stretch;
  }
}

.ajout .champ {
  flex: 1;
  margin: 0;
}

.champ {
  margin-bottom: 0.75rem;
}

.champ-case {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.champ-case label {
  margin: 0;
}

.champ-case input {
  width: auto;
}

.couleur {
  height: 2.4rem;
  padding: 0.15rem;
}

.grille-champs {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.5rem 0.75rem;
}

.faces {
  list-style: none;
  padding: 0;
  margin: 0 0 1.25rem;
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
}

.face {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
}

.type {
  color: var(--texte-doux);
  font-size: 0.85rem;
}

button[aria-pressed='true'] .type {
  color: #dbe6ff;
}

.pastille {
  width: 0.85rem;
  height: 0.85rem;
  border-radius: 50%;
  border: 1px solid rgba(0, 0, 0, 0.25);
}

.compteur {
  min-width: 1.15rem;
  padding: 0 0.25rem;
  border-radius: 0.6rem;
  background: #e2e8f0;
  color: #1b222b;
  font-size: 0.78rem;
  text-align: center;
}

.elements,
.palette,
.liste-cotes {
  list-style: none;
  padding: 0;
  margin: 0;
}

.elements li,
.palette li,
.liste-cotes li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.4rem 0;
  border-bottom: 1px solid var(--bordure);
}

.liste-cotes {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(11rem, 1fr));
  gap: 0 1rem;
}

.liste-cotes label {
  margin: 0;
  font-weight: 600;
}

.liste-cotes input {
  width: 5.5rem;
}

.unite {
  color: var(--texte-doux);
}

.cotes {
  margin-top: 1.5rem;
}

.groupe h3 {
  margin: 0.75rem 0 0.25rem;
  font-size: 0.95rem;
}

.palette .nom {
  flex: 1;
}

.palette .cote {
  color: var(--texte-doux);
  font-size: 0.8rem;
  font-variant-numeric: tabular-nums;
}

.poignee {
  cursor: grab;
  padding: 0 0.25rem;
  color: var(--texte-doux);
  user-select: none;
}

.lien-element {
  flex: 1;
  text-align: left;
  background: none;
  border: none;
  padding: 0.2rem 0;
  cursor: pointer;
}

.lien-element[aria-pressed='true'] {
  background: none;
  color: var(--accent);
  font-weight: 700;
}

.actions-selection {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  margin-bottom: 0.75rem;
}

/* La feuille globale ne connaît que `primary`. Une action destructrice doit se distinguer d'un
   bouton ordinaire — et pas seulement par la couleur : le libellé et la graisse la portent aussi,
   pour qui ne perçoit pas le rouge. `--erreur` sur fond blanc tient le 7:1 exigé en AAA. */
button[data-variant='danger'] {
  border-color: var(--erreur);
  color: var(--erreur);
  font-weight: 700;
}

.voile {
  position: fixed;
  inset: 0;
  display: grid;
  place-items: center;
  background: rgb(0 0 0 / 55%);
  z-index: 20;
}

.aide-clavier {
  max-width: 34rem;
  max-height: 80vh;
  overflow: auto;
  padding: 1.25rem 1.5rem;
  border-radius: 0.6rem;
  background: #ffffff;
}

.aide-clavier dl {
  display: grid;
  grid-template-columns: minmax(8rem, auto) 1fr;
  gap: 0.35rem 1rem;
  margin: 0 0 1rem;
}

.aide-clavier dt {
  margin: 0;
}

.aide-clavier dd {
  margin: 0;
}

kbd {
  display: inline-block;
  padding: 0.1rem 0.4rem;
  border: 1px solid var(--bordure);
  border-radius: 0.25rem;
  background: #f3f5f7;
  font-family: inherit;
  font-size: 0.85rem;
}

.vide {
  color: var(--texte-doux);
}

.controle {
  margin-top: 1.25rem;
  border-top: 1px solid var(--bordure, currentcolor);
  padding-top: 0.75rem;
}

.controle .filtre {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  margin-bottom: 0.5rem;
}

.perime {
  font-weight: 700;
  margin: 0 0 0.5rem;
}

h2,
h3 {
  margin-bottom: 0.5rem;
}
</style>
