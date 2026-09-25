import { describe, expect, it } from "vitest";
import { stageAnchorId, stageStates } from "./PipelineStrip";
import type { RunRecord, StageRow } from "../api";
import { primaryRunForScroll } from "../lib/runIdentity";


function run(overrides: Partial<RunRecord> = {}): RunRecord {
  return {
    schema: "argus-run-v1",
    run_id: "test-run",
    run_dir: "fake-run-dir",
    collection: null,
    target: null,
    stage: null,
    operational_state: "COMPLETE",
    highest_certified_stage: null,
    terminal: null,
    refusal_class: null,
    refusal_reason: null,
    progress: {} as RunRecord["progress"],
    blinding: { sealed: false, marker: null, note: null },
    artifacts: [],
    hashes: {},
    stages: [],
    acquisition: null,
    acquisition_family: null,
    geometry: null,
    geometry_refusal: null,
    mesh_dir: null,
    mesh_sha256: null,
    orientation: null,
    physical_window_mm: null,
    detector: null,
    ...overrides,
  };
}

function stage(module: string, status: "PASS" | "REFUSED", certified: RunRecord["highest_certified_stage"] = null): StageRow {
  return { module, status, certified, refusal_class: status === "REFUSED" ? "SOME_REFUSAL" : null };
}

describe("stageStates: Trace", () => {
  it("is an artifact fact (never scientific), present only when a mesh directory is recorded", () => {
    const s1 = stageStates(run({ mesh_dir: "artifacts/mesh/run1" }));
    expect(s1.trace?.state).toBe("ARTIFACT_SAVED");
    expect(s1.trace?.detail).toContain("run1");

    const s2 = stageStates(run({ mesh_dir: null }));
    expect(s2.trace?.state).toBe("UNAVAILABLE");
  });

  it("never reports SCIENTIFIC_ADMISSIBLE, even when the geometry stage passed with a rung", () => {
    const r = run({
      mesh_dir: "artifacts/mesh/run1",
      stages: [stage("route1.published_mesh", "PASS", "CERTIFIED_SURFACE")],
    });
    const s = stageStates(r);
    expect(s.trace?.state).not.toBe("SCIENTIFIC_ADMISSIBLE");
    expect(s.geometry?.state).toBe("SCIENTIFIC_ADMISSIBLE");
  });
});

describe("stageStates: Flatten", () => {
  it("mirrors stage2.verified_render's own receipt rather than reading NOT_RUN independently", () => {
    const r = run({ stages: [stage("stage2.verified_render", "PASS", "CERTIFIED_2D")] });
    const s = stageStates(r);
    expect(s.flatten?.state).not.toBe("NOT_RUN");
    expect(s.render?.state).toBe("SCIENTIFIC_ADMISSIBLE");
  });

  it("is capped at OPERATIONAL_CONTROL_PASSED even when Sample itself is SCIENTIFIC_ADMISSIBLE -- flattening has no separate measurement to justify the stronger claim", () => {
    const r = run({ stages: [stage("stage2.verified_render", "PASS", "CERTIFIED_2D")] });
    const s = stageStates(r);
    expect(s.flatten?.state).toBe("OPERATIONAL_CONTROL_PASSED");
    expect(s.flatten?.detail).toContain("shares stage2");
  });

  it("reports a real refusal honestly when the shared stage refused", () => {
    const r = run({ stages: [stage("stage2.verified_render", "REFUSED")] });
    const s = stageStates(r);
    expect(s.flatten?.state).toBe("RUN_REFUSED");
    expect(s.render?.state).toBe("RUN_REFUSED");
  });

  it("reports NOT_RUN when the stage never ran", () => {
    const s = stageStates(run());
    expect(s.flatten?.state).toBe("NOT_RUN");
  });
});

describe("stageStates: every PIPELINE key resolves to a real cell", () => {
  it("produces a state for all 8 cells on a fully-passed run", () => {
    const r = run({
      acquisition: { voxel_um: 3.24, energy_kev: 53 } as RunRecord["acquisition"],
      mesh_dir: "artifacts/mesh/run1",
      hashes: { a: "sha256:..." },
      artifacts: [{ name: "x.png", path: "x.png" } as RunRecord["artifacts"][number]],
      stages: [
        stage("route1.published_mesh", "PASS", "CERTIFIED_SURFACE"),
        stage("stage2.verified_render", "PASS", "CERTIFIED_2D"),
        stage("stage3.ink_maps", "PASS", "CERTIFIED_INK_CANDIDATE"),
        stage("stage4.vigiles_decision", "PASS", "CERTIFIED_INK_CANDIDATE"),
      ],
    });
    const s = stageStates(r);
    for (const key of ["ct", "trace", "geometry", "flatten", "render", "ink", "decision", "package"]) {
      expect(s[key], `missing state for ${key}`).toBeTruthy();
    }
  });
});

describe("stageAnchorId", () => {
  it("keeps receipt links addressable without duplicating module identifiers", () => {
    expect(stageAnchorId("stage2.verified_render")).toBe("stage-stage2-verified_render");
    expect(stageAnchorId("route1.published_mesh")).toBe("stage-route1-published_mesh");
  });
});

describe("primaryRunForScroll", () => {
  it("ignores container records and selects the richest receipt for the declared scroll", () => {
    const selected = primaryRunForScroll([
      run({ run_id: "argus_runs", run_dir: "runs" }),
      run({
        run_id: "surface",
        run_dir: "runs/surface",
        target: "PHerc999/segment-a",
        highest_certified_stage: "CERTIFIED_SURFACE",
      }),
      run({
        run_id: "ink",
        run_dir: "runs/ink",
        target: "PHerc999/segment-b",
        highest_certified_stage: "CERTIFIED_INK_CANDIDATE",
      }),
    ], "PHerc999");
    expect(selected?.run_id).toBe("ink");
  });
});
