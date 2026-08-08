<script setup lang="ts">
/**
 * Plan 2D interactif (Konva).
 *
 * Dessine un vrai plan d'architecte : murs épais, ouvertures avec leur symbole et leur
 * débattement, emprise des meubles adossés **et** libres, cotes déportées, fond de plan, grille à
 * deux niveaux. Le composant n'écrit jamais en base : il émet des **intentions** (« ces meubles
 * ont bougé de ce vecteur »), la vue parente décide de la traduction en écriture et de son
 * inverse pour la pile d'annulation.
 *
 * **Rien n'est émis pendant un geste, tout l'est au relâchement.** Émettre à chaque déplacement
 * déclencherait une requête par pixel parcouru — donc une cascade de conflits de version.
 *
 * Accessibilité : le canevas ne prend pas le focus clavier de lui-même et n'expose aucun contenu
 * au lecteur d'écran. Il porte donc `role="application"`, un libellé, et une région `aria-live`
 * qui annonce ce que le geste en cours produirait. Les chemins clavier complets (déplacement,
 * rotation, suppression, sélection) vivent dans la vue parente, avec les listes latérales.
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
// Enregistrement local plutôt que `app.use(VueKonva)` : globalement installé, Konva (55 Ko
// gzip) atterrissait dans le chunk d'entrée, donc sur l'écran de connexion et sur la page de
// partage publique, qui n'affichent jamais de plan 2D.
import {
  Arc as VArc,
  Circle as VCircle,
  Group as VGroup,
  Image as VImage,
  Label as VLabel,
  Layer as VLayer,
  Line as VLine,
  Rect as VRect,
  Stage as VStage,
  Tag as VTag,
  Text as VText,
} from 'vue-konva'

import type { Face, PlanElement } from '@/api/types'
import {
  type BackgroundPlacement,
  effectiveScale,
  isCalibrated,
} from '@/editor/calibration'
import {
  type WallGeometry,
  dimensionLine,
  freeFurnitureFootprint,
  furnitureFootprint,
  isOpening,
  openingSymbol,
  twoLevelGrid,
  wallGeometries,
  wallKey,
  wallOutline,
} from '@/editor/drawing'
import {
  DEFAULT_VIEWPORT,
  areaInSquareMeters,
  fitViewport,
  isSelfIntersecting,
  planToScreen,
  screenToPlan,
  segmentLength,
  snapPoint,
  type Point,
  type Viewport,
} from '@/editor/geometry'
import { formatLengthCm, perimeterCm } from '@/editor/measure'
import { type DragPayload, DRAG_MIME, parseDragPayload } from '@/editor/palette'
import {
  type DropTarget,
  footprintCorners,
  freeFootprint,
  resolveDrop,
} from '@/editor/placement'
import { normalizeAngle } from '@/editor/operations'
import {
  type Selectable,
  elementsInRect,
  isNegligibleRect,
  normalizeRect,
  toggleSelection,
} from '@/editor/selection'
import {
  type Guide,
  collectVertices,
  pointAtDistance,
  resolveSnap,
  verticesExcept,
} from '@/editor/snapping'
import { isTypingTarget } from '@/editor/shortcuts'

const props = withDefaults(
  defineProps<{
    polygon: number[][]
    faces: Face[]
    /** Mobilier posé au sol de la pièce (spec §10, A4) : il n'appartient à aucune face. */
    freeElements?: PlanElement[]
    roomId?: number | null
    roomName?: string
    wallThicknessCm?: number
    selectedFaceLabel?: string | null
    /** Identifiants des éléments sélectionnés. Une liste, pas des références : voir `selection.ts`. */
    selection?: number[]
    mode?: 'navigate' | 'draw' | 'edit' | 'calibrate'
    gridCm?: number
    width?: number
    height?: number
    furnitureNames?: Record<number, string>
    /** Identifiant de persistance du brouillon, de la forme `projet:piece`. */
    draftKey?: string | null
    backgroundUrl?: string | null
    background?: BackgroundPlacement | null
    /**
     * Meuble en cours de glisser depuis la palette.
     *
     * Porté par la vue parente parce que `DataTransfer.getData` rend une chaîne vide hors de
     * `drop` : sans cette prop, l'aperçu de dépose ne saurait ni quelle taille dessiner, ni si
     * l'endroit survolé est acceptable.
     */
    dragPayload?: DragPayload | null
  }>(),
  {
    freeElements: () => [],
    roomId: null,
    roomName: '',
    wallThicknessCm: 10,
    selectedFaceLabel: null,
    selection: () => [],
    mode: 'navigate',
    gridCm: 10,
    width: 960,
    height: 620,
    furnitureNames: () => ({}),
    draftKey: null,
    backgroundUrl: null,
    background: null,
    dragPayload: null,
  },
)

const emit = defineEmits<{
  (event: 'update:polygon', polygon: number[][]): void
  (event: 'update:selection', selection: number[]): void
  (event: 'select-face', label: string): void
  (event: 'select-element', element: PlanElement): void
  (event: 'finish-drawing'): void
  (event: 'drop-furniture', drop: { payload: DragPayload; target: DropTarget }): void
  (event: 'move-elements', move: { ids: number[]; delta: Point }): void
  (event: 'transform-element', change: { id: number; rotation_deg?: number; width_cm?: number; depth_cm?: number }): void
  (event: 'calibrate', points: Point[]): void
}>()

const host = ref<HTMLDivElement | null>(null)
const viewport = ref<Viewport>({ ...DEFAULT_VIEWPORT })
const draft = ref<number[][]>([])
const cursor = ref<Point | null>(null)
const hoveredLabel = ref<string | null>(null)
const shiftHeld = ref(false)
/** Ce que le geste en cours produirait, annoncé au lecteur d'écran et affiché en légende. */
const announcement = ref('')

/**
 * Taille réelle du canvas.
 *
 * Un `<canvas>` de 960 px de large débordait de l'écran d'un téléphone, et Konva ne se
 * redimensionne pas tout seul : la taille est un attribut, pas une règle CSS. Les props
 * `width`/`height` ne servent donc plus que de valeur initiale, avant la première mesure.
 */
const stage = ref({ width: props.width, height: props.height })
let surfaceObserver: ResizeObserver | null = null

/** Copie de travail pendant un glisser : la source reste intacte tant qu'on n'a pas lâché. */
const dragging = ref<{ index: number; polygon: number[][] } | null>(null)
const panning = ref<{ x: number; y: number } | null>(null)
/** Rectangle d'encadrement, en coordonnées du plan. */
const marquee = ref<{ from: Point; to: Point } | null>(null)
/** Déplacement d'éléments : le vecteur est appliqué localement, émis une fois au relâchement. */
const moving = ref<{ ids: number[]; from: Point; delta: Point } | null>(null)
const rotating = ref<{ id: number; angleDeg: number } | null>(null)
const resizing = ref<{ id: number; widthCm: number; depthCm: number } | null>(null)
/** Points cliqués pour le calibrage du fond de plan. */
const calibrationPoints = ref<Point[]>([])
/** Cote saisie au clavier pendant le tracé : le vrai mode de saisie d'un relevé au laser. */
const typedLength = ref('')
const lengthField = ref<HTMLInputElement | null>(null)
/** Pincement à deux doigts : distance et centre de référence. */
const pinch = ref<{ distance: number; center: Point } | null>(null)
const activePointers = new Map<number, { x: number; y: number }>()

