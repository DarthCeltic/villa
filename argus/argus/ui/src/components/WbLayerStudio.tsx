import { useState } from "react";
import "../theme/workbench.css";
import { NextStep } from "./workbench/Clamshell";
import { Chip } from "./Status";

export type StudioGroup =
  | "Base"
  | "Geometry"
  | "Ink"
  | "Validation"
  | "Annotations"
  | "Other declared layers";

export const STUDIO_GROUPS: StudioGroup[] = [
  "Base",
  "Geometry",
  "Ink",
  "Validation",
  "Annotations",
  "Other declared layers",
];

export const GROUP_MEANING: Record<StudioGroup, string> = {
  Base: "the picture of the papyrus itself. Pick one — the others are the same place rendered differently.",
  Geometry: "where the traced sheet is and which way it faces.",
  Ink: "what a detector says about ink. A detector output is not a reading.",
  Validation: "where there is papyrus at all, and which parts carry a complete evidence chain.",
  Annotations: "what people have done to this surface: marks made, and places queued for judgement.",
  "Other declared layers":
    "declared by the layer contract and not covered by the five headings above. Shown rather than dropped.",
};

export interface StudioRamp {
  name: string;
  rgb: [number, number, number];
  red_green_risk: boolean;
}

export interface StudioLayer {
  layer: string;
  group: StudioGroup;
  contractGroup: string;
  exclusive: boolean;
  visible: boolean;
  ramp: string;
  opacity: number;
  gamma: number;
  threshold: number | null;
  blend: string;
  invert: boolean;
  meaning: string;
  available: boolean;
  vector: boolean;
  reason: string;
  evidence: { path: string | null; href: string | null };
  comparability: { valueMeans: string; comparable: boolean; whyNotComparable?: string } | null;
}

export interface SavedLayout {
  name: string;
  saved: string;
}

