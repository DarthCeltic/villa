import type { Certified, Operational, Progress } from "../api";
import { CERT_STATES, type CertState } from "../lib/certification";

export type Tone = "certified" | "blocked" | "refused" | "active";

const GLYPH: Record<Tone, string> = {
  certified: "✓",
  blocked: "!",
  refused: "✕",
  active: "◆",
};

export function operationalTone(s: Operational, sealed = false): Tone {
  if (sealed) return "active";
  switch (s) {
    case "COMPLETE":
      return "certified";
    case "RUNNING":
    case "PENDING":
      return "active";
    case "REFUSED":
      return "refused";
    case "STALLED":
    case "UNKNOWN":
    default:
      return "blocked";
  }
}

export const CERT_LABEL: Record<string, string> = {
  CERTIFIED_SURFACE: "Surface certified",
  CERTIFIED_2D: "2D certified",
  CERTIFIED_INK_CANDIDATE: "Ink candidate",
};

export const CERT_SHORT: Record<string, string> = {
  CERTIFIED_SURFACE: "Surface",
  CERTIFIED_2D: "2D",
  CERTIFIED_INK_CANDIDATE: "Ink candidate",
};

export function Chip({
  tone,
  children,
  title,
  size = "md",
  literal = false,
}: {
  tone: Tone;
  children: React.ReactNode;
  title?: string;
  size?: "sm" | "md";
  literal?: boolean;
}) {
  return (
    <span
      title={title}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding: size === "sm" ? "2px 8px" : "3px 10px",
        borderRadius: 999,
        background: `var(--status-${tone}-dim)`,
        border: `1px solid var(--status-${tone}-edge)`,
        color: `var(--status-${tone})`,
        fontSize: "var(--t-small)",
        lineHeight: 1.5,
        letterSpacing: literal ? 0 : "0.05em",
        textTransform: literal ? "none" : "uppercase",
        fontWeight: 600,
        whiteSpace: "normal",
        maxWidth: "100%",
        minWidth: 0,
        overflowWrap: "break-word",
        wordBreak: "normal",
      }}
    >
      <span aria-hidden style={{ fontSize: "var(--t-meta)", opacity: 0.95, flex: "0 0 auto" }}>
        {GLYPH[tone]}
      </span>
      {children}
    </span>
  );
}

