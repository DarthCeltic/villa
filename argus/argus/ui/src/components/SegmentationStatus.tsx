import { useEffect, useState } from "react";
import { Chip } from "./Status";

type Row = {
  physical_scroll: string;
  seal: string;
  receipt_id?: string;
  model?: string;
  revision?: string;
  semantic_state?: string;
  claim_ceiling?: string;
  not_a_scientific_result?: string;
};

type Inventory = {
  selected_scroll: string | null;
  available: boolean;
  rows: Row[];
  registered_scrolls: string[];
  why?: string | null;
  claim_ceiling?: string;
};

export function SegmentationStatus({ selectedScroll }: { selectedScroll: string | null }) {
  const [state, setState] = useState<Inventory | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setState(null);
    setError(null);
    if (!selectedScroll) return;
    fetch(`/api/segmentation_status?scroll=${encodeURIComponent(selectedScroll)}`)
      .then((response) => (response.ok ? response.json() : Promise.reject(new Error(`HTTP ${response.status}`))))
      .then((payload: Inventory) => setState(payload))
      .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)));
  }, [selectedScroll]);

  if (!selectedScroll) return <Message title="No scroll is selected" body="Select a physical scroll before opening segmentation." />;
  if (error) return <Message title="Segmentation inventory failed" body={error} />;
  if (!state) return <Message title="Reading segmentation state" body="Checking identity-bound Villa outputs…" />;
  if (!state.available) {
    return <Message title={`No official segmentation seal for ${selectedScroll}`} body={state.why ?? "No matching output is registered."} />;
  }

  return (
    <section className="ag-panel" aria-label="Official segmentation status">
      <div className="ag-viewbar" role="group" aria-label="Segmentation provenance">
        <span className="ag-viewbar-label">Official segmentation · {selectedScroll}</span>
        <Chip tone="active" size="sm">operational output</Chip>
        <span className="meta">claim ceiling {state.claim_ceiling ?? "MECHANICS_ONLY"}</span>
      </div>
      <div className="ag-panel-body">
        {state.rows.map((row) => (
          <article className="ag-panel" key={row.seal}>
            <h3 className="ag-panel-title">{row.model ?? "Villa model"}</h3>
            <p className="small faint">{row.receipt_id ?? "sealed receipt"} · {row.seal}</p>
            <dl className="wb-stats">
              <Stat label="revision" value={row.revision ?? "not declared"} />
              <Stat label="semantic state" value={row.semantic_state ?? "not established"} />
              <Stat label="claim ceiling" value={row.claim_ceiling ?? "MECHANICS_ONLY"} />
            </dl>
            <p className="ag-prose">{row.not_a_scientific_result ?? "This is model output, not a scientific reading."}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return <><dt>{label}</dt><dd className="mono">{value}</dd></>;
}

function Message({ title, body }: { title: string; body: string }) {
  return (
    <div className="ag-panel" data-testid="segmentation-state">
      <h3 className="ag-panel-title">{title}</h3>
      <p className="ag-prose">{body}</p>
      <p className="small faint">No segmentation output is being substituted or interpreted as ink.</p>
    </div>
  );
}
