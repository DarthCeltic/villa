import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Grid3x3, Maximize2, Minimize2, RotateCcw } from "lucide-react";
import { HttpError, api, type MeshLattice } from "../api";
import { Chip } from "./Status";
import { useSpatialState } from "../lib/spatialState";
import { hueForWinding } from "../lib/windingColor";
import { useWorkbenchMode } from "../lib/workbenchMode";

import "@kitware/vtk.js/Rendering/Profiles/Geometry";
import vtkGenericRenderWindow from "@kitware/vtk.js/Rendering/Misc/GenericRenderWindow";
import vtkActor from "@kitware/vtk.js/Rendering/Core/Actor";
import vtkMapper from "@kitware/vtk.js/Rendering/Core/Mapper";
import vtkPolyData from "@kitware/vtk.js/Common/DataModel/PolyData";
import vtkPoints from "@kitware/vtk.js/Common/Core/Points";
import vtkCellArray from "@kitware/vtk.js/Common/Core/CellArray";
import vtkInteractorStyleTrackballCamera from "@kitware/vtk.js/Interaction/Style/InteractorStyleTrackballCamera";
import vtkDataArray from "@kitware/vtk.js/Common/Core/DataArray";
import vtkSphereSource from "@kitware/vtk.js/Filters/Sources/SphereSource";
import vtkPointPicker from "@kitware/vtk.js/Rendering/Core/PointPicker";
import { TOKEN_FALLBACK } from "../theme/tokenFallbacks";

type Vec3 = [number, number, number];

function hsl2rgb01(h: number, s: number, l: number): Vec3 {
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const hp = ((h % 360) + 360) % 360 / 60;
  const x = c * (1 - Math.abs((hp % 2) - 1));
  let r = 0;
  let g = 0;
  let b = 0;
  if (hp < 1) [r, g, b] = [c, x, 0];
  else if (hp < 2) [r, g, b] = [x, c, 0];
  else if (hp < 3) [r, g, b] = [0, c, x];
  else if (hp < 4) [r, g, b] = [0, x, c];
  else if (hp < 5) [r, g, b] = [x, 0, c];
  else [r, g, b] = [c, 0, x];
  const m = l - c / 2;
  return [r + m, g + m, b + m];
}

