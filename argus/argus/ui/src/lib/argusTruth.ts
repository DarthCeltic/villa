import { usePoll, type Polled } from "./poll";


export interface CapabilityEvidence {
  receipt: string;
  path: string;
  present: boolean;
  sha256?: string;
  bytes?: number;
  written_utc?: string;
}

export interface CapabilityRow {
  key: string;
  name: string;
  does: string;
  implementation?: string;
  produces_stage: string | null;
  needs_stage: string | null;
  scientific: boolean;
  availability: string;
  operational_verification: string;
  scientific_admissibility: string;
  aggregate: string;
  may_produce_evidence: boolean;
  human_status: string;
  detail?: string;
  evidence: CapabilityEvidence[];
  evidence_present: number;
  evidence_declared: number;
}

export interface RouteEdge {
  stage: string;
  capability: string;
  producible_locally: boolean;
  artifact_already_held: boolean;
  scientifically_admissible: boolean;
  green: boolean;
  why: string | null;
}

export interface CapabilityGraph {
  contract: string;
  capabilities: CapabilityRow[];
  counts: {
    total: number;
    installed: number;
    control_passed: number;
    scientifically_admissible: number;
    scientific_capabilities: number;
  };
  route: {
    edges: RouteEdge[];
    first_blocked_stage: string | null;
    all_green: boolean;
    rule: string;
  };
  headline_rule: string;
}

export interface GateRow {
  gate: string;
  state: "OPEN" | "SHUT" | "BLOCKED_UPSTREAM" | string;
  why: string;
  satisfied_by: string | null;
  evidence?: Record<string, unknown>;
}

export interface GatesPayload {
  schema: string;
  gates: GateRow[];
  n_open: number;
  n_total: number;
  first_closed: string | null;
  next_action: string | null;
  rule?: string;
}

export interface ResultClass {
  contract: string;
  target: string;
  target_class: string;
  exposure_basis: string;
  detector: string;
  detector_cross_scroll_qualified: boolean;
  acquisition: string;
  metric: string | null;
  score: number | null;
  presentation: string;
  banner: string;
  may_claim_discovery: boolean;
  may_claim_ink_found: boolean;
  not_established: string[];
}


export const STAGES = ["CT", "Surface", "Flatten", "Sample", "Detect", "Review"] as const;
export type Stage = (typeof STAGES)[number];

export const STAGE_PLAIN: Record<Stage, string> = {
  CT: "Read the scan",
  Surface: "Find the sheet",
  Flatten: "Lay it flat",
  Sample: "Sample surface",
  Detect: "Look for ink",
  Review: "Check result",
};

export type Tone = "certified" | "blocked" | "refused" | "active" | "neutral";

export interface StageCell {
  stage: Stage;
  plain: string;
  tone: Tone;
  short: string;
  why: string;
  internal: string | null;
  claims: Claim[];
}


export interface Claim {
  says: string;
  route: string;
  field: string;
  receipt?: string | null;
  sha256?: string | null;
}


export function useCapabilityGraph(intervalMs = 30000): Polled<CapabilityGraph> {
  return usePoll<CapabilityGraph>("/api/capability_graph", { intervalMs });
}
export function useGates(intervalMs = 30000): Polled<GatesPayload> {
  return usePoll<GatesPayload>("/api/gates", { intervalMs });
}


const AVAILABILITY_PLAIN: Record<string, string> = {
  INSTALLED: "installed on this machine",
  NOT_INSTALLED: "not installed on this machine",
  UNAVAILABLE: "not available on this machine",
};

const VERIFICATION_PLAIN: Record<string, string> = {
  CONTROL_PASSED: "its mechanical controls pass",
  TESTED: "it is tested",
  UNTESTED: "nothing has tested it",
};

const ADMISSIBILITY_PLAIN: Record<string, string> = {
  PLUMBING_ONLY: "no scientific route admits its output",
  UNQUALIFIED: "its output is not scientifically qualified",
  NOT_APPLICABLE: "it makes no scientific claim",
  ADMISSIBLE: "its output is scientifically admissible",
};

function plain(map: Record<string, string>, key: string): string {
  return map[key] ?? key.toLowerCase().replace(/_/g, " ");
}

function evidenceClaim(row: CapabilityRow, says: string): Claim {
  const ev = row.evidence.find((e) => e.present) ?? row.evidence[0] ?? null;
  return {
    says,
    route: "/api/capability_graph",
    field: `capabilities[key=${row.key}]`,
    receipt: ev ? ev.receipt : null,
    sha256: ev ? (ev.sha256 ?? null) : null,
  };
}

