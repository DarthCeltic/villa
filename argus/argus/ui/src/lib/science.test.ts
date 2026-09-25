import { describe, expect, it } from "vitest";
import {
  REVIEW_CHOICES,
  answerParams,
  attachHeadline,
  attachParams,
  geometryCounts,
  missingRequired,
  mountTone,
  mountWords,
  parseQueueLink,
  pitchRange,
  promotionWords,
  reviewTotals,
  selectionKey,
  statusCounts,
  type SealedTask,
} from "./science";

describe("computed collection facts", () => {
  const pieces = [2.5, 3.25, 2.0, 3.0].map((v) => ({ render_um_per_px: v }));

  it("derives the pitch range from the pieces instead of typing it", () => {
    expect(pitchRange(pieces)?.text).toBe("2.00–3.25 µm/px");
    expect(pitchRange([{ render_um_per_px: 8 }, { render_um_per_px: 12.5 }])?.text).toBe("8.00–12.50 µm/px");
    expect(pitchRange([])).toBeNull();
    expect(pitchRange([{ render_um_per_px: Number.NaN }])).toBeNull();
  });

  it("counts geometry pass and uncertain from the state names", () => {
    const states = [
      "EXACT_VOLUME_GEOMETRY_PASS",
      "EXACT_VOLUME_GEOMETRY_PASS",
      "EXACT_VOLUME_GEOMETRY_UNCERTAIN",
      "EXACT_VOLUME_GEOMETRY_PASS",
    ].map((geometry_state) => ({ geometry_state }));
    expect(geometryCounts(states)).toEqual({ pass: 3, uncertain: 1 });
  });

  it("totals review-priority regions per piece", () => {
    const ps = Array.from({ length: 4 }, () => ({ regions: new Array(3).fill(0), review_priority_count: 3 }));
    expect(reviewTotals(ps)).toBe(12);
    expect(reviewTotals([{ regions: [], review_priority_count: 3 }])).toBe(3);
  });
});

describe("attach plan helpers", () => {
  it("sends only a manifest id and a scroll: there is no path to type", () => {
    const s = { manifest: "fixture_manifest", scroll: "PHercFixture1" };
    expect(attachParams(s)).toEqual(s);
    expect(Object.keys(attachParams(s) ?? {}).sort()).toEqual(["manifest", "scroll"]);
    expect(attachParams({ manifest: "x", scroll: null })).toBeNull();
    expect(attachParams(null)).toBeNull();
    expect(selectionKey(s)).toBe("fixture_manifest|PHercFixture1");
  });

  it("counts every status and says plainly what was and was not verified", () => {
    const c = statusCounts([
      { status: "MOUNTED_VERIFIED" },
      { status: "NOT_MOUNTED" },
      { status: "NOT_MOUNTED" },
      { status: "HASH_MISMATCH" },
    ]);
    expect(c).toMatchObject({ MOUNTED_VERIFIED: 1, NOT_MOUNTED: 2, HASH_MISMATCH: 1, IDENTITY_MISMATCH: 0 });
    expect(attachHeadline({ MOUNTED_VERIFIED: 7, NOT_MOUNTED: 0 })).toBe("all 7 private files are mounted and hash-verified");
    expect(attachHeadline({ MOUNTED_VERIFIED: 0, NOT_MOUNTED: 9 })).toBe("none of 9 private files is verified on this host");
    expect(attachHeadline({ MOUNTED_VERIFIED: 5, NOT_MOUNTED: 1 })).toBe("5 of 6 private files are mounted and hash-verified");
    expect(attachHeadline(undefined)).toBe("not planned yet");
  });

  it("never dresses a mismatch or a refusal as a pass", () => {
    expect(mountTone("MOUNTED_VERIFIED")).toBe("ok");
    expect(mountTone("HASH_MISMATCH")).toBe("bad");
    expect(mountTone("IDENTITY_MISMATCH")).toBe("bad");
    expect(mountTone("NOT_MOUNTED")).toBe("warn");
    expect(mountTone("UNVERIFIED_TIME_BUDGET")).toBe("warn");
    expect(mountWords("NOT_MOUNTED")).toBe("not mounted");
  });
});

describe("blinded review helpers", () => {
  it("offers exactly the four choices", () => {
    expect([...REVIEW_CHOICES]).toEqual(["LIKELY_SIGNAL", "LIKELY_STRUCTURE", "UNCERTAIN", "REFUSE"]);
  });

  it("builds answer params that cannot carry a session, a hash or a model class", () => {
    const p = answerParams("scroll_blocks", "PHB-1-R1", "LIKELY_SIGNAL", { id: "Alice", cls: "COMMUNITY" }, true, 0.6, 12.34);
    expect(p).toEqual({
      kind: "scroll_blocks",
      task_id: "PHB-1-R1",
      value: "LIKELY_SIGNAL",
      reviewer_id: "Alice",
      reviewer_class: "COMMUNITY",
      images_viewed: true,
      confidence: 0.6,
      duration_s: 12.3,
    });
    expect(Object.keys(p)).not.toContain("reviewer_session");
    expect(Object.keys(p)).not.toContain("approved_plan_sha256");
    expect(answerParams("k", "t", "REFUSE", { id: "A", cls: "COMMUNITY" }, false, 0.3, -5).duration_s).toBe(0);
  });

  it("lists the required layers that are not hash-verified", () => {
    const layer = (name: string, status: string) => ({
      name,
      role: "RAW_LAYER",
      label: "",
      derived: false,
      sha256: "a".repeat(64),
      url: "/x",
      mount: { status, expected_sha256: "a".repeat(64) },
    });
    const task = {
      required_layers: ["raw.png", "comp.png"],
      layers: [layer("raw.png", "MOUNTED_VERIFIED"), layer("comp.png", "NOT_MOUNTED"), layer("extra.png", "NOT_MOUNTED")],
    } as unknown as SealedTask;
    expect(missingRequired(task).map((l) => l.name)).toEqual(["comp.png"]);
  });

  it("reads a deep link into a queue and a task", () => {
    expect(parseQueueLink("?queue=fixture_queue&task=FX-1")).toEqual({ queue: "fixture_queue", task: "FX-1" });
    expect(parseQueueLink("?queue=scroll_blocks")).toEqual({ queue: "scroll_blocks", task: null });
    expect(parseQueueLink("")).toBeNull();
  });

  it("says what a promotion is and is not", () => {
    expect(promotionWords("PROMOTABLE")).toContain("worth a closer look");
    expect(promotionWords("NOT_PROMOTABLE")).toBe("not promoted");
    expect(promotionWords("WITHHELD_UNTIL_YOU_ANSWER")).toBe("withheld until you answer");
  });
});