const activePolygon = computed(() => {
  if (props.mode === 'draw') return draft.value
  return dragging.value?.polygon ?? props.polygon
})

const walls = computed<WallGeometry[]>(() => wallGeometries(activePolygon.value, props.faces))
const selfIntersecting = computed(() => isSelfIntersecting(activePolygon.value))
const area = computed(() => areaInSquareMeters(activePolygon.value))
const perimeter = computed(() => perimeterCm(activePolygon.value))

const grid = computed(() =>
  twoLevelGrid(stage.value.width, stage.value.height, viewport.value, props.gridCm),
)

const floorPoints = computed(() =>
  activePolygon.value.flatMap((vertex) => {
    const screen = planToScreen({ x: vertex[0] as number, y: vertex[1] as number }, viewport.value)
    return [screen.x, screen.y]
  }),
)

const wallShapes = computed(() =>
  walls.value.map((wall, index) => ({
    wall,
    key: wallKey(wall.face?.id, index),
    points: wallOutline(wall, props.wallThicknessCm, viewport.value),
    selected: wall.face?.label === props.selectedFaceLabel,
    hovered: wall.face?.label === hoveredLabel.value,
  })),
)

const openings = computed(() =>
  walls.value.flatMap((wall) =>
    (wall.face?.elements ?? [])
      .filter(isOpening)
      .map((element) => openingSymbol(wall, element, props.wallThicknessCm, viewport.value)),
  ),
)

function nameOf(element: PlanElement): string {
  return props.furnitureNames[element.furniture_type_id ?? -1] ?? 'Meuble'
}

/**
 * Élément tel qu'il est *dessiné* : le geste en cours y est déjà appliqué.
 *
 * Le déplacement, la rotation et le redimensionnement se voient immédiatement sans qu'aucune
 * requête ne parte. C'est ce qui rend le glisser utilisable : attendre l'aller-retour serveur
 * pour bouger un meuble donnerait un pixel de retard à chaque image.
 */
function previewed(element: PlanElement): PlanElement {
  let shown = element
  const move = moving.value

  if (move?.ids.includes(element.id)) {
    if (element.face_id === null) {
      shown = {
        ...shown,
        pos_x_cm: (shown.pos_x_cm ?? 0) + move.delta.x,
        pos_y_cm: (shown.pos_y_cm ?? 0) + move.delta.y,
      }
    } else {
      // Un élément adossé ne peut que glisser **le long** de son mur : seule la composante
      // parallèle du vecteur a un sens, le reste le décollerait, ce que le modèle interdit.
      const wall = walls.value.find((item) => item.face?.id === element.face_id)
      if (wall) {
        const along = move.delta.x * wall.direction.x + move.delta.y * wall.direction.y
        const maximum = Math.max(wall.lengthCm - element.width_cm, 0)
        shown = {
          ...shown,
          x_offset_cm: Math.min(Math.max(shown.x_offset_cm + along, 0), maximum),
        }
      }
    }
  }

  if (rotating.value?.id === element.id) {
    shown = { ...shown, rotation_deg: rotating.value.angleDeg }
  }
  if (resizing.value?.id === element.id) {
    shown = { ...shown, width_cm: resizing.value.widthCm, depth_cm: resizing.value.depthCm }
  }
  return shown
}

const furniture = computed(() =>
  walls.value.flatMap((wall) =>
    (wall.face?.elements ?? [])
      .filter((element) => !isOpening(element))
      .map((element) => {
        const shown = previewed(element)
        return {
          ...furnitureFootprint(
            wall,
            shown,
            props.wallThicknessCm,
            viewport.value,
            nameOf(element),
          ),
          selected: props.selection.includes(element.id),
        }
      }),
  ),
)

const freeFurniture = computed(() =>
  props.freeElements
    .map((element) => {
      const shown = previewed(element)
      const footprint = freeFurnitureFootprint(shown, viewport.value, nameOf(element))
      return footprint === null ? null : { ...footprint, selected: props.selection.includes(element.id) }
    })
    .filter((item): item is NonNullable<typeof item> => item !== null),
)

/** Poignées de rotation et de taille : uniquement sur un meuble **libre** sélectionné, et seul. */
const handles = computed(() => {
  if (props.selection.length !== 1 || props.mode === 'draw') return null
  const element = props.freeElements.find((candidate) => candidate.id === props.selection[0])
  if (!element) return null
  const shown = previewed(element)
  const footprint = freeFootprint(shown)
  if (!footprint) return null

  const corner = footprint.corners[2] as Point
  const front = {
    x: (footprint.corners[0] as Point).x + ((footprint.corners[1] as Point).x - (footprint.corners[0] as Point).x) / 2,
    y: (footprint.corners[0] as Point).y + ((footprint.corners[1] as Point).y - (footprint.corners[0] as Point).y) / 2,
  }
  // La poignée de rotation est déportée en dehors de l'emprise : posée dessus, elle serait
  // attrapée à la place du meuble à chaque tentative de déplacement.
  const away = 26 / Math.max(viewport.value.scale, 0.02)
  const normal = {
    x: front.x - footprint.center.x,
    y: front.y - footprint.center.y,
  }
  const norm = Math.hypot(normal.x, normal.y) || 1

  return {
    element,
    rotate: planToScreen(
      { x: front.x + (normal.x / norm) * away, y: front.y + (normal.y / norm) * away },
      viewport.value,
    ),
    resize: planToScreen(corner, viewport.value),
    center: planToScreen(footprint.center, viewport.value),
  }
})

/** Cotes déportées : demi-épaisseur du mur, plus une marge constante à l'écran. */
const dimensions = computed(() => {
  const offset = props.wallThicknessCm / 2 + 36 / Math.max(viewport.value.scale, 0.05)
  return walls.value.map((wall, index) => {
    const line = dimensionLine(wall, offset, viewport.value)
    return {
      wall,
      key: wallKey(wall.face?.id, index),
      ...line,
      screenLabel: planToScreen(line.labelAt, viewport.value),
    }
  })
})

const centroidScreen = computed(() => {
  if (activePolygon.value.length < 3) return null
  const xs = activePolygon.value.map((v) => v[0] as number)
  const ys = activePolygon.value.map((v) => v[1] as number)
  return planToScreen(
    { x: (Math.min(...xs) + Math.max(...xs)) / 2, y: (Math.min(...ys) + Math.max(...ys)) / 2 },
    viewport.value,
  )
})

