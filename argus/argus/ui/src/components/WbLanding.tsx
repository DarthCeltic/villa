import { Link } from "react-router-dom";
import type { RunRecord, SealedExperiment } from "../api";
import { partitionRuns, runLabel } from "../lib/runIdentity";
import { routes } from "../lib/nav";
import { Chip } from "./Status";
import { ScrollContextPicker } from "./ScrollContextPicker";
import "../theme/workbench.css";

const ACTIVE = new Set(["RUNNING", "PENDING", "STALLED"]);

export function WbLanding({
  runs: all,
  loaded,
  sealedExperiments = [],
  registered,
  firstLetters,
}: {
  runs: RunRecord[];
  loaded: boolean;
  sealedExperiments?: SealedExperiment[];
  registered: number;
  firstLetters: number | null;
}) {
  const { runs, containers } = partitionRuns(all.filter((r) => !r.foreign));
  const byNewest = [...runs].sort((a, b) => b.run_id.localeCompare(a.run_id));

  const active = byNewest.filter((r) => ACTIVE.has(r.operational_state));
  const sealed = byNewest.filter((r) => r.blinding.sealed);
  const incomplete = byNewest.filter(
    (r) =>
      !r.blinding.sealed &&
      !ACTIVE.has(r.operational_state) &&
      (r.operational_state === "REFUSED" || r.highest_certified_stage === null),
  );
  const finished = byNewest.filter(
    (r) => !r.blinding.sealed && r.highest_certified_stage !== null,
  );

  return (
    <div className="ag-landing" data-wb="landing">
      <header className="ag-shelf-head">
        <h1 className="ag-shelf-title">The bench is empty</h1>
        <p className="ag-shelf-sub">
          {loaded
            ? `The feed carries ${runs.length} run${runs.length === 1 ? "" : "s"}` +
              (containers.length
                ? ` and ${containers.length} director${containers.length === 1 ? "y" : "ies"} that merely contain${containers.length === 1 ? "s" : ""} runs, which are not work and are not counted`
                : "") +
              ". Open one below, or choose a scroll and start from the object rather than from a run."
            : "The service is building its first snapshot. Nothing is being asserted about what work exists until it arrives."}
        </p>
      </header>

      <div className="ag-landing-grid">
        <Panel
          title="Choose a scroll"
          prose={firstLetters === null ? `Work starts from a physical object. ${registered} scroll identities are known; prize eligibility is unknown because the official target registry is not installed. The shelf preselects nothing.` : `Work starts from a physical object. ${registered} scroll identities are registered, ${firstLetters} of them First Letters targets; the shelf keeps the three collections apart and preselects nothing.`}
        >
          <div className="ag-actions">
            <ScrollContextPicker
              control="wb.landing.picker"
              buttonClassName="ag-btn-primary"
              buttonLabel="Choose a scroll"
            />
            <Link className="ag-btn" to="/explore?tab=scrolls" data-control="wb.landing.explore">
              Compare scrolls in Explore
            </Link>
          </div>
        </Panel>

        <Panel
          title="Start a governed workflow"
          prose={
            "Every action that changes anything goes through the governed job path: a " +
            "short-lived session a person opens on purpose, a preflight that may refuse, and " +
            "a receipt. This screen only reads, so it links to that path rather than " +
            "offering a button that would submit work from a landing page."
          }
        >
          <div className="ag-actions">
            <Link className="ag-btn-primary" to="/system?tab=jobs" data-control="wb.landing.jobs">
              Go to the job ledger
            </Link>
            <Link
              className="ag-btn"
              to="/review?tab=blockers"
              data-control="wb.landing.blockers"
            >
              What is blocking a reading
            </Link>
          </div>
        </Panel>

        <RunPanel
          title="Still going"
          prose="Runs the feed reports as running, pending or stalled. A stalled run is still open, not finished."
          rows={active}
          empty="No run is reported as running, pending or stalled."
          domain="active"
        />

        <Panel
          title="Sealed results"
          prose={
            sealedExperiments.length || sealed.length
              ? "Under an active blinding marker. The service withholds the bytes, so a sealed result can be located and cannot be read here — that is the seal working, not a failure."
              : "No run is under a blinding marker."
          }
        >
          {sealedExperiments.length ? (
            <ul className="ag-list">
              {sealedExperiments.map((e) => (
                <li key={e.run_id}>
                  <Link
                    className="ag-item"
                    to={routes.evidence(e.run_id)}
                    data-control={`wb.landing.sealed.${e.run_id}`}
                  >
                    <span className="ag-item-title">
                      {e.marker} · {e.surfaces_staged} surface
                      {e.surfaces_staged === 1 ? "" : "s"} staged
                    </span>
                    <span className="ag-item-sub">
                      {e.readings.done} of{" "}
                      {e.readings.total === null ? "an undeclared number of" : e.readings.total}{" "}
                      readings — {e.readings.why}
                    </span>
                    <span className="ag-item-id">{e.run_id}</span>
                  </Link>
                </li>
              ))}
            </ul>
          ) : sealed.length ? (
            <RunList rows={sealed} domain="sealed" />
          ) : null}
        </Panel>

        <RunPanel
          title="Refused or incomplete"
          prose="A refusal is a recorded decision and is worth reading; an incomplete run certified nothing. Neither is a negative result about a scroll."
          rows={incomplete}
          empty="No run is refused, and every run in the feed certified something."
          domain="incomplete"
        />

        <RunPanel
          title="Recent finished work"
          prose="Runs that reached at least one certification rung. The rung is named on each row — certification is a ladder, and the rung reached is the fact."
          rows={finished.slice(0, 8)}
          empty="No run in the feed has reached a certification rung."
          domain="recent"
        />
      </div>
    </div>
  );
}

