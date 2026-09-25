import { useEffect, useMemo, useState, useRef } from "react";
import { useSearchParams, Navigate, Link } from "react-router-dom";
import type { FeedState, UnrollIndex } from "../api";
import { api } from "../api";
import { TabBar, activeTab, type TabDef } from "../components/TabBar";
import { Disclosure } from "../components/Disclosure";
import { Library } from "./Library";
import { deriveRoute, useCapabilityGraph } from "../lib/argusTruth";
import { CONTEXT_KEYS, useArgusContext } from "../lib/context";
import { partitionFixtures } from "../lib/fixtures";
import { getJson, type Fetched } from "../lib/http";
import { usePoll } from "../lib/poll";
import {
  scrollOfTarget,
  tierOf,
  TIER_WORD,
  type EvidenceTier,
  type StackDetail,
} from "../lib/unrollScroll";
import {
  FILTERS,
  STAGE_ORDER,
  STAGE_WORD,
  filterEffect,
  matches,
  searchMatches,
  type Collection,
  type FilterKey,
  type ScrollObject,
  type Sourced,
  type Universe,
} from "../components/ShelfUniverse";
import { ShelfCompare, COMPARE_MAX } from "../components/ShelfCompare";
import { ShelfDetail } from "../components/ShelfDetail";
import { SurfaceStatusChips, useSurfaceStatus } from "../components/SurfaceStatusFacts";
import "../theme/workbench.css";
import "../theme/explore.css";
import { useSharedUniverse } from "../lib/sharedUniverse";
import { WorkRecommendations } from "../components/WorkRecommendations";

const TABS: TabDef[] = [
  { id: "scrolls", label: "Scrolls", hint: "every registered scroll, as an object" },
  { id: "archive", label: "Run archive", hint: "every run on disk", extra: { mode: "archive" } },
  { id: "texts", label: "Recovered texts", hint: "what has been read", extra: { mode: "texts" } },
  { id: "sources", label: "Sources", hint: "where the material came from" },
];

export const EXPLORE_MOVED_TO_SOURCES: Record<string, string | null> = {
  holdings: "holdings",
  sources: null,
};

export function Explore({ feed }: { feed: FeedState }) {
  const [params] = useSearchParams();
  const mode = params.get("mode");
  const asked = params.get("tab") ?? (mode === "holdings" ? "holdings" : null);
  if (asked && asked in EXPLORE_MOVED_TO_SOURCES) {
    const q = new URLSearchParams(params);
    q.delete("mode");
    const target = EXPLORE_MOVED_TO_SOURCES[asked];
    if (target) q.set("tab", target);
    else q.delete("tab");
    const search = q.toString();
    return <Navigate to={`/sources${search ? `?${search}` : ""}`} replace />;
  }
  const fromMode = mode === "archive" ? "archive" : mode === "texts" ? "texts" : null;
  const tab = activeTab(params, TABS, fromMode ?? "scrolls");

  return (
    <div className="hub">
      <TabBar tabs={TABS} current={tab} label="Explore sections" />
      <div className="hub-body">
        {tab === "scrolls" ? (
          <ScrollGrid feed={feed} />
        ) : (
          <Library
            feed={feed}
            hideModeTabs
            modeOverride={tab === "texts" ? "texts" : "archive"}
          />
        )}
      </div>
    </div>
  );
}


type CountRead =
  | { state: "OK"; text: string; why: string }
  | { state: "READING"; text: string; why: string }
  | { state: "NOT_READ"; text: string; why: string };

