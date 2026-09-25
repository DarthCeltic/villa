import { Link } from "react-router-dom";
import {
  Boxes, FileCheck2, GitBranch, Grid3x3, Layers, ScanLine, ShieldCheck, Waves,
} from "lucide-react";
import type { RunRecord, StageRow } from "../api";
import { CERT_LABEL, TruthChip } from "./Status";
import { CERT_STATES, type CertState } from "../lib/certification";
import { routes } from "../lib/nav";

export interface PipelineStage {
  key: string;
  label: string;
  sub: string;
  module: string | null;
  Icon: typeof Boxes;
}

export const PIPELINE: PipelineStage[] = [
  { key: "ct", label: "CT Volume", sub: "acquisition", module: null, Icon: Boxes },
  {
    key: "trace",
    label: "Traced Sheet",
    sub: "imported mesh, not ARGUS-generated",
    module: null,
    Icon: GitBranch,
  },
  {
    key: "geometry",
    label: "Validated Geometry",
    sub: "sheet-following, jump fraction, coverage",
    module: "route1.published_mesh",
    Icon: Grid3x3,
  },
  {
    key: "flatten",
    label: "Flatten",
    sub: "no separate receipt -- see Sample",
    module: "stage2.verified_render",
    Icon: Layers,
  },
  {
    key: "render",
    label: "Sample / Render CT",
    sub: "flattened surface",
    module: "stage2.verified_render",
    Icon: Waves,
  },
  {
    key: "ink",
    label: "Detect Ink",
    sub: "detector output",
    module: "stage3.ink_maps",
    Icon: ScanLine,
  },
  {
    key: "decision",
    label: "VIGILES Decision",
    sub: "frozen rule -- distinct from human Review (see header)",
    module: "stage4.vigiles_decision",
    Icon: ShieldCheck,
  },
  {
    key: "package",
    label: "Evidence Package",
    sub: "receipts and hashes",
    module: null,
    Icon: FileCheck2,
  },
];

export interface CellState {
  state: CertState;
  detail: string;
  row?: StageRow;
}

export function stageAnchorId(module: string): string {
  return `stage-${module.replace(/[^a-zA-Z0-9_-]+/g, "-")}`;
}

export function stageStates(run: RunRecord | null): Record<string, CellState> {
  const out: Record<string, CellState> = {};
  const rows = new Map<string, StageRow>();
  for (const s of run?.stages ?? []) rows.set(s.module, s);

  for (const stage of PIPELINE) {
    if (stage.key === "ct") {
      out[stage.key] = run?.acquisition
        ? {
            state: "ARTIFACT_SAVED",
            detail: `${run.acquisition.voxel_um} µm · ${run.acquisition.energy_kev} keV recorded`,
          }
        : { state: "UNAVAILABLE", detail: "no acquisition recorded by any stage" };
      continue;
    }
    if (stage.key === "trace") {
      out[stage.key] = run?.mesh_dir
        ? { state: "ARTIFACT_SAVED", detail: `imported mesh at ${run.mesh_dir}` }
        : { state: "UNAVAILABLE", detail: "no mesh directory recorded; ARGUS has no tracing capability of its own to fall back on" };
      continue;
    }
    if (stage.key === "flatten") {
      const row = rows.get("stage2.verified_render");
      if (!row) {
        out[stage.key] = {
          state: "NOT_RUN",
          detail: run?.operational_state === "RUNNING" ? "not reached yet" : "not run",
        };
      } else if (row.status !== "PASS") {
        out[stage.key] = { state: "RUN_REFUSED", detail: row.refusal_class ?? "refused", row };
      } else {
        out[stage.key] = {
          state: "OPERATIONAL_CONTROL_PASSED",
          detail: "code ran (shares stage2's receipt) -- flattening has no separately measured verdict",
          row,
        };
      }
      continue;
    }
    if (stage.key === "package") {
      const hashed = Object.keys(run?.hashes ?? {}).length > 0;
      const artifacts = run?.artifacts?.length ?? 0;
      out[stage.key] = hashed
        ? {
            state: artifacts > 0 ? "EVIDENCE_PACKAGE_COMPLETE" : "ARTIFACT_SAVED",
            detail:
              artifacts > 0
                ? `${artifacts} artifact${artifacts === 1 ? "" : "s"} hashed — code ran, no result implied`
                : "receipt hashed — code ran, no result implied",
          }
        : { state: "NOT_RUN", detail: "no hashed receipt yet" };
      continue;
    }
    const row = stage.module ? rows.get(stage.module) : undefined;
    if (!row) {
      out[stage.key] = {
        state: "NOT_RUN",
        detail: run?.operational_state === "RUNNING" ? "not reached yet" : "not run",
      };
      continue;
    }
    if (row.status !== "PASS") {
      out[stage.key] = { state: "RUN_REFUSED", detail: row.refusal_class ?? "refused", row };
      continue;
    }
    out[stage.key] = row.certified
      ? {
          state: "SCIENTIFIC_ADMISSIBLE",
          detail: CERT_LABEL[row.certified] ?? row.certified,
          row,
        }
      : { state: "OPERATIONAL_CONTROL_PASSED", detail: "stage passed, certified no rung", row };
  }
  return out;
}

