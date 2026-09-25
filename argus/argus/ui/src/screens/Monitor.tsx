import { Link } from "react-router-dom";
import { ActivityPanel } from "../components/ActivityPanel";
import { IntegrityLine } from "../components/IntegrityBanner";
import { failureLine } from "../lib/http";
import { usePoll } from "../lib/poll";
import { useActivity, useIntegrity, certificationIsSuppressed } from "../lib/systemTruth";

type Mon = {
  utc: string;
  snapshot_age_s: number | null;
  snapshot_ttl_s: number;
  shared_refresh: boolean;
  n_runs: number;
  active: { id?: string; state?: string; stage?: string }[];
  attention: { id?: string; state?: string; reason?: string }[];
  resources: {
    gpu: { used_mib: number; total_mib: number; util_pct: number; busy: boolean } | null;
    cpu_pct: number | null;
    ram: { used_gib: number; total_gib: number; pct: number } | null;
    disks: Record<string, { free_gib: number; total_gib: number }>;
  };
  git: { head: string; branch: string; dirty_paths: number; in_sync: boolean } | null;
  campaign: {
    stage: string;
    lane?: string;
    state: "RUNNING" | "STALLED";
    state_basis: string;
    done: number;
    total: number | null;
    progress_pct: number | null;
    progress_note?: string;
    eta_seconds_range?: [number, number];
    note?: string;
  } | null;
  read_only: boolean;
  sealed_policy: string;
};

type Blockers = {
  items: { id: string; state: string; label: string; summary: string; receipt?: string }[];
};

const POLL_MS = 15000;
const STALE_S = 60;

