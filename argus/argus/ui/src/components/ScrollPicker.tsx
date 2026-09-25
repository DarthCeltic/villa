import { useEffect, useMemo, useState } from "react";
import {
  PREFLIGHT_ACTIONS,
  PreflightResultView,
  usePreflight,
  type PreflightActionId,
} from "./PreflightPanel";

export interface ScrollRow {
  scroll: string;
  display: string;
  physical_segments: number;
  label_representations: number;
  labelled: number;
  aligned: number;
  results_complete: number;
  results_partial: number;
  cached_ct_regions: number;
  blocked_by: string[];
}

export interface ScrollIndex {
  scrolls: ScrollRow[];
  totals: {
    physical_scrolls: number;
    physical_segments: number;
    label_representations: number;
    segments_with_labels: number;
  };
  nothing_moved?: string;
}

type Stage = { key: string; label: string; plain: string; state: "ready" | "partial" | "missing" };

function stagesFor(r: ScrollRow): Stage[] {
  const labels: Stage["state"] =
    r.labelled === 0 ? "missing" : r.labelled === r.physical_segments ? "ready" : "partial";
  const ct: Stage["state"] = r.cached_ct_regions > 0 ? "ready" : "missing";
  const results: Stage["state"] =
    r.results_complete === 0 && r.results_partial === 0
      ? "missing"
      : r.results_partial > 0
      ? "partial"
      : "ready";
  return [
    {
      key: "labels",
      label: "Human ink markings",
      plain:
        r.labelled === 0
          ? "nobody has marked ink on this scroll yet"
          : `${r.labelled} of ${r.physical_segments} pieces have markings`,
      state: labels,
    },
    {
      key: "ct",
      label: "Scan data on this machine",
      plain:
        r.cached_ct_regions > 0
          ? `${r.cached_ct_regions} region${r.cached_ct_regions === 1 ? "" : "s"} downloaded`
          : "not downloaded yet — would have to stream over the network",
      state: ct,
    },
    {
      key: "results",
      label: "Reading results",
      plain:
        r.results_complete === 0 && r.results_partial === 0
          ? "nothing has been read yet"
          : `${r.results_complete} complete · ${r.results_partial} partial`,
      state: results,
    },
  ];
}

const DOT: Record<Stage["state"], string> = {
  ready: "var(--ok)",
  partial: "var(--warn)",
  missing: "var(--dim)",
};

