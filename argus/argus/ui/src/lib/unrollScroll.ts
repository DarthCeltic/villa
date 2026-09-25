import type { UnrollTarget } from "../api";

export function scrollOfTarget(t: Pick<UnrollTarget, "name"> | null | undefined): string | null {
  if (!t) return null;
  const m = /PHerc[0-9A-Za-z]+/.exec(t.name);
  return m ? m[0] : null;
}

export interface StackResultClass {
  target?: string;
  target_class?: string;
  exposure_basis?: string;
  detector?: string;
  detector_cross_scroll_qualified?: boolean;
  acquisition?: string;
  metric?: string | null;
  score?: number | null;
  presentation?: string;
  banner?: string;
  may_claim_discovery?: boolean;
  may_claim_ink_found?: boolean;
  not_established?: string[];
}

export interface StackDetail {
  result_class: StackResultClass;
  banner_required_on_every_surface?: string;
  classification?: string;
  target: { name: string; pitch_um?: number; acquisition?: string; renderer?: string };
  levels: Record<string, {
    what: string;
    effective_pitch_um: number;
    readable: string;
    size: number[];
    images: Record<string, { path: string; sha256?: string }>;
  }>;
}

export type EvidenceTier = "CONTROL" | "DEVELOPMENT" | "EXPLORATORY" | "ADMISSIBLE" | "UNCLASSIFIED";

export function tierOf(rc: StackResultClass | null | undefined): EvidenceTier {
  const p = (rc?.presentation || "").toUpperCase();
  if (!p) return "UNCLASSIFIED";
  if (p.includes("CONTROL")) return "CONTROL";
  if (p.includes("EXPLORATORY") || p.includes("CANDIDATE")) return "EXPLORATORY";
  if (p.includes("DEVELOPMENT")) return "DEVELOPMENT";
  if ((p.includes("ADMISSIBLE") || p.includes("VALIDATED")) && rc?.may_claim_ink_found === true) {
    return "ADMISSIBLE";
  }
  return "UNCLASSIFIED";
}

export const TIER_WORD: Record<EvidenceTier, string> = {
  CONTROL: "control",
  DEVELOPMENT: "development",
  EXPLORATORY: "exploratory",
  ADMISSIBLE: "admissible",
  UNCLASSIFIED: "unclassified",
};