function readCount(c: Sourced<number | null>, u: Universe): CountRead {
  if (c.value === null) {
    const next = u.targetRegistry.nextAction;
    return { state: "NOT_READ", text: "unknown", why: `${u.targetRegistry.why ?? "Prize eligibility is unknown."}${next?.label ? ` Next: ${next.label}.` : ""}` };
  }
  const routes = c.route.split("+").map((r) => r.trim());
  const missing = routes.filter((r) => !u.sources.find((s) => s.route === r)?.ok);
  const cite = `${c.route} → ${c.field}`;
  if (!missing.length) return { state: "OK", text: String(c.value), why: cite };
  if (missing.includes("/api/targets") && u.targetRegistry.state === "UNAVAILABLE") {
    const next = u.targetRegistry.nextAction;
    return {
      state: "NOT_READ",
      text: "not configured",
      why: `${u.targetRegistry.why ?? "Prize eligibility is unknown."}${next?.label ? ` Next: ${next.label}${next.where ? ` in ${next.where}` : ""}.` : ""}`,
    };
  }
  if (u.loading) return { state: "READING", text: "…", why: `still reading ${missing.join(", ")}` };
  const failed = u.failures.filter((f) => missing.some((m) => f.startsWith(m)));
  return {
    state: "NOT_READ",
    text: "not read",
    why: `could not read ${missing.join(", ")}${failed.length ? `: ${failed.join("; ")}` : ""}`,
  };
}


interface Thumb {
  url: string;
  sha: string | null;
  path: string;
  stack: string;
  pitch: number | null;
  tier: EvidenceTier;
  banner: string | null;
}

type ThumbIndex =
  | { state: "READING" }
  | { state: "NOT_READ"; why: string }
  | { state: "OK"; byScroll: Map<string, Thumb> };

function useScrollThumbs(): ThumbIndex {
  const index = usePoll<UnrollIndex>("/api/unroll", { intervalMs: 120000 });
  const owned = useMemo(
    () => partitionFixtures(index.data?.targets ?? []).real.filter((t) => !!scrollOfTarget(t)),
    [index.data],
  );
  const keys = owned.map((t) => t.key).join("|");
  const [rows, setRows] = useState<{ keys: string; got: { scroll: string; key: string; r: Fetched<StackDetail> }[] } | null>(null);

  useEffect(() => {
    if (!owned.length) return;
    let alive = true;
    const ctl = new AbortController();
    void Promise.all(
      owned.map(async (t) => ({
        scroll: scrollOfTarget(t) as string,
        key: t.key,
        r: await getJson<StackDetail>(`/api/unroll?target=${encodeURIComponent(t.key)}`, {
          signal: ctl.signal,
        }),
      })),
    ).then((got) => {
      if (alive) setRows({ keys, got });
    });
    return () => {
      alive = false;
      ctl.abort();
    };
  }, [keys]);

  return useMemo<ThumbIndex>(() => {
    if (!index.data) {
      return index.failure
        ? { state: "NOT_READ", why: `exported layer stacks: ${index.failure.message}` }
        : { state: "READING" };
    }
    const byScroll = new Map<string, Thumb>();
    if (!owned.length) return { state: "OK", byScroll };
    if (!rows || rows.keys !== keys) return { state: "READING" };
    for (const { scroll, key, r } of rows.got) {
      if (!r.ok || byScroll.has(scroll)) continue;
      const d = r.data;
      const levelName = Object.keys(d.levels ?? {})[0];
      const level = levelName ? d.levels[levelName] : undefined;
      const img = level?.images?.["ct_texture"];
      if (!level || !img) continue;
      byScroll.set(scroll, {
        url: api.fileUrl(img.path),
        sha: img.sha256 ?? null,
        path: img.path,
        stack: d.target?.name ?? key,
        pitch: level.effective_pitch_um ?? null,
        tier: tierOf(d.result_class),
        banner: d.banner_required_on_every_surface ?? d.result_class?.banner ?? null,
      });
    }
    return { state: "OK", byScroll };
  }, [index.data, index.failure, owned, rows, keys]);
}


type LaneChoice = "ALL" | "FIRST_LETTERS" | "GRAND_PRIZE" | "NOT_ELIGIBLE";
type PlaceChoice = "ALL" | "HERE" | "UPSTREAM_ONLY" | "NEITHER";
type SortChoice = "REGISTERED" | "MOST_COMPLETE";

