import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../api";
import { WbViewerBar, type CompareMode, type ViewerTransform } from "./WbViewerBar";
import {
  WbLayerStudio,
  type SavedLayout,
  type StudioGroup,
  type StudioLayer,
} from "./WbLayerStudio";
import { NextStep } from "./workbench/Clamshell";
import { physicalToLevelPixel } from "../lib/pyramidTransform";
import { useWorkbenchMode } from "../lib/workbenchMode";
import "../theme/workbench.css";

export interface LayerStyle {
  layer: string;
  ramp: string;
  opacity: number;
  blend: string;
  invert: boolean;
  visible: boolean;
  gamma: number;
  threshold: number | null;
  group: string;
  meaning: string;
}

export interface Palette {
  contract: string;
  groups: { group: string; exclusive: boolean; what: string; layers: string[] }[];
  layers: LayerStyle[];
  available_ramps: { name: string; rgb: [number, number, number]; red_green_risk: boolean }[];
  blend_modes: string[];
  presets: { name: string; what: string }[];
  stack_check: { min_separation: number };
}

export interface Binding {
  source: string | null;
  state: "PRESENT" | "MISSING" | "VECTOR";
  why?: string;
}

export interface UnrollData {
  target: {
    name: string;
    pitch_um: number;
    physical_mm: number[];
    shape: number[];
    acquisition?: string;
  };
  result_class: { presentation: string; may_claim_discovery: boolean; not_established: string[] };
  banner_required_on_every_surface: string;
  classification?: string;
  levels: Record<string, { images: Record<string, { path: string }>; effective_pitch_um: number; readable: string; size: number[]; decimate: number; origin_xy?: number[] }>;
  layer_binding: Record<string, Binding>;
  sources?: Record<string, {
    kind: string;
    value_means: string;
    comparable: boolean;
    why_not_comparable?: string;
  }>;
  default_palette: Palette;
  ground_truth?: { state: string; why: string };
  review_tasks?: { tasks: { binding: { physical_coordinates: Record<string, number[]> } }[] } | null;
  file_base: string;
}

const BLEND_MAP: Record<string, GlobalCompositeOperation> = {
  normal: "source-over",
  screen: "screen",
  multiply: "multiply",
  difference: "difference",
};

const PER_LAYER = "per layer";

type Buf = { w: number; h: number; gray: Uint8Array };

function makeLut(st: LayerStyle, rgb: [number, number, number]) {
  const out = new Uint8Array(768);
  const [r, g, b] = rgb;
  for (let i = 0; i < 256; i++) {
    let v = i / 255;
    if (st.invert) v = 1 - v;
    if (st.threshold !== null && st.threshold !== undefined) v = v >= st.threshold ? 1 : 0;
    v = Math.pow(v, st.gamma);
    out[i * 3] = v * r * 255;
    out[i * 3 + 1] = v * g * 255;
    out[i * 3 + 2] = v * b * 255;
  }
  return out;
}

function sep(a: [number, number, number], b: [number, number, number]) {
  return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]) / Math.sqrt(3);
}

const GROUP_OF_CONTRACT: Record<string, StudioGroup> = {
  base: "Base",
  geometry: "Geometry",
  ink: "Ink",
  work: "Annotations",
};
const GROUP_OF_LAYER: Record<string, StudioGroup> = {
  validity: "Validation",
  provenance_coverage: "Validation",
  annotations: "Annotations",
  review_candidates: "Annotations",
};

function studioGroup(layer: string, contractGroup: string): StudioGroup {
  return (
    GROUP_OF_LAYER[layer] ?? GROUP_OF_CONTRACT[contractGroup] ?? "Other declared layers"
  );
}

interface StoredLayout {
  name: string;
  saved: string;
  styles: Record<string, LayerStyle>;
  order: string[];
  clip: boolean;
}

function layoutKey(fileBase: string) {
  return `argus.layerstudio.${fileBase || "unknown"}`;
}

