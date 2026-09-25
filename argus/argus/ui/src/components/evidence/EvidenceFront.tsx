import { UnavailableState } from "../UnavailableState";
import { Fragment, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, type RunRecord } from "../../api";
import { loadEvidence, thumbUrl, type EvidenceState as DiaryEvidenceState } from "../../lib/diaryEvidence";
import { usePoll } from "../../lib/poll";
import { usePublicDemo } from "../../lib/publicDemo";
import { scrollOfTarget, tierOf, TIER_WORD, type EvidenceTier } from "../../lib/unrollScroll";
import type { ReceiptEnvelope, ReceiptsState } from "../../lib/receipts";
import { useFindings, type FindingRow, type SourceState, type Stack } from "../grail/useGrailData";
import "../../theme/evidence.css";


function copy(text: string) {
  try {
    void navigator.clipboard?.writeText(text);
  } catch {
  }
}

const TIER_GLYPH: Record<EvidenceTier, string> = {
  CONTROL: "◎",
  DEVELOPMENT: "△",
  EXPLORATORY: "◇",
  ADMISSIBLE: "✓",
  UNCLASSIFIED: "?",
};

export function EvidenceTierChip({ tier }: { tier: EvidenceTier }) {
  return (
    <span className="ev-tier" data-tier={tier} data-evidence-tier={tier}>
      <span aria-hidden="true">{TIER_GLYPH[tier]}</span>
      {TIER_WORD[tier]}
    </span>
  );
}

export function EvidenceHash({
  value,
  label,
  control,
}: {
  value: string;
  label: string;
  control: string;
}) {
  const [done, setDone] = useState(false);
  return (
    <div className="ev-hash">
      <code className="ev-mono">{value}</code>
      <button
        type="button"
        className="ops-btn ev-copy"
        data-control={control}
        aria-label={`Copy ${label}`}
        onClick={() => {
          copy(value);
          setDone(true);
          window.setTimeout(() => setDone(false), 1500);
        }}
      >
        {done ? "Copied" : "Copy"}
      </button>
    </div>
  );
}


export function EvidencePager<T>({
  items,
  rowKey,
  render,
  filter,
  query,
  onQuery,
  page = 10,
  noun,
  control,
  placeholder,
  note,
  listClass = "ev-list",
  searching = false,
  itemsAreListItems = false,
}: {
  items: T[];
  rowKey: (t: T) => string;
  render: (t: T) => ReactNode;
  filter?: (t: T, q: string) => boolean;
  query?: string;
  onQuery?: (q: string) => void;
  page?: number;
  noun: [string, string];
  control: string;
  placeholder: string;
  note?: ReactNode;
  listClass?: string;
  searching?: boolean;
  itemsAreListItems?: boolean;
}) {
  const [localQ, setLocalQ] = useState("");
  const q = query ?? localQ;
  const setQ = onQuery ?? setLocalQ;
  const [start, setStart] = useState(0);

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return filter && needle ? items.filter((t) => filter(t, needle)) : items;
  }, [items, filter, q]);

  useEffect(() => {
    setStart(0);
  }, [q]);
  const first = shown.length === 0 ? 0 : Math.min(start, Math.max(0, shown.length - 1));
  const pageStart = first - (first % page);
  const visible = shown.slice(pageStart, pageStart + page);
  const last = pageStart + visible.length;
  const inputId = `${control.replace(/[^a-z0-9]+/gi, "-")}-search`;

  const status =
    items.length === 0
      ? q.trim()
        ? `No ${noun[0]} matches “${q.trim()}”.`
        : `No ${noun[1]}.`
      : shown.length === 0
        ? `No ${noun[0]} matches “${q.trim()}”; ${items.length} ${items.length === 1 ? noun[0] : noun[1]} in all.`
        : `Showing ${pageStart + 1}–${last} of ${shown.length} ${shown.length === 1 ? noun[0] : noun[1]}` +
          (shown.length !== items.length ? ` (filtered from ${items.length})` : "") +
          ".";

  const nav = (where: string) =>
    shown.length > page ? (
      <div className="ev-pager-nav" data-control={`${control}.pager.${where}`}>
        <button
          type="button"
          className="ops-btn"
          disabled={pageStart === 0}
          data-control={`${control}.prev.${where}`}
          onClick={() => setStart(Math.max(0, pageStart - page))}
        >
          Previous {page}
        </button>
        <button
          type="button"
          className="ops-btn"
          disabled={last >= shown.length}
          data-control={`${control}.next.${where}`}
          onClick={() => setStart(pageStart + page)}
        >
          Next {Math.min(page, shown.length - last)}
        </button>
      </div>
    ) : null;

  return (
    <div className="ev-pager" data-control={control}>
      <div className="ev-pager-bar">
        <label className="ev-search" htmlFor={inputId}>
          <span className="ev-visually-hidden">{placeholder}</span>
          <input
            id={inputId}
            type="search"
            value={q}
            placeholder={placeholder}
            onChange={(e) => setQ(e.target.value)}
            data-control={`${control}.search`}
          />
        </label>
        <p className="ev-pager-status" aria-live="polite" data-control={`${control}.status`}>
          {searching ? "Searching… " : ""}
          {status}
          {note ? <> {note}</> : null}
        </p>
        {nav("top")}
      </div>
      {visible.length ? (
        <ul className={listClass}>
          {visible.map((t) =>
            itemsAreListItems ? (
              <Fragment key={rowKey(t)}>{render(t)}</Fragment>
            ) : (
              <li key={rowKey(t)} className="ev-row-host">
                {render(t)}
              </li>
            ),
          )}
        </ul>
      ) : null}
      {visible.length > 3 ? nav("bottom") : null}
    </div>
  );
}