function hexToRgb01(hex: string): Vec3 {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m || !m[1]) return [0.6, 0.6, 0.6];
  const n = parseInt(m[1], 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

export function MeshCanvas({
  meshDir,
  runId,
  height,
}: {
  meshDir: string | null;
  runId?: string | null;
  height?: number | string;
}) {
  const [data, setData] = useState<MeshLattice | null>(null);
  const dataRef = useRef<MeshLattice | null>(null);
  dataRef.current = data;
  const [err, setErr] = useState<string | null>(null);
  const [sealed, setSealed] = useState(false);
  const [showJumps, setShowJumps] = useState(true);
  const [opacity, setOpacity] = useState(0.85);
  const [focusView, setFocusView] = useState(false);
  const [hasInteracted, setHasInteracted] = useState(false);

  const spatial = useSpatialState();
  const activeSpatial = spatial.meshDir === meshDir;

  useWorkbenchMode(focusView);
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (focusView) setFocusView(false);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [focusView]);

  useEffect(() => {
    setData(null);
    setErr(null);
    setSealed(false);
    setHasInteracted(false);
    if (!meshDir) return;
    let live = true;
    api
      .mesh(meshDir, { stride: spatial.windingsStride, withWindings: true })
      .then((d) => live && setData(d))
      .catch((e) => {
        if (!live) return;
        if (e instanceof HttpError && e.status === 423) setSealed(true);
        else setErr(String(e));
      });
    return () => {
      live = false;
    };
  }, [meshDir]);

  const centred = useMemo(() => {
    const src = data?.points3;
    if (!src || !src.length) return null;
    let cx = 0;
    let cy = 0;
    let cz = 0;
    for (const p of src) {
      cx += p[0];
      cy += p[1];
      cz += p[2];
    }
    cx /= src.length;
    cy /= src.length;
    cz /= src.length;
    const pts: Vec3[] = src.map((p) => [p[0] - cx, p[1] - cy, p[2] - cz]);
    const jumps = (data?.jump_edges ?? [])
      .filter((e) => e.from3 && e.to3)
      .map((e) => ({
        a: [e.from3![0] - cx, e.from3![1] - cy, e.from3![2] - cz] as Vec3,
        b: [e.to3![0] - cx, e.to3![1] - cy, e.to3![2] - cz] as Vec3,
      }));
    let r = 1;
    for (const p of pts) r = Math.max(r, Math.hypot(p[0], p[1], p[2]) || 1);
    return { pts, jumps, radius: r, centre: [cx, cy, cz] };
  }, [data]);

  const [container, setContainerEl] = useState<HTMLDivElement | null>(null);
  const setContainer = useCallback((el: HTMLDivElement | null) => {
    setContainerEl(el);
  }, []);

  type VtkHandles = {
    grw: ReturnType<typeof vtkGenericRenderWindow.newInstance>;
    meshPolyData: ReturnType<typeof vtkPolyData.newInstance>;
    meshMapper: ReturnType<typeof vtkMapper.newInstance>;
    meshActor: ReturnType<typeof vtkActor.newInstance>;
    jumpsPolyData: ReturnType<typeof vtkPolyData.newInstance>;
    jumpsMapper: ReturnType<typeof vtkMapper.newInstance>;
    jumpsActor: ReturnType<typeof vtkActor.newInstance>;
    crosshairSource: ReturnType<typeof vtkSphereSource.newInstance>;
    crosshairMapper: ReturnType<typeof vtkMapper.newInstance>;
    crosshairActor: ReturnType<typeof vtkActor.newInstance>;
    picker: ReturnType<typeof vtkPointPicker.newInstance>;
  };
  const vtkRef = useRef<VtkHandles | null>(null);
  const pressPosition = useRef<{ x: number; y: number } | null>(null);
  const programmaticCameraChangeRef = useRef(false);
  const lastFramedMeshDir = useRef<string | null>(null);

  useEffect(() => {
    if (!container) return;
    const css = getComputedStyle(document.body);
    const tok = (n: string, fallback: string) => css.getPropertyValue(n).trim() || fallback;

    const grw = vtkGenericRenderWindow.newInstance({
      background: [...hexToRgb01(tok("--bg-sunken", TOKEN_FALLBACK.bgSunken)), 1],
    });
    grw.setContainer(container);
    grw.resize();
    const renderer = grw.getRenderer();
    const renderWindow = grw.getRenderWindow();

    const meshPolyData = vtkPolyData.newInstance();
    const meshMapper = vtkMapper.newInstance();
    meshMapper.setInputData(meshPolyData);
    const meshActor = vtkActor.newInstance();
    meshActor.setMapper(meshMapper);
    meshActor.getProperty().setColor(...hexToRgb01(tok("--ink-dim", TOKEN_FALLBACK.inkDim)));
    meshActor.getProperty().setLighting(true);
    meshActor.getProperty().setPointSize(2.5);
    renderer.addActor(meshActor);

    const jumpsPolyData = vtkPolyData.newInstance();
    const jumpsMapper = vtkMapper.newInstance();
    jumpsMapper.setInputData(jumpsPolyData);
    const jumpsActor = vtkActor.newInstance();
    jumpsActor.setMapper(jumpsMapper);
    jumpsActor.getProperty().setColor(...hexToRgb01(tok("--status-refused", TOKEN_FALLBACK.statusRefused)));
    jumpsActor.getProperty().setLighting(false);
    jumpsActor.getProperty().setLineWidth(1.6);
    renderer.addActor(jumpsActor);

    const crosshairSource = vtkSphereSource.newInstance({ radius: 1, thetaResolution: 16, phiResolution: 16 });
    const crosshairMapper = vtkMapper.newInstance();
    crosshairMapper.setInputConnection(crosshairSource.getOutputPort());
    const crosshairActor = vtkActor.newInstance();
    crosshairActor.setMapper(crosshairMapper);
    crosshairActor.getProperty().setColor(...hexToRgb01(tok("--accent", TOKEN_FALLBACK.accentCrosshair)));
    crosshairActor.getProperty().setLighting(false);
    crosshairActor.setVisibility(false);
    renderer.addActor(crosshairActor);

    renderer.getActiveCamera().setParallelProjection(true);

    const interactor = grw.getInteractor();
    interactor.setInteractorStyle(vtkInteractorStyleTrackballCamera.newInstance());
    const picker = vtkPointPicker.newInstance({ tolerance: 0.01 });
    const onLeftButtonPress = interactor.onLeftButtonPress((event) => {
      pressPosition.current = { x: event.position.x, y: event.position.y };
    });
    const onLeftButtonRelease = interactor.onLeftButtonRelease((event) => {
      const start = pressPosition.current;
      pressPosition.current = null;
      const current = vtkRef.current;
      const currentData = dataRef.current;
      const points = currentData?.points3;
      if (!start || !current || !points?.length) return;
      if (Math.hypot(event.position.x - start.x, event.position.y - start.y) > 5) return;
      picker.pick([event.position.x, event.position.y, 0], event.pokedRenderer);
      if (!picker.getActors().includes(current.meshActor)) return;
      const pointId = picker.getPointId();
      const point = pointId >= 0 ? points[pointId] : undefined;
      if (!point) return;
      spatial.setCrosshair([Math.round(point[2]), Math.round(point[1]), Math.round(point[0])]);
      const windingId = currentData?.winding_ids?.[pointId];
      if (typeof windingId === "number" && windingId >= 0) spatial.setSelectedWinding(windingId);
    });
    const onModified = renderer.getActiveCamera().onModified(() => {
      if (!programmaticCameraChangeRef.current) setHasInteracted(true);
    });

    const ro =
      typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(() => {
            grw.resize();
            renderWindow.render();
          })
        : null;
    ro?.observe(container);

    vtkRef.current = {
      grw, meshPolyData, meshMapper, meshActor, jumpsPolyData, jumpsMapper, jumpsActor,
      crosshairSource, crosshairMapper, crosshairActor, picker,
    };

    return () => {
      ro?.disconnect();
      onLeftButtonPress.unsubscribe();
      onLeftButtonRelease.unsubscribe();
      onModified.unsubscribe();
      vtkRef.current = null;
      grw.delete();
    };
  }, [container]);

  useEffect(() => {
    const v = vtkRef.current;
    if (!v || !centred) return;

    const n = centred.pts.length;
    const flatPts = new Float32Array(n * 3);
    centred.pts.forEach((p, i) => {
      flatPts[i * 3] = p[0];
      flatPts[i * 3 + 1] = p[1];
      flatPts[i * 3 + 2] = p[2];
    });
    const points = vtkPoints.newInstance();
    points.setData(flatPts, 3);
    v.meshPolyData.setPoints(points);

    const wids = activeSpatial ? data?.winding_ids : null;
    if (wids && wids.length === n) {
      const css = getComputedStyle(document.body);
      const flatColor = hexToRgb01(css.getPropertyValue("--ink-dim").trim() || TOKEN_FALLBACK.inkDim);
      const selectedId = activeSpatial ? spatial.selectedWindingId : null;
      const colors = new Uint8Array(n * 3);
      for (let i = 0; i < n; i++) {
        const id = wids[i] ?? -1;
        let rgb: Vec3;
        if (id < 0) {
          rgb = flatColor;
        } else if (selectedId === null || id === selectedId) {
          rgb = hsl2rgb01(hueForWinding(id), 0.7, id === selectedId ? 0.58 : 0.46);
        } else {
          rgb = hsl2rgb01(hueForWinding(id), 0.12, 0.32);
        }
        colors[i * 3] = Math.round(rgb[0] * 255);
        colors[i * 3 + 1] = Math.round(rgb[1] * 255);
        colors[i * 3 + 2] = Math.round(rgb[2] * 255);
      }
      v.meshPolyData
        .getPointData()
        .setScalars(vtkDataArray.newInstance({ name: "Colors", numberOfComponents: 3, values: colors }));
      v.meshMapper.setScalarVisibility(true);
      v.meshMapper.setScalarModeToUsePointData();
      v.meshMapper.setColorModeToDirectScalars();
    } else {
      v.meshMapper.setScalarVisibility(false);
    }

    if (activeSpatial && spatial.crosshair) {
      const [cz, cy, cx] = spatial.crosshair;
      const [ccx = 0, ccy = 0, ccz = 0] = centred.centre;
      v.crosshairSource.setCenter(cx - ccx, cy - ccy, cz - ccz);
      v.crosshairSource.setRadius(Math.max(centred.radius * 0.012, 0.5));
      v.crosshairActor.setVisibility(true);
    } else {
      v.crosshairActor.setVisibility(false);
    }

    const faces = data?.faces;
    if (faces && faces.length) {
      const cellData = new Uint32Array(faces.length * 4);
      faces.forEach((f, i) => {
        cellData[i * 4] = 3;
        cellData[i * 4 + 1] = f[0];
        cellData[i * 4 + 2] = f[1];
        cellData[i * 4 + 3] = f[2];
      });
      v.meshPolyData.setPolys(vtkCellArray.newInstance({ values: cellData }));
      v.meshPolyData.setVerts(vtkCellArray.newInstance({ values: new Uint32Array(0) }));
    } else {
      const vertData = new Uint32Array(n * 2);
      for (let i = 0; i < n; i++) {
        vertData[i * 2] = 1;
        vertData[i * 2 + 1] = i;
      }
      v.meshPolyData.setVerts(vtkCellArray.newInstance({ values: vertData }));
      v.meshPolyData.setPolys(vtkCellArray.newInstance({ values: new Uint32Array(0) }));
    }
    v.meshPolyData.modified();
    v.meshActor.getProperty().setOpacity(opacity);

    if (centred.jumps.length) {
      const jn = centred.jumps.length;
      const jflat = new Float32Array(jn * 2 * 3);
      centred.jumps.forEach((e, i) => {
        jflat[i * 6] = e.a[0];
        jflat[i * 6 + 1] = e.a[1];
        jflat[i * 6 + 2] = e.a[2];
        jflat[i * 6 + 3] = e.b[0];
        jflat[i * 6 + 4] = e.b[1];
        jflat[i * 6 + 5] = e.b[2];
      });
      const jpts = vtkPoints.newInstance();
      jpts.setData(jflat, 3);
      const jlines = new Uint32Array(jn * 3);
      for (let i = 0; i < jn; i++) {
        jlines[i * 3] = 2;
        jlines[i * 3 + 1] = i * 2;
        jlines[i * 3 + 2] = i * 2 + 1;
      }
      v.jumpsPolyData.setPoints(jpts);
      v.jumpsPolyData.setLines(vtkCellArray.newInstance({ values: jlines }));
    } else {
      v.jumpsPolyData.setPoints(vtkPoints.newInstance());
      v.jumpsPolyData.setLines(vtkCellArray.newInstance({ values: new Uint32Array(0) }));
    }
    v.jumpsPolyData.modified();
    v.jumpsActor.setVisibility(showJumps && centred.jumps.length > 0);

    const renderer = v.grw.getRenderer();
    if (lastFramedMeshDir.current !== data?.mesh_dir) {
      lastFramedMeshDir.current = data?.mesh_dir ?? null;
      programmaticCameraChangeRef.current = true;
      renderer.resetCamera();
      programmaticCameraChangeRef.current = false;
    }
    v.grw.getRenderWindow().render();
  }, [centred, data, showJumps, opacity, container, activeSpatial, spatial.selectedWindingId, spatial.crosshair]);

  const resetView = () => {
    const v = vtkRef.current;
    if (v) {
      programmaticCameraChangeRef.current = true;
      v.grw.getRenderer().resetCamera();
      programmaticCameraChangeRef.current = false;
      v.grw.getRenderWindow().render();
    }
    setHasInteracted(false);
  };

  const frame = (children: React.ReactNode) => (
    <div
      style={{
        height: height ?? "100%",
        minHeight: 260,
        display: "grid",
        placeContent: "center",
        justifyItems: "center",
        gap: 10,
        padding: 30,
        textAlign: "center",
      }}
    >
      {children}
    </div>
  );

  if (sealed)
    return frame(
      <>
        <Chip tone="active">Sealed</Chip>
        <p className="muted" style={{ maxWidth: 420 }}>
          This surface belongs to an active blinded experiment. The service refuses to
          release it, so there is nothing here to hide badly.
        </p>
      </>,
    );
  if (!meshDir)
    return frame(
      <>
        <Grid3x3 size={22} aria-hidden style={{ color: "var(--ink-faint)" }} />
        <p className="muted" style={{ maxWidth: 420 }}>
          {runId
            ? "This run recorded no mesh path, so there is no geometry to draw."
            : "Select a run to see its mesh geometry."}
        </p>
      </>,
    );
  if (err)
    return frame(
      <>
        <Chip tone="blocked">Geometry unavailable</Chip>
        <p className="muted" style={{ maxWidth: 440 }}>
          {err}
        </p>
        <p className="small faint" style={{ maxWidth: 440 }}>
          Unavailable is not a finding. Nothing has been measured or rejected here.
        </p>
      </>,
    );
  if (!data) return frame(<p className="muted">Reading the mesh…</p>);

  const faceCount = data.faces?.length ?? 0;

  return (
    <div
      className="wb-mesh-frame"
      style={{
        display: "grid",
        gridTemplateRows: "auto 1fr auto",
        height: height ?? undefined,
      }}
    >
      <header
        style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", padding: 8 }}
      >
        <strong className="small">MESH LATTICE</strong>
        <Chip tone={data.jump_edges.length ? "blocked" : "certified"}>
          {data.jump_edges.length ? `${data.jump_edges.length} jumps` : "sheet-following"}
        </Chip>
        <Chip tone={hasInteracted ? "active" : "certified"}>
          {hasInteracted ? "view adjusted" : `default view · ${data.projection.axes.join("/")}`}
        </Chip>
        {faceCount ? (
          <Chip tone="certified">{faceCount} triangles</Chip>
        ) : (
          <Chip tone="blocked">point cloud only -- no faces in this response</Chip>
        )}
        {activeSpatial && data.winding_ids ? (
          <Chip tone={spatial.selectedWindingId !== null ? "active" : "certified"}>
            {spatial.selectedWindingId !== null
              ? `winding ${spatial.selectedWindingId} highlighted`
              : "coloured by winding"}
          </Chip>
        ) : null}
        <label className="small" style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <input
            type="checkbox"
            checked={showJumps}
            onChange={(e) => setShowJumps(e.target.checked)}
          />
          highlight jumps
        </label>
        <label className="small" style={{ display: "flex", gap: 6, alignItems: "center" }}>
          opacity
          <input
            type="range"
            min={0.1}
            max={1}
            step={0.05}
            value={opacity}
            onChange={(e) => setOpacity(Number(e.target.value))}
          />
        </label>
        <button
          className="small"
          onClick={resetView}
          title="back to the default framing"
          style={{ display: "flex", gap: 5, alignItems: "center" }}
        >
          <RotateCcw size={13} aria-hidden /> reset view
        </button>
        <button
          className="small"
          aria-pressed={focusView}
          data-control="wb.mesh.focus"
          onClick={() => {
            setFocusView((v) => !v);
          }}
          title="hide surrounding Workbench chrome; press Escape to leave"
          style={{ display: "flex", gap: 5, alignItems: "center" }}
        >
          {focusView ? <Minimize2 size={13} aria-hidden /> : <Maximize2 size={13} aria-hidden />}
          {focusView ? "leave focus" : "focus view"}
        </button>
      </header>

      <div
        ref={setContainer}
        className="wb-mesh-viewfinder"
        style={{
          width: "100%",
          height: "100%",
          minHeight: 220,
          cursor: "grab",
          touchAction: "none",
        }}
      />

      <p className="small faint" style={{ padding: "4px 8px", margin: 0 }}>
        drag to turn · shift-drag to pan · wheel to zoom · {data.points3?.length ?? 0} cells,
        decimated by {data.decimation_step} · a real triangulated surface where the mesh has
        adjacent cells, this is not a render, and carries no intensity from the volume
      </p>
    </div>
  );
}

export default MeshCanvas;
