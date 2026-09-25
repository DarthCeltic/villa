import type { OpsTone } from "../components/OpsKit";

export interface ResumableRow {
  job_id: string;
  kind: string;
  stage: string | null;
  action: string | null;
  state: string;
  resumable: boolean;
  why_not: string | null;
  unit_word: string;
  units_total: number;
  units_done: number;
  units_pending: number;
  resume_unit: string | null;
  stopped_after: string | null;
  pending_preview: string[];
  heartbeat: { age_s?: number | null; stale?: boolean | null; state?: string | null } | null;
  flag: { flag: string; enabled: boolean } | null;
  resume_params: { job_id: string; stage?: string };
}

export interface ResumablePayload {
  schema: string;
  utc: string;
  read_only: boolean;
  jobs: ResumableRow[];
  by_state: Record<string, number>;
  resumable: number;
  how: string;
}

const KIND_LABEL: Record<string, string> = {
  UNIT_LEDGER: "stage with a unit ledger",
  SCROLL_WORKFLOW: "scroll pipeline (stage lineage)",
  PROVIDER_TILED_RUN: "tiled provider run",
  NOT_RESUMABLE: "single-step action",
};

export function kindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? kind;
}

export function stateTone(state: string): OpsTone {
  if (state === "interrupted" || state === "halted" || state === "partial" || state === "paused") return "warn";
  if (state === "running") return "info";
  if (state === "unreadable" || state === "unverifiable" || state === "unresolvable") return "bad";
  return "idle";
}

export function stateWord(state: string): string {
  return state.replace(/_/g, " ");
}

export function stoppedLine(r: ResumableRow): string {
  const noun = r.unit_word;
  if (r.units_total === 0) return "no unit list could be read for this job";
  if (r.units_done === r.units_total) return `every ${noun} is done (${r.units_total} of ${r.units_total})`;
  const after = r.stopped_after ? `stopped after ${noun} ${r.stopped_after}` : `no ${noun} has completed`;
  const next = r.resume_unit ? `would resume at ${noun} ${r.resume_unit}` : "no resume point";
  return `${after}; ${next} (${r.units_done} of ${r.units_total} done)`;
}

export function pendingLine(r: ResumableRow): string {
  if (r.units_pending === 0) return `no ${r.unit_word} pending`;
  const shown = r.pending_preview.join(", ");
  const more = r.units_pending - r.pending_preview.length;
  return `${r.units_pending} pending: ${shown}${more > 0 ? ` and ${more} more` : ""}`;
}

export function resumeParams(r: ResumableRow): Record<string, unknown> {
  return r.resume_params.stage
    ? { job_id: r.resume_params.job_id, stage: r.resume_params.stage }
    : { job_id: r.resume_params.job_id };
}

export function rowKey(r: ResumableRow): string {
  return `${r.job_id}::${r.stage ?? ""}`;
}

const ORDER = ["interrupted", "halted", "partial", "paused", "running", "running_unverified"];

export function orderRows(rows: ResumableRow[]): ResumableRow[] {
  const rank = (r: ResumableRow) => {
    if (r.resumable) return 0;
    const i = ORDER.indexOf(r.state);
    return i >= 0 ? 1 + i : 100;
  };
  return [...rows].sort((a, b) => rank(a) - rank(b) || rowKey(a).localeCompare(rowKey(b)));
}

export function switchLine(r: ResumableRow): string | null {
  if (!r.flag) return null;
  return r.flag.enabled
    ? `${r.flag.flag} is set, so an approved resume will run`
    : `${r.flag.flag} is not set: the plan can be read, but the resume itself is refused until the operator sets it`;
}