function ScrollGrid(_props: { feed: FeedState }) {
  const [params, setParams] = useSearchParams();
  const { ctx } = useArgusContext();
  const u = useSharedUniverse();
  const thumbs = useScrollThumbs();
  const cg = useCapabilityGraph();
  const detect = useMemo(
    () => deriveRoute(cg.data).find((s) => s.stage === "Detect") ?? null,
    [cg.data],
  );

  const [q, setQ] = useState("");
  const [on, setOn] = useState<Set<FilterKey>>(new Set());
  const [lane, setLane] = useState<LaneChoice>("ALL");
  const [place, setPlace] = useState<PlaceChoice>("ALL");
  const [family, setFamily] = useState<string>("ALL");
  const [sort, setSort] = useState<SortChoice>("MOST_COMPLETE");
  const [compare, setCompare] = useState<string[]>([]);

  const detailId = params.get("detail");
  const detail = detailId ? (u.byId.get(detailId) ?? null) : null;
  const scrollToDetailRef = useRef(false);
  const openDetail = (id: string | null) => {
    scrollToDetailRef.current = !!id;
    const next = new URLSearchParams(params);
    if (id) {
      next.set("detail", id);
      if (id !== ctx.scroll) {
        for (const k of CONTEXT_KEYS) if (k !== "scroll") next.delete(k);
        next.set("scroll", id);
      }
    } else {
      next.delete("detail");
    }
    setParams(next, { replace: false });
  };

  const detailShown = !!detail;
  useEffect(() => {
    if (!detailId || !detailShown || !scrollToDetailRef.current) return;
    scrollToDetailRef.current = false;
    const el = document.querySelector(`[data-control="explore.detail.${CSS.escape(detailId)}"]`);
    el?.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [detailId, detailShown]);

  const families = useMemo(() => {
    const all = new Set<string>();
    for (const s of u.scrolls) for (const f of s.families) all.add(f);
    return [...all].sort();
  }, [u.scrolls]);

  const laneOk = (s: ScrollObject) =>
    lane === "ALL"
      ? true
      : lane === "FIRST_LETTERS"
        ? s.firstLetters
        : lane === "GRAND_PRIZE"
          ? s.grandPrize
          : !s.firstLetters && !s.grandPrize;

  const placeOk = (s: ScrollObject) =>
    u.targetRegistry.state === "UNAVAILABLE"
      ? place === "ALL" || (place === "HERE" && s.local.state === "MATERIAL_INDEXED")
      : place === "ALL"
      ? true
      : place === "HERE"
        ? s.local.state === "MATERIAL_INDEXED"
        : place === "UPSTREAM_ONLY"
          ? s.publishedUpstream && s.local.state === "NOTHING_INDEXED"
          : !s.publishedUpstream && s.local.state === "NOTHING_INDEXED";

  const familyOk = (s: ScrollObject) => family === "ALL" || s.families.includes(family);

  const keep = (s: ScrollObject) => {
    if (!searchMatches(s, q)) return false;
    if (!laneOk(s) || !placeOk(s) || !familyOk(s)) return false;
    for (const k of on) if (!matches(s, k)) return false;
    return true;
  };

  const shown = u.scrolls.filter(keep);
  const orderShown = (rows: ScrollObject[]) => sort === "MOST_COMPLETE"
    ? [...rows].sort((a, b) => {
        const stage = STAGE_ORDER.indexOf(b.surface.stage) - STAGE_ORDER.indexOf(a.surface.stage);
        return stage || a.display.localeCompare(b.display);
      })
    : rows;
  const named = [
    ...FILTERS.filter((f) => on.has(f.key)),
    ...(lane === "ALL" ? [] : [{ label: `prize lane ${lane.replace(/_/g, " ").toLowerCase()}` }]),
    ...(place === "ALL"
      ? []
      : [
          {
            label:
              place === "HERE"
                ? "material on this machine"
                : place === "UPSTREAM_ONLY"
                  ? "published upstream but not held"
                  : "nothing published and nothing held",
          },
        ]),
    ...(family === "ALL" ? [] : [{ label: `acquisition ${family}` }]),
  ];
  const effect = filterEffect(shown.length, u.scrolls.length, named, q);
  const anyActive = effect.active;

  const inTray = new Set(compare);
  const trayItems = compare
    .map((id) => u.byId.get(id))
    .filter((s): s is ScrollObject => !!s);

  const toggleCompare = (s: ScrollObject) =>
    setCompare((prev) =>
      prev.includes(s.id)
        ? prev.filter((x) => x !== s.id)
        : prev.length >= COMPARE_MAX
          ? prev
          : [...prev, s.id],
    );

  const clearAll = () => {
    setOn(new Set());
    setQ("");
    setLane("ALL");
    setPlace("ALL");
    setFamily("ALL");
  };

  const registered = readCount(u.counts.registered, u);
  const fl = readCount(u.counts.firstLetters, u);
  const gp = readCount(u.counts.grandPrize, u);
  const rest = readCount(u.counts.controlsAndDev, u);
  const targetRegistryUnavailable = u.targetRegistry.state === "UNAVAILABLE";
  const displayCollections: Collection[] = u.collections;
  const laneCount = (r: CountRead) => (r.state === "OK" ? ` (${r.text})` : "");

  const detectorWhy = detect
    ? detect.why
    : "The capability service has not answered, so nothing is being asserted here about ink detection.";

  return (
    <div className="ag-explore ex-root" data-explore="root">
      {
}
      {detail ? (
        <ShelfDetail s={detail} onClose={() => openDetail(null)} domain="explore" targetRegistry={u.targetRegistry} />
      ) : null}
      <header className="ex-head">
        <h1 className="ag-shelf-title ex-title" data-novice="looking">Every registered scroll</h1>
        {}
        <Link className="ex-collections-link" to="/collections" data-control="explore.open-collections">Themed collections</Link>
        {
}
        <ul className="ex-counts" aria-label="What is registered" data-novice="ready">
          <CountPill r={registered} label="registered" control="explore.count.registered" />
          {targetRegistryUnavailable ? (
            <CountPill r={fl} label="prize eligibility" control="explore.count.eligibility" />
          ) : (
            <>
              <CountPill r={fl} label="First Letters" control="explore.count.firstLetters" />
              <CountPill
                r={gp}
                label={fl.state === "OK" ? `Grand Prize, within those ${fl.text}` : "Grand Prize, within First Letters"}
                control="explore.count.grandPrize"
              />
              <CountPill r={rest} label="controls and development" control="explore.count.controls" />
            </>
          )}
        </ul>
        <span className="ex-detector" data-control="explore.detector" data-novice="missing" title={detectorWhy}>
          <span aria-hidden="true">✕</span> Detector qualification
        </span>
      </header>

      {targetRegistryUnavailable ? (
        <p className="ex-failure" role="status" data-control="explore.targets.unconfigured" data-novice="missing">
          <b>Prize eligibility is not configured.</b> {u.targetRegistry.why}{" "}
          {u.targetRegistry.nextAction?.label ? (
            <Link to="/system?tab=updates#target-registry" data-control="explore.targets.configure">
              {u.targetRegistry.nextAction.label}
              {u.targetRegistry.nextAction.where ? ` in ${u.targetRegistry.nextAction.where}` : ""}
            </Link>
          ) : null}
          . Known scroll identities remain visible below; none is being called eligible or ineligible.
        </p>
      ) : u.failures.length ? (
        <p className="ex-failure" role="status" data-control="explore.failures" data-novice="missing">
          {u.failures.length} source{u.failures.length === 1 ? "" : "s"} could not be read, so
          some counts below say “not read” rather than a number. The routes are listed under
          “Where these numbers come from”.
        </p>
      ) : null}

      <Disclosure
        className="ex-why"
        summaryClassName="ex-why-summary"
        summary="Where these numbers come from, and what each collection means"
        data-control="explore.why"
      >
        <div className="ex-why-body">
          {targetRegistryUnavailable ? (
            <p className="ag-prose">
              The canonical identity inventory remains readable, but the official target registry is
              not installed. ARGUS therefore does not classify any known scroll as First Letters,
              Grand Prize, or ineligible. {u.targetRegistry.why}
            </p>
          ) : (
            <p className="ag-prose">
              {registered.text} registered identities. They are not one cohort: {fl.text} are First
              Letters targets, {gp.text} of those are also Grand Prize targets — an overlap, never an
              addition — and {rest.text} are controls or development scrolls with no prize
              eligibility at all. The collection counts are what EXISTS and do not move when a
              filter does.
            </p>
          )}
          <p className="ag-prose">
            <b>Detector qualification.</b> {detectorWhy}
          </p>
          <dl className="ex-why-list">
            <div>
              <dt>Registered</dt>
              <dd>
                <span className="ag-source" data-kind="route">
                  {u.counts.registered.route} → {u.counts.registered.field}
                </span>
              </dd>
            </div>
            {displayCollections.map((c) => (
              <div key={c.lane}>
                <dt>{c.title}</dt>
                <dd>
                  <span className="ag-source" data-kind="route">
                    {c.count.route} → {c.count.field}
                  </span>
                  <span className="ex-why-prose">{c.why}</span>
                  {c.note ? <span className="ex-why-prose">{c.note}</span> : null}
                </dd>
              </div>
            ))}
            <div>
              <dt>Card images</dt>
              <dd>
                <span className="ag-source">Receipt-backed exported layer stack · CT texture levels</span>
                <span className="ex-why-prose">
                  A card shows a picture only when that scroll&rsquo;s own exported layer stack
                  carries a CT texture. Every other card shows a drawn outline, which means no
                  image is on record — not that the scroll is blank.
                </span>
              </dd>
            </div>
            {u.sources.map((s) => (
              <div key={s.route}>
                <dt>{s.route}</dt>
                <dd>
                  <span className="ex-why-prose">
                    {s.ok
                      ? "answered"
                      : s.route === "/api/targets" && targetRegistryUnavailable
                        ? "NOT CONFIGURED"
                        : u.loading
                          ? "still being read"
                          : "NOT READ"} — {s.what}
                  </span>
                </dd>
              </div>
            ))}
            {u.failures.map((f) => (
              <div key={f}>
                <dt>Failure</dt>
                <dd>
                  <span className="ex-why-prose">{f}</span>
                </dd>
              </div>
            ))}
          </dl>
        </div>
      </Disclosure>

      <WorkRecommendations
        best={u.recommendations.bestNext}
        complete={u.recommendations.mostComplete}
        method={u.recommendations.method}
      />

      <div className="ex-toolbar" role="group" aria-label="Search and filters">
        <input
          id="explore.search"
          type="search"
          className="ag-search ex-search"
          placeholder="Search scrolls, aliases, scan ids, volume stores"
          aria-label="Search scrolls"
          data-control="explore.search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        {
}
        <label className="ex-select">
          <span className="visually-hidden">Sort order</span>
          <select
            className="ag-filter-select"
            data-control="explore.sort"
            value={sort}
            title="Sort order"
            onChange={(e) => setSort(e.target.value as SortChoice)}
          >
            <option value="REGISTERED">registered order</option>
            <option value="MOST_COMPLETE">most complete first</option>
          </select>
        </label>
        <label className="ex-select">
          <span className="visually-hidden">Prize lane</span>
          <select
            className="ag-filter-select"
            data-control="explore.lane"
            value={lane}
            title="Prize lane"
            disabled={targetRegistryUnavailable}
            onChange={(e) => setLane(e.target.value as LaneChoice)}
          >
            <option value="ALL">{targetRegistryUnavailable ? "prize eligibility not configured" : "all prize lanes"}</option>
            {
}
            {targetRegistryUnavailable ? null : (
              <>
                <option value="FIRST_LETTERS">First Letters{laneCount(fl)}</option>
                <option value="GRAND_PRIZE">Grand Prize{laneCount(gp)}</option>
                <option value="NOT_ELIGIBLE">not prize-eligible{laneCount(rest)}</option>
              </>
            )}
          </select>
        </label>
        <label className="ex-select">
          <span className="visually-hidden">Acquisition</span>
          <select
            className="ag-filter-select"
            data-control="explore.acquisition"
            value={family}
            title="Acquisition"
            onChange={(e) => setFamily(e.target.value)}
          >
            <option value="ALL">any acquisition</option>
            {families.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
        </label>
        <label className="ex-select">
          <span className="visually-hidden">Where the data is</span>
          <select
            className="ag-filter-select"
            data-control="explore.place"
            value={place}
            title="Where the data is"
            onChange={(e) => setPlace(e.target.value as PlaceChoice)}
          >
            <option value="ALL">data here or upstream</option>
            <option value="HERE">material on this machine</option>
            <option value="UPSTREAM_ONLY" disabled={targetRegistryUnavailable}>published upstream, not held</option>
            <option value="NEITHER" disabled={targetRegistryUnavailable}>nothing published, nothing held</option>
          </select>
        </label>
        <span className="ex-toggles">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              className="ag-filter"
              aria-pressed={on.has(f.key)}
              title={f.what}
              data-control={`explore.filter.${f.key}`}
              onClick={() =>
                setOn((prev) => {
                  const next = new Set(prev);
                  if (next.has(f.key)) next.delete(f.key);
                  else next.add(f.key);
                  return next;
                })
              }
            >
              {f.label}
            </button>
          ))}
        </span>
        <p
          className="ag-filter-effect ex-effect"
          data-active={anyActive ? "true" : "false"}
          data-control="explore.effect"
          aria-live="polite"
          title="The collection counts are what exists and do not move when a filter does."
        >
          {registered.state === "OK"
            ? effect.line
            : registered.state === "READING"
              ? `Showing ${shown.length} so far; the registered total is still being read.`
              : `Showing ${shown.length}; the registered total was not read, so this is not the whole corpus.`}
          {anyActive ? " Collection counts are unchanged." : ""}
        </p>
        {anyActive ? (
          <button
            type="button"
            className="ag-filter"
            data-control="explore.filter.clear"
            onClick={clearAll}
          >
            Clear filters
          </button>
        ) : null}
      </div>

      <ShelfCompare
        items={trayItems}
        onRemove={(id) => setCompare((prev) => prev.filter((x) => x !== id))}
        onClear={() => setCompare([])}
        domain="explore"
      />


      {displayCollections.map((c) => (
        <ExploreCollection
          key={c.lane}
          c={c}
          count={readCount(c.count, u)}
          parent={c.lane === "GRAND_PRIZE" ? fl : null}
          kept={orderShown(c.scrolls.filter(keep))}
          filtered={anyActive}
          selected={detailId}
          working={ctx.scroll}
          thumbs={thumbs}
          compareIds={inTray}
          targetRegistryUnavailable={targetRegistryUnavailable}
          onOpen={(id) => openDetail(id)}
          onCompare={toggleCompare}
        />
      ))}

      {compare.length >= COMPARE_MAX ? (
        <p className="ag-prose">
          The compare tray holds {COMPARE_MAX}. Remove one to add another — five columns of
          prose do not fit a laptop without pushing the last one off the side of the screen.
        </p>
      ) : null}
    </div>
  );
}

