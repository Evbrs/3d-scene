<script setup lang="ts">
/**
 * Canvas de saisie du plan 2D (Konva).
 *
 * Trois modes : navigation, tracé d'un polygone, déplacement de sommets. Le composant n'écrit
 * jamais en base directement : il émet le polygone, la vue parente décide quand enregistrer.
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'

import {
  DEFAULT_VIEWPORT,
  areaInSquareMeters,
  fitViewport,
  isSelfIntersecting,
  midpoint,
  planToScreen,
  screenToPlan,
  segmentLength,
  snapPoint,
  wallSegments,
  type Point,
  type Viewport,
} from '@/editor/geometry'

const props = withDefaults(
  defineProps<{
    polygon: number[][]
    selectedFaceLabel?: string | null
    mode?: 'navigate' | 'draw' | 'edit'
    gridCm?: number
    width?: number
    height?: number
  }>(),
  { selectedFaceLabel: null, mode: 'navigate', gridCm: 10, width: 900, height: 600 },
)

const emit = defineEmits<{
  (event: 'update:polygon', polygon: number[][]): void
  (event: 'select-face', label: string): void
  (event: 'finish-drawing'): void
}>()

const stage = ref<HTMLDivElement | null>(null)
const viewport = ref<Viewport>({ ...DEFAULT_VIEWPORT })
const draft = ref<number[][]>([])
const cursor = ref<Point | null>(null)
const draggedVertex = ref<number | null>(null)

const activePolygon = computed(() => (props.mode === 'draw' ? draft.value : props.polygon))

const segments = computed(() => wallSegments(activePolygon.value))

const selfIntersecting = computed(() => isSelfIntersecting(activePolygon.value))

const area = computed(() => areaInSquareMeters(activePolygon.value))

/** Points du polygone en coordonnées écran, aplatis pour `<v-line>`. */
const flatScreenPoints = computed(() =>
  activePolygon.value.flatMap((vertex) => {
    const screen = planToScreen({ x: vertex[0] as number, y: vertex[1] as number }, viewport.value)
    return [screen.x, screen.y]
  }),
)

const previewLine = computed(() => {
  if (props.mode !== 'draw' || draft.value.length === 0 || !cursor.value) return []
  const last = draft.value[draft.value.length - 1] as [number, number]
  const from = planToScreen({ x: last[0], y: last[1] }, viewport.value)
  const to = planToScreen(cursor.value, viewport.value)
  return [from.x, from.y, to.x, to.y]
})

function pointerToPlan(event: { evt: PointerEvent }): Point {
  const bounds = stage.value?.getBoundingClientRect()
  const raw = {
    x: event.evt.clientX - (bounds?.left ?? 0),
    y: event.evt.clientY - (bounds?.top ?? 0),
  }
  return snapPoint(screenToPlan(raw, viewport.value), props.gridCm)
}

function onStageClick(event: { evt: PointerEvent }): void {
  if (props.mode !== 'draw') return
  const point = pointerToPlan(event)

  // Refermer le contour en recliquant sur le premier sommet.
  const first = draft.value[0]
  if (first && draft.value.length >= 3) {
    const distance = segmentLength({ x: first[0] as number, y: first[1] as number }, point)
    if (distance <= props.gridCm * 2) {
      emit('update:polygon', [...draft.value])
      emit('finish-drawing')
      draft.value = []
      return
    }
  }
  draft.value = [...draft.value, [point.x, point.y]]
}

function onPointerMove(event: { evt: PointerEvent }): void {
  cursor.value = pointerToPlan(event)
  if (draggedVertex.value !== null && props.mode === 'edit') {
    const updated = props.polygon.map((vertex, index) =>
      index === draggedVertex.value ? [cursor.value!.x, cursor.value!.y] : vertex,
    )
    emit('update:polygon', updated)
  }
}

function startDragging(index: number): void {
  if (props.mode === 'edit') draggedVertex.value = index
}

function stopDragging(): void {
  draggedVertex.value = null
}

function undoLastPoint(): void {
  draft.value = draft.value.slice(0, -1)
}

