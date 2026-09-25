import { useEffect, useState } from "react";
import { Chip } from "./Status";
import { WINDINGS_STRIDE } from "./WindingInspector";

interface Summary {
  n_windings_total: number;
  correspondence: string;
  windings: { flags: string[] }[];
}

export function WindingSummary({ meshPath, onOpenCanvas, next }: { meshPath?: string | null; onOpenCanvas: () => void; next?: React.ReactNode }) {
  const [d, setD] = useState<Summary | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    setD(null);
    setErr(null);
    if (!meshPath) return;
    let live = true;
    fetch(`/api/windings?path=${encodeURIComponent(meshPath)}&stride=${WINDINGS_STRIDE}`)
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return (await r.json()) as Summary;
      })
      .then((j) => live && setD(j))
      .catch((e) => live && setErr(String(e)));
    return () => {
      live = false;
    };
  }, [meshPath]);

  if (!meshPath)
    return (
      <div className="wb-winding-summary" data-control="wb.windings.summary">
        <p className="small">No mesh is selected, so there are no windings to summarise.</p>
        {next ?? null}
      </div>
    );
  const anyFlags = d ? d.windings.some((w) => w.flags.length) : false;
  return (
    <div className="wb-winding-summary" data-control="wb.windings.summary">
      {err ? (
        <p className="small">
          <Chip tone="refused" size="sm">geometry unavailable</Chip> {err}
        </p>
      ) : !d ? (
        <p className="small">Reading the windings…</p>
      ) : (
        <p className="small" data-control="wb.windings.facts">
          <b>{d.n_windings_total}</b> winding{d.n_windings_total === 1 ? "" : "s"} · <Chip tone="blocked" size="sm">{d.correspondence.replace(/_/g, " ")}</Chip>{" "}
          <Chip tone={anyFlags ? "refused" : "active"} size="sm">{anyFlags ? "geometry flags present" : "no geometry flags"}</Chip> <Chip tone="active" size="sm">geometry, not ink</Chip>
        </p>
      )}
      <button type="button" className="ag-btn" data-control="wb.inspector.openWindings" onClick={onOpenCanvas}>
        Open on canvas
      </button>
      {next ?? null}
    </div>
  );
}