function CountPill({ r, label, control }: { r: CountRead; label: string; control: string }) {
  return (
    <li className="ex-count" data-state={r.state} data-control={control} title={r.why}>
      <b>{r.text}</b> {label}
    </li>
  );
}


function ExploreCollection({
  c,
  count,
  parent,
  kept,
  filtered,
  selected,
  working,
  thumbs,
  compareIds,
  targetRegistryUnavailable,
  onOpen,
  onCompare,
}: {
  c: Collection;
  count: CountRead;
  parent: CountRead | null;
  kept: ScrollObject[];
  filtered: boolean;
  selected: string | null;
  working: string | null;
  thumbs: ThumbIndex;
  compareIds: Set<string>;
  targetRegistryUnavailable: boolean;
  onOpen: (id: string) => void;
  onCompare: (s: ScrollObject) => void;
}) {
  return (
    <section
      className="ag-collection ex-collection"
      data-lane={c.lane}
      data-collection={c.lane}
      aria-label={c.title}
    >
      <header className="ex-collection-head">
        <h2 className="ag-collection-title">{c.title}</h2>
        <span
          className="ag-collection-count"
          data-state={count.state}
          data-control={`explore.count.${c.lane}`}
          title={count.why}
        >
          {count.text}
        </span>
        {parent ? (
          <span className="ex-subset" title={c.note ?? undefined}>
            overlapping the {parent.state === "OK" ? `${parent.text} ` : ""}First Letters
            targets, not an addition
          </span>
        ) : null}
        {filtered && count.state === "OK" ? (
          <span className="ex-kept">
            showing {kept.length} of {count.text}
          </span>
        ) : null}
      </header>
      {
}
      {count.state === "OK" && kept.length ? (
        <div className="ex-grid">
          {kept.map((s) => (
            <ExploreCard
              key={s.id}
              s={s}
              inCollection={c.lane}
              selected={s.id === selected}
              working={s.id === working}
              thumbs={thumbs}
              inCompare={compareIds.has(s.id)}
              targetRegistryUnavailable={targetRegistryUnavailable}
              onOpen={onOpen}
              onCompare={onCompare}
            />
          ))}
        </div>
      ) : count.state === "NOT_READ" ? (
        <p className="ex-empty" data-state="NOT_READ">
          Could not read this collection: {count.why}.
        </p>
      ) : count.state === "READING" ? (
        <p className="ex-empty" data-state="READING">
          Still reading this collection.
        </p>
      ) : filtered ? (
        <p className="ex-empty" data-state="FILTERED">
          Nothing in this collection matches the active filter. The collection still holds{" "}
          {count.text}; this is the filter, not the corpus.
        </p>
      ) : (
        <p className="ex-empty" data-state="EMPTY">
          This collection holds no scrolls.
        </p>
      )}
    </section>
  );
}


