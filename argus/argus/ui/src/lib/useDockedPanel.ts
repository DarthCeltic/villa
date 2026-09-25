import { useCallback, useEffect, useRef, useState } from "react";

export const ZONES = ["TOP", "LEFT", "RIGHT", "BOTTOM", "FLOATING"] as const;
export type Zone = (typeof ZONES)[number];

export const SHEET_STOPS = ["COLLAPSED", "HALF", "FULL"] as const;
export type SheetStop = (typeof SHEET_STOPS)[number];

export type Pos = { x: number; y: number };

export type Layout = {
  zone: Zone;
  pos: Pos;
  size: number;
  collapsed: boolean;
  sheet: SheetStop;
  bottomChosen: boolean;
};

const DRAG_THRESHOLD_PX = 4;
const KEEP_VISIBLE_PX = 48;
export const SNAP_PX = 72;
export const MIN_SIZE = 220;
export const MAX_SIZE_FRACTION = 0.6;
export const NARROW_PX = 760;

export const DEFAULT_LAYOUT: Layout = {
  zone: "RIGHT",
  pos: { x: 24, y: 96 },
  size: 380,
  collapsed: true,
  sheet: "COLLAPSED",
  bottomChosen: false,
};

function vw() {
  return typeof window === "undefined" ? 1280 : window.innerWidth;
}
function vh() {
  return typeof window === "undefined" ? 800 : window.innerHeight;
}
export function isNarrow() {
  return vw() <= NARROW_PX;
}

export function clampPos(p: Pos, el?: { w: number; h: number } | null): Pos {
  const w = el?.w ?? 360;
  return {
    x: Math.min(Math.max(p.x, -Math.max(0, w - KEEP_VISIBLE_PX)), Math.max(0, vw() - KEEP_VISIBLE_PX)),
    y: Math.min(Math.max(p.y, 0), Math.max(0, vh() - KEEP_VISIBLE_PX)),
  };
}

export function clampSize(n: number, zone: Zone): number {
  const axis = zone === "BOTTOM" ? vh() : vw();
  return Math.round(Math.min(Math.max(n, MIN_SIZE), axis * MAX_SIZE_FRACTION));
}

export function zoneForPointer(x: number, y: number): Zone | null {
  if (y <= SNAP_PX) return "TOP";
  if (x <= SNAP_PX) return "LEFT";
  if (vw() - x <= SNAP_PX) return "RIGHT";
  if (vh() - y <= SNAP_PX) return "BOTTOM";
  return null;
}

export const LAPTOP_PX = 1440;

export function viewportClass(): "phone" | "laptop" | "desktop" {
  const w = vw();
  if (w <= NARROW_PX) return "phone";
  return w <= LAPTOP_PX ? "laptop" : "desktop";
}

function storageKeyFor(base: string) {
  return `${base}.${viewportClass()}`;
}

function readLayout(base: string): Layout {
  try {
    const raw = window.localStorage.getItem(storageKeyFor(base));
    if (!raw) return { ...DEFAULT_LAYOUT };
    const v = JSON.parse(raw) as Partial<Layout>;
    const requestedZone = (ZONES as readonly string[]).includes(String(v.zone))
      ? (v.zone as Zone)
      : DEFAULT_LAYOUT.zone;
    const zone = isNarrow() && requestedZone === "FLOATING" ? "BOTTOM" : requestedZone;
    const sheet = (SHEET_STOPS as readonly string[]).includes(String(v.sheet))
      ? (v.sheet as SheetStop)
      : DEFAULT_LAYOUT.sheet;
    const pos =
      typeof v.pos?.x === "number" && typeof v.pos?.y === "number"
        ? clampPos(v.pos as Pos)
        : { ...DEFAULT_LAYOUT.pos };
    return {
      zone,
      pos,
      size: clampSize(typeof v.size === "number" ? v.size : DEFAULT_LAYOUT.size, zone),
      collapsed: v.collapsed !== false,
      sheet,
      bottomChosen: v.bottomChosen === true,
    };
  } catch {
    return { ...DEFAULT_LAYOUT };
  }
}