interface UnifiedItem {
  key: string;
  tier: EvidenceTier;
  name: string;
  scroll: string | null;
  url: string;
  sha256?: string;
  sourceKind: string;
  sources: string[];
  detail: string;
  claim: string;
  detector: boolean;
  privateLocal: boolean;
  path?: string;
}

interface DiscoveryPayload {
  assets: { asset_id: string; physical_scroll: string | null; identity_state: string; kind: string;
    status: string; name: string; sha256?: string; display_path: string; note?: string;
    viewable: boolean; preview_url: string | null }[];
  counts: { renders: number; viewable_renders: number; unresolved: number };
}

export function EvidenceGallery({ scroll, scrollLabel, stacks, runs = [] }: {
  scroll: string | null;
  scrollLabel: string | null;
  stacks: SourceState<Stack[]>;
  runs?: RunRecord[];
}) {
  const discovery = usePoll<DiscoveryPayload>("/api/discovery", { intervalMs: 120000, timeoutMs: 120000 });
  const [diary, setDiary] = useState<DiaryEvidenceState | null>(null);
  const [allProject, setAllProject] = useState(!scroll);
  const publicDemo = usePublicDemo();
  useEffect(() => {
    let live = true;
    void loadEvidence().then((value) => live && setDiary(value));
    return () => { live = false; };
  }, []);
  useEffect(() => setAllProject(!scroll), [scroll]);

  const inventory = useMemo<UnifiedItem[]>(() => {
    const rows: UnifiedItem[] = [];
    if (stacks.state === "OK") for (const st of stacks.data) {
      const tier = tierOf(st.detail.result_class);
      for (const [level, lv] of Object.entries(st.detail.levels ?? {})) {
        for (const [layer, img] of Object.entries(lv.images ?? {})) rows.push({
          key: `stack:${st.key}:${level}:${layer}`, tier, name: layer, scroll: st.scroll ?? null,
          url: api.fileUrl(img.path), path: img.path, sha256: img.sha256,
          sourceKind: "exported stack", sources: [`${st.name} · ${level} · ${lv.effective_pitch_um} µm`],
          detail: lv.readable, claim: st.detail.result_class?.banner ?? "Exported stack; being shown is not a pass.",
          detector: /ink|prob|pred|detector/i.test(layer), privateLocal: false,
        });
      }
    }
    for (const a of discovery.data?.assets ?? []) if (a.kind === "RENDER" && a.viewable && a.preview_url) rows.push({
      key: `discovery:${a.asset_id}`, tier: "UNCLASSIFIED", name: a.name, scroll: a.physical_scroll,
      url: a.preview_url, sha256: a.sha256, sourceKind: "local discovery", sources: [a.display_path],
      detail: `${a.status} · ${a.identity_state}`, claim: a.note ?? "Locally discovered; no scientific status inferred.",
      detector: /ink|prob|pred|detector/i.test(a.name), privateLocal: true,
    });
    for (const run of runs) {
      const runScroll = run.target ? scrollOfTarget({ name: run.target }) : null;
      for (const a of run.artifacts ?? []) if (a.path && !a.withheld && /\.(png|jpe?g|webp|gif)$/i.test(a.name)) rows.push({
        key: `run:${run.run_id}:${a.relpath}`, tier: "UNCLASSIFIED", name: a.name, scroll: runScroll,
        url: api.fileUrl(a.path), path: a.path, sha256: a.sha256, sourceKind: "run artifact", sources: [run.run_id],
        detail: run.operational_state, claim: "Run artifact; operational completion is not scientific admissibility.",
        detector: /ink|prob|pred|detector/i.test(a.name), privateLocal: true,
      });
    }
    if (diary?.serving) for (const d of diary.manifest?.rows ?? []) rows.push({
      key: `diary:${d.sha256}`, tier: "CONTROL", name: d.path.split(/[\\/]/).pop() ?? "interface evidence",
      scroll: null, url: thumbUrl(d), path: d.path, sha256: d.sha256, sourceKind: "interface verification",
      sources: [d.dir_why ?? "browser evidence manifest"], detail: `${d.pixels.w}×${d.pixels.h}`,
      claim: "Interface-verification evidence, not scientific evidence about a scroll.", detector: false, privateLocal: true,
    });
    const merged = new Map<string, UnifiedItem>();
    for (const row of rows) {
      const k = row.sha256 ? `sha:${row.sha256}` : `url:${row.url}`;
      const old = merged.get(k);
      if (!old) merged.set(k, { ...row, key: k });
      else old.sources = [...new Set([...old.sources, old.sourceKind, row.sourceKind, ...row.sources])];
    }
    return [...merged.values()].sort((a, b) => `${a.scroll ?? "~"}:${a.name}`.localeCompare(`${b.scroll ?? "~"}:${b.name}`));
  }, [diary, discovery.data, runs, stacks]);

  const available = inventory.filter((x) => !publicDemo || !x.privateLocal);
  const tiles = available.filter((x) => allProject || !scroll || x.scroll === scroll);
  const failures = [
    stacks.state === "FAILED" ? `exported stacks: ${stacks.why}` : null,
    discovery.failure ? `discovery: ${discovery.failure.message}` : null,
  ].filter(Boolean) as string[];
  const loading = stacks.state === "LOADING" || discovery.loading || diary === null;
  const who = scrollLabel ?? scroll;

  return <section id="evidence.gallery" className="ops-sec ev-sec" data-control="evidence.gallery" data-state={loading ? "LOADING" : tiles.length ? "OK" : "NONE"} aria-labelledby="evidence-gallery-title">
    <div className="ev-sec-head">
      <h2 id="evidence-gallery-title">Gallery</h2>
      <p className="ev-muted">{allProject || !scroll
        ? "Every unique image ARGUS can safely serve from stacks, discovery, runs and interface evidence."
        : `Only images explicitly bound to ${who}; none borrowed from another scroll.`} Duplicate bytes collapse by SHA while every provenance route stays visible. Being shown is not a pass.</p>
    </div>
    <div className="ops-controls ev-gallery-scope" data-control="evidence.gallery.scope">
      {scroll ? <button type="button" className="ops-btn" aria-pressed={!allProject} onClick={() => setAllProject(false)}>This scroll ({available.filter((x) => x.scroll === scroll).length})</button> : null}
      <button type="button" className="ops-btn" aria-pressed={allProject || !scroll} onClick={() => setAllProject(true)}>All project evidence ({available.length})</button>
      <span className="ev-muted">{inventory.length} unique · {discovery.data?.counts.viewable_renders ?? 0} discovery-viewable · {discovery.data?.counts.unresolved ?? 0} unresolved assets not shown</span>
    </div>
    {failures.length ? (
      <UnavailableState
        control="evidence.gallery.partial"
        what={`Some image sources could not be read (${failures.length}).`}
        stillAvailable="every image that did load is shown below."
        consequence="Missing sources are unread, not empty."
        technical={<>{failures.join("; ")}</>}
      />
    ) : null}
    {loading ? <p className="ev-muted">Reading every safe evidence source…</p> : tiles.length === 0 ?
      <p className="ev-none" data-control="evidence.gallery.none">{scroll && !allProject
        ? `No safely viewable image is bound to ${who}. ${available.length} unique images exist elsewhere; none is substituted.`
        : "No safely viewable image was returned by any source."}</p> :
      <EvidencePager items={tiles} rowKey={(x) => x.key}
        filter={(x, q) => `${x.name} ${x.sourceKind} ${x.sources.join(" ")} ${x.scroll ?? ""} ${TIER_WORD[x.tier]}`.toLowerCase().includes(q)}
        page={12} noun={["image", "images"]} control="evidence.gallery.list"
        placeholder="Search images by name, scroll, source or class" listClass="ev-tiles"
        render={(x) => <UnifiedGalleryTile item={x} />} />}
  </section>;
}

