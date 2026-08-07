<script setup lang="ts">
/**
 * Plan 2D interactif (Konva).
 *
 * Dessine un vrai plan d'architecte : murs épais, ouvertures avec leur symbole et leur
 * débattement, emprise des meubles, cotes déportées, grille. Le composant n'écrit jamais en
 * base : il émet, la vue parente décide quand enregistrer.
 *
 * **Le contour n'est émis qu'au relâchement de la souris.** Émettre à chaque déplacement
 * déclencherait une requête par pixel parcouru — donc, sur un contour qui se déforme, une
 * cascade de conflits de version et de confirmations.
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
// Enregistrement local plutôt que `app.use(VueKonva)` : globalement installé, Konva (55 Ko
// gzip) atterrissait dans le chunk d'entrée, donc sur l'écran de connexion et sur la page de
// partage publique, qui n'affichent jamais de plan 2D.
import {
  Arc as VArc,
  Circle as VCircle,
  Label as VLabel,
  Layer as VLayer,
  Line as VLine,
  Stage as VStage,
  Tag as VTag,
  Text as VText,
} from 'vue-konva'

import type { Face, PlanElement } from '@/api/types'
import {
  dimensionLine,
  furnitureFootprint,
  gridLines,
  isOpening,
  openingSymbol,
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

const props = withDefaults(
  defineProps<{
    polygon: number[][]
    faces: Face[]
    roomName?: string
    wallThicknessCm?: number
    selectedFaceLabel?: string | null
    mode?: 'navigate' | 'draw' | 'edit'
    gridCm?: number
    width?: number
    height?: number
    furnitureNames?: Record<number, string>
    /** Identifiant de persistance du brouillon, de la forme `projet:piece`. */
    draftKey?: string | null
  }>(),
  {
    roomName: '',
    wallThicknessCm: 10,
    selectedFaceLabel: null,
    mode: 'navigate',
    gridCm: 10,
    width: 960,
    height: 620,
    furnitureNames: () => ({}),
    draftKey: null,
  },
)

const emit = defineEmits<{
  (event: 'update:polygon', polygon: number[][]): void
  (event: 'select-face', label: string): void
  (event: 'select-element', element: PlanElement): void
  (event: 'finish-drawing'): void
}>()

const host = ref<HTMLDivElement | null>(null)
const viewport = ref<Viewport>({ ...DEFAULT_VIEWPORT })
const draft = ref<number[][]>([])
const cursor = ref<Point | null>(null)
const hoveredLabel = ref<string | null>(null)

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

const activePolygon = computed(() => {
  if (props.mode === 'draw') return draft.value
  return dragging.value?.polygon ?? props.polygon
})

const walls = computed(() => wallGeometries(activePolygon.value, props.faces))
const selfIntersecting = computed(() => isSelfIntersecting(activePolygon.value))
const area = computed(() => areaInSquareMeters(activePolygon.value))