function writeLayout(base: string, l: Layout) {
  try {
    window.localStorage.setItem(storageKeyFor(base), JSON.stringify(l));
  } catch {
  }
}

export type Measured = { w: number; h: number } | null;

export function extentFor(l: Layout, narrow: boolean, m: Measured): number {
  if (narrow) return Math.round(m?.h ?? 0);
  if (l.zone === "TOP" || l.zone === "FLOATING") return 0;
  if (l.zone === "BOTTOM") return Math.round(m?.h ?? (l.collapsed ? 0 : l.size));
  return Math.round(m?.w ?? (l.collapsed ? 0 : l.size));
}

function publish(l: Layout, narrow: boolean, m: Measured = null) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  root.dataset.diaryDock = narrow ? "SHEET" : l.zone;
  root.dataset.diaryCollapsed = l.collapsed ? "true" : "false";
  if (narrow) root.dataset.diarySheet = l.sheet;
  else delete root.dataset.diarySheet;
  root.style.setProperty("--diary-extent", `${extentFor(l, narrow, m)}px`);
  root.style.setProperty("--diary-size", `${l.size}px`);
}

export function useDockedPanel(storageBase: string) {
  const panelRef = useRef<HTMLElement | null>(null);
  const [narrow, setNarrow] = useState(() => isNarrow());
  const [layout, setLayout] = useState<Layout>(() =>
    typeof window === "undefined" ? { ...DEFAULT_LAYOUT } : readLayout(storageBase),
  );
  const [dragging, setDragging] = useState(false);
  const [preview, setPreview] = useState<Zone | null>(null);
  const [resizing, setResizing] = useState(false);

  const movedRef = useRef(false);
  const originRef = useRef<{ px: number; py: number; x: number; y: number } | null>(null);
  const resizeRef = useRef<{ px: number; py: number; size: number } | null>(null);

  const commit = useCallback(
    (next: Partial<Layout>) => {
      setLayout((cur) => {
        const merged: Layout = { ...cur, ...next };
        if (Object.prototype.hasOwnProperty.call(next, "zone")) {
          merged.bottomChosen = next.zone === "BOTTOM";
        }
        if (isNarrow() && merged.zone === "FLOATING") merged.zone = "BOTTOM";
        merged.size = clampSize(merged.size, merged.zone);
        merged.pos = clampPos(merged.pos, {
          w: panelRef.current?.offsetWidth ?? 360,
          h: panelRef.current?.offsetHeight ?? 120,
        });
        writeLayout(storageBase, merged);
        publish(merged, isNarrow());
        return merged;
      });
    },
    [storageBase],
  );

  const layoutRef = useRef(layout);
  layoutRef.current = layout;
  const narrowRef = useRef(narrow);
  narrowRef.current = narrow;

  const [panelEl, setPanelEl] = useState<HTMLElement | null>(null);
  const setPanel = useCallback((el: HTMLElement | null) => {
    panelRef.current = el;
    setPanelEl(el);
  }, []);

  const republish = useCallback(() => {
    const el = panelRef.current;
    publish(
      layoutRef.current,
      narrowRef.current,
      el ? { w: el.offsetWidth, h: el.offsetHeight } : null,
    );
  }, []);

  useEffect(() => {
    republish();
  }, [layout, narrow, panelEl, republish]);

  useEffect(() => {
    if (!panelEl || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => republish());
    ro.observe(panelEl);
    return () => ro.disconnect();
  }, [panelEl, republish]);

  useEffect(() => {
    const onResize = () => {
      const n = isNarrow();
      setNarrow(n);
      setLayout((cur) => {
        const fresh = readLayout(storageBase);
        const next = n ? { ...fresh, zone: fresh.zone === "FLOATING" ? "BOTTOM" : fresh.zone } : fresh;
        const merged: Layout = { ...next, collapsed: cur.collapsed };
        merged.pos = clampPos(merged.pos, {
          w: panelRef.current?.offsetWidth ?? 360,
          h: panelRef.current?.offsetHeight ?? 120,
        });
        merged.size = clampSize(merged.size, merged.zone);
        publish(merged, n);
        return merged;
      });
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [storageBase]);

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      if (e.button !== 0) return;
      originRef.current = { px: e.clientX, py: e.clientY, x: layout.pos.x, y: layout.pos.y };
      movedRef.current = false;
      try {
        (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
      } catch {
      }
    },
    [layout.pos.x, layout.pos.y],
  );

  const onPointerMove = useCallback(
    (e: React.PointerEvent) => {
      const o = originRef.current;
      if (!o) return;
      const dx = e.clientX - o.px;
      const dy = e.clientY - o.py;
      if (!movedRef.current && Math.hypot(dx, dy) < DRAG_THRESHOLD_PX) return;

      if (!movedRef.current) {
        movedRef.current = true;
        setDragging(true);
        const r = panelRef.current?.getBoundingClientRect();
        o.x = r ? r.left : layout.pos.x;
        o.y = r ? r.top : layout.pos.y;
      }
      setPreview(zoneForPointer(e.clientX, e.clientY));
      commit({ zone: "FLOATING", pos: { x: o.x + dx, y: o.y + dy } });
    },
    [commit, layout.pos.x, layout.pos.y],
  );

  const onPointerUp = useCallback(
    (e: React.PointerEvent) => {
      const wasDrag = movedRef.current;
      const target = preview;
      originRef.current = null;
      setDragging(false);
      setPreview(null);
      try {
        (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
      } catch {
      }
      if (wasDrag && target) commit({ zone: target });
    },
    [commit, preview],
  );

  const consumedClick = useCallback(() => {
    const was = movedRef.current;
    movedRef.current = false;
    return was;
  }, []);

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      const zoneKeys: Record<string, Zone> = {
        ArrowLeft: "LEFT",
        ArrowRight: "RIGHT",
        ArrowUp: "TOP",
        ArrowDown: "BOTTOM",
      };

      if (e.altKey && zoneKeys[e.key]) {
        e.preventDefault();
        commit({ zone: zoneKeys[e.key] });
        return;
      }

      if (narrow) {
        const dir = e.key === "ArrowUp" ? 1 : e.key === "ArrowDown" ? -1 : 0;
        if (!dir) return;
        e.preventDefault();
        const i = SHEET_STOPS.indexOf(layout.sheet);
        const next = SHEET_STOPS[Math.min(SHEET_STOPS.length - 1, Math.max(0, i + dir))];
        commit({ sheet: next, collapsed: next === "COLLAPSED" });
        return;
      }

      if (layout.zone !== "FLOATING") {
        const z = zoneKeys[e.key];
        if (!z) return;
        e.preventDefault();
        commit({ zone: z });
        return;
      }

      const step = e.shiftKey ? 24 : 6;
      const nudges: Record<string, Pos> = {
        ArrowLeft: { x: -step, y: 0 },
        ArrowRight: { x: step, y: 0 },
        ArrowUp: { x: 0, y: -step },
        ArrowDown: { x: 0, y: step },
      };
      const n = nudges[e.key];
      if (!n) return;
      e.preventDefault();
      const r = panelRef.current?.getBoundingClientRect();
      const base = layout.zone === "FLOATING" ? layout.pos : { x: r?.left ?? 0, y: r?.top ?? 0 };
      commit({ zone: "FLOATING", pos: { x: base.x + n.x, y: base.y + n.y } });
    },
    [commit, layout.pos, layout.sheet, layout.zone, narrow],
  );

  const onResizeDown = useCallback(
    (e: React.PointerEvent) => {
      if (e.button !== 0) return;
      e.stopPropagation();
      resizeRef.current = { px: e.clientX, py: e.clientY, size: layout.size };
      setResizing(true);
      try {
        (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
      } catch {
      }
    },
    [layout.size],
  );

  const onResizeMove = useCallback(
    (e: React.PointerEvent) => {
      const r = resizeRef.current;
      if (!r) return;
      e.stopPropagation();
      const d =
        layout.zone === "LEFT"
          ? e.clientX - r.px
          : layout.zone === "RIGHT"
            ? r.px - e.clientX
            : r.py - e.clientY;
      commit({ size: r.size + d });
    },
    [commit, layout.zone],
  );

  const onResizeUp = useCallback((e: React.PointerEvent) => {
    resizeRef.current = null;
    setResizing(false);
    try {
      (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
    } catch {
    }
  }, []);

  const onResizeKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      const step = e.shiftKey ? 48 : 16;
      const axis = layout.zone === "BOTTOM" ? "y" : "x";
      const grow: Record<string, number> =
        layout.zone === "LEFT"
          ? { ArrowRight: 1, ArrowLeft: -1 }
          : layout.zone === "RIGHT"
            ? { ArrowLeft: 1, ArrowRight: -1 }
            : { ArrowUp: 1, ArrowDown: -1 };
      const dir = grow[e.key];
      if (dir) {
        e.preventDefault();
        commit({ size: layout.size + dir * step });
        return;
      }
      if (e.key === "Home") {
        e.preventDefault();
        commit({ size: MIN_SIZE });
        return;
      }
      if (e.key === "End") {
        e.preventDefault();
        commit({ size: (axis === "y" ? vh() : vw()) * MAX_SIZE_FRACTION });
      }
    },
    [commit, layout.size, layout.zone],
  );

  const dockTo = useCallback((zone: Zone) => commit({ zone }), [commit]);
  const setCollapsed = useCallback((collapsed: boolean) => commit({ collapsed }), [commit]);
  const setSheet = useCallback((sheet: SheetStop) => commit({ sheet }), [commit]);
  const resetLayout = useCallback(() => {
    try {
      window.localStorage.removeItem(storageKeyFor(storageBase));
    } catch {
    }
    setLayout({ ...DEFAULT_LAYOUT });
    publish({ ...DEFAULT_LAYOUT }, isNarrow());
  }, [storageBase]);

  const floating = layout.zone === "FLOATING" && !narrow;
  const sideDocked = !narrow && (layout.zone === "LEFT" || layout.zone === "RIGHT");
  const bottomDocked = !narrow && layout.zone === "BOTTOM";

  const panelStyle: React.CSSProperties | undefined = narrow
    ? undefined
    : floating
      ? {
          position: "fixed",
          left: layout.pos.x,
          top: layout.pos.y,
          zIndex: 60,
          margin: 0,
          transition: dragging ? "none" : undefined,
        }
      : sideDocked
        ? {
            position: "fixed",
            width: layout.collapsed ? undefined : layout.size,
            zIndex: 40,
            margin: 0,
          }
        : bottomDocked
          ? {
              position: "fixed",
              height: layout.collapsed ? undefined : layout.size,
              zIndex: 40,
              margin: 0,
            }
          : undefined;

  const sizeMin = MIN_SIZE;
  const sizeMax = Math.round((bottomDocked ? vh() : vw()) * MAX_SIZE_FRACTION);

  return {
    layout,
    narrow,
    dragging,
    resizing,
    preview,
    floating,
    sideDocked,
    bottomDocked,
    panelRef,
    setPanel,
    panelStyle,
    sizeMin,
    sizeMax,
    dockTo,
    setCollapsed,
    setSheet,
    resetLayout,
    consumedClick,
    handleProps: {
      onPointerDown,
      onPointerMove,
      onPointerUp,
      onPointerCancel: onPointerUp,
      onKeyDown,
    },
    resizeProps: {
      onPointerDown: onResizeDown,
      onPointerMove: onResizeMove,
      onPointerUp: onResizeUp,
      onPointerCancel: onResizeUp,
      onKeyDown: onResizeKeyDown,
      tabIndex: 0,
    },
  };
}