function UnifiedGalleryTile({ item }: { item: UnifiedItem }) {
  const ctl = `evidence.gallery.${item.key.replace(/[^a-z0-9_.-]+/gi, "-")}`;
  return <article className="ev-tile" data-tier={item.tier} data-evidence-gallery-tile={item.key}>
    <a href={item.url} target="_blank" rel="noreferrer" className="ev-tile-img" data-control={`${ctl}.open`} aria-label={`Open ${item.name} at full size`}>
      <img src={item.url} alt={`${item.name}, ${TIER_WORD[item.tier]}. ${item.detail}.`} loading="lazy" decoding="async" />
    </a>
    <div className="ev-tile-meta">
      <div className="ev-tile-row"><EvidenceTierChip tier={item.tier} /><span className="ev-tile-layer">{item.name}</span></div>
      <p className="ev-tile-src">{item.scroll ?? "project-wide"} · {item.sourceKind} · {item.detail}</p>
      {item.detector ? <p className="ev-tile-warn">Detector-like output. The gallery does not promote it; this is not a reading.</p> : null}
      <details className="ev-details" data-control={`${ctl}.provenance`}>
        <summary>Claim boundary and provenance</summary>
        <div className="ev-details-body"><p className="ev-banner">{item.claim}</p><dl className="ev-kv">
          <dt>Sources</dt><dd>{item.sources.join(" · ")}</dd><dt>Class</dt><dd>{TIER_WORD[item.tier]}</dd>
          {item.path ? <><dt>Image path</dt><dd><EvidenceHash value={item.path} label={`path of ${item.name}`} control={`${ctl}.copy.path`} /></dd></> : null}
          <dt>sha256</dt><dd>{item.sha256 ? <EvidenceHash value={item.sha256} label={`sha256 of ${item.name}`} control={`${ctl}.copy.sha`} /> : "not recorded"}</dd>
        </dl></div>
      </details>
    </div>
  </article>;
}

