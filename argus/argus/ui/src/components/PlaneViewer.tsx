import { useCallback, useEffect, useRef, useState } from "react";
import { Chip } from "./Status";
import "../theme/workbench.css";
import { NextStep, usePersistedChoice } from "./workbench/Clamshell";
import { useWorkbenchMode } from "../lib/workbenchMode";
import {
  Download,
  Eraser,
  Expand,
  FlipHorizontal2,
  FlipVertical2,
  Info,
  Keyboard,
  Minimize,
  Pencil,
  RotateCw,
  Wrench,
} from "lucide-react";

type Dims = { h: number; w: number };

type Index = {
  selected_scroll?: string | null;
  staged: string[];
  selected_fragment?: string | null;
  planes: Record<string, number>;
  dims?: Record<string, Dims>;
  not_reachable: string[];
  unmatched?: { fragment: string; scroll: string | null; status: string; why: string }[];
};

const STEP = 256;
const MIN_ZOOM = 0.25;
const MAX_ZOOM = 8;
const INFO_STATES = ["open", "closed"] as const;

function centerOf(dims: Dims | undefined): { y: number; x: number } {
  if (!dims || !dims.h || !dims.w) return { y: 3000, x: 2500 };
  return { y: Math.round(dims.h / 2), x: Math.round(dims.w / 2) };
}

function clampZoom(z: number): number {
  return Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, Number(z.toFixed(2))));
}

function themeColor(token: string, fallback: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(token).trim() || fallback;
}

