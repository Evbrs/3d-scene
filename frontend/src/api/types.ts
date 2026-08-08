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

export type LayingPattern = 'straight' | 'staggered' | 'chevron' | 'herringbone' | 'diagonal'

export interface Covering {
  color?: string | null
  material?: string | null
  unit_width_cm?: number | null
  unit_height_cm?: number | null
  pattern?: LayingPattern | null
}

/**
 * Élément du plan, ancré à une face **ou** à une pièce (spec §10, amendement A4).
 *
 * `face_id` est le discriminant : non nul, l'élément est adossé et ce sont `x_offset_cm` /
 * `y_offset_cm` qui font foi ; nul, `room_id` l'est forcément et le meuble est posé au sol en
 * `pos_x_cm` / `pos_y_cm` — le **centre** de son emprise, dans le repère de `Room.polygon`. Les
 * décalages valent alors 0 et ne veulent rien dire : ne pas les lire.
 */
export interface PlanElement {
  id: number
  face_id: number | null
  room_id: number | null
  kind: ElementKind
  x_offset_cm: number
  y_offset_cm: number
  pos_x_cm: number | null
  pos_y_cm: number | null
  width_cm: number
  height_cm: number
  depth_cm: number
  rotation_deg: number
  furniture_type_id: number | null
  colors: Record<string, string>
  variant_params: Record<string, string | number | boolean | null>
}

/** Un meuble posé au sol : `face_id` nul garantit que le placement est celui de la pièce. */
export function isFreeElement(
  element: PlanElement,
): element is PlanElement & { room_id: number; pos_x_cm: number; pos_y_cm: number } {
  return element.face_id === null && element.room_id !== null
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
  /**
   * Fond de plan calibré (spec §10, amendement A5).
   *
   * `background_scale_cm_per_px` à `null` veut dire « image posée, pas encore calibrée » — jamais
   * « échelle 1 ». Dessiner par-dessus une image non calibrée produit un logement faux : c'est
   * l'outil de calibrage à deux clics qui renseigne cette colonne, rien d'autre.
   */
  background_url: string | null
  background_scale_cm_per_px: number | null
  background_offset_x_cm: number
  background_offset_y_cm: number
  background_rotation_deg: number
  background_opacity: number
  faces: Face[]
  /** Mobilier posé au sol. Liste à part : le fondre dans les faces compterait tout deux fois. */
  free_elements: PlanElement[]
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
  // Axe de révolution : `size` donne la boîte englobante, pas l'orientation. Sans lui, une
  // poignée de porte ou une barre d'appui reste dressée à la verticale.
  axis: 'x' | 'y' | 'z'
}

export interface FurnitureNode {
  kind: 'furniture'
  element_id: number
  /** Nul pour un meuble libre : aucun groupe de face ne doit le masquer (spec §10, A4). */
  face_label: string | null
  furniture_type_slug: string
  position: [number, number, number]
  rotation_y: number
  size_cm: [number, number, number]
  primitives: Primitive[]
  requires_csg: boolean
  variant_params: Record<string, unknown>
}

/** La menuiserie logée dans le percement d'une ouverture : le trou est un vide, la porte non. */
export interface JoineryNode {
  kind: 'joinery'
  element_id: number
  face_label: string
  opening_kind: ElementKind
  furniture_type_slug: string
  position: [number, number, number]
  rotation_y: number
  size_cm: [number, number, number]
  primitives: Primitive[]
  requires_csg: boolean
}

export type SceneNode = WallNode | HorizontalNode | FurnitureNode | JoineryNode

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
  // Aire au sol dans l'œuvre, murs déduits — celle qu'on annonce dans un devis. `floor_area_cm2`
  // reste l'aire brute mesurée sur l'axe des murs.
  net_floor_area_cm2: number
  nodes: SceneNode[]
  cameras: CameraPreset[]
}

export interface SceneGraph {
  units: 'cm'
  project_id: number
  rooms: SceneRoom[]
}

// --- Intelligence du plan (`docs/strategie-produit.md` §3.8) -------------------------------------

/** Les trois sévérités du moteur de règles (`app/intelligence/rules.py::Severity`). */
export type Severity = 'bloquant' | 'avertissement' | 'conseil'

/**
 * Une anomalie de conformité, telle que le serveur la publie.
 *
 * `focus` est un point du **plan** en centimètres, dans le repère de `Room.polygon` : c'est ce qui
 * permet de recentrer l'éditeur 2D sans conversion. Nul quand l'anomalie ne désigne aucun endroit
 * précis — une pièce sans ouverture n'a pas de coordonnée.
 */
export interface Anomaly {
  rule_id: string
  title: string
  severity: Severity
  message: string
  room_id: number | null
  room_name: string | null
  face_labels: string[]
  element_ids: number[]
  focus: [number, number] | null
  measured_cm: number | null
  threshold_cm: number | null
}

export interface RoomInspection {
  room_id: number | null
  name: string | null
  counts: Record<string, number>
}

/**
 * Le rapport complet.
 *
 * `thresholds` republie les seuils appliqués : « passage insuffisant » sans son barème n'est pas
 * vérifiable, et le mode accessible change la réponse.
 */
export interface InspectionReport {
  project_id: number | null
  thresholds: Record<string, number | boolean>
  rooms: RoomInspection[]
  anomalies: Anomaly[]
  counts: Record<string, number>
  warnings: string[]
}

/**
 * Calepinage optimisé. Le détail par face reste libre côté serveur — sa forme dépend du
 * revêtement, une face peinte n'en a pas — donc ici aussi.
 */
export interface LayingPlan {
  project_id: number | null
  rooms: Record<string, unknown>[]
  cuts_saved: number
}

/**
 * Un meuble proposé, prêt à être créé tel quel : `pos_x_cm` / `pos_y_cm` sont le **centre** de
 * l'emprise dans le repère du plan, la convention du mobilier libre (spec §10, amendement A4).
 */
export interface LayoutItem {
  slug: string
  width_cm: number
  depth_cm: number
  height_cm: number
  pos_x_cm: number
  pos_y_cm: number
  rotation_deg: number
  against_face_label: string | null
  clearance_cm: number
}

export interface LayoutProposal {
  rank: number
  score: number
  breakdown: Record<string, number>
  items: LayoutItem[]
}

export interface LayoutProposals {
  room_id: number | null
  program: string
  weights: Record<string, number>
  proposals: LayoutProposal[]
  warnings: string[]
}
