import { useCallback, useEffect, useState } from "react";

export type Density = "roomy" | "comfortable" | "compact";
export type DeviceClass = "desktop" | "laptop" | "phone";

export const DENSITIES: { key: Density; label: string; what: string }[] = [
  { key: "roomy", label: "Roomy", what: "the most space around every panel and control" },
  { key: "comfortable", label: "Comfortable", what: "balanced spacing" },
  { key: "compact", label: "Compact", what: "denser panels; text stays at the legibility floor" },
];

export const SCALE_MIN = 0.8;
export const SCALE_MAX = 1.25;
export const SCALE_STEP = 0.05;

const DENSITY_SPACE: Record<Density, number> = { roomy: 1.3, comfortable: 1.0, compact: 0.78 };

export interface DisplayPrefs {
  density: Density;
  scale: number;
}

export function deviceClass(width: number = typeof window === "undefined" ? 1600 : window.innerWidth): DeviceClass {
  if (width < 760) return "phone";
  if (width < 1480) return "laptop";
  return "desktop";
}

export function defaultsFor(cls: DeviceClass): DisplayPrefs {
  if (cls === "desktop") return { density: "roomy", scale: 1 };
  if (cls === "laptop") return { density: "comfortable", scale: 1 };
  return { density: "comfortable", scale: 1 };
}

const key = (cls: DeviceClass) => `argus.display.v1.${cls}`;

function clampScale(s: number): number {
  if (!Number.isFinite(s)) return 1;
  const stepped = Math.round(s / SCALE_STEP) * SCALE_STEP;
  return Math.min(SCALE_MAX, Math.max(SCALE_MIN, Number(stepped.toFixed(2))));
}

export function readPrefs(cls: DeviceClass): DisplayPrefs {
  const d = defaultsFor(cls);
  try {
    const raw = window.localStorage.getItem(key(cls));
    if (!raw) return d;
    const p = JSON.parse(raw) as Partial<DisplayPrefs>;
    const density = DENSITIES.some((x) => x.key === p.density) ? (p.density as Density) : d.density;
    return { density, scale: clampScale(typeof p.scale === "number" ? p.scale : d.scale) };
  } catch {
    return d;
  }
}

function writePrefs(cls: DeviceClass, p: DisplayPrefs) {
  try {
    window.localStorage.setItem(key(cls), JSON.stringify(p));
  } catch {
  }
}

export function applyPrefs(p: DisplayPrefs) {
  const root = document.documentElement;
  const space = DENSITY_SPACE[p.density] * p.scale;
  root.dataset.density = p.density;
  root.style.setProperty("--ui-scale", String(p.scale));
  root.style.setProperty("--ui-space", String(Number(space.toFixed(3))));
  root.style.setProperty("--ui-type", String(Math.max(1, p.scale)));
}

export function useDisplayPrefs() {
  const [cls, setCls] = useState<DeviceClass>(() => deviceClass());
  const [prefs, setPrefs] = useState<DisplayPrefs>(() => readPrefs(deviceClass()));

  useEffect(() => {
    const onResize = () => {
      const next = deviceClass();
      setCls((prev) => {
        if (prev !== next) setPrefs(readPrefs(next));
        return next;
      });
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    applyPrefs(prefs);
  }, [prefs]);

  const update = useCallback(
    (patch: Partial<DisplayPrefs>) => {
      setPrefs((prev) => {
        const next: DisplayPrefs = {
          density: patch.density ?? prev.density,
          scale: clampScale(patch.scale ?? prev.scale),
        };
        writePrefs(cls, next);
        return next;
      });
    },
    [cls],
  );

  const reset = useCallback(() => {
    const d = defaultsFor(cls);
    try {
      window.localStorage.removeItem(key(cls));
    } catch {
    }
    setPrefs(d);
  }, [cls]);

  return { prefs, deviceClass: cls, update, reset };
}
