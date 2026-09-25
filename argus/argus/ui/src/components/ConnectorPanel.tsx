import { failureLine } from "../lib/http";
import { usePoll } from "../lib/poll";
import { OpsBadge, OpsFact, OpsFacts, OpsSection, OpsUnknown } from "./OpsKit";

type ConnectorStatus = {
  source_ready: boolean;
  runtime_ready: boolean;
  stack_discovery: boolean;
  tools: string[];
  permissions: { read: boolean; plan: boolean; execute: boolean };
  target_detail: "allowed" | "withheld";
  client_attachment: { observable: boolean; state: string; why: string; proof: string };
  boundary: string;
};

export function ConnectorPanel() {
  const q = usePoll<ConnectorStatus>("/api/connect", { intervalMs: 30_000 });
  const d = q.data;
  const ready = Boolean(d?.source_ready && d?.runtime_ready && d?.stack_discovery);
  return (
    <OpsSection
      control="system.connector"
      title="AI connector (MCP)"
      tone={d && !ready ? "warn" : undefined}
      hint="The connector follows the verified live stack instead of assuming development ports. Client attachment is checked in the assistant because MCP runs there, not inside this service."
      aside={d ? <OpsBadge tone={ready ? "ok" : "warn"}>{ready ? "ready to attach" : "setup incomplete"}</OpsBadge> : null}
    >
      {!d ? (
        <ul className="ops-list">
          <OpsUnknown what="MCP connector" why={q.failure ? `/api/connect did not answer: ${failureLine(q.failure)}` : "/api/connect has not answered yet."} />
        </ul>
      ) : (
        <OpsFacts>
          <OpsFact label="Local pieces">
            <span className="ops-badges">
              <OpsBadge tone={d.source_ready ? "ok" : "bad"}>connector {d.source_ready ? "shipped" : "absent"}</OpsBadge>
              <OpsBadge tone={d.runtime_ready ? "ok" : "warn"}>runtime {d.runtime_ready ? "ready" : "missing"}</OpsBadge>
              <OpsBadge tone={d.stack_discovery ? "ok" : "bad"}>live-stack discovery {d.stack_discovery ? "ready" : "absent"}</OpsBadge>
            </span>
          </OpsFact>
          <OpsFact label="Can this assistant use it?">
            Check this assistant's tools for <code>argus_overview</code>. ARGUS cannot claim an
            attachment it cannot observe: {d.client_attachment.why}.
          </OpsFact>
          <OpsFact label="Authority">
            Read: {d.permissions.read ? "yes" : "no"} · dry-run plans: {d.permissions.plan ? "yes" : "no"} · execute: {d.permissions.execute ? "yes" : "no"}.
            <div className="ops-note">{d.boundary}</div>
          </OpsFact>
          <OpsFact label="Privacy">
            Per-target intelligence is {d.target_detail}. The connector exposes {d.tools.length}{" "}
            named tools and never returns the command token or private notes.
          </OpsFact>
        </OpsFacts>
      )}
    </OpsSection>
  );
}
