import type { RunRecord } from "../api";
import { stageStates, type CellState } from "./PipelineStrip";
import { primaryRunForScroll } from "../lib/runIdentity";
import type { ScrollObject, Universe } from "./ShelfUniverse";
import "../theme/pipeline-narrative.css";

type State = "PROVEN" | "AVAILABLE" | "DECLARED" | "RECORDED" | "BLOCKED" | "NOT_RUN" | "UNKNOWN";

type Row = {
  label: string;
  state: State;
  detail: string;
};

function rowsFromShelf(scroll: ScrollObject): Row[] {
  const declared = scroll.acquisitions.filter((item) => item.scanId);
  const acquisition = declared.length === 1 ? declared[0] : undefined;
  const ambiguous = declared.length > 1;
  const hasAcquisition = Boolean(acquisition?.scanId);
  const noRun = "no matching run receipt exists; this execution stage has not been measured";
  return [
    {
      label: "CT acquisition",
      state: hasAcquisition ? "DECLARED" : "UNKNOWN",
      detail: hasAcquisition
        ? `${acquisition?.scanId} is declared in the scroll registry`
        : ambiguous
          ? "multiple acquisition identities are declared; no single one is selected"
          : "no acquisition identity is declared",
    },
    {
      label: "Volume identity",
      state: acquisition?.volumeStore ? "DECLARED" : hasAcquisition ? "DECLARED" : "UNKNOWN",
      detail: acquisition?.volumeStore
        ?? (ambiguous
          ? "multiple acquisition identities are declared; no single volume store is selected"
          : hasAcquisition
            ? "scan identity exists; store binding is not recorded"
            : "no bound local or upstream volume"),
    },
    { label: "Surface geometry", state: "NOT_RUN", detail: noRun },
    { label: "Verified render", state: "NOT_RUN", detail: noRun },
    { label: "VIGILES decision", state: "NOT_RUN", detail: noRun },
    { label: "Ink result", state: "NOT_RUN", detail: noRun },
  ];
}

function rowFromCell(label: string, cell: CellState): Row {
  switch (cell.state) {
    case "SCIENTIFIC_ADMISSIBLE":
      return { label, state: "PROVEN", detail: `${cell.detail} · receipt-backed stage passed` };
    case "SCIENTIFIC_QUALIFIED":
      return { label, state: "RECORDED", detail: `${cell.detail} · ledger qualification is still required` };
    case "OPERATIONAL_CONTROL_PASSED":
    case "ARTIFACT_SAVED":
    case "EVIDENCE_PACKAGE_COMPLETE":
      return { label, state: "AVAILABLE", detail: cell.detail };
    case "RUN_REFUSED":
      return { label, state: "BLOCKED", detail: cell.row?.refusal_reason ?? cell.detail };
    case "UNAVAILABLE":
      return { label, state: "UNKNOWN", detail: cell.detail };
    case "NOT_RUN":
    default:
      return { label, state: "NOT_RUN", detail: cell.detail };
  }
}

function rowsFromReceipt(run: RunRecord): Row[] {
  const states = stageStates(run);
  const missing = (label: string): Row => ({
    label,
    state: "UNKNOWN",
    detail: "the pipeline receipt did not declare this stage",
  });
  const cell = (key: string, label: string): Row => {
    const value = states[key];
    return value ? rowFromCell(label, value) : missing(label);
  };
  const identity: Row = run.acquisition
    ? {
        label: "Volume identity",
        state: "DECLARED",
        detail: `${run.acquisition.volume_id} declared by this run receipt; no separate identity verdict is asserted here`,
      }
    : {
        label: "Volume identity",
        state: "UNKNOWN",
        detail: "this run receipt declares no acquisition identity",
      };
  return [
    cell("ct", "CT acquisition"),
    identity,
    cell("geometry", "Surface geometry"),
    cell("render", "Verified render"),
    cell("decision", "VIGILES decision"),
    cell("ink", "Ink result"),
  ];
}

export function ScrollPipelineNarrative({
  scroll,
  universe,
  run = null,
  runs,
}: {
  scroll: ScrollObject;
  universe: Universe;
  run?: RunRecord | null;
  runs?: RunRecord[];
}) {
  const receiptRun = run ?? primaryRunForScroll(runs ?? [], scroll.id);
  const items = receiptRun ? rowsFromReceipt(receiptRun) : rowsFromShelf(scroll);
  return (
    <section className="pipeline-narrative" aria-label={`Pipeline status for ${scroll.display}`}>
      <div className="pipeline-narrative-head">
        <div>
          <p className="eyebrow">The path for this scroll</p>
          <h3>CT to ink, stage by stage</h3>
        </div>
        <span className="pipeline-narrative-count">{universe.counts.registered.value} registered identities</span>
      </div>
      {!receiptRun && (
        <p className="pipeline-narrative-reference" role="status">
          No run receipt is selected for this scroll. The rows below describe declared shelf
          context only; they are not evidence that these stages ran.
        </p>
      )}
      <ol className="pipeline-narrative-list">
        {items.map((item, index) => (
          <li key={item.label} className="pipeline-narrative-item" data-state={item.state}>
            <span className="pipeline-narrative-index" aria-hidden="true">{index + 1}</span>
            <div>
              <div className="pipeline-narrative-row">
                <strong>{item.label}</strong>
                <span className="pipeline-narrative-state">{item.state.replace("_", " ")}</span>
              </div>
              <p>{item.detail}</p>
            </div>
          </li>
        ))}
      </ol>
      <p className="pipeline-narrative-note">
        Operationally available is not scientifically admissible. A recorded result still needs
        its evidence class and the qualification gates before it can be called a reading.
      </p>
    </section>
  );
}
