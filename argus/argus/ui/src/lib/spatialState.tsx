import { createContext, useCallback, useContext, useMemo, useState } from "react";

export type Voxel = [number, number, number];

export interface SpatialState {
  meshDir: string | null;
  crosshair: Voxel | null;
  selectedWindingId: number | null;
  windingsStride: number;
}

export interface SpatialStateApi extends SpatialState {
  setMesh: (meshDir: string | null) => void;
  setCrosshair: (c: Voxel) => void;
  setSelectedWinding: (id: number | null) => void;
  setWindingsStride: (s: number) => void;
}

const EMPTY: SpatialState = {
  meshDir: null,
  crosshair: null,
  selectedWindingId: null,
  windingsStride: 4,
};

const Ctx = createContext<SpatialStateApi | null>(null);

export function SpatialStateProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<SpatialState>(EMPTY);

  const setMesh = useCallback((meshDir: string | null) => {
    setState((s) => (s.meshDir === meshDir ? s : { ...EMPTY, meshDir }));
  }, []);
  const setCrosshair = useCallback((c: Voxel) => {
    setState((s) => ({ ...s, crosshair: c }));
  }, []);
  const setSelectedWinding = useCallback((id: number | null) => {
    setState((s) => ({ ...s, selectedWindingId: id }));
  }, []);
  const setWindingsStride = useCallback((n: number) => {
    setState((s) => ({ ...s, windingsStride: n }));
  }, []);

  const value = useMemo<SpatialStateApi>(
    () => ({ ...state, setMesh, setCrosshair, setSelectedWinding, setWindingsStride }),
    [state, setMesh, setCrosshair, setSelectedWinding, setWindingsStride],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSpatialState(): SpatialStateApi {
  const v = useContext(Ctx);
  if (!v) throw new Error("useSpatialState() called outside a <SpatialStateProvider>");
  return v;
}
