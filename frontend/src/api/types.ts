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
  /**
   * Taux de chute imposé pour cette face, en **points de base** : 800 = 8 % (spec §10,
   * amendement A14). Absent, c'est la provision du motif de pose qui s'applique côté serveur.
   *
   * Il est ici parce que le client doit pouvoir le renvoyer intact : la fusion du revêtement
   * réécrit le dictionnaire entier, et un champ que le type ignore serait effacé au premier
   * changement de couleur — c'est le défaut que la vague 1 avait corrigé sur `material` et
   * `pattern`.
   */
  waste_ratio_bp?: number | null
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

// --- Métré (`docs/strategie-produit.md` §3.1) -----------------------------------------------------

/**
 * Ce que le métré ne sait pas établir sort à `null`, **jamais à zéro**.
 *
 * La distinction est le cœur du contrat de `app/geometry/quantities.py::build_takeoff` : un zéro
 * s'additionne sans bruit et donne un devis trop bas, une inconnue se voit. Les écrans doivent
 * donc afficher un tiret pour `null`, et jamais « 0 ».
 */
export type Mesure = number | null

/**
 * Calepinage d'une face : ce qu'on commande, ce qu'on pose entier, ce qu'on coupe.
 *
 * `pattern` n'est pas typé `LayingPattern` : le métré accepte un motif qu'il ne connaît pas, le
 * provisionne comme une pose droite et le signale dans `warnings`. Le restreindre ici ferait
 * mentir le type sur ce que le serveur envoie réellement.
 *
 * `full_units` et `cut_units` sont nuls dès que la pose n'est pas alignée sur les bords de la face
 * (diagonale, chevron, bâton rompu) : il n'y a alors ni colonnes ni rangs à compter.
 */
export interface Tiling {
  pattern: string
  unit_width_cm: number
  unit_height_cm: number
  unit_area_m2: number
  waste_ratio: number
  ordered_area_m2: number
  units_total: number
  full_units: Mesure
  cut_units: Mesure
}

export interface TakeoffFace {
  face_id: number
  face_label: string
  kind: FaceKind
  /** Nuls sur un sol et un plafond : une dalle n'a ni longueur ni hauteur de mur. */
  length_m: Mesure
  height_m: Mesure
  gross_area_m2: Mesure
  openings_area_m2: Mesure
  net_area_m2: Mesure
  opening_count: number
  door_count: number
  window_count: number
  skirting_deduction_ml: Mesure
  material: string | null
  tiling: Tiling | null
}

/** Les calepinages regroupés par référence : la forme d'une commande de matériaux. */
export interface TakeoffCovering {
  material: string | null
  pattern: string
  unit_width_cm: number
  unit_height_cm: number
  waste_ratio: number
  net_area_m2: number
  ordered_area_m2: number
  units_total: number
  full_units: Mesure
  cut_units: Mesure
}

/** Mobilier compté **à l'unité** et sans aucun montant (spec §10, amendement A7). */
export interface TakeoffFurniture {
  furniture_type_slug: string
  width_cm: number
  height_cm: number
  depth_cm: number
  footprint_m2: number
  count: number
  free_count: number
  on_face_count: number
}

export interface TakeoffRoom {
  room_id: number | null
  name: string | null
  ceiling_height_m: Mesure
  wall_thickness_m: Mesure
  perimeter_ml: Mesure
  net_perimeter_ml: Mesure
  skirting_ml: Mesure
  cornice_ml: Mesure
  floor_area_m2: Mesure
  ceiling_area_m2: Mesure
  volume_m3: Mesure
  wall_gross_area_m2: Mesure
  wall_openings_area_m2: Mesure
  wall_net_area_m2: Mesure
  opening_count: number
  door_count: number
  window_count: number
  faces: TakeoffFace[]
  coverings: TakeoffCovering[]
  /** Clé **absente** quand la pièce n'en porte aucun : son absence vaut zéro, pas « inconnu ». */
  furniture?: TakeoffFurniture[]
  warnings: string[]
}

export interface TakeoffTotals {
  room_count: number
  floor_area_m2: Mesure
  ceiling_area_m2: Mesure
  wall_gross_area_m2: Mesure
  wall_openings_area_m2: Mesure
  wall_net_area_m2: Mesure
  volume_m3: Mesure
  perimeter_ml: Mesure
  skirting_ml: Mesure
  cornice_ml: Mesure
  opening_count: number
  door_count: number
  window_count: number
  coverings: TakeoffCovering[]
  furniture?: TakeoffFurniture[]
}

export interface Takeoff {
  units: Record<string, string>
  project_id: number | null
  rooms: TakeoffRoom[]
  totals: TakeoffTotals
  /** Non décoratif : les totaux sont **partiels** dès que cette liste n'est pas vide. */
  warnings: string[]
}

