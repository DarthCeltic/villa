
import { getJson, type FailureKind } from "./lib/http";

export type Operational =
  | "PENDING" | "RUNNING" | "STALLED" | "COMPLETE" | "REFUSED" | "UNKNOWN";

export type Certified =
  | "CERTIFIED_SURFACE" | "CERTIFIED_2D" | "CERTIFIED_INK_CANDIDATE" | null;

export interface Progress {
  present: boolean;
  state: string;
  reported_state?: string;
  stage?: string | null;
  done: number | null;
  total: number | null;
  unit?: string | null;
  fraction: number | null;
  age_s: number | null;
  stale: boolean | null;
  pid?: number | null;
  detail?: string | null;
  why?: string | null;
}

export interface StageRow {
  module: string;
  status: "PASS" | "REFUSED" | string;
  certified: Certified;
  seconds?: number | null;
  dir?: string | null;
  refusal_class?: string | null;
  refusal_reason?: string | null;
}

export interface Artifact {
  name: string;
  relpath: string;
  bytes: number;
  path: string | null;
  sha256?: string;
  withheld?: string;
}

export interface Acquisition {
  volume_id: string;
  voxel_um: number;
  energy_kev: number;
  pitch_um_per_px: number;
  pyramid_level?: number | null;
}

export interface Geometry {
  verdict: string;
  sheet_following: boolean | null;
  vertices_inside_volume?: number | null;
  jump_fraction: number | null;
  step_max_ratio: number | null;
  step_median_vox?: number | null;
  n_edges?: number | null;
  coverage?: number | null;
  grid_shape?: [number, number] | null;
  chunks_sampled?: number | null;
  volume_shape?: [number, number, number] | null;
}

export interface DetectorDecl {
  name?: string;
  checkpoint?: string;
  checkpoint_sha256_16?: string;
  qualified_family?: string | null;
  qualified_family_note?: string;
  qualified_window_mm?: number | null;
}

export interface RunRecord {
  schema: string;
  foreign?: boolean;
  run_id: string;
  run_dir: string;
  collection: string | null;
  target: string | null;
  stage: string | null;
  operational_state: Operational;
  highest_certified_stage: Certified;
  terminal: string | null;
  refusal_class: string | null;
  refusal_reason: string | null;
  progress: Progress;
  blinding: { sealed: boolean; marker: string | null; note: string | null };
  artifacts: Artifact[];
  hashes: Record<string, string>;
  stages?: StageRow[];
  acquisition: Acquisition | null;
  acquisition_family: string | null;
  geometry: Geometry | null;
  geometry_refusal: Geometry | null;
  mesh_dir: string | null;
  mesh_sha256: Record<string, string> | null;
  orientation: string | null;
  physical_window_mm: number | null;
  detector: DetectorDecl | null;
  detail?: unknown;
  environment?: { python?: string; platform?: string; argus_core_sha256?: string };
}

export interface SealedExperiment {
  run_id: string;
  run_dir: string;
  sealed: true;
  marker: string;
  marker_text: string;
  surfaces_staged: number;
  surfaces: string[];
  readings: { done: number; total: number | null; why: string };
  progress: Progress;
  note: string;
}

export interface Observatory {
  schema: string;
  generated_at: number;
  runs: RunRecord[];
  sealed_experiments: SealedExperiment[];
  counts: {
    operational: Partial<Record<Operational, number>>;
    highest_certified: Record<string, number>;
    sealed: number;
    total: number;
    foreign_receipts_ignored: number;
  };
  missing_roots: string[];
  note: string;
}

export interface RuntimeIdentity {
  api_contract: string;
  source_root: string;
  build_sha: string;
  truth_package: string;
  artifact_roots: string[];
}

export interface RuntimeReport {
  schema: string;
  service_identity?: RuntimeIdentity;
  interpreter?: { running?: string; declared?: string; matches?: boolean };
  lock?: { matches?: boolean; sha256?: string | null; declared_sha256?: string | null };
  disks?: Record<string, { free_gib?: number; total_gib?: number }>;
}

export interface MeshLattice {
  schema: string;
  mesh_dir: string;
  grid_shape: [number, number];
  decimation_step: number;
  points: [number, number][];
  points3?: [number, number, number][];
  faces?: [number, number, number][];
  winding_ids?: number[] | null;
  jump_edges: {
    from: [number, number];
    to: [number, number];
    from3?: [number, number, number];
    to3?: [number, number, number];
    ratio: number;
  }[];
  projection: {
    axes: [string, string];
    chosen_by: string;
    default_camera_axes?: [number, number];
    note?: string;
  };
  median_step_vox: number;
  jump_ratio_limit: number;
  is_not_image_evidence: boolean;
  note: string;
}

export const CERT_RANK: Record<string, number> = {
  CERTIFIED_SURFACE: 1,
  CERTIFIED_2D: 2,
  CERTIFIED_INK_CANDIDATE: 3,
};

export function rankCertified(c: Certified): number {
  return c ? (CERT_RANK[c] ?? 0) : 0;
}

