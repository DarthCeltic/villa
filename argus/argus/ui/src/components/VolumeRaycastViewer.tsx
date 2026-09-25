import { RotateCcw } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import "@kitware/vtk.js/Rendering/Profiles/Volume";
import vtkGenericRenderWindow from "@kitware/vtk.js/Rendering/Misc/GenericRenderWindow";
import type vtkProp from "@kitware/vtk.js/Rendering/Core/Prop";
import vtkVolume from "@kitware/vtk.js/Rendering/Core/Volume";
import vtkVolumeMapper from "@kitware/vtk.js/Rendering/Core/VolumeMapper";
import vtkColorTransferFunction from "@kitware/vtk.js/Rendering/Core/ColorTransferFunction";
import vtkPiecewiseFunction from "@kitware/vtk.js/Common/DataModel/PiecewiseFunction";
import vtkImageData from "@kitware/vtk.js/Common/DataModel/ImageData";
import vtkDataArray from "@kitware/vtk.js/Common/Core/DataArray";
import vtkPlane from "@kitware/vtk.js/Common/DataModel/Plane";
import vtkAxesActor from "@kitware/vtk.js/Rendering/Core/AxesActor";
import vtkOrientationMarkerWidget from "@kitware/vtk.js/Interaction/Widgets/OrientationMarkerWidget";
import vtkInteractorStyleManipulator from "@kitware/vtk.js/Interaction/Style/InteractorStyleManipulator";
import vtkMouseCameraTrackballRotateManipulator from "@kitware/vtk.js/Interaction/Manipulators/MouseCameraTrackballRotateManipulator";
import vtkMouseCameraTrackballPanManipulator from "@kitware/vtk.js/Interaction/Manipulators/MouseCameraTrackballPanManipulator";
import vtkMouseCameraTrackballZoomManipulator from "@kitware/vtk.js/Interaction/Manipulators/MouseCameraTrackballZoomManipulator";
import { VolumeOverlays, type OverlayLayer } from "./VolumeOverlays";
import { boundBrick, meshOverlay, notDrawn, type TaskBinding, type TaskMesh } from "../lib/taskBinding";
import { CoordinateDisclosure, FiberMatrixPanel, ModelOutputStatus, type ModelOutputRowData } from "./TaskDisclosures";

type RefusalLike = { code: string; why: string };
import {
  DEFAULT_DISPLAY,
  FULL_CLIP,
  brickQuery,
  centreOfBrick,
  chooseEdge,
  hardwareTier,
  applyMask,
  gridMismatch,
  clampDisplay,
  clipPlanes,
  estimateLine,
  parseBrick,
  physicalOf,
  refusalFrom,
  sha256Hex,
  transferPoints,
  type Blend,
  type Brick,
  type BrickMeta,
  type ClipBox,
  type Display,
  type Refusal,
  type Vec3,
} from "../lib/volumeBrick";

declare global {
  interface Window {
    __ARGUS_VOLUME__?: { liveContexts: number; created: number; disposed: number };
  }
}

function counters() {
  if (!window.__ARGUS_VOLUME__) window.__ARGUS_VOLUME__ = { liveContexts: 0, created: 0, disposed: 0 };
  return window.__ARGUS_VOLUME__;
}

export function probeWebgl(): { ok: boolean; max3d: number | null; renderer: string | null; why?: string } {
  try {
    const canvas = document.createElement("canvas");
    const gl = canvas.getContext("webgl2");
    if (!gl) return { ok: false, max3d: null, renderer: null, why: "this browser has no WebGL 2, which 3D volume rendering needs" };
    const max3d = Number(gl.getParameter(gl.MAX_3D_TEXTURE_SIZE));
    const dbg = gl.getExtension("WEBGL_debug_renderer_info");
    const renderer = String(dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER));
    gl.getExtension("WEBGL_lose_context")?.loseContext();
    return { ok: true, max3d, renderer };
  } catch (e) {
    return { ok: false, max3d: null, renderer: null, why: e instanceof Error ? e.message : String(e) };
  }
}

const MASK_TYPES = ["between_wrap_mask", "cavity_mask", "sdf_mask"] as const;
type MaskType = (typeof MASK_TYPES)[number];
const MASK_TYPE_LABELS: Record<MaskType, string> = {
  between_wrap_mask: "Between-wrap inspection",
  cavity_mask: "Cavity inspection",
  sdf_mask: "Signed-distance mask inspection",
};

