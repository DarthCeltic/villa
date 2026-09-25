import { useEffect, useState } from "react";
import { scrollOfTarget, type StackDetail } from "../../lib/unrollScroll";
import type { ScrollStatus } from "../../lib/scrollStatus";

export type SourceState<T> =
  | { state: "LOADING" }
  | { state: "OK"; data: T }
  | { state: "FAILED"; why: string };

async function getJson<T>(url: string, signal: AbortSignal): Promise<T> {
  const r = await fetch(url, { credentials: "same-origin", signal });
  if (!r.ok) throw new Error(`HTTP ${r.status} from ${url.split("?")[0]}`);
  return (await r.json()) as T;
}

function useSource<T>(url: string | null, refreshMs = 120000): SourceState<T> {
  const [s, setS] = useState<SourceState<T>>({ state: "LOADING" });
  useEffect(() => {
    if (!url) { setS({ state: "LOADING" }); return; }
    let alive = true;
    const ctl = new AbortController();
    const run = () => {
      getJson<T>(url, ctl.signal)
        .then((data) => { if (alive) setS({ state: "OK", data }); })
        .catch((e: unknown) => {
          if (!alive || (e instanceof DOMException && e.name === "AbortError")) return;
          setS({ state: "FAILED", why: e instanceof Error ? e.message : String(e) });
        });
    };
    setS({ state: "LOADING" });
    run();
    const t = window.setInterval(run, refreshMs);
    return () => { alive = false; ctl.abort(); window.clearInterval(t); };
  }, [url, refreshMs]);
  return s;
}


export interface Established {
  evidence?: { claim: string; value: string; receipt: string; caveat: string }[];
  what_is_actually_open?: string | string[];
}
export interface Blockers {
  items?: { id: string; state: string; label?: string; summary?: string;
            defects?: { n: number; name: string; severity: string }[] }[];
  gpu_policy?: string;
}
export interface GrailSnapshot {
  present?: boolean;
  overall_state?: string;
  counts?: { failures?: number; warnings?: number; findings?: number; with_ledger_entry?: number };
  obstacles?: { total?: number; closed_or_contained?: number; open_or_inherent?: number };
  age_s?: number;
  snapshot_is_stale?: boolean;
}
export interface FindingRow {
  id: string; n: number; title: string; kind: string; excerpt: string;
  ledger?: unknown; checkable?: unknown; retracts: string[]; amends: string[];
}
export interface FindingsResult {
  present: boolean; why?: string; relation?: string; relation_means?: string;
  counts_by_kind?: Record<string, number>; total_matching_kinds?: number; rows?: FindingRow[];
}
export interface CommunityResult {
  count: number;
  claims: { key: string; claim: string; reported_by: string; would_reproduce: string;
            never_used_for?: string; status?: string }[];
  status_of_every_claim?: string;
  these_are_not_findings?: string;
}
export interface Stack {
  key: string;
  name: string;
  scroll: string | null;
  detail: StackDetail;
}

interface ScrollWorkspacePayload { route: ScrollStatus }

export function useProjectSources() {
  return {
    established: useSource<Established>("/api/established"),
    blockers: useSource<Blockers>("/api/blockers"),
    grail: useSource<GrailSnapshot>("/api/grail"),
  };
}

export function useScrollStatus(scroll: string | null): SourceState<ScrollStatus> {
  const workspace = useSource<ScrollWorkspacePayload>(
    scroll ? `/api/scroll_workspace/${encodeURIComponent(scroll)}` : null,
    30000,
  );
  if (workspace.state === "OK") return { state: "OK", data: workspace.data.route };
  return workspace;
}

