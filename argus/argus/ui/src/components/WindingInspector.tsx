import { useEffect, useMemo, useRef, useState } from "react";
import { Chip } from "./Status";
import { hueForWinding } from "../lib/windingColor";
import { SurfaceCorrections } from "./SurfaceCorrections";
import { useSpatialState } from "../lib/spatialState";
import { TOKEN_FALLBACK } from "../theme/tokenFallbacks";

type Winding = {
  id: number;
  component: number;
  n_points: number;
  grid_bbox: { y0: number; y1: number; x0: number; x1: number };
  extent_vox: { z: number; y: number; x: number };
  centroid_vox: { z: number; y: number; x: number };
  median_step_vox: number;
  jump_edges_touching: number;
  support_fraction: number;
  flags: string[];
  points_zyx: [number, number, number][];
};

type Windings = {
  schema: string;
  mesh_dir: string;
  grid_shape: [number, number];
  stride: number;
  stride_note: string;
  median_step_vox: number;
  jump_ratio_limit: number;
  jump_rule_source: string;
  n_windings_total: number;
  n_windings_returned: number;
  windings: Winding[];
  orientation: {
    n_components: number;
    rejected_components: { component: number; pixels: number; why: string }[];
    discontinuous_edges: number;
    conflict_edges: number;
    field_sha256: string;
  };
  windings_sha256: string;
  correspondence: string;
  correspondence_note: string;
  note: string;
  does_not_trace: string;
};

const hueFor = hueForWinding;

const PLANES: { key: "xy" | "xz" | "yz"; label: string; a: 2 | 1; b: 1 | 0 }[] = [
  { key: "xy", label: "XY", a: 2, b: 1 },
  { key: "xz", label: "XZ", a: 2, b: 0 },
  { key: "yz", label: "YZ", a: 1, b: 0 },
];

const AXIS = ["z", "y", "x"] as const;

export const WINDINGS_STRIDE = 4;

