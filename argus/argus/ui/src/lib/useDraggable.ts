import { useCallback, useEffect, useRef, useState } from "react";

export type Pos = { x: number; y: number };

const DRAG_THRESHOLD_PX = 4;

const KEEP_VISIBLE_PX = 48;

function clamp(p: Pos, size: { w: number; h: number }): Pos {
  const vw = typeof window === "undefined" ? 1280 : window.innerWidth;
  const vh = typeof window === "undefined" ? 800 : window.innerHeight;
  const maxX = Math.max(0, vw - Math.min(size.w, vw) );
  const maxY = Math.max(0, vh - KEEP_VISIBLE_PX);
  return {
    x: Math.min(Math.max(p.x, -Math.max(0, size.w - KEEP_VISIBLE_PX)), maxX),
    y: Math.min(Math.max(p.y, 0), maxY),
  };
}

function read(key: string): { docked: boolean; pos: Pos } | null {
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return null;
    const v = JSON.parse(raw);
    if (typeof v?.pos?.x !== "number" || typeof v?.pos?.y !== "number") return null;
    return { docked: v.docked !== false, pos: { x: v.pos.x, y: v.pos.y } };
  } catch {
    return null;
  }
}

export function useDraggable(storageKey: string) {
  const handleRef = useRef<HTMLElement | null>(null);
  const panelRef = useRef<HTMLElement | null>(null);

  const initial = typeof window === "undefined" ? null : read(storageKey);
  const [docked, setDocked] = useState<boolean>(initial ? initial.docked : true);
  const [pos, setPos] = useState<Pos>(initial ? initial.pos : { x: 24, y: 96 });

  const [dragging, setDragging] = useState(false);
  const movedRef = useRef(false);
  const originRef = useRef<{ px: number; py: number; x: number; y: number } | null>(null);

  const size = useCallback(() => {
    const el = panelRef.current;
    return { w: el?.offsetWidth ?? 360, h: el?.offsetHeight ?? 120 };
  }, []);

  const persist = useCallback(
    (d: boolean, p: Pos) => {
      try {
        window.localStorage.setItem(storageKey, JSON.stringify({ docked: d, pos: p }));
      } catch {
      }
    },
    [storageKey],
  );

  const move = useCallback(
    (p: Pos) => {
      const c = clamp(p, size());
      setPos(c);
      persist(false, c);
    },
    [persist, size],
  );

  const dock = useCallback(() => {
    setDocked(true);
    setDragging(false);
    persist(true, pos);
  }, [persist, pos]);

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      if (e.button !== 0) return;
      originRef.current = { px: e.clientX, py: e.clientY, x: pos.x, y: pos.y };
      movedRef.current = false;
      try {
        (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
      } catch {
      }
    },
    [pos.x, pos.y],
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
        if (docked) {
          const r = panelRef.current?.getBoundingClientRect();
          o.x = r ? r.left : pos.x;
          o.y = r ? r.top : pos.y;
          setDocked(false);
        }
      }
      move({ x: o.x + dx, y: o.y + dy });
    },
    [docked, move, pos.x, pos.y],
  );

  const onPointerUp = useCallback(
    (e: React.PointerEvent) => {
      originRef.current = null;
      setDragging(false);
      try {
        (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
      } catch {
      }
    },
    [],
  );

  const consumedClick = useCallback(() => {
    const was = movedRef.current;
    movedRef.current = false;
    return was;
  }, []);

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      const step = e.shiftKey ? 24 : 6;
      const d: Record<string, Pos> = {
        ArrowLeft: { x: -step, y: 0 },
        ArrowRight: { x: step, y: 0 },
        ArrowUp: { x: 0, y: -step },
        ArrowDown: { x: 0, y: step },
      };
      const v = d[e.key];
      if (!v) return;
      e.preventDefault();
      const base = docked
        ? (() => {
            const r = panelRef.current?.getBoundingClientRect();
            return r ? { x: r.left, y: r.top } : pos;
          })()
        : pos;
      setDocked(false);
      move({ x: base.x + v.x, y: base.y + v.y });
    },
    [docked, move, pos],
  );

  useEffect(() => {
    if (docked) return;
    const onResize = () => setPos((p) => clamp(p, size()));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [docked, size]);

  useEffect(() => {
    if (docked) return;
    setPos((p) => clamp(p, size()));
  }, []);

  return {
    docked,
    pos,
    dragging,
    dock,
    panelRef,
    handleRef,
    consumedClick,
    handleProps: {
      onPointerDown,
      onPointerMove,
      onPointerUp,
      onPointerCancel: onPointerUp,
      onKeyDown,
    },
    panelStyle: docked
      ? undefined
      : ({
          position: "fixed",
          left: pos.x,
          top: pos.y,
          zIndex: 60,
          margin: 0,
          transition: dragging ? "none" : undefined,
        } as React.CSSProperties),
  };
}