export function useStacks(): SourceState<Stack[]> {
  const [s, setS] = useState<SourceState<Stack[]>>({ state: "LOADING" });
  useEffect(() => {
    let alive = true;
    const ctl = new AbortController();
    (async () => {
      try {
        const idx = await getJson<{ targets: { key: string; name: string }[] }>("/api/unroll", ctl.signal);
        const rows = await Promise.all((idx.targets ?? []).map(async (t) => {
          const detail = await getJson<StackDetail>(
            `/api/unroll?target=${encodeURIComponent(t.key)}`, ctl.signal);
          return { key: t.key, name: t.name, scroll: scrollOfTarget(t), detail };
        }));
        if (alive) setS({ state: "OK", data: rows });
      } catch (e) {
        if (!alive || (e instanceof DOMException && e.name === "AbortError")) return;
        setS({ state: "FAILED", why: e instanceof Error ? e.message : String(e) });
      }
    })();
    return () => { alive = false; ctl.abort(); };
  }, []);
  return s;
}

export function useFindings(scroll: string | null, aliases: string[], q: string, kind: string | null) {
  const params = new URLSearchParams();
  if (scroll) params.set("scroll", scroll);
  if (aliases.length) params.set("aliases", aliases.join(","));
  if (q.trim()) params.set("q", q.trim());
  if (kind) params.set("kind", kind);
  params.set("limit", "40");
  return useSource<FindingsResult>(`/api/findings?${params.toString()}`, 300000);
}

export function useCommunity(scroll: string | null, aliases: string[]) {
  const params = new URLSearchParams();
  if (scroll) params.set("scroll", scroll);
  if (aliases.length) params.set("aliases", aliases.join(","));
  return useSource<CommunityResult>(`/api/community?${params.toString()}`, 600000);
}


export interface ScrollMetadataEvidence {
  authority?: string;
  source_authority?: string;
  operational_verification?: string;
  source?: string;
  method?: string;
}
export interface ScrollMetadataPayload {
  scroll_id: string;
  winding_count?: {
    count: number;
    derivation_method?: string;
    evidence?: ScrollMetadataEvidence;
  } | null;
  record_sha256: string;
  validation_problems: string[];
}

export type ScrollMetadataState =
  | { state: "LOADING" }
  | { state: "NO_RECORD" }
  | { state: "OK"; data: ScrollMetadataPayload }
  | { state: "FAILED"; why: string };

export function interpretScrollMetadataResponse(status: number, body: unknown): ScrollMetadataState {
  if (status === 404) return { state: "NO_RECORD" };
  if (status === 200 && body && typeof body === "object" && (body as { state?: unknown }).state === "NO_RECORD") {
    return { state: "NO_RECORD" };
  }
  if (status < 200 || status >= 300) {
    const detail = body && typeof body === "object" && "detail" in body
      ? String((body as { detail: unknown }).detail) : undefined;
    return { state: "FAILED", why: detail ?? `HTTP ${status} from /api/scroll_metadata` };
  }
  return { state: "OK", data: body as ScrollMetadataPayload };
}

export function useScrollMetadata(scrollId: string | null): ScrollMetadataState {
  const [s, setS] = useState<ScrollMetadataState>({ state: "LOADING" });
  useEffect(() => {
    if (!scrollId) { setS({ state: "LOADING" }); return; }
    let alive = true;
    const ctl = new AbortController();
    const run = () => {
      fetch(`/api/scroll_metadata/${encodeURIComponent(scrollId)}?absent_ok=true`,
        { credentials: "same-origin", signal: ctl.signal })
        .then(async (r) => {
          if (!alive) return;
          const body = await r.json().catch(() => null);
          setS(interpretScrollMetadataResponse(r.status, body));
        })
        .catch((e: unknown) => {
          if (!alive || (e instanceof DOMException && e.name === "AbortError")) return;
          setS({ state: "FAILED", why: e instanceof Error ? e.message : String(e) });
        });
    };
    setS({ state: "LOADING" });
    run();
    const t = window.setInterval(run, 300000);
    return () => { alive = false; ctl.abort(); window.clearInterval(t); };
  }, [scrollId]);
  return s;
}
