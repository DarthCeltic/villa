import type { OverlayLayer } from "./overlayIdentity";
import type { Vec3 } from "./volumeBrick";

export interface TaskLayerInfo {
  id: string;
  kind: string;
  label: string;
  permitted: boolean;
  status: string;
  why: string;
  producer?: string;
  source_volume?: string | null;
  pitch_um?: number | null;
  sha256?: string | null;
}

export interface FiberRow {
  capability: "presence" | "hv_class" | "direction" | "adjacency" | "tracing";
  state: string;
  reason?: string;
  description?: string | null;
  upstream_capability?: boolean | null;
  satisfied_only_by: string;
  never_satisfied_by: string[];
}

export interface FiberMatrix {
  rows: FiberRow[];
  rule: string | null;
  contracts: { file: string; sha256: string }[];
}

export interface CoordinateSystem {
  positioning_system: string;
  positioning_statement: string;
  fine_grid_screening_indices: string;
  fine_grid_statement: string;
}

export interface TaskBinding {
  schema: string;
  task: { kind: string; task_id: string; task_sha256: string; queue_root_sha256: string; review_state: string };
  scroll: string;
  volume: string | null;
  level: string;
  pitch_um: number | null;
  coordinate_status: "RESOLVED" | "UNRESOLVED_NONFINITE";
  roi?: { min_zyx: Vec3; max_zyx: Vec3; centre_zyx: Vec3; halo_voxels: number; units: string };
  refusal?: { code: string; why: string };
  geometry: { state: string | null; watermarked: boolean | null; orientation: string; target_gate: { verdict: string; operation_class: string; registry_sha256: string } | null };
  layers: TaskLayerInfo[];
  coordinate_system: CoordinateSystem;
  fiber_capabilities: FiberMatrix;
  claims: string[];
}

export interface TaskMesh {
  task_id: string;
  scroll: string;
  volume: string | null;
  pitch_um: number | null;
  sha256: string;
  status: string;
  vertices: number[];
  faces: number[];
  n_triangles: number;
  quads_dropped_by_triangle_budget: number;
}

export interface StoreRow {
  store: string;
  volume_id?: string | null;
  scroll?: string | null;
}

export type TaskRef = { kind: string; taskId: string };

export function parseTaskParam(v: string | null): TaskRef | null {
  if (!v) return null;
  const i = v.indexOf(":");
  if (i <= 0 || i === v.length - 1) return null;
  const kind = v.slice(0, i);
  const taskId = v.slice(i + 1);
  if (!/^[A-Za-z0-9_]+$/.test(kind) || !/^[A-Za-z0-9_.-]+$/.test(taskId)) return null;
  return { kind, taskId };
}

export function taskLink(scroll: string, kind: string, taskId: string): string {
  const p = new URLSearchParams({ scroll, task: `${kind}:${taskId}`, mode: "volume" });
  return `/workbench?${p.toString()}`;
}

export type Refusal = { code: string; why: string };

export function pickStoreForTask(stores: StoreRow[], b: TaskBinding): { store: string } | Refusal {
  if (!b.volume) return { code: "TASK_DECLARES_NO_VOLUME", why: "the task names no volume, so no store can be matched to it" };
  const norm = (s: string | null | undefined) => (s ? s.replace(/\.zarr$/, "") : null);
  const hit = stores.filter((s) => norm(s.volume_id) === norm(b.volume));
  if (!hit.length)
    return {
      code: "NO_STORE_HOLDS_THIS_VOLUME",
      why: `no registered store for ${b.scroll} declares the volume ${b.volume}. Another volume is never substituted, so this task cannot be opened in the 3D viewer here.`,
    };
  return { store: hit[0]!.store };
}

export function scrollMatches(selected: string | null, b: TaskBinding): Refusal | null {
  if (selected === b.scroll) return null;
  return { code: "TASK_IS_FOR_ANOTHER_SCROLL", why: `this task is bound to ${b.scroll}, but the selected scroll is ${selected ?? "none"}` };
}

export function boundBrick(b: TaskBinding, levelExtent: Vec3, withHalo: boolean): { origin: Vec3; shape: Vec3; clamped: boolean } | Refusal {
  if (b.coordinate_status !== "RESOLVED" || !b.roi) return b.refusal ?? { code: "COORDINATE_UNRESOLVED", why: "the task has no resolved location" };
  const halo = withHalo ? b.roi.halo_voxels : 0;
  const origin: Vec3 = [0, 0, 0];
  const shape: Vec3 = [0, 0, 0];
  let clamped = false;
  for (let i = 0; i < 3; i++) {
    const lo = b.roi.min_zyx[i]! - halo;
    const hi = b.roi.max_zyx[i]! + halo;
    const cl = Math.max(0, lo);
    const ch = Math.min(levelExtent[i]!, hi);
    if (ch <= cl) return { code: "ROI_OUTSIDE_VOLUME", why: `the bound region lies outside this level's extent on axis ${"zyx"[i]}` };
    if (cl !== lo || ch !== hi) clamped = true;
    origin[i] = cl;
    shape[i] = ch - cl;
  }
  return { origin, shape, clamped };
}

export function meshOverlay(b: TaskBinding, m: TaskMesh): OverlayLayer {
  const roi = b.roi!;
  return {
    id: "task-mesh",
    kind: "mesh",
    label: "Surface mesh (identity-matched)",
    producer: "tifxyz mesh, hashed by the science lane",
    physicalScroll: m.scroll,
    sourceVolume: m.volume,
    pitchUm: m.pitch_um,
    transform: { kind: "identity_voxel_zyx" },
    sha256: m.sha256,
    status: m.status,
    level: "0",
    roiVoxelZyx: { min: roi.min_zyx.map((v) => v - roi.halo_voxels) as Vec3, max: roi.max_zyx.map((v) => v + roi.halo_voxels) as Vec3 },
    mesh: { vertices: m.vertices, faces: m.faces },
  };
}

export function notDrawn(b: TaskBinding): TaskLayerInfo[] {
  return b.layers.filter((l) => !l.permitted);
}
