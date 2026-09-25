import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type FeedState, type RunRecord, type UnrollIndex, type UnrollTarget } from "../api";
import { usePoll } from "../lib/poll";
import { failureLine } from "../lib/http";
import {
  derivePlainState,
  deriveRoute,
  useCapabilityGraph,
  useGates,
  type Claim,
  type ResultClass,
  type StageCell,
} from "../lib/argusTruth";
import { partitionFixtures } from "../lib/fixtures";
import { groupByDeclaredTarget, partitionRuns, primaryRunForScroll } from "../lib/runIdentity";
import { useNarrow } from "../lib/useNarrow";
import { routes } from "../lib/nav";
import { Glyph, RouteStrip } from "../components/RouteStrip";
import { Disclosure } from "../components/Disclosure";
import { useArgusContext } from "../lib/context";
import { scrollOfTarget } from "../lib/unrollScroll";
import { ScrollArchive } from "../components/archive/ScrollArchive";
import { ScrollFactsTable } from "../components/ScrollFacts";
import { deviceClass } from "../lib/displayPrefs";
import { type Lane, type ScrollObject } from "../components/ShelfUniverse";
import { useSharedUniverse } from "../lib/sharedUniverse";
import { ScrollPipelineNarrative } from "../components/ScrollPipelineNarrative";
import { Chip } from "../components/Status";
import { OpenProblemsPanel } from "../components/OpenProblemsPanel";
import { WorkRecommendations } from "../components/WorkRecommendations";
import "../theme/workbench.css";
import "../theme/ops.css";


interface UnrollDetail {
  result_class: ResultClass;
  banner_required_on_every_surface: string;
  classification: string;
  target: {
    name: string;
    pitch_um: number;
    shape: number[];
    physical_mm: number[];
    acquisition: string;
    renderer: string;
  };
  ground_truth: { state: string; why: string };
  levels: Record<
    string,
    { what: string; effective_pitch_um: number; readable: string; size: number[]; images: Record<string, { path: string; sha256?: string }> }
  >;
  file_base: string;
}


function runForScroll(all: RunRecord[], scroll: string | null) {
  if (!scroll) return { run: null as RunRecord | null, basis: "no scroll is named by this target" };
  const { runs } = partitionRuns(all.filter((r) => !r.foreign));
  const groups = groupByDeclaredTarget(runs).filter(
    (g) => typeof g.target === "string" && g.target.includes(scroll),
  );
  const run = primaryRunForScroll(all, scroll);
  const best = run ? groups.find((g) => g.records.some((r) => r.run_id === run.run_id)) ?? null : null;
  return {
    run,
    basis: best
      ? `${groups.length} result${groups.length === 1 ? "" : "s"} declare a target naming ` +
        `${scroll}; this is the most certified of them, and the richest of its ` +
        `${best.records.length} recording${best.records.length === 1 ? "" : "s"}`
      : `no run in the feed declares a target naming ${scroll}`,
  };
}

