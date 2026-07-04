export interface Panel {
  panel_id: string;
  substrate_type: string;
  lot_id: string;
  rows: number;
  cols: number;
  component_pitch_mm: number;
  product_type: string;
  defect_count: number;
}

export interface YieldEstimate {
  panel_id: string;
  substrate_type: string;
  die_area_mm2: number;
  defect_count: number;
  defect_density: number;
  clustering_alpha: number;
  yield_poisson: number;
  yield_murphy: number;
  yield_negbinom: number;
  critical_area_fraction: number | null;
}

export interface DieYield {
  row: number;
  col: number;
  defect_count: number;
  d0: number;
  yield_poisson: number;
  yield_murphy: number;
  yield_negbinom: number;
  active: boolean;
}

export interface SpatialYield {
  panel_id: string;
  substrate_type: string;
  spatial_yield_poisson: number;
  spatial_yield_murphy: number;
  spatial_yield_negbinom: number;
  global_yield_poisson: number;
  global_yield_murphy: number;
  global_yield_negbinom: number;
  mean_d0: number;
  std_d0: number;
  cv_d0: number;
  yield_gain_poisson: number;
  yield_gain_negbinom: number;
  n_active_dies: number;
  die_area_mm2: number;
  critical_area_fraction: number | null;
  die_yields: DieYield[];
}

export interface CriticalArea {
  panel_id: string;
  ca_fraction: number;
  layout_density: number;
  min_feature_mm: number;
  effective_area_mm2: number;
  full_die_area_mm2: number;
  n_defects: number;
  mean_defect_size_mm: number;
  method: string;
}

export interface LotNodeItem {
  lot_id: string;
  substrate_type: string;
  process_step: string;
  lot_size: number;
  created_at: string;
}

export interface LineageEdge {
  parent_lot_id: string;
  child_lot_id: string;
  relation_type: string;
  timestamp: string;
  notes: string;
}

export interface LotLineage {
  lot_id: string;
  ancestors: LotNodeItem[];
  descendants: LotNodeItem[];
  edges: LineageEdge[];
  depth: number;
}

export interface DefectDistribution {
  panel_id: string;
  defect_counts: Record<string, number>;
  total_defects: number;
  top_class: string;
  top_class_fraction: number;
  source_system: string;
}

export interface CNNStatus {
  model_available: boolean;
  trained_at: string | null;
  val_accuracy: number | null;
  n_classes: number | null;
  notes: string | null;
}

export interface IngestResult {
  wafers_ingested: number;
  defects_inserted: number;
  lot_id: string;
  panel_ids: string[];
}

export type YieldModel = "poisson" | "murphy" | "negbinom";

export interface ProductSpec {
  spec_id: string;
  product_name: string;
  substrate_type: string;
  die_width_mm: number;
  die_height_mm: number;
  die_area_mm2: number;
  wafer_diameter_mm: number;
  critical_area_fraction: number;
  target_yield: number;
  alpha: number;
  d0_target: number | null;
  process_node_nm: number | null;
  notes: string;
  created_at: string;
  updated_at: string;
}

export interface HistogramBin {
  bin_low: number;
  bin_high: number;
  count: number;
}

export interface MonteCarloResult {
  d0: number;
  die_area_mm2: number;
  critical_area_fraction: number;
  n_runs: number;
  n_dies_per_wafer: number;
  mean_yield: number;
  std_yield: number;
  p10_yield: number;
  p50_yield: number;
  p90_yield: number;
  min_yield: number;
  max_yield: number;
  poisson_yield: number;
  murphy_yield: number;
  negbinom_yield: number;
  yield_d0_minus20: number;
  yield_d0_plus20: number;
  histogram: HistogramBin[];
}

export interface LearningCurvePoint {
  month: number;
  yield_fraction: number;
  d0: number | null;
}

export interface LearningCurveResult {
  model: string;
  current_yield: number;
  target_yield: number;
  y_max: number;
  months_to_target: number | null;
  improvement_rate: number;
  die_area_mm2: number | null;
  initial_d0: number | null;
  final_d0: number | null;
  projected: LearningCurvePoint[];
}

// ── Pareto ───────────────────────────────────────────────────────────────────

export interface ParetoItem {
  defect_type: string;
  count: number;
  avg_size_mm: number;
  avg_confidence: number;
  impact_score: number;
  impact_fraction: number;
  cumulative_fraction: number;
  yield_loss_estimate: number;
  rank: number;
}

export interface ParetoResult {
  panel_id: string | null;
  substrate_type: string | null;
  source_system: string;
  calculated_at: string;
  total_defects: number;
  items: ParetoItem[];
  vital_few: string[];
  trivial_many: string[];
}