const previewLine = computed(() => {
  if (props.mode !== 'draw' || draft.value.length === 0 || !cursor.value) return []
  const last = draft.value[draft.value.length - 1] as [number, number]
  const from = planToScreen({ x: last[0], y: last[1] }, viewport.value)
  const to = planToScreen(cursor.value, viewport.value)
  return [from.x, from.y, to.x, to.y]
})

const previewLengthCm = computed(() => {
  if (props.mode !== 'draw' || draft.value.length === 0 || !cursor.value) return null
  const last = draft.value[draft.value.length - 1] as [number, number]
  return Math.round(segmentLength({ x: last[0], y: last[1] }, cursor.value))
})

/** Origine de la contrainte angulaire et de la saisie numérique : le dernier sommet posé. */
const drawOrigin = computed<Point | null>(() => {
  const last = draft.value[draft.value.length - 1]
  return last ? { x: last[0] as number, y: last[1] as number } : null
})

/** Guides d'aimantation affichés pendant le geste. */
const guides = ref<Guide[]>([])

const guideLines = computed(() =>
  guides.value.map((guide) => {
    const from = planToScreen(guide.from, viewport.value)
    const to = planToScreen(guide.to, viewport.value)
    return { points: [from.x, from.y, to.x, to.y], kind: guide.kind }
  }),
)

const marqueeRect = computed(() => {
  if (!marquee.value) return null
  const rect = normalizeRect(marquee.value.from, marquee.value.to)
  const topLeft = planToScreen({ x: rect.minX, y: rect.minY }, viewport.value)
  const bottomRight = planToScreen({ x: rect.maxX, y: rect.maxY }, viewport.value)
  return {
    x: topLeft.x,
    y: topLeft.y,
    width: bottomRight.x - topLeft.x,
    height: bottomRight.y - topLeft.y,
  }
})

// --- Fond de plan -------------------------------------------------------------------------------

const backgroundImage = ref<HTMLImageElement | null>(null)

/**
 * Charge l'image du fond de plan.
 *
 * Konva veut un `HTMLImageElement` déjà chargé : lui donner une image en cours de chargement
 * dessine un cadre vide qui ne se rafraîchit jamais. L'échec est silencieux côté canevas — la vue
 * parente le signale, elle a le contexte pour dire quoi faire.
 */
watch(
  () => props.backgroundUrl,
  (url) => {
    backgroundImage.value = null
    if (!url || typeof Image === 'undefined') return
    const image = new Image()
    image.onload = () => {
      // L'utilisateur peut avoir changé de pièce entre-temps : on n'écrase pas un fond plus récent.
      if (props.backgroundUrl === url) backgroundImage.value = image
    }
    image.src = url
  },
  { immediate: true },
)

const backgroundConfig = computed(() => {
  const image = backgroundImage.value
  const placement = props.background
  if (!image || !placement) return null
  const scale = effectiveScale(placement) * viewport.value.scale
  const origin = planToScreen(
    { x: placement.offsetXCm, y: placement.offsetYCm },
    viewport.value,
  )
  return {
    image,
    x: origin.x,
    y: origin.y,
    scaleX: scale,
    scaleY: scale,
    rotation: placement.rotationDeg,
    opacity: placement.opacity,
    listening: false,
  }
})

const backgroundWarning = computed(
  () => props.backgroundUrl !== null && props.background !== null && !isCalibrated(props.background),
)

// --- Gestes ------------------------------------------------------------------------------------

/** Rayon d'accroche, constant à l'œil : 12 px d'écran convertis en centimètres du plan. */
const snapToleranceCm = computed(() => 12 / Math.max(viewport.value.scale, 0.02))

/** Sommets candidats à l'aimantation : le contour en cours et celui déjà enregistré. */
const snapVertices = computed(() =>
  collectVertices([props.polygon, draft.value].filter((polygon) => polygon.length > 0)),
)

function rawPointer(event: { evt: PointerEvent }): Point {
  const bounds = host.value?.getBoundingClientRect()
  return {
    x: event.evt.clientX - (bounds?.left ?? 0),
    y: event.evt.clientY - (bounds?.top ?? 0),
  }
}

function pointerToPlan(event: { evt: PointerEvent }): Point {
  return screenToPlan(rawPointer(event), viewport.value)
}

/**
 * Applique l'aimantation et met à jour les guides affichés.
 *
 * `ignoreIndex` retire du jeu de candidats le sommet qu'on est en train de déplacer. Sans ça, il
 * s'accroche à sa **propre** position de départ et à ses propres prolongements : le déplacer de
 * moins d'un rayon d'accroche devient impossible, et un contour ne peut plus être corrigé de
 * quelques centimètres — précisément le geste que l'aimantation est censée servir.
 */
function snapped(point: Point, origin: Point | null, ignoreIndex: number | null = null): Point {
  const vertices =
    ignoreIndex === null ? snapVertices.value : verticesExcept(props.polygon, ignoreIndex)

  const result = resolveSnap(point, {
    vertices,
    gridCm: props.gridCm,
    toleranceCm: snapToleranceCm.value,
    origin,
    constrainAngle: shiftHeld.value,
    guideLengthCm: 200 / Math.max(viewport.value.scale, 0.02),
  })
  guides.value = result.guides
  announcement.value = result.libelle
  return result.point
}

function finishDrawing(): void {
  if (draft.value.length >= 3) emit('update:polygon', [...draft.value])
  draft.value = []
  typedLength.value = ''
  emit('finish-drawing')
}

function onStageClick(event: { evt: PointerEvent }): void {
  if (props.mode === 'calibrate') {
    const point = pointerToPlan(event)
    calibrationPoints.value =
      calibrationPoints.value.length >= 2 ? [point] : [...calibrationPoints.value, point]
    emit('calibrate', [...calibrationPoints.value])
    return
  }
  if (props.mode !== 'draw') return

  const point = snapped(pointerToPlan(event), drawOrigin.value)

  const first = draft.value[0]
  if (first && draft.value.length >= 3) {
    const distance = segmentLength({ x: first[0] as number, y: first[1] as number }, point)
    if (distance <= Math.max(props.gridCm * 2, 20 / viewport.value.scale)) {
      finishDrawing()
      return
    }
  }
  draft.value = [...draft.value, [point.x, point.y]]
  typedLength.value = ''
}

