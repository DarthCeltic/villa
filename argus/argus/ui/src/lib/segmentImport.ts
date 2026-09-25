export interface SegmentForm {
  path: string;
  attach: string;
  attestedBy: string;
  reason: string;
}

export const EMPTY_SEGMENT_FORM: SegmentForm = { path: "", attach: "", attestedBy: "", reason: "" };

const MIN_REASON = 8;

export function segmentParams(f: SegmentForm): Record<string, unknown> | null {
  const path = f.path.trim();
  if (!path) return null;
  const out: Record<string, unknown> = { path };
  if (f.attach.trim()) {
    out.attach_volume_source = f.attach.trim();
    if (f.attestedBy.trim()) out.attested_by = f.attestedBy.trim();
    if (f.reason.trim()) out.attestation_reason = f.reason.trim();
  }
  return out;
}

export function attachProblem(f: SegmentForm): string | null {
  if (!f.attach.trim()) return null;
  if (!f.attestedBy.trim()) return "an attached identity needs attested_by: who vouches for it";
  if (f.reason.trim().length < MIN_REASON) return `an attached identity needs a reason of at least ${MIN_REASON} characters`;
  return null;
}