interface Tile {
  st: Stack;
  tier: EvidenceTier;
  level: string;
  pitch: number;
  readable: string;
  layer: string;
  path: string;
  sha256?: string;
}

export function LegacyStackGallery({
  scroll,
  scrollLabel,
  stacks,
}: {
  scroll: string | null;
  scrollLabel: string | null;
  stacks: SourceState<Stack[]>;
}) {
  const tiles = useMemo<Tile[]>(() => {
    if (stacks.state !== "OK") return [];
    return stacks.data
      .filter((st) => (scroll ? st.scroll === scroll : true))
      .flatMap((st) => {
        const tier = tierOf(st.detail.result_class);
        return Object.entries(st.detail.levels ?? {}).flatMap(([level, lv]) =>
          Object.entries(lv.images ?? {}).map(([layer, img]) => ({
            st,
            tier,
            level,
            pitch: lv.effective_pitch_um,
            readable: lv.readable,
            layer,
            path: img.path,
            sha256: img.sha256,
          })),
        );
      });
  }, [stacks, scroll]);

  const who = scrollLabel ?? scroll;
  let state: "LOADING" | "FAILED" | "NONE" | "OK" = "OK";
  let body: ReactNode;
  if (stacks.state === "LOADING") {
    state = "LOADING";
    body = <p className="ev-muted">Reading the exported layer stacks…</p>;
  } else if (stacks.state === "FAILED") {
    state = "FAILED";
    body = (
      <UnavailableState
        control="evidence.gallery.failed"
        what="The exported image stacks could not be read right now."
        stillAvailable="receipts, findings and packets further down this page."
        consequence="This is the gallery failing to read, not a statement that there are no images."
        repair="try again shortly; if it persists, check System → Service health."
        technical={<>layer stacks: {stacks.why}</>}
      />
    );
  } else if (tiles.length === 0) {
    state = "NONE";
    body = (
      <p className="ev-none" data-control="evidence.gallery.none">
        {scroll
          ? `No receipt-backed image exists for ${who}. ${stacks.data.length} exported layer ${stacks.data.length === 1 ? "stack was" : "stacks were"} read and none belongs to this scroll; no image from another scroll is shown in its place.`
          : `No receipt-backed image exists. ${stacks.data.length} exported layer ${stacks.data.length === 1 ? "stack was" : "stacks were"} read and none carries an image.`}
      </p>
    );
  } else {
    body = (
      <EvidencePager
        items={tiles}
        rowKey={(t) => `${t.st.key}:${t.level}:${t.layer}`}
        filter={(t, q) =>
          `${t.layer} ${t.level} ${t.st.name} ${TIER_WORD[t.tier]} ${t.st.scroll ?? ""}`
            .toLowerCase()
            .includes(q)
        }
        page={8}
        noun={["image", "images"]}
        control="evidence.gallery.list"
        placeholder="Search images by layer, level, stack or class"
        listClass="ev-tiles"
        render={(t) => <GalleryTile t={t} />}
      />
    );
  }

  return (
    <section
      className="ops-sec ev-sec"
      data-control="evidence.gallery"
      data-state={state}
      aria-labelledby="evidence-gallery-title"
    >
      <div className="ev-sec-head">
        <h2 id="evidence-gallery-title">Gallery</h2>
        <p className="ev-muted">
          {scroll
            ? `Only images from ${who}'s own layer stacks, none borrowed from another scroll.`
            : "Every receipt-backed image, from every exported layer stack."}{" "}
          Each carries its result class; being shown is not a pass.
        </p>
      </div>
      {body}
    </section>
  );
}

