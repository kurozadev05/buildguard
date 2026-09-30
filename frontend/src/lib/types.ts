export type Role = "admin" | "site_engineer" | "qa" | "lab" | "client" | "auditor";
export type Status = "VERIFIED" | "REVIEW_REQUIRED" | "FLAGGED" | "PENDING";
export type Lang = "en" | "hi";
export interface User { id: string; email: string; full_name: string; role: Role; language: Lang; is_active: boolean; created_at: string }
export interface Project { id: string; name: string; location: string | null; client_name: string | null; created_at: string }
export interface Outcome { rule_id: string; title: string; reference: string; requirement: string; observed: unknown; status: Status; explanation: string }
export interface Batch {
  id: string; project_id: string; batch_code: string; public_token: string; grade: string; fck: number; exposure: string; construction_type: string;
  supplier: string | null; supplier_ref: string | null; delivery_date: string | null; quantity_m3: number; cement_type: string; cement_content_kg_m3: number | null;
  wc_ratio: number | null; admixture: string | null; mix_notes: string | null; registration_check: { status: Status; outcomes: Outcome[] };
  status: Status; status_label: string; required_samples: number; created_at: string; samples?: Sample[]; tests?: TestRec[];
}
export interface Sample { id: string; batch_id: string; sample_code: string; cast_date: string; cubes_cast: number; curing_method: string | null; notes: string | null }
export interface TestRec {
  id: string; project_id: string; batch_id: string; sample_id: string | null; test_type: string; age_days: number | null; values: Record<string, unknown>;
  lab_name: string | null; notes: string | null; tested_at: string; status: Status; status_label?: string; validation: Outcome[]; review: null | { decision: string; comment: string; by?: string; at?: string };
  version: number; is_current: boolean; record_hash: string; created_at: string; batch_code?: string;
}
export interface Alert { id: string; project_id: string; kind: string; severity: string; message: string; batch_id: string | null; element_id: string | null; test_id: string | null; created_at: string; acknowledged_by: string | null; acknowledged_at: string | null }
export interface Action { id: string; stage: number; action_type: string; element_id: string | null; reference: string; description: string; status: "pending" | "done" | "waived"; result_test_id: string | null; note: string | null; completed_at: string | null; path: string | null }
export interface Investigation { id: string; project_id: string; batch_id: string; test_id: string | null; reason: string; status: string; closure_note: string | null; opened_at: string; closed_at: string | null; batch_code: string; actions: Action[]; progress: { done: number; total: number } }
export interface Dashboard {
  totals: { batches: number; samples: number; tests: number; documents: number; open_alerts: number; open_investigations: number };
  batch_status: Partial<Record<Status, number>>; test_status: Partial<Record<Status, number>>;
  pending_tests: { sample_code: string; batch_code: string; age_days: number; due_date: string }[]; overdue_tests: { sample_code: string; batch_code: string; age_days: number; due_date: string }[];
  sampling_shortfall: { batch_code: string; required: number; taken: number; quantity_m3: number }[];
  tests_missing_evidence: { test_id: string; batch_id: string; test_type: string; age_days: number | null }[];
  recent_alerts: { id: string; kind: string; severity: string; message: string; created_at: string; acknowledged: boolean }[];
  durability_risk: { counts: Record<string, number>; elements: { element_id: string; score: number; level: string; trend: string }[] };
  supplier_scorecards: Supplier[];
}
export interface Supplier { supplier: string; batches: number; flagged: number; review_required: number; flag_rate: number; avg_28d_margin_over_fck_mpa: number | null; reliability_score: number | null; note?: string }
export interface RiskRow { element_id: string; path: string; score: number; level: string; trend: string; top_factors: { factor: string; points: number; detail: string; reference: string }[]; recommendations: string[] }
export interface TreeElement { id: string; name: string; element_type: string; exposure: string | null }
export interface Tree { project_id: string; buildings: { id: string; name: string; floors: { id: string; name: string; level: number; elements: TreeElement[] }[] }[] }
export interface Doc { id: string; project_id: string; batch_id: string | null; sample_id: string | null; test_id: string | null; kind: string; filename: string; content_type: string; size_bytes: number; sha256: string; uploaded_at: string; latitude?: number | null; longitude?: number | null }
export interface Prediction { batch_code: string; batch_id: string; sample_code: string | null; grade: string; mean_7d_mpa: number; predicted_28d_mpa: { estimate: number; low: number; high: number }; required_group_mean_mpa: number; status: "ON_TRACK" | "WATCH" | "AT_RISK"; basis: { source: string; pairs: number; ratio: number }; note: string }
export interface Finding { check: string; severity: "high" | "medium" | "low"; title: string; detail: string; tests: { test_id: string; batch_code: string; age_days: number | null; lab: string | null }[] }
export interface AiSource { label: string; title: string; reference: string | null; source: string }
export interface ChatDone { message_id: string; conversation_id: string; generated_by: "llm" | "retrieval"; validated: boolean; degraded: boolean; sources: AiSource[]; tools: string[]; usage: { input_tokens: number; output_tokens: number } }
export interface AiStatus { enabled: boolean; mode: string; features: { chat: boolean; streaming: boolean; tools: string[]; semantic_search: boolean; vision: boolean; project_documents: boolean }; limits: { requests_per_minute: number; daily_token_budget: number; max_message_chars: number; max_document_kb: number }; admin?: { provider: string | null; model: string | null; kv_backend: string; redis_ok: boolean } }
export interface Conversation { id: string; title: string; project_id: string | null; message_count: number; updated_at: string }
export interface ConvMessage { id: string; role: "user" | "assistant"; content: string; meta: { sources?: AiSource[]; tools?: string[]; validated?: boolean; degraded?: boolean; generated_by?: string; interrupted?: string }; created_at: string }