function onPointerMove(event: { evt: PointerEvent }): void {
  if (activePointers.has(event.evt.pointerId)) {
    activePointers.set(event.evt.pointerId, { x: event.evt.clientX, y: event.evt.clientY })
  }
  if (updatePinch()) return

  const plan = pointerToPlan(event)

  if (panning.value) {
    viewport.value = {
      ...viewport.value,
      offsetX: viewport.value.offsetX + (event.evt.clientX - panning.value.x),
      offsetY: viewport.value.offsetY + (event.evt.clientY - panning.value.y),
    }
    panning.value = { x: event.evt.clientX, y: event.evt.clientY }
    return
  }

  if (moving.value) {
    const delta = snapDelta(plan)
    moving.value = { ...moving.value, delta }
    announcement.value = `déplacement ${Math.round(delta.x)} ; ${Math.round(delta.y)} cm`
    return
  }

  if (rotating.value) {
    const element = props.freeElements.find((candidate) => candidate.id === rotating.value?.id)
    const center = element ? freeFootprint(element)?.center : null
    if (center) {
      const raw = (Math.atan2(center.y - plan.y, plan.x - center.x) * 180) / Math.PI - 90
      const step = shiftHeld.value ? 1 : 15
      const angle = normalizeAngle(Math.round(raw / step) * step)
      rotating.value = { id: rotating.value.id, angleDeg: angle }
      announcement.value = `rotation ${angle}°`
    }
    return
  }

  if (resizing.value) {
    const element = props.freeElements.find((candidate) => candidate.id === resizing.value?.id)
    const footprint = element ? freeFootprint(element) : null
    if (element && footprint) {
      const angle = (element.rotation_deg * Math.PI) / 180
      const widthAxis = { x: Math.cos(angle), y: -Math.sin(angle) }
      const depthAxis = { x: Math.sin(angle), y: Math.cos(angle) }
      const dx = plan.x - footprint.center.x
      const dy = plan.y - footprint.center.y
      const width = Math.max(Math.round(2 * Math.abs(dx * widthAxis.x + dy * widthAxis.y)), 5)
      const depth = Math.max(Math.round(2 * Math.abs(dx * depthAxis.x + dy * depthAxis.y)), 5)
      resizing.value = { id: element.id, widthCm: width, depthCm: depth }
      announcement.value = `${width} × ${depth} cm`
    }
    return
  }

  if (marquee.value) {
    marquee.value = { ...marquee.value, to: plan }
    return
  }

  cursor.value = props.mode === 'draw' ? snapped(plan, drawOrigin.value) : plan

  if (dragging.value) {
    const index = dragging.value.index
    const moved = snapped(plan, previousVertex(index), index)
    dragging.value = {
      index,
      polygon: props.polygon.map((vertex, position) =>
        position === index ? [moved.x, moved.y] : vertex,
      ),
    }
  }
}

/** Sommet précédent : origine de la contrainte angulaire quand on déforme un contour existant. */
function previousVertex(index: number): Point | null {
  const previous = props.polygon[(index - 1 + props.polygon.length) % props.polygon.length]
  return previous ? { x: previous[0] as number, y: previous[1] as number } : null
}

/** Le vecteur d'un déplacement d'éléments, aligné sur la grille pour rester chiffrable. */
function snapDelta(current: Point): Point {
  const from = moving.value?.from ?? current
  return snapPoint({ x: current.x - from.x, y: current.y - from.y }, props.gridCm)
}

function startDraggingVertex(index: number, event: { evt: PointerEvent }): void {
  if (props.mode !== 'edit') return
  event.evt.stopPropagation()
  dragging.value = { index, polygon: props.polygon.map((vertex) => [...vertex]) }
}

function onElementPointerDown(element: PlanElement, event: { evt: PointerEvent }): void {
  if (props.mode === 'draw' || props.mode === 'calibrate') return
  event.evt.stopPropagation()

  const selection = event.evt.shiftKey
    ? toggleSelection(props.selection, element.id)
    : props.selection.includes(element.id)
      ? props.selection
      : [element.id]
  emit('update:selection', selection)
  emit('select-element', element)

  // Un élément qu'on vient de retirer de la sélection ne doit pas partir en déplacement.
  if (!selection.includes(element.id)) return
  moving.value = { ids: selection, from: pointerToPlan(event), delta: { x: 0, y: 0 } }
}

function startRotate(event: { evt: PointerEvent }): void {
  const element = handles.value?.element
  if (!element) return
  event.evt.stopPropagation()
  rotating.value = { id: element.id, angleDeg: element.rotation_deg }
}

function startResize(event: { evt: PointerEvent }): void {
  const element = handles.value?.element
  if (!element) return
  event.evt.stopPropagation()
  resizing.value = { id: element.id, widthCm: element.width_cm, depthCm: element.depth_cm }
}

function releasePointer(event?: { evt?: PointerEvent }): void {
  // `pointerId` est lu prudemment : le même gestionnaire sert `pointerup`, `pointerleave` et
  // `pointercancel`, et ce dernier arrive sur certains navigateurs sans évènement natif attaché.
  const pointerId = event?.evt?.pointerId
  if (pointerId !== undefined) activePointers.delete(pointerId)
  if (activePointers.size < 2) pinch.value = null
  panning.value = null

  if (moving.value) {
    const { ids, delta } = moving.value
    moving.value = null
    if (delta.x !== 0 || delta.y !== 0) emit('move-elements', { ids, delta })
    return
  }

  if (rotating.value) {
    const { id, angleDeg } = rotating.value
    rotating.value = null
    emit('transform-element', { id, rotation_deg: angleDeg })
    return
  }

  if (resizing.value) {
    const { id, widthCm, depthCm } = resizing.value
    resizing.value = null
    emit('transform-element', { id, width_cm: widthCm, depth_cm: depthCm })
    return
  }

  if (marquee.value) {
    const rect = normalizeRect(marquee.value.from, marquee.value.to)
    marquee.value = null
    // Un encadrement d'un pixel, c'est un clic sur le vide : il vide la sélection.
    if (isNegligibleRect(rect, 2 / Math.max(viewport.value.scale, 0.02))) {
      emit('update:selection', [])
      return
    }
    emit('update:selection', elementsInRect(selectables.value, rect))
    return
  }

  if (!dragging.value) return
  const moved = dragging.value.polygon
  dragging.value = null
  guides.value = []
  // Émis une seule fois, au relâchement.
  emit('update:polygon', moved)
}

/** Ce qui peut être encadré : le centre d'emprise de chaque élément de la pièce. */
const selectables = computed<Selectable[]>(() => [
  ...furniture.value.map((item) => ({ id: item.element.id, center: item.center })),
  ...freeFurniture.value.map((item) => ({ id: item.element.id, center: item.center })),
  ...openings.value.map((item) => ({ id: item.element.id, center: item.center })),
])

function onStagePointerDown(event: { evt: PointerEvent }): void {
  activePointers.set(event.evt.pointerId, { x: event.evt.clientX, y: event.evt.clientY })
  if (activePointers.size >= 2) {
    beginPinch()
    return
  }
  if (dragging.value) return

  // Bouton du milieu, Alt, ou mode « déformer » : on déplace la vue. Le test passe **avant**
  // l'écart des modes de saisie, sans quoi on ne pourrait plus recadrer pendant un tracé ou un
  // calibrage — précisément les deux moments où l'on a besoin d'aller voir ailleurs.
  if (event.evt.button === 1 || event.evt.altKey || props.mode === 'edit') {
    panning.value = { x: event.evt.clientX, y: event.evt.clientY }
    return
  }
  if (props.mode === 'draw' || props.mode === 'calibrate') return

  const start = pointerToPlan(event)
  marquee.value = { from: start, to: start }
}