interface MaskRow {
  store: string;
  mask_id?: string;
  artifact_type: MaskType;
  label: string;
  producer: string | null;
  source_volume: string | null;
  source_digest?: string;
  content_sha256?: string;
  merkle_root?: string;
  why?: string;
}

type MaskSets = Record<MaskType, { masks: MaskRow[]; refused: MaskRow[]; registry_error?: { code: string; why: string } }>;

interface ModelOutputRow extends ModelOutputRowData {
  store: string;
  status_words: string[];
}

interface AppliedMask {
  row: MaskRow;
  data: Uint8Array;
  sha256: string | null;
  hashOk: boolean | null;
}

interface Props {
  bound?: TaskBinding | null;
  scroll: string;
  store: string;
  volume: string | null;
  level: string;
  centre: Vec3;
  levelExtent: Vec3;
  overlays?: OverlayLayer[];
  focus?: boolean;
  onFocusChange?: (f: boolean) => void;
}

interface Pipeline {
  dispose: () => void;
  applyDisplay: (d: Display, blend: Blend, clip: ClipBox) => void;
  resetCamera: () => void;
  lookAlong: (axis: "x" | "y" | "z") => void;
  resize: () => void;
  addActor: (a: vtkProp) => void;
  removeActor: (a: vtkProp) => void;
  render: () => void;
  worldMax: Vec3;
}

