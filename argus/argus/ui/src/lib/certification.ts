import type { RunRecord } from "../api";
import type { Tone } from "../components/Status";

export type CertState =
  | "ARTIFACT_SAVED"
  | "EVIDENCE_PACKAGE_COMPLETE"
  | "OPERATIONAL_CONTROL_PASSED"
  | "SCIENTIFIC_ADMISSIBLE"
  | "SCIENTIFIC_QUALIFIED"
  | "RUN_REFUSED"
  | "NOT_RUN"
  | "UNAVAILABLE"
  | "STALE"
  | "FIXTURE_ONLY";

export type CertAxis = "ARTIFACT" | "OPERATIONAL" | "SCIENTIFIC" | "ABSENT";

export interface CertDescriptor {
  word: string;
  axis: CertAxis;
  tone: Tone;
  green: boolean;
  means: string;
}

export const CERT_STATES: Record<CertState, CertDescriptor> = {
  ARTIFACT_SAVED: {
    word: "saved",
    axis: "ARTIFACT",
    tone: "active",
    green: false,
    means:
      "a file was written and hashed. This is a statement about code having run, not " +
      "about any result being correct.",
  },
  EVIDENCE_PACKAGE_COMPLETE: {
    word: "package complete",
    axis: "ARTIFACT",
    tone: "active",
    green: false,
    means:
      "every artifact the run declared is present and hashed. The package being complete " +
      "says nothing about whether the run's science passed.",
  },
  OPERATIONAL_CONTROL_PASSED: {
    word: "control passed",
    axis: "OPERATIONAL",
    tone: "active",
    green: false,
    means:
      "a mechanical control passed -- the stage ran and its own checks held. Operational " +
      "verification is not scientific admissibility.",
  },
  SCIENTIFIC_ADMISSIBLE: {
    word: "certified",
    axis: "SCIENTIFIC",
    tone: "certified",
    green: true,
    means:
      "a scientific check passed and the result is admissible at this rung. This is the " +
      "only state in the interface that earns green.",
  },
  SCIENTIFIC_QUALIFIED: {
    word: "qualified",
    axis: "SCIENTIFIC",
    tone: "blocked",
    green: false,
    means:
      "a scientific result exists and carries a stated qualification -- a scope, a " +
      "tolerance, a caveat. It may not be read as an unqualified result.",
  },
  RUN_REFUSED: {
    word: "refused",
    axis: "ABSENT",
    tone: "refused",
    green: false,
    means: "the run refused, with a class and a reason. Nothing here is a result.",
  },
  NOT_RUN: {
    word: "not run",
    axis: "ABSENT",
    tone: "blocked",
    green: false,
    means: "this stage never ran. Absence is shown as absence, not as a pass.",
  },
  UNAVAILABLE: {
    word: "unavailable",
    axis: "ABSENT",
    tone: "blocked",
    green: false,
    means:
      "the value could not be read. Unavailable is not zero, not empty and not a pass -- " +
      "nothing is being substituted for it.",
  },
  STALE: {
    word: "stale",
    axis: "ABSENT",
    tone: "blocked",
    green: false,
    means:
      "this was true when it was last read and may not be true now. The age is shown so " +
      "the reader can decide.",
  },
  FIXTURE_ONLY: {
    word: "fixture",
    axis: "ABSENT",
    tone: "blocked",
    green: false,
    means:
      "this value comes from a fixture, not from a measurement of real data. It may never " +
      "be read as a result.",
  },
};

export const RUNG_LABEL: Record<string, string> = {
  CERTIFIED_SURFACE: "Surface certified",
  CERTIFIED_2D: "2D certified",
  CERTIFIED_INK_CANDIDATE: "Ink candidate",
};

export interface RunCertification {
  state: CertState;
  descriptor: CertDescriptor;
  rung: string | null;
  withheldBecause: string | null;
  stagesRun: number;
  stagesRefused: number;
  stagesTotalDeclared: number;
}

export function runCertification(
  run: RunRecord | null,
  opts: { chainSuppressed?: boolean; chainWhy?: string | null } = {},
): RunCertification {
  const stages = run?.stages ?? [];
  const stagesRun = stages.filter((s) => s.status === "PASS").length;
  const stagesRefused = stages.filter((s) => s.status !== "PASS").length;
  const base = {
    rung: run?.highest_certified_stage ?? null,
    stagesRun,
    stagesRefused,
    stagesTotalDeclared: stages.length,
  };

  if (!run) {
    return { ...base, state: "UNAVAILABLE", descriptor: CERT_STATES.UNAVAILABLE,
      withheldBecause: "no run record was available to read" };
  }
  if (run.terminal === "REFUSED" || run.operational_state === "REFUSED") {
    return {
      ...base,
      state: "RUN_REFUSED",
      descriptor: CERT_STATES.RUN_REFUSED,
      withheldBecause:
        run.refusal_reason ??
        run.refusal_class ??
        "the run recorded a terminal REFUSED state",
    };
  }
  if (!run.highest_certified_stage) {
    const hashed = Object.keys(run.hashes ?? {}).length > 0;
    return {
      ...base,
      state: hashed ? "ARTIFACT_SAVED" : "NOT_RUN",
      descriptor: hashed ? CERT_STATES.ARTIFACT_SAVED : CERT_STATES.NOT_RUN,
      withheldBecause:
        "no stage certified a scientific rung" +
        (hashed ? "; the hashed receipts record that code ran, nothing more" : ""),
    };
  }
  if (opts.chainSuppressed) {
    return {
      ...base,
      state: "SCIENTIFIC_QUALIFIED",
      descriptor: CERT_STATES.SCIENTIFIC_QUALIFIED,
      withheldBecause: opts.chainWhy ?? "the command ledger cannot verify itself",
    };
  }
  if (stagesRefused > 0) {
    return {
      ...base,
      state: "SCIENTIFIC_QUALIFIED",
      descriptor: CERT_STATES.SCIENTIFIC_QUALIFIED,
      withheldBecause: `${stagesRefused} stage${stagesRefused === 1 ? "" : "s"} refused in this run`,
    };
  }
  return {
    ...base,
    state: "SCIENTIFIC_ADMISSIBLE",
    descriptor: CERT_STATES.SCIENTIFIC_ADMISSIBLE,
    withheldBecause: null,
  };
}

export function cellState(kind: "artifact" | "operational" | "scientific",
                          passed: boolean,
                          opts: { refused?: boolean; unavailable?: boolean } = {}
): CertState {
  if (opts.unavailable) return "UNAVAILABLE";
  if (opts.refused) return "RUN_REFUSED";
  if (!passed) return "NOT_RUN";
  if (kind === "artifact") return "ARTIFACT_SAVED";
  if (kind === "operational") return "OPERATIONAL_CONTROL_PASSED";
  return "SCIENTIFIC_ADMISSIBLE";
}
