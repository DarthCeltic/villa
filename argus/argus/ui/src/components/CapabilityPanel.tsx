import { Fragment, useEffect, useState } from "react";
import { Chip, type Tone } from "./Status";

interface EvidenceRow {
  receipt: string;
  path: string;
  present: boolean;
  sha256: string | null;
  bytes: number | null;
  written_utc: string | null;
  error?: string;
}

interface CapState {
  key: string;
  name?: string;
  does?: string;
  implementation?: string | null;
  availability: "INSTALLED" | "NOT_INSTALLED" | "DEGRADED";
  operational_verification: "CONTROL_PASSED" | "TESTED" | "UNTESTED" | "FAILED";
  scientific_admissibility: string;
  scientific?: boolean;
  verification_label?: string | null;
  version_or_hash?: string | null;
  license?: string | null;
  source?: string | null;
  last_successful_test_utc?: string | null;
  human_status?: string;
  aggregate?: string;
  aggregate_rule?: string;
  detail?: string | null;
  evidence?: EvidenceRow[];
  evidence_present?: number;
  evidence_declared?: number;
}

interface RouteEdge {
  stage: string;
  capability: string;
  producible_locally: boolean;
  artifact_already_held: boolean;
  scientifically_admissible: boolean;
  green: boolean;
  why: string | null;
}

interface Graph {
  capabilities: CapState[];
  counts: Record<string, number>;
  route: {
    edges: RouteEdge[];
    first_blocked_stage: string | null;
    all_green: boolean;
    rule: string;
  };
  headline_rule: string;
  evidence_rule?: string;
}

const AVAIL_TONE: Record<string, Tone> = {
  INSTALLED: "active",
  DEGRADED: "blocked",
  NOT_INSTALLED: "refused",
};
const VERIF_TONE: Record<string, Tone> = {
  CONTROL_PASSED: "active",
  TESTED: "active",
  UNTESTED: "blocked",
  FAILED: "refused",
};
const SCI_TONE: Record<string, Tone> = {
  ADMISSIBLE: "certified",
  RESEARCH_ONLY: "active",
  PLUMBING_ONLY: "blocked",
  TRAINING_PROMOTION_GATED: "blocked",
  UNQUALIFIED: "refused",
  NOT_APPLICABLE: "blocked",
};

function pretty(s: string) {
  return s.replace(/_/g, " ").toLowerCase();
}

const CELL: React.CSSProperties = {
  padding: "7px 10px 7px 0",
  borderBottom: "1px solid var(--line)",
  verticalAlign: "top",
};

function fileHref(path: string) {
  return `/api/file?path=${encodeURIComponent(path)}`;
}

function Evidence({ rows }: { rows: EvidenceRow[] }) {
  if (rows.length === 0) {
    return (
      <div style={{ color: "var(--warn)" }}>
        No receipt is declared for this capability, so nothing on this row can be checked
        from here.
      </div>
    );
  }
  return (
    <div style={{ display: "grid", gap: 6 }}>
      {rows.map((e) => (
        <div key={e.receipt} style={{ display: "grid", gap: 2 }}>
          {e.present ? (
            <a
              href={fileHref(e.path)}
              target="_blank"
              rel="noreferrer"
              style={{ fontFamily: "var(--mono)", fontSize: "var(--t-meta)", color: "var(--accent)" }}
            >
              {e.receipt}
            </a>
          ) : (
            <span style={{ fontFamily: "var(--mono)", fontSize: "var(--t-meta)", color: "var(--warn)" }}>
              {e.receipt} — declared but not on disk
            </span>
          )}
          <span style={{ fontFamily: "var(--mono)", fontSize: "var(--t-meta)", color: "var(--ink-faint)" }}>
            {e.present
              ? `sha256 ${(e.sha256 ?? "").slice(0, 16)}… · ${e.bytes ?? "?"} bytes · evidence written ${e.written_utc ?? "unknown"}`
              : "this absence is why the row above is not stronger"}
          </span>
        </div>
      ))}
    </div>
  );
}

