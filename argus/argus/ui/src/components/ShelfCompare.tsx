import type { ScrollObject } from "./ShelfUniverse";
import "../theme/workbench.css";

export const COMPARE_MAX = 4;

const ROWS: { label: string; of: (s: ScrollObject) => string }[] = [
  { label: "Identity", of: (s) => s.display },
  {
    label: "Prize lane",
    of: (s) =>
      [s.firstLetters ? "First Letters" : null, s.grandPrize ? "Grand Prize" : null]
        .filter(Boolean)
        .join(" + ") || (s.lane === "UNCLASSIFIED" ? "unknown (target registry not installed)" : "not prize-eligible"),
  },
  { label: "Acquisition", of: (s) => s.familyLine },
  {
    label: "Scan ids",
    of: (s) => s.acquisitions.map((a) => a.scanId ?? "undeclared").join(", ") || "none declared",
  },
  { label: "Local data", of: (s) => s.local.line },
  { label: "Surface", of: (s) => s.surface.line },
  { label: "Ink labels", of: (s) => s.labels.line },
  { label: "Work", of: (s) => s.work.line },
  {
    label: "Canonical identity",
    of: (s) =>
      s.inCanonicalIds
        ? "in /api/scroll-ids"
        : "NOT in /api/scroll-ids — carried from the prize registry",
  },
  { label: "Next action", of: (s) => s.next.label },
];

export function ShelfCompare({
  items,
  onRemove,
  onClear,
  domain,
}: {
  items: ScrollObject[];
  onRemove: (id: string) => void;
  onClear: () => void;
  domain: string;
}) {
  if (!items.length) return null;
  return (
    <section className="ag-tray" aria-label="Compare tray" data-control={`${domain}.tray`}>
      <div className="ag-tray-items">
        {items.map((s) => (
          <span key={s.id} className="ag-tray-item">
            {s.display}
            <button
              type="button"
              className="ag-tray-drop"
              aria-label={`Remove ${s.display} from the compare tray`}
              data-control={`${domain}.tray.remove.${s.id}`}
              onClick={() => onRemove(s.id)}
            >
              ×
            </button>
          </span>
        ))}
        <button
          type="button"
          className="ag-btn"
          data-control={`${domain}.tray.clear`}
          onClick={onClear}
        >
          Clear tray
        </button>
      </div>

      {items.length < 2 ? (
        <p className="ag-prose">
          Add one more scroll. One scroll is not a comparison, so the table appears at two and
          holds at most {COMPARE_MAX}.
        </p>
      ) : (
        <>
          <p className="ag-prose">
            These are different physical objects, scanned at different pitches and energies.
            The table places their recorded facts side by
            side; it computes no difference, because a delta between two incommensurable
            objects is a number about nothing.
          </p>
          <div className="ag-compare">
            <table>
              <caption className="ag-source" style={{ textAlign: "left", padding: "6px 10px" }}>
                Composed from /api/targets, /api/scroll-ids, /api/scrolls and the run feed.
              </caption>
              <thead>
                <tr>
                  <th scope="col">Fact</th>
                  {items.map((s) => (
                    <th key={s.id} scope="col">
                      {s.id}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {ROWS.map((r) => (
                  <tr key={r.label}>
                    <th scope="row">{r.label}</th>
                    {items.map((s) => (
                      <td key={s.id} data-control={`${domain}.tray.cell.${s.id}.${r.label}`}>
                        {r.of(s)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
