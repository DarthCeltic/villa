import { useState } from "react";
import { isRefusal, planGoverned, runGoverned, sessionIsOpen, openSession } from "../lib/governed";

type LaunchMode = "interactive" | "headless_roundtrip" | "headless_test_sculpt" | "headless_test_drift";

const MODES: { value: LaunchMode; label: string; hint: string }[] = [
  { value: "interactive", label: "Interactive (a human edits)", hint: "opens the real Blender GUI; nothing is verified until you export and come back" },
  { value: "headless_roundtrip", label: "Headless conservative cleanup", hint: "existing weld/normals repair only (argus.core.blender_adapter); no sculpting" },
  { value: "headless_test_sculpt", label: "Headless test: sculpt (dev/CI)", hint: "deterministic interior bump, for exercising this panel without a human at the keyboard" },
  { value: "headless_test_drift", label: "Headless test: inject drift (dev/CI)", hint: "deterministic whole-mesh rescale, to prove the refusal actually fires" },
];

interface LaunchPlanPreview {
  ready: boolean;
  blender_executable: string | null;
  argv: string[] | null;
  problems: string[];
}

interface VerifyReceipt {
  verdict: "PASS" | "REFUSE";
  problems: string[];
  source_sha256: string;
  edited_sha256: string;
  coordinate_diff: {
    boundary_vertex_count: number;
    interior_vertex_count: number;
    max_boundary_displacement: number | null;
    max_interior_displacement: number | null;
    scale_drift_ratio: number | null;
    tolerance_used: number | null;
  };
}

function deriveDefault(meshPath: string, suffix: string, ext: string): string {
  if (!meshPath) return "";
  const dot = meshPath.lastIndexOf(".");
  const stem = dot > 0 ? meshPath.slice(0, dot) : meshPath;
  return `${stem}${suffix}.${ext}`;
}

