import { useEffect, useRef, useState } from "react";
import { Minus, MonitorCog, Plus, RotateCcw } from "lucide-react";
import {
  DENSITIES, SCALE_MAX, SCALE_MIN, SCALE_STEP, type DisplayPrefs, type DeviceClass,
} from "../lib/displayPrefs";

export function DisplayControl({
  prefs, deviceClass, update, reset,
}: {
  prefs: DisplayPrefs;
  deviceClass: DeviceClass;
  update: (p: Partial<DisplayPrefs>) => void;
  reset: () => void;
}) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        wrap.current?.querySelector<HTMLButtonElement>("button[data-control='display.open']")?.focus();
      }
    };
    const onDown = (e: PointerEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", onDown);
    };
  }, [open]);

  const pct = Math.round(prefs.scale * 100);
  const step = (dir: 1 | -1) => update({ scale: prefs.scale + dir * SCALE_STEP });

  return (
    <div className="dsp" ref={wrap}>
      <button
        type="button"
        className="dsp-open interactive"
        data-control="display.open"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((o) => !o)}
        title="Display density and interface scale"
      >
        <MonitorCog size={18} strokeWidth={1.8} aria-hidden="true" />
        <span>Display</span>
      </button>

      {open && (
        <div className="dsp-panel" role="dialog" aria-label="Display">
          <fieldset className="dsp-group">
            <legend>Density</legend>
            <div className="dsp-seg" role="radiogroup" aria-label="Density">
              {DENSITIES.map((d) => (
                <button
                  key={d.key}
                  type="button"
                  role="radio"
                  aria-checked={prefs.density === d.key}
                  className="dsp-seg-btn"
                  data-control={`display.density.${d.key}`}
                  onClick={() => update({ density: d.key })}
                  title={d.what}
                >
                  {d.label}
                </button>
              ))}
            </div>
          </fieldset>

          <fieldset className="dsp-group">
            <legend>Interface scale</legend>
            <div className="dsp-scale">
              <button type="button" className="dsp-step" onClick={() => step(-1)}
                      disabled={prefs.scale <= SCALE_MIN + 1e-9}
                      data-control="display.scale.down" aria-label="Smaller interface">
                <Minus size={18} aria-hidden="true" />
              </button>
              <input
                type="range"
                min={SCALE_MIN}
                max={SCALE_MAX}
                step={SCALE_STEP}
                value={prefs.scale}
                onChange={(e) => update({ scale: Number(e.target.value) })}
                aria-label="Interface scale"
                aria-valuetext={`${pct} percent`}
                data-control="display.scale"
              />
              <button type="button" className="dsp-step" onClick={() => step(1)}
                      disabled={prefs.scale >= SCALE_MAX - 1e-9}
                      data-control="display.scale.up" aria-label="Larger interface">
                <Plus size={18} aria-hidden="true" />
              </button>
              <output className="dsp-pct" aria-live="polite">{pct}%</output>
            </div>
          </fieldset>

          <p className="dsp-note">
            Saved for this {deviceClass}. Text never drops below the 15&nbsp;px floor, and
            nothing scientific changes: images, canvases, coordinates and exported evidence are
            untouched.
          </p>

          <button type="button" className="dsp-reset" onClick={reset} data-control="display.reset">
            <RotateCcw size={16} aria-hidden="true" /> Reset to the {deviceClass} default
          </button>
        </div>
      )}
    </div>
  );
}
