import { Landmark } from "lucide-react";
import type { RunRecord } from "../api";
import { Chip } from "../components/Status";

export const COLLECTIONS = [
  { id: "herculaneum", name: "Herculaneum", blurb: "Greco-Roman · Vesuvius", ready: true },
  { id: "egypt", name: "Egypt", blurb: "Lapis, ochre, monumental geometry", ready: false },
  {
    id: "mesopotamia",
    name: "Mesopotamia",
    blurb: "Clay, bitumen, cuneiform geometry",
    ready: false,
  },
  {
    id: "medieval",
    name: "Medieval",
    blurb: "Vellum, ultramarine, manuscript illumination",
    ready: false,
  },
  {
    id: "modern",
    name: "Modern",
    blurb: "Paper, graphite, archival photography",
    ready: false,
  },
];

export function Collections({
  current,
  onPick,
  runs,
}: {
  current: string;
  onPick: (id: string) => void;
  runs: RunRecord[];
}) {
  return (
    <div
      style={{
        padding: "20px 22px 44px",
        display: "grid",
        gap: 18,
        alignContent: "start",
        maxWidth: 1180,
      }}
    >
      <h1 style={{ margin: 0 }}>Collections</h1>
      <p className="muted" style={{ margin: 0, maxWidth: 760 }}>
        A collection is a FILTER over one instrument&rsquo;s holdings, not a second dataset.
        Choosing one changes accent, texture, imagery and display type — never a status
        colour, never a workflow, and never what is on the disk. Nothing here downloads,
        unlocks or reveals material: a collection with no material is an empty filter, and
        it is listed as one.
      </p>

      <div
        style={{
          display: "grid",
          gap: 14,
          gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 270px), 1fr))",
        }}
      >
        {COLLECTIONS.map((c) => {
          const count = runs.filter((r) => r.collection === c.id).length;
          return (
            <button
              key={c.id}
              disabled={!c.ready}
              onClick={() => onPick(c.id)}
              className={c.ready ? "panel interactive" : "panel"}
              aria-pressed={current === c.id}
              style={{
                textAlign: "left",
                padding: 18,
                cursor: c.ready ? "pointer" : "not-allowed",
                opacity: c.ready ? 1 : 0.55,
                borderLeft:
                  current === c.id ? "3px solid var(--accent)" : "3px solid transparent",
                display: "grid",
                gap: 9,
              }}
            >
              <Landmark size={17} aria-hidden style={{ color: "var(--accent)" }} />
              <div className="display" style={{ fontSize: "var(--t-h2)" }}>
                {c.name}
              </div>
              <div className="small faint">{c.blurb}</div>
              <div>
                {c.ready ? (
                  <Chip tone={count ? "active" : "blocked"} size="sm">
                    {count} {count === 1 ? "run" : "runs"}
                  </Chip>
                ) : (
                  <Chip tone="blocked" size="sm">
                    no material of any kind on this machine
                  </Chip>
                )}
              </div>
            </button>
          );
        })}
      </div>

      <p className="small faint" style={{ margin: 0, maxWidth: 760 }}>
        A run is counted here only when its manifest declared the collection. A collection
        is never inferred from a scroll&rsquo;s name, so targets without one appear on the
        Library&rsquo;s uncatalogued shelf rather than being assigned to a civilization they
        may not belong to. What material actually exists is a separate question, answered in
        the Library&rsquo;s Holdings room; this room only says how it is themed.
      </p>
    </div>
  );
}
