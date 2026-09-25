import type { BrickMeta, Vec3 } from "./volumeBrick";

export type OverlayKind = "mesh" | "cavity_mask" | "between_wrap" | "prediction" | "fiber";

export interface PredictionContract {
  artifact_kind: string;
  provider: { id: string; version?: string | null };
  exposure_state: string;
  control_verdict: string;
  qualification_state: string;
  status_words: string[];
}

export const REQUIRED_STATUS_WORDS = ["MODEL OUTPUT", "NOT QUALIFIED"] as const;

export interface OverlayLayer {
  id: string;
  kind: OverlayKind;
  label: string;
  producer: string;
  physicalScroll: string;
  sourceVolume: string | null;
  pitchUm: number | null;
  level?: string;
  transform: { kind: "identity_voxel_zyx" } | { kind: "unresolved"; note?: string } | { kind: "other"; note?: string };
  sha256: string | null;
  status: string;
  roiVoxelZyx?: { min: Vec3; max: Vec3 } | null;
  mesh?: { vertices: number[]; faces: number[] };
  prediction?: PredictionContract;
}

export interface Verdict {
  accepted: boolean;
  reasons: string[];
}

const INK_WORDS = /\b(ink|letter|text|reading)\b/i;

export function checkOverlay(layer: OverlayLayer, brick: BrickMeta, scroll: string, volume: string | null): Verdict {
  const reasons: string[] = [];
  if (layer.physicalScroll !== scroll) reasons.push(`layer is for ${layer.physicalScroll}, not ${scroll}`);
  if (!volume) reasons.push("the store declares no volume id, so a layer's source volume cannot be matched");
  else if (layer.sourceVolume !== volume) reasons.push(`layer's source volume ${layer.sourceVolume ?? "is not declared"} is not ${volume}`);
  if (layer.level !== undefined && layer.level !== brick.level) reasons.push(`layer coordinates are level ${layer.level} voxels but this brick is level ${brick.level}; no resampling is guessed`);
  if (layer.transform.kind !== "identity_voxel_zyx") reasons.push(`coordinate transform is ${layer.transform.kind}: it cannot be applied exactly, so the layer is not drawn`);
  const sp = brick.spacing_um_zyx;
  if (layer.pitchUm == null) reasons.push("layer declares no pitch");
  else if (!sp) reasons.push("the brick's store declares no spacing to compare the layer's pitch with");
  else if (Math.abs(layer.pitchUm - sp[2]) > 1e-6 * Math.max(1, sp[2])) reasons.push(`layer pitch ${layer.pitchUm} µm differs from the brick's ${sp[2]} µm`);
  if (!layer.sha256) reasons.push("layer carries no hash");
  if (layer.roiVoxelZyx) {
    const lo = brick.origin_voxel_zyx;
    const hi = [0, 1, 2].map((i) => lo[i]! + brick.shape_zyx[i]!);
    const overlaps = [0, 1, 2].every((i) => layer.roiVoxelZyx!.min[i]! < hi[i]! && layer.roiVoxelZyx!.max[i]! > lo[i]!);
    if (!overlaps) reasons.push("layer region does not intersect this brick");
  }
  if (layer.kind === "prediction") {
    if (INK_WORDS.test(layer.label)) reasons.push("a prediction is never labelled ink, letters or text; rename the layer to what the producer claims");
    const c = layer.prediction;
    if (!c) reasons.push("a prediction overlay carries no model-output contract (artifact kind, provider, exposure, sabotage/control verdict, qualification), so it is not shown");
    else {
      if (c.artifact_kind !== "MODEL_OUTPUT") reasons.push(`its artifact kind is ${c.artifact_kind}, not MODEL_OUTPUT`);
      if (!c.provider?.id) reasons.push("it names no provider");
      if (!c.exposure_state) reasons.push("it declares no exposure state");
      if (!c.control_verdict) reasons.push("it declares no sabotage/control verdict");
      for (const w of REQUIRED_STATUS_WORDS) if (!c.status_words?.includes(w)) reasons.push(`its status does not say ${w}`);
      if ((c.status_words?.length ?? 0) < 4) reasons.push("its status is missing words the contract requires");
    }
  }
  return { accepted: reasons.length === 0, reasons };
}