async function get<T>(url: string): Promise<T> {
  const res = await getJson<T>(url);
  if (res.ok) return res.data;
  throw new HttpError(res.status ?? 0, res.message, res.kind, res.bodyPreview);
}

export class HttpError extends Error {
  constructor(
    public status: number,
    message: string,
    public kind: FailureKind = "HTTP_ERROR",
    public bodyPreview: string | null = null,
  ) {
    super(message);
  }
  get isSealed() {
    return this.status === 423;
  }
  get retryable() {
    return this.kind === "TIMEOUT" || this.kind === "NETWORK" || this.kind === "SERVER_ERROR";
  }
}

export const api = {
  health: () => get<{ ok: boolean; read_only: boolean }>("/api/health"),
  runtime: () => get<RuntimeReport>("/api/runtime"),
  observatory: () => get<Observatory>("/api/observatory"),
  run: (id: string) => get<RunRecord>(`/api/runs/${encodeURIComponent(id)}`),
  mesh: (path: string, opts?: { stride?: number; withWindings?: boolean }) => {
    const q = new URLSearchParams({ path });
    if (opts?.stride) q.set("stride", String(opts.stride));
    if (opts?.withWindings) q.set("with_windings", "true");
    return get<MeshLattice>(`/api/mesh?${q.toString()}`);
  },
  fileUrl: (path: string) => `/api/file?path=${encodeURIComponent(path)}`,
  unrollTargets: () => get<UnrollIndex>("/api/unroll"),
  unroll: (target: string) => get<unknown>(`/api/unroll?target=${encodeURIComponent(target)}`),
};

export interface UnrollTarget {
  key: string;
  base: string;
  name: string;
  classification: string;
  acquisition: string;
  has_ground_truth: boolean;
  one_line: string;
}

export interface UnrollIndex {
  targets: UnrollTarget[];
  comparison_rule: string;
}

export type FeedState = {
  data: Observatory | null;
  ageS: number;
  connected: boolean;
  socketOpen: boolean;
  snapshotAgeS: number | null;
  error: string | null;
};

const WS_CANDIDATES = (): string[] => {
  const runtime = (window as unknown as { __ARGUS_WS__?: string }).__ARGUS_WS__;
  if (runtime) return [runtime];
  const override = (import.meta as { env?: Record<string, string> }).env
    ?.VITE_ARGUS_WS;
  if (override) return [override];
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return [`${proto}://${location.host}/ws/observatory`];
};

const FIRST_MESSAGE_TIMEOUT_MS = 12000;

export function subscribe(onState: (s: FeedState) => void): () => void {
  const urls = WS_CANDIDATES();
  let attempt = 0;
  let ws: WebSocket | null = null;
  let closed = false;
  let last: Observatory | null = null;
  let lastMsg = Date.now();
  let lastSnapshot: number | null = null;
  let socketOpen = false;
  let error: string | null = null;
  let firstTimer: ReturnType<typeof setTimeout> | null = null;

  const emit = () => {
    if (closed) return;
    onState({
      data: last,
      ageS: (Date.now() - lastMsg) / 1000,
      connected: socketOpen && lastSnapshot !== null,
      socketOpen,
      snapshotAgeS: lastSnapshot === null ? null : (Date.now() - lastSnapshot) / 1000,
      error,
    });
  };

  const open = () => {
    if (closed) return;
    if (firstTimer) clearTimeout(firstTimer);
    const url = urls[attempt % urls.length] as string;
    const mine = new WebSocket(url);
    ws = mine;

    firstTimer = setTimeout(() => {
      if (closed || last !== null || ws !== mine) return;
      attempt += 1;
      error =
        "the feed socket opened on this origin and delivered no snapshot within " +
        `${Math.round(FIRST_MESSAGE_TIMEOUT_MS / 1000)}s`;
      emit();
      try {
        mine.close();
      } catch {
      }
    }, FIRST_MESSAGE_TIMEOUT_MS);

    mine.onopen = () => {
      socketOpen = true;
      lastMsg = Date.now();
      emit();
    };
    mine.onmessage = (e) => {
      if (firstTimer) clearTimeout(firstTimer);
      lastMsg = Date.now();
      try {
        const msg = JSON.parse(e.data);
        if (msg.schema && String(msg.schema).startsWith("argus-feed")) {
          last = msg;
          lastSnapshot = Date.now();
          error = null;
        }
      } catch {
        error = "the feed sent something this client could not parse";
      }
      emit();
    };
    mine.onerror = () => {
      error = "the ARGUS service is not answering";
      emit();
    };
    mine.onclose = () => {
      if (ws !== mine) return;
      if (firstTimer) clearTimeout(firstTimer);
      socketOpen = false;
      emit();
      if (!closed) setTimeout(open, 1200);
    };
  };

  open();
  const tick = setInterval(emit, 1000);
  return () => {
    closed = true;
    clearInterval(tick);
    if (firstTimer) clearTimeout(firstTimer);
    ws?.close();
  };
}