const grid = computed(() =>
  gridLines(stage.value.width, stage.value.height, viewport.value, props.gridCm * 10),
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

const furniture = computed(() =>
  walls.value.flatMap((wall) =>
    (wall.face?.elements ?? [])
      .filter((element) => !isOpening(element))
      .map((element) =>
        furnitureFootprint(
          wall,
          element,
          props.wallThicknessCm,
          viewport.value,
          props.furnitureNames[element.furniture_type_id ?? -1] ?? 'Meuble',
        ),
      ),
  ),
)

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

function pointerToPlan(event: { evt: PointerEvent }): Point {
  const bounds = host.value?.getBoundingClientRect()
  const raw = {
    x: event.evt.clientX - (bounds?.left ?? 0),
    y: event.evt.clientY - (bounds?.top ?? 0),
  }
  return snapPoint(screenToPlan(raw, viewport.value), props.gridCm)
}

function finishDrawing(): void {
  if (draft.value.length >= 3) emit('update:polygon', [...draft.value])
  draft.value = []
  emit('finish-drawing')
}

function onStageClick(event: { evt: PointerEvent }): void {
  if (props.mode !== 'draw') return
  const point = pointerToPlan(event)

  const first = draft.value[0]
  if (first && draft.value.length >= 3) {
    const distance = segmentLength({ x: first[0] as number, y: first[1] as number }, point)
    if (distance <= Math.max(props.gridCm * 2, 20 / viewport.value.scale)) {
      finishDrawing()
      return
    }
  }
  draft.value = [...draft.value, [point.x, point.y]]
}

function onPointerMove(event: { evt: PointerEvent }): void {
  cursor.value = pointerToPlan(event)

  if (panning.value) {
    viewport.value = {
      ...viewport.value,
      offsetX: viewport.value.offsetX + (event.evt.clientX - panning.value.x),
      offsetY: viewport.value.offsetY + (event.evt.clientY - panning.value.y),
    }
    panning.value = { x: event.evt.clientX, y: event.evt.clientY }
    return
  }

  if (dragging.value && cursor.value) {
    const index = dragging.value.index
    const moved = cursor.value
    dragging.value = {
      index,
      polygon: props.polygon.map((vertex, position) =>
        position === index ? [moved.x, moved.y] : vertex,
      ),
    }
  }
}

function startDraggingVertex(index: number, event: { evt: PointerEvent }): void {
  if (props.mode !== 'edit') return
  event.evt.stopPropagation()
  dragging.value = { index, polygon: props.polygon.map((vertex) => [...vertex]) }
}

function releasePointer(): void {
  panning.value = null
  if (!dragging.value) return
  const moved = dragging.value.polygon
  dragging.value = null
  // Émis une seule fois, au relâchement.
  emit('update:polygon', moved)
}

function startPanning(event: { evt: PointerEvent }): void {
  if (props.mode === 'draw' || dragging.value) return
  panning.value = { x: event.evt.clientX, y: event.evt.clientY }
}

function onWheel(event: { evt: WheelEvent }): void {
  event.evt.preventDefault()
  const bounds = host.value?.getBoundingClientRect()
  const pointer = {
    x: event.evt.clientX - (bounds?.left ?? 0),
    y: event.evt.clientY - (bounds?.top ?? 0),
  }
  const before = screenToPlan(pointer, viewport.value)
  const factor = event.evt.deltaY < 0 ? 1.12 : 1 / 1.12
  const scale = Math.min(Math.max(viewport.value.scale * factor, 0.02), 12)
  // Le zoom garde sous le curseur le point du plan qui s'y trouvait.
  viewport.value = {
    scale,
    offsetX: pointer.x - before.x * scale,
    offsetY: pointer.y - before.y * scale,
  }
}

const TYPING_TAGS = new Set(['INPUT', 'TEXTAREA', 'SELECT'])

/**
 * L'écoute est posée sur `window` — un canvas Konva ne prend pas le focus clavier. Il faut donc
 * écarter explicitement la saisie de texte : sans ce filtre, une correction au clavier dans le
 * champ « nom de pièce » supprimait un sommet du tracé, et personne ne faisait le lien.
 */
function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  return TYPING_TAGS.has(target.tagName) || target.isContentEditable
}

function onKeydown(event: KeyboardEvent): void {
  if (props.mode !== 'draw' || isTypingTarget(event.target)) return
  if (event.key === 'Escape') {
    draft.value = []
    emit('finish-drawing')
  }
  if (event.key === 'Backspace') {
    event.preventDefault()
    draft.value = draft.value.slice(0, -1)
  }
  if (event.key === 'Enter') finishDrawing()
}

function fit(): void {
  if (activePolygon.value.length >= 3) {
    const padding = Math.min(90, stage.value.width / 8)
    viewport.value = fitViewport(activePolygon.value, stage.value.width, stage.value.height, padding)
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
  observeSurface()
  fit()
})
onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
  surfaceObserver?.disconnect()
})

// Recadre au changement de pièce, pas à chaque micro-édition.
watch(() => props.faces.map((face) => face.id).join(','), fit)

defineExpose({ fit })
</script>

<template>
  <div class="plan">
    <div
      ref="host"
      class="surface"
      :data-mode="mode"
    >
      <VStage
        :config="{ width: stage.width, height: stage.height }"
        @click="onStageClick"
        @pointermove="onPointerMove"
        @pointerdown="startPanning"
        @pointerup="releasePointer"
        @pointerleave="releasePointer"
        @wheel="onWheel"
      >
        <VLayer :config="{ listening: false }">
          <VLine
            v-for="(line, index) in grid"
            :key="`g-${index}`"
            :config="{ points: line, stroke: '#e6eaef', strokeWidth: 1 }"
          />
          <VLine
            v-if="floorPoints.length >= 6"
            :config="{ points: floorPoints, closed: true, fill: '#f7f4ef' }"
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
                stroke: '#475569',
                strokeWidth: 1,
              }"
              @click="emit('select-element', item.element)"
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
        </VLayer>
      </VStage>
    </div>

    <p
      v-if="selfIntersecting"
      class="alerte"
      role="alert"
    >
      Le contour se recoupe : la pièce n'a pas d'intérieur défini et ne pourra pas être extrudée
      en 3D.
    </p>

    <div class="legende">
      <span aria-live="polite">
        {{ activePolygon.length }} sommet(s) · {{ walls.length }} mur(s) ·
        <strong>{{ area.toFixed(2) }} m²</strong>
      </span>
      <span class="aide">
        Molette : zoom · glisser le fond : déplacer
        <template v-if="mode === 'draw'"> · Entrée : fermer · Échap : annuler</template>
      </span>
    </div>
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

.alerte {
  margin: 0;
  padding: 0.5rem 0.75rem;
  border-radius: 0.35rem;
  background: #fdecea;
  color: #7a1010;
  font-weight: 600;
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
</style>