function onKeydown(event: KeyboardEvent): void {
  if (props.mode !== 'draw') return
  if (event.key === 'Escape') {
    draft.value = []
    emit('finish-drawing')
  }
  if (event.key === 'Backspace') {
    event.preventDefault()
    undoLastPoint()
  }
  if (event.key === 'Enter' && draft.value.length >= 3) {
    emit('update:polygon', [...draft.value])
    emit('finish-drawing')
    draft.value = []
  }
}

function fit(): void {
  if (activePolygon.value.length >= 3) {
    viewport.value = fitViewport(activePolygon.value, props.width, props.height)
  }
}

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  fit()
})
onUnmounted(() => window.removeEventListener('keydown', onKeydown))

defineExpose({ fit, undoLastPoint })
</script>

<template>
  <div class="canvas-shell">
    <div
      ref="stage"
      class="canvas-host"
      :data-mode="mode"
    >
      <v-stage
        :config="{ width, height }"
        @click="onStageClick"
        @pointermove="onPointerMove"
        @pointerup="stopDragging"
      >
        <v-layer>
          <!-- Contour de la pièce -->
          <v-line
            v-if="flatScreenPoints.length >= 4"
            :config="{
              points: flatScreenPoints,
              closed: mode !== 'draw',
              stroke: selfIntersecting ? '#b00020' : '#1f2933',
              strokeWidth: 2,
              fill: mode === 'draw' ? undefined : 'rgba(31, 41, 51, 0.06)',
            }"
          />

          <!-- Segment en cours de tracé -->
          <v-line
            v-if="previewLine.length === 4"
            :config="{ points: previewLine, stroke: '#8a8a8a', dash: [6, 4], strokeWidth: 1 }"
          />

          <!-- Cotes et étiquettes des murs -->
          <template
            v-for="segment in segments"
            :key="segment.label"
          >
            <v-text
              :config="{
                x: planToScreen(midpoint(segment.from, segment.to), viewport).x - 26,
                y: planToScreen(midpoint(segment.from, segment.to), viewport).y - 20,
                text: `${segment.label} · ${Math.round(segmentLength(segment.from, segment.to))} cm`,
                fontSize: 12,
                fill: segment.label === selectedFaceLabel ? '#0b5fff' : '#4a4a4a',
                fontStyle: segment.label === selectedFaceLabel ? 'bold' : 'normal',
              }"
              @click="emit('select-face', segment.label)"
            />
          </template>

          <!-- Sommets manipulables -->
          <template
            v-for="(vertex, index) in activePolygon"
            :key="`v-${index}`"
          >
            <v-circle
              :config="{
                x: planToScreen({ x: vertex[0] as number, y: vertex[1] as number }, viewport).x,
                y: planToScreen({ x: vertex[0] as number, y: vertex[1] as number }, viewport).y,
                radius: 6,
                fill: mode === 'edit' ? '#0b5fff' : '#1f2933',
              }"
              @pointerdown="startDragging(index)"
            />
          </template>
        </v-layer>
      </v-stage>
    </div>

    <p
      v-if="selfIntersecting"
      class="warning"
      role="alert"
    >
      Le contour se recoupe : la pièce n'a pas d'intérieur défini et ne pourra pas être extrudée
      en 3D.
    </p>
    <p
      class="readout"
      aria-live="polite"
    >
      {{ activePolygon.length }} sommet(s) · {{ segments.length }} mur(s) ·
      {{ area.toFixed(2) }} m²
    </p>
  </div>
</template>

<style scoped>
.canvas-shell {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.canvas-host {
  border: 1px solid #d4d4d4;
  border-radius: 0.5rem;
  background: #fbfbfb;
  overflow: hidden;
}

.canvas-host[data-mode='draw'] {
  cursor: crosshair;
}

.warning {
  margin: 0;
  color: #8a1010;
  font-weight: 600;
}

.readout {
  margin: 0;
  color: #4a4a4a;
  font-variant-numeric: tabular-nums;
}
</style>
