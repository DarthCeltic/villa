import type { RunRecord } from "../api";
import { Chip } from "./Status";
import { NextStep } from "./workbench/Clamshell";

type Licence = {
  key: string;
  title: string;
  granted: boolean | null;
  observed: string;
  required: string;
  why: string;
};

const BLURB: Record<string, string> = {
  detector_domain:
    "A detector is licensed only inside the acquisition family it was qualified on; outside it a reading is refused rather than scored low.",
  physical_window:
    "The receptive field in millimetres on the papyrus, compared with the window the detector was qualified at.",
  calibration_control:
    "A known-ink control at the same acquisition, passed. Without it the instrument is uncalibrated.",
  specificity:
    "A pre-registered false-positive rate on matched ink-free material. Not specific means a candidate is not reportable however it looks.",
};

export function VigilesPanel({
  run,
  evidenceTo,
}: {
  run: RunRecord | null;
  evidenceTo?: string;
}) {
  const licences = extract(run);

  return (
    <div style={{ display: "grid", gap: 12, padding: 18, alignContent: "start" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <h2 className="eyebrow" style={{ color: "var(--ink-dim)" }}>
          VIGILES · licences
        </h2>
        {licences === null ? (
          <Chip tone="blocked">Not evaluated</Chip>
        ) : licences.every((l) => l.granted) ? (
          <Chip tone="certified">Licensed</Chip>
        ) : (
          <Chip tone="refused">
            {licences.filter((l) => !l.granted).length} of {licences.length} missing
          </Chip>
        )}
      </div>

      {licences === null ? (
        <p className="small muted">
          This run did not reach the decision stage, so no licence was evaluated. That is
          not the same as a licence being granted.
        </p>
      ) : (
        licences.map((l) => (
          <div
            key={l.key}
            className="panel"
            style={{
              padding: 14,
              display: "grid",
              gap: 8,
              borderLeft: `3px solid var(--status-${l.granted ? "certified" : "refused"})`,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <Chip tone={l.granted ? "certified" : "refused"} size="sm">
                {l.granted ? "granted" : "denied"}
              </Chip>
              <strong style={{ fontSize: "var(--t-body)", textTransform: "capitalize" }}>
                {l.title}
              </strong>
            </div>
            <div className="meta" style={{ lineHeight: 1.6, wordBreak: "break-word" }}>
              <span className="faint">observed </span>
              {l.observed}
              <span className="faint"> · required </span>
              {l.required}
            </div>
            {!l.granted ? (
              <div className="small muted">{l.why}</div>
            ) : null}
            {!l.granted && evidenceTo ? (
              <NextStep id={`vigiles.${l.key}`} to={evidenceTo} label="Read the licence record on Evidence" />
            ) : null}
            <div className="small faint">{BLURB[l.key]}</div>
          </div>
        ))
      )}

      {run?.refusal_class ? (
        <div
          className="panel"
          style={{ padding: 14, borderColor: "var(--status-refused-edge)" }}
        >
          <Chip tone="refused">{run.refusal_class}</Chip>
          <div className="small" style={{ marginTop: 8 }}>
            {run.refusal_reason}
          </div>
          {run.highest_certified_stage ? (
            <div className="small faint" style={{ marginTop: 8 }}>
              This refusal does not erase what was established: the run still reached{" "}
              <strong>{run.highest_certified_stage}</strong>.
            </div>
          ) : null}
          {evidenceTo ? (
            <NextStep id="vigiles.refusal" to={evidenceTo} label="Read the refusal on Evidence" />
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function extract(run: RunRecord | null): Licence[] | null {
  if (!run) return null;
  const det = (run.detail ?? {}) as Record<string, unknown>;
  const ev = (det.evidence ?? {}) as Record<string, unknown>;
  const raw = (ev.licences ?? (det as Record<string, unknown>).licences) as
    | Record<string, Record<string, unknown>>
    | undefined;
  if (!raw) return null;
  return Object.entries(raw).map(([key, v]) => ({
    key,
    title: key.replace(/_/g, " "),
    granted: Boolean(v.granted),
    observed: describe(key, v, "observed"),
    required: describe(key, v, "required"),
    why: String(v.why ?? ""),
  }));
}

function describe(
  key: string,
  v: Record<string, unknown>,
  side: "observed" | "required",
): string {
  const s = (x: unknown) => (x === null || x === undefined ? "—" : String(x));
  if (key === "detector_domain")
    return side === "observed" ? s(v.observed) : s(v.qualified);
  if (key === "physical_window")
    return side === "observed"
      ? `${s(v.window_mm)} mm`
      : `${s(v.qualified_window_mm)} mm`;
  if (key === "calibration_control")
    return side === "observed"
      ? JSON.stringify(v.control ?? null)
      : "passed, same acquisition family";
  if (key === "specificity")
    return side === "observed"
      ? JSON.stringify(v.panel ?? null)
      : "SPECIFIC, pre-registered";
  return side === "observed" ? s(v.observed) : s(v.required);
}
