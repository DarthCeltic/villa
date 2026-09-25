import { useEffect, useState } from "react";
import { Chip } from "./Status";
import {
  promoteCorrection, readCorrections, recordCorrection, recordDecision, undoCorrection,
  type CoordinateTransform,
} from "../lib/governed";
import type { Voxel } from "../lib/spatialState";

const MOVE_ALONG_NORMAL_BOUND_VOXELS = 20;

type Winding = {
  id: number;
  component: number;
  centroid_vox: { z: number; y: number; x: number };
  extent_vox: { z: number; y: number; x: number };
};

export function SurfaceCorrections({
  meshPath,
  windings,
  selectedWindingId,
  crosshair,
  pitchUm,
  volumeId,
}: {
  meshPath: string;
  windings: Winding[];
  selectedWindingId: number | null;
  crosshair: Voxel | null;
  pitchUm: number | null;
  volumeId: string | null;
}) {
  const [corrections, setCorrections] = useState<Awaited<ReturnType<typeof readCorrections>> | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [mergeWith, setMergeWith] = useState<number | null>(null);
  const [offsetVoxels, setOffsetVoxels] = useState(0);
  const [centerline, setCenterline] = useState<{ plane: number; points: [number, number][] }>({
    plane: 0, points: [],
  });

  const proposalId = selectedWindingId !== null ? String(selectedWindingId) : null;
  const sel = windings.find((w) => w.id === selectedWindingId) ?? null;

  const refresh = () => {
    readCorrections(meshPath)
      .then(setCorrections)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  };
  useEffect(() => {
    if (!meshPath) return;
    refresh();
  }, [meshPath]);

  useEffect(() => {
    setCenterline({ plane: 0, points: [] });
  }, [proposalId]);

  const zBound =
    windings.length > 0
      ? Math.max(...windings.map((w) => w.centroid_vox.z + w.extent_vox.z)) + 1
      : 1;
  const transform: CoordinateTransform | null =
    pitchUm != null
      ? {
          volume_id: volumeId ?? meshPath, array_path: meshPath, crop_row0: 0, crop_col0: 0,
          depth_offset: 0, depth_planes: Math.max(1, Math.ceil(zBound)), voxel_um: pitchUm,
        }
      : null;

  const proposalSha256ForWinding = (id: number) =>
    id.toString(16).padStart(64, "0");

  const run = async (fn: () => Promise<unknown>): Promise<boolean> => {
    setBusy(true);
    setErr(null);
    try {
      await fn();
      refresh();
      return true;
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const doPointAction = (action: "CLICK_CORRECT_SHEET" | "MARK_SHEET_SWITCH") => {
    if (!proposalId || !crosshair || !transform) return;
    const [z, y, x] = crosshair;
    run(() =>
      recordCorrection(meshPath, proposalId, proposalSha256ForWinding(selectedWindingId!),
        action, { view_row: y, view_col: x, plane: z }, transform),
    );
  };

  const addCenterlinePoint = () => {
    if (!crosshair) return;
    const [z, y, x] = crosshair;
    setCenterline((cur) => {
      if (cur.points.length > 0 && cur.plane !== z) {
        setErr(
          `the crosshair is on plane ${z}, but this centerline's first point pinned plane ` +
            `${cur.plane}. A centerline is one path on one plane -- move the crosshair back, ` +
            `or Cancel and start a new one here.`,
        );
        return cur;
      }
      return { plane: z, points: [...cur.points, [y, x]] };
    });
  };

  const commitCenterline = async () => {
    if (!proposalId || !transform || centerline.points.length < 2) return;
    const ok = await run(() =>
      recordCorrection(meshPath, proposalId, proposalSha256ForWinding(selectedWindingId!),
        "DRAW_CENTERLINE", { points: centerline.points, plane: centerline.plane }, transform),
    );
    if (ok) setCenterline({ plane: 0, points: [] });
  };

  const commitMoveAlongNormal = async () => {
    if (!proposalId || !crosshair || !transform || offsetVoxels === 0) return;
    const [z, y, x] = crosshair;
    const ok = await run(() =>
      recordCorrection(meshPath, proposalId, proposalSha256ForWinding(selectedWindingId!),
        "MOVE_ALONG_NORMAL", { view_row: y, view_col: x, plane: z, offset_voxels: offsetVoxels },
        transform),
    );
    if (ok) setOffsetVoxels(0);
  };

  const describeConstraint = (c: Record<string, unknown>): string => {
    switch (c.constraint_kind) {
      case "SURFACE_PASSES_THROUGH":
      case "SURFACE_DISCONTINUOUS_AT": {
        const v = c.voxel as [number, number, number] | undefined;
        return v ? `voxel z${v[0].toFixed(1)} y${v[1].toFixed(1)} x${v[2].toFixed(1)}` : "";
      }
      case "SURFACE_OFFSET_ALONG_NORMAL":
        return `${(c.offset_voxels as number).toFixed(2)} vox (${(c.offset_um as number).toFixed(1)} um) along the local normal`;
      case "SURFACE_FOLLOWS_POLYLINE": {
        const poly = (c.polyline as [number, number, number][]) ?? [];
        return `${poly.length}-point path`;
      }
      case "COMPONENT_IS_NOT_ONE_SHEET":
        return `component ${c.component_id}`;
      case "COMPONENTS_ARE_ONE_SHEET":
        return `components ${(c.component_ids as string[])?.join(" + ")}`;
      case "NO_USABLE_PROPOSAL_HERE":
        return "no usable proposal";
      default:
        return "";
    }
  };

  if (!meshPath) return null;

  const row = proposalId ? corrections?.proposals?.[proposalId] : null;

  return (
    <section className="panel" style={{ padding: 16, display: "grid", gap: 10 }} data-testid="surface-corrections">
      <header style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <h3 style={{ margin: 0 }}>Surface corrections</h3>
        {row ? <Chip tone="active">{row.state.replace(/_/g, " ").toLowerCase()}</Chip> : null}
        {row?.decision ? (
          <Chip tone={row.decision.value === "ACCEPTED" ? "certified" : "refused"}>
            {row.decision.value.toLowerCase()}
          </Chip>
        ) : null}
      </header>

      {!sel ? (
        <p className="small faint">Select a winding in the table below to correct it.</p>
      ) : (
        <>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button
              type="button"
              className="ag-btn"
              disabled={busy || !crosshair || !transform}
              title={
                !transform
                  ? "this run declared no voxel pitch, so a point correction cannot be pinned to a real coordinate"
                  : "confirm the sheet passes through the current crosshair"
              }
              data-control="correction.click_correct_sheet"
              onClick={() => doPointAction("CLICK_CORRECT_SHEET")}
            >
              Confirm sheet at crosshair
            </button>
            <button
              type="button"
              className="ag-btn"
              disabled={busy || !crosshair || !transform}
              title={
                !transform
                  ? "this run declared no voxel pitch, so a point correction cannot be pinned to a real coordinate"
                  : "mark that the sheet switches wraps at the current crosshair"
              }
              data-control="correction.mark_sheet_switch"
              onClick={() => doPointAction("MARK_SHEET_SWITCH")}
            >
              Mark wrap switch at crosshair
            </button>
            <button
              type="button"
              className="ag-btn"
              disabled={busy}
              data-control="correction.mark_neither_usable"
              onClick={() =>
                run(() =>
                  recordCorrection(meshPath, proposalId!, proposalSha256ForWinding(sel.id),
                    "MARK_NEITHER_USABLE", {}, transform ?? {
                      volume_id: volumeId ?? meshPath, array_path: meshPath, crop_row0: 0,
                      crop_col0: 0, depth_offset: 0, depth_planes: 1, voxel_um: 1,
                    }),
                )
              }
            >
              Mark not usable
            </button>
            <button
              type="button"
              className="ag-btn"
              disabled={busy}
              data-control="correction.split_component"
              onClick={() =>
                run(() =>
                  recordCorrection(meshPath, proposalId!, proposalSha256ForWinding(sel.id),
                    "SPLIT_COMPONENT", { component_id: String(sel.component) },
                    transform ?? {
                      volume_id: volumeId ?? meshPath, array_path: meshPath, crop_row0: 0,
                      crop_col0: 0, depth_offset: 0, depth_planes: 1, voxel_um: 1,
                    }),
                )
              }
            >
              This is two sheets
            </button>
          </div>

          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <span className="small">
              move surface along normal, at crosshair
              {crosshair ? ` (z${crosshair[0].toFixed(1)} y${crosshair[1].toFixed(1)} x${crosshair[2].toFixed(1)})` : ""}
            </span>
            <input
              type="number"
              className="small"
              style={{ width: 72 }}
              data-control="correction.move_along_normal.offset"
              min={-MOVE_ALONG_NORMAL_BOUND_VOXELS}
              max={MOVE_ALONG_NORMAL_BOUND_VOXELS}
              step={0.5}
              value={offsetVoxels}
              disabled={busy || !crosshair || !transform}
              onChange={(e) => setOffsetVoxels(Number(e.target.value))}
            />
            <span className="small faint">
              vox (bounded +-{MOVE_ALONG_NORMAL_BOUND_VOXELS})
              {transform ? ` -- before 0.0 vox / 0.0 um, after ${offsetVoxels.toFixed(1)} vox / ${(offsetVoxels * transform.voxel_um).toFixed(1)} um` : ""}
            </span>
            <button
              type="button"
              className="ag-btn"
              disabled={busy || !crosshair || !transform || offsetVoxels === 0}
              title={
                !transform
                  ? "this run declared no voxel pitch, so a point correction cannot be pinned to a real coordinate"
                  : "assert the surface sits this far off, along its own local normal, at the crosshair"
              }
              data-control="correction.move_along_normal.commit"
              onClick={commitMoveAlongNormal}
            >
              Apply normal offset
            </button>
          </div>

          <div style={{ display: "grid", gap: 6 }}>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <span className="small">
                centerline
                {crosshair ? ` -- crosshair at z${crosshair[0].toFixed(1)} y${crosshair[1].toFixed(1)} x${crosshair[2].toFixed(1)}` : ""}
              </span>
              <button
                type="button"
                className="ag-btn"
                disabled={busy || !crosshair || !transform}
                title="append the current crosshair to the working, uncommitted path"
                data-control="correction.draw_centerline.add_point"
                onClick={addCenterlinePoint}
              >
                Add point at crosshair
              </button>
              <button
                type="button"
                className="ag-btn"
                disabled={busy || centerline.points.length === 0}
                title="discard the working path; nothing is sent to the server"
                data-control="correction.draw_centerline.cancel"
                onClick={() => setCenterline({ plane: 0, points: [] })}
              >
                Cancel
              </button>
              <button
                type="button"
                className="ag-btn"
                disabled={busy || !transform || centerline.points.length < 2}
                title="commit this path as the sheet's centerline; append-only, cannot be edited after"
                data-control="correction.draw_centerline.commit"
                onClick={commitCenterline}
              >
                Commit centerline ({centerline.points.length} pt{centerline.points.length === 1 ? "" : "s"})
              </button>
            </div>
            {centerline.points.length > 0 ? (
              <span className="small faint" data-testid="centerline-preview">
                before: no path committed -- after (uncommitted): plane {centerline.plane}, {" "}
                {centerline.points.map(([r, c], i) => `(${r.toFixed(1)},${c.toFixed(1)})${i < centerline.points.length - 1 ? " -> " : ""}`)}
              </span>
            ) : null}
          </div>

          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <span className="small">merge with winding</span>
            <select
              value={mergeWith ?? ""}
              data-control="correction.merge.pick"
              onChange={(e) => setMergeWith(e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">choose…</option>
              {windings.filter((w) => w.id !== sel.id).map((w) => (
                <option key={w.id} value={w.id}>
                  winding {w.id}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="ag-btn"
              disabled={busy || mergeWith === null}
              data-control="correction.merge_components"
              onClick={() =>
                run(() =>
                  recordCorrection(meshPath, proposalId!, proposalSha256ForWinding(sel.id),
                    "MERGE_COMPONENTS",
                    { component_ids: [String(sel.component), String(windings.find((w) => w.id === mergeWith)?.component ?? mergeWith)] },
                    transform ?? {
                      volume_id: volumeId ?? meshPath, array_path: meshPath, crop_row0: 0,
                      crop_col0: 0, depth_offset: 0, depth_planes: 1, voxel_um: 1,
                    }),
                )
              }
            >
              These are one sheet
            </button>
          </div>

          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button type="button" className="ag-btn" disabled={busy}
              data-control="correction.accept"
              onClick={() => run(() => recordDecision(meshPath, proposalId!, "ACCEPTED"))}>
              Accept proposal
            </button>
            <button type="button" className="ag-btn" disabled={busy}
              data-control="correction.reject"
              onClick={() => run(() => recordDecision(meshPath, proposalId!, "REJECTED"))}>
              Reject proposal
            </button>
            {row && row.state !== "FROZEN_SUPERVISION" ? (
              <button
                type="button"
                className="ag-btn"
                disabled={busy}
                title="advances one rung on the promotion ladder if the evidence on hand legally allows it"
                data-control="correction.promote"
                onClick={() =>
                  run(() => {
                    const next = {
                      PROPOSED: "PAIRWISE_PREFERRED", PAIRWISE_PREFERRED: "HUMAN_CORRECTED",
                      HUMAN_CORRECTED: "INDEPENDENTLY_VALIDATED",
                      INDEPENDENTLY_VALIDATED: "FROZEN_SUPERVISION",
                    }[row.state];
                    if (!next) throw new Error("already at the last promotion state");
                    return promoteCorrection(meshPath, proposalId!, next);
                  })
                }
              >
                Advance promotion state
              </button>
            ) : null}
          </div>

          {err ? <p style={{ color: "var(--status-refused)" }}>{err}</p> : null}

          {row && row.constraints.length > 0 ? (
            <div style={{ display: "grid", gap: 4 }}>
              <span className="small faint">history</span>
              <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "grid", gap: 3 }}>
                {row.constraints.map((c) => (
                  <li
                    key={c.event_id as string}
                    style={{
                      display: "flex", gap: 8, alignItems: "center",
                      opacity: c.undone ? 0.5 : 1, fontSize: "var(--t-small)",
                    }}
                  >
                    <span className="mono">{(c.constraint_kind as string)?.toLowerCase()}</span>
                    <span className="small faint">{describeConstraint(c)}</span>
                    {c.stale ? <Chip tone="blocked">stale</Chip> : null}
                    {c.undone ? <span className="small faint">(undone)</span> : (
                      <button
                        type="button"
                        className="small"
                        disabled={busy}
                        data-control={`correction.undo.${c.event_id}`}
                        onClick={() => run(() => undoCorrection(meshPath, c.event_id))}
                      >
                        undo
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}
