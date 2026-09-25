import { describe, expect, it } from "vitest";
import {
  DEFAULT_DISPLAY,
  FULL_CLIP,
  brickQuery,
  centreOfBrick,
  chooseEdge,
  clampDisplay,
  clipPlanes,
  estimateLine,
  parseBrick,
  physicalOf,
  refusalFrom,
  sha256Hex,
  transferPoints,
  type BrickMeta,
} from "./volumeBrick";

const META: BrickMeta = {
  schema: "argus-volume-brick-v1", store: "s", level: "0", axis_order: "zyx", shape_zyx: [2, 3, 4], origin_voxel_zyx: [10, 20, 30],
  requested_centre_voxel_zyx: [11, 21, 32], origin_adjusted_to_fit: false, level_extent_zyx: [100, 100, 100], bytes: 24,
  estimated_gpu_bytes: 36, spacing_um_zyx: [4, 2, 1], spacing_status: "DECLARED_BY_STORE", origin_um_zyx: [40, 40, 30],
};

describe("what is asked of the brick service", () => {
  it("names the scroll, the store, the level, the centre and an explicit edge", () => {
    const q = new URLSearchParams(brickQuery({ store: "s", level: "1", scroll: "PHercFixture1", volume: "v", centre: [1, 2, 3], edge: 256 }));
    expect(q.get("scroll")).toBe("PHercFixture1");
    expect(q.get("centre")).toBe("1,2,3");
    expect(q.get("edge")).toBe("256");
    expect(q.get("volume")).toBe("v");
    expect(q.get("allow_partial")).toBeNull();
  });

  it("never asks for a bigger brick than the browser can upload, and larger only on request", () => {
    expect(chooseEdge({ maxTexture: 2048 })).toBe(256);
    expect(chooseEdge({ maxTexture: 2048, wantLarger: true })).toBe(512);
    expect(chooseEdge({ maxTexture: 128, wantLarger: true })).toBe(128);
    expect(chooseEdge({ maxTexture: null, wantLarger: true })).toBe(256);
    expect(chooseEdge({ maxTexture: 2048, levelExtent: [64, 300, 300] })).toBe(64);
  });
});

describe("what comes back", () => {
  it("refuses a body that is not the size its own shape declares", () => {
    expect(() => parseBrick(JSON.stringify(META), new ArrayBuffer(23))).toThrow(/needs 24/);
    expect(() => parseBrick(null, new ArrayBuffer(24))).toThrow(/no X-Argus-Brick-Meta/);
    const b = parseBrick(JSON.stringify(META), new ArrayBuffer(24));
    expect(b.data.length).toBe(24);
    expect(b.meta.shape_zyx).toEqual([2, 3, 4]);
  });

  it("verifies the declared hash of the bytes", async () => {
    const bytes = new Uint8Array([1, 2, 3]);
    expect(await sha256Hex(bytes)).toBe("039058c6f2c0cb492c533b0a4d14ef77cc0f78abccced5287d84a1a2011cfb81");
  });

  it("says which limit refused it, in the service's own words when it gave them", () => {
    expect(refusalFrom(400, { code: "MISSING_CHUNKS", why: "3 chunk(s) absent" })).toEqual({ code: "MISSING_CHUNKS", why: "3 chunk(s) absent" });
    expect(refusalFrom(409, { detail: "wrong identity" }).code).toBe("IDENTITY_MISMATCH");
    expect(refusalFrom(403, {}).code).toBe("OUTSIDE_DECLARED_ROOTS");
    expect(refusalFrom(423, {}).code).toBe("SEALED");
    expect(refusalFrom(422, { detail: "scroll required" }).why).toBe("scroll required");
  });

  it("reports physical position only when the store declared a spacing", () => {
    expect(physicalOf(META, [1, 2, 3])).toEqual([4, 4, 3]);
    expect(physicalOf({ ...META, spacing_um_zyx: null, spacing_status: "NOT_DECLARED" }, [1, 2, 3])).toBeNull();
    expect(centreOfBrick(META)).toEqual([11, 21, 32]);
    expect(estimateLine(META)).toContain("2 x 3 x 4 voxels");
  });
});

