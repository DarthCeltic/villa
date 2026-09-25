import { createContext, useContext, type ReactNode } from "react";
import type { RunRecord } from "../api";
import { useScrollUniverse, type Universe } from "../components/ShelfUniverse";

const Ctx = createContext<Universe | null>(null);

export function UniverseProvider({ runs, children }: { runs: RunRecord[]; children: ReactNode }) {
  const u = useScrollUniverse(runs);
  return <Ctx.Provider value={u}>{children}</Ctx.Provider>;
}

export function useSharedUniverse(): Universe {
  const u = useContext(Ctx);
  if (!u) throw new Error("useSharedUniverse used outside <UniverseProvider>");
  return u;
}
