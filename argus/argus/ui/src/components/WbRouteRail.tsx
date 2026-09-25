import { useState } from "react";
import type { StageCell } from "../lib/argusTruth";
import { STATE_TONE, stepWord, type ScrollStatus, type StatusStep } from "../lib/scrollStatus";
import { Glyph } from "./RouteStrip";
import { NextStep, usePersistedChoice } from "./workbench/Clamshell";

const OPEN_CLOSED = ["open", "closed"] as const;
import "../theme/workbench.css";

export function WbRouteRail({ route, scrollStatus }: { route: StageCell[]; scrollStatus?: ScrollStatus | null }) {
  const [railState, setRailState] = usePersistedChoice("clam.route", OPEN_CLOSED, "closed");
  const open = railState === "open";
  const setOpen = (f: (v: boolean) => boolean) => setRailState((cur) => (f(cur === "open") ? "open" : "closed"));
  const [stage, setStage] = useState<string | null>(null);
  const selectedSteps: StatusStep[] | null = scrollStatus?.status === "OK" ? scrollStatus.steps : null;
  const selectedPicked = selectedSteps?.find((s) => s.id === stage) ?? null;
  const picked = route.find((s) => s.stage === stage) ?? null;

  if (selectedSteps) {
    const completed = selectedSteps.filter((s) => s.state === "DONE");
    const done = completed.length;
    const furthest = completed[completed.length - 1] ?? null;
    const blocked = selectedSteps.filter((s) => s.state === "BLOCKED").length;
    const progress = scrollStatus?.progress_summary;
    return (
      <div className="ag-rail" data-wb="route-rail" data-route-source="scroll-status">
        <div className="ag-rail-head">
          <button
            type="button"
            className="ag-btn"
            aria-expanded={open}
            aria-controls="wb-route-rail-body"
            data-control="wb.route.toggle"
            onClick={() => setOpen((v) => !v)}
          >
            {open ? "Hide the route" : "Show the route"}
          </button>
          <span className="ag-rail-summary" data-control="wb.route.summary">
            {progress ? (
              <>{(progress.evidence_attempted || progress.verified_outputs)
                ? `${progress.done} canonical prerequisite${progress.done === 1 ? "" : "s"} reconciled; ${progress.verified_outputs ?? 0} downstream output${progress.verified_outputs === 1 ? "" : "s"} verified and viewable outside the unfinished route`
                : `Route receipts: ${progress.done} of ${progress.total} complete`}
              {progress.ready ? `, ${progress.ready} ready` : ""}{progress.decisions ? `, ${progress.decisions} decision${progress.decisions === 1 ? "" : "s"}` : ""}{progress.blocked ? `, ${progress.blocked} required blocker${progress.blocked === 1 ? "" : "s"}` : ""}. {progress.furthest_complete ? `Furthest canonical receipt: ${progress.furthest_complete.label}. ` : ""}</>
            ) : (
              <>Current scroll route: {done} of {selectedSteps.length} steps done{blocked ? `, ${blocked} blocked` : ", no blocked step recorded"}. {furthest ? `Furthest complete: ${furthest.n}. ${furthest.label}. ` : "No completed step recorded. "}</>
            )}
            {scrollStatus?.next_action ? `Work next: ${scrollStatus.next_action.label}.` : ""}
          </span>
        </div>
        {open ? (
          <div id="wb-route-rail-body" className="ag-rail">
            <nav className="ag-rail-stages" aria-label="Current scroll route">
              {selectedSteps.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  className="ag-rail-stage"
                  data-tone={STATE_TONE[s.state]}
                  data-state={s.state}
                  data-stage={s.id}
                  aria-pressed={stage === s.id}
                  data-control={`wb.route.stage.${s.id}`}
                  onClick={() => setStage(stage === s.id ? null : s.id)}
                >
                  <span aria-hidden>{s.n}</span>
                  <span>{s.label}</span>
                  <span className="ag-rail-short">{stepWord(s)}</span>
                </button>
              ))}
            </nav>
            {selectedPicked ? (
              <div className="ag-rail-inspector" data-control={`wb.route.inspector.${selectedPicked.id}`}>
                <b>{selectedPicked.n}. {selectedPicked.label}: {stepWord(selectedPicked)}</b>
                <span>{selectedPicked.why}</span>
                <span className="ag-source" data-kind="route">/api/scroll_status?scroll={encodeURIComponent(scrollStatus?.requested ?? "")}</span>
                {selectedPicked.to && selectedPicked.action_label ? (
                  <NextStep id={`route.${selectedPicked.id}`} to={selectedPicked.to} label={selectedPicked.action_label} />
                ) : scrollStatus?.next_action ? (
                  <NextStep id={`route.${selectedPicked.id}`} to={scrollStatus.next_action.to} label={scrollStatus.next_action.label} />
                ) : null}
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    );
  }

  const blocked = route.filter((s) => s.tone === "blocked").length;
  const done = route.filter((s) => s.short === "Done").length;

  return (
    <div className="ag-rail" data-wb="route-rail">
      <div className="ag-rail-head">
        <button
          type="button"
          className="ag-btn"
          aria-expanded={open}
          aria-controls="wb-route-rail-body"
          data-control="wb.route.toggle"
          onClick={() => setOpen((v) => !v)}
        >
          {open ? "Hide the route" : "Show the route"}
        </button>
        <span className="ag-rail-summary" data-control="wb.route.summary">
          Route to a reading: {done} of {route.length} stage{route.length === 1 ? "" : "s"} done
          {blocked
            ? `, ${blocked} cannot run or is not scientifically qualified`
            : ", none reported as blocked"}
          .
        </span>
      </div>

      {open ? (
        <div id="wb-route-rail-body" className="ag-rail">
          <nav className="ag-rail-stages" aria-label="Route to a reading">
            {route.map((s, i) => (
              <button
                key={s.stage}
                type="button"
                className="ag-rail-stage"
                data-tone={s.tone}
                data-stage={s.stage}
                aria-pressed={stage === s.stage}
                data-control={`wb.route.stage.${s.stage}`}
                onClick={() => setStage(stage === s.stage ? null : s.stage)}
              >
                <span aria-hidden>{i + 1}</span>
                <span>{s.plain}</span>
                <span className="ag-rail-short">
                  <Glyph tone={s.tone} /> {s.short}
                </span>
              </button>
            ))}
          </nav>

          {picked ? (
            <div className="ag-rail-inspector" data-control={`wb.route.inspector.${picked.stage}`}>
              <b>
                {picked.stage} — {picked.plain}: {picked.short}
              </b>
              <span>{picked.why}</span>
              <span className="ag-source" data-kind="route">
                {picked.internal ?? "no aggregate reported"} · /api/capability_graph
              </span>
              {picked.claims.length ? (
                <ul className="ag-list">
                  {picked.claims.map((c, n) => (
                    <li key={`${c.field}-${n}`}>
                      <span className="ag-item-sub">{c.says}</span>
                      <span className="ag-item-id">
                        {c.route} → {c.field}
                        {c.receipt ? ` · ${c.receipt}` : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : null}
              {picked.tone === "blocked" || picked.tone === "refused" ? (
                <NextStep
                  id={`route.${picked.stage}`}
                  to="/review"
                  label="See what is blocking this stage on Review"
                />
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
