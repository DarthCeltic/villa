
export type Vec3 = [number, number, number];

export interface BrickMeta {
  schema: string;
  store: string;
  level: string;
  axis_order: "zyx";
  shape_zyx: Vec3;
  origin_voxel_zyx: Vec3;
  requested_centre_voxel_zyx: Vec3 | null;
  origin_adjusted_to_fit: boolean;
  level_extent_zyx: Vec3;
  bytes: number;
  estimated_gpu_bytes: number;
  spacing_um_zyx: Vec3 | null;
  spacing_status: "DECLARED_BY_STORE" | "NOT_DECLARED";
  origin_um_zyx: Vec3 | null;
  missing_chunk_count?: number;
  missing_voxels_are_zero_and_declared_missing?: boolean;
  sha256?: string;
  display_mapping?: { mode: string; clip_max: number; formula: string; comparable_with: string };
  scroll?: string;
  volume?: string;
  note?: string;
}

export interface BrickRequest {
  store: string;
  level: string;
  scroll: string;
  volume?: string | null;
  centre: Vec3;
  edge: number;
  origin?: Vec3;
  shape?: Vec3;
  maxTexture?: number | null;
  budgetMiB?: number;
  vramMiB?: number | null;
  allowPartial?: boolean;
}

export const DEFAULT_EDGE = 256;
export const LARGER_EDGE = 512;
export const CONSERVATIVE_TEXTURE_LIMIT = 256;

export function brickQuery(r: BrickRequest): string {
  const p = new URLSearchParams();
  p.set("store", r.store);
  p.set("level", r.level);
  p.set("scroll", r.scroll);
  if (r.volume) p.set("volume", r.volume);
  let voxels = r.edge ** 3;
  if (r.origin && r.shape) {
    p.set("origin", r.origin.join(","));
    p.set("shape", r.shape.join(","));
    voxels = r.shape[0] * r.shape[1] * r.shape[2];
  } else {
    p.set("centre", r.centre.join(","));
    p.set("edge", String(r.edge));
  }
  p.set("budget_mib", String(r.budgetMiB ?? Math.max(64, Math.ceil(voxels / (1 << 20)))));
  if (r.maxTexture) p.set("max_texture", String(r.maxTexture));
  if (r.vramMiB) p.set("vram_mib", String(r.vramMiB));
  if (r.allowPartial) p.set("allow_partial", "true");
  return p.toString();
}

export type HardwareTier = "SOFTWARE_RENDERER" | "UNKNOWN_GPU" | "DISCRETE_GPU";

export interface TierInfo {
  tier: HardwareTier;
  defaultEdge: number;
  largestEdge: number;
  vramBudgetMiB: number;
  why: string;
}

const SOFTWARE = /swiftshader|llvmpipe|softpipe|software|basic render|microsoft basic/i;
const DISCRETE = /nvidia|geforce|quadro|(^|[^a-z])rtx|(^|[^a-z])gtx|radeon rx|radeon pro|amd radeon|apple m\d|intel\(r\) arc/i;

export function hardwareTier(renderer: string | null, max3d: number | null): TierInfo {
  const r = renderer ?? "";
  if (SOFTWARE.test(r))
    return { tier: "SOFTWARE_RENDERER", defaultEdge: 128, largestEdge: 256, vramBudgetMiB: 192, why: "the browser renders in software, so bricks are kept small to stay interactive" };
  if (DISCRETE.test(r) && (max3d ?? 0) >= 2048)
    return { tier: "DISCRETE_GPU", defaultEdge: DEFAULT_EDGE, largestEdge: LARGER_EDGE, vramBudgetMiB: 1536, why: "a discrete-class GPU with a large 3D texture limit; a larger brick is allowed on request" };
  return { tier: "UNKNOWN_GPU", defaultEdge: DEFAULT_EDGE, largestEdge: DEFAULT_EDGE, vramBudgetMiB: 512, why: "the GPU class is not recognised, so the default brick is used and larger ones are not offered" };
}

export function chooseEdge(opts: { maxTexture: number | null; wantLarger?: boolean; levelExtent?: Vec3 | null; tier?: TierInfo | null }): number {
  const limit = opts.maxTexture && opts.maxTexture > 0 ? opts.maxTexture : CONSERVATIVE_TEXTURE_LIMIT;
  const largest = opts.tier ? opts.tier.largestEdge : LARGER_EDGE;
  let edge = opts.wantLarger && limit >= LARGER_EDGE && largest >= LARGER_EDGE ? LARGER_EDGE : (opts.tier?.defaultEdge ?? DEFAULT_EDGE);
  edge = Math.min(edge, limit);
  if (opts.levelExtent) edge = Math.min(edge, ...opts.levelExtent);
  return Math.max(1, Math.floor(edge));
}

