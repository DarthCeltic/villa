import "../theme/workbench.css";

export type CompareMode = "single" | "wipe" | "split";

export interface ViewerTransform {
  mode: "fit" | "actual";
  zoom: number;
  rotation: number;
}

export function WbViewerBar({
  t,
  onT,
  compare,
  onCompare,
  compareWhy,
  wipe,
  onWipe,
  overlayBlend,
  onOverlayBlend,
  blendModes,
  onPan,
  onReset,
  focus,
  onFocus,
  depth,
  levels,
  level,
  onLevel,
}: {
  t: ViewerTransform;
  onT: (next: ViewerTransform) => void;
  compare: CompareMode;
  onCompare: (m: CompareMode) => void;
  compareWhy: string | null;
  wipe: number;
  onWipe: (v: number) => void;
  overlayBlend: string;
  onOverlayBlend: (v: string) => void;
  blendModes: string[];
  onPan: (dx: number, dy: number) => void;
  onReset: () => void;
  focus: boolean;
  onFocus: (v: boolean) => void;
  depth:
    | {
        available: true;
        value: number;
        max: number;
        onChange: (v: number) => void;
        reversed: boolean;
        onReverse: (v: boolean) => void;
      }
    | { available: false; why: string };
  levels: string[];
  level: string;
  onLevel: (l: string) => void;
}) {
  return (
    <div className="ag-viewbar" role="group" aria-label="Viewer controls">
      <div className="ag-viewbar-group">
        <button
          type="button"
          className="ag-btn"
          aria-pressed={t.mode === "fit"}
          data-control="wb.view.fit"
          title="bound the picture to the frame"
          onClick={() => onT({ ...t, mode: "fit", zoom: 1 })}
        >
          Fit
        </button>
        <button
          type="button"
          className="ag-btn"
          aria-pressed={t.mode === "actual"}
          data-control="wb.view.oneToOne"
          title="one canvas pixel per exported pixel — the only comparable magnification"
          onClick={() => onT({ ...t, mode: "actual", zoom: 1 })}
        >
          1:1
        </button>
        <button
          type="button"
          className="ag-btn"
          data-control="wb.view.zoomOut"
          aria-label="Zoom out"
          onClick={() => onT({ ...t, zoom: Math.max(0.1, Number((t.zoom - 0.25).toFixed(2))) })}
        >
          Zoom −
        </button>
        <span className="ag-depth-read" data-control="wb.view.zoomRead">
          {Math.round(t.zoom * 100)}%
        </span>
        <button
          type="button"
          className="ag-btn"
          data-control="wb.view.zoomIn"
          aria-label="Zoom in"
          onClick={() => onT({ ...t, zoom: Math.min(16, Number((t.zoom + 0.25).toFixed(2))) })}
        >
          Zoom +
        </button>
        <button
          type="button"
          className="ag-btn"
          data-control="wb.view.rotate"
          title="rotate the picture by a quarter turn"
          onClick={() => onT({ ...t, rotation: (t.rotation + 90) % 360 })}
        >
          Rotate {t.rotation}°
        </button>
      </div>

      <div className="ag-viewbar-group">
        <span className="ag-viewbar-label">Pan</span>
        <button type="button" className="ag-btn" data-control="wb.view.pan.left" aria-label="Pan left" onClick={() => onPan(-160, 0)}>
          ←
        </button>
        <button type="button" className="ag-btn" data-control="wb.view.pan.right" aria-label="Pan right" onClick={() => onPan(160, 0)}>
          →
        </button>
        <button type="button" className="ag-btn" data-control="wb.view.pan.up" aria-label="Pan up" onClick={() => onPan(0, -160)}>
          ↑
        </button>
        <button type="button" className="ag-btn" data-control="wb.view.pan.down" aria-label="Pan down" onClick={() => onPan(0, 160)}>
          ↓
        </button>
        <span className="ag-viewbar-label">or drag the picture</span>
      </div>

      <div className="ag-viewbar-group">
        <button
          type="button"
          className="ag-btn"
          aria-pressed={compare === "single"}
          data-control="wb.view.compare.single"
          onClick={() => onCompare("single")}
        >
          Full stack
        </button>
        <button
          type="button"
          className="ag-btn"
          aria-pressed={compare === "wipe"}
          disabled={!!compareWhy}
          data-control="wb.view.compare.wipe"
          title={compareWhy ?? "wipe between the bare papyrus and the full stack of the same place"}
          onClick={() => onCompare("wipe")}
        >
          Before / after
        </button>
        <button
          type="button"
          className="ag-btn"
          aria-pressed={compare === "split"}
          disabled={!!compareWhy}
          data-control="wb.view.compare.split"
          title={compareWhy ?? "bare papyrus on the left, full stack on the right"}
          onClick={() => onCompare("split")}
        >
          Split view
        </button>
        {compare === "wipe" || compare === "split" ? (
          <label className="ag-depth">
            <span className="ag-viewbar-label">{compare === "wipe" ? "Wipe" : "Split"}</span>
            <input
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={wipe}
              data-control="wb.view.compare.position"
              aria-label="Comparison position"
              onChange={(e) => onWipe(Number(e.target.value))}
            />
            <span className="ag-depth-read">{Math.round(wipe * 100)}%</span>
          </label>
        ) : null}
      </div>

      <div className="ag-viewbar-group">
        <label className="ag-filter-label">
          Overlay blend
          <select
            className="ag-filter-select"
            value={overlayBlend}
            data-control="wb.view.overlayBlend"
            title="applied to every visible layer above the base — a value-on-black plane drawn normal paints black where it is near zero and erases what it sits on"
            onChange={(e) => onOverlayBlend(e.target.value)}
          >
            {blendModes.map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </select>
        </label>
        {levels.length > 1 ? (
          <label className="ag-filter-label">
            Resolution level
            <select
              className="ag-filter-select"
              value={level}
              data-control="wb.view.level"
              onChange={(e) => onLevel(e.target.value)}
            >
              {levels.map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </div>

      <div className="ag-viewbar-group" style={{ flex: "1 1 240px" }}>
        {depth.available ? (
          <>
            <label className="ag-depth">
              <span className="ag-viewbar-label">Depth</span>
              <input
                type="range"
                min={0}
                max={Math.max(0, depth.max)}
                value={depth.value}
                data-control="wb.view.depth"
                aria-label="Depth plane"
                onChange={(e) => depth.onChange(Number(e.target.value))}
              />
              <span className="ag-depth-read">
                plane {depth.value} of {depth.max}
              </span>
            </label>
            <button
              type="button"
              className="ag-btn"
              aria-pressed={depth.reversed}
              data-control="wb.view.orientation"
              title="a wrong depth orientation is the failure upstream calls silent, so flipping the stack is one press"
              onClick={() => depth.onReverse(!depth.reversed)}
            >
              {depth.reversed ? "Depth reversed" : "Depth forward"}
            </button>
          </>
        ) : (
          <span className="ag-unavail-row" data-control="wb.view.depth.unavailable">
            <span>
              <span className="ag-unavail-name">Depth and orientation</span> — not applicable
              here
            </span>
            <span className="ag-unavail-why" title={depth.why}>
              {depth.why}
            </span>
          </span>
        )}
      </div>

      <div className="ag-viewbar-group" style={{ marginLeft: "auto" }}>
        <button type="button" className="ag-btn" data-control="wb.view.reset" onClick={onReset}>
          Reset view
        </button>
        <button
          type="button"
          className="ag-btn"
          aria-pressed={focus}
          data-control="wb.view.focus"
          title="the canvas alone, full screen, with this bar"
          onClick={() => onFocus(!focus)}
        >
          {focus ? "Leave focus mode" : "Focus mode"}
        </button>
      </div>
    </div>
  );
}