export function WbLayerStudio({
  layers,
  order,
  ramps,
  blendModes,
  presets,
  saved,
  onPatch,
  onMove,
  onReorder,
  onPreset,
  onSave,
  onApplySaved,
  onDeleteSaved,
  separability,
  onSafePreset,
  clip,
  onClip,
}: {
  layers: StudioLayer[];
  order: string[];
  ramps: StudioRamp[];
  blendModes: string[];
  presets: { name: string; what: string }[];
  saved: SavedLayout[];
  onPatch: (layer: string, patch: Partial<StudioLayer>) => void;
  onMove: (layer: string, delta: -1 | 1) => void;
  onReorder: (layer: string, before: string) => void;
  onPreset: (name: string) => void;
  onSave: (name: string) => void;
  onApplySaved: (name: string) => void;
  onDeleteSaved: (name: string) => void;
  separability: { problems: string[]; warns: string[]; count: number };
  onSafePreset: () => void;
  clip: boolean;
  onClip: (v: boolean) => void;
}) {
  const [drag, setDrag] = useState<string | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const [name, setName] = useState("");

  const byLayer = new Map(layers.map((l) => [l.layer, l]));
  const sorted = order
    .map((k) => byLayer.get(k))
    .filter((l): l is StudioLayer => !!l);
  for (const l of layers) if (!order.includes(l.layer)) sorted.push(l);

  return (
    <div className="ag-studio" data-wb="layer-studio">
      <header className="ag-studio-head">
        <h3 className="ag-studio-title">Layer studio</h3>
        <p className="ag-studio-sub">
          {separability.count} layer{separability.count === 1 ? "" : "s"} visible. Ramp,
          opacity, gamma, cut, invert and blend change how a layer LOOKS and never what it is;
          a threshold chosen by eye is a display choice and is never a scientific criterion.
        </p>
        <p className="ag-studio-sub">
          Order is top-of-list drawn first, so the LAST row in each group sits on top. The five
          headings are a presentation of the contract&rsquo;s own groups — each row names the
          group it came from, and exclusivity is the contract&rsquo;s, not the heading&rsquo;s.
        </p>
      </header>

      {separability.problems.length ? (
        <div className="ag-collection-note" role="note">
          <b>These layers cannot be told apart on screen.</b>
          <ul className="ag-list" style={{ marginTop: 6 }}>
            {separability.problems.map((p) => (
              <li key={p} className="ag-item-sub">
                {p}
              </li>
            ))}
          </ul>
        </div>
      ) : separability.warns.length ? (
        <div className="ag-collection-note" role="note">
          {separability.warns.length} visible pair
          {separability.warns.length === 1 ? " is" : "s are"} hard to separate for a red-green
          colour-blind viewer ({separability.warns.join("; ")}).{" "}
          <button
            type="button"
            className="ag-btn"
            data-control="wb.studio.safePreset"
            onClick={onSafePreset}
          >
            Use the colour-blind-safe preset
          </button>
        </div>
      ) : null}

      <div className="ag-viewbar-group">
        <button
          type="button"
          className="ag-btn"
          aria-pressed={clip}
          data-control="wb.studio.clip"
          onClick={() => onClip(!clip)}
        >
          {clip ? "Clipping to papyrus" : "Not clipping"}
        </button>
        <span className="ag-viewbar-label">
          Off the papyrus there is no reading. With clipping on, anything outside the validity
          mask is drawn as absent rather than as a low value.
        </span>
      </div>

      {STUDIO_GROUPS.map((g) => {
        const rows = sorted.filter((l) => l.group === g);
        if (!rows.length) return null;
        const present = rows.filter((l) => l.available);
        const absent = rows.filter((l) => !l.available);
        const exclusive = rows.some((l) => l.exclusive);
        return (
          <section className="ag-studio-group" key={g} aria-label={`${g} layers`}>
            <header className="ag-studio-group-head">
              <span className="ag-studio-group-name">{g}</span>
              <span className="ag-collection-count">{rows.length}</span>
              {exclusive ? (
                <span className="tag" title="the layer contract declares this group exclusive">
                  PICK ONE
                </span>
              ) : null}
            </header>
            <p className="ag-studio-group-what">{GROUP_MEANING[g]}</p>

            {present.map((l) => (
              <LayerRow
                key={l.layer}
                l={l}
                ramps={ramps}
                blendModes={blendModes}
                onPatch={onPatch}
                onMove={onMove}
                dragging={drag === l.layer}
                dropzone={over === l.layer}
                onDragStart={() => setDrag(l.layer)}
                onDragEnd={() => {
                  setDrag(null);
                  setOver(null);
                }}
                onDragOver={() => setOver(l.layer)}
                onDrop={() => {
                  if (drag && drag !== l.layer) onReorder(drag, l.layer);
                  setDrag(null);
                  setOver(null);
                }}
              />
            ))}

            {absent.length ? (
              <div className="ag-unavail">
                {absent.map((l) => (
                  <div
                    className="ag-unavail-row"
                    key={l.layer}
                    data-control={`wb.layer.${l.layer}.unavailable`}
                  >
                    <span>
                      <span className="ag-unavail-name">{human(l.layer)}</span> — unavailable
                    </span>
                    <span className="ag-unavail-why">{l.reason}</span>
                    <NextStep
                      id={`layer.${l.layer}`}
                      to="/sources?tab=holdings"
                      label="See available material on Sources"
                    />
                  </div>
                ))}
              </div>
            ) : null}
          </section>
        );
      })}

      <section className="ag-studio-group" aria-label="Presets and layouts">
        <header className="ag-studio-group-head">
          <span className="ag-studio-group-name">Presets</span>
        </header>
        <p className="ag-studio-group-what">
          A preset is a whole layer stack the contract ships, with its own reason for existing.
        </p>
        <div className="ag-viewbar-group">
          {presets.map((p) => (
            <button
              key={p.name}
              type="button"
              className="ag-btn"
              title={p.what}
              data-control={`wb.preset.${p.name}`}
              onClick={() => onPreset(p.name)}
            >
              {p.name.replace(/_/g, " ")}
            </button>
          ))}
        </div>
        {presets.map((p) => (
          <p className="ag-studio-group-what" key={`${p.name}-what`}>
            <b>{p.name.replace(/_/g, " ")}</b> — {p.what}
          </p>
        ))}

        <header className="ag-studio-group-head">
          <span className="ag-studio-group-name">Saved layouts</span>
        </header>
        <p className="ag-studio-group-what">
          Saved in this browser only. A layout is a display choice, so it is never written to
          the record and never travels with a receipt.
        </p>
        <div className="ag-viewbar-group">
          <input
            className="ag-search"
            placeholder="Name this layout"
            aria-label="Name this layout"
            data-control="wb.layout.name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <button
            type="button"
            className="ag-btn"
            data-control="wb.layout.save"
            disabled={!name.trim()}
            onClick={() => {
              onSave(name.trim());
              setName("");
            }}
          >
            Save layout
          </button>
        </div>
        {saved.length ? (
          <ul className="ag-list">
            {saved.map((s) => (
              <li key={s.name} className="ag-item">
                <span className="ag-item-title">{s.name}</span>
                <span className="ag-item-id">saved {s.saved}</span>
                <span className="ag-viewbar-group">
                  <button
                    type="button"
                    className="ag-btn"
                    data-control={`wb.layout.apply.${s.name}`}
                    onClick={() => onApplySaved(s.name)}
                  >
                    Apply
                  </button>
                  <button
                    type="button"
                    className="ag-btn"
                    data-control={`wb.layout.delete.${s.name}`}
                    onClick={() => onDeleteSaved(s.name)}
                  >
                    Delete
                  </button>
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="ag-studio-group-what">No layout has been saved in this browser.</p>
        )}
      </section>
    </div>
  );
}


function LayerRow({
  l,
  ramps,
  blendModes,
  onPatch,
  onMove,
  dragging,
  dropzone,
  onDragStart,
  onDragEnd,
  onDragOver,
  onDrop,
}: {
  l: StudioLayer;
  ramps: StudioRamp[];
  blendModes: string[];
  onPatch: (layer: string, patch: Partial<StudioLayer>) => void;
  onMove: (layer: string, delta: -1 | 1) => void;
  dragging: boolean;
  dropzone: boolean;
  onDragStart: () => void;
  onDragEnd: () => void;
  onDragOver: () => void;
  onDrop: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ramp = ramps.find((r) => r.name === l.ramp) ?? null;
  return (
    <div
      className="ag-lrow"
      data-visible={l.visible ? "true" : "false"}
      data-dragging={dragging ? "true" : "false"}
      data-dropzone={dropzone ? "true" : "false"}
      data-layer={l.layer}
      draggable
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onDragOver={(e) => {
        e.preventDefault();
        onDragOver();
      }}
      onDrop={(e) => {
        e.preventDefault();
        onDrop();
      }}
    >
      <div className="ag-lrow-top">
        <button
          type="button"
          className="ag-eye"
          aria-pressed={l.visible}
          aria-label={`${l.visible ? "Hide" : "Show"} ${human(l.layer)}`}
          data-control={`wb.layer.${l.layer}.toggle`}
          onClick={() => onPatch(l.layer, { visible: !l.visible })}
        >
          {l.visible ? "visible" : "hidden"}
        </button>
        <span
          className="ag-swatch"
          style={{ background: ramp ? css(ramp.rgb) : "var(--line-strong)" }}
          aria-hidden="true"
        />
        <span className="ag-lrow-text">
          <span className="ag-lrow-name">{human(l.layer)}</span>
          <p className="ag-lrow-explain">{l.meaning}</p>
          {l.comparability ? (
            <p className="ag-lrow-explain" style={{ display: "flex", gap: 6, alignItems: "baseline", flexWrap: "wrap" }}>
              <Chip tone={l.comparability.comparable ? "certified" : "blocked"} size="sm">
                {l.comparability.comparable ? "comparable" : "not comparable"}
              </Chip>
              <span className="small faint">
                {l.comparability.valueMeans}
                {l.comparability.whyNotComparable ? ` — ${l.comparability.whyNotComparable}` : ""}
              </span>
            </p>
          ) : null}
          <span className="ag-source">
            contract group {l.contractGroup}
            {l.vector ? " · drawn as points, not a raster" : ""}
            {l.reason ? ` · ${l.reason}` : ""}
          </span>
        </span>
        <span className="ag-reorder">
          <button
            type="button"
            aria-label={`Move ${human(l.layer)} down the stack`}
            data-control={`wb.layer.${l.layer}.down`}
            onClick={() => onMove(l.layer, -1)}
          >
            ↓
          </button>
          <button
            type="button"
            aria-label={`Move ${human(l.layer)} up the stack`}
            data-control={`wb.layer.${l.layer}.up`}
            onClick={() => onMove(l.layer, 1)}
          >
            ↑
          </button>
        </span>
      </div>

      <div className="ag-lrow-mid">
        <label className="ag-slider" style={{ flex: "1 1 240px" }}>
          <span className="ag-slider-name">opacity</span>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={l.opacity}
            data-control={`wb.layer.${l.layer}.opacity`}
            onChange={(e) => onPatch(l.layer, { opacity: Number(e.target.value) })}
          />
          <span className="ag-slider-value">{l.opacity.toFixed(2)}</span>
        </label>
        {
}
        {l.group === "Base" ? null : (
          <label className="ag-filter-label">
            blend
            <select
              className="ag-filter-select"
              value={l.blend}
              data-control={`wb.layer.${l.layer}.blend`}
              onChange={(e) => onPatch(l.layer, { blend: e.target.value })}
            >
              {blendModes.map((b) => (
                <option key={b} value={b}>
                  {b}
                </option>
              ))}
            </select>
          </label>
        )}
        {l.evidence.href ? (
          <a
            className="ag-evidence"
            href={l.evidence.href}
            target="_blank"
            rel="noreferrer"
            title={l.evidence.path ?? undefined}
            data-control={`wb.layer.${l.layer}.evidence`}
          >
            Evidence: the bytes this layer draws
          </a>
        ) : (
          <span className="ag-viewbar-label">
            no file backs this layer, so there is nothing to link
          </span>
        )}
        <button
          type="button"
          className="ag-btn"
          aria-expanded={open}
          data-control={`wb.layer.${l.layer}.more`}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? "Fewer controls" : "More display controls"}
        </button>
      </div>

      {open ? (
        <div className="ag-lrow-controls">
          <span className="ag-source">
            DISPLAY ONLY — ramp, gamma, cut and invert change how this layer looks, never what
            it is.
          </span>
          <div className="ag-swatches">
            {ramps.map((r) => (
              <button
                key={r.name}
                type="button"
                className="ag-swatch-btn"
                aria-pressed={r.name === l.ramp}
                aria-label={`Colour ${r.name}${r.red_green_risk ? " (red-green risk)" : ""}`}
                title={`${r.name}${r.red_green_risk ? " — red-green risk" : ""}`}
                data-control={`wb.layer.${l.layer}.ramp.${r.name}`}
                style={{ background: css(r.rgb) }}
                onClick={() => onPatch(l.layer, { ramp: r.name })}
              />
            ))}
          </div>
          <label className="ag-slider">
            <span className="ag-slider-name">gamma</span>
            <input
              type="range"
              min={0.2}
              max={3}
              step={0.05}
              value={l.gamma}
              data-control={`wb.layer.${l.layer}.gamma`}
              onChange={(e) => onPatch(l.layer, { gamma: Number(e.target.value) })}
            />
            <span className="ag-slider-value">{l.gamma.toFixed(2)}</span>
          </label>
          {l.threshold !== null ? (
            <label className="ag-slider">
              <span className="ag-slider-name">cut</span>
              <input
                type="range"
                min={0}
                max={1}
                step={0.01}
                value={l.threshold}
                data-control={`wb.layer.${l.layer}.cut`}
                onChange={(e) => onPatch(l.layer, { threshold: Number(e.target.value) })}
              />
              <span className="ag-slider-value">{l.threshold.toFixed(2)}</span>
            </label>
          ) : null}
          <div className="ag-viewbar-group">
            <button
              type="button"
              className="ag-btn"
              aria-pressed={l.invert}
              data-control={`wb.layer.${l.layer}.invert`}
              onClick={() => onPatch(l.layer, { invert: !l.invert })}
            >
              {l.invert ? "Inverted" : "Not inverted"}
            </button>
            <button
              type="button"
              className="ag-btn"
              aria-pressed={l.threshold !== null}
              data-control={`wb.layer.${l.layer}.cut.toggle`}
              onClick={() => onPatch(l.layer, { threshold: l.threshold === null ? 0.5 : null })}
            >
              {l.threshold === null ? "No cut applied" : "Cut applied"}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function human(layer: string): string {
  return layer.replace(/_/g, " ");
}

export function css(rgb: [number, number, number]): string {
  return `rgb(${Math.round(rgb[0] * 255)},${Math.round(rgb[1] * 255)},${Math.round(rgb[2] * 255)})`;
}