export interface Refusal {
  code: string;
  why: string;
}

export function refusalFrom(status: number, body: unknown): Refusal {
  const b = (body ?? {}) as { code?: string; why?: string; detail?: unknown; error?: string };
  if (b.code || b.why) return { code: b.code ?? "REFUSED", why: b.why ?? "the service refused this brick" };
  const detail = typeof b.detail === "string" ? b.detail : null;
  if (status === 409) return { code: "IDENTITY_MISMATCH", why: detail ?? "the store's identity does not match the selected scroll or volume" };
  if (status === 403) return { code: "OUTSIDE_DECLARED_ROOTS", why: detail ?? "outside the declared volume roots" };
  if (status === 423) return { code: "SEALED", why: detail ?? "under an active blinded experiment" };
  if (status === 422) return { code: "BAD_REQUEST", why: detail ?? "the request is incomplete" };
  return { code: "REFUSED", why: detail ?? `HTTP ${status}` };
}

export interface Brick {
  meta: BrickMeta;
  data: Uint8Array;
}

export function parseBrick(metaHeader: string | null, buffer: ArrayBuffer): Brick {
  if (!metaHeader) throw new Error("the brick response carried no X-Argus-Brick-Meta header");
  const meta = JSON.parse(metaHeader) as BrickMeta;
  const [z, y, x] = meta.shape_zyx;
  if (buffer.byteLength !== z * y * x) {
    throw new Error(`the brick is ${buffer.byteLength} bytes but its declared shape ${z}x${y}x${x} needs ${z * y * x}`);
  }
  return { meta, data: new Uint8Array(buffer) };
}

