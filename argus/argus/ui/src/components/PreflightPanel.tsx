import { useCallback, useState } from "react";

export const PREFLIGHT_ACTIONS = [
  {
    id: "download",
    label: "Download the scan data",
    what: "Estimates what would transfer and checks there is room for it. Transfers nothing.",
  },
  {
    id: "read_for_ink",
    label: "Read for ink",
    what: "Lists which pieces could be scored and which are blocked, and states in advance what the result would be allowed to mean.",
  },
  {
    id: "review_lab",
    label: "Open in Review Lab",
    what: "Shows what a reviewer would see, and what is deliberately hidden from them.",
  },
  {
    id: "evidence",
    label: "Show the evidence",
    what: "Lists the receipt files and hashes a reader needs to check the chain themselves.",
  },
] as const;

export type PreflightActionId = (typeof PREFLIGHT_ACTIONS)[number]["id"];

interface Refusal {
  code: string;
  detail: string;
  [k: string]: unknown;
}

interface ReceiptRef {
  path: string;
  sha256: string;
}

interface BlockedSegment {
  scroll: string;
  segment: string;
  blocked_by?: string[];
}

interface PreflightResult {
  action?: string;
  error?: string;
  known?: string[];
  read_only_preflight?: boolean;
  scrolls?: string[];
  refusals?: Refusal[];
  may_proceed?: boolean;
  enqueue_via?: string;

  unique_acquisitions?: number;
  objects_required_unique?: number;
  objects_already_verified?: number;
  objects_still_to_fetch?: number;
  estimated_transfer_bytes?: number | null;
  estimated_transfer_note?: string;
  disk_free_bytes?: number;
  disk_sufficient?: boolean | null;

  segments_ready?: BlockedSegment[];
  segments_blocked?: BlockedSegment[];
  expected_result_class?: string;
  expected_result_meaning?: string;
  detector?: Record<string, unknown>;

  complete_outputs?: unknown[];
  partial_outputs?: unknown[];
  no_output?: unknown[];
  partial_policy?: string;

  receipts?: Record<string, ReceiptRef>;
  result_classification?: string;
}

