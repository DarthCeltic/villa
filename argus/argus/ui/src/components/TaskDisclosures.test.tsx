import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { CoordinateDisclosure, FiberMatrixPanel, ModelOutputStatus } from "./TaskDisclosures";
import { checkOverlay, type OverlayLayer, type PredictionContract } from "../lib/overlayIdentity";
import type { BrickMeta } from "../lib/volumeBrick";

const text = (html: string) => html.replace(/<[^>]+>/g, "").replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/\s+/g, " ");

describe("coordinate disclosure", () => {
  const system = {
    positioning_system: "TIFXYZ_MESH_LEVEL0_XYZ",
    positioning_statement: "The camera and region box are positioned from this task's tifxyz/mesh level-0 XYZ coordinates (voxels of the exact volume): the mesh block's four corners and centre.",
    fine_grid_screening_indices: "SEPARATE_COORDINATE_SYSTEM_NOT_USED_TO_POSITION_THE_VIEWER",
    fine_grid_statement: "The task's fine-grid screening indices are a separate coordinate system. They are not used to position this viewer, and they are not part of what this view is sent.",
  };
  it("says the mesh level-0 XYZ coordinates place the view, and that fine-grid indices are separate and unused", () => {
    const html = renderToStaticMarkup(<CoordinateDisclosure system={system} />);
    const t = text(html);
    expect(t).toContain("tifxyz/mesh level-0 XYZ coordinates");
    expect(t).toContain("fine-grid screening indices are a separate coordinate system");
    expect(t).toContain("not used to position this viewer");
    expect(html).toContain('data-positioning="TIFXYZ_MESH_LEVEL0_XYZ"');
  });
});

describe("fiber capability matrix", () => {
  const matrix = {
    rows: [
      { capability: "presence", state: "UNAVAILABLE_FOR_TASK", reason: "no presence artifact is bound to this task's volume and region", satisfied_only_by: "fiber_presence", never_satisfied_by: [] },
      { capability: "hv_class", state: "UNAVAILABLE_FOR_TASK", reason: "no hv_class artifact is bound", satisfied_only_by: "fiber_hv_class", never_satisfied_by: [] },
      { capability: "direction", state: "PENDING_UPSTREAM_ARTIFACT", reason: "fiber direction model: not published", satisfied_only_by: "fiber_direction", never_satisfied_by: [] },
      { capability: "adjacency", state: "UNKNOWN_LEGACY", reason: "`adjacent_branches` ABSENT: NOT 'no adjacent links'", satisfied_only_by: "adjacency_declaration", never_satisfied_by: [] },
      { capability: "tracing", state: "UNAVAILABLE_FOR_TASK", reason: "no tracing artifact is bound", satisfied_only_by: "fiber_tracing", never_satisfied_by: [] },
    ],
    rule: "a presence mask or an H/V class map can NEVER satisfy the direction or tracing gate",
    contracts: [{ file: "fiber_schema_contract.json", sha256: "a".repeat(64) }],
  } as const;
  it("shows five separate rows with the server's states, UNKNOWN_LEGACY and PENDING_UPSTREAM_ARTIFACT included", () => {
    const html = renderToStaticMarkup(<FiberMatrixPanel matrix={matrix as never} />);
    const t = text(html);
    for (const [cap, state] of [["presence", "UNAVAILABLE_FOR_TASK"], ["hv_class", "UNAVAILABLE_FOR_TASK"], ["direction", "PENDING_UPSTREAM_ARTIFACT"], ["adjacency", "UNKNOWN_LEGACY"], ["tracing", "UNAVAILABLE_FOR_TASK"]] as const) {
      expect(html).toContain(`data-control="wb.volume3d.fiber.${cap}" data-state="${state}"`);
    }
    expect(t).toContain("Fiber presence");
    expect(t).toContain("H/V classification");
    expect(t).toContain("One capability never satisfies another.");
    expect(t).toContain("NEVER satisfy the direction or tracing gate");
    expect(t).toContain("NOT 'no adjacent links'");
    expect(t).not.toMatch(/\bfiber layer\b/i);
  });
});

describe("model output status and the overlay contract", () => {
  const contract: PredictionContract = {
    artifact_kind: "MODEL_OUTPUT",
    provider: { id: "synthetic-provider" },
    exposure_state: "EXPOSURE_DECLARED",
    control_verdict: "CONTROL_DECLARED",
    qualification_state: "NOT_QUALIFIED",
    status_words: ["MODEL OUTPUT", "EXPOSURE DECLARED", "CONTROL DECLARED", "NOT QUALIFIED"],
  };
  const brick: BrickMeta = {
    schema: "argus-volume-brick-v1", store: "s", level: "0", axis_order: "zyx", shape_zyx: [10, 10, 10], origin_voxel_zyx: [100, 100, 100], requested_centre_voxel_zyx: null,
    origin_adjusted_to_fit: false, level_extent_zyx: [1000, 1000, 1000], bytes: 1000, estimated_gpu_bytes: 1500, spacing_um_zyx: [8, 8, 8],
    spacing_status: "DECLARED_BY_STORE", origin_um_zyx: null,
  };
  const layer = (over: Partial<OverlayLayer> = {}): OverlayLayer => ({
    id: "p", kind: "prediction", label: "Model output (synthetic)", producer: "synthetic-provider", physicalScroll: "PHercFixture1", sourceVolume: "V1", pitchUm: 8,
    transform: { kind: "identity_voxel_zyx" }, sha256: "b".repeat(64), status: "CONTROL_DECLARED", level: "0", prediction: contract, ...over,
  });

  it("renders exactly the declared status words, and never the word ink", () => {
    const html = renderToStaticMarkup(<ModelOutputStatus overlay={{ label: "Model output (synthetic)", provider: { id: "synthetic-provider" }, status_words: contract.status_words }} />);
    const t = text(html);
    expect(t).toContain("MODEL OUTPUT / EXPOSURE DECLARED / CONTROL DECLARED / NOT QUALIFIED");
    expect(t).not.toMatch(/\bink\b/i);
    expect(t).toContain("does not draw model output");
  });

  it("accepts a complete synthetic overlay and refuses every incomplete one", () => {
    expect(checkOverlay(layer(), brick, "PHercFixture1", "V1")).toEqual({ accepted: true, reasons: [] });
    expect(checkOverlay(layer({ prediction: undefined }), brick, "PHercFixture1", "V1").reasons.join()).toContain("no model-output contract");
    expect(checkOverlay(layer({ prediction: { ...contract, artifact_kind: "INK_DETECTION" } }), brick, "PHercFixture1", "V1").reasons.join()).toContain("not MODEL_OUTPUT");
    expect(checkOverlay(layer({ prediction: { ...contract, control_verdict: "" } }), brick, "PHercFixture1", "V1").reasons.join()).toContain("control verdict");
    expect(checkOverlay(layer({ prediction: { ...contract, exposure_state: "" } }), brick, "PHercFixture1", "V1").reasons.join()).toContain("exposure state");
    expect(checkOverlay(layer({ prediction: { ...contract, provider: { id: "" } } }), brick, "PHercFixture1", "V1").reasons.join()).toContain("names no provider");
    expect(checkOverlay(layer({ prediction: { ...contract, status_words: ["MODEL OUTPUT", "EXPOSURE DECLARED", "CONTROL DECLARED"] } }), brick, "PHercFixture1", "V1").reasons.join()).toContain("NOT QUALIFIED");
    expect(checkOverlay(layer({ label: "Ink prediction" }), brick, "PHercFixture1", "V1").reasons.join()).toContain("never labelled ink");
  });
});
