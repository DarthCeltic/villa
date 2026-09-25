import { describe, expect, it } from "vitest";
import {
  callableCapabilities,
  CANDIDATE_PLAN_ACTION,
  lifecycleTone,
  type ProviderRecord,
  rowsWithPlanAction,
  stateLabel,
} from "./ProviderPlanForm";

function row(id: string, over: Partial<ProviderRecord> = {}): ProviderRecord {
  return { id, stage: "s", role: "r", revision: "rev", lifecycle: "L", ...over };
}

describe("provider registry rows in the plan form", () => {
  it("offers every capability provider.invoke can run, including vesuvius.predict, and never a ledger-only one", () => {
    const capabilities = callableCapabilities({
      capability_ledger: {
        geometry: { callable: ["vc_tifxyz"] },
        prediction: { callable: ["vesuvius.predict"] },
        lasagna_maxflow: { callable: [] },
        upstream_commit: "0000000" as unknown as { callable?: string[] },
      },
    });
    expect(capabilities).toEqual(["vc_tifxyz", "vesuvius.predict"]);
    expect(capabilities).not.toContain("vc_lasagna_maxflow_graph");
  });

  it("lists only rows that name a plan action", () => {
    const rows = rowsWithPlanAction([
      row("a", { plan_action: CANDIDATE_PLAN_ACTION }),
      row("b", { plan_action: "provider.invoke" }),
      row("c", { plan_action: null }),
      row("d"),
    ]);
    expect(rows.map((r) => r.id)).toEqual(["a", "b"]);
    expect(rowsWithPlanAction(undefined)).toEqual([]);
  });

  it("never paints an executable or plan-only row as a pass", () => {
    expect(lifecycleTone("BLOCKED")).toBe("bad");
    expect(lifecycleTone("PLAN_ONLY")).toBe("warn");
    expect(lifecycleTone("RESEARCH_ONLY")).toBe("warn");
    expect(lifecycleTone("GATED_EXECUTABLE")).toBe("info");
    expect(lifecycleTone("EXECUTABLE")).toBe("info");
    expect(lifecycleTone("CATALOG_ONLY")).toBe("idle");
    expect(lifecycleTone(undefined)).toBe("idle");
    expect(["GATED_EXECUTABLE", "EXECUTABLE", "PLAN_ONLY", "BLOCKED"].map(lifecycleTone)).not.toContain("ok");
  });

  it("says when a server did not report a state instead of guessing one", () => {
    expect(stateLabel("PLAN_ONLY")).toBe("plan only");
    expect(stateLabel(undefined)).toBe("not reported");
  });
});