export function Home({ feed }: { feed: FeedState }) {
  const [params, setParams] = useSearchParams();
  const narrow = useNarrow(760);

  const index = usePoll<UnrollIndex>("/api/unroll", { intervalMs: 60000 });
  const cg = useCapabilityGraph();
  const gates = useGates();

  const split = useMemo(
    () => partitionFixtures(index.data?.targets ?? []),
    [index.data],
  );

  const { ctx, set } = useArgusContext();
  const chosenKey = params.get("target");
  const target: UnrollTarget | null = useMemo(() => {
    if (!ctx.scroll) return null;
    const list = split.real.filter((t) => scrollOfTarget(t) === ctx.scroll);
    if (!list.length) return null;
    if (chosenKey) return list.find((t) => t.key === chosenKey) ?? null;
    return list[0] ?? null;
  }, [split.real, chosenKey, ctx.scroll]);

  const detail = usePoll<UnrollDetail>(
    target ? `/api/unroll?target=${encodeURIComponent(target.key)}` : "/api/unroll",
    { intervalMs: 120000 },
  );
  const stack = target && detail.data && "target" in detail.data ? detail.data : null;

  const route: StageCell[] = useMemo(() => deriveRoute(cg.data), [cg.data]);
  const state = useMemo(
    () => derivePlainState(cg.data, gates.data, stack?.result_class ?? null),
    [cg.data, gates.data, stack],
  );

  const universe = useSharedUniverse();
  const me: ScrollObject | null = ctx.scroll ? (universe.byId.get(ctx.scroll) ?? null) : null;
  const family = useMemo(() => {
    if (!me) {
      return {
        line: null as string | null,
        why:
          "Not available: no scroll is selected, so no acquisition is being asserted for the picture.",
      };
    }
    if (!me.families.length) {
      return {
        line: null as string | null,
        why:
          "Not available: the eligible-target registry declares no pitch and energy for this scroll, and an acquisition is never inferred from a file name.",
      };
    }
    const siblings = universe.scrolls.filter((x) =>
      x.families.some((f) => me.families.includes(f)),
    ).length;
    return {
      line: me.familyLine,
      why: `${siblings} registered scroll${siblings === 1 ? "" : "s"} share this acquisition`,
    };
  }, [me, universe.scrolls]);

  const preview = useMemo(() => {
    if (!stack) return null;
    const levelName = Object.keys(stack.levels)[0];
    if (!levelName) return null;
    const level = stack.levels[levelName];
    if (!level) return null;
    const img = level.images["ct_texture"] ?? null;
    if (!img) return null;
    return {
      url: api.fileUrl(img.path),
      sha: img.sha256 ?? null,
      path: img.path,
      pitch: level.effective_pitch_um,
      readable: level.readable,
      size: level.size,
      what: level.what,
    };
  }, [stack]);

  const scroll = scrollOfTarget(target);
  const loading = index.loading || cg.loading;

  const chosen = useMemo(
    () => runForScroll(feed.data?.runs ?? [], scroll),
    [feed.data, scroll],
  );
  const scrollQ = ctx.scroll ? `scroll=${encodeURIComponent(ctx.scroll)}` : "";
  const workbenchHref = target
    ? `${routes.workbench(chosen.run?.run_id ?? null)}?target=${encodeURIComponent(target.key)}${
        scrollQ ? `&${scrollQ}` : ""
      }`
    : `${routes.workbench(chosen.run?.run_id ?? null)}${scrollQ ? `?${scrollQ}` : ""}`;

  const secondary = (
    <div
      className="home-secondary"
      style={{ display: "flex", gap: 10, flexWrap: "wrap", minWidth: 0 }}
    >
      <Link
        data-action="secondary"
        data-control="home.blockers"
        className="link-control interactive"
        to="/review?tab=blockers"
        style={{ minHeight: "var(--control-h-primary)", padding: "0 16px" }}
      >
        What is blocking ink detection
      </Link>
      <Link
        data-action="secondary"
        data-control="home.chooseAnother"
        className="link-control interactive"
        to="/explore?tab=scrolls"
        style={{ minHeight: "var(--control-h-primary)", padding: "0 16px" }}
      >
        Choose a different scroll
      </Link>
    </div>
  );

  const overview = (
    <div className="home-grid">
      {}
      {
}
      {me ? (
        <section className="ag-collection" data-lane={me.lane} aria-label="Scroll overview">
          <header className="ag-collection-head">
            <h2 className="ag-collection-title" data-control={`home.overview.${me.id}`}>
              {me.display}
            </h2>
            {me.firstLetters ? (
              <Chip tone="active" size="sm">
                First Letters target
              </Chip>
            ) : null}
            {me.grandPrize ? (
              <Chip tone="blocked" size="sm">
                Grand Prize target
              </Chip>
            ) : null}
            {me.lane === "CONTROL_OR_DEV" ? (
              <Chip tone="refused" size="sm">
                not prize-eligible
              </Chip>
            ) : me.lane === "UNCLASSIFIED" ? (
              <Chip tone="blocked" size="sm">
                prize eligibility unknown
              </Chip>
            ) : null}
            {me.labels.present ? (
              <Chip tone="active" size="sm">
                ink labels
              </Chip>
            ) : null}
            {me.fixture.fixture ? (
              <span className="ag-fixture">fixture</span>
            ) : null}
            <span className="ag-collection-count">
              1 of {universe.counts.registered.value} registered
            </span>
          </header>
          <ScrollPipelineNarrative scroll={me} universe={universe} run={chosen.run} />
          <div className="ag-card-lines">
            <span className="ag-card-line">
              <b>Acquisition</b> {me.familyLine}
            </span>
            <span className="ag-card-line">
              <b>Local data</b> {me.local.line} <span className="ag-source" data-kind="route">{me.local.route}</span>
            </span>
            <span className="ag-card-line">
              <b>Surface</b> {me.surface.line}{" "}
              <span className="ag-source" data-kind="route">{me.surface.route}</span>
            </span>
            <span className="ag-card-line">
              <b>Work</b> {me.work.line}
            </span>
            <span className="ag-card-line">
              <b>Ink labels</b> {me.labels.line}
            </span>
            {me.localDisagreement ? (
              <span className="ag-card-line" style={{ color: "var(--status-blocked)" }} data-novice="missing">
                <b>Two routes disagree</b> {me.localDisagreement}
              </span>
            ) : null}
          </div>
          <div className="ag-actions">
            <button
              type="button"
              className="ag-btn"
              data-control="home.backToShelf"
              onClick={() => set({ scroll: null })}
            >
              Back to the shelf
            </button>
            <Link className="ag-btn" data-control="home.openInExplore" to={`/explore?tab=scrolls&detail=${encodeURIComponent(me.id)}`}>
              Every recorded identifier for this scroll
            </Link>
          </div>
        </section>
      ) : (
        <section className="ag-collection" data-lane="CONTROL_OR_DEV" aria-label="Scroll overview">
          <header className="ag-collection-head">
            <h2 className="ag-collection-title">{ctx.scroll}</h2>
          </header>
          <p className="ag-prose">
            {universe.loading
              ? "Reading the registry for this scroll…"
              : `No record in /api/targets, /api/scroll-ids or /api/scrolls names ${ctx.scroll}. ` +
                "Nothing is being substituted for it."}
          </p>
          <div className="ag-actions">
            <button
              type="button"
              className="ag-btn"
              data-control="home.backToShelf"
              onClick={() => set({ scroll: null })}
            >
              Back to the shelf
            </button>
          </div>
        </section>
      )}

      {}
      <section className="home-frame" data-home="frame" aria-label="Artifact preview">
        <div className="home-artifact" data-home="artifact" data-has-image={preview ? "true" : "false"}>
          {preview ? (
            <img
              src={preview.url}
              alt={
                stack
                  ? `${stack.target.name}: ${preview.what}. ${preview.readable}.`
                  : "the exported surface plane for this target"
              }
              className="home-img"
            />
          ) : (
            <p className="home-empty small">
              {detail.failure
                ? `Not available: ${failureLine(detail.failure)}`
                : loading
                  ? "Reading the exported layer stack…"
                  : "Not available: no layer stack has been exported for this target, so there is no real image to show. Nothing is hidden here — the export has not been run."}
            </p>
          )}

          {
}
          <figcaption className="home-caption">
            <span className="home-name" data-home="target-name" data-novice="looking">
              {stack?.target.name ?? target?.name ?? (loading ? "Reading…" : "No target")}
            </span>
            <span className="home-acq" data-home="acquisition">
              {family.line ?? "Acquisition not available"}
              <span className="faint"> · {family.why}</span>
            </span>
            {preview ? (
              <span className="home-plane meta">
                {preview.what} · {preview.readable}
              </span>
            ) : null}
          </figcaption>
        </div>

        {
}
        {stack ? (
          <p className="home-banner" role="note">
            {stack.banner_required_on_every_surface}
          </p>
        ) : null}
      </section>

      {}
      <section className="home-act" data-home="action" aria-label="Current state and next action">
        <p className="home-state" data-home="state" data-novice="ready">
          {state.sentence}
        </p>
        <div className="home-buttons">
          <Link
            data-action="primary"
            data-control="home.continue"
            className="link-control interactive home-primary"
            to={workbenchHref}
            title={chosen.basis}
          >
            {chosen.run ? "Continue" : "Open"} {scroll ?? ctx.scroll ?? "this target"} in the
            Workbench
          </Link>
          {!narrow ? secondary : null}
        </div>
      </section>

      {}
      <RouteStrip route={route} scroll={me} vocabulary={universe.journey?.vocabularies.route_strip} />

      {}
      {
}
      <Disclosure
        className="home-details"
        summaryClassName="home-summary"
        summary="Details and evidence"
        data-home="details"
      >
        <div className="home-details-body">
          <Blocker state={state} />
          <ClaimList claims={state.claims} />
          <RouteDetail route={route} />
          <ArtifactDetail preview={preview} stack={stack} />
          <OpensWhat chosen={chosen} />
          <Elsewhere />
          <Fixtures n={split.fixtures.length} />
          <Switcher
            targets={split.real}
            current={target}
            onPick={(k) => {
              const next = new URLSearchParams(params);
              next.set("target", k);
              setParams(next, { replace: true });
            }}
          />
        </div>
      </Disclosure>

      {
}
      {narrow ? <div className="home-secondary-narrow">{secondary}</div> : null}
    </div>
  );

  const laneParam = params.get("collection");
  const lanes: Lane[] = ["FIRST_LETTERS", "GRAND_PRIZE", "PARIS4_TITLE", "CONTROL_OR_DEV"];
  const collection: Lane = lanes.includes(laneParam as Lane)
    ? (laneParam as Lane)
    : (me?.lane ?? "FIRST_LETTERS");

  return (
    <>
      <WorkRecommendations
        best={universe.recommendations.bestNext}
        complete={universe.recommendations.mostComplete}
        method={universe.recommendations.method}
        compact
      />
      <ScrollArchive
        universe={universe}
        deviceClass={deviceClass()}
        selected={ctx.scroll}
        collection={collection}
        onCollection={(lane) => {
          const next = new URLSearchParams(params);
          next.set("collection", lane);
          setParams(next, { replace: true });
        }}
        onSelect={(id) => set({ scroll: id })}
        renderDetail={() => overview}
      />
      {
}
      <ScrollFactsTable
        universe={universe}
        lane={collection}
        caption={`Where each scroll stands: ${LANE_CAPTION[collection]}`}
        control="home.facts"
      />
      <OpenProblemsPanel compact />
    </>
  );
}