// --- Zoom, pan, pincement -----------------------------------------------------------------------

function zoomAt(pointer: Point, factor: number): void {
  const before = screenToPlan(pointer, viewport.value)
  const scale = Math.min(Math.max(viewport.value.scale * factor, 0.02), 12)
  // Le zoom garde sous le curseur le point du plan qui s'y trouvait.
  viewport.value = {
    scale,
    offsetX: pointer.x - before.x * scale,
    offsetY: pointer.y - before.y * scale,
  }
}

/**
 * Molette et trackpad.
 *
 * `ctrlKey` sur un évènement de molette, c'est un **pincement de trackpad** : le navigateur le
 * signale ainsi depuis toujours. Un défilement à deux doigts porte lui un `deltaX` significatif
 * et doit déplacer la vue, pas zoomer — sinon un simple mouvement latéral fait sauter l'échelle.
 * La molette d'une vraie souris n'a ni l'un ni l'autre : elle zoome, comme avant.
 */
function onWheel(event: { evt: WheelEvent }): void {
  event.evt.preventDefault()
  const pointer = {
    x: event.evt.clientX - (host.value?.getBoundingClientRect().left ?? 0),
    y: event.evt.clientY - (host.value?.getBoundingClientRect().top ?? 0),
  }

  if (!event.evt.ctrlKey && Math.abs(event.evt.deltaX) > Math.abs(event.evt.deltaY)) {
    viewport.value = {
      ...viewport.value,
      offsetX: viewport.value.offsetX - event.evt.deltaX,
      offsetY: viewport.value.offsetY - event.evt.deltaY,
    }
    return
  }
  zoomAt(pointer, event.evt.deltaY < 0 ? 1.12 : 1 / 1.12)
}

function pointerSpread(): { distance: number; center: Point } | null {
  const points = [...activePointers.values()]
  const [a, b] = points
  if (!a || !b) return null
  const bounds = host.value?.getBoundingClientRect()
  return {
    distance: Math.hypot(b.x - a.x, b.y - a.y),
    center: {
      x: (a.x + b.x) / 2 - (bounds?.left ?? 0),
      y: (a.y + b.y) / 2 - (bounds?.top ?? 0),
    },
  }
}

function beginPinch(): void {
  // Un pincement qui commence annule ce qu'un seul doigt avait entamé : sur tablette, le second
  // doigt arrive toujours après le premier, et sans ça on encadrerait en zoomant.
  marquee.value = null
  moving.value = null
  panning.value = null
  pinch.value = pointerSpread()
}

/** Vrai si le geste en cours est un pincement, auquel cas rien d'autre ne doit s'appliquer. */
function updatePinch(): boolean {
  if (activePointers.size < 2) return false
  const spread = pointerSpread()
  const previous = pinch.value
  if (!spread) return true
  if (!previous || previous.distance <= 0) {
    pinch.value = spread
    return true
  }
  zoomAt(spread.center, spread.distance / previous.distance)
  viewport.value = {
    ...viewport.value,
    offsetX: viewport.value.offsetX + (spread.center.x - previous.center.x),
    offsetY: viewport.value.offsetY + (spread.center.y - previous.center.y),
  }
  pinch.value = spread
  return true
}

// --- Glisser-déposer depuis la palette ----------------------------------------------------------

const dropPreview = ref<{ payload: DragPayload; target: DropTarget; at: Point } | null>(null)

/** Point du plan visé par un évènement de glisser natif, aligné sur la grille. */
function dragPoint(event: DragEvent): Point {
  const bounds = host.value?.getBoundingClientRect()
  return snapPoint(
    screenToPlan(
      { x: event.clientX - (bounds?.left ?? 0), y: event.clientY - (bounds?.top ?? 0) },
      viewport.value,
    ),
    props.gridCm,
  )
}

function targetAt(point: Point, payload: DragPayload): DropTarget {
  if (props.roomId === null) return { kind: 'refuse', raison: 'aucune pièce sélectionnée' }
  return resolveDrop(point, {
    roomId: props.roomId,
    polygon: props.polygon,
    walls: walls.value,
    wallThicknessCm: props.wallThicknessCm,
    widthCm: payload.width_cm,
    depthCm: payload.depth_cm,
  })
}

/**
 * Survol pendant un glisser.
 *
 * La charge utile vient de la prop et non de `DataTransfer.getData` : la spécification HTML
 * impose à `getData` de rendre une chaîne vide hors de `drop`, pour ne pas laisser une page lire
 * un fichier simplement survolé. Sans la prop, l'aperçu ne saurait donc pas quelle taille
 * dessiner, ni si l'endroit visé est acceptable.
 */
function onDragOver(event: DragEvent): void {
  if (!event.dataTransfer?.types.includes(DRAG_MIME)) return
  event.preventDefault()
  event.dataTransfer.dropEffect = 'copy'

  const payload = props.dragPayload
  if (!payload) return
  const point = dragPoint(event)
  const target = targetAt(point, payload)
  dropPreview.value = { payload, target, at: point }
  announcement.value = target.kind === 'refuse' ? target.raison : target.libelle
}

function onDrop(event: DragEvent): void {
  event.preventDefault()
  const payload = parseDragPayload(event.dataTransfer?.getData(DRAG_MIME)) ?? props.dragPayload
  const preview = dropPreview.value
  dropPreview.value = null
  if (!payload) return

  const point = preview?.at ?? dragPoint(event)
  emit('drop-furniture', { payload, target: targetAt(point, payload) })
}

/**
 * Le survol quitte réellement la surface.
 *
 * `dragleave` remonte depuis les enfants : passer du bord du canevas au canevas lui-même en émet
 * un, et effacer l'aperçu à chaque fois le ferait clignoter tout le long du geste. On ne l'efface
 * que si la destination est en dehors de l'hôte.
 */
function onDragLeave(event: DragEvent): void {
  const target = event.relatedTarget
  if (target instanceof Node && host.value?.contains(target)) return
  dropPreview.value = null
}

const dropOutline = computed(() => {
  const preview = dropPreview.value
  if (!preview) return null
  const corners = footprintCorners(preview.at, preview.payload.width_cm, preview.payload.depth_cm, 0)
  return {
    points: corners.flatMap((corner) => {
      const screen = planToScreen(corner, viewport.value)
      return [screen.x, screen.y]
    }),
    refused: preview.target.kind === 'refuse',
    label:
      preview.target.kind === 'refuse'
        ? `⚠ ${preview.target.raison}`
        : `${preview.payload.name} — ${preview.target.libelle}`,
    at: planToScreen(preview.at, viewport.value),
  }
})

