import { useEffect, type MutableRefObject } from "react";
import type vtkProp from "@kitware/vtk.js/Rendering/Core/Prop";
import vtkActor from "@kitware/vtk.js/Rendering/Core/Actor";
import vtkMapper from "@kitware/vtk.js/Rendering/Core/Mapper";
import vtkPolyData from "@kitware/vtk.js/Common/DataModel/PolyData";
import vtkPoints from "@kitware/vtk.js/Common/Core/Points";
import vtkCellArray from "@kitware/vtk.js/Common/Core/CellArray";
import { checkOverlay, type OverlayLayer } from "../lib/overlayIdentity";
import type { BrickMeta, Vec3 } from "../lib/volumeBrick";

export type { OverlayLayer } from "../lib/overlayIdentity";

interface PipeLike {
  addActor: (a: vtkProp) => void;
  removeActor: (a: vtkProp) => void;
  render: () => void;
}

function meshActor(layer: OverlayLayer, brick: BrickMeta): { actor: ReturnType<typeof vtkActor.newInstance>; dispose: () => void } | null {
  if (!layer.mesh) return null;
  const sp = brick.spacing_um_zyx ?? [1, 1, 1];
  const o = brick.origin_voxel_zyx;
  const pts = vtkPoints.newInstance();
  const flat: number[] = [];
  for (let i = 0; i < layer.mesh.vertices.length; i += 3) {
    const z = layer.mesh.vertices[i]!;
    const y = layer.mesh.vertices[i + 1]!;
    const x = layer.mesh.vertices[i + 2]!;
    flat.push((x - o[2]) * sp[2], (y - o[1]) * sp[1], (z - o[0]) * sp[0]);
  }
  pts.setData(Float32Array.from(flat), 3);
  const cells = vtkCellArray.newInstance();
  const f = layer.mesh.faces;
  const packed: number[] = [];
  for (let i = 0; i + 2 < f.length; i += 3) packed.push(3, f[i]!, f[i + 1]!, f[i + 2]!);
  cells.setData(Uint32Array.from(packed));
  const poly = vtkPolyData.newInstance();
  poly.setPoints(pts);
  poly.setPolys(cells);
  const mapper = vtkMapper.newInstance();
  mapper.setInputData(poly);
  const actor = vtkActor.newInstance();
  actor.setMapper(mapper);
  actor.getProperty().setColor(0.95, 0.75, 0.2);
  actor.getProperty().setOpacity(0.55);
  return { actor, dispose: () => { actor.delete(); mapper.delete(); poly.delete(); cells.delete(); pts.delete(); } };
}

export interface NotDrawnRow {
  id: string;
  label: string;
  kind: string;
  status: string;
  why: string;
}

export function VolumeOverlays({ layers, notDrawn = [], brick, scroll, volume, pipeline }: { layers: OverlayLayer[]; notDrawn?: NotDrawnRow[]; brick: BrickMeta | null; scroll: string; volume: string | null; pipeline: MutableRefObject<PipeLike | null> }) {
  const verdicts = layers.map((l) => ({ layer: l, verdict: brick ? checkOverlay(l, brick, scroll, volume) : null }));

  useEffect(() => {
    const p = pipeline.current;
    if (!p || !brick) return;
    const made = verdicts
      .filter((v) => v.verdict?.accepted && v.layer.kind === "mesh")
      .map((v) => meshActor(v.layer, brick))
      .filter((m): m is NonNullable<ReturnType<typeof meshActor>> => m !== null);
    made.forEach((m) => p.addActor(m.actor));
    p.render();
    return () => {
      made.forEach((m) => {
        p.removeActor(m.actor);
        m.dispose();
      });
    };
  }, [brick, layers, scroll, volume]);

  if (!layers.length && !notDrawn.length) return null;
  return (
    <section aria-label="Registered layers" data-control="wb.volume3d.overlays" style={{ display: "grid", gap: 4 }}>
      <div style={{ fontWeight: 600 }}>Registered layers</div>
      {verdicts.map(({ layer, verdict }) => (
        <div key={layer.id} className="meta" data-control={`wb.volume3d.overlay.${layer.id}`} data-accepted={String(Boolean(verdict?.accepted))}>
          <b>{layer.label}</b> ({layer.kind}) · producer {layer.producer} · source volume {layer.sourceVolume ?? "not declared"} · pitch {layer.pitchUm ?? "not declared"} µm ·
          transform {layer.transform.kind} · sha256 {layer.sha256 ? layer.sha256.slice(0, 12) + "…" : "none"} · status <b>{layer.status}</b> ·{" "}
          {!brick ? "waiting for a loaded brick" : verdict?.accepted ? "drawn in this frame" : `REFUSED: ${verdict?.reasons.join("; ")}`}
        </div>
      ))}
      {notDrawn.filter((n) => n.kind !== "raw_ct").map((n) => (
        <div key={n.id} className="meta" data-control={`wb.volume3d.overlay.${n.id}`} data-accepted="false">
          <b>{n.label}</b> ({n.kind}) · status <b>{n.status}</b> · not drawn: {n.why}
        </div>
      ))}
    </section>
  );
}

export type { Vec3 };