function GalleryTile({ t }: { t: Tile }) {
  const rc = t.st.detail.result_class ?? {};
  const isDetector = /ink|prob|pred|detector/i.test(t.layer);
  const url = api.fileUrl(t.path);
  const ctl = `evidence.gallery.${t.st.key}.${t.level}.${t.layer}`;
  return (
    <article className="ev-tile" data-tier={t.tier} data-evidence-gallery-tile={t.path}>
      <a
        href={url}
        target="_blank"
        rel="noreferrer"
        className="ev-tile-img"
        data-control={`${ctl}.open`}
        aria-label={`Open ${t.layer} from ${t.st.name} at full size`}
      >
        <img
          src={url}
          alt={`${t.st.name}: ${t.layer} at ${t.level}, ${TIER_WORD[t.tier]} result class. ${t.readable}.`}
          loading="lazy"
          decoding="async"
        />
      </a>
      <div className="ev-tile-meta">
        <div className="ev-tile-row">
          <EvidenceTierChip tier={t.tier} />
          <span className="ev-tile-layer">{t.layer}</span>
        </div>
        <p className="ev-tile-src">
          {t.st.scroll ?? "no Herculaneum scroll"} · {t.level} · {t.pitch} µm
        </p>
        {isDetector ? (
          <p className="ev-tile-warn" data-control={`${ctl}.detector`}>
            Detector output.
            {rc.detector_cross_scroll_qualified
              ? " The stack declares its detector cross-scroll qualified; the result class above still decides what may be claimed."
              : " The detector is not cross-scroll qualified, so this is not a reading."}
          </p>
        ) : null}
        <details className="ev-details" data-control={`${ctl}.provenance`}>
          <summary data-control={`${ctl}.provenance.toggle`}>Result class and provenance</summary>
          <div className="ev-details-body">
            <p className="ev-banner">
              {rc.banner ?? "No result-class banner was exported with this stack."}
            </p>
            <dl className="ev-kv">
              <dt>Stack</dt>
              <dd>
                {t.st.name} <span className="ev-mono">({t.st.key})</span>
              </dd>
              <dt>Class</dt>
              <dd>
                {rc.target_class ?? "not declared"} · {rc.presentation ?? "not declared"}
              </dd>
              <dt>Detector</dt>
              <dd>{rc.detector ?? "not declared"}</dd>
              <dt>Receipt route</dt>
              <dd>Hash-bound exported layer-stack record</dd>
              <dt>Image path</dt>
              <dd>
                <EvidenceHash value={t.path} label={`path of ${t.layer}`} control={`${ctl}.copy.path`} />
              </dd>
              <dt>sha256</dt>
              <dd>
                {t.sha256 ? (
                  <EvidenceHash value={t.sha256} label={`sha256 of ${t.layer}`} control={`${ctl}.copy.sha`} />
                ) : (
                  "not recorded in the exported stack"
                )}
              </dd>
            </dl>
          </div>
        </details>
      </div>
    </article>
  );
}