// --- Saisie numérique de la cote ------------------------------------------------------------------

/**
 * Pose le sommet à la distance saisie.
 *
 * C'est le geste central d'un relevé : on vise grossièrement la direction, on tape la mesure lue
 * au mètre laser, Entrée. Sans lui, la seule façon d'obtenir un mur de 347 cm est de viser le
 * pixel exact — c'est-à-dire de ne jamais l'obtenir.
 */
function placeTypedLength(): void {
  const origin = drawOrigin.value
  // `v-model` sur un `input[type=number]` rend un nombre, pas une chaîne — Vue applique la
  // conversion d'office. On repasse par `String` pour accepter aussi la virgule décimale saisie
  // au pavé numérique français quand le champ retombe en texte.
  const length = Number(String(typedLength.value).replace(',', '.'))
  if (!origin || !Number.isFinite(length) || length <= 0) return
  // La direction vient du curseur : on vise l'orientation à la souris, on donne la mesure au
  // clavier. Maj y ajoute la contrainte à 45°, ce qui suffit à saisir un logement d'équerre.
  const direction = cursor.value ?? { x: origin.x + 1, y: origin.y }
  const point = pointAtDistance(origin, shiftHeld.value ? snapped(direction, origin) : direction, length)
  draft.value = [...draft.value, [Math.round(point.x), Math.round(point.y)]]
  typedLength.value = ''
  announcement.value = `mur de ${length} cm posé`
}

/**
 * Touches du tracé. L'écoute est posée sur `window` — un canevas Konva ne prend pas le focus
 * clavier. Il faut donc écarter explicitement la saisie de texte : sans ce filtre, une correction
 * au clavier dans le champ « nom de pièce » supprimait un sommet du tracé, et personne ne faisait
 * le lien.
 */
function onKeydown(event: KeyboardEvent): void {
  // Maj est suivie même dans un champ : la contrainte angulaire doit rester vraie quand le focus
  // est sur la cote, sinon taper une mesure la relâcherait en silence.
  if (event.key === 'Shift') shiftHeld.value = true
  if (isTypingTarget(event.target)) return
  if (props.mode !== 'draw') return

  if (event.key === 'Escape') {
    draft.value = []
    emit('finish-drawing')
  }
  if (event.key === 'Backspace') {
    event.preventDefault()
    draft.value = draft.value.slice(0, -1)
  }
  if (event.key === 'Enter') finishDrawing()
  // Un chiffre tapé alors que le canevas a le focus bascule dans le champ de cote : c'est le
  // réflexe de quiconque a déjà utilisé un logiciel de dessin technique.
  if (/^[0-9]$/.test(event.key)) {
    typedLength.value += event.key
    lengthField.value?.focus()
  }
}

function onKeyup(event: KeyboardEvent): void {
  if (event.key === 'Shift') shiftHeld.value = false
}

function fit(): void {
  if (activePolygon.value.length >= 3) {
    const padding = Math.min(90, stage.value.width / 8)
    viewport.value = fitViewport(activePolygon.value, stage.value.width, stage.value.height, padding)
  }
}

/**
 * Amène un point du plan au centre de la surface, en centimètres.
 *
 * L'échelle est conservée : le panneau d'inspection désigne un endroit, il ne décide pas du zoom.
 * Zoomer au passage ferait perdre le repère visuel que l'utilisateur venait de se construire.
 */
function centerOn(point: Point): void {
  const { scale } = viewport.value
  viewport.value = {
    scale,
    offsetX: stage.value.width / 2 - point.x * scale,
    offsetY: stage.value.height / 2 - point.y * scale,
  }
}

/**
 * Persistance du brouillon de tracé.
 *
 * Un contour en cours de saisie n'existe que côté client : tant qu'il n'est pas fermé, rien
 * n'est envoyé au serveur. Un rafraîchissement de page ou un onglet fermé par mégarde effaçait
 * donc plusieurs minutes de relevé. La clé porte le projet **et** la pièce : c'est ce qui
 * garantit qu'un brouillon ne peut pas ressortir dans une autre pièce.
 */
function storageKey(): string | null {
  return props.draftKey ? `renovation.brouillon.${props.draftKey}` : null
}

function restoreDraft(): void {
  const key = storageKey()
  if (!key) return
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(key) ?? 'null')
    draft.value = Array.isArray(parsed) ? (parsed as number[][]) : []
  } catch {
    // Brouillon corrompu (écriture interrompue, format d'une version antérieure) : on repart
    // d'un tracé vide plutôt que de casser le montage du composant.
    draft.value = []
  }
}

function persistDraft(): void {
  const key = storageKey()
  if (!key) return
  if (draft.value.length === 0) localStorage.removeItem(key)
  else localStorage.setItem(key, JSON.stringify(draft.value))
}

watch(draft, persistDraft)

// Le calibrage repart de zéro dès qu'on quitte l'outil : deux points à moitié posés qui
// ressortiraient plus tard donneraient une échelle absurde sans que personne ne comprenne d'où.
watch(
  () => props.mode,
  (mode) => {
    if (mode !== 'calibrate') calibrationPoints.value = []
    guides.value = []
  },
)

/** Suit la largeur disponible ; ne recadre qu'au changement de largeur, car la hauteur bouge à
 *  chaque apparition de la barre d'adresse sur mobile et recadrer sans arrêt serait insupportable. */
function observeSurface(): void {
  if (typeof ResizeObserver === 'undefined' || !host.value) return
  surfaceObserver = new ResizeObserver((entries) => {
    const box = entries[0]?.contentRect
    if (!box || box.width < 1) return
    const widthChanged = Math.round(box.width) !== stage.value.width
    stage.value = { width: Math.round(box.width), height: Math.round(box.height) }
    if (widthChanged) fit()
  })
  surfaceObserver.observe(host.value)
}

onMounted(() => {
  restoreDraft()
  window.addEventListener('keydown', onKeydown)
  window.addEventListener('keyup', onKeyup)
  observeSurface()
  fit()
})
onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('keyup', onKeyup)
  surfaceObserver?.disconnect()
})

// Recadre au changement de pièce, pas à chaque micro-édition.
watch(() => props.faces.map((face) => face.id).join(','), fit)

defineExpose({ fit, zoomAt, centerOn })
</script>

