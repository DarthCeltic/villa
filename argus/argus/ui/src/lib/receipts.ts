
export interface ReceiptEnvelope<T = unknown> {
  key: string;
  relpath: string;
  root?: string;
  present: boolean;
  bytes?: number | null;
  mtime_utc?: string | null;
  sha256_16?: string | null;
  produced_by?: string;
  produced_by_scripts?: string[];
  sealed?: boolean;
  withheld_reason?: string | null;
  missing_reason?: string | null;
  content_redactions?: number;
  content_paths_relativized?: number;
  content: T | null;
}

export interface ReceiptIndex {
  schema: string;
  generated_at: number;
  index_age_s: number;
  ttl_s?: number;
  receipts: Record<string, ReceiptEnvelope>;
}

export type ReceiptFailure = { status: number | null; detail: string };

export interface ReceiptsState {
  index: ReceiptIndex | null;
  failure: ReceiptFailure | null;
  settled: boolean;
}

export const RECEIPTS_ROUTE = "/api/receipts";

export async function fetchReceipts(): Promise<ReceiptsState> {
  try {
    const r = await fetch(RECEIPTS_ROUTE);
    if (!r.ok) {
      let detail = r.statusText || "the service refused the request";
      try {
        const body = await r.json();
        detail = body.detail ?? body.error ?? detail;
      } catch {
      }
      return { index: null, failure: { status: r.status, detail }, settled: true };
    }
    return { index: (await r.json()) as ReceiptIndex, failure: null, settled: true };
  } catch (e) {
    return {
      index: null,
      failure: { status: null, detail: String(e) },
      settled: true,
    };
  }
}

export function receipt<T>(
  index: ReceiptIndex | null,
  key: string,
): ReceiptEnvelope<T> | null {
  return (index?.receipts?.[key] as ReceiptEnvelope<T> | undefined) ?? null;
}

export function content<T>(index: ReceiptIndex | null, key: string): T | null {
  const r = receipt<T>(index, key);
  return r && r.present && !r.sealed ? (r.content ?? null) : null;
}