export function deriveRoute(cg: CapabilityGraph | null): StageCell[] {
  return STAGES.map((stage): StageCell => {
    if (!cg) {
      return {
        stage,
        plain: STAGE_PLAIN[stage],
        tone: "neutral",
        short: "Not available",
        why: "The capability service has not answered yet, so nothing is known about this stage.",
        internal: null,
        claims: [],
      };
    }
    const edge = cg.route.edges.find((e) => e.stage === stage) ?? null;
    const row = edge ? (cg.capabilities.find((c) => c.key === edge.capability) ?? null) : null;
    if (!edge || !row) {
      return {
        stage,
        plain: STAGE_PLAIN[stage],
        tone: "neutral",
        short: "Not available",
        why: `The capability graph reports no capability for ${stage}, so this instrument cannot say whether the stage can run.`,
        internal: null,
        claims: [
          {
            says: `no capability is declared for ${stage}`,
            route: "/api/capability_graph",
            field: "route.edges",
          },
        ],
      };
    }

    const runnable = edge.producible_locally;
    const admissible = row.may_produce_evidence === true;

    let tone: Tone;
    let short: string;
    if (!runnable) {
      tone = "blocked";
      short = "Cannot run";
    } else if (!admissible) {
      tone = "blocked";
      short = "Not qualified";
    } else if (edge.artifact_already_held) {
      tone = "certified";
      short = "Done";
    } else {
      tone = "certified";
      short = "Ready";
    }

    const why =
      `${row.name} is ${plain(AVAILABILITY_PLAIN, row.availability)} and ` +
      `${plain(VERIFICATION_PLAIN, row.operational_verification)}` +
      (row.scientific
        ? `, but ${plain(ADMISSIBILITY_PLAIN, row.scientific_admissibility)}.`
        : ".") +
      (edge.why ? ` ${edge.why}` : "");

    return {
      stage,
      plain: STAGE_PLAIN[stage],
      tone,
      short,
      why,
      internal: row.aggregate,
      claims: [
        evidenceClaim(row, `${row.name}: ${row.human_status}`),
        {
          says: edge.artifact_already_held
            ? `an artifact for ${stage} is already held`
            : `no artifact for ${stage} is held yet`,
          route: "/api/capability_graph",
          field: `route.edges[stage=${stage}].artifact_already_held`,
        },
      ],
    };
  });
}


export interface PlainState {
  sentence: string;
  tone: Tone;
  blocker: { what: string; why: string; nextAction: string | null } | null;
  claims: Claim[];
  grounded: boolean;
}

export function derivePlainState(
  cg: CapabilityGraph | null,
  gates: GatesPayload | null,
  rc: ResultClass | null,
): PlainState {
  const claims: Claim[] = [];

  if (!cg) {
    return {
      sentence:
        "Not available: the capability service has not answered, so nothing can be said about what this machine can do right now.",
      tone: "neutral",
      blocker: null,
      claims: [
        { says: "no capability payload was received", route: "/api/capability_graph", field: "(whole payload)" },
      ],
      grounded: false,
    };
  }

  const route = deriveRoute(cg);
  const firstUnrunnable = route.find((s) => s.short === "Cannot run") ?? null;
  const detect = route.find((s) => s.stage === "Detect") ?? null;
  const doneStages = route.filter((s) => s.short === "Done");
  const lastDone = doneStages.length ? doneStages[doneStages.length - 1] : null;

  claims.push({
    says: `${cg.counts.installed} of ${cg.counts.total} capabilities are installed and ${cg.counts.control_passed} pass their mechanical controls`,
    route: "/api/capability_graph",
    field: "counts",
  });
  claims.push({
    says: `${cg.counts.scientifically_admissible} of ${cg.counts.scientific_capabilities} scientific capabilities are admissible`,
    route: "/api/capability_graph",
    field: "counts.scientifically_admissible",
  });

  if (firstUnrunnable) {
    claims.push(...firstUnrunnable.claims);
    return {
      sentence:
        `${lastDone ? `${lastDone.plain} is done. ` : ""}` +
        `${firstUnrunnable.plain} cannot run on this machine, so ink detection cannot be reached.`,
      tone: "blocked",
      blocker: {
        what: `${firstUnrunnable.stage} cannot run here`,
        why: firstUnrunnable.why,
        nextAction: gates?.next_action ?? null,
      },
      claims,
      grounded: true,
    };
  }

  const unqualified =
    (rc && rc.detector_cross_scroll_qualified === false) ||
    (detect !== null && detect.short === "Not qualified");

  if (unqualified) {
    if (rc) {
      claims.push({
        says: `the detector on this target has not passed cross-scroll qualification`,
        route: "/api/unroll",
        field: "result_class.detector_cross_scroll_qualified",
      });
      for (const ne of rc.not_established) {
        claims.push({ says: `not established: ${ne}`, route: "/api/unroll", field: "result_class.not_established" });
      }
    }
    if (detect) claims.push(...detect.claims);
    if (gates?.next_action) {
      claims.push({ says: `next legal step: ${gates.next_action}`, route: "/api/gates", field: "next_action" });
    }
    const closed = gates?.gates.find((g) => g.gate === gates.first_closed) ?? null;
    return {
      sentence:
        "Every stage up to ink detection can run here, but no detector has passed cross-scroll qualification — so anything it produces is a lead to examine, not a reading.",
      tone: "blocked",
      blocker: {
        what: "Ink detection is not scientifically qualified",
        why:
          closed?.why ??
          (rc
            ? rc.not_established.join("; ") || rc.banner
            : "the capability graph reports the ink engine as runnable but not admissible"),
        nextAction: gates?.next_action ?? null,
      },
      claims,
      grounded: true,
    };
  }

  const next = route.find((s) => s.short === "Ready") ?? null;
  if (next) {
    claims.push(...next.claims);
    return {
      sentence: `${lastDone ? `${lastDone.plain} is done. ` : ""}${next.plain} is ready to run.`,
      tone: "certified",
      blocker: null,
      claims,
      grounded: true,
    };
  }

  return {
    sentence: "Every stage this instrument declares is complete and admissible.",
    tone: "certified",
    blocker: null,
    claims,
    grounded: true,
  };
}
