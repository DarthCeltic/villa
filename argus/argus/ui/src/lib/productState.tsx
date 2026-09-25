import { createContext, useContext, useMemo, type ReactNode } from "react";
import { usePoll, type Polled } from "./poll";

export type Tone = "ok" | "info" | "warn" | "bad" | "idle" | "unknown";

export interface StateCell {
  key: string;
  label: string;
  word: string;
  tone: Tone;
  detail: string;
  source: string;
  asOf: string | null;
  unknown: boolean;
}

export interface ProductState {
  prizes: { progress: StateCell; firstLetters: StateCell; grandPrize: StateCell };
  ceiling: { detector: StateCell; transfer: StateCell; blindHunt: StateCell };
  surfaces: Polled<SurfacesLite>;
}

export interface SurfacesLite {
  generated_utc?: string;
  surfaces?: {
    resources?: { state?: string; headline?: string; leases?: number; orphan_jobs?: (string | null)[] };
    [k: string]: unknown;
  };
}

function notPublic(key: string, label: string): StateCell {
  return {
    key, label, source: "not part of the public release", asOf: null, unknown: true, tone: "unknown",
    word: "not public",
    detail: "This status is not part of the public release. Unknown is not a result.",
  };
}

const Ctx = createContext<ProductState | null>(null);

export function ProductStateProvider({ children }: { children: ReactNode }) {
  const surfaces = usePoll<SurfacesLite>("/api/surfaces", { intervalMs: 120000, staleAfterS: 900 });

  const value = useMemo<ProductState>(() => ({
    prizes: {
      progress: notPublic("progress", "Progress Prize"),
      firstLetters: notPublic("firstLetters", "First Letters"),
      grandPrize: notPublic("grandPrize", "Grand Prize"),
    },
    ceiling: {
      detector: notPublic("detector", "Detector"),
      transfer: notPublic("transfer", "Cross-scroll transfer"),
      blindHunt: notPublic("blindHunt", "Research gate"),
    },
    surfaces,
  }), [surfaces]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useProductState(): ProductState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useProductState used outside <ProductStateProvider>");
  return v;
}
