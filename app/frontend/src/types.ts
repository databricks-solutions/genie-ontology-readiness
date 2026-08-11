// Shared types matching the backend API contract (base path /api).

export interface AIModel {
  id: string;
  label: string;
  provider: string;
}

export interface ConfigPillar {
  key: string;
  name: string;
  short: string;
  weight: number;
  capability: string;
}

export interface AppConfig {
  app_name: string;
  brand_name: string;
  workspace_id: string;
  level_labels: string[];
  pillars: ConfigPillar[];
  ai_models: AIModel[];
  default_model: string;
  lakebase_enabled: boolean;
  genie_space_configured: boolean;
  assess_catalogs: string[];
}

export interface Signal {
  label: string;
  value: number | string;
  unit?: string;
  detail: string;
}

export interface UcSchemaCount {
  schema: string;
  tables: number;
}
export interface TopAccessedTable {
  name: string;
  accesses: number;
  certified: boolean;
}
export interface ContentQuery {
  title: string;
  sql: string;
}

export interface PillarScore {
  key: string;
  name: string;
  short: string;
  capability: string;
  weight: number;
  score: number;
  technical_score: number | null;
  level: number;
  level_label: string;
  available: boolean;
  note: string | null;
  signals: Signal[];
  gaps: string[];
  best_practices: string[];
  summary: string;
  metrics: Record<string, unknown>;
}

export interface ScorecardOverall {
  score: number;
  level: number;
  level_label: string;
  readiness_stage: string;
  readiness_detail: string;
  assessed_at?: string;
}

export interface TopGap {
  pillar: string;
  gap: string;
}

export interface Scorecard {
  overall: ScorecardOverall;
  pillars: PillarScore[];
  top_gaps: TopGap[];
}

export interface HistorySnapshot {
  id: string | number;
  created_at: string;
  created_by: string;
  overall_score: number;
  overall_level: number;
}

export interface HistoryResponse {
  snapshots: HistorySnapshot[];
}

export interface SnapshotResponse {
  id: string | number;
  created_at: string;
  created_by: string;
  scorecard: Scorecard;
}

export interface PlanListItem {
  id: number;
  created_at: string;
  created_by: string;
  snapshot_id: number | null;
  title: string;
  model: string;
}

export interface PlansResponse {
  plans: PlanListItem[];
}

export interface PlanDetail extends PlanListItem {
  plan_markdown: string;
}

export interface PlanSaveResponse {
  id: number | null;
  saved: boolean;
}

export interface DocSource {
  title: string;
  url: string;
}

export type AcceleratorType = 'notebook' | 'sql' | 'dab' | 'dashboard' | 'repo' | 'guide';

export interface Accelerator {
  key: string;
  title: string;
  summary: string;
  capability: string;
  type: AcceleratorType;
  effort: string;
  what_it_does: string;
  prerequisites: string[];
  improves_signals: string[];
  target_level: number;
  review_mode: boolean;
  steps: string[];
  artifact_dir?: string;
  artifact_file?: string;
  source?: DocSource;
  valid_as_of?: string;
  superseded_by?: string;
}

export interface Capability {
  key: string;
  name: string;
  tagline: string;
  what: string;
  technical_value: string;
  business_value: string;
  technical_enablement: string[];
  business_adoption: string[];
  best_practices: string[];
  sources: DocSource[];
  queries?: ContentQuery[];
  accelerators?: Accelerator[];
}

export interface ContentResponse {
  capabilities: Capability[];
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface GenieSpaceCuration {
  title: string;
  instructions: number;
  sample_questions: number;
  example_sqls: number;
  functions: number;
  benchmarks: number;
  tables: number;
}

export interface GenieSpace {
  id: string;
  title: string;
}

export interface GenieSpacesResponse {
  spaces: GenieSpace[];
  note?: string;
}

export interface GenieResult {
  result: {
    text: string;
    sql: string;
    columns: string[];
    // Backend builds each row as an object keyed by column name (see genie_client.py).
    rows: Record<string, unknown>[];
  };
}
