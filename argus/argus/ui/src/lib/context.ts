import { useCallback, useMemo, useSyncExternalStore } from "react";
import { useSearchParams } from "react-router-dom";

export type ArgusMode = "guided" | "expert";
export const MODES: readonly ArgusMode[] = ["guided", "expert"];
export const MODE_KEY = "argus.mode";
export const DEFAULT_MODE: ArgusMode = "guided";

type ModeStorage = Pick<Storage, "getItem" | "setItem">;

function browserStorage(): ModeStorage | null {
  try {
    return typeof window !== "undefined" ? window.localStorage : null;
  } catch {
    return null;
  }
}

export function readMode(storage: ModeStorage | null = browserStorage()): ArgusMode {
  try {
    const raw = storage?.getItem(MODE_KEY);
    return raw === "guided" || raw === "expert" ? raw : DEFAULT_MODE;
  } catch {
    return DEFAULT_MODE;
  }
}

export function writeMode(mode: ArgusMode, storage: ModeStorage | null = browserStorage()): boolean {
  try {
    if (!storage) return false;
    storage.setItem(MODE_KEY, mode);
    return true;
  } catch {
    return false;
  }
}

let currentMode: ArgusMode | null = null;
const modeListeners = new Set<() => void>();

function modeSnapshot(): ArgusMode {
  if (currentMode === null) currentMode = readMode();
  return currentMode;
}

function subscribeMode(listener: () => void): () => void {
  modeListeners.add(listener);
  return () => modeListeners.delete(listener);
}

export function setMode(next: ArgusMode): void {
  currentMode = next;
  writeMode(next);
  try {
    if (typeof document !== "undefined") document.documentElement.dataset.argusMode = next;
  } catch {
  }
  modeListeners.forEach((l) => l());
}

export function useArgusMode(): ArgusMode {
  return useSyncExternalStore(subscribeMode, modeSnapshot, modeSnapshot);
}

export function plainReason(text: string | null | undefined): string {
  if (!text) return "";
  const m = /^([A-Z][A-Z0-9_]{3,}):\s+(.+)$/s.exec(text);
  if (!m) return text;
  const rest = m[2]!;
  return rest.charAt(0).toUpperCase() + rest.slice(1);
}

export function resetModeForTests(): void {
  currentMode = null;
}

export type ArgusContext = {
  scroll: string | null;
  run: string | null;
  layers: string[];
  task: string | null;
};

export const CONTEXT_KEYS = ["scroll", "run", "layers", "task"] as const;

export type ContextPatch = Partial<{
  scroll: string | null;
  run: string | null;
  layers: string[] | null;
  task: string | null;
}>;

export function useArgusContext() {
  const [params, setParams] = useSearchParams();

  const ctx: ArgusContext = useMemo(
    () => ({
      scroll: params.get("scroll") || null,
      run: params.get("run") || null,
      layers: (params.get("layers") || "").split(",").filter(Boolean),
      task: params.get("task") || null,
    }),
    [params],
  );

  const set = useCallback(
    (patch: ContextPatch, opts?: { replace?: boolean }) => {
      const next = new URLSearchParams(params);
      for (const [k, v] of Object.entries(patch)) {
        if (v === null || v === undefined || (Array.isArray(v) && v.length === 0)) {
          next.delete(k);
        } else {
          next.set(k, Array.isArray(v) ? v.join(",") : String(v));
        }
      }
      if ("scroll" in patch && patch.scroll !== ctx.scroll) {
        next.delete("run");
        next.delete("layers");
        next.delete("task");
      }
      setParams(next, { replace: opts?.replace ?? false });
    },
    [params, setParams, ctx.scroll],
  );

  const href = useCallback(
    (path: string, patch?: ContextPatch) => {
      const next = new URLSearchParams(params);
      for (const [k, v] of Object.entries(patch || {})) {
        if (v === null || v === undefined || (Array.isArray(v) && v.length === 0)) next.delete(k);
        else next.set(k, Array.isArray(v) ? v.join(",") : String(v));
      }
      const q = next.toString();
      return q ? `${path}?${q}` : path;
    },
    [params],
  );

  const mode = useSyncExternalStore(subscribeMode, modeSnapshot, modeSnapshot);

  return { ctx, set, href, mode, setMode };
}

export function requireScroll(ctx: ArgusContext): { ok: boolean; why: string } {
  if (ctx.scroll) return { ok: true, why: "" };
  return {
    ok: false,
    why: "No scroll is selected. Choose one from the shelf — this screen will not pick for you, "
      + "because a screen that picks teaches you that its choice is a recommendation.",
  };
}

export function contextIsCoherent(
  ctx: ArgusContext,
  runScroll: string | null | undefined,
): { coherent: boolean; why: string } {
  if (!ctx.run || !ctx.scroll || !runScroll) return { coherent: true, why: "" };
  if (runScroll === ctx.scroll) return { coherent: true, why: "" };
  return {
    coherent: false,
    why:
      `The selected scroll is ${ctx.scroll}, but the open run belongs to ${runScroll}. `
      + "Showing both at once invites a comparison between two different physical objects.",
  };
}
