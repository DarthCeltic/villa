import { useEffect, useState } from "react";
import { Activity, GitCommitHorizontal, HelpCircle } from "lucide-react";
import { Chip, type Tone } from "./Status";
import { isKnown, unknownReason, type Triple } from "../lib/models";
import type { ReceiptsState } from "../lib/receipts";

export interface Health {
  ok: boolean;
  read_only: boolean;
  roots_present?: Record<string, boolean>;
  scan?: {
    last_seconds?: number | null;
    runs?: number | null;
    listings_read?: number | null;
    listings_reused?: number | null;
    snapshot_age_s?: number | null;
    snapshot_ttl_s?: number | null;
    ws_poll_s?: number | null;
    refresh_s?: number | null;
    ttl_exceeds_poll?: boolean;
  };
  process_cpu_seconds?: number | null;
  pid?: number | null;
}

export function useHealth(): { health: Health | null; error: string | null } {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    const tick = () =>
      fetch("/api/health")
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.statusText))))
        .then((d) => live && setHealth(d as Health))
        .catch((e) => live && setError(String(e)));
    tick();
    const t = setInterval(tick, 10000);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, []);
  return { health, error };
}

export function ServiceHealth({
  health,
  error,
}: {
  health: Health | null;
  error: string | null;
}) {
  return (
    <section className="panel" style={{ padding: 18, display: "grid", gap: 12 }}>
      <h2 className="eyebrow">
        <Activity size={13} aria-hidden /> Service
      </h2>
      {!health ? (
        <span className="small" style={{ color: "var(--status-refused)" }}>
          {error
            ? `/api/health is not answering — ${error}. Everything else on this screen was read through the same service, so treat it as historical.`
            : "asking the service…"}
        </span>
      ) : (
        <>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <Chip tone={health.ok ? "active" : "refused"} size="sm">
              {health.ok ? "answering" : "not healthy"}
            </Chip>
            <Chip tone={health.read_only ? "certified" : "refused"} size="sm">
              {health.read_only ? "read-only" : "WRITES POSSIBLE"}
            </Chip>
            {Object.entries(health.roots_present ?? {}).map(([root, present]) => (
              <Chip key={root} tone={present ? "active" : "blocked"} size="sm">
                evidence root {present ? "present" : "MISSING"}
              </Chip>
            ))}
          </div>
          {

}
          <div className="meta" style={{ color: "var(--ink-dim)", marginTop: 8, lineHeight: 1.5 }}>
            <strong>Two authorities.</strong> This service is observational: every write verb is
            refused by middleware, which is why nothing you do here can disturb a running or
            sealed experiment. Writing lives on a <em>separate</em> governed transport, reachable
            only from <code>System → Governed actions</code>, limited to a short explicit list of
            actions, and only after a session is opened deliberately. Each one produces an
            audited receipt. Neither service can promote a scientific verdict.
          </div>


          {
}
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <Chip
              tone={health.scan?.ttl_exceeds_poll ? "certified" : "refused"}
              size="sm"
              title="the snapshot cache must outlive the poll interval or it can never be hit"
            >
              snapshot cache {health.scan?.ttl_exceeds_poll ? "outlives" : "EXPIRES BEFORE"}{" "}
              the poll
            </Chip>
            <span className="meta">
              ttl {health.scan?.snapshot_ttl_s}s vs poll {health.scan?.ws_poll_s}s · refresh{" "}
              {health.scan?.refresh_s}s
            </span>
          </div>

          <dl
            style={{
              margin: 0,
              display: "grid",
              gap: 8,
              gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 190px), 1fr))",
            }}
          >
            <Stat
              k="last scan"
              v={
                health.scan?.last_seconds != null
                  ? `${health.scan.last_seconds}s over ${health.scan.runs ?? "?"} runs`
                  : null
              }
            />
            <Stat
              k="directory listings"
              v={
                health.scan?.listings_read != null
                  ? `${health.scan.listings_read} read, ${health.scan.listings_reused ?? 0} reused`
                  : null
              }
            />
            <Stat
              k="snapshot age"
              v={
                health.scan?.snapshot_age_s != null ? `${health.scan.snapshot_age_s}s` : null
              }
            />
            <Stat
              k="process CPU"
              v={
                health.process_cpu_seconds != null
                  ? `${health.process_cpu_seconds}s`
                  : null
              }
            />
          </dl>
        </>
      )}
    </section>
  );
}

function Stat({ k, v }: { k: string; v: string | null }) {
  return (
    <div style={{ display: "grid", gap: 2 }}>
      <dt className="eyebrow">{k}</dt>
      <dd
        className="small"
        style={{ margin: 0, color: v ? "var(--ink)" : "var(--ink-faint)" }}
      >
        {v ?? "not reported by the service"}
      </dd>
    </div>
  );
}

interface Lock {
  upstream?: { name?: string; commit?: string; committed_utc?: string; subject?: string; path?: string };
  working_tree?: {
    head_matches_pin?: boolean;
    dirty_source_count?: number;
    dirty_generated_count?: number;
    is_the_pinned_code?: boolean;
    files_checked_out?: number;
    components_present?: string[];
    measured_utc?: string;
    what_each_count_means?: Record<string, string>;
  };
  runtime?: {
    status?: string;
    python?: string;
    torch?: { version?: string; cuda_available?: boolean; usable_gpu?: boolean; device?: string };
    entry_points_running?: number;
    entry_points_total?: number;
    verified_utc?: string;
  };
  cache?: { chunk_cache?: string; policy?: string };
  cpp_gate?: { package?: string; status?: string; gates?: string[]; does_not_gate?: string[] };
}

