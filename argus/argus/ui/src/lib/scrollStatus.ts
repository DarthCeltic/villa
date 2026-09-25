
export type StepState = "DONE" | "AVAILABLE" | "HUMAN_GATED" | "BLOCKED" | "NOT_REACHED" | "UNKNOWN";
export type BlockCode =
  | "DATA_UNAVAILABLE" | "CODE_MISSING" | "LICENCE" | "EXPOSURE" | "RUNTIME" | "NOT_AUTHORIZED";
export type QuestionKey =
  | "eligibility" | "acquisition" | "local" | "geometry" | "detector" | "result" | "next" | "evidence";
export type QuestionTone = "ok" | "warn" | "bad" | "idle" | "unknown";

export interface StatusQuestion {
  answer: string | null;
  basis: string[];
  refusal: { code: string; why: string } | null;
  tone: QuestionTone;
  detail: string;
  to: string | null;
}

export interface StatusStep {
  n: number;
  id: string;
  label: string;
  screen: string;
  state: StepState;
  code: BlockCode | null;
  why: string;
  basis: string[];
  receipt: string | null;
  optional: boolean;
  evidence_role: string | null;
  to?: string | null;
  action_label?: string | null;
  waits_for?: string | null;
}

export interface NextAction {
  step: string | null;
  n: number | null;
  label: string;
  kind: "ACT" | "DECIDE" | "RESOLVE_BLOCKER" | "WAIT";
  to: string;
  why: string;
  operator_only: boolean;
  action_ids: string[];
  basis: string[];
}

export interface StatusBlocker {
  step: string;
  code: string;
  text: string;
  at_next_step?: boolean;
  basis: string[];
}

export interface ScrollStatus {
  schema: string;
  status: "OK" | "REFUSED";
  scroll: string | null;
  requested: string;
  display: string;
  refusal: { code: string; why: string } | null;
  questions: Record<QuestionKey, StatusQuestion>;
  capability_lanes?: CapabilityLane[];
  progress_summary?: ProgressSummary;
  work_queue?: WorkQueueItem[];
  steps: StatusStep[];
  step_counts?: Record<StepState, number>;
  next_action: NextAction;
  blocker: StatusBlocker | null;
  contradictions: string[];
  science_artifacts?: {
    state: "NONE" | "UNAVAILABLE" | "IMPORT_INVALID" | "DISCOVERED_NOT_ATTACHED" | "VERIFIED_LOCAL_OUTPUTS" | "VERIFIED_MANIFEST_NOT_MOUNTED";
    physical_scroll: string;
    source: string | null;
    source_commit?: string | null;
    exact_volume: string | null;
    operation_class?: string | null;
    pieces: number;
    geometry_pass: number;
    geometry_uncertain: number;
    review_priority: number;
    viewable_renders: number;
    records?: number;
    renders?: number;
    claim_ceiling: string | null;
    detector_run: boolean;
    presented_as_ink: boolean;
    to: string;
    detail?: string;
  };
  lineage: { chain_state: string | null; counts: { attempted?: number } | null; path: string | null } | null;
  generated_utc: string;
  claim_ceiling: string;
}

export interface ProgressSummary {
  done: number;
  ready: number;
  decisions: number;
  blocked: number;
  later: number;
  optional_missing: number;
  total: number;
  furthest_complete: { n: number; id: string; label: string } | null;
  input_state?: "SEALED" | "PARTIAL_INDEXED" | "CATALOGUED_UNSEALED" | "NOT_LOCAL";
  evidence_attempted?: number;
  evidence_chain_state?: string | null;
  verified_outputs?: number;
  scope: string;
}

export interface WorkQueueItem {
  step: string;
  n: number;
  label: string;
  state: StepState;
  why: string;
  to: string;
  action_label: string;
  action_ids: string[];
  basis: string[];
}

export interface CapabilityLane {
  id: string;
  label: string;
  state: StepState;
  done: number;
  total: number;
  summary: string;
  next: string | null;
  to: string | null;
  basis: string[];
}

export interface JourneyStep {
  n: number;
  id: string;
  label: string;
  screen: string;
  optional: boolean;
  ui_only: boolean;
}

export interface Journey {
  schema: string;
  steps: JourneyStep[];
  vocabularies: Record<string, Record<string, string>>;
}

