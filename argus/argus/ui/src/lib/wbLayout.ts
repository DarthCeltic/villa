
export type WbClass = "wide" | "medium" | "narrow";

export const WIDE_MIN = 960;
export const MEDIUM_MIN = 600;

export function classifyWidth(px: number): WbClass {
  if (!Number.isFinite(px)) return "narrow";
  if (px >= WIDE_MIN) return "wide";
  if (px >= MEDIUM_MIN) return "medium";
  return "narrow";
}

export const LAYOUT_VERSION = "v2";
export const STORAGE_PREFIX = "argus.workbench.";
export const LAYOUT_EVENT = "argus-wb-layout";

export function stateKey(cls: WbClass, key: string): string {
  return `${STORAGE_PREFIX}${LAYOUT_VERSION}.${cls}.${key}`;
}

export function isLegacyKey(fullKey: string): boolean {
  if (!fullKey.startsWith(STORAGE_PREFIX)) return false;
  const rest = fullKey.slice(STORAGE_PREFIX.length);
  return rest === "panel" || rest.startsWith("clam.");
}

export function isVersionedKey(fullKey: string): boolean {
  return fullKey.startsWith(`${STORAGE_PREFIX}${LAYOUT_VERSION}.`);
}

export function readState(cls: WbClass, key: string): string | null {
  try {
    return window.localStorage.getItem(stateKey(cls, key));
  } catch {
    return null;
  }
}

export function writeState(cls: WbClass, key: string, value: string): void {
  try {
    window.localStorage.setItem(stateKey(cls, key), value);
  } catch {
  }
}

function storedKeys(): string[] {
  try {
    const out: string[] = [];
    for (let i = 0; i < window.localStorage.length; i++) {
      const k = window.localStorage.key(i);
      if (k) out.push(k);
    }
    return out;
  } catch {
    return [];
  }
}

export type LayoutAction = { kind: "reset" } | { kind: "collapse"; cls: WbClass };

export function resetLayout(): number {
  let n = 0;
  for (const k of storedKeys()) {
    if (!(isVersionedKey(k) || isLegacyKey(k))) continue;
    try {
      window.localStorage.removeItem(k);
      n++;
    } catch {
    }
  }
  window.dispatchEvent(new CustomEvent<LayoutAction>(LAYOUT_EVENT, { detail: { kind: "reset" } }));
  return n;
}

export function collapseAll(cls: WbClass): number {
  let n = 0;
  const prefix = stateKey(cls, "clam.");
  for (const k of storedKeys()) {
    if (!k.startsWith(prefix)) continue;
    try {
      window.localStorage.setItem(k, "closed");
      n++;
    } catch {
    }
  }
  window.dispatchEvent(new CustomEvent<LayoutAction>(LAYOUT_EVENT, { detail: { kind: "collapse", cls } }));
  return n;
}