export function UpstreamPin({ rec }: { rec: ReceiptsState }) {
  const env = rec.index?.receipts?.upstream_lock;
  const lock = (env?.present && !env.sealed ? (env.content as Lock) : null) ?? null;

  return (
    <section className="panel" style={{ padding: 18, display: "grid", gap: 12 }}>
      <h2 className="eyebrow">
        <GitCommitHorizontal size={13} aria-hidden /> Upstream pin
      </h2>
      {!lock ? (
        <span className="small" style={{ color: "var(--status-blocked)" }}>
          {!rec.settled
            ? "asking the service…"
            : env
              ? `the pin receipt is not readable — ${env.withheld_reason ?? env.missing_reason ?? "no reason given"} (${env.relpath})`
              : "the service is not serving the pin receipt, so which upstream code this machine runs is unknown here"}
        </span>
      ) : (
        <>
          <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
            <Chip
              tone={lock.working_tree?.is_the_pinned_code ? "certified" : "refused"}
              size="sm"
            >
              {lock.working_tree?.is_the_pinned_code
                ? "this is the pinned code"
                : "the tree is NOT the pinned code"}
            </Chip>
            <code className="mono">{(lock.upstream?.commit ?? "").slice(0, 12)}</code>
            <span className="small muted">{lock.upstream?.subject}</span>
          </div>

          {
}
          <div style={{ display: "flex", gap: 14, flexWrap: "wrap" }}>
            <span className="small">
              <Chip
                tone={lock.working_tree?.dirty_source_count ? "refused" : "certified"}
                size="sm"
              >
                {lock.working_tree?.dirty_source_count ?? "?"} source files modified
              </Chip>{" "}
              <span className="muted">must be zero</span>
            </span>
            <span className="small">
              <Chip tone="active" size="sm">
                {lock.working_tree?.dirty_generated_count ?? "?"} generated files differ
              </Chip>{" "}
              <span className="muted">install side-effects; expected</span>
            </span>
          </div>

          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <Chip
              tone={lock.runtime?.torch?.usable_gpu ? "certified" : "blocked"}
              size="sm"
            >
              {lock.runtime?.torch?.usable_gpu ? "GPU usable" : "no usable GPU"}
            </Chip>
            <span className="meta">
              python {lock.runtime?.python} · torch {lock.runtime?.torch?.version}
              {lock.runtime?.torch?.device ? ` · ${lock.runtime.torch.device}` : ""} ·{" "}
              {lock.runtime?.entry_points_running}/{lock.runtime?.entry_points_total} entry
              points
            </span>
          </div>

          {lock.cpp_gate ? (
            <div style={{ display: "grid", gap: 4 }}>
              <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                <Chip
                  tone={
                    (lock.cpp_gate.status ?? "").startsWith("NOT BUILT") ? "blocked" : "certified"
                  }
                  size="sm"
                >
                  {lock.cpp_gate.package}: {lock.cpp_gate.status}
                </Chip>
              </div>
              {
}
              <span className="small faint">
                gates {(lock.cpp_gate.gates ?? []).join(", ") || "nothing"} · does not gate{" "}
                {(lock.cpp_gate.does_not_gate ?? []).join(", ") || "nothing"}
              </span>
            </div>
          ) : null}

          <span className="meta">
            measured {lock.working_tree?.measured_utc} · runtime verified{" "}
            {lock.runtime?.verified_utc}
          </span>
        </>
      )}
    </section>
  );
}

export function NotMeasuredHere({ chunkCache }: { chunkCache: Triple<{ known_shape?: { total_gib: number }; status?: string }> | null }) {
  const rows: { what: string; why: string }[] = [
    {
      what: "WSL and Docker virtual disks",
      why: "virtual disk files can be large and no route reaches them. The disk panel above reports the volume's free space, which moves when they grow — but not what is inside them, so a full disk cannot be attributed here.",
    },
    {
      what: "GPU memory in use right now",
      why: "oversight reads total VRAM from the vendor tool at the moment it is asked. Nothing samples it over time, so this screen cannot say whether a run is about to run out.",
    },
    {
      what: "network reachability of upstream data",
      why: "no route here performs a probe. The Sources room does that deliberately and on request, because an interface that silently reaches the internet is one that can leak a sealed experiment.",
    },
  ];
  return (
    <section className="panel" style={{ padding: 18, display: "grid", gap: 10 }}>
      <h2 className="eyebrow">
        <HelpCircle size={13} aria-hidden /> Not measured here
      </h2>
      {chunkCache ? (
        <div className="small">
          <Chip tone="active" size="sm">
            chunk cache
          </Chip>{" "}
          <span className="muted">
            {isKnown(chunkCache)
              ? `${chunkCache.value.known_shape?.total_gib ?? "an unmeasured number of"} GiB, not walked on this screen. ${chunkCache.value.status ?? ""}`
              : `not established — ${unknownReason(chunkCache)}`}
          </span>
        </div>
      ) : null}
      {rows.map((r) => (
        <div key={r.what} style={{ display: "grid", gap: 3 }}>
          <span className="small">{r.what}</span>
          <span className="small faint">{r.why}</span>
        </div>
      ))}
    </section>
  );
}

export type { Tone };