function gib(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "not established";
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

function fileHref(path: string) {
  return `/api/file?path=${encodeURIComponent(path)}`;
}

const LABEL: React.CSSProperties = { color: "var(--ink)", fontWeight: 600 };

export function usePreflight() {
  const [busy, setBusy] = useState<PreflightActionId | null>(null);
  const [result, setResult] = useState<PreflightResult | null>(null);
  const [ranAction, setRanAction] = useState<PreflightActionId | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async (action: PreflightActionId, scrolls: string[]) => {
    setBusy(action);
    setError(null);
    try {
      const q = `action=${encodeURIComponent(action)}&scrolls=${encodeURIComponent(scrolls.join(","))}`;
      const r = await fetch(`/api/preflight?${q}`);
      if (!r.ok) throw new Error(`the service answered HTTP ${r.status}`);
      setResult((await r.json()) as PreflightResult);
      setRanAction(action);
    } catch (e) {
      setResult(null);
      setRanAction(action);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }, []);

  const clear = useCallback(() => {
    setResult(null);
    setRanAction(null);
    setError(null);
  }, []);

  return { busy, result, ranAction, error, run, clear };
}

export function PreflightResultView({
  action,
  result,
  error,
  onClose,
}: {
  action: PreflightActionId | null;
  result: PreflightResult | null;
  error: string | null;
  onClose: () => void;
}) {
  if (!action) return null;
  const meta = PREFLIGHT_ACTIONS.find((a) => a.id === action);

  return (
    <div
      style={{
        border: "1px solid var(--line)",
        borderRadius: 8,
        padding: "12px 14px",
        display: "grid",
        gap: 10,
        background: "var(--bg-raised)",
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <strong style={{ fontSize: "var(--t-body)" }}>{meta?.label ?? action}</strong>
        <span style={{ fontSize: "var(--t-small)", color: "var(--dim)" }}>
          preview only — nothing was started
        </span>
        <div style={{ flex: 1 }} />
        <button onClick={onClose}>Close</button>
      </div>

      {error ? (
        <div style={{ color: "var(--bad)", fontSize: "var(--t-small)" }}>
          The preview could not be computed: {error}. This says nothing about whether the
          action would succeed — it was never asked.
        </div>
      ) : null}

      {result?.error ? (
        <div style={{ color: "var(--bad)", fontSize: "var(--t-small)" }}>
          {result.error}
          {result.known ? ` — known actions: ${result.known.join(", ")}` : ""}
        </div>
      ) : null}

      {result && !result.error ? (
        <>
          <div style={{ fontSize: "var(--t-small)" }}>
            <span style={{ ...LABEL, color: result.may_proceed ? "var(--ok)" : "var(--warn)" }}>
              {result.may_proceed
                ? "Nothing blocks this."
                : "This would be refused as things stand."}
            </span>{" "}
            <span style={{ color: "var(--dim)" }}>
              {result.scrolls?.length
                ? `Computed over ${result.scrolls.length} scroll${result.scrolls.length === 1 ? "" : "s"}: ${result.scrolls.join(", ")}.`
                : "No scroll was selected."}
            </span>
          </div>

          {(result.refusals ?? []).length > 0 ? (
            <div style={{ display: "grid", gap: 4 }}>
              {(result.refusals ?? []).map((r) => (
                <div key={r.code} style={{ fontSize: "var(--t-meta)", color: "var(--warn)" }}>
                  <span style={{ fontFamily: "var(--mono)", fontSize: "var(--t-meta)" }}>{r.code}</span> —{" "}
                  {r.detail}
                </div>
              ))}
            </div>
          ) : null}

          {action === "download" ? (
            <dl style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "3px 12px", margin: 0, fontSize: "var(--t-small)" }}>
              <dt style={LABEL}>still to fetch</dt>
              <dd style={{ margin: 0, color: "var(--dim)" }}>
                {result.objects_still_to_fetch ?? "not established"} of{" "}
                {result.objects_required_unique ?? "?"} objects across{" "}
                {result.unique_acquisitions ?? "?"} acquisition
                {result.unique_acquisitions === 1 ? "" : "s"}
              </dd>
              <dt style={LABEL}>estimated transfer</dt>
              <dd style={{ margin: 0, color: "var(--dim)" }}>
                {gib(result.estimated_transfer_bytes)}
                {result.estimated_transfer_note ? ` — ${result.estimated_transfer_note}` : ""}
              </dd>
              <dt style={LABEL}>room on disk</dt>
              <dd style={{ margin: 0, color: "var(--dim)" }}>
                {gib(result.disk_free_bytes)} free —{" "}
                {result.disk_sufficient === null || result.disk_sufficient === undefined
                  ? "whether that is enough is not established, because nothing has been fetched for these acquisitions yet"
                  : result.disk_sufficient
                    ? "enough, with the safety margin"
                    : "NOT enough with the safety margin"}
              </dd>
            </dl>
          ) : null}

          {action === "read_for_ink" ? (
            <div style={{ display: "grid", gap: 6, fontSize: "var(--t-small)" }}>
              <div style={{ color: "var(--dim)" }}>
                <b style={LABEL}>{result.segments_ready?.length ?? 0}</b> piece
                {(result.segments_ready?.length ?? 0) === 1 ? "" : "s"} could be scored;{" "}
                <b style={LABEL}>{result.segments_blocked?.length ?? 0}</b> blocked.
              </div>
              {(result.segments_blocked ?? []).slice(0, 6).map((s) => (
                <div key={`${s.scroll}/${s.segment}`} style={{ color: "var(--warn)", fontSize: "var(--t-small)" }}>
                  {s.scroll} {s.segment} — {(s.blocked_by ?? []).join("; ")}
                </div>
              ))}
              {(result.segments_blocked?.length ?? 0) > 6 ? (
                <div style={{ color: "var(--dim)", fontSize: "var(--t-small)" }}>
                  {(result.segments_blocked?.length ?? 0) - 6} further blocked pieces are not
                  listed here.
                </div>
              ) : null}
              {result.expected_result_meaning ? (
                <div style={{ color: "var(--warn)" }}>
                  <b style={LABEL}>{result.expected_result_class ?? "result class not stated"}</b>{" "}
                  — {result.expected_result_meaning}
                </div>
              ) : null}
            </div>
          ) : null}

          {action === "review_lab" ? (
            <div style={{ display: "grid", gap: 6, fontSize: "var(--t-small)", color: "var(--dim)" }}>
              <div>
                <b style={LABEL}>{result.complete_outputs?.length ?? 0}</b> complete ·{" "}
                <b style={LABEL}>{result.partial_outputs?.length ?? 0}</b> partial ·{" "}
                <b style={LABEL}>{result.no_output?.length ?? 0}</b> with nothing read yet.
              </div>
              {result.partial_policy ? <div>{result.partial_policy}</div> : null}
            </div>
          ) : null}

          {action === "evidence" ? (
            <div style={{ display: "grid", gap: 6, fontSize: "var(--t-small)" }}>
              {result.result_classification ? (
                <div style={{ color: "var(--warn)" }}>
                  Anything read from these scrolls is classified{" "}
                  <b style={LABEL}>{result.result_classification}</b>.
                </div>
              ) : null}
              {Object.entries(result.receipts ?? {}).length === 0 ? (
                <div style={{ color: "var(--warn)" }}>
                  None of the declared receipt files is on disk, so the chain cannot be checked
                  from here.
                </div>
              ) : (
                Object.entries(result.receipts ?? {}).map(([name, r]) => (
                  <div key={name} style={{ display: "grid", gap: 1 }}>
                    <a
                      href={fileHref(r.path)}
                      target="_blank"
                      rel="noreferrer"
                      style={{ fontFamily: "var(--mono)", fontSize: "var(--t-meta)", color: "var(--accent)" }}
                    >
                      {name}
                    </a>
                    <span style={{ fontFamily: "var(--mono)", fontSize: "var(--t-meta)", color: "var(--ink-faint)" }}>
                      sha256 {r.sha256.slice(0, 16)}…
                    </span>
                  </div>
                ))
              )}
            </div>
          ) : null}

          {result.enqueue_via ? (
            <div style={{ fontSize: "var(--t-small)", color: "var(--ink-faint)" }}>{result.enqueue_via}</div>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
