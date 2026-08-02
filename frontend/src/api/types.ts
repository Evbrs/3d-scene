/**
 * Types de l'API, transcrits depuis le schéma OpenAPI servi par le backend.
 *
 * `docs/plan-generation-ia.md` §6 : le schéma OpenAPI (`/openapi.json`) est la **source de
 * vérité** des routes et des formats. Ces types en sont la transcription manuelle ; ils ne
 * doivent jamais diverger de ce que le backend publie. Le test `api-contract.spec.ts` compare
 * les deux automatiquement.
 */

export type FaceKind = 'wall' | 'floor' | 'ceiling'

export type ElementKind = 'door_hinged' | 'door_sliding' | 'window' | 'furniture'

export type LayingPattern = 'straight' | 'staggered' | 'chevron' | 'herringbone'

export interface Covering {
  color?: string | null
  material?: string | null
  unit_width_cm?: number | null
  unit_height_cm?: number | null
  pattern?: LayingPattern | null
}

export interface PlanElement {
  id: number
  face_id: number
  kind: ElementKind
  x_offset_cm: number
  y_offset_cm: number
  width_cm: number
  height_cm: number
  depth_cm: number
  rotation_deg: number
  furniture_type_id: number | null
  colors: Record<string, string>
  variant_params: Record<string, string | number | boolean | null>
}

export interface Face {
  id: number
  room_id: number
  label: string
  kind: FaceKind
  start_x_cm: number | null
  start_y_cm: number | null
  end_x_cm: number | null
  end_y_cm: number | null
  covering: Covering
  elements: PlanElement[]
}

export interface Room {
  id: number
  project_id: number
  name: string
  wall_thickness_cm: number
  ceiling_height_cm: number
  polygon: number[][]
  faces: Face[]
}

export interface ProjectSummary {
  id: number
  name: string
  description: string | null
  version: number
  created_at: string
  updated_at: string
}

export interface Project extends ProjectSummary {
  rooms: Room[]
}

export interface Page<T> {
  total: number
  limit: number
  offset: number
  items: T[]
}

export interface FurnitureType {
  id: number
  slug: string
  name: string
  category: string
  color_slots: string[]
  parts: Record<string, unknown>[]
  default_width_cm: number
  default_height_cm: number
  default_depth_cm: number
}

// --- Scene graph (P6) -------------------------------------------------------------------------

export interface WallNode {
  kind: 'wall'
  face_id: number
  face_label: string
  length_cm: number
  height_cm: number
  origin: [number, number, number]
  rotation_y: number
  outward_normal: [number, number, number]
  outline: number[][]
  holes: number[][][]
  extrude_depth_cm: number
  extrude_offset_cm: number
  covering: Covering
}

export interface HorizontalNode {
  kind: 'floor' | 'ceiling'
  face_id: number
  face_label: string
  origin: [number, number, number]
  rotation_x: number
  outline: number[][]
  holes: number[][][]
  covering: Covering
}

export interface Primitive {
  type: 'box' | 'cylinder' | 'sphere'
  offset: [number, number, number]
  size: [number, number, number]
  color_slot: string
  color: string | null
  operation: 'add' | 'subtract'
}

export interface FurnitureNode {
  kind: 'furniture'
  element_id: number
  face_label: string
  furniture_type_slug: string
  position: [number, number, number]
  rotation_y: number
  size_cm: [number, number, number]
  primitives: Primitive[]
  requires_csg: boolean
  variant_params: Record<string, unknown>
}

export type SceneNode = WallNode | HorizontalNode | FurnitureNode

export interface CameraPreset {
  name: string
  kind: 'orthographic' | 'perspective'
  position: [number, number, number]
  target: [number, number, number]
  up: [number, number, number]
  face_label: string | null
  half_width_cm?: number
  half_height_cm?: number
  fov_deg?: number
}

export interface SceneRoom {
  id: number
  name: string
  wall_thickness_cm: number
  ceiling_height_cm: number
  floor_area_cm2: number
  nodes: SceneNode[]
  cameras: CameraPreset[]
}

export interface SceneGraph {
  units: 'cm'
  project_id: number
  rooms: SceneRoom[]
}