export function Monitor() {
  const mon = usePoll<Mon>("/api/monitor", { intervalMs: POLL_MS, staleAfterS: STALE_S });
  const blkP = usePoll<Blockers>("/api/blockers", { intervalMs: POLL_MS });
  const act = useActivity(10000);
  const integrity = useIntegrity();
  const gate = certificationIsSuppressed(integrity.data);

  const d = mon.data;
  const blk = blkP.data;
  const err = mon.failure ? failureLine(mon.failure) : null;
  const ageS = mon.ageS;
  const stale = mon.stale === true;

  return (
    <div
      style={{
        height: "100dvh",
        minHeight: "100dvh",
        overflowY: "auto",
        overflowX: "hidden",
        boxSizing: "border-box",
        background: "var(--bg-sunken)",
        color: "var(--ink)",
        padding: "14px 14px 28px",
        display: "grid",
        gap: 12,
        alignContent: "start",
        fontSize: 15,
        lineHeight: 1.45,
      }}
    >
      <header style={{ display: "grid", gap: 2 }}>
        <div style={{ fontSize: "var(--t-small)", letterSpacing: ".12em", opacity: 0.6 }}>ARGUS</div>
        <h1 style={{ margin: 0, fontSize: 22 }}>Monitor</h1>
        <div style={{ fontSize: "var(--t-small)", opacity: 0.65 }}>
          read-only · {ageS === null ? "…" : `updated ${ageS}s ago`}
          {stale ? " · STALE" : ""}
        </div>
        {}
        <Link to="/jobs" data-control="monitor.back" style={{ fontSize: "var(--t-small)", color: "var(--accent)" }}>
          Back to ARGUS Jobs
        </Link>
      </header>

      {gate.suppressed ? (
        <Card tone="warn">
          <div style={{ fontSize: "var(--t-small)", opacity: 0.65 }}>SYSTEM INTEGRITY</div>
          <strong>Audit chain BROKEN</strong>
          <div style={{ fontSize: "var(--t-small)", opacity: 0.85 }}>{gate.why}</div>
        </Card>
      ) : null}

      {
}
      <Card tone={act.data?.verdict === "ACTIVE" ? undefined : "warn"}>
        <div style={{ fontSize: "var(--t-small)", opacity: 0.65 }}>ACTIVITY</div>
        <ActivityPanel a={act} compact />
      </Card>

      {err ? (
        <Card tone="warn">
          <strong>No answer from the service</strong>
          <div style={{ fontSize: "var(--t-small)", opacity: 0.8 }}>{err}</div>
          <div style={{ fontSize: "var(--t-small)", opacity: 0.8 }}>
            This is the monitor failing to read, not a statement about the campaign.
            {mon.consecutiveFailures > 1
              ? " " + mon.consecutiveFailures + " consecutive failed reads."
              : ""}
          </div>
        </Card>
      ) : null}

      {d ? (
        <>
          <Card tone={d.attention.length || (blk?.items.length ?? 0) ? "warn" : "ok"}>
            <div style={{ fontSize: "var(--t-small)", opacity: 0.65 }}>ATTENTION</div>
            {d.attention.length === 0 && !(blk?.items.length ?? 0) ? (
              <strong>Nothing needs you</strong>
            ) : d.attention.length === 0 ? (
              <>
                <strong>
                  {blk!.items.length} blocked or stopped
                </strong>
                <div style={{ fontSize: "var(--t-small)", opacity: 0.8 }}>
                  no ledger job needs you; see below
                </div>
              </>
            ) : (
              <div style={{ display: "grid", gap: 6 }}>
                {d.attention.map((a, i) => (
                  <div key={i}>
                    <strong>{a.state}</strong>{" "}
                    <span style={{ opacity: 0.8 }}>{a.id}</span>
                    {a.reason ? (
                      <div style={{ fontSize: "var(--t-small)", opacity: 0.8 }}>{a.reason}</div>
                    ) : null}
                  </div>
                ))}
              </div>
            )}
          </Card>

          {blk && blk.items.length ? (
            <Card tone="warn">
              <div style={{ fontSize: "var(--t-small)", opacity: 0.65 }}>BLOCKED / STOPPED</div>
              {blk.items.map((b) => (
                <div key={b.id} style={{ marginBottom: 6 }}>
                  <strong>
                    {b.id} — {b.state}
                  </strong>
                  <div style={{ fontSize: "var(--t-small)", opacity: 0.8 }}>{b.label}</div>
                  <div style={{ fontSize: "var(--t-small)", opacity: 0.7 }}>{b.summary}</div>
                </div>
              ))}
            </Card>
          ) : null}

          <Card tone={d.campaign?.state === "STALLED" ? "warn" : undefined}>
            <div style={{ fontSize: "var(--t-small)", opacity: 0.65 }}>CAMPAIGN</div>
            {!d.campaign ? (
              <>
                <strong>No stage declared</strong>
                <div style={{ fontSize: "var(--t-small)", opacity: 0.75 }}>
                  Nothing has written a campaign heartbeat, so no campaign stage is
                  declared. That is not the same as nothing running -- work can be in flight
                  with no campaign attached to it, and ACTIVITY above is what answers that.
                </div>
              </>
            ) : (
              <>
                <strong>{d.campaign.stage}</strong>
                <div style={{ fontSize: "var(--t-small)", opacity: 0.85 }}>
                  {d.campaign.state}
                  {d.campaign.lane ? ` · lane ${d.campaign.lane}` : ""} ·{" "}
                  {d.campaign.progress_pct !== null
                    ? `${d.campaign.done}/${d.campaign.total} (${d.campaign.progress_pct}%)`
                    : d.campaign.progress_note}
                </div>
                {d.campaign.eta_seconds_range ? (
                  <div style={{ fontSize: "var(--t-small)", opacity: 0.75 }}>
                    eta {fmtRange(d.campaign.eta_seconds_range)}
                  </div>
                ) : null}
                {d.campaign.note ? (
                  <div style={{ fontSize: "var(--t-small)", opacity: 0.7 }}>{d.campaign.note}</div>
                ) : null}
                <div style={{ fontSize: "var(--t-small)", opacity: 0.6 }}>{d.campaign.state_basis}</div>
              </>
            )}
          </Card>

          {
}
          <Card>
            <div style={{ fontSize: "var(--t-small)", opacity: 0.65 }}>LEDGER RUNS</div>
            {d.active.length === 0 ? (
              <>
                <strong>No run in this ledger</strong>
                <div style={{ fontSize: "var(--t-small)", opacity: 0.75 }}>
                  {d.n_runs} run{d.n_runs === 1 ? "" : "s"} on record and none of them
                  active. That is a statement about this run ledger only -- see ACTIVITY
                  above for whether the machine is doing anything.
                </div>
              </>
            ) : (
              <div style={{ display: "grid", gap: 8 }}>
                {d.active.map((a, i) => (
                  <div key={i}>
                    <strong>{a.stage ?? a.id}</strong>
                    <div style={{ fontSize: "var(--t-small)", opacity: 0.8 }}>
                      {a.state} · total unknown, so no percentage is shown
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Card>

          <Card>
            <div style={{ fontSize: "var(--t-small)", opacity: 0.65 }}>MACHINE</div>
            <Row
              k="GPU"
              v={
                d.resources.gpu
                  ? `${d.resources.gpu.used_mib} / ${d.resources.gpu.total_mib} MiB · ${d.resources.gpu.util_pct}%`
                  : "unavailable"
              }
            />
            <Row k="CPU" v={d.resources.cpu_pct === null ? "unavailable" : `${d.resources.cpu_pct}%`} />
            <Row
              k="RAM"
              v={
                d.resources.ram
                  ? `${d.resources.ram.used_gib} / ${d.resources.ram.total_gib} GiB`
                  : "unavailable"
              }
            />
            {Object.entries(d.resources.disks).map(([root, v]) => (
              <Row key={root} k={root} v={`${v.free_gib} GiB free`} />
            ))}
          </Card>

          <Card>
            <div style={{ fontSize: "var(--t-small)", opacity: 0.65 }}>REPOSITORY</div>
            {d.git ? (
              <>
                <Row k="commit" v={`${d.git.head} (${d.git.branch})`} />
                <Row k="origin" v={d.git.in_sync ? "in sync" : "not in sync"} />
                <Row k="uncommitted" v={`${d.git.dirty_paths} path${d.git.dirty_paths === 1 ? "" : "s"}`} />
              </>
            ) : (
              <span style={{ opacity: 0.75 }}>unavailable</span>
            )}
          </Card>

          <div style={{ fontSize: "var(--t-small)", opacity: 0.55, display: "grid", gap: 4 }}>
            <IntegrityLine integrity={integrity} />
            {blkP.failure ? (
              <span style={{ color: "var(--status-blocked)" }}>
                the blocker list could not be read ({blkP.failure.message}) -- it is missing
                from this screen, not empty
              </span>
            ) : null}
            <span>
              {d.sealed_policy} · one shared refresh (TTL {d.snapshot_ttl_s}s)
            </span>
          </div>
        </>
      ) : null}
    </div>
  );
}

function fmtRange([lo, hi]: [number, number]): string {
  const m = (s: number) => (s < 90 ? `${Math.round(s)}s` : `${Math.round(s / 60)}m`);
  return `${m(lo)}–${m(hi)}`;
}

function Card({ children, tone }: { children: React.ReactNode; tone?: "ok" | "warn" }) {
  const border =
    tone === "warn" ? "1px solid var(--status-blocked-edge)" : "1px solid var(--line)";
  return (
    <section
      style={{
        border,
        borderRadius: 12,
        padding: "12px 14px",
        display: "grid",
        gap: 6,
        background: "var(--bg)",
      }}
    >
      {children}
    </section>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, fontSize: "var(--t-small)" }}>
      <span style={{ opacity: 0.7 }}>{k}</span>
      <span style={{ textAlign: "right" }}>{v}</span>
    </div>
  );
}

export default Monitor;
