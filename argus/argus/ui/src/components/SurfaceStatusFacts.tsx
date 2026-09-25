import { useEffect, useState } from "react";
import { Chip, type Tone } from "./Status";
import { useArgusMode } from "../lib/context";

export interface SurfaceStatusFact {
  field: string;
  value: string;
  tier: string;
  evidence_path: string;
  source_commit: string;
  timestamp: string;
  note?: string;
  superseded: SurfaceStatusFact[];
}

export interface SurfaceStatus {
  scroll: string;
  as_of: string | null;
  fields: Record<string, SurfaceStatusFact>;
}

type SurfaceStatusIndex = { scrolls: SurfaceStatus[] };

let statusIndexPromise: Promise<Map<string, SurfaceStatus>> | null = null;

function readStatusIndex(): Promise<Map<string, SurfaceStatus>> {
  if (!statusIndexPromise) {
    statusIndexPromise = fetch("/api/surface_status", { credentials: "same-origin" })
      .then((r) => (r.ok ? (r.json() as Promise<SurfaceStatusIndex>) : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((body) => new Map((body.scrolls ?? []).map((row) => [row.scroll, row])))
      .catch((error) => {
        statusIndexPromise = null;
        throw error;
      });
  }
  return statusIndexPromise;
}

const TIER_WORD: Record<string, string> = {
  integration_receipt: "integration receipt",
  historical_receipt: "historical receipt",
};

export function factTone(value: string): Tone {
  const v = value.toUpperCase();
  if (/REFUS|FAIL|MISSING/.test(v)) return "refused";
  if (/NOT_|^NO_|PENDING|INELIGIBLE|UNKNOWN|BLOCKED|UNCERTAIN|UNRESOLVED/.test(v)) return "blocked";
  return "active";
}

function humanize(field: string): string {
  return field.replace(/_/g, " ");
}

function fmtAge(iso: string | undefined): string {
  if (!iso) return "unknown time";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const days = Math.floor((Date.now() - t) / 86400000);
  if (days <= 0) return "today";
  if (days === 1) return "1 day ago";
  return `${days} days ago`;
}

export function useSurfaceStatus(scroll: string | null | undefined): {
  status: SurfaceStatus | null;
  loading: boolean;
} {
  const [status, setStatus] = useState<SurfaceStatus | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!scroll) {
      setStatus(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    readStatusIndex()
      .then((index) => {
        if (!cancelled) setStatus(index.get(scroll) ?? null);
      })
      .catch(() => {
        if (!cancelled) setStatus(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [scroll]);

  return { status, loading };
}

export function SurfaceStatusChips({
  status,
  size = "sm",
}: {
  status: SurfaceStatus | null;
  size?: "sm" | "md";
}) {
  if (!status) return null;
  const fields = Object.values(status.fields);
  if (!fields.length) return null;
  return (
    <span style={{ display: "inline-flex", gap: 6, flexWrap: "wrap" }}>
      {fields.map((f) => (
        <span key={f.field} title={`${humanize(f.field)} — ${TIER_WORD[f.tier] ?? "receipt"}, ${fmtAge(f.timestamp)}. ${f.note ?? ""}`}>
          <Chip tone={factTone(f.value)} size={size}>
            {humanize(f.value)}
          </Chip>
        </span>
      ))}
    </span>
  );
}

export function SurfaceStatusDetail({ status }: { status: SurfaceStatus | null }) {
  const guided = useArgusMode() === "guided";
  if (!status) {
    return <p className="small faint">No surface-status record has been recorded for this scroll.</p>;
  }
  const fields = Object.entries(status.fields);
  return (
    <div style={{ display: "grid", gap: 10 }}>
      {status.as_of ? (
        <p className="meta faint">as of {status.as_of}</p>
      ) : null}
      {fields.map(([key, f]) => (
        <div key={key} style={{ display: "grid", gap: 2 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <span className="meta" style={{ textTransform: "uppercase", letterSpacing: ".05em" }}>
              {humanize(f.field)}
            </span>
            <Chip tone={factTone(f.value)} size="sm" literal={/\d/.test(f.value)}>
              {/\d/.test(f.value) ? f.value : humanize(f.value)}
            </Chip>
            {f.tier !== "integration_receipt" ? (
              <span className="small faint">({TIER_WORD[f.tier] ?? "receipt"})</span>
            ) : null}
          </div>
          {guided ? (
            <details className="ssf-source" data-control={`surface-status.${key}.source`}>
              <summary className="small faint">Where this comes from · {fmtAge(f.timestamp)}</summary>
              {f.note ? <p className="small faint" style={{ margin: 0 }}>{f.note}</p> : null}
              <p className="meta faint" style={{ margin: 0 }}>evidence: {f.evidence_path}</p>
            </details>
          ) : (
            <>
              {f.note ? <p className="small faint" style={{ margin: 0 }}>{f.note}</p> : null}
              <p className="meta faint" style={{ margin: 0 }}>
                evidence: {f.evidence_path} · {fmtAge(f.timestamp)}
              </p>
            </>
          )}
          {f.superseded.length ? (
            <p className="meta faint" style={{ margin: 0 }}>
              supersedes {f.superseded.length} earlier value
              {f.superseded.length === 1 ? "" : "s"}: {f.superseded.map((s) => humanize(s.value)).join(", ")}
            </p>
          ) : null}
        </div>
      ))}
    </div>
  );
}
