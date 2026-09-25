import { describe, expect, it } from "vitest";
import { boundBrick, meshOverlay, notDrawn, parseTaskParam, pickStoreForTask, scrollMatches, taskLink, type TaskBinding, type TaskMesh } from "./taskBinding";
import { checkOverlay } from "./overlayIdentity";
import { brickQuery, type BrickMeta } from "./volumeBrick";

const BINDING: TaskBinding = {
  schema: "argus-workbench-task-binding-v1",
  task: { kind: "scroll_blocks", task_id: "PHB-fixture-A-R3", task_sha256: "a".repeat(64), queue_root_sha256: "b".repeat(64), review_state: "UNREVIEWED" },
  scroll: "PHercFixture1",
  volume: "fixture-volume-9um",
  level: "0",
  pitch_um: 9.0,
  coordinate_status: "RESOLVED",
  roi: { min_zyx: [100, 200, 300], max_zyx: [171, 255, 469], centre_zyx: [135.5, 227.5, 384.5], halo_voxels: 24, units: "level-0 voxels" },
  geometry: { state: "EXACT_VOLUME_GEOMETRY_PASS", watermarked: false, orientation: "ORIENTATION_UNCONFIRMED", target_gate: { verdict: "PERMITTED", operation_class: "GEOMETRY_ONLY", registry_sha256: "9f3d" } },
  layers: [
    { id: "raw_ct", kind: "raw_ct", label: "Raw CT", permitted: true, status: "DISPLAY_ONLY", why: "" },
    { id: "mesh", kind: "mesh", label: "Surface mesh", permitted: true, status: "GEOMETRY_ONLY", why: "" },
    { id: "prediction", kind: "prediction", label: "Model outputs", permitted: false, status: "WITHHELD_FROM_BLINDED_TASK", why: "blinded" },
    { id: "cavity_mask", kind: "cavity_mask", label: "Cavity inspection", permitted: false, status: "UNAVAILABLE", why: "none registered" },
    { id: "between_wrap_mask", kind: "between_wrap_mask", label: "Between-wrap inspection", permitted: false, status: "UNAVAILABLE", why: "none registered" },
  ],
  coordinate_system: {
    positioning_system: "TIFXYZ_MESH_LEVEL0_XYZ",
    positioning_statement: "positioned from this task's tifxyz/mesh level-0 XYZ coordinates",
    fine_grid_screening_indices: "SEPARATE_COORDINATE_SYSTEM_NOT_USED_TO_POSITION_THE_VIEWER",
    fine_grid_statement: "separate coordinate system, not used to position this viewer",
  },
  fiber_capabilities: { rows: [], rule: null, contracts: [] },
  claims: ["GEOMETRY_ONLY"],
};

describe("the task reference in the URL", () => {
  it("round-trips through the link and rejects anything that is not kind:id", () => {
    const link = taskLink("PHercFixture1", BINDING.task.kind, BINDING.task.task_id);
    const q = new URLSearchParams(link.split("?")[1] ?? "");
    expect(q.get("scroll")).toBe("PHercFixture1");
    expect(parseTaskParam(q.get("task"))).toEqual({ kind: "scroll_blocks", taskId: "PHB-fixture-A-R3" });
    expect(q.get("mode")).toBe("volume");
    for (const bad of [null, "", "nokind", ":x", "k:", "../x:y", "k:../../etc/passwd", "k:a b"]) expect(parseTaskParam(bad)).toBeNull();
  });
});

describe("which store may serve a task", () => {
  const stores = [
    { store: "old.zarr", volume_id: "fixture-volume-2um" },
    { store: "exact.zarr", volume_id: "fixture-volume-9um" },
  ];
  it("picks the store that declares exactly the task's volume", () => {
    expect(pickStoreForTask(stores, BINDING)).toEqual({ store: "exact.zarr" });
    expect(pickStoreForTask([{ store: "p.zarr", volume_id: "fixture-volume-2um-masked.zarr" }], { ...BINDING, volume: "fixture-volume-2um-masked" })).toEqual({ store: "p.zarr" });
  });
  it("never substitutes another volume or a store with no declared volume", () => {
    const r = pickStoreForTask([stores[0]!, { store: "anon.zarr", volume_id: null }], BINDING);
    expect(r).toMatchObject({ code: "NO_STORE_HOLDS_THIS_VOLUME" });
    expect(pickStoreForTask(stores, { ...BINDING, volume: null })).toMatchObject({ code: "TASK_DECLARES_NO_VOLUME" });
  });
  it("refuses to switch scrolls on its own", () => {
    expect(scrollMatches("PHercFixture1", BINDING)).toBeNull();
    expect(scrollMatches("PHercFixture2", BINDING)).toMatchObject({ code: "TASK_IS_FOR_ANOTHER_SCROLL" });
    expect(scrollMatches(null, BINDING)).toMatchObject({ code: "TASK_IS_FOR_ANOTHER_SCROLL" });
  });
});