// ── SPC ──────────────────────────────────────────────────────────────────────

export interface SpcPoint {
  panel_id: string;
  sequence: number;
  value: number;
  moving_range: number;
  ewma: number;
  cusum_pos: number;
  cusum_neg: number;
  ucl_shewhart: number;
  lcl_shewhart: number;
  ucl_ewma: number;
  lcl_ewma: number;
  ucl_cusum: number;
  ucl_imr: number;
  shewhart_signal: boolean;
  ewma_signal: boolean;
  cusum_signal: boolean;
  imr_signal: boolean;
  we_rules: string[];
}

export interface SpcCapability {
  cp: number | null;
  cpk: number | null;
  usl: number | null;
  lsl: number | null;
  interpretation: string;
}

export interface SpcResult {
  lot_id: string | null;
  substrate_type: string | null;
  calculated_at: string;
  n_points: number;
  centerline: number;
  sigma: number;
  lambda_ewma: number;
  L_ewma: number;
  cusum_k: number;
  cusum_h: number;
  points: SpcPoint[];
  alarms: {
    panel_id: string; sequence: number; chart_type: string;
    rule_fired: string; value: number; control_limit: number; severity: string;
  }[];
  shewhart_signals: string[];
  ewma_signals: string[];
  cusum_signals: string[];
  imr_signals: string[];
  process_state: string;
  capability: SpcCapability;
  db_id: number | null;
}

// ── Lot Trend ────────────────────────────────────────────────────────────────

export interface TrendPoint {
  lot_id: string;
  sequence: number;
  created_at: string;
  substrate_type: string;
  avg_defect_density: number;
  avg_yield_negbinom: number | null;
  excursion_count: number;
  lot_status: string;
}

export interface TrendResult {
  substrate_type: string;
  n_lots: number;
  data_points: TrendPoint[];
  slope: number;
  intercept: number;
  r_squared: number;
  direction: string;
  mean_density: number;
  mean_yield: number | null;
  first_lot_id: string | null;
  last_lot_id: string | null;
}

// ── Defects ──────────────────────────────────────────────────────────────────

export interface Defect {
  defect_id: number;
  panel_id: string;
  component_row: number;
  component_col: number;
  source_system: string;
  defect_type: string;
  x: number;
  y: number;
  size: number;
  confidence_score: number;
  match_id: string | null;
  created_at: string;
}

export interface DefectListResult {
  total: number;
  page: number;
  limit: number;
  results: Defect[];
}

// ── Generate ─────────────────────────────────────────────────────────────────

export interface GeneratedPanelSummary {
  panel_id: string;
  lot_id: string;
  rows: number;
  cols: number;
  active_dies: number;
  total_defects: number;
  system_a_count: number;
  system_b_count: number;
  defect_density: number | null;
  yield_poisson: number | null;
  yield_negbinom: number | null;
  clustering_class: string | null;
}

export interface GenerateResult {
  substrate_type: string;
  n_panels: number;
  total_defects: number;
  mean_defect_count: number;
  panels: GeneratedPanelSummary[];
  elapsed_ms: number;
}

// ── Lot Summary ───────────────────────────────────────────────────────────────

export interface PanelLotStats {
  panel_id: string;
  defect_density: number;
  yield_negbinom: number | null;
  cluster_class: string | null;
}

export interface LotSummary {
  lot_id: string;
  substrate_type: string;
  panel_count: number;
  panels: PanelLotStats[];
  avg_defect_density: number;
  std_defect_density: number;
  avg_yield_negbinom: number | null;
  std_yield_negbinom: number | null;
  excursion_count: number;
  lot_status: string;
  status_reason: string;
}

// ── Correlation ───────────────────────────────────────────────────────────────

export interface RepeatLocation {
  component_row: number;
  component_col: number;
  region_id: string;
  repeat_count: number;
  repeat_rate: number;
  dominant_type: string;
  type_consistency: number;
  panel_ids: string[];
}

export interface CorrelationResult {
  lot_id: string | null;
  substrate_type: string | null;
  total_panels: number;
  total_locations: number;
  repeat_threshold: number;
  systematic_locations: RepeatLocation[];
  systematic_count: number;
  systematic_rate: number;
  calculated_at: string;
  classification: string;
  classification_reason: string;
}

// ── Signatures ────────────────────────────────────────────────────────────────

export interface SignatureMatch {
  signature_name: string;
  confidence: number;
  description: string;
  root_cause: string;
  recommended_action: string;
  evidence: string;
}

export interface SignatureResult {
  panel_id: string;
  substrate_type: string;
  calculated_at: string;
  defect_count: number;
  zone_fractions: Record<string, number>;
  matches: SignatureMatch[];
  top_match: SignatureMatch | null;
}
