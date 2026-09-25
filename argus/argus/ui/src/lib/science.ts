import { getJson, type Fetched } from "./http";
import type { OpsTone } from "../components/OpsKit";

export type MountStatus =
  | "MOUNTED_VERIFIED"
  | "NOT_MOUNTED"
  | "HASH_MISMATCH"
  | "IDENTITY_MISMATCH"
  | "UNVERIFIED_TIME_BUDGET";

export const MOUNT_STATUSES: MountStatus[] = [
  "MOUNTED_VERIFIED",
  "NOT_MOUNTED",
  "HASH_MISMATCH",
  "IDENTITY_MISMATCH",
  "UNVERIFIED_TIME_BUDGET",
];

const MOUNT_WORDS: Record<MountStatus, string> = {
  MOUNTED_VERIFIED: "mounted and hash-verified",
  NOT_MOUNTED: "not mounted",
  HASH_MISMATCH: "hash mismatch, not used",
  IDENTITY_MISMATCH: "identity refused",
  UNVERIFIED_TIME_BUDGET: "not verified within the time budget",
};

export function mountWords(s: string): string {
  return MOUNT_WORDS[s as MountStatus] ?? s.replace(/_/g, " ").toLowerCase();
}

export function mountTone(s: string): OpsTone {
  if (s === "MOUNTED_VERIFIED") return "ok";
  if (s === "HASH_MISMATCH" || s === "IDENTITY_MISMATCH") return "bad";
  return "warn";
}

export interface MountRow {
  name?: string;
  status: MountStatus;
  expected_sha256: string;
  expected_path?: string;
  url?: string;
}


export interface Selection {
  manifest: string;
  label: string;
  scroll: string | null;
  import_verified: boolean;
  groups?: number;
  files?: number;
  problems?: string[];
  latest_record: {
    utc: string;
    plan_sha256: string;
    counts: Record<string, number>;
    copied_bytes: number;
    identity_verdict?: string;
  } | null;
}

export interface AttachmentsState {
  read_only: boolean;
  selections: Selection[];
  records: number;
  mount_root_configured: boolean;
  note: string;
}

export interface AttachRow {
  name: string;
  kind: string;
  sha256: string;
  status: MountStatus;
  size?: number;
  expected_path?: string;
  observed_sha256?: string;
  analyst_only?: boolean;
}

export interface AttachGroup {
  group: string;
  dir: string;
  files: AttachRow[];
}

export interface AttachPlan {
  manifest: string;
  scroll: string;
  plan_sha256: string;
  would_be_refused?: boolean;
  refusal?: { code: string; why: string };
  identity?: {
    verdict: string;
    reasons: string[];
    physical_scroll: string;
    exact_volume: string;
    operation_class: string;
  };
  import?: { verified: boolean; problems: string[]; source_commit?: string };
  private?: {
    mount_root_configured: boolean;
    total: number;
    counts: Record<string, number>;
    groups: AttachGroup[];
  };
}

export const selectionKey = (s: { manifest: string; scroll: string | null }): string =>
  `${s.manifest}|${s.scroll ?? ""}`;

export function attachParams(s: { manifest: string; scroll: string | null } | null): {
  manifest: string;
  scroll: string;
} | null {
  return s && s.scroll ? { manifest: s.manifest, scroll: s.scroll } : null;
}

export function statusCounts(rows: { status: string }[]): Record<MountStatus, number> {
  const out = Object.fromEntries(MOUNT_STATUSES.map((s) => [s, 0])) as Record<MountStatus, number>;
  for (const r of rows) if (r.status in out) out[r.status as MountStatus] += 1;
  return out;
}

export function attachHeadline(counts: Record<string, number> | undefined): string {
  if (!counts) return "not planned yet";
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const ok = counts.MOUNTED_VERIFIED ?? 0;
  if (total === 0) return "nothing listed";
  if (ok === total) return `all ${total} private files are mounted and hash-verified`;
  if (ok === 0) return `none of ${total} private files is verified on this host`;
  return `${ok} of ${total} private files are mounted and hash-verified`;
}


export interface Region {
  task_id: string;
  rank: number;
  block_centre_xyz_L0_voxels: (number | null)[];
  fine_grid_block_origin_yx: number[];
  flags: string[];
  review_link: string;
}

// Public release: the collection view is a generic shape. The research collections it was built
// for are not part of the public release, so the public service answers with nothing to show.
export interface CollectionPiece {
  segment: string;
  geometry_state: string;
  render_um_per_px: number;
  presented_as_ink: boolean;
  review_priority_count: number;
  regions: Region[];
  private_render: MountRow | null;
}

export interface CloseoutView {
  read_only: boolean;
  imports: {
    closeout: { verified: boolean; problems: string[]; source_commit?: string };
    seals: { verified: boolean; problems: string[]; source_commit?: string };
  };
  sealed_roots?: Record<string, string>;
  human_review?: { answers_recorded: number; statement: string };
  native: {
    physical_scroll: string;
    exact_volume: string;
    operation_class: string;
    geometry_status: string;
    counts: { pieces: number; pass: number; uncertain: number };
    review_priority_total: number;
    pieces: CollectionPiece[];
    claim_ceiling: string;
    detector_run: boolean;
    presented_as_ink: boolean;
  } | null;
}