export function CapabilityPanel() {
  const [g, setG] = useState<Graph | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    fetch("/api/capability_graph")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d) => live && setG(d as Graph))
      .catch((e) => live && setErr(String(e.message ?? e)));
    return () => {
      live = false;
    };
  }, []);

  if (err) return <p className="small" style={{ color: "var(--bad)" }}>{err}</p>;
  if (!g) return <p className="small muted">Reading capability receipts…</p>;

  const sci = g.counts.scientifically_admissible ?? 0;
  const sciTotal = g.counts.scientific_capabilities ?? 0;

  const inadmissible = g.route.edges.filter((e) => !e.scientifically_admissible);
  const held = g.route.edges.filter((e) => e.artifact_already_held && !e.producible_locally);

  return (
    <section className="panel" style={{ padding: 18, display: "grid", gap: 12 }}>
      <header style={{ display: "flex", gap: 12, alignItems: "baseline", flexWrap: "wrap" }}>
        <h2 style={{ margin: 0, fontSize: "var(--t-h2)" }}>Capabilities</h2>
        <span className="small muted">three independent axes, derived from receipts</span>
      </header>

      <p style={{ margin: 0, fontSize: "var(--t-small)", color: "var(--ink-dim)" }}>
        <b style={{ color: "var(--ink)" }}>{g.counts.installed}</b> of {g.counts.total} installed
        {" · "}
        <b style={{ color: "var(--ink)" }}>{g.counts.control_passed}</b> control-passed
        {" · "}
        <b style={{ color: sci === 0 ? "var(--warn)" : "var(--ink)" }}>{sci}</b> of {sciTotal}{" "}
        scientifically admissible
      </p>

      <p style={{ margin: 0, fontSize: "var(--t-small)", color: "var(--ink-faint)" }}>
        Open a row to see the receipt files behind it. Every claim on this panel is a link to
        the bytes that justify it.
      </p>

      <div style={{ overflowX: "auto" }}>
        <table style={{ borderCollapse: "collapse", width: "100%", fontSize: "var(--t-small)" }}>
          <thead>
            <tr style={{ textAlign: "left", color: "var(--ink-dim)" }}>
              {["Capability", "Available", "Verified", "Admissible", "Evidence"].map((h) => (
                <th
                  key={h}
                  style={{
                    padding: "6px 10px 6px 0",
                    borderBottom: "1px solid var(--line)",
                    fontFamily: "var(--mono)",
                    fontSize: "var(--t-meta)",
                    letterSpacing: ".08em",
                    textTransform: "uppercase",
                    fontWeight: 600,
                  }}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {g.capabilities.map((c) => {
              const isOpen = open === c.key;
              const ev = c.evidence ?? [];
              const have = c.evidence_present ?? ev.filter((e) => e.present).length;
              const want = c.evidence_declared ?? ev.length;
              return (
                <Fragment key={c.key}>
                  {

}
                  <tr
                    role="button"
                    tabIndex={0}
                    aria-expanded={isOpen}
                    aria-label={"Evidence for " + (c.name ?? pretty(c.key))}
                    onClick={() => setOpen(isOpen ? null : c.key)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        setOpen(isOpen ? null : c.key);
                      }
                    }}
                    style={{
                      cursor: "pointer",
                      background: isOpen ? "var(--bg-raised-2)" : undefined,
                    }}
                  >
                    <td style={CELL}>
                      <div style={{ color: "var(--ink)" }}>{c.name ?? pretty(c.key)}</div>
                      {c.human_status ? (
                        <div style={{ fontSize: "var(--t-small)", color: "var(--ink-dim)" }}>
                          {c.human_status}
                        </div>
                      ) : null}
                    </td>
                    <td style={CELL}>
                      <Chip tone={AVAIL_TONE[c.availability] ?? "blocked"} size="sm">
                        {pretty(c.availability)}
                      </Chip>
                    </td>
                    <td style={CELL}>
                      <Chip tone={VERIF_TONE[c.operational_verification] ?? "blocked"} size="sm">
                        {pretty(c.operational_verification)}
                      </Chip>
                    </td>
                    <td style={CELL}>
                      <Chip tone={SCI_TONE[c.scientific_admissibility] ?? "blocked"} size="sm">
                        {pretty(c.scientific_admissibility)}
                      </Chip>
                    </td>
                    <td style={CELL}>
                      {
}
                      <span
                        style={{
                          fontFamily: "var(--mono)",
                          fontSize: "var(--t-meta)",
                          color: want === 0 || have < want ? "var(--warn)" : "var(--ink-dim)",
                        }}
                      >
                        {want === 0 ? "none declared" : `${have} of ${want}`}
                      </span>
                    </td>
                  </tr>
                  {isOpen && (
                    <tr>
                      <td colSpan={5} style={{ padding: "4px 0 14px", borderBottom: "1px solid var(--line)" }}>
                        <div style={{ display: "grid", gap: 8, fontSize: "var(--t-small)", color: "var(--ink-dim)" }}>
                          {c.does && <div>{c.does}</div>}
                          {c.detail && <div>{c.detail}</div>}

                          {c.aggregate && (
                            <div>
                              <b style={{ color: "var(--ink)" }}>summary</b> {pretty(c.aggregate)}
                              {c.aggregate_rule ? (
                                <span style={{ color: "var(--ink-faint)" }}>
                                  {" — because "}
                                  {c.aggregate_rule}
                                </span>
                              ) : null}
                            </div>
                          )}

                          <div>
                            <b style={{ color: "var(--ink)" }}>control</b>{" "}
                            {c.verification_label ?? "no named control has been recorded"}
                          </div>

                          {}
                          <div>
                            <b style={{ color: "var(--ink)" }}>last successful test</b>{" "}
                            {c.last_successful_test_utc ? (
                              <span style={{ fontFamily: "var(--mono)", fontSize: "var(--t-meta)" }}>
                                {c.last_successful_test_utc}
                              </span>
                            ) : (
                              <span style={{ color: "var(--warn)" }}>
                                not measured — no successful test is recorded for this
                                capability
                              </span>
                            )}
                          </div>

                          {c.implementation && (
                            <div>
                              <b style={{ color: "var(--ink)" }}>implementation</b>{" "}
                              {c.implementation}
                            </div>
                          )}
                          <div>
                            <b style={{ color: "var(--ink)" }}>licence</b>{" "}
                            {c.license ?? "not declared on this row"}
                          </div>
                          {c.version_or_hash && (
                            <div style={{ fontFamily: "var(--mono)", fontSize: "var(--t-meta)", color: "var(--ink-faint)" }}>
                              {c.version_or_hash.slice(0, 32)}
                            </div>
                          )}

                          <div style={{ display: "grid", gap: 4 }}>
                            <b style={{ color: "var(--ink)" }}>evidence</b>
                            <Evidence rows={ev} />
                          </div>

                          <div style={{ fontFamily: "var(--mono)", fontSize: "var(--t-meta)", color: "var(--ink-faint)" }}>
                            identifier: {c.key}
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      <div style={{ display: "grid", gap: 6 }}>
        <h3 className="eyebrow" style={{ margin: 0 }}>Route</h3>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {g.route.edges.map((e) => (
            <span
              key={e.stage}
              title={
                e.why ??
                (e.producible_locally
                  ? "this machine can produce this stage"
                  : "green only because the artifact is already held; nothing here produces it")
              }
              style={{
                padding: "5px 10px",
                borderRadius: "var(--radius-sm)",
                border: "1px solid " + (e.green ? "var(--line-strong)" : "var(--bad)"),
                background: e.green ? "var(--bg-raised-2)" : "transparent",
                fontSize: "var(--t-small)",
                color: e.green ? "var(--ink)" : "var(--bad)",
                display: "inline-flex",
                gap: 6,
                alignItems: "center",
              }}
            >
              {e.stage}
              {
}
              <span style={{ fontFamily: "var(--mono)", fontSize: "var(--t-meta)", color: "var(--ink-faint)" }}>
                {e.producible_locally ? "produced here" : e.artifact_already_held ? "held" : "—"}
              </span>
              <span
                style={{
                  fontFamily: "var(--mono)",
                  fontSize: "var(--t-meta)",
                  color: e.scientifically_admissible ? "var(--ok)" : "var(--warn)",
                }}
              >
                {e.scientifically_admissible ? "adm" : "not adm"}
              </span>
            </span>
          ))}
        </div>
        <p style={{ margin: 0, fontSize: "var(--t-small)", color: "var(--ink-faint)" }}>
          {g.route.first_blocked_stage
            ? `Route blocked at ${g.route.first_blocked_stage}.`
            : "Every stage is walkable — as artifacts held or produced locally."}{" "}
          Admissibility is shown beside each stage and never folded into it.{" "}
          {inadmissible.length > 0
            ? `${inadmissible.length} of ${g.route.edges.length} stages are not scientifically admissible (${inadmissible
                .map((e) => e.stage)
                .join(", ")}), so a walkable route is still not evidence.`
            : "No stage on this route is currently marked inadmissible."}
          {held.length > 0
            ? ` ${held.length} stage${held.length === 1 ? " is" : "s are"} green only because the artifact is held (${held
                .map((e) => e.stage)
                .join(", ")}); this machine does not produce ${held.length === 1 ? "it" : "them"}.`
            : ""}
        </p>
      </div>
    </section>
  );
}