<template>
  <div class="plan">
    <div
      ref="host"
      class="surface"
      :data-mode="mode"
      role="application"
      :aria-label="`Plan de ${roomName || 'la pièce'}`"
      @dragover="onDragOver"
      @drop="onDrop"
      @dragleave="onDragLeave"
    >
      <VStage
        :config="{ width: stage.width, height: stage.height }"
        @click="onStageClick"
        @pointermove="onPointerMove"
        @pointerdown="onStagePointerDown"
        @pointerup="releasePointer"
        @pointerleave="releasePointer"
        @pointercancel="releasePointer"
        @wheel="onWheel"
      >
        <VLayer :config="{ listening: false }">
          <VImage
            v-if="backgroundConfig"
            :config="backgroundConfig"
          />
          <VLine
            v-for="(line, index) in grid.fine"
            :key="`g-${index}`"
            :config="{ points: line, stroke: '#e6eaef', strokeWidth: 1 }"
          />
          <VLine
            v-for="(line, index) in grid.coarse"
            :key="`gm-${index}`"
            :config="{ points: line, stroke: '#cfd8e3', strokeWidth: 1 }"
          />
          <VLine
            v-if="floorPoints.length >= 6"
            :config="{ points: floorPoints, closed: true, fill: '#f7f4ef', opacity: 0.85 }"
          />
        </VLayer>

        <VLayer>
          <VLine
            v-for="shape in wallShapes"
            :key="`w-${shape.key}`"
            :config="{
              points: shape.points,
              closed: true,
              fill: shape.selected ? '#0b4fd6' : shape.hovered ? '#55657a' : '#2b3440',
              stroke: shape.selected ? '#0b4fd6' : '#1b222b',
              strokeWidth: 1,
            }"
            @click="shape.wall.face && emit('select-face', shape.wall.face.label)"
            @pointerenter="hoveredLabel = shape.wall.face?.label ?? null"
            @pointerleave="hoveredLabel = null"
          />

          <template
            v-for="opening in openings"
            :key="`o-${opening.element.id}`"
          >
            <VLine :config="{ points: opening.gap, closed: true, fill: '#f7f4ef' }" />
            <VLine
              v-for="(stroke, index) in opening.strokes"
              :key="`os-${opening.element.id}-${index}`"
              :config="{ points: stroke, stroke: '#1b222b', strokeWidth: 1.6 }"
            />
            <VArc
              v-if="opening.arc"
              :config="{
                x: opening.arc.x,
                y: opening.arc.y,
                innerRadius: opening.arc.radius,
                outerRadius: opening.arc.radius,
                angle: opening.arc.to,
                rotation: opening.arc.from,
                stroke: '#95a0b0',
                strokeWidth: 1,
                dash: [4, 3],
              }"
            />
          </template>

          <template
            v-for="item in furniture"
            :key="`f-${item.element.id}`"
          >
            <VLine
              :config="{
                points: item.outline,
                closed: true,
                fill: item.element.colors.corps ?? item.element.colors.structure ?? '#cbd5e1',
                opacity: 0.9,
                stroke: item.selected ? '#0b4fd6' : '#475569',
                strokeWidth: item.selected ? 2.5 : 1,
              }"
              @pointerdown="onElementPointerDown(item.element, $event)"
            />
            <VText
              :config="{
                x: planToScreen(item.center, viewport).x - 45,
                y: planToScreen(item.center, viewport).y - 6,
                width: 90,
                align: 'center',
                text: item.label,
                fontSize: 10,
                fill: '#1b222b',
                listening: false,
              }"
            />
          </template>

          <template
            v-for="item in freeFurniture"
            :key="`l-${item.element.id}`"
          >
            <VLine
              :config="{
                points: item.outline,
                closed: true,
                fill: item.element.colors.corps ?? item.element.colors.structure ?? '#dbeafe',
                opacity: 0.92,
                stroke: item.selected ? '#0b4fd6' : '#334155',
                strokeWidth: item.selected ? 2.5 : 1.2,
                dash: item.selected ? [] : [6, 3],
              }"
              @pointerdown="onElementPointerDown(item.element, $event)"
            />
            <VText
              :config="{
                x: planToScreen(item.center, viewport).x - 45,
                y: planToScreen(item.center, viewport).y - 6,
                width: 90,
                align: 'center',
                text: item.label,
                fontSize: 10,
                fill: '#1b222b',
                listening: false,
              }"
            />
          </template>
        </VLayer>

        <VLayer :config="{ listening: false }">
          <VLine
            v-for="(guide, index) in guideLines"
            :key="`gd-${index}`"
            :config="{
              points: guide.points,
              stroke: guide.kind === 'angle' ? '#c026d3' : '#0ea5e9',
              strokeWidth: 1,
              dash: [5, 4],
            }"
          />

          <template
            v-for="dimension in dimensions"
            :key="`d-${dimension.key}`"
          >
            <VLine :config="{ points: dimension.line, stroke: '#95a0b0', strokeWidth: 1 }" />
            <VLine
              v-for="(tick, index) in dimension.ticks"
              :key="`t-${dimension.key}-${index}`"
              :config="{ points: tick, stroke: '#ccd3dc', strokeWidth: 1 }"
            />
            <VLabel
              :config="{
                x: dimension.screenLabel.x,
                y: dimension.screenLabel.y,
                offsetX: 36,
                offsetY: 9,
              }"
            >
              <VTag :config="{ fill: '#ffffff', cornerRadius: 3 }" />
              <VText
                :config="{
                  text: `${dimension.wall.face?.label ?? ''} · ${dimension.text} cm`,
                  fontSize: 11,
                  padding: 3,
                  width: 72,
                  align: 'center',
                  fill: dimension.wall.face?.label === selectedFaceLabel ? '#0b4fd6' : '#414a56',
                  fontStyle:
                    dimension.wall.face?.label === selectedFaceLabel ? 'bold' : 'normal',
                }"
              />
            </VLabel>
          </template>

          <VText
            v-if="centroidScreen && roomName"
            :config="{
              x: centroidScreen.x - 90,
              y: centroidScreen.y - 16,
              width: 180,
              align: 'center',
              text: `${roomName}\n${area.toFixed(2)} m²`,
              fontSize: 13,
              lineHeight: 1.5,
              fill: '#414a56',
            }"
          />

          <VLine
            v-if="previewLine.length === 4"
            :config="{ points: previewLine, stroke: '#0b4fd6', dash: [6, 4], strokeWidth: 1.5 }"
          />
          <VLabel
            v-if="previewLengthCm !== null && cursor"
            :config="{
              x: planToScreen(cursor, viewport).x + 12,
              y: planToScreen(cursor, viewport).y - 26,
            }"
          >
            <VTag :config="{ fill: '#0b4fd6', cornerRadius: 3 }" />
            <VText
              :config="{
                text: `${previewLengthCm} cm`,
                fontSize: 11,
                padding: 4,
                fill: '#ffffff',
              }"
            />
          </VLabel>

          <VLine
            v-if="dropOutline"
            :config="{
              points: dropOutline.points,
              closed: true,
              fill: dropOutline.refused ? '#fee2e2' : '#dbeafe',
              opacity: 0.75,
              stroke: dropOutline.refused ? '#b91c1c' : '#0b4fd6',
              strokeWidth: 2,
              dash: [6, 4],
            }"
          />
          <VLabel
            v-if="dropOutline"
            :config="{ x: dropOutline.at.x + 14, y: dropOutline.at.y + 14 }"
          >
            <VTag :config="{ fill: dropOutline.refused ? '#b91c1c' : '#0b4fd6', cornerRadius: 3 }" />
            <VText
              :config="{
                text: dropOutline.label,
                fontSize: 11,
                padding: 4,
                fill: '#ffffff',
              }"
            />
          </VLabel>

          <VRect
            v-if="marqueeRect"
            :config="{
              x: marqueeRect.x,
              y: marqueeRect.y,
              width: marqueeRect.width,
              height: marqueeRect.height,
              fill: 'rgba(11, 79, 214, 0.12)',
              stroke: '#0b4fd6',
              strokeWidth: 1,
              dash: [4, 3],
            }"
          />

          <VGroup v-if="mode === 'calibrate'">
            <VCircle
              v-for="(point, index) in calibrationPoints"
              :key="`c-${index}`"
              :config="{
                x: planToScreen(point, viewport).x,
                y: planToScreen(point, viewport).y,
                radius: 6,
                fill: '#c026d3',
                stroke: '#ffffff',
                strokeWidth: 2,
              }"
            />
            <VLine
              v-if="calibrationPoints.length === 2"
              :config="{
                points: [
                  planToScreen(calibrationPoints[0]!, viewport).x,
                  planToScreen(calibrationPoints[0]!, viewport).y,
                  planToScreen(calibrationPoints[1]!, viewport).x,
                  planToScreen(calibrationPoints[1]!, viewport).y,
                ],
                stroke: '#c026d3',
                strokeWidth: 2,
              }"
            />
          </VGroup>
        </VLayer>

        <VLayer>
          <VCircle
            v-for="(vertex, index) in activePolygon"
            :key="`v-${index}`"
            :config="{
              x: planToScreen({ x: vertex[0] as number, y: vertex[1] as number }, viewport).x,
              y: planToScreen({ x: vertex[0] as number, y: vertex[1] as number }, viewport).y,
              radius: mode === 'edit' ? 7 : 4,
              fill: mode === 'edit' ? '#0b4fd6' : '#2b3440',
              stroke: '#ffffff',
              strokeWidth: 2,
            }"
            @pointerdown="startDraggingVertex(index, $event)"
          />

          <template v-if="handles">
            <VLine
              :config="{
                points: [handles.center.x, handles.center.y, handles.rotate.x, handles.rotate.y],
                stroke: '#0b4fd6',
                strokeWidth: 1,
                dash: [3, 3],
                listening: false,
              }"
            />
            <VCircle
              :config="{
                x: handles.rotate.x,
                y: handles.rotate.y,
                radius: 8,
                fill: '#ffffff',
                stroke: '#0b4fd6',
                strokeWidth: 2,
              }"
              @pointerdown="startRotate"
            />
            <VRect
              :config="{
                x: handles.resize.x - 6,
                y: handles.resize.y - 6,
                width: 12,
                height: 12,
                fill: '#ffffff',
                stroke: '#0b4fd6',
                strokeWidth: 2,
              }"
              @pointerdown="startResize"
            />
          </template>
        </VLayer>
      </VStage>

      <!-- Saisie de la cote : un vrai champ, donc un chemin clavier complet et un focus visible.
           Il est prêt en permanence pendant le tracé — taper un chiffre y bascule directement. -->
      <div
        v-if="mode === 'draw' && draft.length > 0"
        class="cote-saisie"
      >
        <label for="cote-mur">Longueur du mur (cm)</label>
        <input
          id="cote-mur"
          ref="lengthField"
          v-model="typedLength"
          type="number"
          min="1"
          inputmode="numeric"
          autocomplete="off"
          placeholder="ex. 347"
          @keydown.enter.prevent="placeTypedLength"
          @keydown.esc.prevent="typedLength = ''"
        >
        <button
          type="button"
          @click="placeTypedLength"
        >
          Poser
        </button>
      </div>
    </div>

    <p
      v-if="selfIntersecting"
      class="alerte"
      role="alert"
    >
      Le contour se recoupe : la pièce n'a pas d'intérieur défini et ne pourra pas être extrudée
      en 3D.
    </p>

    <p
      v-if="backgroundWarning"
      class="alerte alerte-douce"
      role="status"
    >
      Fond de plan <strong>non calibré</strong> : l'échelle affichée est provisoire. Utilisez
      l'outil de calibrage avant de relever des cotes dessus.
    </p>

    <div class="legende">
      <span aria-live="polite">
        {{ activePolygon.length }} sommet(s) · {{ walls.length }} mur(s) ·
        <strong>{{ area.toFixed(2) }} m²</strong> ·
        périmètre {{ formatLengthCm(perimeter) }}
      </span>
      <span class="aide">
        Molette : zoom · Alt ou clic milieu : déplacer · glisser : encadrer
        <template v-if="mode === 'draw'"> · Entrée : fermer · Échap : annuler</template>
        <template v-if="mode === 'calibrate'"> · deux clics sur une distance connue</template>
      </span>
    </div>
    <p
      class="annonce"
      aria-live="polite"
    >
      {{ announcement }}
    </p>
  </div>
