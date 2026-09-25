import { failureLine } from "../lib/http";
import { usePoll } from "../lib/poll";
import { OpsBadge, OpsSection, OpsUnknown } from "./OpsKit";

type StackRow = {
  ports: Record<string, number>;
  ui_port: string | null;
  build_sha: string | null;
  idle_minutes: number;
  pinned: boolean;
  memory_gib: number | null;
  this_stack: boolean;
};

type InstancesPayload = {
  stacks: StackRow[];
  count: number;
  ceiling: number;
  reserve_gib: number;
  idle_retire_minutes: number;
  commit_free_gib: number | null;
};

export function StacksPanel() {
  const q = usePoll<InstancesPayload>("/api/instances", { intervalMs: 30_000 });
  const d = q.data;
  const tight = d?.commit_free_gib != null && d.commit_free_gib < d.reserve_gib + 2;
  return (
    <OpsSection
      control="system.stacks"
      title="ARGUS stacks on this machine"
      tone={tight ? "warn" : undefined}
      hint={
        d
          ? `Any number of agents may use ARGUS; a new stack must leave ${d.reserve_gib} GiB of memory free, and a stack idle for ${Math.round(d.idle_retire_minutes)} minutes is retired to make room unless it is pinned.`
          : "How many ARGUS stacks run here, and the memory they share."
      }
      aside={
        d ? (
          <OpsBadge tone={tight ? "warn" : "ok"} control="system.stacks.headroom">
            {d.commit_free_gib != null ? `${d.commit_free_gib} GiB memory free` : "memory headroom unknown"}
          </OpsBadge>
        ) : null
      }
    >
      {!d ? (
        <ul className="ops-list">
          <OpsUnknown what="Running stacks" why={q.failure ? `/api/instances did not answer: ${failureLine(q.failure)}` : "/api/instances has not answered yet."} />
        </ul>
      ) : d.count === 0 ? (
        <p className="meta" data-control="system.stacks.none">
          No stack has registered yet. Stacks started before this version register when they are next restarted.
        </p>
      ) : (
        <ul className="ops-list" data-control="system.stacks.list">
          {d.stacks.map((s, i) => (
            <li key={i} className="ops-item" data-control={`system.stacks.${s.ports.observe ?? i}`}>
              <div className="ops-item-head">
                <strong>
                  {s.this_stack ? "This stack" : "Stack"} · observe {s.ports.observe ?? "—"}
                  {s.ui_port ? ` · UI ${s.ui_port}` : ""}
                </strong>
                <span className="ops-badges">
                  {s.pinned ? <OpsBadge tone="info">pinned</OpsBadge> : null}
                  <OpsBadge tone={s.idle_minutes >= d.idle_retire_minutes && !s.pinned ? "warn" : "ok"}>
                    {s.idle_minutes < 1 ? "in use" : `idle ${Math.round(s.idle_minutes)} min`}
                  </OpsBadge>
                </span>
              </div>
              <div className="ops-item-body meta">
                build {s.build_sha ? s.build_sha.slice(0, 12) : "unrecorded"} ·{" "}
                {s.memory_gib != null ? `holds ${s.memory_gib} GiB` : "memory not measured"}
              </div>
            </li>
          ))}
        </ul>
      )}
      <p className="meta" style={{ color: "var(--ink-dim)" }}>
        Stop one from a terminal: <code>python -m argus.serve stop-stack &lt;port&gt;</code>
      </p>
    </OpsSection>
  );
}