export function PlaneViewer({ selectedScroll }: { selectedScroll: string | null }) {
  const [idx, setIdx] = useState<Index | null>(null);
  const [fragment, setFragment] = useState<string>("");
  const [plane, setPlane] = useState(32);
  const [pos, setPos] = useState({ y: 3000, x: 2500 });
  const [size] = useState({ h: 512, w: 512 });
  const [field, setField] = useState(8);
  const [zoom, setZoom] = useState(1);
  const [fit, setFit] = useState(true);
  const [auto, setAuto] = useState(false);
  const [reverse, setReverse] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [focusView, setFocusView] = useState(false);
  useWorkbenchMode(focusView);

  const [flipX, setFlipX] = useState(false);
  const [flipY, setFlipY] = useState(false);
  const [rotate, setRotate] = useState(0);
  const [origin, setOrigin] = useState("50% 50%");
  const [dragging, setDragging] = useState(false);
  const [drawMode, setDrawMode] = useState(false);
  const [cursorReadout, setCursorReadout] = useState<{ y: number; x: number } | null>(null);
  const [hovering, setHovering] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [showTools, setShowTools] = useState(false);
  const [infoState, setInfoState] = usePersistedChoice("plane.info", INFO_STATES, "closed");
  const showInfo = infoState === "open";

  const stageRef = useRef<HTMLElement | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const drawCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const drawingRef = useRef(false);
  const dragRef = useRef<{ x: number; y: number; startPos: { y: number; x: number }; renderedW: number } | null>(null);

  useEffect(() => {
    let dead = false;
    setIdx(null);
    setFragment("");
    setErr(null);
    if (!selectedScroll) return () => {
      dead = true;
    };
    fetch(`/api/planes?scroll=${encodeURIComponent(selectedScroll)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d: Index) => {
        if (dead) return;
        setIdx(d);
        setFragment(d.selected_fragment ?? "");
      })
      .catch((e) => !dead && setErr(String(e)));
    return () => {
      dead = true;
    };
  }, [selectedScroll]);

  useEffect(() => {
    if (!fragment) return;
    setPos(centerOf(idx?.dims?.[fragment]));
    setFlipX(false);
    setFlipY(false);
    setRotate(0);
    setOrigin("50% 50%");
  }, [fragment, idx]);

  const dims = idx?.dims?.[fragment];
  const n = idx?.planes[fragment] ?? 0;
  const fieldFits = !dims || (size.h * field <= dims.h && size.w * field <= dims.w);
  const src = fragment
    ? `/api/plane?fragment=${encodeURIComponent(fragment)}&plane=${plane}` +
      `&y=${pos.y}&x=${pos.x}&h=${size.h}&w=${size.w}` +
      `&auto=${auto}&reverse=${reverse}&zoom=${field}` +
      `&scroll=${encodeURIComponent(selectedScroll ?? "")}`
    : "";
  const call = fragment
    ? `argus.core.planes.crop('${fragment}', ${plane}, ${pos.y}, ${pos.x}, ` +
      `${size.h}, ${size.w}, auto=${auto ? "True" : "False"}, ` +
      `reverse=${reverse ? "True" : "False"}, zoom=${field})`
    : "";

  const step = useCallback((d: number) => setPlane((p) => Math.max(0, Math.min(Math.max(0, n - 1), p + d))), [n]);
  const pan = useCallback(
    (dy: number, dx: number) => setPos((p) => ({ y: Math.max(0, p.y + dy), x: Math.max(0, p.x + dx) })),
    [],
  );
  const resetView = useCallback(() => {
    setFit(true);
    setZoom(1);
    setField(8);
    setAuto(false);
    setReverse(false);
    setPlane(32);
    setPos(centerOf(dims));
    setFlipX(false);
    setFlipY(false);
    setRotate(0);
    setOrigin("50% 50%");
  }, [dims]);

  const clearDrawing = useCallback(() => {
    const c = drawCanvasRef.current;
    const ctx = c?.getContext("2d");
    if (c && ctx) ctx.clearRect(0, 0, c.width, c.height);
  }, []);

  useEffect(() => {
    clearDrawing();
  }, [src, clearDrawing]);

  const exportPng = useCallback(() => {
    const img = imgRef.current;
    if (!img || !img.naturalWidth) return;
    const w = img.naturalWidth;
    const h = img.naturalHeight;
    const captionH = 46;
    const out = document.createElement("canvas");
    out.width = w;
    out.height = h + captionH;
    const ctx = out.getContext("2d");
    if (!ctx) return;
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, out.width, out.height);
    ctx.save();
    ctx.translate(w / 2, h / 2);
    ctx.rotate((rotate * Math.PI) / 180);
    ctx.scale(flipX ? -1 : 1, flipY ? -1 : 1);
    ctx.drawImage(img, -w / 2, -h / 2, w, h);
    ctx.restore();
    if (drawCanvasRef.current) ctx.drawImage(drawCanvasRef.current, 0, 0, w, h);
    ctx.fillStyle = themeColor("--ink", "rgb(232,232,232)");
    ctx.font = "11px monospace";
    ctx.fillText(call, 8, h + 18);
    ctx.fillStyle = auto ? themeColor("--status-blocked", "rgb(224,160,44)") : themeColor("--status-certified", "rgb(76,175,125)");
    ctx.fillText(auto ? "auto contrast — NOT comparable" : "fixed contrast — comparable", 8, h + 34);
    if (flipX || flipY || rotate) {
      ctx.fillStyle = themeColor("--status-active", "rgb(143,180,255)");
      ctx.fillText(
        `display only: ${[flipX && "flipped h", flipY && "flipped v", rotate && `rotated ${rotate}°`].filter(Boolean).join(", ")}`,
        Math.min(340, w - 200),
        h + 34,
      );
    }
    const a = document.createElement("a");
    a.href = out.toDataURL("image/png");
    a.download = `${fragment}_plane${plane}_y${pos.y}_x${pos.x}.png`;
    a.click();
  }, [auto, call, flipX, flipY, fragment, plane, pos.x, pos.y, rotate]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onWheelNative = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const ox = ((e.clientX - rect.left) / rect.width) * 100;
      const oy = ((e.clientY - rect.top) / rect.height) * 100;
      setOrigin(`${Math.max(0, Math.min(100, ox))}% ${Math.max(0, Math.min(100, oy))}%`);
      setFit(false);
      setZoom((z) => clampZoom(z + (e.deltaY < 0 ? 0.25 : -0.25)));
    };
    el.addEventListener("wheel", onWheelNative, { passive: false });
    return () => el.removeEventListener("wheel", onWheelNative);
  }, []);

  const onPointerDownWrap = (e: React.PointerEvent<HTMLDivElement>) => {
    if (drawMode || e.button !== 0) return;
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
    const rect = imgRef.current?.getBoundingClientRect();
    dragRef.current = { x: e.clientX, y: e.clientY, startPos: pos, renderedW: rect?.width || size.w };
    setDragging(true);
  };
  const onPointerMoveWrap = (e: React.PointerEvent<HTMLDivElement>) => {
    if (dragRef.current) {
      const { x, y, startPos, renderedW } = dragRef.current;
      const volumePerScreen = field / Math.max(1, renderedW / size.w);
      setPos({
        y: Math.max(0, Math.round(startPos.y - (e.clientY - y) * volumePerScreen)),
        x: Math.max(0, Math.round(startPos.x - (e.clientX - x) * volumePerScreen)),
      });
    }
    if (!drawMode && rotate === 0) {
      const rect = imgRef.current?.getBoundingClientRect();
      if (rect) {
        const fx = (e.clientX - rect.left) / rect.width;
        const fy = (e.clientY - rect.top) / rect.height;
        if (fx >= 0 && fx <= 1 && fy >= 0 && fy <= 1) {
          const spanX = size.w * field;
          const spanY = size.h * field;
          const rx = flipX ? 1 - fx : fx;
          const ry = flipY ? 1 - fy : fy;
          setCursorReadout({
            x: Math.round(pos.x - spanX / 2 + rx * spanX),
            y: Math.round(pos.y - spanY / 2 + ry * spanY),
          });
        } else {
          setCursorReadout(null);
        }
      }
    }
  };
  const onPointerUpWrap = () => {
    dragRef.current = null;
    setDragging(false);
  };

  const drawAt = (e: React.PointerEvent<HTMLCanvasElement>, start: boolean) => {
    const c = drawCanvasRef.current;
    const ctx = c?.getContext("2d");
    if (!c || !ctx) return;
    const rect = c.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width) * c.width;
    const y = ((e.clientY - rect.top) / rect.height) * c.height;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.strokeStyle = themeColor("--status-refused", "rgb(255,59,59)");
    ctx.lineWidth = 3;
    if (start) {
      ctx.beginPath();
      ctx.moveTo(x, y);
    } else {
      ctx.lineTo(x, y);
      ctx.stroke();
    }
  };
  const onDrawDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    drawingRef.current = true;
    drawAt(e, true);
  };
  const onDrawMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (drawingRef.current) drawAt(e, false);
  };
  const onDrawUp = () => {
    drawingRef.current = false;
  };

  useEffect(() => {
    const onFsChange = () => setIsFullscreen(document.fullscreenElement === stageRef.current);
    document.addEventListener("fullscreenchange", onFsChange);
    return () => document.removeEventListener("fullscreenchange", onFsChange);
  }, []);
  const toggleFullscreen = () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else stageRef.current?.requestFullscreen?.();
  };

  useEffect(() => {
    if (!hovering) return;
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
      switch (e.key) {
        case "+":
        case "=":
          setFit(false);
          setZoom((z) => clampZoom(z + 0.25));
          break;
        case "-":
          setFit(false);
          setZoom((z) => clampZoom(z - 0.25));
          break;
        case "0":
        case "f":
        case "F":
          setFit(true);
          setZoom(1);
          setOrigin("50% 50%");
          break;
        case "1":
          setFit(false);
          setZoom(1);
          break;
        case "r":
        case "R":
          resetView();
          break;
        case "ArrowLeft":
          pan(0, -STEP);
          e.preventDefault();
          break;
        case "ArrowRight":
          pan(0, STEP);
          e.preventDefault();
          break;
        case "ArrowUp":
          pan(-STEP, 0);
          e.preventDefault();
          break;
        case "ArrowDown":
          pan(STEP, 0);
          e.preventDefault();
          break;
        case "h":
        case "H":
          setFlipX((v) => !v);
          break;
        case "v":
        case "V":
          setFlipY((v) => !v);
          break;
        case "[":
          setRotate((r) => (r + 270) % 360);
          break;
        case "]":
          setRotate((r) => (r + 90) % 360);
          break;
        case "d":
        case "D":
          setDrawMode((v) => !v);
          break;
        case "i":
        case "I":
          setInfoState((v) => (v === "open" ? "closed" : "open"));
          break;
        case "e":
        case "E":
          exportPng();
          break;
        case "?":
          setShowHelp((v) => !v);
          break;
        case "Escape":
          if (showHelp) setShowHelp(false);
          else if (drawMode) setDrawMode(false);
          else if (focusView) setFocusView(false);
          break;
        default:
          return;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [hovering, showHelp, drawMode, focusView, pan, resetView, exportPng, setInfoState]);

  if (!selectedScroll)
    return (
      <section className="ag-panel" data-testid="planes-refusal">
        <h3 className="ag-panel-title">No scroll is selected</h3>
        <p className="ag-prose">The plane viewer will not choose a global fragment. Select a physical scroll first.</p>
      </section>
    );
  if (err)
    return (
      <section className="ag-panel">
        <h3 className="ag-panel-title">The surface volumes could not be listed</h3>
        <p className="ag-prose">
          {err}. This is a report about the service, not a statement that no volume is staged.
        </p>
        <NextStep
          id="planes.error"
          to={`/system?tab=process&scroll=${encodeURIComponent(selectedScroll ?? "")}`}
          label="See what this scroll needs next on System"
        />
      </section>
    );
  if (!idx)
    return (
      <section className="ag-panel">
        <p className="ag-prose">Reading the staged surface volumes…</p>
      </section>
    );
  if (!fragment)
    return (
      <section className="ag-panel" data-testid="planes-refusal">
        <h3 className="ag-panel-title">No rendering is registered for {selectedScroll}</h3>
        <p className="ag-prose">
          The available staged image is not substituted because no local rendering identity
          matches this scroll. {idx.unmatched?.length
            ? `Available under another identity: ${idx.unmatched.map((a) =>
                a.scroll ? `${a.fragment} (${a.scroll})` : `${a.fragment} (identity not recorded)`).join(", ")}.`
            : "No matching surface volume is registered on this machine."}
          {idx.not_reachable.length
            ? ` Not reachable from here: ${idx.not_reachable.join(", ")}.`
            : ""}
        </p>
        <NextStep
          id="planes.none"
          to={`/system?tab=process&scroll=${encodeURIComponent(selectedScroll ?? "")}`}
          label="See what this scroll needs before surface viewing"
        />
        {idx.unmatched?.find((a) => a.scroll && a.scroll !== selectedScroll)?.scroll ? (
          <NextStep
            id="planes.matching-scroll"
            to={`/workbench?scroll=${encodeURIComponent(idx.unmatched.find((a) => a.scroll && a.scroll !== selectedScroll)?.scroll ?? "")}`}
            label={`Open the identity-matched rendering under ${idx.unmatched.find((a) => a.scroll && a.scroll !== selectedScroll)?.scroll}`}
          />
        ) : null}
      </section>
    );

  return (
    <section
      className={focusView ? "ag-stage ag-focus" : "ag-stage"}
      data-wb-interactive="true"
      aria-label="CT surface volume"
      ref={stageRef as React.RefObject<HTMLElement>}
      onMouseEnter={() => {
        setHovering(true);
      }}
      onMouseLeave={() => {
        setHovering(false);
        setCursorReadout(null);
      }}
    >
      {
}
      <div className="ag-toolbars">
        {
}
        <div className="ag-viewbar" role="group" aria-label="Plane viewer controls">
        <div className="ag-viewbar-group">
          <label className="ag-filter-label">
            Fragment
            <select
              className="ag-filter-select"
              value={fragment}
              data-control="wb.planes.fragment"
              onChange={(e) => setFragment(e.target.value)}
            >
              {idx.staged.map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="ag-depth">
          <span className="ag-viewbar-label">Depth</span>
          <button
            type="button"
            className="ag-btn"
            data-control="wb.planes.depth.down"
            aria-label="Previous plane"
            onClick={() => step(-1)}
          >
            −
          </button>
          <input
            type="range"
            min={0}
            max={Math.max(0, n - 1)}
            value={plane}
            data-control="wb.planes.depth"
            aria-label="Depth plane"
            onChange={(e) => setPlane(Number(e.target.value))}
          />
          <button
            type="button"
            className="ag-btn"
            data-control="wb.planes.depth.up"
            aria-label="Next plane"
            onClick={() => step(1)}
          >
            +
          </button>
          <span className="ag-depth-read">
            plane {plane} of {n}
          </span>
        </div>

        <div className="ag-viewbar-group">
          <button
            type="button"
            className="ag-btn"
            aria-pressed={fit}
            data-control="wb.planes.fit"
            onClick={() => {
              setFit(true);
              setZoom(1);
              setOrigin("50% 50%");
            }}
          >
            Fit
          </button>
          <button
            type="button"
            className="ag-btn"
            aria-pressed={!fit && zoom === 1}
            data-control="wb.planes.oneToOne"
            title="one screen pixel per sampled pixel — the only comparable magnification"
            onClick={() => {
              setFit(false);
              setZoom(1);
            }}
          >
            1:1
          </button>
          <button
            type="button"
            className="ag-btn"
            data-control="wb.planes.zoomOut"
            aria-label="Zoom out"
            onClick={() => {
              setFit(false);
              setZoom((z) => clampZoom(z - 0.25));
            }}
          >
            −
          </button>
          <span className="ag-depth-read">{Math.round(zoom * 100)}%</span>
          <button
            type="button"
            className="ag-btn"
            data-control="wb.planes.zoomIn"
            aria-label="Zoom in"
            onClick={() => {
              setFit(false);
              setZoom((z) => clampZoom(z + 0.25));
            }}
          >
            +
          </button>
          <span className="ag-viewbar-label" title="drag the image to pan; scroll the wheel to zoom on the pointer">
            drag to pan · scroll to zoom
          </span>
        </div>

        <div className="ag-viewbar-group">
          <button
            type="button"
            className="ag-btn"
            aria-pressed={showTools}
            data-control="wb.planes.tools"
            title="more tools: contrast, field of view, flip/rotate, draw, export"
            onClick={() => setShowTools((v) => !v)}
          >
            <Wrench size={15} aria-hidden /> Tools
          </button>
          <button
            type="button"
            className="ag-btn ag-btn-primary"
            aria-pressed={focusView}
            data-control="wb.planes.focus"
            title="hide everything except this canvas, full screen"
            onClick={() => {
              setFocusView((v) => !v);
            }}
          >
            {focusView ? "Exit focus" : "Focus view"}
          </button>
        </div>
      </div>

      {
}
      {showTools ? (
        <div className="ag-viewbar ag-viewbar-tools" role="group" aria-label="More plane viewer tools">
          <div className="ag-viewbar-group">
            <button
              type="button"
              className="ag-btn"
              aria-pressed={reverse}
              data-control="wb.planes.orientation"
              title="a wrong depth orientation is the failure upstream calls silent, so flipping the stack is one press"
              onClick={() => setReverse((v) => !v)}
            >
              {reverse ? "Depth reversed" : "Depth forward"}
            </button>
            <button
              type="button"
              className="ag-btn"
              aria-pressed={auto}
              data-control="wb.planes.autoContrast"
              title="auto contrast is chosen from this crop alone, so two auto crops are not comparable"
              onClick={() => setAuto((v) => !v)}
            >
              {auto ? "Auto contrast" : "Fixed contrast"}
            </button>
          </div>

          <div className="ag-viewbar-group">
            <label className="ag-filter-label">
              Field
              <select
                className="ag-filter-select"
                value={field}
                data-control="wb.planes.field"
                title="how much papyrus the crop covers. This changes what the service samples, not how large it is drawn."
                onChange={(e) => setField(Number(e.target.value))}
              >
                <option value={1}>full resolution</option>
                <option value={2}>2× wider</option>
                <option value={4}>4× wider</option>
                <option value={8}>8× wider</option>
                <option value={16}>16× wider</option>
              </select>
            </label>
            <button type="button" className="ag-btn" data-control="wb.planes.reset" onClick={resetView}>
              Reset view
            </button>
          </div>

          <div className="ag-viewbar-group">
            <button
              type="button"
              className="ag-btn"
              aria-pressed={flipX}
              data-control="wb.planes.flipH"
              title="mirror the display left/right — the volume itself is unchanged (h)"
              onClick={() => setFlipX((v) => !v)}
            >
              <FlipHorizontal2 size={15} aria-hidden /> Flip
            </button>
            <button
              type="button"
              className="ag-btn"
              aria-pressed={flipY}
              data-control="wb.planes.flipV"
              title="mirror the display top/bottom — the volume itself is unchanged (v)"
              onClick={() => setFlipY((v) => !v)}
            >
              <FlipVertical2 size={15} aria-hidden /> Flip
            </button>
            <button
              type="button"
              className="ag-btn"
              aria-pressed={rotate !== 0}
              data-control="wb.planes.rotate"
              title="rotate the display 90° — the volume itself is unchanged ([ / ])"
              onClick={() => setRotate((r) => (r + 90) % 360)}
            >
              <RotateCw size={15} aria-hidden /> {rotate}°
            </button>
          </div>

          <div className="ag-viewbar-group">
            <button
              type="button"
              className="ag-btn"
              aria-pressed={drawMode}
              data-control="wb.planes.draw"
              title="freehand markup on this crop only — cleared the moment the crop changes (d)"
              onClick={() => setDrawMode((v) => !v)}
            >
              <Pencil size={15} aria-hidden /> Draw
            </button>
            <button
              type="button"
              className="ag-btn"
              data-control="wb.planes.clearDraw"
              title="clear the current markup"
              onClick={clearDrawing}
            >
              <Eraser size={15} aria-hidden /> Clear
            </button>
            <button
              type="button"
              className="ag-btn"
              data-control="wb.planes.export"
              title="export this exact crop, with its markup and its reproducibility caption, as a PNG (e)"
              onClick={exportPng}
            >
              <Download size={15} aria-hidden /> Export
            </button>
          </div>

          <div className="ag-viewbar-group">
            <button
              type="button"
              className="ag-btn"
              aria-pressed={isFullscreen}
              data-control="wb.planes.fullscreen"
              title="browser fullscreen — the whole monitor, not just this tab"
              onClick={toggleFullscreen}
            >
              {isFullscreen ? <Minimize size={15} aria-hidden /> : <Expand size={15} aria-hidden />} Fullscreen
            </button>
            <button
              type="button"
              className="ag-btn"
              aria-pressed={showInfo}
              data-control="wb.planes.info"
              title="show the comparability status and the exact reproduction call (i)"
              onClick={() => setInfoState((v) => (v === "open" ? "closed" : "open"))}
            >
              <Info size={15} aria-hidden /> Details
            </button>
            <button
              type="button"
              className="ag-btn"
              aria-pressed={showHelp}
              data-control="wb.planes.help"
              title="keyboard shortcuts (also: ?)"
              onClick={() => setShowHelp((v) => !v)}
            >
              <Keyboard size={15} aria-hidden /> Shortcuts
            </button>
          </div>
        </div>
      ) : null}

      {showHelp ? (
        <div className="ag-shortcuts" role="dialog" aria-label="Keyboard shortcuts" data-wb="shortcuts-help">
          <h3 className="ag-panel-title">Keyboard shortcuts (while hovering the canvas)</h3>
          <dl className="ag-shortcuts-grid">
            <dt>Scroll</dt>
            <dd>zoom on the pointer</dd>
            <dt>Drag</dt>
            <dd>pan</dd>
            <dt>Arrows</dt>
            <dd>pan by {STEP}px</dd>
            <dt>+ / −</dt>
            <dd>zoom in / out</dd>
            <dt>0 / f</dt>
            <dd>fit</dd>
            <dt>1</dt>
            <dd>1:1</dd>
            <dt>r</dt>
            <dd>reset view</dd>
            <dt>h / v</dt>
            <dd>flip horizontal / vertical</dd>
            <dt>[ / ]</dt>
            <dd>rotate</dd>
            <dt>d</dt>
            <dd>toggle draw mode</dd>
            <dt>i</dt>
            <dd>toggle the info panel</dd>
            <dt>e</dt>
            <dd>export PNG</dd>
            <dt>Esc</dt>
            <dd>close this / exit draw / exit focus</dd>
          </dl>
          <button type="button" className="ag-btn" onClick={() => setShowHelp(false)}>
            Close
          </button>
        </div>
      ) : null}
      </div>

      <div className="ag-stage-scroll" ref={scrollRef}>
        <div className="ag-stage-pad">
          {fieldFits ? (
            <div
              className="ag-canvas-wrap"
              ref={wrapRef}
              style={{
                transform: `scale(${zoom * (flipX ? -1 : 1)}, ${zoom * (flipY ? -1 : 1)}) rotate(${rotate}deg)`,
                transformOrigin: origin,
                transition: dragging ? "none" : "transform 0.12s ease",
                cursor: drawMode ? "crosshair" : dragging ? "grabbing" : "grab",
                touchAction: "none",
              }}
              onPointerDown={onPointerDownWrap}
              onPointerMove={onPointerMoveWrap}
              onPointerUp={onPointerUpWrap}
              onPointerCancel={onPointerUpWrap}
            >
              <img
                ref={imgRef}
                src={src}
                alt={`${fragment} plane ${plane} at y${pos.y} x${pos.x}`}
                data-wb="plane-image"
                draggable={false}
                style={{
                  display: "block",
                  ...(fit ? { maxWidth: "100%", maxHeight: 620, width: "auto", height: "auto" } : {}),
                  imageRendering: "pixelated",
                  border: "1px solid var(--line)",
                  borderRadius: 4,
                }}
              />
              <canvas
                ref={drawCanvasRef}
                width={size.w}
                height={size.h}
                className="ag-draw-canvas"
                style={{ pointerEvents: drawMode ? "auto" : "none" }}
                onPointerDown={onDrawDown}
                onPointerMove={onDrawMove}
                onPointerUp={onDrawUp}
                onPointerCancel={onDrawUp}
              />
            </div>
          ) : (
            <div className="ag-panel" data-wb="plane-field-unavailable">
              <h3 className="ag-panel-title">This field of view is not available</h3>
              <p className="ag-prose">
                {field}× wider needs {size.w * field}×{size.h * field} source pixels.{" "}
                {fragment}'s plane is only {dims?.w}×{dims?.h}. Pick a narrower field, or a
                position further from the edge will not help — the volume itself is not big
                enough to fill this request.
              </p>
            </div>
          )}
          {cursorReadout ? (
            <span className="ag-cursor-hud" data-wb="cursor-hud">
              y {cursorReadout.y} · x {cursorReadout.x}
            </span>
          ) : null}
          {flipX || flipY || rotate ? (
            <span className="ag-display-badge" data-wb="display-transform-badge" title="display only — never applied to the data or the reproduction call">
              {[flipX && "flipped h", flipY && "flipped v", rotate && `rotated ${rotate}°`].filter(Boolean).join(" · ")}
            </span>
          ) : null}
        </div>
      </div>

      <div className="ag-stage-note">
        {showInfo ? (
          <>
            <span style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
              <Chip tone={auto ? "blocked" : "certified"} size="sm">
                {auto ? "auto — NOT comparable" : "fixed contrast — comparable"}
              </Chip>
              <Chip tone={reverse ? "blocked" : "active"} size="sm">
                {reverse ? "depth reversed" : "depth forward"}
              </Chip>
              <Chip tone={field > 1 ? "active" : "certified"} size="sm">
                {field > 1
                  ? `${size.w * field}×${size.h * field} px averaged to ${size.w}×${size.h}`
                  : "full resolution"}
              </Chip>
            </span>
            {auto ? (
              <p className="ag-prose">
                Auto contrast is chosen from this crop alone. Two auto crops are not comparable, and
                a shape that appears only here may be a property of the stretch.
              </p>
            ) : null}
            <p className="ag-item-id">{call}</p>
            {idx.not_reachable.length ? (
              <p className="ag-prose">
                Not reachable from here: {idx.not_reachable.join(", ")}. These are volumes the
                service knows of and cannot serve, which is different from volumes that do not
                exist.
              </p>
            ) : null}
          </>
        ) : (
          <button
            type="button"
            className="ag-btn ag-btn-quiet"
            data-control="wb.planes.info.reveal"
            onClick={() => setInfoState("open")}
          >
            <Info size={13} aria-hidden /> Comparability status &amp; reproduction call
          </button>
        )}
      </div>
    </section>
  );
}