function buildPipeline(container: HTMLElement, brick: Brick): Pipeline {
  const { meta, data } = brick;
  const [dz, dy, dx] = meta.shape_zyx;
  const sp = meta.spacing_um_zyx ?? [1, 1, 1];
  const grw = vtkGenericRenderWindow.newInstance({ background: [0.03, 0.03, 0.045] });
  grw.setContainer(container);
  grw.resize();
  const renderer = grw.getRenderer();
  const rw = grw.getRenderWindow();
  const interactor = grw.getInteractor();

  const img = vtkImageData.newInstance();
  img.setDimensions(dx, dy, dz);
  img.setSpacing([sp[2], sp[1], sp[0]]);
  img.setOrigin([0, 0, 0]);
  img.getPointData().setScalars(vtkDataArray.newInstance({ numberOfComponents: 1, values: data, dataType: "Uint8Array" }));

  const mapper = vtkVolumeMapper.newInstance();
  mapper.setInputData(img);
  const minSp = Math.min(sp[0], sp[1], sp[2]);
  mapper.setAutoAdjustSampleDistances(false);
  mapper.setSampleDistance(minSp * 0.6);
  const volume = vtkVolume.newInstance();
  volume.setMapper(mapper);
  const ctf = vtkColorTransferFunction.newInstance();
  const ofn = vtkPiecewiseFunction.newInstance();
  const prop = volume.getProperty();
  prop.setRGBTransferFunction(0, ctf);
  prop.setScalarOpacity(0, ofn);
  prop.setInterpolationTypeToLinear();
  prop.setShade(false);
  prop.setScalarOpacityUnitDistance(0, minSp * 4);
  renderer.addVolume(volume);

  const style = vtkInteractorStyleManipulator.newInstance();
  style.removeAllMouseManipulators();
  const rotate = vtkMouseCameraTrackballRotateManipulator.newInstance();
  rotate.setButton(1);
  const panShift = vtkMouseCameraTrackballPanManipulator.newInstance();
  panShift.setButton(1);
  panShift.setShift(true);
  const panRight = vtkMouseCameraTrackballPanManipulator.newInstance();
  panRight.setButton(3);
  const zoom = vtkMouseCameraTrackballZoomManipulator.newInstance();
  zoom.setButton(2);
  const wheelZoom = vtkMouseCameraTrackballZoomManipulator.newInstance();
  wheelZoom.setDragEnabled(false);
  wheelZoom.setScrollEnabled(true);
  style.addMouseManipulator(wheelZoom);
  style.addMouseManipulator(rotate);
  style.addMouseManipulator(panShift);
  style.addMouseManipulator(panRight);
  style.addMouseManipulator(zoom);
  interactor.setInteractorStyle(style);

  const axes = vtkAxesActor.newInstance();
  const marker = vtkOrientationMarkerWidget.newInstance({ actor: axes, interactor });
  marker.setEnabled(true);
  marker.setViewportCorner(vtkOrientationMarkerWidget.Corners.BOTTOM_LEFT);
  marker.setViewportSize(0.18);
  marker.setMinPixelSize(64);
  marker.setMaxPixelSize(140);

  const worldMax: Vec3 = [(dx - 1) * sp[2], (dy - 1) * sp[1], (dz - 1) * sp[0]];
  const planes: ReturnType<typeof vtkPlane.newInstance>[] = [];
  const applyDisplay = (d: Display, blend: Blend, clip: ClipBox) => {
    const tp = transferPoints(d);
    ofn.removeAllPoints();
    tp.opacity.forEach(([x, y]) => ofn.addPoint(x, y));
    ctf.removeAllPoints();
    tp.colour.forEach(([x, r, g, b]) => ctf.addRGBPoint(x, r, g, b));
    if (blend === "mip") mapper.setBlendModeToMaximumIntensity();
    else mapper.setBlendModeToComposite();
    mapper.removeAllClippingPlanes();
    planes.splice(0).forEach((p) => p.delete());
    for (const c of clipPlanes(clip, [0, 0, 0], worldMax)) {
      const p = vtkPlane.newInstance({ origin: c.origin, normal: c.normal });
      planes.push(p);
      mapper.addClippingPlane(p);
    }
    rw.render();
  };
  const centreW: Vec3 = [worldMax[0] / 2, worldMax[1] / 2, worldMax[2] / 2];
  const homeDist = Math.max(worldMax[0], worldMax[1], worldMax[2]) * 3;
  const resetCamera = () => {
    const cam = renderer.getActiveCamera();
    cam.setFocalPoint(centreW[0], centreW[1], centreW[2]);
    cam.setPosition(centreW[0] + homeDist * 0.55, centreW[1] + homeDist * 0.4, centreW[2] + homeDist * 0.75);
    cam.setViewUp(0, 1, 0);
    cam.setParallelProjection(false);
    renderer.resetCamera();
    rw.render();
  };
  const lookAlong = (axis: "x" | "y" | "z") => {
    const cam = renderer.getActiveCamera();
    const c: Vec3 = [worldMax[0] / 2, worldMax[1] / 2, worldMax[2] / 2];
    const d = Math.max(worldMax[0], worldMax[1], worldMax[2]) * 3;
    const pos: Vec3 = [...c];
    const up: Vec3 = [0, 0, 0];
    if (axis === "z") { pos[2] += d; up[1] = 1; }
    else if (axis === "y") { pos[1] += d; up[2] = 1; }
    else { pos[0] += d; up[2] = 1; }
    cam.setFocalPoint(c[0], c[1], c[2]);
    cam.setPosition(pos[0], pos[1], pos[2]);
    cam.setViewUp(up[0], up[1], up[2]);
    cam.setParallelProjection(true);
    renderer.resetCamera();
    rw.render();
  };
  resetCamera();

  const c = counters();
  c.liveContexts += 1;
  c.created += 1;
  let disposed = false;
  const dispose = () => {
    if (disposed) return;
    disposed = true;
    try {
      const canvas = grw.getApiSpecificRenderWindow().getCanvas();
      const gl = (canvas?.getContext("webgl2") ?? canvas?.getContext("webgl")) as WebGL2RenderingContext | null;
      marker.setEnabled(false);
      marker.delete();
      axes.delete();
      planes.forEach((p) => p.delete());
      volume.delete();
      mapper.delete();
      ofn.delete();
      ctf.delete();
      img.delete();
      style.delete();
      grw.delete();
      gl?.getExtension("WEBGL_lose_context")?.loseContext();
    } finally {
      c.liveContexts -= 1;
      c.disposed += 1;
    }
  };
  return {
    dispose,
    applyDisplay,
    resetCamera,
    lookAlong,
    resize: () => grw.resize(),
    addActor: (a) => renderer.addActor(a),
    removeActor: (a) => renderer.removeActor(a),
    render: () => rw.render(),
    worldMax,
  };
}

