import { describe, expect, it, beforeEach } from "vitest";
import { DEFAULT_MODE, MODE_KEY, plainReason, readMode, resetModeForTests, setMode, writeMode } from "./context";

function stubStorage(initial: Record<string, string> = {}) {
  const data = { ...initial };
  return {
    data,
    getItem: (k: string): string | null => data[k] ?? null,
    setItem: (k: string, v: string) => { data[k] = v; },
  };
}

describe("the guided / expert mode", () => {
  beforeEach(() => resetModeForTests());

  it("defaults to guided when nothing is stored", () => {
    expect(DEFAULT_MODE).toBe("guided");
    expect(readMode(stubStorage())).toBe("guided");
  });

  it("reads back what was written", () => {
    const store = stubStorage();
    expect(writeMode("expert", store)).toBe(true);
    expect(store.data[MODE_KEY]).toBe("expert");
    expect(readMode(store)).toBe("expert");
  });

  it("ignores a stored value that is not a mode", () => {
    expect(readMode(stubStorage({ [MODE_KEY]: "wizard" }))).toBe("guided");
  });

  it("survives storage that throws or is absent", () => {
    const blocked = {
      getItem: () => { throw new Error("blocked"); },
      setItem: () => { throw new Error("blocked"); },
    };
    expect(readMode(blocked)).toBe("guided");
    expect(writeMode("expert", blocked)).toBe(false);
    expect(readMode(null)).toBe("guided");
    expect(writeMode("expert", null)).toBe(false);
  });

  it("setMode does not throw without a browser and changes the in-memory mode", () => {
    expect(() => setMode("expert")).not.toThrow();
  });
});

describe("plainReason (Guided view reads the sentence, not the code)", () => {
  it("drops a leading machine code and capitalises the sentence", () => {
    expect(plainReason("ORIENTATION_UNCONFIRMED: no independent orientation evidence")).toBe(
      "No independent orientation evidence",
    );
  });
  it("leaves ordinary prose, short words and empty input alone", () => {
    expect(plainReason("no topology decision is recorded")).toBe("no topology decision is recorded");
    expect(plainReason("NOTE: x")).toBe("x".toUpperCase());
    expect(plainReason("CT: missing")).toBe("CT: missing");
    expect(plainReason(null)).toBe("");
  });
});
