import { Chip } from "./Status";
import { Link } from "react-router-dom";
import { SurfaceStatusChips, useSurfaceStatus } from "./SurfaceStatusFacts";
import {
  STAGE_ORDER,
  type Lane,
  type Collection,
  type ScrollObject,
} from "./ShelfUniverse";
import "../theme/workbench.css";

const LANE_CHIP: Record<Lane, { label: string; tone: "active" | "blocked" | "refused" }> = {
  FIRST_LETTERS: { label: "First Letters", tone: "active" },
  GRAND_PRIZE: { label: "Grand Prize", tone: "blocked" },
  PARIS4_TITLE: { label: "Paris 4 title lane", tone: "blocked" },
  CONTROL_OR_DEV: { label: "not prize-eligible", tone: "refused" },
  UNCLASSIFIED: { label: "eligibility unknown", tone: "blocked" },
};

export function ShelfCard({
  s,
  selected,
  onOpen,
  onContinue,
  domain,
  compact = false,
  inCompare,
  onCompare,
}: {
  s: ScrollObject;
  selected: boolean;
  onOpen: (scroll: string) => void;
  onContinue?: (s: ScrollObject) => void;
  domain: string;
  compact?: boolean;
  inCompare?: boolean;
  onCompare?: (s: ScrollObject) => void;
}) {
  const reached = Math.max(0, STAGE_ORDER.indexOf(s.surface.stage));
  const fill = Math.round((reached / (STAGE_ORDER.length - 1)) * 100);
  const { status: surfaceStatus } = useSurfaceStatus(s.id);
  return (
    <div className="ag-card" data-lane={s.lane} aria-current={selected ? "true" : undefined}>
      <span className="ag-card-spine" aria-hidden="true">
        <span className="ag-card-fill" style={{ height: `${fill}%` }} />
      </span>
      <span className="ag-card-body">
        <button
          type="button"
          className="ag-card-name"
          data-control={`${domain}.scroll.${s.id}`}
          aria-pressed={selected}
          onClick={() => onOpen(s.id)}
          style={{
            background: "none",
            border: 0,
            padding: 0,
            color: "inherit",
            font: "inherit",
            textAlign: "left",
            cursor: "pointer",
            minHeight: "var(--control-h)",
          }}
        >
          {s.display}
        </button>

        <span className="ag-card-chips">
          {s.firstLetters ? (
            <Chip tone="active" size="sm" title="on the published First Letters list">
              First Letters
            </Chip>
          ) : null}
          {s.grandPrize ? (
            <Chip tone="blocked" size="sm" title="also on the Grand Prize list">
              Grand Prize
            </Chip>
          ) : null}
          {!s.firstLetters && !s.grandPrize ? (
            <Chip tone="refused" size="sm">
              {LANE_CHIP.CONTROL_OR_DEV.label}
            </Chip>
          ) : null}
          {s.labels.present ? (
            <Chip tone="active" size="sm" title={s.labels.line}>
              ink labels
            </Chip>
          ) : null}
          {s.work.sealed ? (
            <Chip tone="blocked" size="sm">
              sealed work
            </Chip>
          ) : null}
          {s.fixture.fixture ? (
            <span className="ag-fixture" title={s.fixture.why ?? "a test fixture"}>
              fixture
            </span>
          ) : null}
        </span>

        {surfaceStatus ? (
          <span
            className="ag-card-chips"
            title="each badge is its own independently-sourced claim -- bytes present, a render existing, geometric admissibility and prize eligibility are never the same fact"
          >
            <SurfaceStatusChips status={surfaceStatus} size="sm" />
          </span>
        ) : null}

        <span className="ag-card-lines">
          <span className="ag-card-line">
            <b>Acquisition</b> {s.familyLine}
          </span>
          <span className="ag-card-line">
            <b>{compact ? "Here" : "Local data"}</b> {s.local.line}
          </span>
          {compact ? null : (
            <span className="ag-card-line">
              <b>Surface</b> {s.surface.line}
            </span>
          )}
          {compact ? null : (
            <span className="ag-card-line">
              <b>Work</b> {s.work.line}
            </span>
          )}
          {s.localDisagreement && !compact ? (
            <span className="ag-card-line" style={{ color: "var(--status-blocked)" }}>
              <b>Two routes disagree</b> {s.localDisagreement}
            </span>
          ) : null}
        </span>

        <span className="ag-card-next">
          <span className="ag-card-next-glyph" aria-hidden>
            →
          </span>
          {onContinue ? <button
            type="button"
            className="ag-card-next-action"
            data-control={`${domain}.next.${s.id}`}
            title={s.next.why}
            onClick={() => onContinue(s)}
            style={{
              background: "none",
              border: 0,
              padding: 0,
              color: "var(--ink)",
              font: "inherit",
              textAlign: "left",
              textDecoration: "underline",
              cursor: "pointer",
              minHeight: "var(--control-h)",
            }}
          >
            {s.next.label}
          </button> : <Link
            className="ag-card-next-action"
            data-control={`${domain}.next.${s.id}`}
            title={s.next.why}
            to={s.next.to}
          >
            {s.next.label}
          </Link>}
          {onCompare ? (
            <button
              type="button"
              className="ag-btn"
              aria-pressed={!!inCompare}
              data-control={`${domain}.compare.${s.id}`}
              title={
                inCompare
                  ? "remove this scroll from the compare tray"
                  : "add this scroll to the compare tray (two to four scrolls)"
              }
              onClick={() => onCompare(s)}
              style={{ marginLeft: "auto" }}
            >
              {inCompare ? "In compare" : "Compare"}
            </button>
          ) : null}
        </span>
      </span>
    </div>
  );
}

export function ShelfCollection({
  c,
  selected,
  onOpen,
  onContinue,
  domain,
  filterNote,
  compact = false,
  compareIds,
  onCompare,
}: {
  c: Collection;
  selected: string | null;
  onOpen: (scroll: string) => void;
  onContinue?: (s: ScrollObject) => void;
  domain: string;
  filterNote?: string | null;
  compact?: boolean;
  compareIds?: Set<string>;
  onCompare?: (s: ScrollObject) => void;
}) {
  return (
    <section
      className="ag-collection"
      data-lane={c.lane}
      data-collection={c.lane}
      aria-label={c.title}
    >
      <header className="ag-collection-head">
        <h2 className="ag-collection-title">{c.title}</h2>
        <span className="ag-collection-count" data-control={`${domain}.count.${c.lane}`}>
          {c.count.value}
        </span>
        <span className="ag-source" data-kind="route">
          {c.count.route} → {c.count.field}
        </span>
      </header>
      <p className="ag-collection-why">{c.why}</p>
      {c.note ? <p className="ag-collection-note">{c.note}</p> : null}
      {filterNote ? <p className="ag-filter-effect">{filterNote}</p> : null}
      {c.scrolls.length ? (
        <div className="ag-spines">
          {c.scrolls.map((s) => (
            <ShelfCard
              key={s.id}
              s={s}
              selected={s.id === selected}
              onOpen={onOpen}
              onContinue={onContinue}
              domain={domain}
              compact={compact}
              inCompare={compareIds?.has(s.id) ?? false}
              onCompare={onCompare}
            />
          ))}
        </div>
      ) : (
        <p className="ag-collection-why">
          Nothing in this collection matches the active filter. The collection still holds{" "}
          {c.count.value}; this is the filter, not the corpus.
        </p>
      )}
    </section>
  );
}