export function pitchRange(pieces: { render_um_per_px: number }[]): { min: number; max: number; text: string } | null {
  const v = pieces.map((p) => p.render_um_per_px).filter((x) => Number.isFinite(x));
  if (v.length === 0) return null;
  const min = Math.min(...v);
  const max = Math.max(...v);
  return { min, max, text: `${min.toFixed(2)}–${max.toFixed(2)} µm/px` };
}

export function geometryCounts(pieces: { geometry_state: string }[]): { pass: number; uncertain: number } {
  return {
    pass: pieces.filter((p) => p.geometry_state.endsWith("_PASS")).length,
    uncertain: pieces.filter((p) => p.geometry_state.endsWith("_UNCERTAIN")).length,
  };
}

export function reviewTotals(pieces: { regions: unknown[]; review_priority_count: number }[]): number {
  return pieces.reduce((n, p) => n + (p.regions.length || p.review_priority_count), 0);
}

export function parseQueueLink(search: string): { queue: string; task: string | null } | null {
  const q = new URLSearchParams(search);
  const queue = q.get("queue");
  return queue ? { queue, task: q.get("task") } : null;
}


export const REVIEW_CHOICES = ["LIKELY_SIGNAL", "LIKELY_STRUCTURE", "UNCERTAIN", "REFUSE"] as const;
export type ReviewChoice = (typeof REVIEW_CHOICES)[number];

export interface QueueTaskRow {
  task_id: string;
  label: string;
  group: string | null;
  answers: number;
  you_answered: boolean;
  status: string;
}

export interface SealedQueue {
  queue: string;
  state?: "NOT_INSTALLED";
  integrity?: "NOT_VERIFIED";
  why?: string;
  n_tasks: number;
  root_sha256: string;
  tasks: QueueTaskRow[];
  answered_tasks: number;
  promotable_tasks: number;
  choices: { value: ReviewChoice; meaning: string }[];
  notice: { gate: string; blinding: string; abstention: string; no_ink_claim: string; evidence: string };
}

export interface SealedLayer {
  name: string;
  role: string;
  label: string;
  derived: boolean;
  sha256: string;
  url: string;
  mount: MountRow | null;
}

export interface SealedTask {
  task_id: string;
  queue: string;
  layers: SealedLayer[];
  required_layers: string[];
  choices: { value: ReviewChoice; meaning: string }[];
  identity: Record<string, unknown>;
  mesh: Record<string, unknown>;
  scale: { um_per_px: number; native_voxel_um: number; note: string };
  coordinates: Record<string, unknown>;
  false_positive_controls: Record<string, unknown>;
  notice: SealedQueue["notice"];
}

export interface SealedResults {
  withheld: boolean;
  why?: string;
  promotion?: {
    status: string;
    reasons: string[];
    counts: Record<string, number>;
    abstentions: string[];
    disagreement: boolean;
    independent_human_reviewers: number;
  };
  answers?: { reviewer_id: string; reviewer_class: string; value: string; evidence_role?: string }[];
}

export function missingRequired(task: SealedTask): SealedLayer[] {
  const need = new Set(task.required_layers);
  return task.layers.filter((l) => need.has(l.name) && l.mount?.status !== "MOUNTED_VERIFIED");
}

export function answerParams(
  queue: string,
  taskId: string,
  value: ReviewChoice,
  reviewer: { id: string; cls: string },
  imagesViewed: boolean,
  confidence: number,
  durationS: number,
): Record<string, unknown> {
  return {
    kind: queue,
    task_id: taskId,
    value,
    reviewer_id: reviewer.id,
    reviewer_class: reviewer.cls,
    images_viewed: imagesViewed,
    confidence,
    duration_s: Math.max(0, Math.round(durationS * 10) / 10),
  };
}

export function promotionWords(status: string): string {
  if (status === "PROMOTABLE") return "candidate: two named people agree a place is worth a closer look";
  if (status === "WITHHELD_UNTIL_YOU_ANSWER") return "withheld until you answer";
  return "not promoted";
}

export const READING_GATE_STATEMENT =
  "No reading, OCR, transcription or translation consumes a task until two named people have independently " +
  "reviewed it with the image in front of them and none of the answers is an AI class.";


export const scienceApi = {
  closeout: (): Promise<Fetched<CloseoutView>> => getJson<CloseoutView>("/api/science_closeout", { timeoutMs: 120000 }),
  attachments: (): Promise<Fetched<AttachmentsState>> =>
    getJson<AttachmentsState>("/api/science_attachments", { timeoutMs: 60000 }),
  queue: (kind: string, reviewer?: string): Promise<Fetched<SealedQueue>> =>
    getJson<SealedQueue>(
      `/api/sealed_review/${encodeURIComponent(kind)}${reviewer ? `?reviewer=${encodeURIComponent(reviewer)}` : ""}`,
    ),
  task: (kind: string, id: string): Promise<Fetched<SealedTask>> =>
    getJson<SealedTask>(`/api/sealed_review/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`, {
      timeoutMs: 120000,
    }),
  results: (kind: string, id: string, reviewer: string): Promise<Fetched<SealedResults>> =>
    getJson<SealedResults>(
      `/api/sealed_review/${encodeURIComponent(kind)}/${encodeURIComponent(id)}/results?reviewer=${encodeURIComponent(reviewer)}`,
    ),
};
