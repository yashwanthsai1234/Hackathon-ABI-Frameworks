// Mirrors SPEC §C.3 export.json contract. Absent fields are null, never omitted.

export type Route = "auto_accept" | "flag_for_review" | "reject";
export type CheckStatus = "pass" | "warn" | "fail";

export interface Payer {
  code: string; // MCB | MCA | MCD | HMO
  name: string;
  type: string;
}

export interface Wound {
  type: string;
  stage: string | null;
  location: string;
  L: number | null;
  W: number | null;
  D: number | null;
  drainage: string | null;
  format: string;
}

export interface Highlight {
  field: string;
  value: string;
  start: number; // char offset into note_text
  end: number;
}

export interface EligibilityCheck {
  label: string;
  code: string | null;
  status: CheckStatus;
  detail: string;
}

export type EvidenceNodeType = "wound" | "note" | "dx" | "assessment";
export interface EvidenceNode {
  id: string;
  type: EvidenceNodeType;
  label: string;
}
export interface EvidenceEdge {
  id: string;
  source: string;
  target: string;
  relation: "agree" | "conflict";
  field: string | null;
}
export interface EvidenceGraph {
  nodes: EvidenceNode[];
  edges: EvidenceEdge[];
  agreeing_sources: number;
}

export interface Patient {
  patient_id: string;
  id: number;
  name: string;
  payer: Payer;
  wound: Wound;
  route: Route;
  confidence: number;
  field_confidence: Record<string, number>;
  reason: string;
  note_text: string;
  highlights: Highlight[];
  eligibility_checks: EligibilityCheck[];
  evidence_graph: EvidenceGraph;
}

export interface Stage {
  id: string;
  label: string;
  in: number;
  out: number;
  retried: number;
  note: string;
}

export interface RunManifest {
  run_id: string;
  generated_at: string;
  total_patients: number;
  duration_s: number;
  rate_limit_hits: number;
  stages: Stage[];
  routes: Record<Route, number>;
}

export interface SankeyEdge {
  source: string;
  target: string;
  value: number;
}

export interface Funnel {
  total: number;
  mcb_active: number;
  active_wound: number;
  has_measurements: number;
  auto_accept: number;
  flag_for_review: number;
  reject: number;
  sankey: SankeyEdge[];
}

export interface ExportData {
  generated_at: string;
  run_id: string;
  manifest: RunManifest;
  funnel: Funnel;
  patients: Patient[];
}