function PlaneView({
  plane,
  windings,
  selected,
  crosshair,
  onCrosshair,
  onPick,
  show,
}: {
  plane: (typeof PLANES)[number];
  windings: Winding[];
  selected: number | null;
  crosshair: [number, number, number];
  onCrosshair: (c: [number, number, number]) => void;
  onPick: (id: number) => void;
  show: { traces: boolean; jumps: boolean; support: boolean; normals: boolean };
}) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  const W = 260;
  const H = 200;

  const bounds = useMemo<{ lo: [number, number]; hi: [number, number] }>(() => {
    let lo: [number, number] = [Infinity, Infinity];
    let hi: [number, number] = [-Infinity, -Infinity];
    for (const w of windings)
      for (const p of w.points_zyx) {
        const u = p[plane.a];
        const v = p[plane.b];
        lo = [Math.min(lo[0], u), Math.min(lo[1], v)];
        hi = [Math.max(hi[0], u), Math.max(hi[1], v)];
      }
    if (!isFinite(lo[0])) return { lo: [0, 0], hi: [1, 1] };
    return { lo, hi };
  }, [windings, plane]);

  const toPx = (u: number, v: number): [number, number] => {
    const sx = (u - bounds.lo[0]) / Math.max(1e-6, bounds.hi[0] - bounds.lo[0]);
    const sy = (v - bounds.lo[1]) / Math.max(1e-6, bounds.hi[1] - bounds.lo[1]);
    return [8 + sx * (W - 16), 8 + sy * (H - 16)];
  };

  useEffect(() => {
    const c = ref.current;
    if (!c) return;
    const g = c.getContext("2d");
    if (!g) return;
    const css = getComputedStyle(document.documentElement);
    const line = css.getPropertyValue("--line").trim() || "#333";
    const refusal = css.getPropertyValue("--status-refused").trim() || "#c33";
    const ink = css.getPropertyValue("--ink").trim() || "#eee";
    g.clearRect(0, 0, W, H);
    g.strokeStyle = line;
    g.strokeRect(0.5, 0.5, W - 1, H - 1);

    if (show.traces) {
      for (const w of windings) {
        const isSel = selected === w.id;
        g.globalAlpha = selected === null || isSel ? 1 : 0.28;
        g.fillStyle = `hsl(${hueFor(w.id)} 70% ${isSel ? 62 : 46}%)`;
        for (const p of w.points_zyx) {
          const px = toPx(p[plane.a], p[plane.b]);
          g.fillRect(px[0], px[1], isSel ? 1.6 : 1.1, isSel ? 1.6 : 1.1);
        }
      }
      g.globalAlpha = 1;
    }

    if (show.jumps) {
      g.strokeStyle = refusal;
      g.lineWidth = 1.5;
      for (const w of windings) {
        if (!w.jump_edges_touching) continue;
        const c2 = toPx(w.centroid_vox[AXIS[plane.a]], w.centroid_vox[AXIS[plane.b]]);
        g.beginPath();
        g.arc(c2[0], c2[1], 7, 0, Math.PI * 2);
        g.stroke();
      }
    }

    if (show.support) {
      const warn = css.getPropertyValue("--status-blocked").trim() || TOKEN_FALLBACK.statusBlocked;
      g.strokeStyle = warn;
      g.lineWidth = 1.5;
      for (const w of windings) {
        if (w.support_fraction >= 1) continue;
        const c2 = toPx(w.centroid_vox[AXIS[plane.a]], w.centroid_vox[AXIS[plane.b]]);
        const r = 4 + 8 * (1 - w.support_fraction);
        g.strokeRect(c2[0] - r, c2[1] - r, r * 2, r * 2);
      }
    }

    if (show.normals) {
      g.strokeStyle = refusal;
      g.lineWidth = 1.5;
      for (const w of windings) {
        if (!w.flags.includes("CONTRADICTORY_ORIENTATION")) continue;
        const c2 = toPx(w.centroid_vox[AXIS[plane.a]], w.centroid_vox[AXIS[plane.b]]);
        const r = 6;
        g.beginPath();
        g.moveTo(c2[0] - r, c2[1] - r);
        g.lineTo(c2[0] + r, c2[1] + r);
        g.moveTo(c2[0] + r, c2[1] - r);
        g.lineTo(c2[0] - r, c2[1] + r);
        g.stroke();
      }
    }

    const cross = toPx(crosshair[plane.a], crosshair[plane.b]);
    const cx = cross[0];
    const cy = cross[1];
    g.strokeStyle = ink;
    g.lineWidth = 1;
    g.beginPath();
    g.moveTo(cx, 4);
    g.lineTo(cx, H - 4);
    g.moveTo(4, cy);
    g.lineTo(W - 4, cy);
    g.stroke();
  }, [windings, selected, crosshair, plane, bounds, show]);

  const fromPx = (px: number, py: number): [number, number] => {
    const u = bounds.lo[0] + ((px - 8) / (W - 16)) * (bounds.hi[0] - bounds.lo[0]);
    const v = bounds.lo[1] + ((py - 8) / (H - 16)) * (bounds.hi[1] - bounds.lo[1]);
    return [u, v];
  };

  const handle = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const uv = fromPx(e.clientX - r.left, e.clientY - r.top);
    const u = uv[0];
    const v = uv[1];
    const next: [number, number, number] = [...crosshair];
    next[plane.a] = u;
    next[plane.b] = v;
    onCrosshair(next);
    let best: { id: number; d: number } | null = null;
    for (const w of windings)
      for (const p of w.points_zyx) {
        const d = (p[plane.a] - u) ** 2 + (p[plane.b] - v) ** 2;
        if (!best || d < best.d) best = { id: w.id, d };
      }
    if (best) onPick(best.id);
  };

  return (
    <div style={{ display: "grid", gap: 4 }}>
      <div style={{ color: "var(--ink-dim)", fontFamily: "var(--mono-font)" }}>
        {plane.label}
      </div>
      <canvas
        ref={ref}
        width={W}
        height={H}
        onClick={handle}
        data-testid={`plane-${plane.key}`}
        style={{ background: "var(--bg-sunken)", borderRadius: 4, cursor: "crosshair" }}
      />
    </div>
  );
}

