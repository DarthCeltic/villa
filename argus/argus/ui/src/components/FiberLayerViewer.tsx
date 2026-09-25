import { useEffect, useState } from "react";
import { Chip } from "./Status";

type Inventory = {
  selected_scroll: string | null;
  available: boolean;
  why?: string;
  model?: string;
  revision?: string;
  semantic_state?: string;
  support?: number;
  fill_only?: number;
  not_a_scientific_result?: string;
};

export function FiberLayerViewer({ selectedScroll }: { selectedScroll: string | null }) {
  const [inventory, setInventory] = useState<Inventory | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setInventory(null);
    setError(null);
    if (!selectedScroll) return;
    fetch(`/api/fiber_layers?scroll=${encodeURIComponent(selectedScroll)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d: Inventory) => setInventory(d))
      .catch((e) => setError(String(e)));
  }, [selectedScroll]);

  if (!selectedScroll)
    return <Empty title="No scroll is selected" body="Select a physical scroll before opening model output." />;
  if (error) return <Empty title="The fibre output could not be listed" body={error} />;
  if (!inventory) return <Empty title="Reading fibre output" body="Checking the identity-bound fibre control…" />;
  if (!inventory.available)
    return <Empty title={`No fibre output is registered for ${selectedScroll}`} body={inventory.why ?? "The service did not find an identity-matched output."} />;

  return (
    <section className="ag-fiber-view" aria-label="Operational fibre model preview">
      <div className="ag-viewbar" role="group" aria-label="Fibre output provenance">
        <span className="ag-viewbar-label">fibre control · {selectedScroll}</span>
        <Chip tone="active" size="sm">operational preview</Chip>
        <span className="meta">supported patches {inventory.support ?? "—"} · fill-only {inventory.fill_only ?? "—"}</span>
      </div>
      <div className="ag-fiber-image-wrap">
        <img
          src={`/api/fiber_layer?scroll=${encodeURIComponent(selectedScroll)}`}
          alt={`Colour projection of the retained fibre model output for one supported patch of ${selectedScroll}`}
          className="ag-fiber-image"
        />
      </div>
      <div className="ag-fiber-note">
        <strong>Model output, not a reading.</strong> The declared classes are shown for inspection;
        their semantics are <span className="mono">{inventory.semantic_state ?? "not established"}</span>.
        This output is bound to {selectedScroll} and is not substituted for another scroll or treated as ink.
        <span className="meta"> {inventory.model ?? "model unavailable"} · {inventory.revision ?? "revision unavailable"}</span>
      </div>
    </section>
  );
}

function Empty({ title, body }: { title: string; body: string }) {
  return (
    <div className="ag-panel" data-testid="fiber-layer-state">
      <h3 className="ag-panel-title">{title}</h3>
      <p className="ag-prose">{body}</p>
      <p className="small faint">This is an asset or identity state, not an absent fibre finding.</p>
    </div>
  );
}