type Place = "HERE" | "UPSTREAM" | "NEITHER" | "UNKNOWN";

const PLACE: Record<Place, { word: string; glyph: string }> = {
  HERE: { word: "material indexed here", glyph: "■" },
  UPSTREAM: { word: "published upstream, not held", glyph: "◇" },
  NEITHER: { word: "nothing published, nothing held", glyph: "·" },
  UNKNOWN: { word: "upstream status not configured", glyph: "?" },
};

function ExploreCard({
  s,
  inCollection,
  selected,
  working,
  thumbs,
  inCompare,
  targetRegistryUnavailable,
  onOpen,
  onCompare,
}: {
  s: ScrollObject;
  inCollection: Collection["lane"];
  selected: boolean;
  working: boolean;
  thumbs: ThumbIndex;
  inCompare: boolean;
  targetRegistryUnavailable: boolean;
  onOpen: (id: string) => void;
  onCompare: (s: ScrollObject) => void;
}) {
  const place: Place =
    s.local.state === "MATERIAL_INDEXED"
      ? "HERE"
      : targetRegistryUnavailable
        ? "UNKNOWN"
        : s.publishedUpstream
          ? "UPSTREAM"
          : "NEITHER";
  const thumb = thumbs.state === "OK" ? (thumbs.byScroll.get(s.id) ?? null) : null;
  return (
    <div
      className="ag-card ex-card"
      data-lane={s.lane}
      data-place={place}
      aria-current={working ? "true" : undefined}
      data-selected={selected ? "true" : undefined}
    >
      <ScrollThumb s={s} thumb={thumb} index={thumbs} />
      <span className="ex-card-main">
        <button
          type="button"
          className="ag-card-name ex-card-name"
          data-control={`explore.scroll.${s.id}`}
          aria-pressed={selected}
          title={s.id === s.display ? undefined : s.id}
          onClick={() => onOpen(s.id)}
        >
          {s.display}
        </button>
        <span className="ex-place" data-place={place} title={s.local.line}>
          <span aria-hidden="true">{PLACE[place].glyph}</span> {PLACE[place].word}
        </span>
        <span className="ex-acq" title={s.familyLine}>
          {s.families.length
            ? s.families.join(" · ")
            : targetRegistryUnavailable
              ? "acquisition not configured"
              : "no acquisition declared"}
        </span>
      </span>

      <span className="ex-tags">
        {working ? (
          <span className="ex-tag" data-tag="working" title="this is the scroll in the working context (?scroll=)">
            working scroll
          </span>
        ) : null}
        {s.grandPrize && inCollection !== "GRAND_PRIZE" ? (
          <span className="ex-tag" title="also on the Grand Prize list">
            also Grand Prize
          </span>
        ) : null}
        {s.labels.present ? (
          <span className="ex-tag" title={s.labels.line}>
            ink labels declared
          </span>
        ) : null}
        {thumb ? (
          <span className="ex-tag" title={thumb.banner ?? undefined}>
            CT image · {TIER_WORD[thumb.tier]}
          </span>
        ) : null}
        {s.work.sealed ? (
          <span className="ex-tag" title={s.work.line}>
            sealed work
          </span>
        ) : null}
        {s.localDisagreement ? (
          <span className="ex-tag" data-tag="disagree" title={s.localDisagreement}>
            two routes disagree
          </span>
        ) : null}
        {s.fixture.fixture ? (
          <span className="ag-fixture" title={s.fixture.why ?? "a test fixture"}>
            fixture
          </span>
        ) : null}
      </span>

      <SurfaceStatusRow scroll={s.id} />

      <span className="ex-progress" data-control={`explore.progress.${s.id}`}>
        <strong>Furthest recorded:</strong> {STAGE_WORD[s.surface.stage]}
      </span>

      <span className="ex-card-actions">
        <Link
          className="ag-card-next-action ex-next"
          data-control={`explore.next.${s.id}`}
          title={s.next.why}
          to={s.next.to}
        >
          <span aria-hidden="true">→</span> {s.next.label}
        </Link>
        <button
          type="button"
          className="ag-btn ex-compare"
          aria-pressed={inCompare}
          data-control={`explore.compare.${s.id}`}
          title={
            inCompare
              ? "remove this scroll from the compare tray"
              : "add this scroll to the compare tray (two to four scrolls)"
          }
          onClick={() => onCompare(s)}
        >
          {inCompare ? "In compare" : "Compare"}
        </button>
      </span>
    </div>
  );
}