export function WindingInspector({
  meshPath,
  pitchUm,
  volumeId,
  next,
  onOpenCt,
}: {
  meshPath?: string;
  pitchUm?: number | null;
  volumeId?: string | null;
  next?: React.ReactNode;
  onOpenCt?: () => void;
}) {
  const [d, setD] = useState<Windings | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const spatial = useSpatialState();
  const selected = spatial.selectedWindingId;
  const setSelected = spatial.setSelectedWinding;
  const crosshair = spatial.crosshair ?? [0, 0, 0];
  const setCrosshair = spatial.setCrosshair;
  const [show, setShow] = useState({
    ct: false,
    traces: true,
    normals: false,
    support: true,
    uncertainty: false,
    jumps: true,
  });
  const [neighbours, setNeighbours] = useState(2);

  useEffect(() => {
    spatial.setWindingsStride(WINDINGS_STRIDE);
  }, []);

  useEffect(() => {
    if (!meshPath) return;
    let live = true;
    fetch(`/api/windings?path=${encodeURIComponent(meshPath)}&stride=${WINDINGS_STRIDE}`)
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return (await r.json()) as Windings;
      })
      .then((j) => {
        if (!live) return;
        setD(j);
        setErr(null);
        const first = j.windings[0];
        if (first) {
          setSelected(first.id);
          const c = first.centroid_vox;
          setCrosshair([c.z, c.y, c.x]);
        }
      })
      .catch((e) => live && setErr(String(e)));
    return () => {
      live = false;
    };
  }, [meshPath]);

  const visible = useMemo(() => {
    if (!d) return [];
    if (selected === null) return d.windings.slice(0, 1 + neighbours);
    const sel = d.windings.find((w) => w.id === selected);
    if (!sel) return d.windings.slice(0, 1 + neighbours);
    const rest = d.windings
      .filter((w) => w.id !== selected)
      .map((w) => ({
        w,
        dist:
          (w.centroid_vox.z - sel.centroid_vox.z) ** 2 +
          (w.centroid_vox.y - sel.centroid_vox.y) ** 2 +
          (w.centroid_vox.x - sel.centroid_vox.x) ** 2,
      }))
      .sort((a, b) => a.dist - b.dist)
      .slice(0, neighbours)
      .map((r) => r.w);
    return [sel, ...rest];
  }, [d, selected, neighbours]);

  if (!meshPath)
    return (
      <section className="panel" style={{ padding: 16 }}>
        <h2 style={{ margin: 0 }}>Reconstruction</h2>
        <p style={{ color: "var(--ink-dim)" }}>
          No mesh selected. Choose a run with a tifxyz surface to inspect its windings.
        </p>
        {next ?? null}
      </section>
    );
  if (err)
    return (
      <section className="panel" style={{ padding: 16 }}>
        <h2 style={{ margin: 0 }}>Reconstruction</h2>
        <Chip tone="refused">geometry unavailable</Chip>
        <p style={{ color: "var(--ink-dim)" }}>{err}</p>
        {next ?? null}
      </section>
    );
  if (!d)
    return (
      <section className="panel" style={{ padding: 16 }}>
        reading mesh geometry…
      </section>
    );

  const sel = d.windings.find((w) => w.id === selected) ?? null;
  const anyRed = d.windings.some((w) => w.flags.length);

  return (
    <section
      className="panel"
      style={{ padding: 16, display: "grid", gap: 12 }}
      data-testid="winding-inspector"
    >
      <header style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <h2 style={{ margin: 0 }}>Reconstruction — Winding Inspector</h2>
        <Chip tone="blocked">{d.correspondence.replace(/_/g, " ")}</Chip>
        <Chip tone={anyRed ? "refused" : "active"}>
          {anyRed ? "geometry flags present" : "no geometry flags"}
        </Chip>
        <Chip tone="active">geometry, not ink</Chip>
      </header>

      <p
        style={{
          margin: 0,
          color: "var(--ink-dim)",
          borderLeft: "3px solid var(--status-blocked-edge)",
          paddingLeft: 10,
        }}
        data-testid="correspondence-banner"
      >
        {d.correspondence_note} {d.note}
      </p>

      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", alignItems: "center" }}>
        {(
          [
            ["ct", "CT", onOpenCt ? null : "raw CT compositing behind the trace is not implemented on this panel yet"],
            ["traces", "traces", null],
            ["normals", "normals", null],
            ["support", "support", null],
            [
              "uncertainty",
              "uncertainty",
              "argus.core.windings carries no per-winding uncertainty measure to show",
            ],
            ["jumps", "discontinuities", null],
          ] as const
        ).map(([k, label, unavailableWhy]) => (
          <label
            key={k}
            style={{
              display: "flex",
              gap: 5,
              alignItems: "center",
              opacity: unavailableWhy ? 0.5 : 1,
            }}
            title={unavailableWhy ?? undefined}
          >
            {k === "ct" && onOpenCt ? (
              <button
                type="button"
                className="ag-btn"
                data-testid="open-ct-canvas"
                onClick={onOpenCt}
              >
                Put CT on canvas
              </button>
            ) : (
              <input
                type="checkbox"
                checked={show[k]}
                disabled={!!unavailableWhy}
                data-testid={`toggle-${k}`}
                onChange={(e) => setShow((s) => ({ ...s, [k]: e.target.checked }))}
              />
            )}
            {label}
            {unavailableWhy ? " (not available)" : ""}
          </label>
        ))}
        <label style={{ marginLeft: "auto", display: "flex", gap: 6, alignItems: "center" }}>
          neighbours
          <input
            type="range"
            min={0}
            max={6}
            value={neighbours}
            data-testid="neighbour-count"
            onChange={(e) => setNeighbours(Number(e.target.value))}
          />
          <strong>{neighbours}</strong>
        </label>
      </div>

      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        {PLANES.map((p) => (
          <PlaneView
            key={p.key}
            plane={p}
            windings={visible}
            selected={selected}
            crosshair={crosshair}
            onCrosshair={setCrosshair}
            onPick={setSelected}
            show={show}
          />
        ))}
      </div>

      <div
        style={{ fontFamily: "var(--mono-font)", color: "var(--ink-dim)" }}
        data-testid="crosshair-readout"
      >
        crosshair — voxel z {crosshair[0].toFixed(1)} y {crosshair[1].toFixed(1)} x{" "}
        {crosshair[2].toFixed(1)}
        {pitchUm != null ? (
          <>
            {" "}
            · physical {(crosshair[0] * pitchUm).toFixed(1)}/{(crosshair[1] * pitchUm).toFixed(1)}/
            {(crosshair[2] * pitchUm).toFixed(1)} µm at {pitchUm} µm
          </>
        ) : (
          <span> · no physical reading: this run declared no voxel pitch</span>
        )}
      </div>

      <div style={{ overflowX: "auto" }}>
        <table
          style={{ borderCollapse: "collapse", fontFamily: "var(--mono-font)", minWidth: 620 }}
        >
          <thead>
            <tr style={{ color: "var(--ink-dim)", textAlign: "left" }}>
              <th style={{ padding: "4px 10px 4px 0" }}>id</th>
              <th style={{ padding: "4px 10px 4px 0" }}>points</th>
              <th style={{ padding: "4px 10px 4px 0" }}>jumps</th>
              <th style={{ padding: "4px 10px 4px 0" }}>support</th>
              <th style={{ padding: "4px 0" }}>flags</th>
            </tr>
          </thead>
          <tbody>
            {d.windings.map((w) => (
              <tr
                key={w.id}
                data-testid={`winding-row-${w.id}`}
                onClick={() => setSelected(w.id)}
                tabIndex={0}
                role="button"
                aria-pressed={w.id === selected}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    setSelected(w.id);
                  }
                }}
                style={{
                  borderTop: "1px solid var(--line-soft)",
                  cursor: "pointer",
                  background: w.id === selected ? "var(--bg-raised-2)" : undefined,
                }}
              >
                <td style={{ padding: "4px 10px 4px 0" }}>
                  <span
                    style={{
                      display: "inline-block",
                      width: 10,
                      height: 10,
                      marginRight: 6,
                      borderRadius: 2,
                      background: `hsl(${hueFor(w.id)} 70% 55%)`,
                    }}
                  />
                  {w.id}
                </td>
                <td style={{ padding: "4px 10px 4px 0" }}>{w.n_points}</td>
                <td
                  style={{
                    padding: "4px 10px 4px 0",
                    color: w.jump_edges_touching ? "var(--status-refused)" : undefined,
                  }}
                >
                  {w.jump_edges_touching}
                </td>
                <td style={{ padding: "4px 10px 4px 0" }}>{w.support_fraction.toFixed(3)}</td>
                <td style={{ padding: "4px 0" }}>
                  {w.flags.length ? (
                    w.flags.map((f) => (
                      <Chip key={f} tone="refused">
                        {f.replace(/_/g, " ").toLowerCase()}
                      </Chip>
                    ))
                  ) : (
                    <span style={{ color: "var(--ink-faint)" }}>—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {sel ? (
        <div style={{ color: "var(--ink-dim)", fontFamily: "var(--mono-font)" }}>
          selected {sel.id} · extent z {sel.extent_vox.z.toFixed(0)} y{" "}
          {sel.extent_vox.y.toFixed(0)} x {sel.extent_vox.x.toFixed(0)} vox · median step{" "}
          {sel.median_step_vox.toFixed(2)} vox · jump limit {d.jump_ratio_limit}×
        </div>
      ) : null}

      {meshPath ? (
        <SurfaceCorrections
          meshPath={meshPath}
          windings={d.windings}
          selectedWindingId={selected}
          crosshair={spatial.crosshair}
          pitchUm={pitchUm ?? null}
          volumeId={volumeId ?? null}
        />
      ) : null}

      <div style={{ color: "var(--ink-faint)", fontFamily: "var(--mono-font)" }}>
        grid {d.grid_shape[0]}×{d.grid_shape[1]} · stride {d.stride} · {d.stride_note} ·{" "}
        {d.n_windings_returned} of {d.n_windings_total} windings · orientation:{" "}
        {d.orientation.conflict_edges} conflict edges,{" "}
        {d.orientation.rejected_components.length} rejected component
        {d.orientation.rejected_components.length === 1 ? "" : "s"} · windings{" "}
        {d.windings_sha256.slice(0, 12)} · normals {d.orientation.field_sha256.slice(0, 12)}
      </div>

      <div style={{ color: "var(--ink-faint)" }}>{d.does_not_trace}</div>
    </section>
  );
}