function Panel({
  title,
  prose,
  children,
}: {
  title: string;
  prose: string;
  children?: React.ReactNode;
}) {
  return (
    <section className="ag-panel" aria-label={title}>
      <h3 className="ag-panel-title">{title}</h3>
      <p className="ag-prose">{prose}</p>
      {children}
    </section>
  );
}

function RunPanel({
  title,
  prose,
  rows,
  empty,
  domain,
}: {
  title: string;
  prose: string;
  rows: RunRecord[];
  empty: string;
  domain: string;
}) {
  return (
    <section className="ag-panel" aria-label={title}>
      <h3 className="ag-panel-title">
        {title} <span className="ag-collection-count">{rows.length}</span>
      </h3>
      <p className="ag-prose">{rows.length ? prose : empty}</p>
      {rows.length ? <RunList rows={rows} domain={domain} /> : null}
    </section>
  );
}

function RunList({ rows, domain }: { rows: RunRecord[]; domain: string }) {
  return (
    <ul className="ag-list">
      {rows.map((r) => {
        const label = runLabel(r);
        const scroll = r.target ? (r.target.split("/")[0] ?? null) : null;
        return (
          <li key={r.run_id}>
            <Link
              className="ag-item"
              to={`${routes.workbench(r.run_id)}${
                scroll ? `?scroll=${encodeURIComponent(scroll)}` : ""
              }`}
              data-control={`wb.landing.${domain}.${r.run_id}`}
            >
              <span className="ag-item-title">
                {label.primary}
                {scroll ? ` · ${scroll}` : " · no target declared"}
              </span>
              <span className="ag-item-sub">
                <Chip
                  tone={
                    r.operational_state === "REFUSED"
                      ? "refused"
                      : r.operational_state === "COMPLETE"
                        ? "certified"
                        : "active"
                  }
                  size="sm"
                >
                  {r.operational_state.toLowerCase()}
                </Chip>{" "}
                {r.highest_certified_stage
                  ? r.highest_certified_stage.replace(/_/g, " ").toLowerCase()
                  : "nothing certified"}
                {r.refusal_reason ? ` — ${r.refusal_reason}` : ""}
              </span>
              <span className="ag-item-id">{r.run_id}</span>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