export function VolumeRaycastViewer({ bound = null, scroll, store, volume, level, centre, levelExtent, overlays = [], focus = false, onFocusChange }: Props) {
  const wgl = useMemo(() => probeWebgl(), []);
  const holder = useRef<HTMLDivElement | null>(null);
  const pipe = useRef<Pipeline | null>(null);
  const [plan, setPlan] = useState<BrickMeta | null>(null);
  const [refusal, setRefusal] = useState<Refusal | null>(null);
  const [busy, setBusy] = useState<"plan" | "load" | null>(null);
  const [brick, setBrick] = useState<Brick | null>(null);
  const [hashOk, setHashOk] = useState<boolean | null>(null);
  const [wantLarger, setWantLarger] = useState(false);
  const [blend, setBlend] = useState<Blend>("composite");
  const [display, setDisplay] = useState<Display>(DEFAULT_DISPLAY);
  const [clip, setClip] = useState<ClipBox>(FULL_CLIP);

  const [withHalo, setWithHalo] = useState(true);
  const [allowPartial, setAllowPartial] = useState(false);
  const [taskMesh, setTaskMesh] = useState<TaskMesh | null>(null);
  const [meshNote, setMeshNote] = useState<string | null>(null);

  const tier = useMemo(() => hardwareTier(wgl.renderer, wgl.max3d), [wgl.renderer, wgl.max3d]);
  const edge = chooseEdge({ maxTexture: wgl.max3d, wantLarger, levelExtent, tier });
  const boundBox = useMemo(() => (bound ? boundBrick(bound, levelExtent, withHalo) : null), [bound, levelExtent, withHalo]);
  const boundRefusal: RefusalLike | null = boundBox && "code" in boundBox ? boundBox : null;
  const request = useMemo(() => {
    const base = { store, level, scroll, volume, centre, edge, maxTexture: wgl.max3d, vramMiB: tier.vramBudgetMiB, allowPartial };
    return boundBox && !("code" in boundBox) ? { ...base, origin: boundBox.origin, shape: boundBox.shape } : base;
  }, [store, level, scroll, volume, centre, edge, wgl.max3d, tier.vramBudgetMiB, allowPartial, boundBox]);

  const taskId = bound?.task.task_id ?? null;
  useEffect(() => {
    setWithHalo(true);
    setAllowPartial(false);
  }, [taskId]);

  const meshPermitted = bound?.layers.find((l) => l.id === "mesh")?.permitted ?? false;
  useEffect(() => {
    setTaskMesh(null);
    setMeshNote(null);
    if (!bound || !meshPermitted) return;
    let dead = false;
    fetch(`/api/workbench_task/${encodeURIComponent(bound.task.kind)}/${encodeURIComponent(bound.task.task_id)}/mesh`)
      .then(async (r) => {
        const body = await r.json().catch(() => ({}));
        if (dead) return;
        if (r.ok) setTaskMesh(body as TaskMesh);
        else setMeshNote(`mesh refused (${body.code ?? r.status}): ${body.why ?? "no reason given"}`);
      })
      .catch((e) => !dead && setMeshNote(`mesh unavailable: ${String(e)}`));
    return () => {
      dead = true;
    };
  }, [taskId, meshPermitted]);

  const overlayList = useMemo(() => (bound && taskMesh ? [...overlays, meshOverlay(bound, taskMesh)] : overlays), [overlays, bound, taskMesh]);

  useEffect(() => {
    setBrick(null);
    setHashOk(null);
    setPlan(null);
    setRefusal(null);
    if (!wgl.ok) return;
    if (boundRefusal) {
      setRefusal(boundRefusal);
      return;
    }
    let dead = false;
    setBusy("plan");
    fetch(`/api/volume_brick_plan?${brickQuery(request)}`)
      .then(async (r) => {
        const body = await r.json().catch(() => ({}));
        if (dead) return;
        if (!r.ok || body.error) setRefusal(refusalFrom(r.status, body));
        else setPlan(body as BrickMeta);
      })
      .catch((e) => !dead && setRefusal({ code: "NETWORK", why: String(e) }))
      .finally(() => !dead && setBusy(null));
    return () => {
      dead = true;
    };
  }, [request, wgl.ok, boundRefusal?.code]);

  const load = useCallback(async () => {
    setBusy("load");
    setRefusal(null);
    try {
      const r = await fetch(`/api/volume_brick?${brickQuery(request)}`);
      if (!r.ok) {
        setRefusal(refusalFrom(r.status, await r.json().catch(() => ({}))));
        return;
      }
      const b = parseBrick(r.headers.get("X-Argus-Brick-Meta"), await r.arrayBuffer());
      const digest = await sha256Hex(b.data);
      setHashOk(digest === null || !b.meta.sha256 ? null : digest === b.meta.sha256);
      setBrick(b);
    } catch (e) {
      setRefusal({ code: "BRICK_UNREADABLE", why: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(null);
    }
  }, [request]);

  const [maskSets, setMaskSets] = useState<MaskSets | null>(null);
  const [modelOutputs, setModelOutputs] = useState<{ overlays: ModelOutputRow[]; refused: { why: string; store: string }[] } | null>(null);
  const [applied, setApplied] = useState<AppliedMask | null>(null);
  const [maskNote, setMaskNote] = useState<string | null>(null);

  useEffect(() => {
    setMaskSets(null);
    setModelOutputs(null);
    setApplied(null);
    setMaskNote(null);
    if (!brick || !volume) return;
    let dead = false;
    Promise.all(
      MASK_TYPES.map((artifact_type) =>
        fetch(`/api/volume_masks?${new URLSearchParams({ scroll, volume, level, artifact_type }).toString()}`).then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))),
      ),
    )
      .then((rows) => !dead && setMaskSets(Object.fromEntries(MASK_TYPES.map((t, i) => [t, rows[i]])) as MaskSets))
      .catch((e) => !dead && setMaskNote(`could not list registered masks: ${String(e)}`));
    if (!bound)
      fetch(`/api/prediction_overlays?${new URLSearchParams({ scroll, volume, level }).toString()}`)
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
        .then((d) => !dead && setModelOutputs(d))
        .catch(() => undefined);
    return () => {
      dead = true;
    };
  }, [brick, scroll, volume, level]);

  const toggleMask = useCallback(
    async (row: MaskRow, on: boolean) => {
      setMaskNote(null);
      if (!on) {
        setApplied(null);
        return;
      }
      if (!brick) return;
      try {
        const q = brickQuery({
          store: row.store, level, scroll, volume, centre, edge, maxTexture: wgl.max3d, vramMiB: tier.vramBudgetMiB,
          origin: brick.meta.origin_voxel_zyx, shape: brick.meta.shape_zyx, allowPartial: false,
        });
        const r = await fetch(`/api/volume_brick?${q}`);
        if (!r.ok) {
          const ref = refusalFrom(r.status, await r.json().catch(() => ({})));
          setMaskNote(`mask refused (${ref.code}): ${ref.why}`);
          return;
        }
        const m = parseBrick(r.headers.get("X-Argus-Brick-Meta"), await r.arrayBuffer());
        const mismatch = gridMismatch(brick.meta, m.meta);
        if (mismatch) {
          setMaskNote(`mask refused (GRID_MISMATCH): ${mismatch}`);
          return;
        }
        const digest = await sha256Hex(m.data);
        setApplied({ row, data: m.data, sha256: m.meta.sha256 ?? null, hashOk: digest === null || !m.meta.sha256 ? null : digest === m.meta.sha256 });
      } catch (e) {
        setMaskNote(`mask unreadable: ${e instanceof Error ? e.message : String(e)}`);
      }
    },
    [brick, level, scroll, volume, centre, edge, wgl.max3d, tier.vramBudgetMiB],
  );

  const masked = useMemo(() => (brick && applied && applied.hashOk !== false ? applyMask(brick.data, applied.data) : null), [brick, applied]);
  const displayBrick = useMemo<Brick | null>(() => (brick && masked ? { meta: brick.meta, data: masked.data } : brick), [brick, masked]);

  useEffect(() => {
    if (!displayBrick || !holder.current) return;
    const p = buildPipeline(holder.current, displayBrick);
    pipe.current = p;
    p.applyDisplay(clampDisplay(display), blend, clip);
    const ro = new ResizeObserver(() => {
      p.resize();
      p.render();
    });
    ro.observe(holder.current);
    return () => {
      ro.disconnect();
      p.dispose();
      pipe.current = null;
    };
  }, [displayBrick]);

  useEffect(() => {
    pipe.current?.applyDisplay(clampDisplay(display), blend, clip);
  }, [display, blend, clip]);

  const meta = brick?.meta ?? null;
  const shown = meta ?? plan;
  const centreVoxel = meta ? centreOfBrick(meta) : centre;
  const phys = shown ? physicalOf(shown, centreVoxel) : null;
  const setAxis = (which: "min" | "max", axis: number, v: number) =>
    setClip((c) => {
      const next = { min: [...c.min] as Vec3, max: [...c.max] as Vec3 };
      next[which][axis] = v;
      if (next.min[axis]! > next.max[axis]! - 0.02) next[which][axis] = which === "min" ? next.max[axis]! - 0.02 : next.min[axis]! + 0.02;
      return next;
    });

  if (!wgl.ok) {
    return (
      <div className="ag-panel" data-control="wb.volume3d.unsupported" data-webgl="unsupported">
        <h3 className="ag-panel-title">3D volume is unavailable in this browser</h3>
        <p className="ag-prose">{wgl.why}. The orthogonal slice view is unaffected.</p>
      </div>
    );
  }

  return (
    <div style={{ display: "grid", gap: 8, padding: 8 }} data-control="wb.volume3d" data-webgl="ok" data-brick-sha={meta?.sha256 ?? ""} data-roi={meta ? JSON.stringify({ origin: meta.origin_voxel_zyx, shape: meta.shape_zyx }) : ""}>
      {bound ? (
        <div className="meta" role="note" data-control="wb.volume3d.task" data-task-id={bound.task.task_id} style={{ border: "1px solid var(--line)", padding: 6 }}>
          Sealed task <b>{bound.task.task_id}</b> ({bound.task.kind}) · review state <b>{bound.task.review_state}</b> · geometry {bound.geometry.state ?? "not recorded"}
          {bound.geometry.watermarked ? " · WATERMARKED" : ""} · orientation {bound.geometry.orientation} · task sha256 {bound.task.task_sha256.slice(0, 12)}… · queue root{" "}
          {bound.task.queue_root_sha256.slice(0, 12)}… · opened at the bound region
          {bound.roi && bound.roi.halo_voxels > 0 ? (withHalo ? ` plus a ${bound.roi.halo_voxels}-voxel halo` : " (exact region, no halo)") : ""}. {bound.claims.join(" ")}
        </div>
      ) : null}

      {bound ? <CoordinateDisclosure system={bound.coordinate_system} /> : null}

      <div className="meta" data-control="wb.volume3d.readout" style={{ fontFamily: "var(--mono-font)" }}>
        scroll {scroll} · volume {volume ?? "not declared by the store"} · level {level} ·{" "}
        {shown ? (
          <>
            ROI z {shown.origin_voxel_zyx[0]}–{shown.origin_voxel_zyx[0] + shown.shape_zyx[0]} y {shown.origin_voxel_zyx[1]}–{shown.origin_voxel_zyx[1] + shown.shape_zyx[1]} x{" "}
            {shown.origin_voxel_zyx[2]}–{shown.origin_voxel_zyx[2] + shown.shape_zyx[2]} ·{" "}
            {shown.spacing_um_zyx ? `spacing z ${shown.spacing_um_zyx[0]} y ${shown.spacing_um_zyx[1]} x ${shown.spacing_um_zyx[2]} µm` : "spacing not declared by this store: shown in voxels"} ·{" "}
            {phys ? `centre ${phys.map((v) => v.toFixed(1)).join(" / ")} µm (z/y/x)` : `centre voxel ${centreVoxel.join(", ")}`}
          </>
        ) : (
          "planning…"
        )}
      </div>

      {plan && !brick ? (
        <div className="meta" data-control="wb.volume3d.estimate" data-hardware-tier={tier.tier}>
          {estimateLine(plan)}. Hardware tier {tier.tier} (heuristic from the WebGL renderer: {tier.why}; GPU budget {tier.vramBudgetMiB} MiB). {plan.origin_adjusted_to_fit ? "The brick was moved to fit inside the volume. " : ""}
          <button type="button" className="ag-btn ag-btn-primary" data-control="wb.volume3d.load" disabled={busy !== null} onClick={() => void load()}>
            {busy === "load" ? "Loading…" : "Load 3D volume here"}
          </button>{" "}
          <label className="meta">
            <input type="checkbox" checked={wantLarger} onChange={(e) => setWantLarger(e.target.checked)} data-control="wb.volume3d.larger" /> larger brick (only if this GPU allows it)
          </label>
        </div>
      ) : null}

      {refusal ? (
        <div className="meta" role="alert" data-control="wb.volume3d.refused" style={{ color: "var(--warn)" }}>
          Refused ({refusal.code}): {refusal.why}
          {refusal.code === "MISSING_CHUNKS" && bound && withHalo && bound.roi && bound.roi.halo_voxels > 0 ? (
            <>
              {" "}
              <button type="button" className="ag-btn" data-control="wb.volume3d.no-halo" onClick={() => setWithHalo(false)}>Open the exact region without the halo</button>
            </>
          ) : null}
          {refusal.code === "MISSING_CHUNKS" && bound && !allowPartial ? (
            <>
              {" "}
              <button type="button" className="ag-btn" data-control="wb.volume3d.allow-partial" onClick={() => setAllowPartial(true)}>Load anyway, missing chunks marked as not CT</button>
            </>
          ) : null}
        </div>
      ) : null}
      {meshNote ? <div className="meta" role="note" data-control="wb.volume3d.mesh-note" style={{ color: "var(--warn)" }}>{meshNote}</div> : null}
      {meta?.missing_chunk_count ? (
        <div className="meta" role="note" data-control="wb.volume3d.missing" style={{ color: "var(--warn)" }}>
          {meta.missing_chunk_count} chunk(s) under this brick are missing and drawn as empty: not CT.
        </div>
      ) : null}
      {hashOk === false ? (
        <div className="meta" role="alert" style={{ color: "var(--bad)" }}>The received bytes do not match the brick's declared sha256; do not trust this picture.</div>
      ) : null}

      <div style={{ position: "relative", height: focus ? "70vh" : 420, minHeight: 280, border: "1px solid var(--line)", background: "var(--bg-sunken)" }}>
        <div ref={holder} data-testid="volume-raycast-canvas" style={{ position: "absolute", inset: 0 }} />
        {!brick ? (
          <div className="meta" style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", pointerEvents: "none" }}>
            {busy === "plan" ? "Asking what this brick would cost…" : "No volume loaded"}
          </div>
        ) : null}
      </div>

      {brick ? (
        <div style={{ display: "grid", gap: 6 }} data-control="wb.volume3d.controls">
          <div className="ag-viewbar-group" role="group" aria-label="Blend mode">
            <button type="button" className="ag-btn" aria-pressed={blend === "composite"} data-control="wb.volume3d.blend.composite" onClick={() => setBlend("composite")}>Composite</button>
            <button type="button" className="ag-btn" aria-pressed={blend === "mip"} data-control="wb.volume3d.blend.mip" onClick={() => setBlend("mip")}>Maximum intensity</button>
            <button type="button" className="ag-btn" data-control="wb.volume3d.reset" onClick={() => pipe.current?.resetCamera()} title="reset the camera to the whole brick">
              <RotateCcw size={14} aria-hidden /> Reset view
            </button>
            {(["x", "y", "z"] as const).map((a) => (
              <button key={a} type="button" className="ag-btn" data-control={`wb.volume3d.look.${a}`} onClick={() => pipe.current?.lookAlong(a)} title={`look down the ${a.toUpperCase()} axis from its positive side`}>
                Look along {a.toUpperCase()}
              </button>
            ))}
            {onFocusChange ? (
              <button type="button" className="ag-btn" aria-pressed={focus} data-control="wb.volume3d.focus" onClick={() => onFocusChange(!focus)}>
                {focus ? "Leave focus" : "Focus view"}
              </button>
            ) : null}
            <span className="meta">Left drag rotates · right or Shift+left drag pans · wheel zooms · display settings never change the data</span>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 8 }}>
            {(
              [
                ["Window low", "windowLo", 0, 254],
                ["Window high", "windowHi", 1, 255],
                ["Opacity", "opacity", 0, 1],
                ["Threshold", "threshold", 0, 255],
              ] as const
            ).map(([label, key, min, max]) => (
              <label key={key} className="meta" style={{ display: "grid", gap: 2 }}>
                {label}: {key === "opacity" ? display[key].toFixed(2) : display[key]}
                <input
                  type="range"
                  min={min}
                  max={max}
                  step={key === "opacity" ? 0.01 : 1}
                  value={display[key]}
                  data-control={`wb.volume3d.${key}`}
                  onChange={(e) => setDisplay((d) => ({ ...d, [key]: Number(e.target.value) }))}
                />
              </label>
            ))}
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 8 }} data-control="wb.volume3d.clip">
            {(["x", "y", "z"] as const).map((name, axis) => (
              <div key={name} className="meta" style={{ display: "grid", gap: 2 }}>
                Clip {name}: {Math.round(clip.min[axis]! * 100)}–{Math.round(clip.max[axis]! * 100)}%
                <input type="range" min={0} max={1} step={0.01} value={clip.min[axis]!} aria-label={`clip ${name} min`} data-control={`wb.volume3d.clip.${name}.min`} onChange={(e) => setAxis("min", axis, Number(e.target.value))} />
                <input type="range" min={0} max={1} step={0.01} value={clip.max[axis]!} aria-label={`clip ${name} max`} data-control={`wb.volume3d.clip.${name}.max`} onChange={(e) => setAxis("max", axis, Number(e.target.value))} />
              </div>
            ))}
          </div>
          <div className="meta" data-control="wb.volume3d.provenance">
            Raw CT visualization, not a detector. Brick sha256 {meta?.sha256?.slice(0, 16)}… {hashOk ? "(verified in the browser)" : ""} · display mapping {meta?.display_mapping?.formula}
            {" "}· {estimateLine(meta!)}
          </div>
        </div>
      ) : null}

      {brick ? (
        <section aria-label="Registered masks" data-control="wb.volume3d.masks" style={{ display: "grid", gap: 6 }}>
          <div style={{ fontWeight: 600 }}>Cavity, signed-distance, paired-surface and between-wrap inspection (four separate capabilities)</div>
          {MASK_TYPES.map((type) => {
            const set = maskSets?.[type];
            return (
              <div key={type} style={{ display: "grid", gap: 2 }} data-control={`wb.volume3d.mask.${type}`}>
                {set && !set.masks.length ? (
                  <div className="meta" data-control={`wb.volume3d.mask.${type}.unavailable`}>
                    <b>{MASK_TYPE_LABELS[type]}</b>: unavailable. No hash-bound registered {type} artifact exists for {scroll} / {volume ?? "an undeclared volume"} at level {level}; the view is not
                    approximated from anything else, and an artifact of another type does not stand in for it.
                    {set.registry_error ? ` The mask registry could not be read (${set.registry_error.code}).` : ""}
                  </div>
                ) : null}
                {set?.refused.length ? (
                  <div className="meta" data-control={`wb.volume3d.mask.${type}.refused-list`} data-count={set.refused.length}>
                    Not offered ({set.refused.length}): {set.refused.map((m) => `${m.store.split(/[\\/]/).pop()}: ${m.why}`).join("; ")}.
                  </div>
                ) : null}
                {set?.masks.map((m) => (
                  <label key={m.store} className="meta" data-control="wb.volume3d.mask" data-mask-type={type} style={{ display: "block" }}>
                    <input type="checkbox" checked={applied?.row.store === m.store} onChange={(e) => void toggleMask(m, e.target.checked)} data-control="wb.volume3d.mask.toggle" data-mask-type={type} />{" "}
                    <b>{MASK_TYPE_LABELS[type]}</b> · show only inside this registered mask (display only; the brick's bytes and hash are unchanged) · mask {m.mask_id} · producer {m.producer ?? "not declared"} ·
                    source volume {m.source_volume} · source digest {m.source_digest?.slice(0, 12)}… · content sha256 {m.content_sha256?.slice(0, 12)}… · Merkle root {m.merkle_root?.slice(0, 12)}… · the server re-verified
                    every byte against its registered manifest before offering it
                  </label>
                ))}
              </div>
            );
          })}
          <div className="meta" data-control="wb.volume3d.mask.paired_surface.unavailable">
            <b>Paired-surface inspection</b>: unavailable. It needs two identity-matched, hash-bound bounding surfaces; none is registered and this viewer has no renderer for one, so no region between two surfaces is drawn.
          </div>
          {applied && masked ? (
            <div className="meta" role="note" data-control="wb.volume3d.mask.status">
              {MASK_TYPE_LABELS[applied.row.artifact_type]}: {masked.hidden} of {brick.data.length} voxels are hidden by the mask (mask sha256 {applied.sha256?.slice(0, 12) ?? "unhashed"}…{applied.hashOk ? ", verified in the browser" : ""}). This is geometry apparatus, not a detector, and
              says nothing about ink.
            </div>
          ) : null}
          {maskNote ? <div className="meta" role="alert" data-control="wb.volume3d.mask.refused" style={{ color: "var(--warn)" }}>{maskNote}</div> : null}
        </section>
      ) : null}

      {modelOutputs && (modelOutputs.overlays.length || modelOutputs.refused.length) ? (
        <section aria-label="Model output" data-control="wb.volume3d.model-outputs" style={{ display: "grid", gap: 4 }}>
          <div style={{ fontWeight: 600 }}>Registered model output</div>
          {modelOutputs.overlays.map((o) => (
            <ModelOutputStatus key={o.store} overlay={o} />
          ))}
          {modelOutputs.refused.length ? (
            <div className="meta" data-control="wb.volume3d.model-outputs.refused" data-count={modelOutputs.refused.length}>
              Refused ({modelOutputs.refused.length}): {modelOutputs.refused.map((m) => m.why).join("; ")}.
            </div>
          ) : null}
        </section>
      ) : null}

      {bound ? <FiberMatrixPanel matrix={bound.fiber_capabilities} /> : null}

      <VolumeOverlays layers={overlayList} notDrawn={bound ? notDrawn(bound) : []} brick={meta} scroll={scroll} volume={volume} pipeline={pipe} />
    </div>
  );
}
