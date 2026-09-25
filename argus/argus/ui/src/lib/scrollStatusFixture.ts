import type { ScrollStatus, StatusQuestion, StatusStep, QuestionKey } from "./scrollStatus";
import { QUESTION_ORDER } from "./scrollStatus";

const STEP_IDS = [
  "select_scroll", "exact_identity", "acquisition", "ct_inspection", "surface_prediction", "tracing",
  "topology", "normal_orientation", "flatten_render", "fibers", "ink_inference", "evidence_comparison",
  "candidate_review", "transcription", "translation", "export_packet",
];

function question(answer: string | null, extra: Partial<StatusQuestion> = {}): StatusQuestion {
  return {
    answer, basis: ["argus.core.scroll_shelf"], refusal: answer === null ? { code: "DATA_UNAVAILABLE", why: "declared nowhere" } : null,
    tone: answer === null ? "unknown" : "idle", detail: `detail of ${answer ?? "a refusal"}`, to: null, ...extra,
  };
}

export function fixtureStatus(over: Partial<ScrollStatus> = {}): ScrollStatus {
  const steps: StatusStep[] = STEP_IDS.map((id, i) => ({
    n: i + 1, id, label: `Step label ${i + 1}`, screen: "/workbench",
    state: i < 5 ? "DONE" : i === 5 ? "AVAILABLE" : i === 9 ? "BLOCKED" : "NOT_REACHED",
    code: i === 9 ? "DATA_UNAVAILABLE" : null,
    why: `why ${id}`, basis: [`argus.core.basis_${id}`], receipt: i === 4 ? "artifacts/receipt_surface.json" : null,
    optional: id === "ct_inspection", evidence_role: null,
  }));
  const questions = Object.fromEntries(
    QUESTION_ORDER.map((k) => [k, question(k === "acquisition" ? null : `answer ${k}`)]),
  ) as Record<QuestionKey, StatusQuestion>;
  questions.next = question("Trace and segment the surface", { to: "/workbench?scroll=PHerc0001", tone: "ok" });
  return {
    schema: "argus-scroll-status-v1", status: "OK", scroll: "PHerc0001", requested: "PHerc0001",
    display: "PHerc 0001", refusal: null, questions, steps,
    next_action: {
      step: "tracing", n: 6, label: "Trace and segment the surface", kind: "ACT",
      to: "/workbench?scroll=PHerc0001", why: "geometry can be produced", operator_only: false,
      action_ids: ["seed.grow.execute"], basis: ["argus.core.material_readiness"],
    },
    blocker: { step: "fibers", code: "DATA_UNAVAILABLE", text: "no fiber volume registered", basis: ["argus.core.material_readiness"] },
    contradictions: [],
    lineage: { chain_state: "VERIFIED", counts: { attempted: 2 }, path: "artifacts/stage_lineage/PHerc0001.jsonl" },
    generated_utc: "2026-09-18T00:00:00Z", claim_ceiling: "readiness only",
    ...over,
  };
}

export function refusedStatus(): ScrollStatus {
  return fixtureStatus({
    status: "REFUSED", scroll: null, requested: "PHerc-nope", display: "PHerc-nope", steps: [],
    refusal: { code: "IDENTITY_UNKNOWN", why: "unknown scroll id 'PHerc-nope'" }, blocker: null,
    next_action: {
      step: "select_scroll", n: 1, label: "Choose a registered scroll", kind: "ACT", to: "/",
      why: "unknown scroll id", operator_only: false, action_ids: [], basis: [],
    },
  });
}