</template>

<style scoped>
.plan {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.surface {
  /* La hauteur suit l'écran : sur un téléphone tenu à la verticale, 620 px figés ne laissaient
     plus rien voir du reste de la page. */
  position: relative;
  height: clamp(18rem, 62vh, 38.75rem);
  border: 1px solid var(--bordure);
  border-radius: 0.5rem;
  background: #fdfdfc;
  overflow: hidden;
  touch-action: none;
}

.surface[data-mode='draw'] {
  cursor: crosshair;
}

.surface[data-mode='edit'] {
  cursor: grab;
}

.surface[data-mode='calibrate'] {
  cursor: cell;
}

.cote-saisie {
  position: absolute;
  left: 0.75rem;
  bottom: 0.75rem;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.65rem;
  border: 1px solid var(--bordure);
  border-radius: 0.4rem;
  background: #ffffff;
  box-shadow: 0 2px 10px rgb(0 0 0 / 12%);
}

.cote-saisie label {
  margin: 0;
  font-size: 0.85rem;
  font-weight: 600;
}

.cote-saisie input {
  width: 7rem;
}

.alerte {
  margin: 0;
  padding: 0.5rem 0.75rem;
  border-radius: 0.35rem;
  background: #fdecea;
  color: #7a1010;
  font-weight: 600;
}

/* Contraste 7:1 sur fond clair (WCAG AAA) : un avertissement doit rester lisible pour tout le
   monde, y compris celui qui a le plus besoin de le lire. */
.alerte-douce {
  background: #fdf3d8;
  color: #5a4300;
}

.legende {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 1rem;
  color: var(--texte-doux);
  font-variant-numeric: tabular-nums;
}

.aide {
  font-size: 0.9rem;
}

.annonce {
  margin: 0;
  min-height: 1.2rem;
  color: var(--texte-doux);
  font-size: 0.85rem;
}
</style>
