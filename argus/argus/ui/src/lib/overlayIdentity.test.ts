import { describe, expect, it } from "vitest";
import { checkOverlay, type OverlayLayer } from "./overlayIdentity";
import type { BrickMeta } from "./volumeBrick";

const BRICK: BrickMeta = {
  schema: "argus-volume-brick-v1", store: "s", level: "0", axis_order: "zyx", shape_zyx: [10, 10, 10], origin_voxel_zyx: [100, 100, 100],
  requested_centre_voxel_zyx: null, origin_adjusted_to_fit: false, level_extent_zyx: [1000, 1000, 1000], bytes: 1000, estimated_gpu_bytes: 1500,
  spacing_um_zyx: [9.0, 9.0, 9.0], spacing_status: "DECLARED_BY_STORE", origin_um_zyx: null,
};

const GOOD: OverlayLayer = {
  id: "mesh1", kind: "mesh", label: "Surface mesh", producer: "tifxyz import", physicalScroll: "PHercFixture1", sourceVolume: "V1", pitchUm: 9.0,
  transform: { kind: "identity_voxel_zyx" }, sha256: "a".repeat(64), status: "GEOMETRY_ONLY", roiVoxelZyx: { min: [90, 90, 90], max: [120, 120, 120] },
};

const check = (l: Partial<OverlayLayer>, scroll = "PHercFixture1", volume: string | null = "V1") => checkOverlay({ ...GOOD, ...l }, BRICK, scroll, volume);

describe("a layer is drawn only when its identity matches exactly", () => {
  it("accepts a layer that matches the scroll, volume, pitch, transform and region", () => {
    expect(check({})).toEqual({ accepted: true, reasons: [] });
  });

  it("refuses a wrong scroll and a wrong volume", () => {
    expect(check({ physicalScroll: "PHercFixture2" }).reasons[0]).toContain("not PHercFixture1");
    expect(check({ sourceVolume: "V2" }).reasons.join()).toContain("V2");
    expect(check({}, "PHercFixture1", null).accepted).toBe(false);
  });

  it("refuses an unresolved or non-identity transform instead of approximating it", () => {
    expect(check({ transform: { kind: "unresolved" } }).reasons.join()).toContain("cannot be applied exactly");
    expect(check({ transform: { kind: "other" } }).accepted).toBe(false);
  });

  it("refuses a pitch that differs, a missing pitch, a missing hash and a region elsewhere", () => {
    expect(check({ pitchUm: 2.4 }).reasons.join()).toContain("differs");
    expect(check({ pitchUm: null }).accepted).toBe(false);
    expect(check({ sha256: null }).accepted).toBe(false);
    expect(check({ roiVoxelZyx: { min: [500, 500, 500], max: [520, 520, 520] } }).reasons.join()).toContain("does not intersect");
  });

  it("never lets a prediction be called ink, letters or text", () => {
    const v = check({ kind: "prediction", label: "Ink prediction", status: "FIXTURE" });
    expect(v.accepted).toBe(false);
    expect(v.reasons.join()).toContain("never labelled ink");
    expect(check({ kind: "prediction", label: "Fixture model output", status: "FIXTURE" }).reasons.join()).toContain("no model-output contract");
    const contract = {
      artifact_kind: "MODEL_OUTPUT", provider: { id: "fixture-provider" }, exposure_state: "EXPOSURE_UNDECLARED", control_verdict: "CONTROL_NOT_RUN", qualification_state: "NOT_QUALIFIED",
      status_words: ["MODEL OUTPUT", "EXPOSURE UNDECLARED", "CONTROL NOT RUN", "NOT QUALIFIED"],
    };
    expect(check({ kind: "prediction", label: "Model output (fixture)", status: "FIXTURE", prediction: contract }).accepted).toBe(true);
  });
});