export function ScrollPicker({
  onRun,
}: {
  onRun?: (action: PreflightActionId, scrolls: string[]) => void;
}) {
  const pf = usePreflight();
  const [data, setData] = useState<ScrollIndex | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    let dead = false;
    fetch("/api/scrolls")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d) => !dead && setData(d))
      .catch((e) => !dead && setError(String(e)));
    return () => {
      dead = true;
    };
  }, []);

  const workable = useMemo(
    () => (data?.scrolls ?? []).filter((s) => s.scroll !== "UNATTRIBUTED"),
    [data],
  );
  const orphan = useMemo(
    () => (data?.scrolls ?? []).find((s) => s.scroll === "UNATTRIBUTED"),
    [data],
  );

  const toggle = (id: string) =>
    setPicked((p) => {
      const n = new Set(p);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });

  const chosen = [...picked];
  const n = chosen.length;

  if (error) return <div style={{ color: "var(--bad)" }}>Could not load scrolls: {error}</div>;
  if (!data) return <div style={{ color: "var(--dim)" }}>Reading what is on disk…</div>;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, minHeight: 0 }}>
      <div>
        <div style={{ fontSize: 15, fontWeight: 600 }}>Choose what to work on</div>
        <div style={{ color: "var(--dim)", fontSize: "var(--t-small)" }}>
          Tick one scroll, several, or all of them. Everything below applies to what you tick.
        </div>
        {
}
        <div style={{ color: "var(--dim)", fontSize: "var(--t-small)", marginTop: 4 }}>
          The dots describe what is on this machine — not whether anything read from it is
          right.
        </div>
      </div>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button onClick={() => setPicked(new Set(workable.map((s) => s.scroll)))}>
          Select all {workable.length}
        </button>
        <button onClick={() => setPicked(new Set())} disabled={n === 0}>
          Clear
        </button>
        <button
          onClick={() =>
            setPicked(new Set(workable.filter((s) => s.labelled > 0).map((s) => s.scroll)))
          }
        >
          Only scrolls with ink markings
        </button>
        <button
          onClick={() =>
            setPicked(new Set(workable.filter((s) => s.blocked_by.length === 0).map((s) => s.scroll)))
          }
        >
          Only scrolls with nothing missing
        </button>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 8, overflow: "auto", minHeight: 0 }}>
        {workable.map((s) => {
          const on = picked.has(s.scroll);
          const stages = stagesFor(s);
          const expanded = open === s.scroll;
          return (
            <div
              key={s.scroll}
              style={{
                border: `1px solid ${on ? "var(--acc)" : "var(--line)"}`,
                borderRadius: 8,
                padding: "10px 12px",
                background: on ? "var(--bg-raised-2)" : "transparent",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                {
}
                <label
                  aria-label={`Select ${s.display}`}
                  style={{ display: "inline-flex", alignItems: "center", justifyContent: "center",
                           minHeight: "var(--control-h)", minWidth: "var(--control-h)",
                           flex: "0 0 auto", cursor: "pointer" }}
                >
                  <input type="checkbox" checked={on} onChange={() => toggle(s.scroll)} />
                </label>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 600 }}>{s.display}</div>
                  <div style={{ fontSize: "var(--t-small)", color: "var(--dim)" }}>
                    {s.physical_segments} physical piece
                    {s.physical_segments === 1 ? "" : "s"} · {s.label_representations}{" "}
                    labelled representation{s.label_representations === 1 ? "" : "s"}
                  </div>
                </div>
                <div style={{ display: "flex", gap: 14 }}>
                  {stages.map((st) => (
                    <div key={st.key} style={{ textAlign: "center", minWidth: 96 }}>
                      <div
                        style={{
                          width: 8,
                          height: 8,
                          borderRadius: 4,
                          background: DOT[st.state],
                          margin: "0 auto 4px",
                        }}
                      />
                      <div style={{ fontSize: "var(--t-small)", color: "var(--dim)" }}>{st.label}</div>
                    </div>
                  ))}
                </div>
                <button onClick={() => setOpen(expanded ? null : s.scroll)}>
                  {expanded ? "Hide" : "Details"}
                </button>
              </div>

              {s.blocked_by.length > 0 && (
                <div style={{ marginTop: 8, fontSize: "var(--t-small)", color: "var(--warn)" }}>
                  {s.blocked_by.map((b, i) => (
                    <div key={i}>· {b}</div>
                  ))}
                </div>
              )}

              {expanded && (
                <div style={{ marginTop: 10, fontSize: "var(--t-small)", display: "flex", flexDirection: "column", gap: 6 }}>
                  {stages.map((st) => (
                    <div key={st.key} style={{ display: "flex", gap: 8 }}>
                      <span style={{ color: DOT[st.state], minWidth: 8 }}>●</span>
                      <span style={{ minWidth: 170 }}>{st.label}</span>
                      <span style={{ color: "var(--dim)" }}>{st.plain}</span>
                    </div>
                  ))}
                  <div style={{ color: "var(--dim)" }}>
                    {s.label_representations} labelled representation
                    {s.label_representations === 1 ? "" : "s"}; representations from different scans are never mixed.
                  </div>
                  <div style={{ color: "var(--dim)" }}>Identifier: {s.scroll}</div>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {orphan && orphan.cached_ct_regions > 0 && (
        <div style={{ fontSize: "var(--t-small)", color: "var(--dim)" }}>
          {orphan.cached_ct_regions} downloaded region
          {orphan.cached_ct_regions === 1 ? "" : "s"} could not be matched to a scroll yet. This is
          normal while a download is still running.
        </div>
      )}

      <div style={{ borderTop: "1px solid var(--line)", paddingTop: 10 }}>
        <div style={{ fontSize: "var(--t-small)", color: "var(--dim)", marginBottom: 6 }}>
          {n === 0
            ? "Nothing selected yet."
            : `These will run on ${n} scroll${n === 1 ? "" : "s"}: ${chosen.join(", ")}`}
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {
}
          {PREFLIGHT_ACTIONS.map((a) => (
            <button
              key={a.id}
              disabled={n === 0 || pf.busy !== null}
              title={a.what}
              onClick={() => (onRun ? onRun(a.id, chosen) : void pf.run(a.id, chosen))}
            >
              {pf.busy === a.id ? `${a.label}…` : a.label}
            </button>
          ))}
        </div>
        <div style={{ fontSize: "var(--t-small)", color: "var(--dim)", marginTop: 8 }}>
          These four buttons show you what would happen. None of them starts anything: the
          service they ask has no way to write. {data.nothing_moved ?? ""}
        </div>
        {onRun ? null : (
          <div style={{ marginTop: 10 }}>
            <PreflightResultView
              action={pf.ranAction}
              result={pf.result}
              error={pf.error}
              onClose={pf.clear}
            />
          </div>
        )}
      </div>
    </div>
  );
}
