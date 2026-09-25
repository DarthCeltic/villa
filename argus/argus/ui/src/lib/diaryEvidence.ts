import {
  declaredReceipt,
  declaredReceiptUrl,
  type DeclaredReceiptResponse,
} from "./declaredReceipts";

export interface EvidenceRow {
  path: string;
  dir_why?: string;
  sha256: string;
  bytes: number;
  pixels: { w: number; h: number };
  mtime_utc: string;
  serve_url: string;
}

export interface EvidenceManifest {
  schema: string;
  generated_utc: string;
  scanned: number;
  kept: number;
  max_rows: number;
  max_per_dir?: number;
  is_a_selection: boolean;
  served_by: string;
  rows: EvidenceRow[];
  skipped: { path: string; reason: string }[];
}

export interface EvidenceState {
  manifest: EvidenceManifest | null;
  serving: boolean;
  reason: string | null;
  manifestUrl: string;
}

export const MANIFEST_PATH = "artifacts/diary_evidence/DIARY_EVIDENCE.json";
export const MANIFEST_KEY = "diary_evidence";
export const MANIFEST_URL = declaredReceiptUrl(MANIFEST_KEY);

export const BUILD_COMMAND = "the diary evidence build, which is not part of this build";

function why(status: number): string {
  if (status === 423)
    return "the service refuses these bytes: the path is under an active blinded experiment " +
           "(HTTP 423). Sealed bytes do not leave the service, and that is correct.";
  if (status === 403)
    return "the service refuses these bytes: the path is outside its declared artifact roots " +
           "(HTTP 403).";
  if (status === 404)
    return "the file in the manifest is no longer at that path (HTTP 404). The manifest is " +
           "stale; rebuild it with " + BUILD_COMMAND;
  return "the service answered HTTP " + status + " for the first row's bytes.";
}

export function thumbUrl(row: EvidenceRow): string {
  return row.serve_url || "/api/file?path=" + encodeURI(row.path);
}

export async function loadEvidence(): Promise<EvidenceState> {
  const base: EvidenceState = {
    manifest: null, serving: false, reason: null, manifestUrl: MANIFEST_URL,
  };
  let doc: EvidenceManifest;
  try {
    const r = await fetch(MANIFEST_URL, { credentials: "same-origin" });
    if (!r.ok) {
      return {
        ...base,
        reason: r.status === 404
          ? "no evidence manifest has been built on this machine. Build it with " + BUILD_COMMAND
          : why(r.status),
      };
    }
    const payload = (await r.json()) as DeclaredReceiptResponse<EvidenceManifest>;
    const row = declaredReceipt(payload, MANIFEST_KEY);
    if (!row?.present || !row.content) {
      return {
        ...base,
        reason: row?.missing_reason
          ? `${row.missing_reason}. Build it with ${BUILD_COMMAND}`
          : "no evidence manifest has been built on this machine. Build it with " + BUILD_COMMAND,
      };
    }
    doc = row.content;
  } catch (e) {
    return { ...base, reason: e instanceof Error ? e.message : String(e) };
  }

  const rows = Array.isArray(doc?.rows) ? doc.rows : [];
  const first = rows[0];
  if (!first) {
    return {
      ...base, manifest: doc,
      reason: "the manifest was read and lists no servable PNG. Its `skipped` list says why " +
              "each candidate was left out.",
    };
  }

  try {
    for (const row of rows) {
      const r = await fetch(thumbUrl(row), { credentials: "same-origin" });
      if (!r.ok) return { ...base, manifest: doc, reason: why(r.status) };
      const ct = r.headers.get("Content-Type") || "";
      if (!ct.startsWith("image/")) {
        return {
          ...base, manifest: doc,
          reason: "the route served " + (ct || "an unlabelled body") + " rather than an image, so " +
                  "these rows are shown as path and hash instead of as pictures.",
        };
      }
      await r.blob();
    }
    return { ...base, manifest: doc, serving: true, reason: null };
  } catch (e) {
    return {
      ...base, manifest: doc,
      reason: "no image endpoint reachable: " + (e instanceof Error ? e.message : String(e)),
    };
  }
}

export function shortHash(sha: string): string {
  return (sha || "").slice(0, 12);
}

export function kb(n: number): string {
  if (!n) return "0 kB";
  return n >= 1_000_000 ? (n / 1_000_000).toFixed(1) + " MB" : Math.round(n / 1000) + " kB";
}
