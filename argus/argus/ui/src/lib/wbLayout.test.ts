import { beforeEach, describe, expect, it } from "vitest";
import {
  LAYOUT_EVENT,
  LAYOUT_VERSION,
  MEDIUM_MIN,
  WIDE_MIN,
  classifyWidth,
  collapseAll,
  isLegacyKey,
  isVersionedKey,
  readState,
  resetLayout,
  stateKey,
  writeState,
  type LayoutAction,
} from "./wbLayout";

class MemoryStorage {
  private m = new Map<string, string>();
  get length() {
    return this.m.size;
  }
  key(i: number) {
    return [...this.m.keys()][i] ?? null;
  }
  getItem(k: string) {
    return this.m.has(k) ? this.m.get(k)! : null;
  }
  setItem(k: string, v: string) {
    this.m.set(k, String(v));
  }
  removeItem(k: string) {
    this.m.delete(k);
  }
}

const events: LayoutAction[] = [];

beforeEach(() => {
  events.length = 0;
  const target = new EventTarget();
  (globalThis as unknown as { window: unknown }).window = {
    localStorage: new MemoryStorage(),
    dispatchEvent: (e: CustomEvent<LayoutAction>) => {
      events.push(e.detail);
      return target.dispatchEvent(new Event(LAYOUT_EVENT));
    },
  };
  (globalThis as unknown as { CustomEvent: unknown }).CustomEvent =
    (globalThis as { CustomEvent?: unknown }).CustomEvent ??
    class {
      detail: unknown;
      constructor(_t: string, init: { detail: unknown }) {
        this.detail = init.detail;
      }
    };
});

describe("the three width classes", () => {
  it("are decided by the Workbench's own width, at 600 and 960", () => {
    expect(classifyWidth(1400)).toBe("wide");
    expect(classifyWidth(WIDE_MIN)).toBe("wide");
    expect(classifyWidth(WIDE_MIN - 1)).toBe("medium");
    expect(classifyWidth(MEDIUM_MIN)).toBe("medium");
    expect(classifyWidth(MEDIUM_MIN - 1)).toBe("narrow");
    expect(classifyWidth(356)).toBe("narrow");
    expect(classifyWidth(Number.NaN)).toBe("narrow");
  });
});

describe("remembered choices are per class and versioned", () => {
  it("keeps a desktop-open panel from opening on a phone", () => {
    writeState("wide", "panel", "inspector");
    expect(readState("wide", "panel")).toBe("inspector");
    expect(readState("narrow", "panel")).toBeNull();
    expect(readState("medium", "panel")).toBeNull();
    expect(stateKey("narrow", "panel")).toBe(`argus.workbench.${LAYOUT_VERSION}.narrow.panel`);
  });
  it("never reads the unversioned keys the older layout wrote", () => {
    const w = (globalThis as unknown as { window: { localStorage: MemoryStorage } }).window;
    w.localStorage.setItem("argus.workbench.panel", "inspector");
    w.localStorage.setItem("argus.workbench.clam.windings", "open");
    for (const cls of ["wide", "medium", "narrow"] as const) {
      expect(readState(cls, "panel")).toBeNull();
      expect(readState(cls, "clam.windings")).toBeNull();
    }
    expect(isLegacyKey("argus.workbench.panel")).toBe(true);
    expect(isLegacyKey("argus.workbench.clam.windings")).toBe(true);
    expect(isLegacyKey(stateKey("wide", "panel"))).toBe(false);
    expect(isVersionedKey(stateKey("wide", "panel"))).toBe(true);
    expect(isVersionedKey("argus.workbench.panel")).toBe(false);
  });
});

describe("collapse all and reset", () => {
  it("collapse shuts this class's sections only, keeps the panel, and announces itself", () => {
    writeState("narrow", "panel", "inspector");
    writeState("narrow", "clam.windings", "open");
    writeState("narrow", "clam.blender", "open");
    writeState("wide", "clam.windings", "open");
    expect(collapseAll("narrow")).toBe(2);
    expect(readState("narrow", "clam.windings")).toBe("closed");
    expect(readState("narrow", "clam.blender")).toBe("closed");
    expect(readState("narrow", "panel")).toBe("inspector");
    expect(readState("wide", "clam.windings")).toBe("open");
    expect(events.at(-1)).toEqual({ kind: "collapse", cls: "narrow" });
  });
  it("reset forgets every class and the older layout's keys, and leaves unrelated storage alone", () => {
    const w = (globalThis as unknown as { window: { localStorage: MemoryStorage } }).window;
    w.localStorage.setItem("argus.workbench.panel", "layers");
    w.localStorage.setItem("argus.workbench.clam.pipeline", "open");
    w.localStorage.setItem("argus.display.scale", "1.2");
    writeState("wide", "panel", "inspector");
    writeState("medium", "clam.review", "open");
    expect(resetLayout()).toBe(4);
    expect(readState("wide", "panel")).toBeNull();
    expect(readState("medium", "clam.review")).toBeNull();
    expect(w.localStorage.getItem("argus.workbench.panel")).toBeNull();
    expect(w.localStorage.getItem("argus.display.scale")).toBe("1.2");
    expect(events.at(-1)).toEqual({ kind: "reset" });
  });
});
