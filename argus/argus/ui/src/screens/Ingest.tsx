import { useEffect, useState } from "react";
import "../theme/ops.css";
import {
  OpsBadge,
  OpsDetails,
  OpsDisabled,
  OpsFact,
  OpsFacts,
  OpsItem,
  OpsSource,
  OpsUnknown,
} from "../components/OpsKit";

interface Action {
  name: string;
  mutating: boolean;
  describe: string;
  args: string[];
}

interface Payload {
  actions: Action[];
  n_actions: number;
  n_mutating: number;
  runners: string[];
  implemented: boolean;
  previous_ui_claim?: string;
  control_plane: Record<string, unknown>;
}

export function Ingest() {
  const [d, setD] = useState<Payload | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    fetch("/api/ingest")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((j) => live && setD(j as Payload))
      .catch((e) => live && setErr(String(e)));
    return () => {
      live = false;
    };
  }, []);

  if (err) {
    return (
      <ul className="ops-list">
        <OpsUnknown
          what="The stage action allowlist"
          why={`the read-only service did not answer on this origin (${err}). Nothing is missing from the system; this panel simply has nothing to read.`}
          next={
            <>
              start it and reload.{" "}
              <OpsDetails
                control="sources.ingest.lock.startcmd"
                summary="Developer details: startup command"
              >
                <span className="ops-mono">
                  python -m uvicorn argus.service.app:app --host 127.0.0.1 --port 8787
                </span>
              </OpsDetails>
            </>
          }
        />
      </ul>
    );
  }
  if (!d) return <div className="ops-note">Reading the action allowlist…</div>;

  const reading = d.actions.filter((a) => !a.mutating);
  const writing = d.actions.filter((a) => a.mutating);

  return (
    <>
      <OpsFacts>
        <OpsFact label="Stage surface">
          {d.n_actions} vetted action{d.n_actions === 1 ? "" : "s"}, {d.n_mutating} of them
          mutating, from <span className="ops-mono">argus.core.jobs</span>
        </OpsFact>
        <OpsFact label="Runners">
          {d.runners.length ? d.runners.join(", ") : "none registered"}
        </OpsFact>
        <OpsFact label="Where mutations actually live">
          <span className="ops-mono">
            {String(d.control_plane.mutations_live_in ?? "not declared")}
          </span>
          <div className="ops-note">
            gated by{" "}
            <span className="ops-mono">
              {String(d.control_plane.enabled_env ?? "an undeclared switch")}
            </span>
            . {String(d.control_plane.why_separate ?? "")}
          </div>
        </OpsFact>
      </OpsFacts>

      <OpsItem
        control="sources.ingest.lock"
        tone="ok"
        title="This list is read-only"
        state="scoped claim"
      >
        <div className="ops-item-body">
          This panel only reads the list of allowed actions, and lists them without invoking
          it. It is <strong>not</strong> a statement that this system as a whole cannot mutate —
          it once said exactly that while a proxy was quietly attaching a bearer token to every
          request a page made.
        </div>
      </OpsItem>

      <OpsDetails
        control="sources.ingest.reading"
        summary={
          <>
            <strong>Inspection actions</strong> — {reading.length}, none of which change
            anything
          </>
        }
      >
        <ActionList items={reading} control="sources.ingest.reading" />
      </OpsDetails>

      <OpsDetails
        control="sources.ingest.writing"
        summary={
          <>
            <strong>Staging and control actions</strong> — {writing.length}, every one of which
            mutates state
          </>
        }
      >
        <ActionList items={writing} control="sources.ingest.writing" />
      </OpsDetails>

      <OpsDisabled
        control="sources.ingest.invoke"
        label="Invoke an action"
        reason="Absent by design. These run in a separate application that has to be started deliberately, and this panel reports the allowlist rather than reaching it. There is no route by which an action name here becomes a command: the dispatcher is a table lookup with typed arguments, and it accepts no command string, script path or module to import."
      />

      <OpsSource route="/api/ingest" field="actions[], n_actions, n_mutating, runners, control_plane" />
    </>
  );
}

function ActionList({ items, control }: { items: Action[]; control: string }) {
  if (items.length === 0) return <div className="ops-note">None.</div>;
  return (
    <ul className="ops-list">
      {items.map((a) => (
        <OpsItem
          key={a.name}
          control={`${control}.${a.name}`}
          tone={a.mutating ? "warn" : "idle"}
          title={<span className="ops-mono">{a.name}</span>}
          badges={
            <OpsBadge tone={a.mutating ? "warn" : "idle"}>
              {a.mutating ? "mutates state" : "reads only"}
            </OpsBadge>
          }
        >
          {a.describe ? <div className="ops-item-body">{a.describe}</div> : null}
          {a.args.length ? (
            <div className="ops-note">
              arguments: <span className="ops-mono">{a.args.join(", ")}</span>
            </div>
          ) : (
            <div className="ops-note">takes no arguments</div>
          )}
        </OpsItem>
      ))}
    </ul>
  );
}

export default Ingest;
