import type { CoordinateSystem, FiberMatrix } from "../lib/taskBinding";

const FIBER_LABELS: Record<string, string> = {
  presence: "Fiber presence",
  hv_class: "H/V classification",
  direction: "Direction",
  adjacency: "Adjacency",
  tracing: "Tracing",
};

export function CoordinateDisclosure({ system }: { system: CoordinateSystem }) {
  return (
    <div className="meta" role="note" data-control="wb.volume3d.coordinates" data-positioning={system.positioning_system} data-fine-grid={system.fine_grid_screening_indices}>
      <b>Which coordinates place this view.</b> {system.positioning_statement} {system.fine_grid_statement}
    </div>
  );
}

export function FiberMatrixPanel({ matrix }: { matrix: FiberMatrix }) {
  return (
    <section aria-label="Fiber capabilities" data-control="wb.volume3d.fiber" style={{ display: "grid", gap: 4 }}>
      <div style={{ fontWeight: 600 }}>Fiber capabilities (five separate ones)</div>
      {matrix.rows.map((r) => (
        <div key={r.capability} className="meta" data-control={`wb.volume3d.fiber.${r.capability}`} data-state={r.state}>
          <b>{FIBER_LABELS[r.capability] ?? r.capability}</b> · <b>{r.state}</b>
          {r.reason ? ` · ${r.reason}` : ""} · satisfied only by <code>{r.satisfied_only_by}</code>
        </div>
      ))}
      <div className="meta" data-control="wb.volume3d.fiber.rule">
        One capability never satisfies another. {matrix.rule ?? ""}
      </div>
      {matrix.contracts.length ? (
        <div className="meta">from the imported science contracts: {matrix.contracts.map((c) => `${c.file} ${c.sha256.slice(0, 12)}…`).join(" · ")}</div>
      ) : null}
    </section>
  );
}

export interface ModelOutputRowData {
  label: string;
  provider: { id: string; version?: string | null };
  status_words: string[];
}

export function ModelOutputStatus({ overlay }: { overlay: ModelOutputRowData }) {
  return (
    <div className="meta" data-control="wb.volume3d.prediction" data-provider={overlay.provider.id}>
      <b>{overlay.label}</b> ·{" "}
      <span data-control="wb.volume3d.prediction.status">
        {overlay.status_words.map((w, i) => (
          <span key={w}>
            {i ? " / " : ""}
            <b>{w}</b>
          </span>
        ))}
      </span>{" "}
      · listed here with its declared status; this viewer does not draw model output.
    </div>
  );
}