function useDebounced(v: string, ms = 300): string {
  const [d, setD] = useState(v);
  useEffect(() => {
    const t = window.setTimeout(() => setD(v), ms);
    return () => window.clearTimeout(t);
  }, [v, ms]);
  return d;
}

export function EvidenceTimeline({
  scroll,
  scrollLabel,
  aliases,
  rec,
}: {
  scroll: string | null;
  scrollLabel: string | null;
  aliases: string[];
  rec: ReceiptsState;
}) {
  const [q, setQ] = useState("");
  const dq = useDebounced(q);
  const findings = useFindings(scroll, aliases, dq, null);
  const who = scrollLabel ?? scroll;

  return (
    <section
      className="ops-sec ev-sec"
      data-control="evidence.timeline"
      aria-labelledby="evidence-timeline-title"
    >
      <div className="ev-sec-head">
        <h2 id="evidence-timeline-title">Timeline</h2>
        <p className="ev-muted">
          {scroll
            ? `Findings that mention ${who}, newest record first.`
            : "Receipts by the time they were written, then findings by record number. Two clocks, kept apart."}
        </p>
      </div>

      {scroll ? (
        <p className="ev-muted" data-control="evidence.timeline.receipts.unscoped">
          No receipt envelope names a scroll, so none is filed under {who} here. Every declared
          receipt is listed under Declared receipts below.
        </p>
      ) : (
        <ReceiptWrites rec={rec} />
      )}

      <div className="ev-subsec" data-control="evidence.timeline.findings">
        <h3>{scroll ? `Findings mentioning ${who}` : "Findings"}</h3>
        {findings.state === "LOADING" && !dq ? (
          <p className="ev-muted">Reading /api/findings…</p>
        ) : findings.state === "FAILED" ? (
          <UnavailableState
            control="evidence.timeline.findings.failed"
            what="The findings could not be read right now."
            stillAvailable="the receipts above and the declared receipts below."
            consequence={<>This is the timeline failing to read, not a statement that no finding mentions {who ?? "anything"}.</>}
            repair="try again shortly; if it persists, check System → Service health."
            technical={<>GET /api/findings: {findings.why}</>}
          />
        ) : findings.state === "OK" && !findings.data.present ? (
          <UnavailableState
            control="evidence.timeline.findings.unavailable"
            what="No findings corpus is installed in this ARGUS home."
            stillAvailable="the receipts above, the declared receipts below and every render on this machine."
            consequence={<>Findings are unknown here, not empty: nothing is being said about what has or has not been found{who ? ` for ${who}` : ""}.</>}
            repair="findings appear once a findings corpus is present in this ARGUS home; nothing needs to be done to keep working."
            technical={<>GET /api/findings → present=false: {findings.data.why ?? "the route gave no reason"}</>}
          />
        ) : (
          <EvidencePager<FindingRow>
            items={findings.state === "OK" ? (findings.data.rows ?? []) : []}
            rowKey={(r) => r.id}
            query={q}
            onQuery={setQ}
            searching={findings.state === "LOADING"}
            page={8}
            noun={["finding", "findings"]}
            control="evidence.timeline.findings.list"
            placeholder={scroll ? `Search findings that mention ${who}` : "Search every finding"}
            note={
              findings.state === "OK" ? (
                <>
                  The route returns the newest {findings.data.rows?.length ?? 0} of{" "}
                  {findings.data.total_matching_kinds ?? "an undeclared number of"} matching
                  {dq.trim() ? ` “${dq.trim()}”` : ""}; search to reach older records.
                  {findings.data.relation_means ? ` ${findings.data.relation_means}` : ""}
                </>
              ) : null
            }
            render={(r) => <FindingLine r={r} />}
          />
        )}
      </div>
    </section>
  );
}

