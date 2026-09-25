import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { RunRecord } from "../api";
import { deriveRoute, useCapabilityGraph } from "../lib/argusTruth";
import {
  matches,
  searchMatches,
  useScrollUniverse,
  type FilterKey,
  type ScrollObject,
} from "./ShelfUniverse";
import { ShelfCollection } from "./ShelfCard";
import { ShelfFilters } from "./ShelfFilters";
import { FILTERS, filterEffect } from "./ShelfUniverse";
import "../theme/workbench.css";

function RecommendationRow({
  row, label, onOpen,
}: {
  row: import("../lib/scrollStatus").ScrollRecommendation;
  label: string;
  onOpen: (scroll: string) => void;
}) {
  return (
    <article
      className="ag-recommendation interactive"
      data-control={`shelf.recommendation.${label}.${row.scroll}`}
      title={row.next.why}
    >
      <button
        type="button"
        className="ag-recommendation-open"
        onClick={() => onOpen(row.scroll)}
        data-control={`shelf.recommendation.${label}.${row.scroll}.inspect`}
      >
        <strong>{row.display}</strong>
        <span>Inspect scroll</span>
      </button>
      <span>{row.done}/{row.total} route receipts · {row.verified_outputs} verified outputs</span>
      <Link
        className="ag-recommendation-next"
        to={row.next.to}
        data-control={`shelf.recommendation.${label}.${row.scroll}.continue`}
      >
        Continue: {row.next.label}
      </Link>
    </article>
  );
}

export type { ShelfScroll } from "./ShelfUniverse";

export function ScrollShelf({
  runs,
  selected,
  onPick,
  onContinue,
  domain = "home",
}: {
  runs: RunRecord[];
  selected?: string | null;
  onPick: (scroll: string) => void;
  onContinue?: (s: ScrollObject) => void;
  domain?: string;
}) {
  const u = useScrollUniverse(runs);
  const cg = useCapabilityGraph();
  const detect = useMemo(
    () => deriveRoute(cg.data).find((s) => s.stage === "Detect") ?? null,
    [cg.data],
  );

  const [q, setQ] = useState("");
  const [on, setOn] = useState<Set<FilterKey>>(new Set());

  const keep = (s: ScrollObject) => {
    if (!searchMatches(s, q)) return false;
    for (const k of on) if (!matches(s, k)) return false;
    return true;
  };

  const shownTotal = u.scrolls.filter(keep).length;
  const effect = filterEffect(
    shownTotal,
    u.scrolls.length,
    FILTERS.filter((f) => on.has(f.key)),
    q,
  );

  if (u.loading && !u.scrolls.length) {
    return (
      <section className="ag-panel">
        <h2 className="ag-panel-title">Reading the registry…</h2>
        <p className="ag-prose">
          The shelf is composed from /api/targets, /api/scroll-ids and /api/scrolls. Nothing is
          shown until one of them answers — an empty shelf would read as “there are no
          scrolls”, which is a claim about the corpus rather than a report about a read.
        </p>
      </section>
    );
  }

  return (
    <div className="ag-shelf" data-shelf="root">
      <header className="ag-shelf-head">
        <h1 className="ag-shelf-title">Choose a scroll</h1>
        <p className="ag-shelf-sub">
          Nothing is selected until you pick one. Click a scroll to open its overview; the
          action on each card opens the work that already exists on it.
        </p>
        <p className="ag-shelf-universe" data-shelf="universe">
          <span>
            <b data-control="home.count.registered">{u.counts.registered.value}</b> registered
            scroll identities
          </span>
          <span>
            <b>{u.counts.firstLetters.value}</b> First Letters targets
          </span>
          <span>
            <b>{u.counts.grandPrize.value}</b> Grand Prize targets, overlapping those{" "}
            {u.counts.firstLetters.value}
          </span>
          <span>
            <b>{u.counts.controlsAndDev.value}</b> controls and development scrolls
          </span>
        </p>
        <p className="ag-source" data-kind="route">
          {u.counts.registered.route} → {u.counts.registered.field} ·{" "}
          {u.counts.firstLetters.route} → {u.counts.firstLetters.field} /{" "}
          {u.counts.grandPrize.field}
        </p>
        <p className="ag-collection-note" data-shelf="qualification">
          <b>Ink detection.</b>{" "}
          {detect
            ? detect.why
            : "The capability service has not answered, so the qualification state of ink detection is not being asserted here."}{" "}
          Choosing a scroll selects what to look at; it does not make anything on it a reading.
        </p>
        {u.missingFromCanonicalIds.length ? (
          <p className="ag-collection-note">
            {u.missingFromCanonicalIds.length} declared prize target
            {u.missingFromCanonicalIds.length === 1 ? " is" : "s are"} absent from the canonical
            identity list ({u.missingFromCanonicalIds.join(", ")}). They are on the shelf from
            the prize registry, and the omission is reported rather than smoothed over: the
            identity list is the thing that should be corrected, and a silently repaired input
            never gets corrected.
          </p>
        ) : null}
        {u.failures.length ? (
          <p className="ag-collection-note" data-shelf="failures">
            {u.failures.length} of the routes this shelf reads did not answer:{" "}
            {u.failures.join("; ")}. What is shown came from the routes that did — this is a
            report about reads, not a claim about the corpus.
          </p>
        ) : null}
      </header>

      <ShelfFilters
        q={q}
        onQ={setQ}
        on={on}
        onToggle={(k) =>
          setOn((prev) => {
            const next = new Set(prev);
            if (next.has(k)) next.delete(k);
            else next.add(k);
            return next;
          })
        }
        onClear={() => {
          setOn(new Set());
          setQ("");
        }}
        shown={shownTotal}
        total={u.scrolls.length}
        domain={domain}
      />

      {u.recommendations.bestNext.length || u.recommendations.mostComplete.length ? (
        <section className="ag-recommendations" aria-label="Where to work next">
          <header>
            <h2>Where to work next</h2>
            <p>Workflow opportunity and completed evidence are ranked separately. Neither is a scientific score.</p>
          </header>
          <div className="ag-recommendation-columns">
            <div>
              <h3>Best next work</h3>
              {u.recommendations.bestNext.slice(0, 3).map((row) => (
                <RecommendationRow key={row.scroll} row={row} label="best" onOpen={onPick} />
              ))}
            </div>
            <div>
              <h3>Most complete</h3>
              {u.recommendations.mostComplete.slice(0, 3).map((row) => (
                <RecommendationRow key={row.scroll} row={row} label="complete" onOpen={onPick} />
              ))}
            </div>
          </div>
          <details>
            <summary>How this ranking works</summary>
            <p>{u.recommendations.method}</p>
          </details>
        </section>
      ) : null}

      {u.collections.map((c) => {
        const kept = c.scrolls.filter(keep);
        return (
          <ShelfCollection
            key={c.lane}
            c={{ ...c, scrolls: kept }}
            selected={selected ?? null}
            onOpen={onPick}
            onContinue={onContinue}
            domain={domain}
            filterNote={
              effect.active
                ? `Showing ${kept.length} of this collection’s ${c.count.value} — ${effect.line.toLowerCase()}`
                : null
            }
          />
        );
      })}
    </div>
  );
}
