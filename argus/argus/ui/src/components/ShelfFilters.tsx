import { FILTERS, filterEffect, type FilterKey } from "./ShelfUniverse";
import "../theme/workbench.css";

export function ShelfFilters({
  q,
  onQ,
  on,
  onToggle,
  onClear,
  shown,
  total,
  domain,
  children,
}: {
  q: string;
  onQ: (v: string) => void;
  on: Set<FilterKey>;
  onToggle: (k: FilterKey) => void;
  onClear: () => void;
  shown: number;
  total: number;
  domain: string;
  children?: React.ReactNode;
}) {
  const active = FILTERS.filter((f) => on.has(f.key));
  const effect = filterEffect(shown, total, active, q);
  return (
    <div className="ag-filters" role="group" aria-label="Search and filters">
      <div className="ag-filter-row">
        <input
          type="search"
          className="ag-search"
          placeholder="Search scrolls, aliases, scan ids, volume stores"
          aria-label="Search scrolls"
          data-control={`${domain}.search`}
          value={q}
          onChange={(e) => onQ(e.target.value)}
        />
        {children}
      </div>
      <div className="ag-filter-row">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            className="ag-filter"
            aria-pressed={on.has(f.key)}
            title={f.what}
            data-control={`${domain}.filter.${f.key}`}
            onClick={() => onToggle(f.key)}
          >
            {f.label}
          </button>
        ))}
        {on.size || q ? (
          <button
            type="button"
            className="ag-filter"
            data-control={`${domain}.filter.clear`}
            onClick={onClear}
          >
            Clear filters
          </button>
        ) : null}
      </div>
      <p
        className="ag-filter-effect"
        data-active={effect.active ? "true" : "false"}
        data-control={`${domain}.filter.effect`}
        aria-live="polite"
      >
        {effect.line}
        {effect.active ? " The collection counts above are unchanged: they are what exists." : ""}
      </p>
    </div>
  );
}
