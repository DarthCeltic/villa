import { Link } from "react-router-dom";
import type { ScrollRecommendation } from "../lib/scrollStatus";

const detailHref = (scroll: string) =>
  `/explore?tab=scrolls&detail=${encodeURIComponent(scroll)}&scroll=${encodeURIComponent(scroll)}`;

function nextReason(row: ScrollRecommendation): string {
  const bits = [
    row.ready ? `${row.ready} step${row.ready === 1 ? "" : "s"} ready now` : null,
    row.decisions ? `${row.decisions} decision${row.decisions === 1 ? "" : "s"} waiting for a person` : null,
    row.blocked ? `${row.blocked} blocked` : null,
  ].filter(Boolean);
  return bits.length ? bits.join(" · ") : "nothing ready or waiting";
}

function completeReason(row: ScrollRecommendation): string {
  return `${row.done} of ${row.total} route receipts complete · ${row.verified_outputs} verified output${row.verified_outputs === 1 ? "" : "s"}`;
}

function Row({ row, lane, reasons }: { row: ScrollRecommendation; lane: "best" | "complete" | "both"; reasons: string[] }) {
  return (
    <article className="ag-work-rec" data-control={`work-recommendation.${lane}.${row.scroll}`}>
      <Link className="ag-work-rec__scroll" to={detailHref(row.scroll)} data-control={`work-recommendation.${lane}.${row.scroll}.open`}>
        <strong>{row.display}</strong>
        {reasons.map((r) => <span key={r}>{r}</span>)}
      </Link>
      <Link className="ag-work-rec__next" to={row.next.to} title={row.next.why} data-control={`work-recommendation.${lane}.${row.scroll}.next`}>
        {row.next.label}
      </Link>
    </article>
  );
}

const hasWork = (r: ScrollRecommendation) => r.ready > 0 || r.decisions > 0;

export function WorkRecommendations({
  best,
  complete,
  method,
  compact = false,
}: {
  best: ScrollRecommendation[];
  complete: ScrollRecommendation[];
  method: string;
  compact?: boolean;
}) {
  if (!best.length && !complete.length) return null;
  const limit = compact ? 1 : 3;
  const bestRows = best.filter(hasWork).slice(0, limit);
  const floor = complete.length ? Math.min(...complete.map((r) => r.done)) : 0;
  const completeRows = complete.filter((r) => r.verified_outputs > 0 || r.done > floor).slice(0, limit);
  const allTied = complete.length > 1 && completeRows.length === 0;
  const same = compact && bestRows[0] && completeRows[0] && bestRows[0].scroll === completeRows[0].scroll;
  return (
    <section className="ag-work-recs" data-control="work-recommendations" data-compact={compact ? "true" : undefined} aria-label="Where to work next">
      <header>
        <h2>Where to work next</h2>
        <p>Workflow progress and evidence availability — never an ink score.</p>
      </header>
      {same ? (
        <div className="ag-work-recs__lanes" data-same="true">
          <div>
            <h3>Most complete, and the best next work</h3>
            <Row row={bestRows[0]!} lane="both" reasons={[completeReason(completeRows[0]!), nextReason(bestRows[0]!)]} />
          </div>
        </div>
      ) : (
        <div className="ag-work-recs__lanes">
          <div>
            <h3>Best next work</h3>
            <p className="ag-work-recs__why">work that can be done now</p>
            {bestRows.length
              ? bestRows.map((row) => <Row key={row.scroll} row={row} lane="best" reasons={[nextReason(row)]} />)
              : <p className="ag-work-recs__none" data-control="work-recommendation.best.none">No scroll has a step ready or a decision waiting.</p>}
          </div>
          <div>
            <h3>Most complete route</h3>
            <p className="ag-work-recs__why">route receipts completed so far</p>
            {completeRows.length
              ? completeRows.map((row) => <Row key={row.scroll} row={row} lane="complete" reasons={[completeReason(row)]} />)
              : (
                <p className="ag-work-recs__none" data-control="work-recommendation.complete.none">
                  {allTied
                    ? `No scroll is further along than the others yet: each has ${floor} of ${complete[0]!.total} route receipts and no verified output.`
                    : "No scroll has a completed route receipt yet."}
                </p>
              )}
          </div>
        </div>
      )}
      {compact ? null : (
        <details>
          <summary>How ARGUS ranks this</summary>
          <p>{method}</p>
        </details>
      )}
    </section>
  );
}