export async function sha256Hex(bytes: Uint8Array): Promise<string | null> {
  const c = (globalThis as { crypto?: Crypto }).crypto;
  if (!c?.subtle) return null;
  const digest = await c.subtle.digest("SHA-256", bytes as unknown as BufferSource);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

export type Blend = "composite" | "mip";

export interface Display {
  windowLo: number;
  windowHi: number;
  opacity: number;
  threshold: number;
}

export const DEFAULT_DISPLAY: Display = { windowLo: 0, windowHi: 255, opacity: 0.5, threshold: 0 };

export function clampDisplay(d: Display): Display {
  const lo = Math.min(254, Math.max(0, Math.round(d.windowLo)));
  const hi = Math.min(255, Math.max(lo + 1, Math.round(d.windowHi)));
  return { windowLo: lo, windowHi: hi, opacity: Math.min(1, Math.max(0, d.opacity)), threshold: Math.min(255, Math.max(0, Math.round(d.threshold))) };
}

export function transferPoints(display: Display): { opacity: [number, number][]; colour: [number, number, number, number][] } {
  const d = clampDisplay(display);
  const t = d.threshold;
  const opacity: [number, number][] = [[0, 0]];
  if (t > 0) opacity.push([Math.max(0, t - 1), 0]);
  opacity.push([Math.max(t, d.windowLo), 0], [d.windowHi, d.opacity]);
  if (d.windowHi < 255) opacity.push([255, d.opacity]);
  const colour: [number, number, number, number][] = [[d.windowLo, 0, 0, 0], [d.windowHi, 1, 1, 1]];
  return { opacity: dedupe(opacity), colour };
}

function dedupe(pts: [number, number][]): [number, number][] {
  const out: [number, number][] = [];
  for (const p of pts.sort((a, b) => a[0] - b[0])) {
    if (out.length && out[out.length - 1]![0] === p[0]) out[out.length - 1] = p;
    else out.push(p);
  }
  return out;
}

export interface ClipBox {
  min: Vec3;
  max: Vec3;
}

export const FULL_CLIP: ClipBox = { min: [0, 0, 0], max: [1, 1, 1] };

export function clipPlanes(box: ClipBox, worldMin: Vec3, worldMax: Vec3): { origin: Vec3; normal: Vec3 }[] {
  const at = (axis: number, f: number) => worldMin[axis]! + f * (worldMax[axis]! - worldMin[axis]!);
  const planes: { origin: Vec3; normal: Vec3 }[] = [];
  for (let axis = 0; axis < 3; axis++) {
    const lo: Vec3 = [worldMin[0], worldMin[1], worldMin[2]];
    const hi: Vec3 = [worldMin[0], worldMin[1], worldMin[2]];
    lo[axis] = at(axis, box.min[axis]!);
    hi[axis] = at(axis, box.max[axis]!);
    const nlo: Vec3 = [0, 0, 0];
    const nhi: Vec3 = [0, 0, 0];
    nlo[axis] = 1;
    nhi[axis] = -1;
    if (box.min[axis]! > 0) planes.push({ origin: lo, normal: nlo });
    if (box.max[axis]! < 1) planes.push({ origin: hi, normal: nhi });
  }
  return planes;
}

export function physicalOf(meta: BrickMeta, voxelZyx: Vec3): Vec3 | null {
  const s = meta.spacing_um_zyx;
  return s ? [voxelZyx[0] * s[0], voxelZyx[1] * s[1], voxelZyx[2] * s[2]] : null;
}

export function centreOfBrick(meta: BrickMeta): Vec3 {
  return [0, 1, 2].map((i) => meta.origin_voxel_zyx[i]! + Math.floor(meta.shape_zyx[i]! / 2)) as Vec3;
}

export function mib(bytes: number): string {
  return `${(bytes / (1 << 20)).toFixed(bytes < (1 << 20) ? 2 : 1)} MiB`;
}

export function estimateLine(meta: Pick<BrickMeta, "shape_zyx" | "bytes" | "estimated_gpu_bytes">): string {
  const [z, y, x] = meta.shape_zyx;
  return `${z} x ${y} x ${x} voxels, ${mib(meta.bytes)} to load, about ${mib(meta.estimated_gpu_bytes)} of GPU memory`;
}

export function gridMismatch(ct: BrickMeta, mask: BrickMeta): string | null {
  if (ct.level !== mask.level) return `the mask is level ${mask.level} but the brick is level ${ct.level}`;
  for (let i = 0; i < 3; i++) {
    if (ct.shape_zyx[i] !== mask.shape_zyx[i] || ct.origin_voxel_zyx[i] !== mask.origin_voxel_zyx[i]) return "the mask covers a different box than the brick";
  }
  const a = ct.spacing_um_zyx;
  const b = mask.spacing_um_zyx;
  if ((a === null) !== (b === null) || (a && b && a.some((v, i) => Math.abs(v - b[i]!) > 1e-9))) return "the mask declares a different voxel spacing than the brick";
  if (mask.missing_chunk_count) return `${mask.missing_chunk_count} chunk(s) of the mask are missing, so it does not describe the whole brick`;
  return null;
}

export function applyMask(data: Uint8Array, mask: Uint8Array): { data: Uint8Array; hidden: number } {
  if (data.length !== mask.length) throw new Error(`the mask has ${mask.length} voxels but the brick has ${data.length}`);
  const out = new Uint8Array(data.length);
  let hidden = 0;
  for (let i = 0; i < data.length; i++) {
    if (mask[i]! > 0) out[i] = data[i]!;
    else hidden++;
  }
  return { data: out, hidden };
}


export interface LevelGeometry {
  shape?: Vec3 | null;
  pitch_um_yx?: [number, number] | null;
}

export function crosshairOnLevel(cur: LevelGeometry, next: LevelGeometry, at: Vec3): Vec3 {
  const ratio = (axis: 0 | 1 | 2): number =>
    cur.shape && next.shape && cur.shape[axis] > 0 ? next.shape[axis] / cur.shape[axis] : 1;
  const [z, y, x] = at;
  const cp = cur.pitch_um_yx;
  const np = next.pitch_um_yx;
  const ry = cp && np && np[0] > 0 ? cp[0] / np[0] : ratio(1);
  const rx = cp && np && np[1] > 0 ? cp[1] / np[1] : ratio(2);
  const clamp = (v: number, n: number | undefined) => (n ? Math.max(0, Math.min(n - 1, v)) : v);
  return [
    clamp(Math.round(z * ratio(0)), next.shape?.[0]),
    clamp(Math.round(y * ry), next.shape?.[1]),
    clamp(Math.round(x * rx), next.shape?.[2]),
  ];
}

export function startingView(
  levels: { level: string; openable: boolean; shape?: Vec3 | null }[],
  identityRoi?: { array_path: string; centre_zyx: Vec3 } | null,
): { level: string; crosshair: Vec3 } | null {
  const first = levels.find((l) => l.openable && l.shape);
  if (!first) return null;
  const roiLevel = identityRoi ? levels.find((l) => l.level === identityRoi.array_path && l.openable && l.shape) : undefined;
  if (identityRoi && roiLevel && roiLevel.shape) {
    const c = identityRoi.centre_zyx;
    if (c.every((v, i) => v >= 0 && v < (roiLevel.shape![i] ?? 0))) return { level: roiLevel.level, crosshair: [c[0], c[1], c[2]] };
  }
  const [z, y, x] = first.shape!;
  return { level: first.level, crosshair: [Math.floor(z / 2), Math.floor(y / 2), Math.floor(x / 2)] };
}