// --- Barème, devis et facture (`docs/strategie-produit.md` §2 et §3.1) ----------------------------

/** Les quatre unités du barème : deux que le métré produit, deux que l'artisan saisit. */
export type PriceUnit = 'm2' | 'ml' | 'u' | 'forfait'

/** Nature d'une face, telle que `default_price_codes` la nomme côté serveur. */
export type CostedFaceKind = 'wall' | 'floor' | 'ceiling'

export interface PriceBook {
  id: number
  organization_id: number
  name: string
  is_default: boolean
}

/**
 * Une ligne de barème. Les montants sont des **centimes entiers**, les taux des points de base
 * (1000 = 10 %). Ni les uns ni les autres ne doivent passer par un flottant pour être calculés.
 */
export interface PriceItem {
  id: number
  price_book_id: number
  code: string
  label: string
  unit: PriceUnit
  unit_price_cents: number
  vat_rate_bp: number
}

/**
 * Décision explicite de l'artisan sur une face : elle prime sur tout le reste du chiffrage.
 *
 * Les trois champs sont indépendants — imposer le seul code, la seule quantité (une reprise
 * partielle relevée sur place) ou le seul prix (un tarif négocié).
 */
export interface FaceCosting {
  id: number
  face_id: number
  price_item_code: string | null
  /** Décimale transportée en **chaîne** : la reprendre en flottant perdrait des millièmes. */
  override_quantity: string | null
  override_unit_price_cents: number | null
}

/**
 * Cycle de vie d'un devis. L'ordre compte : `draft` est le seul état où le document se modifie
 * encore. Dès `sent`, il porte un numéro, il est parti chez le client, et il est figé.
 */
export type QuoteStatus = 'draft' | 'sent' | 'accepted' | 'refused' | 'invoiced'

export interface QuoteLine {
  id: number
  position: number
  label: string
  unit: PriceUnit
  /** Décimale en chaîne, pour la même raison que `FaceCosting.override_quantity`. */
  quantity: string
  unit_price_cents: number
  vat_rate_bp: number
  total_ht_cents: number
  /** La face d'où vient la ligne, quand elle vient du métré. */
  source_face_id?: number | null
  source_price_item_code?: string | null
}

/** La TVA est calculée par taux sur la somme des bases, jamais ligne à ligne. */
export interface VatBucket {
  rate_bp: number
  base_cents: number
  tax_cents: number
}

export interface QuoteSummary {
  id: number
  status: QuoteStatus
  number?: string | null
  invoice_number?: string | null
  client_name: string
  total_ht_cents: number
  total_ttc_cents: number
  issued_at?: string | null
  valid_until?: string | null
}

export interface Quote extends QuoteSummary {
  organization_id: number
  project_id?: number | null
  project_name?: string | null
  total_tva_cents: number
  lines: QuoteLine[]
  vat_breakdown: VatBucket[]
  /** Faces que le chiffrage n'a pas su rattacher : le devis a l'air complet et ne l'est pas. */
  warnings: string[]
  invoiced_at?: string | null
  due_date?: string | null
  client_is_consumer: boolean
  client_email?: string | null
  client_phone?: string | null
  client_address_line1?: string | null
  client_address_line2?: string | null
  client_postal_code?: string | null
  client_city?: string | null
  client_country?: string | null
  client_vat_number?: string | null
  site_address_line1?: string | null
  site_address_line2?: string | null
  site_postal_code?: string | null
  site_city?: string | null
  payment_terms?: string | null
  late_penalty_rate_bp: number
  recovery_indemnity_cents: number
  mediator_name?: string | null
  mediator_url?: string | null
  notes?: string | null
  vat_attestation_required: boolean
  vat_attestation_over_two_years?: boolean | null
  vat_attestation_premises_use?: string | null
  vat_attestation_signatory?: string | null
  vat_attestation_signed_at?: string | null
}

// --- Multi-locataire : entreprise, membres, invitations -------------------------------------------

/** Quatre rôles, chacun explicable en une phrase à un artisan. */
export type OrganizationRole = 'owner' | 'admin' | 'editor' | 'viewer'

export interface Member {
  user_id: number
  email: string
  role: OrganizationRole
  invited_at?: string | null
  accepted_at?: string | null
}

export interface Invitation {
  id: number
  organization_id: number
  email: string
  role: OrganizationRole
  expires_at: string
  accepted_at?: string | null
}

/**
 * Réponse de création d'invitation : **seul endroit** où le jeton en clair existe.
 *
 * La base n'en garde que le hachage. Une invitation perdue se réémet, elle ne se retrouve pas —
 * l'écran doit donc le dire au moment où il l'affiche.
 */
export interface InvitationCreated extends Invitation {
  token: string
}