export interface ScrollStatusBatch {
  schema: string;
  journey: Journey;
  count: number;
  scrolls: ScrollStatus[];
  recommendations?: {
    best_next_work: ScrollRecommendation[];
    most_complete: ScrollRecommendation[];
    method: string;
  };
}

export interface ScrollRecommendation {
  scroll: string;
  display: string;
  done: number;
  total: number;
  verified_outputs: number;
  ready: number;
  decisions: number;
  blocked: number;
  next: {
    label: string;
    to: string;
    why: string;
    kind: string;
    operator_only: boolean;
    step: string;
  };
}

export const QUESTION_ORDER: QuestionKey[] = [
  "eligibility", "acquisition", "local", "geometry", "detector", "result", "next", "evidence",
];

export const QUESTION_LABELS: Record<QuestionKey, string> = {
  eligibility: "Prize eligibility",
  acquisition: "Acquisition",
  local: "Local data",
  geometry: "Geometry / surface",
  detector: "Detector",
  result: "Result class",
  next: "Smallest legal next action",
  evidence: "Evidence",
};

export const STATE_WORD: Record<StepState, string> = {
  DONE: "done",
  AVAILABLE: "can run",
  HUMAN_GATED: "needs a person",
  BLOCKED: "blocked",
  NOT_REACHED: "not reached",
  UNKNOWN: "unknown",
};

export const STATE_GLYPH: Record<StepState, string> = {
  DONE: "●",
  AVAILABLE: "◐",
  HUMAN_GATED: "◆",
  BLOCKED: "✕",
  NOT_REACHED: "○",
  UNKNOWN: "?",
};

export const STATE_TONE: Record<StepState, "ok" | "warn" | "bad" | "idle" | "unknown"> = {
  DONE: "ok",
  AVAILABLE: "idle",
  HUMAN_GATED: "warn",
  BLOCKED: "bad",
  NOT_REACHED: "idle",
  UNKNOWN: "unknown",
};

export function currentStep(s: ScrollStatus): StatusStep | null {
  const id = s.next_action?.step;
  return id ? (s.steps.find((x) => x.id === id) ?? null) : null;
}

export function stepWord(step: StatusStep): string {
  return step.state === "BLOCKED" && step.code
    ? `blocked: ${step.code.replace(/_/g, " ").toLowerCase()}`
    : STATE_WORD[step.state];
}

export function positionLine(s: ScrollStatus): string {
  if (s.status === "REFUSED") return "refused";
  const p = s.progress_summary;
  if (p) {
    const hasSeparateEvidence = !!(p.evidence_attempted || p.verified_outputs);
    const parts = [hasSeparateEvidence
      ? `canonical route ${p.done}/${p.total} receipts`
      : `route ${p.done}/${p.total} receipts`];
    if (p.ready) parts.push(`${p.ready} ready`);
    if (p.decisions) parts.push(`${p.decisions} ${p.decisions === 1 ? "decision" : "decisions"}`);
    if (p.verified_outputs) parts.push(`${p.verified_outputs} downstream output${p.verified_outputs === 1 ? "" : "s"} verified and viewable`);
    else if (p.evidence_attempted) parts.push(`${p.evidence_attempted} downstream attempt${p.evidence_attempted === 1 ? "" : "s"} recorded`);
    if (p.blocked) parts.push(`${p.blocked} blocked`);
    return parts.join(" · ");
  }
  const step = currentStep(s);
  if (!step) return s.steps.length ? "every step done" : "unknown";
  return `step ${step.n} of ${s.steps.length}: ${step.label.toLowerCase()}`;
}

export function blockerLine(s: ScrollStatus, _publicDemo: boolean): { text: string; full: string; code: string | null } {
  if (s.status === "REFUSED") {
    return { text: `refused: ${s.refusal?.code ?? "unknown"}`, full: s.refusal?.why ?? "", code: s.refusal?.code ?? null };
  }
  const b = s.blocker;
  if (!b) {
    return { text: "none on the route ahead", full: "No step ahead of this scroll is recorded as blocked.", code: null };
  }
  return {
    text: `${b.code.replace(/_/g, " ").toLowerCase()} at ${b.step.replace(/_/g, " ")}`,
    full: b.text,
    code: b.code,
  };
}

export function noStatusLine(scroll: string | null, loading: boolean): string {
  if (!scroll) return "No scroll is selected. Choose one; every status is a status of a particular scroll.";
  return loading ? "Reading this scroll's status." : "This scroll's status has not been read.";
}
