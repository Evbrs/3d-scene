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

import type { Face, PlanElement } from '@/api/types'
import {
  dimensionLine,
  furnitureFootprint,
  gridLines,
  isOpening,
  openingSymbol,
  wallGeometries,
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

const grid = computed(() => gridLines(props.width, props.height, viewport.value, props.gridCm * 10))

const floorPoints = computed(() =>
  activePolygon.value.flatMap((vertex) => {
    const screen = planToScreen({ x: vertex[0] as number, y: vertex[1] as number }, viewport.value)
    return [screen.x, screen.y]
  }),
)

const wallShapes = computed(() =>
  walls.value.map((wall) => ({
    wall,
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
  return walls.value.map((wall) => {
    const line = dimensionLine(wall, offset, viewport.value)
    return { wall, ...line, screenLabel: planToScreen(line.labelAt, viewport.value) }
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

function onKeydown(event: KeyboardEvent): void {
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
}

function fit(): void {
  if (activePolygon.value.length >= 3) {
    viewport.value = fitViewport(activePolygon.value, props.width, props.height, 90)
  }
}

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  fit()
})
onUnmounted(() => window.removeEventListener('keydown', onKeydown))

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
      <v-stage
        :config="{ width, height }"
        @click="onStageClick"
        @pointermove="onPointerMove"
        @pointerdown="startPanning"
        @pointerup="releasePointer"
        @pointerleave="releasePointer"
        @wheel="onWheel"
      >
        <v-layer :config="{ listening: false }">
          <v-line
            v-for="(line, index) in grid"
            :key="`g-${index}`"
            :config="{ points: line, stroke: '#e6eaef', strokeWidth: 1 }"
          />
          <v-line
            v-if="floorPoints.length >= 6"
            :config="{ points: floorPoints, closed: true, fill: '#f7f4ef' }"
          />
        </v-layer>

        <v-layer>
          <v-line
            v-for="shape in wallShapes"
            :key="`w-${shape.wall.face?.id ?? shape.wall.from.x}`"
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
            <v-line :config="{ points: opening.gap, closed: true, fill: '#f7f4ef' }" />
            <v-line
              v-for="(stroke, index) in opening.strokes"
              :key="`os-${opening.element.id}-${index}`"
              :config="{ points: stroke, stroke: '#1b222b', strokeWidth: 1.6 }"
            />
            <v-arc
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
            <v-line
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
            <v-text
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
        </v-layer>

        <v-layer :config="{ listening: false }">
          <template
            v-for="dimension in dimensions"
            :key="`d-${dimension.wall.face?.id ?? dimension.text}`"
          >
            <v-line :config="{ points: dimension.line, stroke: '#95a0b0', strokeWidth: 1 }" />
            <v-line
              v-for="(tick, index) in dimension.ticks"
              :key="`t-${index}`"
              :config="{ points: tick, stroke: '#ccd3dc', strokeWidth: 1 }"
            />
            <v-label
              :config="{
                x: dimension.screenLabel.x,
                y: dimension.screenLabel.y,
                offsetX: 36,
                offsetY: 9,
              }"
            >
              <v-tag :config="{ fill: '#ffffff', cornerRadius: 3 }" />
              <v-text
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
            </v-label>
          </template>

          <v-text
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

          <v-line
            v-if="previewLine.length === 4"
            :config="{ points: previewLine, stroke: '#0b4fd6', dash: [6, 4], strokeWidth: 1.5 }"
          />
          <v-label
            v-if="previewLengthCm !== null && cursor"
            :config="{
              x: planToScreen(cursor, viewport).x + 12,
              y: planToScreen(cursor, viewport).y - 26,
            }"
          >
            <v-tag :config="{ fill: '#0b4fd6', cornerRadius: 3 }" />
            <v-text
              :config="{
                text: `${previewLengthCm} cm`,
                fontSize: 11,
                padding: 4,
                fill: '#ffffff',
              }"
            />
          </v-label>
        </v-layer>

        <v-layer>
          <v-circle
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
        </v-layer>
      </v-stage>
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