const LANE_CAPTION: Record<Lane, string> = {
  FIRST_LETTERS: "First Letters",
  GRAND_PRIZE: "Grand Prize",
  PARIS4_TITLE: "Paris 4 title",
  CONTROL_OR_DEV: "controls and development",
  UNCLASSIFIED: "prize eligibility unknown",
};



function Blocker({ state }: { state: ReturnType<typeof derivePlainState> }) {
  if (!state.blocker) {
    return (
      <section className="home-block">
        <h3 className="eyebrow">What is blocking a reading</h3>
        <p className="small">Nothing is reported as blocking. That is the derivation&rsquo;s finding, not a certificate.</p>
      </section>
    );
  }
  return (
    <section className="home-block">
      <h3 className="eyebrow">What is blocking a reading</h3>
      <p className="home-blocker-what">
        <Glyph tone="blocked" /> {state.blocker.what}
      </p>
      <p className="small">{state.blocker.why}</p>
      {state.blocker.nextAction ? (
        <p className="small">
          <strong>Next legal step:</strong> {state.blocker.nextAction}
        </p>
      ) : (
        <p className="small faint">
          No next step is declared by the gate ladder, so none is shown. An invented one
          would be a suggestion dressed as an instruction.
        </p>
      )}
    </section>
  );
}

