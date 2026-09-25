import { useCallback, useEffect, useRef, useState } from "react";

export const NATURAL_W = 1672;
export const NATURAL_H = 941;
export const ZOOM_MIN = 0.2;
export const ZOOM_MAX = 3;
const STEP = 1.2;

export interface View {
  scale: number;
  tx: number;
  ty: number;
}

const storageKey = (cls: string) => `argus.archive.view.v2.${cls}`;

const SHELF_ROW_Y = 470;

function clamp(v: number, lo: number, hi: number) {
  return Math.min(hi, Math.max(lo, v));
}

export function useArchiveView(deviceClass: string, onSwipe?: (dir: 1 | -1) => void) {
  const swipeStart = useRef<{ x: number; y: number } | null>(null);
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [view, setView] = useState<View | null>(null);
  const [fitScale, setFitScale] = useState(1);
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const pinch = useRef<{ dist: number; scale: number; cx: number; cy: number } | null>(null);
  const drag = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null);

  const computeFit = useCallback((): View | null => {
    const host = hostRef.current;
    if (!host) return null;
    const w = host.clientWidth;
    const h = host.clientHeight;
    if (!w || !h) return null;
    const byWidth = w / NATURAL_W;
    const byHeight = h / NATURAL_H;
    const cover = deviceClass !== "phone";
    const s = cover ? Math.max(byWidth, byHeight) : Math.min(byWidth, byHeight);
    const tx = (w - NATURAL_W * s) / 2;
    const imgH = NATURAL_H * s;
    const ty = imgH > h
      ? clamp(h * 0.56 - SHELF_ROW_Y * s, h - imgH, 0)
      : (h - imgH) / 2;
    return { scale: s, tx, ty };
  }, [deviceClass]);

  const autoFitRef = useRef(false);

  useEffect(() => {
    const fit = computeFit();
    if (!fit) return;
    setFitScale(fit.scale);
    let stored: View | null = null;
    try {
      const raw = window.localStorage.getItem(storageKey(deviceClass));
      if (raw) {
        const v = JSON.parse(raw) as View;
        if ([v.scale, v.tx, v.ty].every((n) => Number.isFinite(n))) stored = v;
      }
    } catch {
      stored = null;
    }
    autoFitRef.current = !stored;
    setView(stored ?? fit);
    const onResize = () => {
      const f = computeFit();
      if (!f) return;
      setFitScale(f.scale);
      if (autoFitRef.current) setView(f);
    };
    window.addEventListener("resize", onResize);
    const host = hostRef.current;
    let ro: ResizeObserver | null = null;
    if (host && typeof ResizeObserver !== "undefined") {
      ro = new ResizeObserver(onResize);
      ro.observe(host);
    }
    return () => {
      window.removeEventListener("resize", onResize);
      ro?.disconnect();
    };
  }, [computeFit, deviceClass]);

  const commit = useCallback(
    (v: View) => {
      autoFitRef.current = false;
      const next = { ...v, scale: clamp(v.scale, ZOOM_MIN, ZOOM_MAX) };
      setView(next);
      try {
        window.localStorage.setItem(storageKey(deviceClass), JSON.stringify(next));
      } catch {
      }
    },
    [deviceClass],
  );

  const zoomAt = useCallback(
    (factor: number, px?: number, py?: number) => {
      const host = hostRef.current;
      if (!host || !view) return;
      const cx = px ?? host.clientWidth / 2;
      const cy = py ?? host.clientHeight / 2;
      const s = clamp(view.scale * factor, ZOOM_MIN, ZOOM_MAX);
      const k = s / view.scale;
      commit({ scale: s, tx: cx - (cx - view.tx) * k, ty: cy - (cy - view.ty) * k });
    },
    [view, commit],
  );

  const fit = useCallback(() => {
    const f = computeFit();
    if (f) commit(f);
  }, [computeFit, commit]);

  const actual = useCallback(() => {
    const host = hostRef.current;
    if (!host || !view) return;
    const cx = host.clientWidth / 2;
    const cy = host.clientHeight / 2;
    const k = 1 / view.scale;
    commit({ scale: 1, tx: cx - (cx - view.tx) * k, ty: cy - (cy - view.ty) * k });
  }, [view, commit]);

  const reset = useCallback(() => {
    try {
      window.localStorage.removeItem(storageKey(deviceClass));
    } catch {
    }
    autoFitRef.current = true;
    const f = computeFit();
    if (f) setView(f);
  }, [computeFit, deviceClass]);

  const pan = useCallback(
    (dx: number, dy: number) => {
      if (view) commit({ ...view, tx: view.tx + dx, ty: view.ty + dy });
    },
    [view, commit],
  );


  const onWheel = useCallback(
    (e: React.WheelEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
      zoomAt(e.deltaY < 0 ? STEP : 1 / STEP, e.clientX - rect.left, e.clientY - rect.top);
    },
    [zoomAt],
  );

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      if ((e.target as HTMLElement).closest(
        "[data-archive-object], [data-archive-ui], button, a, input, select, textarea, summary, [role=toolbar], [role=dialog]",
      )) return;
      (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
      pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pointers.current.size === 1 && view) {
        swipeStart.current = { x: e.clientX, y: e.clientY };
        drag.current = { x: e.clientX, y: e.clientY, tx: view.tx, ty: view.ty };
      } else if (pointers.current.size === 2 && view) {
        const [a, b] = [...pointers.current.values()];
        if (!a || !b) return;
        const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
        pinch.current = {
          dist: Math.hypot(a.x - b.x, a.y - b.y),
          scale: view.scale,
          cx: (a.x + b.x) / 2 - rect.left,
          cy: (a.y + b.y) / 2 - rect.top,
        };
        drag.current = null;
      }
    },
    [view],
  );

  const onPointerMove = useCallback(
    (e: React.PointerEvent) => {
      if (!pointers.current.has(e.pointerId) || !view) return;
      pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pinch.current && pointers.current.size === 2) {
        const [a, b] = [...pointers.current.values()];
        if (!a || !b) return;
        const d = Math.hypot(a.x - b.x, a.y - b.y);
        const target = clamp(pinch.current.scale * (d / pinch.current.dist), ZOOM_MIN, ZOOM_MAX);
        zoomAt(target / view.scale, pinch.current.cx, pinch.current.cy);
      } else if (drag.current && view.scale > fitScale * 1.02) {
        setView({
          ...view,
          tx: drag.current.tx + (e.clientX - drag.current.x),
          ty: drag.current.ty + (e.clientY - drag.current.y),
        });
      }
    },
    [view, zoomAt, fitScale],
  );

  const onPointerUp = useCallback(
    (e: React.PointerEvent) => {
      pointers.current.delete(e.pointerId);
      if (pointers.current.size < 2) pinch.current = null;
      if (pointers.current.size === 0) {
        const start = swipeStart.current;
        swipeStart.current = null;
        drag.current = null;
        const atFit = view ? view.scale <= fitScale * 1.02 : true;
        if (atFit && start && onSwipe) {
          const dx = e.clientX - start.x;
          const dy = e.clientY - start.y;
          if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5) {
            onSwipe(dx < 0 ? 1 : -1);
            return;
          }
        }
        if (view && !atFit) commit(view);
      }
    },
    [view, commit, fitScale, onSwipe],
  );

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      const k = e.key;
      if (k === "+" || k === "=") { e.preventDefault(); zoomAt(STEP); }
      else if (k === "-" || k === "_") { e.preventDefault(); zoomAt(1 / STEP); }
      else if (k === "0") { e.preventDefault(); fit(); }
      else if (k === "1") { e.preventDefault(); actual(); }
      else if (e.shiftKey && k === "ArrowLeft") { e.preventDefault(); pan(60, 0); }
      else if (e.shiftKey && k === "ArrowRight") { e.preventDefault(); pan(-60, 0); }
      else if (e.shiftKey && k === "ArrowUp") { e.preventDefault(); pan(0, 60); }
      else if (e.shiftKey && k === "ArrowDown") { e.preventDefault(); pan(0, -60); }
    },
    [zoomAt, fit, actual, pan],
  );

  return {
    hostRef,
    view,
    fitScale,
    percent: view ? Math.round(view.scale * 100) : 100,
    zoomIn: () => zoomAt(STEP),
    zoomOut: () => zoomAt(1 / STEP),
    fit,
    actual,
    reset,
    handlers: { onWheel, onPointerDown, onPointerMove, onPointerUp, onPointerCancel: onPointerUp,
                onKeyDown },
  };
}
