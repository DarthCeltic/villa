import { describe, expect, it } from "vitest";
import { buildUniverse } from "./ShelfUniverse";

const polled = <T,>(data: T | null) => ({ data, failure: null, loading: false, refresh: () => undefined }) as never;
const ids = Array.from({ length: 39 }, (_, i) => `PHerc${String(1000 + i)}`);
const u = buildUniverse(
  polled({ schema: "argus-eligible-targets-unconfigured-v1", available: false, sets: {}, targets: [], why_v2: "No official target registry is installed in this ARGUS home. Eligibility is unknown, not empty.", next_action: { label: "Install or refresh the official target registry" } }),
  polled({ canonical: ids, placeholders: [], aliases: {}, confusable_pairs: [] }),
  polled({ scrolls: [{ scroll: ids[0], display: ids[0] }] }),
  polled({ scrolls: [] }),
  [],
  polled({ boards: {} }),
  polled({ rows: [] }),
  polled({ scrolls: [] }),
);

describe("unavailable target registry", () => {
  it("keeps all 39 known identities visible", () => {
    expect(u.scrolls).toHaveLength(39);
    expect(u.counts.registered.value).toBe(39);
  });
  it("reports prize counts as unknown (null), never zero", () => {
    for (const k of ["firstLetters", "grandPrize", "controlsAndDev", "firstLettersWithLocalMaterial", "firstLettersWithBytesHere", "grandPrizeWithCertifiedInk"] as const) {
      expect(u.counts[k].value, k).toBeNull();
    }
  });
  it("files no scroll as a control, development or non-prize scroll", () => {
    expect(u.scrolls.every((s) => s.lane === "UNCLASSIFIED")).toBe(true);
    expect(u.collections.map((c) => c.lane)).toEqual(["UNCLASSIFIED"]);
    expect(u.collections[0]?.count.value).toBe(39);
  });
  it("never says acquisition is undeclared or that a scroll is ineligible", () => {
    const text = JSON.stringify({ c: u.collections.map((c) => [c.title, c.why, c.note]), f: u.scrolls.map((s) => s.familyLine) });
    expect(text).not.toMatch(/no acquisition declared/i);
    expect(text).not.toMatch(/not prize.eligible|Neither is prize-eligible/i);
    expect(text).not.toMatch(/Controls 39|0 of 0/);
    expect(text).toMatch(/unknown/i);
  });
});