function ClaimList({ claims }: { claims: Claim[] }) {
  if (!claims.length) return null;
  return (
    <section className="home-block">
      <h3 className="eyebrow">Source for every claim above</h3>
      <ul className="home-claims">
        {claims.map((c, i) => (
          <li key={`${c.route}${c.field}${i}`}>
            <span className="small">{c.says}</span>
            <span className="meta">
              {c.route} → {c.field}
              {c.receipt ? ` · ${c.receipt}` : ""}
              {c.sha256 ? ` · ${c.sha256.slice(0, 12)}…` : ""}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function RouteDetail({ route }: { route: StageCell[] }) {
  return (
    <section className="home-block">
      <h3 className="eyebrow">The six stages, in the contract&rsquo;s own words</h3>
      <ul className="home-claims">
        {route.map((s) => (
          <li key={s.stage}>
            <span className="small">
              <strong>{s.stage}</strong> — {s.why}
            </span>
            <span className="meta">
              {s.internal ?? "no aggregate reported"} · /api/capability_graph
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function ArtifactDetail({
  preview,
  stack,
}: {
  preview: { path: string; sha: string | null; size: number[]; pitch: number } | null;
  stack: UnrollDetail | null;
}) {
  if (!preview || !stack) return null;
  return (
    <section className="home-block">
      <h3 className="eyebrow">The picture</h3>
      <ul className="home-claims">
        <li>
          <span className="small">the plane on screen, as exported</span>
          <span className="meta">{preview.path}</span>
        </li>
        {preview.sha ? (
          <li>
            <span className="small">its hash</span>
            <span className="meta">{preview.sha}</span>
          </li>
        ) : null}
        <li>
          <span className="small">
            {preview.size[0]} × {preview.size[1]} px at {preview.pitch} µm/px
          </span>
          <span className="meta">Receipt-backed exported layer stack · CT texture levels</span>
        </li>
        <li>
          <span className="small">ground truth: {stack.ground_truth.state.toLowerCase()}</span>
          <span className="meta">{stack.ground_truth.why}</span>
        </li>
        <li>
          <span className="small">renderer</span>
          <span className="meta">{stack.target.renderer}</span>
        </li>
      </ul>
    </section>
  );
}

function OpensWhat({ chosen }: { chosen: { run: RunRecord | null; basis: string } }) {
  return (
    <section className="home-block">
      <h3 className="eyebrow">What the button opens</h3>
      <ul className="home-claims">
        <li>
          <span className="small">{chosen.basis}</span>
          <span className="meta">
            {chosen.run ? `${chosen.run.run_id} · ${chosen.run.target ?? "no target declared"}` : "the Workbench, with no run selected"}
          </span>
        </li>
      </ul>
    </section>
  );
}

function Elsewhere() {
  const rows: [string, string, string][] = [
    ["Every run on disk", "Explore → Run archive", "/explore?tab=archive"],
    ["What has been asked for, and each gate's decision", "Jobs", "/jobs"],
    ["What to bring in", "Sources → Ingest", "/sources?tab=ingest"],
    ["What we hold, and where it came from", "Sources", "/sources"],
    ["Which detector may be believed", "Sources → Model inventory", "/sources?tab=models"],
    ["Themed shelves", "Home → collections", "/"],
    ["The bench and its readiness", "Workbench", "/workbench"],
    ["Storage and machine capability", "System", "/system"],
    ["Reproduction and receipts", "Evidence", "/evidence"],
    ["A small board of reproducible evidence products", "ARGUS board", "/leaderboard"],
  ];
  return (
    <section className="home-block">
      <h3 className="eyebrow">Where everything else lives</h3>
      <ul className="home-claims">
        {rows.map(([what, where, to]) => (
          <li key={to}>
            <span className="small">{what}</span>
            <span className="meta">
              <Link to={to}>{where}</Link> · {to}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Fixtures({ n }: { n: number }) {
  return (
    <section className="home-block">
      <h3 className="eyebrow">Developer data</h3>
      <p className="small">
        {n === 0
          ? "No test fixture was hidden from this screen: the target index contained none."
          : `${n} test fixture${n === 1 ? "" : "s"} hidden from this screen.`}{" "}
        Fixtures are listed under <Link to="/system?tab=developer">System → Developer data</Link>.
      </p>
    </section>
  );
}

function Switcher({
  targets,
  current,
  onPick,
}: {
  targets: UnrollTarget[];
  current: UnrollTarget | null;
  onPick: (key: string) => void;
}) {
  if (targets.length < 2) return null;
  return (
    <section className="home-block">
      <h3 className="eyebrow">Other targets with an exported stack</h3>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {targets.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => onPick(t.key)}
            aria-pressed={t.key === current?.key}
            className="link-control interactive"
            style={{
              borderColor: t.key === current?.key ? "var(--accent)" : "var(--line-strong)",
            }}
          >
            {t.name}
            {t.has_ground_truth ? " (control)" : ""}
          </button>
        ))}
      </div>
    </section>
  );
}