function SurfaceStatusRow({ scroll }: { scroll: string }) {
  const { status } = useSurfaceStatus(scroll);
  if (!status) return null;
  const n = Object.keys(status.fields).length;
  const COMPACT_MAX = 4;
  const capped =
    n > COMPACT_MAX
      ? {
          ...status,
          fields: Object.fromEntries(Object.entries(status.fields).slice(0, COMPACT_MAX)),
        }
      : status;
  return (
    <span
      className="ex-status"
      title="each badge below is its own independently-sourced claim -- bytes present, a render existing, geometric admissibility and prize eligibility are never the same fact. Open the scroll's detail panel for the complete, evidenced list."
    >
      <SurfaceStatusChips status={capped} size="sm" />
      {n > COMPACT_MAX ? (
        <span className="ex-tag" title={`${n - COMPACT_MAX} more status fact${n - COMPACT_MAX === 1 ? "" : "s"} in the detail panel`}>
          +{n - COMPACT_MAX} more
        </span>
      ) : null}
    </span>
  );
}

function ScrollThumb({ s, thumb, index }: { s: ScrollObject; thumb: Thumb | null; index: ThumbIndex }) {
  if (thumb) {
    return (
      <span className="ex-thumb" data-thumb="image">
        <img
          src={thumb.url}
          alt={`CT texture from ${thumb.stack}${thumb.pitch ? ` at ${thumb.pitch} µm` : ""}, ${TIER_WORD[thumb.tier]}`}
          title={`${thumb.stack} · ct_texture · ${thumb.path}${thumb.sha ? ` · sha256 ${thumb.sha}` : ""}`}
          data-sha256={thumb.sha ?? undefined}
          loading="lazy"
          decoding="async"
        />
      </span>
    );
  }
  const why =
    index.state === "NOT_READ"
      ? `image index could not be read (${index.why})`
      : index.state === "READING"
        ? "image index still being read"
        : `no image on record for ${s.id}`;
  return (
    <span className="ex-thumb" data-thumb={index.state === "OK" ? "none" : index.state} role="img" aria-label={why} title={why}>
      <svg viewBox="0 0 40 40" aria-hidden="true" focusable="false">
        <path
          d="M20 20 m0 -2 a2 2 0 1 1 -2 2 a4 4 0 0 1 4 -4 a6 6 0 0 1 6 6 a8 8 0 0 1 -8 8 a10 10 0 0 1 -10 -10 a12 12 0 0 1 12 -12 a14 14 0 0 1 14 14"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
        />
        {index.state === "NOT_READ" ? (
          <text x="31" y="36" fontSize="11" fill="currentColor" textAnchor="middle">?</text>
        ) : null}
      </svg>
    </span>
  );
}