describe("display settings only change how the same bytes are drawn", () => {
  it("defaults to a fixed, identity contrast", () => {
    expect(DEFAULT_DISPLAY.windowLo).toBe(0);
    expect(DEFAULT_DISPLAY.windowHi).toBe(255);
    const t = transferPoints(DEFAULT_DISPLAY);
    expect(t.colour[0]).toEqual([0, 0, 0, 0]);
    expect(t.colour[1]).toEqual([255, 1, 1, 1]);
  });

  it("draws nothing below the threshold", () => {
    const t = transferPoints({ windowLo: 0, windowHi: 255, opacity: 0.8, threshold: 100 });
    const at = (v: number) => t.opacity.filter(([x]) => x <= v).slice(-1)[0]![1];
    expect(at(99)).toBe(0);
    expect(at(100)).toBe(0);
    expect(at(255)).toBe(0.8);
    expect(t.opacity.map((p) => p[0])).toEqual([...t.opacity.map((p) => p[0])].sort((a, b) => a - b));
  });

  it("keeps the window ordered and the opacity in range", () => {
    expect(clampDisplay({ windowLo: 300, windowHi: 10, opacity: 4, threshold: -3 })).toEqual({ windowLo: 254, windowHi: 255, opacity: 1, threshold: 0 });
  });

  it("builds clipping planes only for the faces that were moved in", () => {
    expect(clipPlanes(FULL_CLIP, [0, 0, 0], [10, 10, 10])).toEqual([]);
    const p = clipPlanes({ min: [0.2, 0, 0], max: [1, 0.5, 1] }, [0, 0, 0], [10, 20, 30]);
    expect(p).toHaveLength(2);
    expect(p[0]).toEqual({ origin: [2, 0, 0], normal: [1, 0, 0] });
    expect(p[1]).toEqual({ origin: [0, 10, 0], normal: [0, -1, 0] });
  });
});

import { applyMask, gridMismatch, hardwareTier } from "./volumeBrick";

describe("hardware tier", () => {
  it("keeps bricks small in software rendering and offers larger ones only on a recognised discrete GPU", () => {
    const sw = hardwareTier("ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero)), SwiftShader driver)", 2048);
    expect(sw).toMatchObject({ tier: "SOFTWARE_RENDERER", defaultEdge: 128, largestEdge: 256 });
    const gtx = hardwareTier("ANGLE (NVIDIA, NVIDIA GeForce GTX 1080 Direct3D11 vs_5_0 ps_5_0, D3D11)", 2048);
    expect(gtx).toMatchObject({ tier: "DISCRETE_GPU", defaultEdge: 256, largestEdge: 512, vramBudgetMiB: 1536 });
    expect(hardwareTier("ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11)", 2048)).toMatchObject({ tier: "UNKNOWN_GPU", largestEdge: 256 });
    expect(hardwareTier(null, null).tier).toBe("UNKNOWN_GPU");
    expect(hardwareTier("NVIDIA GeForce RTX 3080", 512).tier).toBe("UNKNOWN_GPU");
  });
  it("chooses the edge from the tier, the texture limit and the opt-in together", () => {
    const gtx = hardwareTier("NVIDIA GeForce GTX 1080", 2048);
    const sw = hardwareTier("SwiftShader", 2048);
    expect(chooseEdge({ maxTexture: 2048, tier: gtx })).toBe(256);
    expect(chooseEdge({ maxTexture: 2048, tier: gtx, wantLarger: true })).toBe(512);
    expect(chooseEdge({ maxTexture: 256, tier: gtx, wantLarger: true })).toBe(256);
    expect(chooseEdge({ maxTexture: 2048, tier: sw })).toBe(128);
    expect(chooseEdge({ maxTexture: 2048, tier: sw, wantLarger: true })).toBe(128);
    expect(chooseEdge({ maxTexture: 2048, tier: gtx, wantLarger: true, levelExtent: [100, 900, 900] })).toBe(100);
  });
});

describe("registered masks", () => {
  const ct: BrickMeta = { ...META, shape_zyx: [2, 2, 2], origin_voxel_zyx: [0, 0, 0], spacing_um_zyx: [9.0, 9.0, 9.0] };
  it("lays a mask over a brick only when level, box and spacing agree and no chunk is missing", () => {
    expect(gridMismatch(ct, { ...ct })).toBeNull();
    expect(gridMismatch(ct, { ...ct, level: "2" })).toContain("level 2");
    expect(gridMismatch(ct, { ...ct, origin_voxel_zyx: [1, 0, 0] })).toContain("different box");
    expect(gridMismatch(ct, { ...ct, shape_zyx: [2, 2, 3] })).toContain("different box");
    expect(gridMismatch(ct, { ...ct, spacing_um_zyx: [9.0, 9.0, 4.0] })).toContain("spacing");
    expect(gridMismatch(ct, { ...ct, spacing_um_zyx: null })).toContain("spacing");
    expect(gridMismatch(ct, { ...ct, missing_chunk_count: 1 })).toContain("missing");
  });
  it("hides voxels outside the mask on a copy, never in the brick, and says how many", () => {
    const data = Uint8Array.from([10, 20, 30, 40, 50, 60, 70, 80]);
    const mask = Uint8Array.from([255, 0, 1, 0, 0, 0, 255, 0]);
    const out = applyMask(data, mask);
    expect(Array.from(out.data)).toEqual([10, 0, 30, 0, 0, 0, 70, 0]);
    expect(out.hidden).toBe(5);
    expect(Array.from(data)).toEqual([10, 20, 30, 40, 50, 60, 70, 80]);
    expect(() => applyMask(data, new Uint8Array(3))).toThrow(/mask has 3 voxels/);
  });
});