export function BlenderRoundtrip({ meshPath }: { meshPath?: string | null }) {
  const [mesh, setMesh] = useState(meshPath ?? "");
  const [mode, setMode] = useState<LaunchMode>("headless_test_sculpt");
  const [outputPath, setOutputPath] = useState("");
  const [reportPath, setReportPath] = useState("");
  const [previewBusy, setPreviewBusy] = useState(false);
  const [preview, setPreview] = useState<LaunchPlanPreview | null>(null);
  const [launchBusy, setLaunchBusy] = useState(false);
  const [launchOut, setLaunchOut] = useState<Record<string, unknown> | null>(null);

  const [editedPath, setEditedPath] = useState("");
  const [receiptPath, setReceiptPath] = useState("");
  const [verifyBusy, setVerifyBusy] = useState(false);
  const [verifyOut, setVerifyOut] = useState<
    { state: "PASS" | "REFUSE" | "ERROR"; why?: string; receipt?: VerifyReceipt } | null
  >(null);

  const effectiveOutput = outputPath || deriveDefault(mesh, "-blender-edit", "obj");
  const effectiveReport = reportPath || deriveDefault(mesh, "-blender-report", "json");

  async function buildPreview() {
    setPreviewBusy(true);
    setPreview(null);
    try {
      const needsOutput = mode !== "interactive";
      const query = new URLSearchParams({ mesh_path: mesh, mode });
      if (needsOutput) {
        query.set("output_path", effectiveOutput);
        query.set("report_path", effectiveReport);
      }
      const response = await fetch(`/api/providers/blender/plan?${query.toString()}`);
      const body = await response.json();
      setPreview(body.plan as LaunchPlanPreview);
    } catch (error) {
      setPreview({ ready: false, blender_executable: null, argv: null,
                  problems: [error instanceof Error ? error.message : String(error)] });
    } finally {
      setPreviewBusy(false);
    }
  }

  async function launch() {
    setLaunchBusy(true);
    setLaunchOut(null);
    try {
      if (!sessionIsOpen()) await openSession();
      const params: Record<string, unknown> = { mesh_path: mesh, mode };
      if (mode !== "interactive") {
        params.output_path = effectiveOutput;
        params.report_path = effectiveReport;
      }
      const prepared = await planGoverned("blender.launch", params);
      if (isRefusal(prepared)) {
        setLaunchOut({ state: "REFUSED", why: prepared.reason });
        return;
      }
      if (prepared.plan.ready !== true) {
        setLaunchOut({ state: "REFUSED", why: "the launch plan is not executable", plan: prepared.plan });
        return;
      }
      const approvedPlanSha256 = String((prepared.plan as { plan_sha256?: string }).plan_sha256 ?? "");
      const executed = await runGoverned("blender.launch", { ...params, approved_plan_sha256: approvedPlanSha256 });
      if (isRefusal(executed)) {
        setLaunchOut({ state: "REFUSED", why: executed.reason });
        return;
      }
      const jobStatus = executed.result?.status ?? "SUBMITTED";
      const inner = (executed.result?.result ?? {}) as Record<string, unknown>;
      setLaunchOut({ state: jobStatus === "REFUSED" ? "REFUSED" : String(inner.status ?? jobStatus), ...inner });
      if (mode !== "interactive") {
        setEditedPath(effectiveOutput);
        if (!receiptPath) setReceiptPath(deriveDefault(mesh, "-blender-receipt", "json"));
      }
    } catch (error) {
      setLaunchOut({ state: "ERROR", why: error instanceof Error ? error.message : String(error) });
    } finally {
      setLaunchBusy(false);
    }
  }

  async function verify() {
    setVerifyBusy(true);
    setVerifyOut(null);
    try {
      if (!sessionIsOpen()) await openSession();
      const params: Record<string, unknown> = {
        source_mesh_path: mesh, edited_mesh_path: editedPath,
        receipt_path: receiptPath || deriveDefault(mesh, "-blender-receipt", "json"),
      };
      const prepared = await planGoverned("blender.verify_roundtrip", params);
      if (isRefusal(prepared)) {
        setVerifyOut({ state: "ERROR", why: prepared.reason });
        return;
      }
      const approvedPlanSha256 = String((prepared.plan as { plan_sha256?: string }).plan_sha256 ?? "");
      const executed = await runGoverned("blender.verify_roundtrip", { ...params, approved_plan_sha256: approvedPlanSha256 });
      if (isRefusal(executed)) {
        setVerifyOut({ state: "ERROR", why: executed.reason });
        return;
      }
      const result = executed.result?.result as { code?: string; why?: string; receipt?: VerifyReceipt } | undefined;
      if (executed.result?.status === "REFUSED") {
        setVerifyOut({ state: "REFUSE", why: result?.why, receipt: result?.receipt });
      } else {
        const ok = executed.result?.result as { receipt?: VerifyReceipt } | undefined;
        setVerifyOut({ state: "PASS", receipt: ok?.receipt });
      }
    } catch (error) {
      setVerifyOut({ state: "ERROR", why: error instanceof Error ? error.message : String(error) });
    } finally {
      setVerifyBusy(false);
    }
  }

  return (
    <div className="ag-prose" data-control="wb.blender-roundtrip">
      <p className="small" data-control="wb.blender.summary">
        Optional mesh repair through Blender, never the tracer. An edited mesh comes back only if its stitched boundary did not move.
      </p>
      <details className="wb-advanced" data-control="wb.blender.advanced">
        <summary className="wb-advanced-summary">Advanced details</summary>
        <p className="small faint">
          Blender is an optional interchange/repair provider (never the canonical tracer or
          flattener). This launches the mesh above into the real Blender executable, and refuses
          to accept an edited mesh back if its boundary loop — the stitch to neighbouring patches —
          moved beyond a tolerance measured from the mesh's own size. Sculpting the interior freely
          is the point; a coordinate or scale drift on the whole mesh is what gets refused.
        </p>
        <p className="small faint">
          After an edit exists — from the interactive session once you export it, or automatically
          from a headless test mode below — verify it here. The verify step never edits either mesh; it
          only reads both and writes one receipt.
        </p>
      </details>

      {
}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 8 }}>
        <label className="ops-field" style={{ fontSize: "var(--t-body)" }}>Mesh (.obj)
          <input value={mesh} onChange={(e) => setMesh(e.target.value)} placeholder="path to an existing .obj" />
        </label>
        <label className="ops-field" style={{ fontSize: "var(--t-body)" }}>Launch mode
          <select value={mode} onChange={(e) => setMode(e.target.value as LaunchMode)}>
            {MODES.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
          </select>
        </label>
      </div>
      <p className="small faint">{MODES.find((m) => m.value === mode)?.hint}</p>

      {mode !== "interactive" ? (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 8 }}>
          <label className="ops-field" style={{ fontSize: "var(--t-body)" }}>Output mesh (new file; never overwrites the source)
            <input value={outputPath} onChange={(e) => setOutputPath(e.target.value)} placeholder={effectiveOutput || "derived from the mesh path"} />
          </label>
          <label className="ops-field" style={{ fontSize: "var(--t-body)" }}>Report path
            <input value={reportPath} onChange={(e) => setReportPath(e.target.value)} placeholder={effectiveReport || "derived from the mesh path"} />
          </label>
        </div>
      ) : null}

      {
}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 8 }}>
        <button type="button" className="ag-btn" onClick={buildPreview} disabled={previewBusy || !mesh}
          style={{ opacity: 1 }}>
          {previewBusy ? "Checking…" : "Preview the command (read-only)"}
        </button>
        <button type="button" className="ag-btn" onClick={launch} disabled={launchBusy || !mesh}
          style={{ opacity: 1 }}>
          {launchBusy ? "Launching…" : "Launch in Blender"}
        </button>
      </div>

      {preview ? (
        <div role="status" className="ops-note" style={{ marginTop: 8 }}>
          <strong>{preview.ready ? "would run" : "would refuse"}</strong>
          {preview.blender_executable ? <div className="small">executable: <code>{preview.blender_executable}</code></div> : null}
          {preview.argv ? <div className="small" style={{ wordBreak: "break-all" }}>argv: <code>{preview.argv.join(" ")}</code></div> : null}
          {preview.problems?.length ? <div className="small">{preview.problems.join("; ")}</div> : null}
        </div>
      ) : null}

      {launchOut ? (
        <div role="status" className="ops-note" style={{ marginTop: 8 }}>
          <strong>{String(launchOut.state)}</strong>
          {launchOut.why ? <div className="small">{String(launchOut.why)}</div> : null}
          {launchOut.pid ? <div className="small">pid: <code>{String(launchOut.pid)}</code> — interactive; not waited on</div> : null}
          {launchOut.output_path ? <div className="small">output: <code>{String(launchOut.output_path)}</code></div> : null}
        </div>
      ) : null}

      <hr style={{ margin: "16px 0", border: "none", borderTop: "1px solid var(--line)" }} />

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 8 }}>
        <label className="ops-field" style={{ fontSize: "var(--t-body)" }}>Edited mesh (.obj)
          <input value={editedPath} onChange={(e) => setEditedPath(e.target.value)} placeholder="the mesh Blender produced" />
        </label>
        <label className="ops-field" style={{ fontSize: "var(--t-body)" }}>Receipt path
          <input value={receiptPath} onChange={(e) => setReceiptPath(e.target.value)} placeholder={deriveDefault(mesh, "-blender-receipt", "json") || "derived from the mesh path"} />
        </label>
      </div>
      <button type="button" className="ag-btn" onClick={verify} disabled={verifyBusy || !mesh || !editedPath}
        style={{ marginTop: 8, opacity: 1 }}>
        {verifyBusy ? "Verifying…" : "Verify edited mesh & write receipt"}
      </button>

      {verifyOut ? (
        <div
          role="status"
          className="ops-note"
          style={{
            marginTop: 8, padding: 10, borderRadius: 6,
            border: `1px solid ${verifyOut.state === "PASS" ? "var(--line)" : "var(--warn, #b45)"}`,
          }}
        >
          <strong>{verifyOut.state === "PASS" ? "PASS — accepted" : verifyOut.state === "REFUSE" ? "REFUSED — coordinate/scale drift or topology change" : "ERROR"}</strong>
          {verifyOut.why ? <div className="small" style={{ marginTop: 4 }}>{verifyOut.why}</div> : null}
          {verifyOut.receipt ? (
            <dl className="wb-stats" style={{ marginTop: 6 }}>
              <div><dt>boundary vertices</dt><dd>{verifyOut.receipt.coordinate_diff.boundary_vertex_count}</dd></div>
              <div><dt>interior vertices</dt><dd>{verifyOut.receipt.coordinate_diff.interior_vertex_count}</dd></div>
              <div><dt>max boundary displacement</dt><dd>{verifyOut.receipt.coordinate_diff.max_boundary_displacement ?? "—"}</dd></div>
              <div><dt>max interior displacement</dt><dd>{verifyOut.receipt.coordinate_diff.max_interior_displacement ?? "—"}</dd></div>
              <div><dt>scale drift ratio</dt><dd>{verifyOut.receipt.coordinate_diff.scale_drift_ratio ?? "—"}</dd></div>
              <div><dt>tolerance used</dt><dd>{verifyOut.receipt.coordinate_diff.tolerance_used ?? "—"}</dd></div>
              <div><dt>source sha256</dt><dd className="small">{verifyOut.receipt.source_sha256.slice(0, 16)}…</dd></div>
              <div><dt>edited sha256</dt><dd className="small">{verifyOut.receipt.edited_sha256.slice(0, 16)}…</dd></div>
            </dl>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
