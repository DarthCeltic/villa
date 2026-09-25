import { Maximize2, Minimize2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Chip } from "./Status";
import { parseTaskParam, pickStoreForTask, scrollMatches, type Refusal, type TaskBinding } from "../lib/taskBinding";
import { useWorkbenchMode } from "../lib/workbenchMode";
import { VolumeRaycastViewer } from "./VolumeRaycastViewer";
import { crosshairOnLevel, startingView } from "../lib/volumeBrick";

type LevelInfo = {
  level: string;
  declared: boolean;
  openable: boolean;
  shape?: [number, number, number];
  dtype?: string;
  pitch_um_yx?: [number, number] | null;
  why_not_openable?: string;
};

type VolumeMeta = {
  store: string;
  scroll?: string | null;
  axes: string[] | null;
  levels: LevelInfo[];
  identity_roi?: { array_path: string; centre_zyx: [number, number, number] } | null;
};

type VolumeStore = {
  store: string;
  scroll: string | null;
  volume_id?: string | null;
  status: string;
};

const PLANES: { key: "xy" | "xz" | "yz"; label: string }[] = [
  { key: "xy", label: "XY" },
  { key: "xz", label: "XZ" },
  { key: "yz", label: "YZ" },
];
const TILE = 320;

export function StorePicker({ stores, store, onStore }: { stores: VolumeStore[]; store: string; onStore: (s: string) => void }) {
  if (!stores.length) return null;
  return (
    <label className="ag-filter-label">
      Store
      <select className="ag-filter-select" value={store} data-control="wb.volume.store" onChange={(e) => onStore(e.target.value)}>
        {!store ? <option value="">Choose a store…</option> : null}
        {stores.map((s) => (
          <option key={s.store} value={s.store}>{s.store}</option>
        ))}
      </select>
    </label>
  );
}

export function VolumePending({ stores, store, onStore }: { stores: VolumeStore[]; store: string; onStore: (s: string) => void }) {
  return (
    <div className="ag-panel" data-testid="raw-volume-pending">
      {!store && stores.length > 1 ? (
        <p className="ag-prose">{stores.length} stores hold this scroll and none is selected. Choose one; a store is never picked for you.</p>
      ) : (
        <p className="ag-prose">Reading this store's pyramid…</p>
      )}
      <StorePicker stores={stores} store={store} onStore={onStore} />
    </div>
  );
}