export function applyIntegrityGate(
  states: Record<string, CellState>,
  suppressed: boolean,
): Record<string, CellState> {
  if (!suppressed) return states;
  const out: Record<string, CellState> = {};
  for (const [k, v] of Object.entries(states)) {
    out[k] =
      v.state === "SCIENTIFIC_ADMISSIBLE"
        ? { ...v, state: "SCIENTIFIC_QUALIFIED", detail: v.detail + " · ledger unverified" }
        : v;
  }
  return out;
}

export function PipelineStrip({
  run,
  compact = false,
  integritySuppressed = false,
}: {
  run: RunRecord | null;
  compact?: boolean;
  integritySuppressed?: boolean;
}) {
  const states = applyIntegrityGate(stageStates(run), integritySuppressed);
  return (
    <div
      role="list"
      aria-label="Reconstruction pipeline"
      style={{
        display: "grid",
        gridTemplateColumns: `repeat(auto-fit, minmax(${compact ? 150 : 176}px, 1fr))`,
        gap: 0,
        border: "1px solid var(--line)",
        borderRadius: "var(--radius)",
        overflowX: "auto",
        overflowY: "hidden",
        overscrollBehaviorX: "contain",
        background: "var(--bg-raised)",
      }}
    >
      {PIPELINE.map((stage, i) => {
        const st = states[stage.key];
        if (!st) return null;
        const d = CERT_STATES[st.state];
        const subdued = d.axis === "ABSENT";
        return (
          <div
            key={stage.key}
            role="listitem"
            title={`${stage.label} — ${d.axis} axis, ${d.word}: ${st.detail}. ${d.means}`}
            style={{
              padding: compact ? "12px 12px" : "14px 16px",
              borderRight: i < PIPELINE.length - 1 ? "1px solid var(--line)" : undefined,
              borderTop: `2px solid var(--status-${d.tone})`,
              opacity: subdued ? 0.58 : 1,
              display: "grid",
              gap: 8,
              minWidth: 0,
              background: subdued ? "transparent" : "var(--bg-raised-2)",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
              <stage.Icon
                size={17}
                strokeWidth={1.6}
                aria-hidden
                color={`var(--status-${d.tone})`}
              />
              <span
                style={{
                  fontSize: "var(--t-small)",
                  fontWeight: 600,
                  minWidth: 0,
                  overflowWrap: "anywhere",
                }}
              >
                {stage.label}
              </span>
            </div>
            <TruthChip state={st.state} detail={st.detail} size="sm" />
            <div
              className="meta"
              style={{ minWidth: 0, overflowWrap: "anywhere" }}
            >
              {st.detail || stage.sub}
            </div>
            {

}
            {run && !subdued ? (
              <div>
                {
}
                <Link
                  to={`${routes.evidence(run.run_id)}#${stageAnchorId(st.row?.module ?? stage.module ?? stage.key)}`}
                  className="small"
                  style={{ color: "var(--accent)" }}
                  title={`Read ${stage.label}'s receipt on Evidence`}
                >
                  Evidence →
                </Link>
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

export function stageEvidenceHref(run: RunRecord | null, stage: PipelineStage, st: CellState): string | null {
  if (!run) return null;
  if (CERT_STATES[st.state].axis === "ABSENT") return null;
  return `${routes.evidence(run.run_id)}#${stageAnchorId(st.row?.module ?? stage.module ?? stage.key)}`;
}

export function PipelineLedger({
  run,
  integritySuppressed = false,
}: {
  run: RunRecord | null;
  integritySuppressed?: boolean;
}) {
  const states = applyIntegrityGate(stageStates(run), integritySuppressed);
  return (
    <ol className="wb-ledger" aria-label="Reconstruction pipeline, stage by stage" data-control="wb.ledger">
      {PIPELINE.map((stage) => {
        const st = states[stage.key];
        if (!st) return null;
        const d = CERT_STATES[st.state];
        const href = stageEvidenceHref(run, stage, st);
        return (
          <li
            key={stage.key}
            className="wb-ledger-row"
            data-stage={stage.key}
            data-state={st.state}
            data-axis={d.axis}
            title={`${stage.label} — ${d.axis} axis, ${d.word}: ${st.detail}. ${d.means}`}
          >
            <span className="wb-ledger-icon" aria-hidden="true">
              <stage.Icon size={18} strokeWidth={1.6} color={`var(--status-${d.tone})`} />
            </span>
            <span className="wb-ledger-name">{stage.label}</span>
            <span className="wb-ledger-state">
              <TruthChip state={st.state} detail={st.detail} size="sm" />
            </span>
            <span className="wb-ledger-detail">{st.detail || stage.sub}</span>
            {href ? (
              <Link to={href} className="wb-ledger-link" data-control={`wb.ledger.${stage.key}.evidence`} title={`Read ${stage.label}'s receipt on Evidence`}>
                Evidence →
              </Link>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