export function CertLadder({
  highest,
  compact = false,
}: {
  highest: Certified;
  compact?: boolean;
}) {
  const rungs: Exclude<Certified, null>[] = [
    "CERTIFIED_SURFACE",
    "CERTIFIED_2D",
    "CERTIFIED_INK_CANDIDATE",
  ];
  const reached = highest ? rungs.indexOf(highest) : -1;
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center", minWidth: 0, flexWrap: "wrap" }}>
      <div style={{ display: "flex", gap: 3, flex: "0 0 auto" }} aria-hidden>
        {rungs.map((r, i) => (
          <span
            key={r}
            title={`${CERT_LABEL[r]} — ${i <= reached ? "reached" : "not reached"}`}
            style={{
              height: 5,
              width: compact ? 20 : 28,
              borderRadius: 3,
              background:
                i <= reached ? "var(--status-certified)" : "var(--line-strong)",
            }}
          />
        ))}
      </div>
      <span
        className="small"
        style={{
          color: highest ? "var(--ink-dim)" : "var(--ink-faint)",
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        {highest ? CERT_LABEL[highest] : "nothing certified"}
      </span>
    </div>
  );
}

export function ProgressReadout({
  p,
  emphasis = false,
}: {
  p: Progress;
  emphasis?: boolean;
}) {
  if (!p.present) {
    return (
      <div
        className="small"
        style={{
          color: "var(--ink-faint)",
          display: "flex",
          alignItems: "center",
          gap: 8,
          flexWrap: "wrap",
        }}
      >
        <Chip tone="blocked" size="sm">
          Progress not instrumented
        </Chip>
        <span>this worker publishes no heartbeat, so progress is unknown rather than
          estimated</span>
      </div>
    );
  }
  const stale = p.stale === true;
  return (
    <div style={{ display: "grid", gap: 6 }}>
      {p.fraction !== null ? (
        <div
          role="progressbar"
          aria-valuenow={p.done ?? undefined}
          aria-valuemax={p.total ?? undefined}
          aria-label={`${p.done} of ${p.total} ${p.unit ?? ""}`}
          style={{
            height: emphasis ? 8 : 6,
            background: "var(--bg-sunken)",
            border: "1px solid var(--line)",
            borderRadius: 99,
            overflow: "hidden",
          }}
        >
          <div
            style={{
              height: "100%",
              width: `${Math.round(p.fraction * 100)}%`,
              background: stale ? "var(--status-blocked)" : "var(--status-active)",
              transition: "width 240ms ease",
            }}
          />
        </div>
      ) : null}
      <div
        style={{
          display: "flex",
          gap: 10,
          flexWrap: "wrap",
          alignItems: "baseline",
          fontSize: emphasis ? "var(--t-body)" : "var(--t-small)",
          color: stale ? "var(--status-blocked)" : "var(--ink-dim)",
        }}
      >
        <span>
          <strong className="mono" style={{ fontSize: "inherit" }}>
            {p.done ?? "?"}
            {p.total !== null ? ` / ${p.total}` : ""}
          </strong>{" "}
          {p.total === null ? "done, total unknown" : (p.unit ?? "")}
        </span>
        {p.age_s !== null ? (
          <span className="meta">heartbeat {Math.round(p.age_s)}s ago</span>
        ) : null}
        {stale ? <Chip tone="blocked" size="sm">Stalled</Chip> : null}
      </div>
      {p.total === null && p.why ? (
        <div className="small faint">{p.why}</div>
      ) : null}
    </div>
  );
}

export function SealBadge({ marker }: { marker: string | null }) {
  return (
    <Chip
      tone="active"
      title={
        marker
          ? `sealed by ${marker} — scores, verdicts and previews are withheld by the service, not merely hidden here`
          : undefined
      }
    >
      Sealed
    </Chip>
  );
}


export function TruthChip({
  state,
  detail,
  size = "md",
}: {
  state: CertState;
  detail?: string;
  size?: "sm" | "md";
}) {
  const d = CERT_STATES[state];
  return (
    <span
      style={{ display: "inline-flex", flexWrap: "wrap", alignItems: "center", gap: "2px 6px", minWidth: 0, maxWidth: "100%" }}
      title={`${d.axis} axis — ${d.means}${detail ? ` (${detail})` : ""}`}
    >
      <Chip tone={d.tone} size={size}>
        {d.word}
      </Chip>
      <span
        className="meta"
        style={{
          letterSpacing: ".1em",
          textTransform: "uppercase",
          color: "var(--ink-faint)",
          whiteSpace: "nowrap",
        }}
      >
        {d.axis}
      </span>
    </span>
  );
}

export function AxisLegend({ compact = false }: { compact?: boolean }) {
  const rows: CertState[] = compact
    ? ["SCIENTIFIC_ADMISSIBLE", "OPERATIONAL_CONTROL_PASSED", "ARTIFACT_SAVED", "NOT_RUN"]
    : [
        "SCIENTIFIC_ADMISSIBLE",
        "SCIENTIFIC_QUALIFIED",
        "OPERATIONAL_CONTROL_PASSED",
        "EVIDENCE_PACKAGE_COMPLETE",
        "ARTIFACT_SAVED",
        "RUN_REFUSED",
        "NOT_RUN",
        "UNAVAILABLE",
        "STALE",
        "FIXTURE_ONLY",
      ];
  return (
    <div style={{ display: "grid", gap: 6 }}>
      <div className="small faint">
        Three axes, never merged: an artifact being saved, a mechanical control passing, and
        a scientific result being admissible are different claims. Only the last is green.
      </div>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        {rows.map((s) => (
          <TruthChip key={s} state={s} size="sm" />
        ))}
      </div>
    </div>
  );
}