function FindingLine({ r }: { r: FindingRow }) {
  return (
    <div className="ev-line" data-control={`evidence.timeline.finding.${r.id}`}>
      <div className="ev-line-head">
        <span className="ev-mono ev-line-when">{r.id}</span>
        <span className="ev-kind" data-kind={r.kind.toLowerCase()}>
          {r.kind.toLowerCase()}
        </span>
        <span className="ev-line-title">{r.title}</span>
      </div>
      <details className="ev-details">
        <summary data-control={`evidence.timeline.finding.${r.id}.toggle`}>Excerpt and relations</summary>
        <div className="ev-details-body">
          <p className="ev-excerpt">{r.excerpt}</p>
          {r.retracts.length || r.amends.length ? (
            <p className="ev-muted">
              {r.retracts.length ? `Retracts ${r.retracts.join(", ")}. ` : ""}
              {r.amends.length ? `Amends ${r.amends.join(", ")}.` : ""}
            </p>
          ) : null}
          <EvidenceHash value={r.id} label={`finding id ${r.id}`} control={`evidence.timeline.finding.${r.id}.copy`} />
        </div>
      </details>
    </div>
  );
}

function ReceiptWrites({ rec }: { rec: ReceiptsState }) {
  const rows = useMemo(() => {
    const map = rec.index?.receipts ?? {};
    return Object.values(map)
      .filter((e): e is ReceiptEnvelope => Boolean(e))
      .sort((a, b) => (b.mtime_utc ?? "").localeCompare(a.mtime_utc ?? ""));
  }, [rec.index]);

  return (
    <div className="ev-subsec" data-control="evidence.timeline.receipts">
      <h3>Receipts written</h3>
      {rec.failure ? (
        <UnavailableState
          control="evidence.timeline.receipts.failed"
          what="The receipt timeline could not be read right now."
          stillAvailable="the image gallery above and the findings below."
          consequence="This is the timeline failing to read, not a statement that no receipt was written."
          repair="try again shortly; if it persists, check System → Service health."
          technical={<>GET /api/receipts: {rec.failure.status ?? "transport failure"} — {rec.failure.detail}</>}
        />
      ) : !rec.settled ? (
        <p className="ev-muted">Reading /api/receipts…</p>
      ) : (
        <EvidencePager<ReceiptEnvelope>
          items={rows}
          rowKey={(e) => e.key}
          filter={(e, q) => `${e.key} ${e.produced_by ?? ""} ${e.relpath}`.toLowerCase().includes(q)}
          page={6}
          noun={["receipt", "receipts"]}
          control="evidence.timeline.receipts.list"
          placeholder="Search receipts by name, generator or path"
          note="Newest write first; a receipt with no recorded write time sorts last."
          render={(e) => (
            <div className="ev-line" data-control={`evidence.timeline.receipt.${e.key}`}>
              <div className="ev-line-head">
                <span className="ev-mono ev-line-when">
                  {e.mtime_utc ?? (e.present ? "no write time recorded" : "never written")}
                </span>
                <span className="ev-line-title">{e.key}</span>
                <span className="ev-muted">
                  {e.sealed ? "sealed" : e.present ? "on disk" : "absent"}
                  {e.produced_by ? ` · ${e.produced_by}` : ""}
                </span>
              </div>
            </div>
          )}
        />
      )}
    </div>
  );
}
