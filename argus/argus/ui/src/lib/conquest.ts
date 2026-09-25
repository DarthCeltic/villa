// Public release: renderer-comparison and flattening annotations are not part of the public
// release. These types keep their exported names so any importer still compiles; the public
// service answers with status "UNAVAILABLE" and a reason, and nothing here carries a figure.

export interface Promotion {
  ink: false;
  reading: false;
  candidate_ready: false;
  prize_authorized: false;
}

export interface RenderConvention {
  id: string;
  label: string;
  meaning: string;
  is_default: boolean;
}

export interface PieceParity {
  role: string;
  layer_label: string;
}

export interface ConquestStatus {
  schema: string;
  status?: "UNAVAILABLE";
  reason?: string;
  conventions?: RenderConvention[];
  promotion?: Promotion;
  claim_boundary?: string;
}

export interface TaskAnnotation {
  schema: string;
  status?: "UNAVAILABLE";
  reason?: string;
  annotated: boolean;
  task_sha256: string;
  local_um_per_px?: number;
  warnings?: string[];
  status_effect: "NONE";
  promotion: Promotion;
}

export function scaleBarPx(localUmPerPx: number | undefined, lengthUm: number): number | null {
  if (!localUmPerPx || !(localUmPerPx > 0)) return null;
  return Math.round((lengthUm / localUmPerPx) * 100) / 100;
}

export type FlatteningChoice = "original" | "new";
export type ConventionChoice = string;
export type CompareView = "parity" | "distortion" | "scale";

export function newFlatteningAvailable(_status: ConquestStatus | null): boolean {
  return false;
}

export interface PieceFlattening {
  schema: string;
  status?: "UNAVAILABLE";
  reason?: string;
  pieces: Record<string, { label: string; status_effect: "NONE"; promotion: Promotion }>;
}