function readLayouts(fileBase: string): StoredLayout[] {
  try {
    const raw = window.localStorage.getItem(layoutKey(fileBase));
    if (!raw) return [];
    const parsed = JSON.parse(raw) as StoredLayout[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function writeLayouts(fileBase: string, rows: StoredLayout[]) {
  try {
    window.localStorage.setItem(layoutKey(fileBase), JSON.stringify(rows));
  } catch {
  }
}

export function LayerStack({
  data,
  mode,
  controlsPortal,
}: {
  data: UnrollData;
  mode: "ink" | "overlay";
  controlsPortal?: HTMLElement | null;
}) {
  const palette = data.default_palette;
  const ramps = useMemo(
    () => Object.fromEntries(palette.available_ramps.map((r) => [r.name, r])),
    [palette],
  );

  const [styles, setStyles] = useState<Record<string, LayerStyle>>(() =>
    Object.fromEntries(palette.layers.map((l) => [l.layer, { ...l }])),
  );
  const [order, setOrder] = useState<string[]>(() => palette.layers.map((l) => l.layer));
  const [level, setLevel] = useState<string>(() => Object.keys(data.levels)[0] ?? "");
  const [clip, setClip] = useState(true);
  const [bufs, setBufs] = useState<Record<string, Buf> | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [transform, setTransform] = useState<ViewerTransform>({
    mode: "fit",
    zoom: 1,
    rotation: 0,
  });
  const [compare, setCompare] = useState<CompareMode>("single");
  const [wipe, setWipe] = useState(0.5);
  const [overlayBlend, setOverlayBlend] = useState<string>(PER_LAYER);
  const [focus, setFocus] = useState(false);
  useWorkbenchMode(focus);
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (focus) setFocus(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [focus]);
  const [layouts, setLayouts] = useState<StoredLayout[]>(() => readLayouts(data.file_base));
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const drag = useRef<{ x: number; y: number; left: number; top: number } | null>(null);

  const baseLayers = useMemo(() => {
    const g = palette.groups.find((x) => x.group === "base");
    return new Set(g?.layers ?? []);
  }, [palette]);

  useEffect(() => {
    setStyles((prev) => {
      const next = { ...prev };
      const cur = next["ct_texture"];
      if (cur) next["ct_texture"] = { ...cur, opacity: mode === "overlay" ? 0.55 : 1 };
      return next;
    });
  }, [mode]);

  useEffect(() => {
    setLayouts(readLayouts(data.file_base));
  }, [data.file_base]);

  useEffect(() => {
    let dead = false;
    setBufs(null);
    setErr(null);
    const lvl = data.levels[level];
    if (!lvl) return;
    const names = Object.keys(lvl.images);
    Promise.all(
      names.map(
        (n) =>
          new Promise<[string, Buf]>((res, rej) => {
            const im = new Image();
            im.crossOrigin = "anonymous";
            im.onload = () => {
              const c = document.createElement("canvas");
              c.width = im.naturalWidth;
              c.height = im.naturalHeight;
              const x = c.getContext("2d", { willReadFrequently: true })!;
              x.drawImage(im, 0, 0);
              const d = x.getImageData(0, 0, c.width, c.height).data;
              const g = new Uint8Array(c.width * c.height);
              for (let i = 0, j = 0; i < d.length; i += 4, j++) g[j] = d[i] ?? 0;
              res([n, { w: c.width, h: c.height, gray: g }]);
            };
            im.onerror = () => rej(new Error(`could not load layer plane "${n}"`));
            const entry = lvl.images[n];
            if (!entry) {
              rej(new Error(`layer plane "${n}" is not in the level index`));
              return;
            }
            im.src = api.fileUrl(entry.path);
          }),
      ),
    )
      .then((rows) => {
        if (!dead) setBufs(Object.fromEntries(rows));
      })
      .catch((e) => {
        if (!dead) setErr(String(e.message ?? e));
      });
    return () => {
      dead = true;
    };
  }, [data, level]);

  const blendOf = useCallback(
    (st: LayerStyle) =>
      overlayBlend !== PER_LAYER && !baseLayers.has(st.layer) ? overlayBlend : st.blend,
    [overlayBlend, baseLayers],
  );

  const composite = useCallback(
    (keep: (layer: string) => boolean): HTMLCanvasElement | null => {
      if (!bufs) return null;
      const any = Object.values(bufs)[0];
      if (!any) return null;
      const out = document.createElement("canvas");
      out.width = any.w;
      out.height = any.h;
      const ctx = out.getContext("2d");
      if (!ctx) return null;
      ctx.fillStyle = "#000";
      ctx.fillRect(0, 0, out.width, out.height);

      const validity = bufs["validity"]?.gray ?? null;
      for (const name of order) {
        const st = styles[name];
        const bind = data.layer_binding[name];
        if (!st?.visible || !bind || bind.state === "MISSING") continue;
        if (!keep(name)) continue;
        if (bind.state === "VECTOR") continue;
        const src = bind.source ? bufs[bind.source] : null;
        if (!src) continue;
        const rgb = ramps[st.ramp]?.rgb ?? [1, 1, 1];
        const lut = makeLut(st, rgb);
        const img = ctx.createImageData(src.w, src.h);
        const o = img.data;
        const g = src.gray;
        const gate: boolean = clip && validity !== null && name !== "validity";
        for (let i = 0, j = 0; i < g.length; i++, j += 4) {
          const v = (g[i] ?? 0) * 3;
          o[j] = lut[v] ?? 0;
          o[j + 1] = lut[v + 1] ?? 0;
          o[j + 2] = lut[v + 2] ?? 0;
          o[j + 3] = gate && validity ? ((validity[i] ?? 0) > 127 ? 255 : 0) : 255;
        }
        const tmp = document.createElement("canvas");
        tmp.width = src.w;
        tmp.height = src.h;
        tmp.getContext("2d")!.putImageData(img, 0, 0);
        ctx.globalAlpha = st.opacity;
        ctx.globalCompositeOperation = BLEND_MAP[blendOf(st)] ?? "source-over";
        ctx.drawImage(tmp, 0, 0, out.width, out.height);
      }
      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = "source-over";

      const rc = styles["review_candidates"];
      const tasks = data.review_tasks?.tasks ?? [];
      const lvl = data.levels[level];
      if (rc?.visible && keep("review_candidates") && tasks.length && lvl) {
        const [rr, rg, rb] = ramps[rc.ramp]?.rgb ?? [1, 1, 1];
        ctx.strokeStyle = `rgba(${rr * 255},${rg * 255},${rb * 255},${rc.opacity})`;
        ctx.lineWidth = 2;
        for (const t of tasks) {
          const pc = t.binding.physical_coordinates as Record<string, number[]>;
          const px = pc.pixel_xy ?? pc.patch_pixel_xy;
          if (!px) continue;
          const [x, y] = physicalToLevelPixel(px[0] ?? 0, px[1] ?? 0, lvl, out.width, out.height);
          if (x < 0 || y < 0 || x > out.width || y > out.height) continue;
          ctx.beginPath();
          ctx.arc(x, y, 13, 0, 7);
          ctx.stroke();
        }
      }
      return out;
    },
    [bufs, styles, order, clip, data, ramps, level, blendOf],
  );

  const overlaysOn = useMemo(
    () =>
      palette.layers.filter(
        (l) =>
          !baseLayers.has(l.layer) &&
          styles[l.layer]?.visible &&
          data.layer_binding[l.layer]?.state !== "MISSING",
      ).length,
    [palette.layers, baseLayers, styles, data.layer_binding],
  );

  const draw = useCallback(() => {
    const cv = canvasRef.current;
    if (!cv || !bufs) return;
    const full = composite(() => true);
    if (!full) return;
    cv.width = full.width;
    cv.height = full.height;
    const ctx = cv.getContext("2d");
    if (!ctx) return;
    ctx.globalCompositeOperation = "source-over";
    ctx.globalAlpha = 1;
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, cv.width, cv.height);

    if (compare === "single" || overlaysOn === 0) {
      ctx.drawImage(full, 0, 0);
      return;
    }
    const base = composite((l) => baseLayers.has(l));
    if (!base) {
      ctx.drawImage(full, 0, 0);
      return;
    }
    const x = Math.round(cv.width * wipe);
    if (compare === "wipe") {
      ctx.drawImage(full, 0, 0);
      ctx.save();
      ctx.beginPath();
      ctx.rect(0, 0, x, cv.height);
      ctx.clip();
      ctx.fillStyle = "#000";
      ctx.fillRect(0, 0, x, cv.height);
      ctx.drawImage(base, 0, 0);
      ctx.restore();
      ctx.strokeStyle = "#fff";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, cv.height);
      ctx.stroke();
      return;
    }
    const half = Math.max(1, x);
    ctx.save();
    ctx.beginPath();
    ctx.rect(0, 0, half, cv.height);
    ctx.clip();
    ctx.drawImage(base, 0, 0);
    ctx.restore();
    ctx.save();
    ctx.beginPath();
    ctx.rect(half + 2, 0, cv.width - half - 2, cv.height);
    ctx.clip();
    ctx.drawImage(full, 0, 0);
    ctx.restore();
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(half + 1, 0);
    ctx.lineTo(half + 1, cv.height);
    ctx.stroke();
  }, [bufs, composite, compare, wipe, baseLayers, overlaysOn]);

  useEffect(() => {
    draw();
  }, [draw]);

  const check = useMemo(() => {
    const vis = palette.layers
      .map((l) => styles[l.layer])
      .filter((s): s is LayerStyle =>
        Boolean(s && s.visible && data.layer_binding[s.layer]?.state === "PRESENT"));
    const problems: string[] = [];
    const warns = new Set<string>();
    for (const g of palette.groups) {
      if (!g.exclusive) continue;
      const on = g.layers.filter((l) => vis.some((v) => v.layer === l));
      if (on.length > 1)
        problems.push(`${on.join(", ")} are all ${g.group} layers — only one may be visible.`);
    }
    for (let i = 0; i < vis.length; i++)
      for (let j = i + 1; j < vis.length; j++) {
        const a = vis[i];
        const b = vis[j];
        if (!a || !b) continue;
        const ra = ramps[a.ramp];
        const rb = ramps[b.ramp];
        if (!ra || !rb) continue;
        if (a.invert !== b.invert) continue;
        if (a.ramp === b.ramp) {
          problems.push(`${a.layer} and ${b.layer} share the ramp ${a.ramp}.`);
          continue;
        }
        const d = sep(ra.rgb, rb.rgb);
        if (d < palette.stack_check.min_separation)
          problems.push(`${a.layer} and ${b.layer} are only ${d.toFixed(2)} apart in colour.`);
        if (ra.red_green_risk && rb.red_green_risk) warns.add(`${a.ramp} / ${b.ramp}`);
      }
    return { problems, warns: [...warns], count: vis.length };
  }, [styles, palette, ramps, data]);

  const applyPreset = async (name: string) => {
    try {
      const r = await fetch(api.fileUrl(`${data.file_base}\\preset_${name}.json`));
      if (!r.ok) return;
      const p: Palette = await r.json();
      setStyles(Object.fromEntries(p.layers.map((l) => [l.layer, { ...l }])));
      setOrder(p.layers.map((l) => l.layer));
    } catch {
    }
  };

  const patch = (layer: string, p: Partial<LayerStyle>) =>
    setStyles((prev) => {
      const cur = prev[layer];
      if (!cur) return prev;
      const next: Record<string, LayerStyle> = { ...prev, [layer]: { ...cur, ...p } };
      if (p.visible) {
        const g = palette.groups.find((gr) => gr.layers.includes(layer));
        if (g?.exclusive)
          for (const l of g.layers) {
            const other = next[l];
            if (l !== layer && other) next[l] = { ...other, visible: false };
          }
      }
      return next;
    });

  const move = (layer: string, delta: -1 | 1) =>
    setOrder((prev) => {
      const i = prev.indexOf(layer);
      if (i < 0) return prev;
      const j = i + delta;
      if (j < 0 || j >= prev.length) return prev;
      const next = [...prev];
      const a = next[i];
      const b = next[j];
      if (a === undefined || b === undefined) return prev;
      next[i] = b;
      next[j] = a;
      return next;
    });

  const reorder = (layer: string, before: string) =>
    setOrder((prev) => {
      const from = prev.indexOf(layer);
      const to = prev.indexOf(before);
      if (from < 0 || to < 0 || from === to) return prev;
      const next = prev.filter((l) => l !== layer);
      next.splice(prev.indexOf(before) > from ? to : to, 0, layer);
      return next;
    });


  const lvl = data.levels[level];

  const studioLayers: StudioLayer[] = useMemo(
    () =>
      palette.layers.map((meta) => {
        const st = styles[meta.layer] ?? meta;
        const bind = data.layer_binding[meta.layer];
        const contractGroup =
          palette.groups.find((g) => g.layers.includes(meta.layer))?.group ?? meta.group;
        const exclusive =
          palette.groups.find((g) => g.layers.includes(meta.layer))?.exclusive ?? false;
        const path = bind?.source ? (lvl?.images[bind.source]?.path ?? null) : null;
        return {
          layer: meta.layer,
          group: studioGroup(meta.layer, contractGroup),
          contractGroup,
          exclusive,
          visible: st.visible,
          ramp: st.ramp,
          opacity: st.opacity,
          gamma: st.gamma,
          threshold: st.threshold ?? null,
          blend: blendOf(st),
          invert: st.invert,
          meaning: meta.meaning,
          available: !!bind && bind.state !== "MISSING",
          vector: bind?.state === "VECTOR",
          reason:
            bind?.why ??
            (bind
              ? ""
              : "the layer contract declares no binding for this layer on this target, so nothing is known about it — which is not the same as nothing being there"),
          evidence: {
            path,
            href: path ? api.fileUrl(path) : null,
          },
          comparability: (() => {
            const s = data.sources?.[meta.layer];
            if (!s) return null;
            return {
              valueMeans: s.value_means,
              comparable: s.comparable,
              whyNotComparable: s.why_not_comparable,
            };
          })(),
        };
      }),
    [palette, styles, data.layer_binding, data.sources, lvl, blendOf],
  );

  const saved: SavedLayout[] = layouts.map((l) => ({ name: l.name, saved: l.saved }));

  const saveLayout = (name: string) => {
    const rows = [
      ...layouts.filter((l) => l.name !== name),
      { name, saved: new Date().toISOString().replace("T", " ").slice(0, 16), styles, order, clip },
    ];
    setLayouts(rows);
    writeLayouts(data.file_base, rows);
  };
  const applySaved = (name: string) => {
    const row = layouts.find((l) => l.name === name);
    if (!row) return;
    setStyles(row.styles);
    setOrder(row.order);
    setClip(row.clip);
  };
  const deleteSaved = (name: string) => {
    const rows = layouts.filter((l) => l.name !== name);
    setLayouts(rows);
    writeLayouts(data.file_base, rows);
  };

  const studio = (
    <WbLayerStudio
      layers={studioLayers}
      order={order}
      ramps={palette.available_ramps}
      blendModes={palette.blend_modes}
      presets={palette.presets}
      saved={saved}
      onPatch={(layer, p) =>
        patch(layer, {
          ...(p.visible === undefined ? {} : { visible: p.visible }),
          ...(p.ramp === undefined ? {} : { ramp: p.ramp }),
          ...(p.opacity === undefined ? {} : { opacity: p.opacity }),
          ...(p.gamma === undefined ? {} : { gamma: p.gamma }),
          ...(p.threshold === undefined ? {} : { threshold: p.threshold }),
          ...(p.blend === undefined ? {} : { blend: p.blend }),
          ...(p.invert === undefined ? {} : { invert: p.invert }),
        })
      }
      onMove={move}
      onReorder={reorder}
      onPreset={applyPreset}
      onSave={saveLayout}
      onApplySaved={applySaved}
      onDeleteSaved={deleteSaved}
      separability={check}
      onSafePreset={() => applyPreset("colour_blind_safe")}
      clip={clip}
      onClip={setClip}
    />
  );

  const pan = (dx: number, dy: number) => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollBy({ left: dx, top: dy, behavior: "smooth" });
  };

  const bar = (
    <WbViewerBar
      t={transform}
      onT={setTransform}
      compare={compare}
      onCompare={setCompare}
      compareWhy={
        overlaysOn === 0
          ? "nothing is visible above the base papyrus, so there is nothing to wipe off or split against"
          : null
      }
      wipe={wipe}
      onWipe={setWipe}
      overlayBlend={overlayBlend}
      onOverlayBlend={setOverlayBlend}
      blendModes={[PER_LAYER, ...palette.blend_modes]}
      onPan={pan}
      onReset={() => {
        setTransform({ mode: "fit", zoom: 1, rotation: 0 });
        setCompare("single");
        setWipe(0.5);
        const el = scrollRef.current;
        if (el) el.scrollTo({ left: 0, top: 0 });
      }}
      focus={focus}
      onFocus={(next) => {
        setFocus(next);
      }}
      depth={{
        available: false,
        why:
          `this export declares ${Object.keys(data.levels).length} resolution level(s) and no depth axis, so there is no plane to step through here — the CT surface-volume view has the same scrubber, live`,
      }}
      levels={Object.keys(data.levels)}
      level={level}
      onLevel={setLevel}
    />
  );

  const canvasStyle: React.CSSProperties =
    transform.mode === "fit"
      ? {
          maxWidth: "100%",
          maxHeight: focus ? "calc(100vh - 120px)" : 620,
          width: "auto",
          height: "auto",
          display: "block",
        }
      : { display: "block", imageRendering: "pixelated" };

  const stage = (
    <div
      className="ag-stage"
      data-wb-interactive="true"
      aria-label="Interactive layer canvas"
    >
      {bar}
      <div
        className="ag-stage-scroll"
        ref={scrollRef}
        onPointerDown={(e) => {
          const el = scrollRef.current;
          if (!el) return;
          drag.current = { x: e.clientX, y: e.clientY, left: el.scrollLeft, top: el.scrollTop };
        }}
        onPointerMove={(e) => {
          const el = scrollRef.current;
          const d = drag.current;
          if (!el || !d) return;
          el.scrollLeft = d.left - (e.clientX - d.x);
          el.scrollTop = d.top - (e.clientY - d.y);
        }}
        onPointerUp={() => {
          drag.current = null;
        }}
        onPointerLeave={() => {
          drag.current = null;
        }}
      >
        <div className="ag-stage-pad">
          {err ? (
            <p className="ag-stage-note" style={{ color: "var(--bad)" }}>
              {err}. This is the interface failing to read a plane, not a statement about the
              surface.{" "}
              <NextStep id="stack.plane" to="/sources?tab=holdings" label="See available material on Sources" />
            </p>
          ) : !bufs ? (
            <p className="ag-stage-note">Reading the layer planes…</p>
          ) : null}
          <div
            className="ag-canvas-wrap"
            style={{
              transform: `rotate(${transform.rotation}deg) scale(${transform.zoom})`,
            }}
          >
            <canvas ref={canvasRef} style={canvasStyle} data-wb="layer-canvas" />
          </div>
        </div>
      </div>
      <p className="ag-stage-note" data-wb="canvas-caption">
        {lvl ? `${lvl.effective_pitch_um} µm/px · ${lvl.readable}` : "no level index for this stack"}
        {compare === "single"
          ? ""
          : compare === "wipe"
            ? " · before/after: bare papyrus left of the handle, full stack right of it — the same place at the same pitch"
            : " · split: bare papyrus left, full stack right — the same place at the same pitch"}
        {transform.mode === "actual"
          ? " · 1:1, the only magnification at which two crops are comparable"
          : " · fitted to the frame, which is not a comparable magnification"}
      </p>
    </div>
  );

  return (
    <>
      <div className={focus ? "ag-focus" : "ls-canvas-only"} data-wb="layer-stage">
        {stage}
      </div>
      {controlsPortal ? createPortal(studio, controlsPortal) : studio}
    </>
  );
}