export function RawVolumeViewer({ selectedScroll }: { selectedScroll: string | null }) {
  const [stores, setStores] = useState<VolumeStore[] | null>(null);
  const [store, setStore] = useState<string>("");
  const [meta, setMeta] = useState<VolumeMeta | null>(null);
  const [level, setLevel] = useState<string>("");
  const [crosshair, setCrosshair] = useState<[number, number, number] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [focusView, setFocusView] = useState(false);
  const [sp, setSp] = useSearchParams();
  const taskRef = useMemo(() => parseTaskParam(sp.get("task")), [sp]);
  const [mode, setMode] = useState<"slices" | "volume">(() => (sp.get("mode") === "volume" || parseTaskParam(sp.get("task")) ? "volume" : "slices"));
  const [binding, setBinding] = useState<TaskBinding | null>(null);
  const [taskRefusal, setTaskRefusal] = useState<Refusal | null>(null);
  const [taskRows, setTaskRows] = useState<{ kind: string; task_id: string; coordinate_status: string }[]>([]);

  useWorkbenchMode(focusView);
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (focusView) setFocusView(false);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [focusView]);

  useEffect(() => {
    setStores(null);
    setStore("");
    setMeta(null);
    setCrosshair(null);
    setErr(null);
    if (!selectedScroll) return;
    fetch(`/api/volumes?scroll=${encodeURIComponent(selectedScroll)}`)
      .then((r) => r.json())
      .then((d: { stores: VolumeStore[]; selected_store?: string | null }) => {
        const list = d.stores;
        setStores(list);
        setStore(d.selected_store ?? "");
      })
      .catch((e) => setErr(String(e)));
  }, [selectedScroll]);

  useEffect(() => {
    if (!store) return;
    setMeta(null);
    setCrosshair(null);
    setErr(null);
    fetch(`/api/volume_meta?store=${encodeURIComponent(store)}&scroll=${encodeURIComponent(selectedScroll ?? "")}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d: VolumeMeta) => {
        setMeta(d);
        const start = startingView(d.levels, d.identity_roi);
        if (start) {
          setLevel(start.level);
          setCrosshair(start.crosshair);
        }
      })
      .catch((e) => setErr(String(e)));
  }, [store, selectedScroll]);

  useEffect(() => {
    setBinding(null);
    setTaskRefusal(null);
    if (!taskRef) return;
    setMode("volume");
    let dead = false;
    fetch(`/api/workbench_task/${encodeURIComponent(taskRef.kind)}/${encodeURIComponent(taskRef.taskId)}`)
      .then(async (r) => {
        const body = await r.json().catch(() => ({}));
        if (dead) return;
        if (!r.ok) setTaskRefusal({ code: body.code ?? `HTTP_${r.status}`, why: body.why ?? "the task could not be bound" });
        else setBinding(body as TaskBinding);
      })
      .catch((e) => !dead && setTaskRefusal({ code: "NETWORK", why: String(e) }));
    return () => {
      dead = true;
    };
  }, [taskRef?.kind, taskRef?.taskId]);

  useEffect(() => {
    setTaskRows([]);
    if (!selectedScroll) return;
    let dead = false;
    fetch(`/api/workbench_tasks?scroll=${encodeURIComponent(selectedScroll)}`)
      .then((r) => (r.ok ? r.json() : { tasks: [] }))
      .then((d: { tasks: { kind: string; task_id: string; coordinate_status: string }[] }) => !dead && setTaskRows(d.tasks ?? []))
      .catch(() => undefined);
    return () => {
      dead = true;
    };
  }, [selectedScroll]);

  const taskStore = useMemo(() => {
    if (!binding || !stores) return null;
    return scrollMatches(selectedScroll, binding) ?? (binding.coordinate_status !== "RESOLVED" ? (binding.refusal ?? null) : pickStoreForTask(stores, binding));
  }, [binding, stores, selectedScroll]);

  useEffect(() => {
    if (!binding || !taskStore) return;
    if ("code" in taskStore) {
      setTaskRefusal(taskStore);
      return;
    }
    setTaskRefusal(null);
    setStore(taskStore.store);
  }, [binding, taskStore]);

  useEffect(() => {
    if (!binding || !taskStore || "code" in taskStore || !meta || meta.store !== taskStore.store || !binding.roi) return;
    const lvl = meta.levels.find((l) => l.level === binding.level);
    if (!lvl || !lvl.openable || !lvl.shape) {
      setTaskRefusal({ code: "LEVEL_NOT_OPENABLE", why: `the task is bound at level ${binding.level}, which this store does not open${lvl?.why_not_openable ? `: ${lvl.why_not_openable}` : ""}` });
      return;
    }
    setLevel(lvl.level);
    setCrosshair(binding.roi.centre_zyx.map((v) => Math.round(v)) as [number, number, number]);
  }, [binding, taskStore, meta]);

  const curLevel = meta?.levels.find((l) => l.level === level) ?? null;
  const shape0 = curLevel?.shape;
  const boundActive: TaskBinding | null =
    binding && !taskRefusal && taskStore && "store" in taskStore && store === taskStore.store && level === binding.level ? binding : null;

  const openTask = (value: string) =>
    setSp((prev) => {
      const next = new URLSearchParams(prev);
      if (value) {
        next.set("task", value);
        next.set("mode", "volume");
      } else {
        next.delete("task");
      }
      return next;
    });

  const [planes, setPlanes] = useState<Record<string, { src: string; missing: number } | null>>({});

  useEffect(() => {
    if (!store || !level || !crosshair || !shape0) return;
    let dead = false;
    const created: string[] = [];
    const loadOne = async (planeKey: "xy" | "xz" | "yz") => {
      const [z, y, x] = crosshair;
      let index: number, u0: number, v0: number, uMax: number, vMax: number;
      if (planeKey === "xy") { index = z; u0 = y; v0 = x; uMax = shape0[1]; vMax = shape0[2]; }
      else if (planeKey === "xz") { index = y; u0 = z; v0 = x; uMax = shape0[0]; vMax = shape0[2]; }
      else { index = x; u0 = z; v0 = y; uMax = shape0[0]; vMax = shape0[1]; }
      const half = TILE / 2;
      const u = Math.max(0, Math.min(Math.max(0, uMax - TILE), u0 - half));
      const v = Math.max(0, Math.min(Math.max(0, vMax - TILE), v0 - half));
      const h = Math.min(TILE, uMax);
      const w = Math.min(TILE, vMax);
      const url = `/api/volume_plane?store=${encodeURIComponent(store)}&level=${level}` +
                 `&plane=${planeKey}&index=${index}&u=${u}&v=${v}&h=${h}&w=${w}&allow_partial=true` +
                 `&scroll=${encodeURIComponent(selectedScroll ?? "")}`;
      try {
        const r = await fetch(url);
        if (!r.ok || dead) return;
        const missingN = Number(r.headers.get("X-Argus-Missing-Chunks") ?? "0");
        const blob = await r.blob();
        if (dead) return;
        const objUrl = URL.createObjectURL(blob);
        created.push(objUrl);
        setPlanes((p) => ({ ...p, [planeKey]: { src: objUrl, missing: missingN } }));
      } catch {
      }
    };
    void loadOne("xy");
    void loadOne("xz");
    void loadOne("yz");
    return () => {
      dead = true;
      created.forEach((u) => URL.revokeObjectURL(u));
    };
  }, [store, level, crosshair, shape0]);

  const changeLevel = (nextLevel: string) => {
    const next = meta?.levels.find((l) => l.level === nextLevel);
    if (!next || !next.openable || !crosshair || !curLevel) {
      setLevel(nextLevel);
      return;
    }
    setCrosshair(crosshairOnLevel(curLevel, next, crosshair));
    setLevel(nextLevel);
  };

  const storePicker = <StorePicker stores={stores ?? []} store={store} onStore={setStore} />;

  const taskRefusalPanel = taskRefusal ? (
    <div className="ag-panel" role="alert" data-control="wb.task.refused" data-code={taskRefusal.code}>
      <h3 className="ag-panel-title">This task cannot be opened here</h3>
      <p className="ag-prose">
        {taskRefusal.code}: {taskRefusal.why}
      </p>
    </div>
  ) : null;

  if (!selectedScroll)
    return (
      <div className="ag-panel" data-testid="raw-volume-refusal">
        <h3 className="ag-panel-title">No scroll is selected</h3>
        <p className="ag-prose">The raw-volume viewer will not choose a global OME-Zarr store. Select a physical scroll first.</p>
      </div>
    );
  if (err) return <div className="ag-panel"><p className="ag-prose">{err}</p></div>;
  if (!stores) return <div className="ag-panel"><p className="ag-prose">Reading the volume index…</p></div>;
  if (taskRefusalPanel && (!meta || !curLevel || !crosshair)) return taskRefusalPanel;
  if (!stores.length)
    return (
      <div className="ag-panel" data-testid="raw-volume-refusal">
        <h3 className="ag-panel-title">No rendering is registered for {selectedScroll}</h3>
        <p className="ag-prose">No local OME-Zarr store has an explicit identity for this scroll. An available store is not substituted.</p>
      </div>
    );
  if (!meta || !curLevel || !crosshair)
    return <VolumePending stores={stores} store={store} onStore={setStore} />;

  const shape = curLevel.shape!;
  const pitch = curLevel.pitch_um_yx;

  const planeWindow = (planeKey: "xy" | "xz" | "yz") => {
    const [z, y, x] = crosshair;
    let index: number, u0: number, v0: number, uMax: number, vMax: number;
    if (planeKey === "xy") { index = z; u0 = y; v0 = x; uMax = shape[1]; vMax = shape[2]; }
    else if (planeKey === "xz") { index = y; u0 = z; v0 = x; uMax = shape[0]; vMax = shape[2]; }
    else { index = x; u0 = z; v0 = y; uMax = shape[0]; vMax = shape[1]; }
    const half = TILE / 2;
    const u = Math.max(0, Math.min(Math.max(0, uMax - TILE), u0 - half));
    const v = Math.max(0, Math.min(Math.max(0, vMax - TILE), v0 - half));
    const h = Math.min(TILE, uMax);
    const w = Math.min(TILE, vMax);
    return { index, u, v, h, w };
  };

  const handleClick = (planeKey: "xy" | "xz" | "yz", e: React.MouseEvent<HTMLImageElement>) => {
    const { u, v, h, w } = planeWindow(planeKey);
    const rect = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * w + v;
    const py = ((e.clientY - rect.top) / rect.height) * h + u;
    const next: [number, number, number] = [...crosshair];
    if (planeKey === "xy") { next[1] = Math.round(py); next[2] = Math.round(px); }
    else if (planeKey === "xz") { next[0] = Math.round(py); next[2] = Math.round(px); }
    else { next[0] = Math.round(py); next[1] = Math.round(px); }
    setCrosshair(next);
  };

  return (
    <section
      className={focusView ? "ag-stage ag-focus" : "ag-stage"}
      aria-label="Raw CT volume"
      data-testid="raw-volume-viewer"
    >
      <div className="ag-viewbar" role="group" aria-label="Volume controls">
        <div className="ag-viewbar-group">
          {storePicker}
        </div>
        <div className="ag-viewbar-group">
          <label className="ag-filter-label">
            Level
            <select
              className="ag-filter-select"
              value={level}
              data-control="wb.volume.level"
              onChange={(e) => changeLevel(e.target.value)}
            >
              {meta.levels.map((l) => (
                <option key={l.level} value={l.level} disabled={!l.openable}>
                  {l.level}{l.openable ? "" : " (not openable)"}
                </option>
              ))}
            </select>
          </label>
          {!curLevel.openable ? <Chip tone="blocked">not openable</Chip> : null}
        </div>
        <div className="ag-viewbar-group" role="group" aria-label="Raw CT view">
          <button type="button" className="ag-btn" aria-pressed={mode === "slices"} data-control="wb.volume.mode.slices" onClick={() => setMode("slices")}>
            Orthogonal slices
          </button>
          <button type="button" className="ag-btn" aria-pressed={mode === "volume"} data-control="wb.volume.mode.volume" onClick={() => setMode("volume")}>
            Interactive 3D volume
          </button>
        </div>
        {taskRows.length ? (
          <div className="ag-viewbar-group">
            <label className="ag-filter-label">
              Sealed task
              <select className="ag-filter-select" value={taskRef ? `${taskRef.kind}:${taskRef.taskId}` : ""} data-control="wb.task.select" onChange={(e) => openTask(e.target.value)}>
                <option value="">none (free navigation)</option>
                {taskRows.map((t) => (
                  <option key={`${t.kind}:${t.task_id}`} value={`${t.kind}:${t.task_id}`}>
                    {t.task_id}{t.coordinate_status === "RESOLVED" ? "" : " (location unresolved)"}
                  </option>
                ))}
              </select>
            </label>
          </div>
        ) : null}
        <div className="ag-depth-read">
          {pitch ? `${pitch[0].toFixed(3)} µm/px` : "no physical pitch declared for this level"}
        </div>
        <button
          type="button"
          className="ag-btn ag-btn-primary"
          aria-pressed={focusView}
          data-control="wb.volume.focus"
          title="hide surrounding Workbench chrome; press Escape to leave"
          onClick={() => {
            setFocusView((v) => !v);
          }}
        >
          {focusView ? <Minimize2 size={14} aria-hidden /> : <Maximize2 size={14} aria-hidden />}
          {focusView ? "Leave focus" : "Focus view"}
        </button>
      </div>

      {taskRefusalPanel}

      {mode === "volume" && curLevel.openable && !taskRefusal && (!taskRef || binding) ? (
        <VolumeRaycastViewer
          bound={boundActive}
          scroll={selectedScroll}
          store={store}
          volume={stores.find((s) => s.store === store)?.volume_id ?? null}
          level={level}
          centre={crosshair}
          levelExtent={shape}
          focus={focusView}
          onFocusChange={setFocusView}
        />
      ) : null}

      <div style={{ display: mode === "slices" ? "flex" : "none", gap: 12, flexWrap: "wrap", padding: 8 }}>
        {PLANES.map((p) => {
          const tile = planes[p.key];
          const hasMissing = (tile?.missing ?? 0) > 0;
          return (
            <div key={p.key} style={{ display: "grid", gap: 4 }}>
              <div style={{ color: "var(--ink-dim)", fontFamily: "var(--mono-font)" }}>
                {p.label}
              </div>
              <div style={{ position: "relative", width: TILE, height: TILE }}>
                {tile ? (
                  <img
                    src={tile.src}
                    alt={`${p.label} plane`}
                    data-testid={`volume-plane-${p.key}`}
                    width={TILE}
                    height={TILE}
                    style={{ display: "block", cursor: "crosshair", imageRendering: "pixelated",
                            border: "1px solid var(--line)" }}
                    onClick={(e) => handleClick(p.key, e)}
                  />
                ) : (
                  <div style={{ width: TILE, height: TILE, background: "var(--bg-sunken)",
                              border: "1px solid var(--line)" }} />
                )}
                {hasMissing ? (
                  <div
                    style={{
                      position: "absolute", inset: 0, display: "flex", alignItems: "center",
                      justifyContent: "center", background: "rgba(0,0,0,0.4)",
                      color: "var(--status-blocked)", fontSize: "var(--t-small)",
                      pointerEvents: "none", textAlign: "center", padding: 8,
                    }}
                    data-testid={`volume-plane-${p.key}-missing`}
                  >
                    unstaged region -- not rendered as CT
                  </div>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>

      <div style={{ fontFamily: "var(--mono-font)", color: "var(--ink-dim)", padding: "0 8px 8px" }}>
        {curLevel.openable ? (
          <>
            crosshair — voxel z {crosshair[0]} y {crosshair[1]} x {crosshair[2]} at level {level}
            {pitch ? (
              <> · physical {(crosshair[1] * pitch[0]).toFixed(1)}/{(crosshair[2] * pitch[1]).toFixed(1)} µm</>
            ) : (
              <> · no physical reading: this level declares no pitch</>
            )}
          </>
        ) : (
          <>selected level {level} is not openable: {curLevel.why_not_openable}. The crosshair
            still reflects the last level that was.</>
        )}
      </div>
    </section>
  );
}