describe("the box a task opens", () => {
  const extent: [number, number, number] = [1000, 1000, 1000];
  it("is the ROI plus the declared halo, in the level's own voxels", () => {
    const b = boundBrick(BINDING, extent, true);
    expect(b).toEqual({ origin: [76, 176, 276], shape: [119, 103, 217], clamped: false });
    const exact = boundBrick(BINDING, extent, false);
    expect(exact).toEqual({ origin: [100, 200, 300], shape: [71, 55, 169], clamped: false });
  });
  it("is clamped to the volume rather than reading outside it, and says so", () => {
    const b = boundBrick({ ...BINDING, roi: { ...BINDING.roi!, min_zyx: [5, 200, 300] } }, extent, true);
    expect(b).toMatchObject({ origin: [0, 176, 276], clamped: true });
  });
  it("refuses an unresolved location and a region outside the volume", () => {
    expect(boundBrick({ ...BINDING, coordinate_status: "UNRESOLVED_NONFINITE", roi: undefined, refusal: { code: "COORDINATE_UNRESOLVED", why: "hole" } }, extent, true)).toMatchObject({ code: "COORDINATE_UNRESOLVED" });
    expect(boundBrick(BINDING, [50, 50, 50], true)).toMatchObject({ code: "ROI_OUTSIDE_VOLUME" });
  });
  it("is requested as origin and shape, with a budget sized to the box, not a centre and an edge", () => {
    const b = boundBrick(BINDING, extent, true);
    if ("code" in b) throw new Error("unexpected refusal");
    const q = new URLSearchParams(brickQuery({ store: "s", level: "0", scroll: "PHercFixture1", volume: BINDING.volume, centre: [0, 0, 0], edge: 256, origin: b.origin, shape: b.shape }));
    expect(q.get("origin")).toBe("76,176,276");
    expect(q.get("shape")).toBe("119,103,217");
    expect(q.get("centre")).toBeNull();
    expect(q.get("edge")).toBeNull();
    expect(Number(q.get("budget_mib"))).toBeGreaterThanOrEqual(5);
  });
});

describe("the task's mesh as an overlay", () => {
  const brick: BrickMeta = {
    schema: "argus-volume-brick-v1", store: "s", level: "0", axis_order: "zyx", shape_zyx: [119, 103, 217], origin_voxel_zyx: [76, 176, 276], requested_centre_voxel_zyx: null,
    origin_adjusted_to_fit: false, level_extent_zyx: [1000, 1000, 1000], bytes: 1, estimated_gpu_bytes: 1, spacing_um_zyx: [9.0, 9.0, 9.0], spacing_status: "DECLARED_BY_STORE", origin_um_zyx: null,
  };
  const mesh: TaskMesh = { task_id: BINDING.task.task_id, scroll: "PHercFixture1", volume: BINDING.volume, pitch_um: 9.0, sha256: "c".repeat(64), status: "GEOMETRY_ONLY", vertices: [150, 220, 350], faces: [], n_triangles: 0, quads_dropped_by_triangle_budget: 0 };

  it("is accepted on the exact volume at level 0 and refused at any other level or volume", () => {
    const layer = meshOverlay(BINDING, mesh);
    expect(checkOverlay(layer, brick, "PHercFixture1", BINDING.volume)).toEqual({ accepted: true, reasons: [] });
    expect(checkOverlay(layer, { ...brick, level: "2" }, "PHercFixture1", BINDING.volume).reasons.join()).toContain("level 0 voxels but this brick is level 2");
    expect(checkOverlay(layer, brick, "PHercFixture1", "fixture-volume-2um").accepted).toBe(false);
    expect(checkOverlay(layer, brick, "PHercFixture2", BINDING.volume).accepted).toBe(false);
  });
  it("is refused where the brick does not reach the mesh's region", () => {
    const away = { ...brick, origin_voxel_zyx: [600, 600, 600] as [number, number, number] };
    expect(checkOverlay(meshOverlay(BINDING, mesh), away, "PHercFixture1", BINDING.volume).reasons.join()).toContain("does not intersect");
  });
});

describe("what the task itself says may not be drawn", () => {
  it("lists model outputs and the unregistered between-wrap view with their reasons, and never the raw CT or the permitted mesh", () => {
    const rows = notDrawn(BINDING);
    expect(rows.map((r) => r.id)).toEqual(["prediction", "cavity_mask", "between_wrap_mask"]);
    expect(rows[0]!.status).toBe("WITHHELD_FROM_BLINDED_TASK");
  });
});
