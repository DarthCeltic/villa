import { ACTIVITY_LABEL, type Activity, type ActivityClass, type ActivityEvidence } from "../lib/systemTruth";
import type { Polled } from "../lib/poll";
import { failureLine } from "../lib/http";
import { Chip, type Tone } from "./Status";

const STATE_TONE: Record<string, Tone> = {
  ACTIVE: "active",
  UNKNOWN: "blocked",
  NONE_OBSERVED: "blocked",
};

const STATE_WORD: Record<string, string> = {
  ACTIVE: "active",
  UNKNOWN: "unknown",
  NONE_OBSERVED: "none observed",
};

function verdictTone(v: string | undefined): Tone {
  if (v === "ACTIVE") return "active";
  if (v === "IDLE_OBSERVED") return "certified";
  return "blocked";
}

export function ActivityHeadline({ a }: { a: Polled<Activity> }) {
  if (a.loading && !a.data) {
    return <span className="small faint">reading every activity root…</span>;
  }
  if (!a.data) {
    return (
      <span className="small" style={{ color: "var(--status-blocked)" }}>
        Activity unknown — the activity derivation could not be read
        {a.failure ? `: ${failureLine(a.failure)}` : "."} Nothing is being inferred from
        that silence; in particular it is not idleness.
      </span>
    );
  }
  return (
    <span style={{ display: "inline-flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
      <Chip tone={verdictTone(a.data.verdict)}>{a.data.verdict.replace(/_/g, " ")}</Chip>
      <strong>{a.data.headline}</strong>
    </span>
  );
}

export function ActivityPanel({
  a,
  compact = false,
}: {
  a: Polled<Activity>;
  compact?: boolean;
}) {
  const d = a.data;
  return (
    <div style={{ display: "grid", gap: 10 }}>
      <div style={{ display: "flex", gap: 12, alignItems: "baseline", flexWrap: "wrap" }}>
        <ActivityHeadline a={a} />
        <div style={{ flex: 1, minWidth: 8 }} />
        <span className="meta faint">
          {d ? `observed ${d.observed_utc}` : "not observed"} ·{" "}
          {a.ageS === null ? "never read" : `read ${a.ageS}s ago`}
          {a.stale ? " · STALE" : ""}
        </span>
      </div>

      {a.failure && d ? (
        <div className="small" style={{ color: "var(--status-blocked)" }}>
          The last refresh failed ({failureLine(a.failure)}). What follows is the previous
          reading, not the current state.
        </div>
      ) : null}

      {d ? (
        <>
          <div style={{ display: "grid", gap: 1, background: "var(--line)" }}>
            {Object.entries(d.classes).map(([key, c]) => (
              <ClassRow key={key} name={key} c={c} compact={compact} />
            ))}
          </div>

          {d.unreadable_roots.length ? (
            <div
              className="small"
              style={{ color: "var(--status-blocked)", display: "grid", gap: 3 }}
            >
              <strong>
                {d.unreadable_roots.length} declared root
                {d.unreadable_roots.length === 1 ? "" : "s"} could not be read
              </strong>
              {d.unreadable_roots.map((r) => (
                <span key={r.root}>
                  <span className="mono">{r.root}</span> — {r.why}. Work recorded under it is
                  unaccounted for, which is why this board does not say idle.
                </span>
              ))}
            </div>
          ) : null}

          {d.feed_coverage ? (
            <div className="meta faint">
              {d.feed_coverage.note} (run feed: {d.feed_coverage.observatory_feed_roots} root
              {d.feed_coverage.observatory_feed_roots === 1 ? "" : "s"}; this derivation:{" "}
              {d.feed_coverage.activity_roots})
            </div>
          ) : null}
          <div className="meta faint">{d.rule}</div>
        </>
      ) : null}
    </div>
  );
}

function ClassRow({ name, c, compact }: { name: string; c: ActivityClass; compact: boolean }) {
  const label = ACTIVITY_LABEL[name] ?? name.replace(/_/g, " ");
  return (
    <div
      style={{
        background: "var(--bg-raised)",
        padding: compact ? "8px 10px" : "10px 14px",
        display: "grid",
        gap: 5,
      }}
    >
      <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
        <span style={{ fontWeight: 600, fontSize: "var(--t-small)" }}>{label}</span>
        <Chip tone={STATE_TONE[c.state] ?? "blocked"} size="sm">
          {STATE_WORD[c.state] ?? c.state}
        </Chip>
      </div>
      {c.evidence.slice(0, compact ? 1 : 3).map((e, i) => (
        <EvidenceLine key={i} e={e} />
      ))}
      {c.unknown_because.length ? (
        <div className="small" style={{ color: "var(--status-blocked)" }}>
          {c.unknown_because.slice(0, 2).join(" · ")}
        </div>
      ) : null}
      {!compact ? <div className="small faint">{c.means}</div> : null}
    </div>
  );
}

function EvidenceLine({ e }: { e: ActivityEvidence }) {
  const bits: string[] = [];
  if (e.segment) bits.push(e.segment);
  if (typeof e.blocks_done === "number" && typeof e.blocks_total === "number") {
    bits.push(`${e.blocks_done}/${e.blocks_total} blocks`);
  }
  if (typeof e.percent === "number") bits.push(`${e.percent}% (worker-reported)`);
  if (e.current_orientation) bits.push(e.current_orientation);
  if (typeof e.elapsed_min === "number") bits.push(`${Math.round(e.elapsed_min)} min elapsed`);
  if (typeof e.eta_min === "number") bits.push(`worker ETA ${Math.round(e.eta_min)} min`);
  if (typeof e.util_pct === "number") {
    bits.push(`${e.used_mib}/${e.total_mib} MiB · ${e.util_pct}%`);
  }
  if (typeof e.cpu_pct === "number") bits.push(`${e.cpu_pct}%`);
  if (typeof e.running === "number") {
    bits.push(`${e.running} job record${e.running === 1 ? "" : "s"} still being touched`);
    if (typeof e.newest_running_age_s === "number") {
      bits.push(`newest touched ${Math.round(e.newest_running_age_s / 60)} min ago`);
    }
    if (e.abandoned_running_records) {
      bits.push(`${e.abandoned_running_records} abandoned RUNNING record(s)`);
    }
  }
  if (typeof e.blocks_seen === "number") bits.push(`${e.blocks_seen} blocks seen`);
  if (typeof e.age_s === "number") bits.push(`receipt ${Math.round(e.age_s)}s old`);

  return (
    <div className="small" style={{ display: "grid", gap: 2, color: "var(--ink-faint)" }}>
      <span>
        <span className="faint">{e.witness}</span>
        {e.what ? ` — ${e.what}` : ""}
        {bits.length ? ` — ${bits.join(" · ")}` : ""}
      </span>
      {e.note ? (
        <span style={{ color: "var(--status-blocked)" }}>{e.note}</span>
      ) : null}
      {e.receipt ? (
        <span className="faint">
          {e.root} · <span className="mono">{e.receipt}</span>
        </span>
      ) : null}
      {e.observed_by ? <span className="faint">source: {e.observed_by}</span> : null}
    </div>
  );
}
