import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { usePoll } from "../lib/poll";
import { getJson, type Failure } from "../lib/http";
import { useArgusContext, type ArgusMode } from "../lib/context";
import { fetchReceipts, type ReceiptIndex, type ReceiptsState } from "../lib/receipts";
import { approvedHash, isRefusal, openSession, planGoverned, runGoverned, sessionIsOpen } from "../lib/governed";
import { Ingest } from "./Ingest";
import { Models } from "./Models";
import { SrcGroup, SrcPager, usePage } from "../components/SourcesKit";
import { ExternalEvidencePanel } from "../components/ExternalEvidencePanel";
import { SegmentImportPanel } from "../components/SegmentImportPanel";
import { ScienceAttachPanel } from "../components/ScienceAttachPanel";
import { ScrollStatusBar, useScrollStatus } from "../components/ScrollStatusBar";
import "../theme/ops.css";
import "../theme/sources.css";
import {
  OpsBadge,
  OpsDetails,
  OpsDisabled,
  OpsFact,
  OpsFacts,
  OpsFailure,
  OpsItem,
  OpsPage,
  OpsQuote,
  OpsSection,
  OpsSource,
  OpsTable,
  OpsUnknown,
  agoLabel,
  bytesLabel,
  type OpsTone,
} from "../components/OpsKit";


interface SourceRow {
  id: string;
  name: string;
  kind: string;
  url: string;
  authoritative_for: string;
  notes?: string;
  caution?: string | null;
  acquisition_policy?: string | null;
  assets?: {
    scroll: string;
    pass: number;
    url: string;
    state: string;
    role: string;
  }[];
  local: {
    any_present: boolean;
    paths: { path: string; present: boolean; entries?: number; error?: string }[];
  };
  probe_result: {
    checked: boolean;
    reachable?: boolean;
    status?: number;
    ms?: number;
    error?: string;
    note?: string;
    why?: string;
  };
}

interface SourcesPayload {
  schema: string;
  sources: SourceRow[];
  note?: string;
}

interface TargetRow {
  scroll: string;
  scan_id: string;
  prizes: string[];
  volume_store: string | null;
  store_state: "FOUND" | "NOT_FOUND" | string;
  pitch_um: number | null;
  energy_kev: number | null;
  v1_scan_id: string | null;
  v1_scan_id_still_listed: boolean | null;
  operator_fence: string | null;
}

interface TargetsPayload {
  schema: string;
  id: string;
  available?: boolean;
  sets: Record<string, { count?: number; scrolls?: string[]; rule?: string } | string>;
  targets: TargetRow[];
  acquisition_families: Record<string, string[]>;
  operator_fences: Record<string, string>;
  v1_ids_that_no_longer_resolve: string[];
  why_v2: string;
  what_this_does_not_change?: string[];
  source?: { url: string; sha256: string; bytes: number };
  next_action?: { label?: string; where?: string; effect?: string };
}

interface ScrollRow {
  scroll: string;
  display: string;
  physical_segments: number;
  label_representations: number;
  labelled: number;
  native9: number;
  aligned: number;
  results_complete: number;
  results_partial: number;
  cached_ct_regions: number;
  attested_stores?: number;
  blocked_by: string[];
}

interface ScrollsPayload {
  scrolls: ScrollRow[];
  totals: {
    physical_scrolls: number;
    physical_segments: number;
    label_representations: number;
    segments_with_labels: number;
  };
  nothing_moved?: string;
}

interface ScrollIdsPayload {
  canonical: string[];
  confusable_pairs: [string, string][];
}

interface SurfacesPayload {
  surfaces: {
    public_release?: {
      state: string;
      headline: string;
      undeclared_licences?: string[];
      by_class?: Record<string, number>;
      publishable?: number;
      tracked_files?: number;
    };
    updates?: {
      state: string;
      headline: string;
      checked_utc?: string;
      sources?: { key: string; kind: string; state: string; stable?: string; candidate?: string; behind?: string | null }[];
      drifted?: string[];
      nothing_was_promoted?: string;
    };
  };
}

interface IngestPlanPayload {
  schema: string;
  holdings: {
    kind: string;
    id: string;
    bytes: number;
    planes?: number;
    planes_expected?: number;
    complete: boolean;
    why_incomplete?: string | null;
    chunks_by_level?: Record<string, number>;
  }[];
  storage: {
    free_bytes: number;
    total_bytes: number;
    floor_bytes: number;
    headroom_bytes: number;
    headroom_note: string;
  };
  staged_bytes_total: number;
}

interface RetrievalPlanPayload {
  schema: string;
  state: string;
  read_only: boolean;
  scroll?: string | null;
  requests_made: number;
  bytes_fetched: number;
  destination?: string;
  why?: string;
  next?: string;
  required?: string[];
  plan?: {
    phase?: string;
    phase_means?: string;
    store_id?: string;
    planned_objects?: number;
    planned_upper_bound_bytes?: number;
    acquisition_id?: string;
  };
  plan_sha256?: string;
  execution?: string;
}

export interface RetrievalPrefill {
  scroll: string;
  url: string;
  volumeId: string;
  authorityJson: string;
  declaredJson: string;
}

export function retrievalPrefill(
  selectedScroll: string | null,
  targets: TargetsPayload | null,
  sources: SourcesPayload | null,
): RetrievalPrefill | null {
  const scroll = selectedScroll?.trim() ?? "";
  if (!scroll || targets?.available === false) return null;
  const target = targets?.targets.find((row) => row.scroll === scroll);
  const source = sources?.sources.find((row) => row.id === "open-data-s3" && row.url.trim());
  if (!target?.volume_store || !source) return null;
  const base = source.url.endsWith("/") ? source.url : `${source.url}/`;
  const metadata = {
    physical_scroll: scroll,
    scan_id: target.scan_id,
    volume_store: target.volume_store,
    pitch_um: target.pitch_um,
    energy_kev: target.energy_kev,
  };
  return {
    scroll,
    url: `${base}${encodeURIComponent(scroll)}/volumes/${target.volume_store}`,
    volumeId: target.scan_id,
    authorityJson: JSON.stringify({ source_id: source.id, ...metadata }, null, 2),
    declaredJson: JSON.stringify(metadata, null, 2),
  };
}

interface ProfilePlanPayload {
  schema: string;
  state: string;
  read_only: boolean;
  scroll: string;
  segment?: string | null;
  receipt?: string | null;
  required?: string[];
  profile_fingerprint?: string;
  matching?: { verdict?: string; recipe_id?: string; why?: string; note?: string } | null;
  why?: string;
  next?: string;
}

interface DiscoveryAsset {
  asset_id: string;
  physical_scroll: string | null;
  identity_state: "RESOLVED" | "AMBIGUOUS" | "UNRESOLVED" | string;
  identity_candidates: string[];
  unresolved_tokens: string[];
  kind: "RENDER" | "SCIENCE_RECORD" | string;
  status: string;
  name: string;
  bytes: number;
  sha256: string | null;
  display_path: string;
  source: string | null;
  note: string | null;
  panels?: string;
  shape?: number[];
  viewable?: boolean;
  preview_url?: string | null;
}

interface DiscoveryPayload {
  schema: string;
  read_only: boolean;
  scanned_utc: string;
  roots: { label: string; display_path: string; files_considered: number; truncated: boolean }[];
  skipped_roots: { label: string; reason: string }[];
  assets: DiscoveryAsset[];
  counts: { assets: number; renders: number; verified_renders: number; viewable_renders?: number; resolved_scrolls: number; unresolved: number };
  next?: string;
}

interface StoragePayload {
  ok: boolean;
  catalog_present: boolean;
  assets: number;
  by_state: Record<string, { assets: number; gib: number }>;
  largest: {
    asset_id: string;
    name: string | null;
    project: string | null;
    state: string | null;
    gib: number;
    local: string | null;
    remote: string | null;
  }[];
  mutations_are_not_here?: string;
}

interface Triple<T> {
  value: T | null;
  source?: string;
  evidence?: string;
  reason?: string;
}

interface DatasetScroll {
  scroll: string;
  eligibility?: Triple<{ first_letters_2027?: boolean; grand_prize_2027?: boolean }>;
  acquisition?: Triple<{ voxel_um?: number; energy_kev?: number }>;
  best_resolution_um?: Triple<number>;
  local_state?: Triple<{
    present?: boolean;
    bytes?: number;
    files?: number;
    catalog_records?: number;
    asset_kinds?: Record<string, number>;
  }>;
  cache_state?: Triple<{
    chunk_cache_dirs?: number;
    chunk_cache_bytes?: number;
    chunk_files?: number;
  }>;
  remote_state?: Triple<string>;
  etag?: Triple<string>;
  hashes?: Triple<string>;
  labels?: Triple<{ held_locally?: boolean; where?: string }>;
  volumes?: Triple<
    { name: string; id?: string; voxel_um?: number; dense_level0_bytes?: number; url?: string }[]
  >;
  surfaces?: Triple<
    { run: string; pyramid_level?: number | null; effective_um?: number | null; of_volume_id?: string }[]
  >;
  predictions?: Triple<Record<string, string[]>>;
  segments?: {
    observations?: { date?: string; method?: string; count?: number; path?: string; note?: string }[];
    note?: string;
  };
}

interface DatasetRegistry {
  schema: string;
  utc: string;
  scroll_count: number;
  eligible_count: number;
  scrolls: Record<string, DatasetScroll>;
  totals?: Record<string, number>;
  chunk_cache_hold?: Triple<{
    bytes?: number;
    files?: number;
    status?: string;
    attributed_fraction?: number;
    unique_information?: string;
  }>;
  sources?: Record<string, string>;
}

interface CatalogueAsset {
  local_path: string;
  asset_kind: string;
  files: number | null;
  bytes: number | null;
  size_truncated?: boolean;
  segment_id?: Triple<string>;
  local_state?: string;
  remote_state?: Triple<unknown>;
  hashes?: Triple<unknown>;
  grouping_key: string;
  grouping_key_is_verified: boolean;
}

interface AssetCatalogue {
  schema: string;
  utc: string;
  asset_count: number;
  assets: CatalogueAsset[];
  pairability?: Record<
    string,
    { per_segment?: Record<string, { projection_state?: string }> }
  >;
  schema_for_consumers?: { asset_kinds?: string[]; grouping?: string };
}


const TABS = [
  { id: "holdings", label: "What we hold", hint: "by scroll, then by source family" },
  { id: "discovered", label: "On this computer", hint: "local scroll material ARGUS has found" },
  { id: "targets", label: "Eligible targets", hint: "the two prize lists, never merged" },
  { id: "upstream", label: "Upstream", hint: "hosts, reachability, licence" },
  { id: "ingest", label: "Get or add data", hint: "download an exact CT or attach material already on this computer" },
  { id: "models", label: "Model inventory", hint: "which detector may be believed" },
] as const;

type TabId = (typeof TABS)[number]["id"];

export function Sources() {
  const [params, setParams] = useSearchParams();
  const asked = params.get("tab");
  const selectedScroll = params.get("scroll");
  const tab: TabId = (TABS.some((t) => t.id === asked) ? asked : "holdings") as TabId;

  const sources = usePoll<SourcesPayload>("/api/sources?live=true", { intervalMs: 120000 });
  const targets = usePoll<TargetsPayload>("/api/targets", { intervalMs: 300000 });
  const scrolls = usePoll<ScrollsPayload>("/api/scrolls", { intervalMs: 120000 });
  const ids = usePoll<ScrollIdsPayload>("/api/scroll-ids", { intervalMs: 300000 });
  const surfaces = usePoll<SurfacesPayload>("/api/surfaces", { intervalMs: 300000 });
  const plan = usePoll<IngestPlanPayload>("/api/ingest/plan", { intervalMs: 300000 });
  const discovery = usePoll<DiscoveryPayload>("/api/discovery", { intervalMs: 120000, timeoutMs: 20000 });

  const [rec, setRec] = useState<ReceiptsState>({ index: null, failure: null, settled: false });
  useEffect(() => {
    let live = true;
    fetchReceipts().then((s) => live && setRec(s));
    return () => {
      live = false;
    };
  }, []);

  const ds = useMemo(() => datasetRegistry(rec.index), [rec.index]);
  const fl = useMemo(() => firstLetters(targets.data, scrolls.data, ds), [targets.data, scrolls.data, ds]);

  return (
    <OpsPage
      control="sources"
      title="Sources"
      lede={
        <>
          See what is available, where it lives, what is already on this machine, and the
          governed path that downloads an exact volume into ARGUS only after approval.
        </>
      }
    >
      <BlockerHeadline fl={fl} surfaces={surfaces.data} />

      <nav className="ops-subtabs src-tabs" aria-label="Sources sections">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            className="ops-subtab"
            aria-current={t.id === tab ? "true" : undefined}
            aria-pressed={t.id === tab}
            title={t.hint}
            data-control={`sources.tab.${t.id}`}
            onClick={() => {
              const next = new URLSearchParams(params);
              next.set("tab", t.id);
              setParams(next, { replace: true });
            }}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {tab === "holdings" ? (
        <Holdings scrolls={scrolls} ids={ids} ds={ds} rec={rec} plan={plan.data} />
      ) : tab === "discovered" ? (
        <DiscoveredOnComputer payload={discovery.data} failure={discovery.failure} selectedScroll={selectedScroll} />
      ) : tab === "targets" ? (
        <Targets targets={targets} fl={fl} ds={ds} />
      ) : tab === "upstream" ? (
        <Upstream sources={sources} surfaces={surfaces.data} ds={ds} />
      ) : tab === "ingest" ? (
        <IngestTab selectedScroll={selectedScroll} targets={targets.data} sources={sources.data} plan={plan.data} />
      ) : (
        <ModelsTab />
      )}
    </OpsPage>
  );
}

function DiscoveredOnComputer({
  payload,
  failure,
  selectedScroll,
}: {
  payload: DiscoveryPayload | null;
  failure: Failure | null;
  selectedScroll: string | null;
}) {
  if (!payload) {
    return (
      <OpsSection control="sources.discovered" title="On this computer" hint="ARGUS is checking bounded local roots for scroll-related material.">
        <OpsUnknown what="local discovery" why={failure?.message ?? "the first bounded scan has not answered yet"} />
      </OpsSection>
    );
  }
  const byScroll = new Map<string, DiscoveryAsset[]>();
  for (const asset of payload.assets.filter((item) => !selectedScroll || item.physical_scroll === selectedScroll)) {
    const key = asset.physical_scroll ?? "UNRESOLVED_IDENTITY";
    const list = byScroll.get(key) ?? [];
    list.push(asset);
    byScroll.set(key, list);
  }
  const groups = [...byScroll.entries()].sort(([a], [b]) => a.localeCompare(b));
  return (
    <OpsSection
      control="sources.discovered"
      title={selectedScroll ? `On this computer: ${selectedScroll}` : "On this computer"}
      hint="A read-only bounded scan finds scroll-related manifests, receipts and renders outside the formal catalog. It moves nothing and does not infer ink."
    >
      <div className="src-tiles" data-control="sources.discovered.summary">
        <Tile control="sources.discovered.assets" value={String(payload.counts.assets)} label="discovered records" tone="info" word="local" />
        <Tile control="sources.discovered.renders" value={`${payload.counts.viewable_renders ?? payload.counts.verified_renders} / ${payload.counts.renders}`} label="viewable renders" tone={(payload.counts.viewable_renders ?? payload.counts.verified_renders) ? "ok" : "warn"} word={(payload.counts.viewable_renders ?? payload.counts.verified_renders) ? "openable" : "not mounted"} />
        <Tile control="sources.discovered.scrolls" value={String(payload.counts.resolved_scrolls)} label="resolved scrolls" tone="info" word="identity-bound" />
      </div>
      <div className="ops-note" data-control="sources.discovered.explanation">
        Last scan {payload.scanned_utc}. {selectedScroll ? `Showing identity-matched material for ${selectedScroll}. ` : ""}ARGUS checks declared roots and sibling worktrees, skips raw arrays and model blobs, and never silently files one scroll's asset under another. Hash-bound private renders outside a declared serving root remain listed but not viewable until they are explicitly mounted through the science-attach path.
      </div>
      <div className="ops-note" data-control="sources.discovered.roots">
        <strong>Where ARGUS looked</strong>
        <ul className="ops-list">
          {payload.roots.map((root) => (
            <li key={root.label}>
              <code>{root.display_path}</code> · {root.files_considered} matching files considered
              {root.truncated ? " · bounded scan stopped at its safety limit" : " · complete within the bounded rules"}
            </li>
          ))}
          {payload.skipped_roots.map((root) => (
            <li key={root.label}><code>{root.label}</code> · not scanned: {root.reason}</li>
          ))}
        </ul>
        Personal or scratch science stays removable: add it with <code>ARGUS_DISCOVERY_EXTRA_ROOTS</code>.
        ARGUS indexes it read-only and never folds those bytes into the public release.
      </div>
      {groups.length === 0 ? <OpsUnknown what="scroll-related local material" why="the bounded roots contain no matching records" /> : null}
      {groups.map(([scroll, assets]) => (
        <section className="ops-section" key={scroll} data-control={`sources.discovered.scroll.${scroll}`}>
          <div className="ops-section-head">
            <div>
              <h3>{scroll === "UNRESOLVED_IDENTITY" ? "Identity needs review" : scroll}</h3>
              <p>{assets.length} discovered {assets.length === 1 ? "record" : "records"}; these are local holdings, not scientific qualification.</p>
            </div>
            {scroll !== "UNRESOLVED_IDENTITY" ? <a className="ops-button" href={`/workbench?scroll=${encodeURIComponent(scroll)}`}>Open in Workbench</a> : null}
          </div>
          <div className="ops-list">
            {assets.map((asset) => (
              <article className="ops-item" key={asset.asset_id} data-control={`sources.discovered.asset.${asset.asset_id}`}>
                <div className="ops-item-head">
                  <strong>{asset.name}</strong>
                  <OpsBadge tone={asset.identity_state === "RESOLVED" && asset.status === "LOCAL_VERIFIED" ? "ok" : "warn"}>
                    {asset.identity_state === "RESOLVED" ? asset.status.replaceAll("_", " ").toLowerCase() : asset.identity_state.toLowerCase()}
                  </OpsBadge>
                </div>
                <div className="ops-note">{asset.kind.replaceAll("_", " ").toLowerCase()} · {(asset.bytes / 1024 / 1024).toFixed(2)} MB · {asset.display_path}</div>
                {asset.source ? <div className="ops-note">found through {asset.source}</div> : null}
                {asset.panels ? <div className="ops-note">{asset.panels}</div> : null}
                {asset.viewable && asset.preview_url ? <img className="src-discovered-preview" src={asset.preview_url} alt={`${asset.physical_scroll ?? "discovered"} ${asset.name}`} loading="lazy" /> : null}
                {asset.kind === "RENDER" && !asset.viewable ? <div className="ops-note">Render bytes are present and hash-bound, but the source is outside ARGUS’s declared serving roots. Mount or attach it explicitly before viewing.</div> : null}
                {asset.identity_state !== "RESOLVED" ? <div className="ops-note">ARGUS will not guess this identity. Candidates: {asset.identity_candidates.join(", ") || "none"}.</div> : null}
              </article>
            ))}
          </div>
        </section>
      ))}
      <OpsSource route="/api/discovery" field="assets, counts, roots, scanned_utc" />
    </OpsSection>
  );
}


function datasetRegistry(index: ReceiptIndex | null): {
  registry: DatasetRegistry | null;
  mtime: string | null;
  sha: string | null;
  relpath: string | null;
} {
  const env = index?.receipts?.dataset_registry;
  if (!env || !env.present || env.sealed) {
    return { registry: null, mtime: null, sha: null, relpath: env?.relpath ?? null };
  }
  return {
    registry: (env.content as DatasetRegistry | null) ?? null,
    mtime: env.mtime_utc ?? null,
    sha: env.sha256_16 ?? null,
    relpath: env.relpath,
  };
}

type CatalogueRead =
  | { state: "asking" }
  | { state: "failed"; why: string }
  | { state: "absent"; why: string }
  | { state: "read"; cat: AssetCatalogue; mtime: string | null; sha: string | null };

function assetCatalogue(rec: ReceiptsState): CatalogueRead {
  if (!rec.settled) return { state: "asking" };
  if (!rec.index) {
    return {
      state: "failed",
      why: rec.failure
        ? `/api/receipts answered ${rec.failure.status ?? "with a transport failure"}: ${rec.failure.detail}`
        : "/api/receipts returned no index",
    };
  }
  const env = rec.index.receipts?.asset_catalogue;
  if (!env) return { state: "absent", why: "the receipt index names no asset_catalogue receipt" };
  if (env.sealed) return { state: "absent", why: env.withheld_reason ?? "the receipt is under an active seal" };
  if (!env.present || !env.content) {
    return { state: "absent", why: env.missing_reason ?? `${env.relpath} has not been written` };
  }
  const cat = env.content as AssetCatalogue;
  if (!Array.isArray(cat.assets)) {
    return { state: "absent", why: `${env.relpath} carries no assets[] list` };
  }
  return { state: "read", cat, mtime: env.mtime_utc ?? null, sha: env.sha256_16 ?? null };
}

export interface FirstLettersView {
  all: string[];
  inHoldingsIndex: string[];
  withLocalBytes: { scroll: string; bytes: number | null; records: number | null }[];
  attestedNotSealed: string[];
  withContentHash: string[];
  ready: boolean;
}

export function firstLetters(
  targets: TargetsPayload | null,
  scrolls: ScrollsPayload | null,
  ds: { registry: DatasetRegistry | null },
): FirstLettersView | null {
  if (!targets) return null;
  const set = targets.sets.FIRST_LETTERS;
  const all =
    typeof set === "object" && set !== null && Array.isArray(set.scrolls) ? set.scrolls : [];
  if (all.length === 0) return null;

  const attestedOnly = (r: ScrollRow) =>
    (r.attested_stores ?? 0) > 0 && !r.cached_ct_regions && !r.physical_segments && !r.labelled;
  const held = new Set((scrolls?.scrolls ?? []).filter((r) => !attestedOnly(r)).map((r) => r.scroll));
  const reg = ds.registry;

  const withLocalBytes: FirstLettersView["withLocalBytes"] = [];
  const withContentHash: string[] = [];
  for (const s of all) {
    const row = reg?.scrolls?.[s];
    const local = row?.local_state?.value ?? null;
    if (local?.present) {
      withLocalBytes.push({
        scroll: s,
        bytes: typeof local.bytes === "number" ? local.bytes : null,
        records: typeof local.catalog_records === "number" ? local.catalog_records : null,
      });
    }
    if (row?.hashes?.value) withContentHash.push(s);
  }

  const attestedSet = new Set(
    (scrolls?.scrolls ?? []).filter((s) => (s.attested_stores ?? 0) > 0).map((s) => s.scroll),
  );
  return {
    all,
    attestedNotSealed: all.filter((s) => attestedSet.has(s)),
    inHoldingsIndex: all.filter((s) => held.has(s)),
    withLocalBytes,
    withContentHash,
    ready: Boolean(scrolls && reg),
  };
}


function BlockerHeadline({
  fl,
  surfaces,
}: {
  fl: FirstLettersView | null;
  surfaces: SurfacesPayload | null;
}) {
  const rel = surfaces?.surfaces?.public_release ?? null;
  const undeclared = rel?.undeclared_licences ?? null;

  if (!fl) {
    return (
      <section className="src-headline" data-tone="idle" data-control="sources.headline" aria-live="polite">
        <div className="src-headline-line">
          <OpsBadge tone="idle">unclaimed</OpsBadge>
          <strong>Reading the target freeze and the holdings index…</strong>
        </div>
        <div className="ops-note">
          No coverage figure is shown until both have answered. A denominator this screen
          invented would be misleading.
        </div>
      </section>
    );
  }

  const n = fl.all.length;
  return (
    <section className="src-headline" data-tone="bad" data-control="sources.headline" data-novice="missing" aria-live="polite">
      <div className="src-headline-line">
        <OpsBadge tone="bad">blocked</OpsBadge>
        <strong>
          {fl.inHoldingsIndex.length} of {n} First Letters targets has a sealed local store.
        </strong>
      </div>

      <div className="src-tiles" data-novice="ready">
        <Tile
          control="sources.headline.sealed"
          value={`${fl.inHoldingsIndex.length} / ${n}`}
          label="sealed local stores"
          tone="bad"
          word={fl.inHoldingsIndex.length === 0 ? "none sealed" : "partially sealed"}
        />
        {fl.attestedNotSealed.length > 0 ? (
          <Tile
            control="sources.headline.attested"
            value={`${fl.attestedNotSealed.length} / ${n}`}
            label="attested by hand, not sealed"
            tone="warn"
            word={fl.attestedNotSealed.join(" · ")}
          />
        ) : null}
        <Tile
          control="sources.headline.bytes"
          value={`${fl.withLocalBytes.length} / ${n}`}
          label="with SOME bytes catalogued"
          tone="warn"
          word="bytes, not sealed"
        />
        <Tile
          control="sources.headline.hash"
          value={`${fl.withContentHash.length} / ${n}`}
          label="with a content hash"
          tone="bad"
          word={fl.withContentHash.length === 0 ? "none hashed" : "partially hashed"}
        />
      </div>

      <OpsDetails control="sources.headline.why" summary="Why this is the blocker, and where each number comes from">
        <div className="ops-note">
          The blocker is <strong>verified acquisition identity</strong>. Not licence: the
          release surface reports{" "}
          {undeclared === null
            ? "no licence check in this read"
            : undeclared.length === 0
              ? "zero components with an undeclared licence"
              : `${undeclared.length} component(s) with an undeclared licence`}
          . And not model capability: identifying the input is a separate problem from
          detecting ink, so an unidentified store produces a result nobody can reproduce
          whatever model reads it.
        </div>
        <div className="ops-note">
          A sealed store means a store whose identity record binds source
          URL, array path, declared ROI, an ordered key-list hash over every fetched object, and
          the hash of the code that fetched it — a directory name is not an identity, and neither
          is a byte count.
        </div>
        <div className="ops-note">
          Reachable, held, complete and sealed are four separate facts on this page and are
          never merged into one indicator: a host that answers says nothing about what is held,
          bytes on disk say nothing about completeness, and a complete download is still not a
          sealed store.
        </div>
        <div className="ops-refusal">
          No read-only route publishes a store-identity binding, so this screen cannot show one
          as present even if it existed on disk. That is why the sealed count is derived from
          the holdings index — the strictest thing available — and reported as the floor it is.
        </div>
        <OpsSource route="sealed" field="derived: /api/targets → sets.FIRST_LETTERS.scrolls ∩ /api/scrolls → scrolls[]" />
        <OpsSource route="bytes" field="receipt dataset_registry → scrolls[].local_state.value.present" />
        <OpsSource route="hash" field="receipt dataset_registry → scrolls[].hashes.value" />
      </OpsDetails>
    </section>
  );
}

function Tile({
  control,
  value,
  label,
  tone,
  word,
}: {
  control: string;
  value: string;
  label: string;
  tone: OpsTone;
  word: string;
}) {
  return (
    <div className="src-tile" data-control={control} data-tone={tone}>
      <span className="src-tile-value">{value}</span>
      <span className="src-tile-label">{label}</span>
      <OpsBadge tone={tone}>{word}</OpsBadge>
    </div>
  );
}


interface AssetRow {
  key: string;
  name: string;
  detail: string | null;
  size: string;
  where: string;
  upstream: string;
  archive: string;
  pathHint: boolean;
  search: string;
}

interface FamilyView {
  id: string;
  origin: "upstream" | "local";
  label: string;
  rows: AssetRow[];
  countOnly: string | null;
  source: string;
}

interface GroupView {
  key: string;
  ungrouped: boolean;
  families: FamilyView[];
  localStores: number | null;
  localBytes: number | null;
  upstreamVolumes: number | null;
  reg: DatasetScroll | null;
  idx: ScrollRow | null;
  firstLetters: boolean | null;
  confusableWith: string | null;
  hasLocal: boolean;
}

const ARCHIVE_WORD = "no per-store record";

function tripleWord(t: Triple<unknown> | undefined, known: string, unknown: string): string {
  if (!t) return unknown;
  if (t.value === null || t.value === undefined) return unknown;
  return typeof t.value === "string" ? t.value : known;
}

function leaf(path: string): string {
  if (/^<withheld/.test(path)) return path;
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts.slice(-2).join("/");
}

function buildGroups(
  cat: CatalogueRead,
  reg: DatasetRegistry | null,
  idx: ScrollsPayload | null,
  ids: ScrollIdsPayload | null,
): GroupView[] {
  const keys = new Set<string>();
  const assetsBy = new Map<string, CatalogueAsset[]>();
  if (cat.state === "read") {
    for (const a of cat.cat.assets) {
      const k = a.grouping_key || "UNGROUPED";
      keys.add(k);
      const list = assetsBy.get(k);
      if (list) list.push(a);
      else assetsBy.set(k, [a]);
    }
  }
  for (const k of Object.keys(reg?.scrolls ?? {})) keys.add(k);
  for (const s of idx?.scrolls ?? []) keys.add(s.scroll);
  for (const k of ids?.canonical ?? []) keys.add(k);

  const confusable = new Map<string, string>();
  for (const [a, b] of ids?.confusable_pairs ?? []) {
    confusable.set(a, b);
    confusable.set(b, a);
  }

  const kindOrder = cat.state === "read" ? (cat.cat.schema_for_consumers?.asset_kinds ?? []) : [];

  const groups: GroupView[] = [];
  for (const key of keys) {
    const r = reg?.scrolls?.[key] ?? null;
    const families: FamilyView[] = [];

    if (r) {
      const vols = r.volumes?.value ?? null;
      if (vols && vols.length) {
        families.push({
          id: "volumes",
          origin: "upstream",
          label: "volumes",
          countOnly: null,
          source: "receipt dataset_registry → scrolls[].volumes",
          rows: vols.map((v) => ({
            key: `vol-${v.name}`,
            name: v.name,
            detail: typeof v.voxel_um === "number" ? `${v.voxel_um} µm` : null,
            size:
              typeof v.dense_level0_bytes === "number"
                ? `${bytesLabel(v.dense_level0_bytes)} dense level 0, derived from shape`
                : "size not recorded",
            where: "published upstream, not held",
            upstream: "listed",
            archive: ARCHIVE_WORD,
            pathHint: false,
            search: `${v.name} volume`.toLowerCase(),
          })),
        });
      }
      const surf = r.surfaces?.value ?? null;
      if (surf && surf.length) {
        families.push({
          id: "surfaces",
          origin: "upstream",
          label: "surfaces",
          countOnly: null,
          source: "receipt dataset_registry → scrolls[].surfaces",
          rows: surf.map((s) => ({
            key: `surf-${s.run}`,
            name: s.run,
            detail:
              typeof s.effective_um === "number"
                ? `${s.effective_um} µm effective${typeof s.pyramid_level === "number" ? `, level ${s.pyramid_level}` : ""}`
                : null,
            size: "size not recorded",
            where: "published upstream, not held",
            upstream: "listed",
            archive: ARCHIVE_WORD,
            pathHint: false,
            search: `${s.run} surface`.toLowerCase(),
          })),
        });
      }
      for (const [pk, list] of Object.entries(r.predictions?.value ?? {})) {
        if (pk === "surfaces" || !Array.isArray(list) || list.length === 0) continue;
        families.push({
          id: `predictions.${pk}`,
          origin: "upstream",
          label: `predictions: ${pk}`,
          countOnly: null,
          source: `receipt dataset_registry → scrolls[].predictions.${pk}`,
          rows: list.map((name) => ({
            key: `pred-${pk}-${name}`,
            name,
            detail: null,
            size: "size not recorded",
            where: "published upstream, not held",
            upstream: "listed",
            archive: ARCHIVE_WORD,
            pathHint: false,
            search: `${name} ${pk} prediction`.toLowerCase(),
          })),
        });
      }
      const obs = r.segments?.observations?.[0] ?? null;
      if (obs && typeof obs.count === "number" && obs.count > 0) {
        families.push({
          id: "segments",
          origin: "upstream",
          label: "segments",
          rows: [],
          countOnly: `${obs.count} listed upstream${obs.date ? ` as of ${obs.date}` : ""}${obs.method ? ` (${obs.method})` : ""} — counted, not itemised by the registry, and not held`,
          source: "receipt dataset_registry → scrolls[].segments.observations[0]",
        });
      }
    }

    const assets = assetsBy.get(key) ?? [];
    const byKind = new Map<string, CatalogueAsset[]>();
    for (const a of assets) {
      const list = byKind.get(a.asset_kind);
      if (list) list.push(a);
      else byKind.set(a.asset_kind, [a]);
    }
    const kinds = [...byKind.keys()].sort((a, b) => {
      const ia = kindOrder.indexOf(a);
      const ib = kindOrder.indexOf(b);
      return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib) || a.localeCompare(b);
    });
    let localBytes = 0;
    let bytesKnown = true;
    for (const kind of kinds) {
      const list = byKind.get(kind) ?? [];
      families.push({
        id: `local.${kind}`,
        origin: "local",
        label: kind.replace(/_/g, " "),
        countOnly: null,
        source: `receipt asset_catalogue → assets[asset_kind=${kind}]`,
        rows: list.map((a, i) => {
          if (typeof a.bytes === "number") localBytes += a.bytes;
          else bytesKnown = false;
          const seg = a.segment_id?.value ?? null;
          const hashed = a.hashes?.value !== null && a.hashes?.value !== undefined;
          const present = (a.local_state ?? "").toUpperCase() === "PRESENT";
          return {
            key: `loc-${kind}-${a.local_path}-${i}`,
            name: leaf(a.local_path),
            detail: seg ? `segment ${seg}` : null,
            size: `${bytesLabel(a.bytes)}${a.size_truncated ? " (size walk truncated)" : ""} in ${a.files ?? "an unrecorded number of"} files`,
            where: present
              ? hashed
                ? "on this machine, hashed"
                : "on this machine, not sealed"
              : `local state: ${a.local_state ?? "not recorded"}`,
            upstream: tripleWord(a.remote_state, "recorded", "not established"),
            archive: ARCHIVE_WORD,
            pathHint: !a.grouping_key_is_verified,
            search: `${a.local_path} ${kind} ${seg ?? ""}`.toLowerCase(),
          };
        }),
      });
    }

    const eligible = r?.eligibility?.value ?? null;
    const index = idx?.scrolls.find((s) => s.scroll === key) ?? null;
    groups.push({
      key,
      ungrouped: key === "UNGROUPED",
      families,
      localStores: cat.state === "read" ? assets.length : null,
      localBytes: cat.state === "read" ? (bytesKnown ? localBytes : null) : null,
      upstreamVolumes: r ? (r.volumes?.value?.length ?? 0) : null,
      reg: r,
      idx: index,
      firstLetters: eligible ? Boolean(eligible.first_letters_2027) : null,
      confusableWith: confusable.get(key) ?? null,
      hasLocal: assets.length > 0 || Boolean(index) || Boolean(r?.local_state?.value?.present),
    });
  }

  const rank = (g: GroupView) => (g.ungrouped ? 1 : g.hasLocal ? 0 : 2);
  return groups.sort((a, b) => rank(a) - rank(b) || a.key.localeCompare(b.key));
}


const GROUP_PAGE = 50;
const ROW_PAGE = 12;
const TARGET_PAGE = 50;

function Holdings({
  scrolls,
  ids,
  ds,
  rec,
  plan,
}: {
  scrolls: ReturnType<typeof usePoll<ScrollsPayload>>;
  ids: ReturnType<typeof usePoll<ScrollIdsPayload>>;
  ds: ReturnType<typeof datasetRegistry>;
  rec: ReceiptsState;
  plan: IngestPlanPayload | null;
}) {
  const { ctx } = useArgusContext();
  const selected = ctx.scroll;
  const reg = ds.registry;
  const cat = useMemo(() => assetCatalogue(rec), [rec]);
  const all = useMemo(
    () => buildGroups(cat, reg, scrolls.data, ids.data),
    [cat, reg, scrolls.data, ids.data],
  );
  const constructible =
    cat.state === "read" && cat.cat.pairability
      ? Object.values(cat.cat.pairability).reduce(
          (total, scroll) =>
            total +
            Object.values(scroll.per_segment ?? {}).filter(
              (segment) => segment.projection_state === "RAW_CT_PAIR_CONSTRUCTIBLE",
            ).length,
          0,
        )
      : null;

  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();
  const [toggled, setToggled] = useState<Record<string, boolean>>({});

  const visible = useMemo(() => {
    const out: GroupView[] = [];
    for (const g of all) {
      if (!q || g.key.toLowerCase().includes(q)) {
        out.push(g);
        continue;
      }
      const families = g.families
        .map((f) => ({
          ...f,
          rows: f.rows.filter((r) => r.search.includes(q)),
          countOnly: f.countOnly && f.label.includes(q) ? f.countOnly : null,
        }))
        .filter((f) => f.rows.length > 0 || f.countOnly);
      if (families.length) out.push({ ...g, families });
    }
    if (selected) {
      const i = out.findIndex((g) => g.key === selected);
      if (i > 0) out.unshift(...out.splice(i, 1));
    }
    return out;
  }, [all, q, selected]);

  const page = usePage(visible.length, GROUP_PAGE, `${q}|${selected ?? ""}`);
  const selectedKnown = selected ? all.some((g) => g.key === selected) : false;
  const loadingAll = cat.state === "asking" && !reg && !scrolls.data;

  return (
    <>
      <OpsSection
        control="sources.holdings"
        title="What we hold, by scroll and source family"
        hint="Each store states where it is once: on this machine, published upstream, and whether an archive record exists. Bytes catalogued is not a sealed store."
        aside={
          scrolls.data ? (
            <>
              <OpsBadge tone="idle" control="sources.holdings.count">
                {scrolls.data.totals.physical_scrolls} physical scrolls in the holdings index
              </OpsBadge>
              <OpsBadge tone="idle">{scrolls.data.totals.physical_segments} pieces</OpsBadge>
              <OpsBadge tone="idle">
                {scrolls.data.totals.label_representations} label representations
              </OpsBadge>
              {constructible !== null ? (
                <OpsBadge tone={constructible > 0 ? "ok" : "warn"} control="sources.holdings.constructible">
                  {constructible} segment{constructible === 1 ? "" : "s"} constructible
                </OpsBadge>
              ) : null}
            </>
          ) : null
        }
      >
        <ReadStates cat={cat} scrolls={scrolls} ds={ds} rec={rec} />

        {constructible !== null ? (
          <p className="ops-note">
            A segment is constructible only when ink labels, raw CT and a coordinate map exist
            for that same segment. Labels alone are not a pair.
          </p>
        ) : null}
        <p className="ops-note" aria-label="Pairability legend">
          Pairability states: <strong>raw CT pair constructible</strong>; <strong>labels present but
          projection unavailable</strong>; or <strong>no labels</strong>. These are segment states,
          not model verdicts.
        </p>
        <p className="ops-note">
          Grouping marked <strong>inferred from path</strong> came from a directory name only:
          a hint, not a declaration.
        </p>
        <OpsTable control="sources.holdings.measurement-units">
          <thead>
            <tr>
              <th>Pitch, microns</th>
              <th>Value and interpretation</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>source-declared</td>
              <td>source-declared scale; no unit conversion is implied</td>
            </tr>
          </tbody>
        </OpsTable>

        <div className="src-toolbar">
          <label className="src-filter">
            <span>Filter by scroll or store</span>
            <input
              type="search"
              value={query}
              placeholder="e.g. PHercFixture1, labels, seg-A"
              onChange={(e) => setQuery(e.target.value)}
              data-control="sources.holdings.filter"
            />
          </label>
          <SrcPager page={page} noun="scroll groups" control="sources.holdings.pager" />
        </div>

        {selected ? (
          <div className="ops-note" data-control="sources.holdings.selection">
            {selectedKnown ? (
              <>
                <strong>{selected}</strong> is the selected scroll (from the address), so its group
                is shown first and open.
              </>
            ) : (
              <>
                <strong>{selected}</strong> is selected in the address, and no payload this tab reads
                lists a scroll by that id.
              </>
            )}
          </div>
        ) : null}

        {loadingAll ? (
          <ul className="ops-list">
            <OpsUnknown what="The holdings" why="/api/scrolls and /api/receipts have not answered yet." />
          </ul>
        ) : visible.length === 0 ? (
          <div className="ops-note">
            {q ? `No scroll or store matches “${query.trim()}”.` : "No payload this tab reads lists any scroll."}
          </div>
        ) : (
          <div className="src-groups">
            {visible.slice(page.start, page.end).map((g) => {
              const isSel = g.key === selected;
              const auto = isSel || (q !== "" && visible.length <= 3);
              const open = toggled[g.key] ?? auto;
              return (
                <SrcGroup
                  key={g.key}
                  control={`sources.holdings.scroll.${g.key}`}
                  selected={isSel}
                  open={open}
                  onToggle={(o) => setToggled((t) => ({ ...t, [g.key]: o }))}
                  summary={<GroupSummary g={g} selected={isSel} cat={cat} />}
                >
                  <GroupBody g={g} cat={cat} filter={q} />
                </SrcGroup>
              );
            })}
          </div>
        )}

        <OpsDetails control="sources.holdings.provenance" summary="Where these groups come from">
          <div className="ops-note">
            Scrolls are grouped by the asset catalogue&rsquo;s <span className="ops-mono">grouping_key</span>{" "}
            and by the dataset registry&rsquo;s canonical ids. A store filed under a scroll only
            because its path names one is marked <em>path hint</em>: the catalogue forbids a
            filename from being a declaration. Source families are the kinds each payload declares
            — the registry&rsquo;s volumes, surfaces, predictions and segment counts for what is
            published upstream, and the catalogue&rsquo;s asset kinds for what is on this machine.
          </div>
          <div className="ops-note">
            Physical pieces and label representations are different counts and are never
            collapsed: one piece can carry markings in two acquisition families, and reporting
            representations as pieces overstates the inventory.
          </div>
          {scrolls.data?.nothing_moved ? <div className="ops-note">{scrolls.data.nothing_moved}</div> : null}
          <OpsSource route="/api/scrolls" field="scrolls[], totals" />
          {reg ? (
            <OpsSource
              route="/api/receipts"
              field="receipts.dataset_registry.content.scrolls[]"
              note={`written ${ds.mtime ?? "at an unrecorded time"} · sha ${ds.sha ?? "unknown"}`}
            />
          ) : null}
          {cat.state === "read" ? (
            <OpsSource
              route="/api/receipts"
              field="receipts.asset_catalogue.content.assets[]"
              note={`${cat.cat.assets.length} stores · written ${cat.mtime ?? "at an unrecorded time"} · sha ${cat.sha ?? "unknown"}`}
            />
          ) : null}
        </OpsDetails>
      </OpsSection>

      <StagedHoldings plan={plan} />

      <ArchiveConveyor />

      <ChunkCacheHold ds={ds} />
    </>
  );
}

function ReadStates({
  cat,
  scrolls,
  ds,
  rec,
}: {
  cat: CatalogueRead;
  scrolls: ReturnType<typeof usePoll<ScrollsPayload>>;
  ds: ReturnType<typeof datasetRegistry>;
  rec: ReceiptsState;
}) {
  const lines: React.ReactNode[] = [];
  if (scrolls.failure && !scrolls.data) {
    lines.push(
      <OpsFailure
        key="scrolls"
        what="Could not read /api/scrolls (the holdings index)"
        failure={scrolls.failure}
        onRetry={scrolls.refresh}
        control="sources.holdings"
      />,
    );
  }
  if (cat.state === "failed") {
    lines.push(
      <OpsUnknown
        key="cat"
        what="Could not read the asset catalogue"
        why={cat.why}
        next="start the read-only service and reload. Nothing on this page is substituted for the missing catalogue."
      />,
    );
  } else if (cat.state === "absent") {
    lines.push(<OpsUnknown key="cat" what="The asset catalogue receipt is not available" why={cat.why} />);
  }
  if (rec.settled && rec.index && !ds.registry) {
    lines.push(
      <OpsUnknown
        key="reg"
        what="The dataset registry receipt is not available"
        why={`${ds.relpath ?? "artifacts/registries/datasets.json"} is absent or sealed, so nothing published upstream can be listed per scroll.`}
      />,
    );
  }
  return lines.length ? <ul className="ops-list">{lines}</ul> : null;
}

function GroupSummary({ g, selected, cat }: { g: GroupView; selected: boolean; cat: CatalogueRead }) {
  return (
    <span className="src-sum">
      <span className="src-sum-id">{g.ungrouped ? "No scroll established" : g.key}</span>
      {selected ? <OpsBadge tone="info">selected</OpsBadge> : null}
      {g.firstLetters ? <span className="src-tag">First Letters target</span> : null}
      {g.confusableWith ? (
        <span className="src-tag" title="Computed by argus.core.scroll_ids.confusable_pairs">
          may be misread for {g.confusableWith} · argus.core.scroll_ids
        </span>
      ) : null}
      <span className="src-sum-facts">
        <span>
          {g.localStores === null
            ? cat.state === "asking"
              ? "local catalogue still loading"
              : "local catalogue could not be read"
            : g.localStores === 0
              ? g.reg?.local_state?.value?.present
                ? `no store in the asset catalogue · ${bytesLabel(g.reg.local_state.value.bytes ?? null)} in the data catalog, not sealed`
                : "nothing on this machine (nothing catalogued here)"
              : `${g.localStores} store${g.localStores === 1 ? "" : "s"} here · ${bytesLabel(g.localBytes)}, not sealed`}
        </span>
        <span>
          {g.ungrouped
            ? "not attributable to an upstream listing"
            : g.upstreamVolumes === null
              ? "no registry entry for upstream volumes"
              : g.upstreamVolumes === 0
                ? "no volume listed upstream"
                : `${g.upstreamVolumes} volume${g.upstreamVolumes === 1 ? "" : "s"} published upstream`}
        </span>
      </span>
    </span>
  );
}

function GroupBody({ g, cat, filter }: { g: GroupView; cat: CatalogueRead; filter: string }) {
  const r = g.reg;
  const local = r?.local_state?.value ?? null;
  const cache = r?.cache_state?.value ?? null;
  const labels = r?.labels ?? null;
  const upstream = g.families.filter((f) => f.origin === "upstream");
  const here = g.families.filter((f) => f.origin === "local");

  return (
    <>
      {g.idx && g.idx.blocked_by.length ? (
        <div className="src-missing">
          <strong>What is missing: </strong>
          {g.idx.blocked_by.join(" · ")}
        </div>
      ) : null}

      <div className="src-state-legend">
        Each row reads <strong>where it is</strong> · <strong>upstream</strong> ·{" "}
        <strong>archive</strong>.
      </div>

      <FamilyBlock title="Published upstream" families={upstream} empty={r ? "the registry lists nothing published upstream for this scroll" : "the dataset registry has no entry for this id"} filter={filter} scroll={g.key} />
      <FamilyBlock
        title="Catalogued on this machine"
        families={here}
        empty={
          cat.state === "read"
            ? "none: the asset catalogue files no store under this scroll"
            : cat.state === "asking"
              ? "the asset catalogue has not answered yet"
              : "could not read the asset catalogue, so this is unknown rather than empty"
        }
        filter={filter}
        scroll={g.key}
      />

      <OpsDetails control={`sources.holdings.scroll.${g.key}.facts`} summary="Scroll-level facts: labels, readings, catalogued bytes, integrity">
        <OpsFacts>
          {g.idx ? (
            <>
              <OpsFact label="Readings">
                {g.idx.results_complete > 0
                  ? `${g.idx.results_complete} complete reading${g.idx.results_complete === 1 ? "" : "s"}`
                  : g.idx.results_partial > 0
                    ? `${g.idx.results_partial} partial, none complete`
                    : "nothing read yet"}
              </OpsFact>
              <OpsFact label="Human ink markings">
                {g.idx.labelled === 0
                  ? "nobody has marked ink on this scroll"
                  : `${g.idx.labelled} of ${g.idx.physical_segments} pieces have markings · ${g.idx.label_representations} representation(s)`}
              </OpsFact>
              <OpsFact label="Acquisition families of the labels">
                {g.idx.native9} native · {g.idx.aligned} aligned label representation(s)
              </OpsFact>
              <OpsFact label="Cached CT regions">
                {g.idx.cached_ct_regions > 0
                  ? `${g.idx.cached_ct_regions} cached CT region(s)`
                  : "none — scoring would stream over the network"}
              </OpsFact>
            </>
          ) : (
            <OpsFact label="Holdings index">
              not listed in /api/scrolls, which lists only scrolls with staged, labelled or read material
            </OpsFact>
          )}
          {r ? (
            <>
              <OpsFact label="Labels">
                {labels?.value
                  ? labels.value.held_locally
                    ? `held locally${labels.value.where ? `: ${labels.value.where}` : ""}`
                    : "the registry records labels as not held locally"
                  : (labels?.reason ?? "the registry records no label field")}
              </OpsFact>
              <OpsFact label="Bytes in the data catalog">
                {local?.present
                  ? `${bytesLabel(local.bytes ?? null)} in ${local.files ?? "an unrecorded number of"} files, ${local.catalog_records ?? "an unrecorded number of"} record(s) — bytes, not a sealed store`
                  : "none catalogued"}
              </OpsFact>
              {cache && (cache.chunk_cache_dirs ?? 0) > 0 ? (
                <OpsFact label="Attributed chunk cache">
                  {cache.chunk_cache_dirs} dir(s), {bytesLabel(cache.chunk_cache_bytes ?? null)} — pieces
                  of upstream volumes, not a copy of any
                </OpsFact>
              ) : null}
              <OpsFact label="Integrity / seal">
                {r.hashes?.value
                  ? "a content hash is recorded"
                  : (r.hashes?.reason ??
                    "no content hash is recorded for this scroll, and no route publishes a store-identity binding")}
              </OpsFact>
            </>
          ) : null}
        </OpsFacts>
      </OpsDetails>
    </>
  );
}

function FamilyBlock({
  title,
  families,
  empty,
  filter,
  scroll,
}: {
  title: string;
  families: FamilyView[];
  empty: string;
  filter: string;
  scroll: string;
}) {
  return (
    <div className="src-origin">
      <h3 className="src-origin-title">{title}</h3>
      {families.length === 0 ? (
        <div className="ops-note">{filter ? "nothing here matches the filter" : empty}</div>
      ) : (
        <>
          {families.map((f) => (
            <FamilyTable key={f.id} f={f} filter={filter} scroll={scroll} />
          ))}
          <OpsSource route="/api/receipts" field={families.map((f) => f.source.replace(/^receipt /, "")).join(" · ")} />
        </>
      )}
    </div>
  );
}

function FamilyTable({ f, filter, scroll }: { f: FamilyView; filter: string; scroll: string }) {
  const page = usePage(f.rows.length, ROW_PAGE, filter);
  const control = `sources.holdings.scroll.${scroll}.${f.id}`;
  return (
    <div className="src-fam" data-control={control}>
      <div className="src-fam-head">
        <span className="src-fam-title">{f.label}</span>
        <span className="src-fam-count">
          {f.countOnly ? "count only" : `${f.rows.length} ${f.rows.length === 1 ? "item" : "items"}`}
        </span>
      </div>
      {f.countOnly ? (
        <div className="ops-note">{f.countOnly}</div>
      ) : (
        <>
          <OpsTable control={`${control}.table`}>
            <colgroup>
              <col className="src-col-item" />
              <col className="src-col-size" />
              <col className="src-col-state" />
            </colgroup>
            <thead>
              <tr>
                <th>Item</th>
                <th>Size</th>
                <th>Where it is · upstream · archive</th>
              </tr>
            </thead>
            <tbody>
              {f.rows.slice(page.start, page.end).map((r) => (
                <tr key={r.key}>
                  <td>
                    <span className="ops-mono">{r.name}</span>
                    {r.detail || r.pathHint ? (
                      <div className="src-row-sub">
                        {r.detail}
                        {r.detail && r.pathHint ? " · " : ""}
                        {r.pathHint ? (
                          <span title="filed under this scroll because its path names it; no metadata in the store declares a scroll">
                            path hint, not declared
                          </span>
                        ) : null}
                      </div>
                    ) : null}
                  </td>
                  <td>{r.size}</td>
                  <td>
                    <span className="src-state">
                      <span className="src-state-where">{r.where}</span>
                      {r.upstream !== "listed" ? (
                        <span>
                          <span className="src-state-k">upstream </span>
                          {r.upstream}
                        </span>
                      ) : null}
                      <span>
                        <span className="src-state-k">archive </span>
                        {r.archive}
                      </span>
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </OpsTable>
          {f.rows.length > ROW_PAGE ? (
            <SrcPager page={page} noun={f.label} control={`${control}.pager`} />
          ) : null}
        </>
      )}
    </div>
  );
}

function StagedHoldings({ plan }: { plan: IngestPlanPayload | null }) {
  return (
    <OpsSection
      control="sources.staged"
      title="Staged material, and whether more will fit"
      hint="Completeness is counted, never inferred from a directory existing."
      aside={
        plan ? (
          <OpsBadge
            tone={plan.holdings.every((h) => h.complete) ? "idle" : "warn"}
            control="sources.staged.count"
          >
            {plan.holdings.filter((h) => h.complete).length} of {plan.holdings.length} complete
          </OpsBadge>
        ) : null
      }
    >
      {!plan ? (
        <ul className="ops-list">
          <OpsUnknown what="Staged holdings" why="/api/ingest/plan has not answered yet." />
        </ul>
      ) : (
        <>
          <OpsTable control="sources.staged.table">
            <thead>
              <tr>
                <th>Asset</th>
                <th>Kind</th>
                <th>Size</th>
                <th>Complete</th>
                <th>What is missing</th>
              </tr>
            </thead>
            <tbody>
              {plan.holdings.map((h) => (
                <tr key={`${h.kind}-${h.id}`}>
                  <td className="ops-mono">{h.id}</td>
                  <td>{h.kind.replace(/_/g, " ")}</td>
                  <td className="ops-mono">{bytesLabel(h.bytes)}</td>
                  <td>
                    <OpsBadge tone={h.complete ? "idle" : "warn"}>
                      {h.complete ? "complete, counted" : "partial"}
                    </OpsBadge>
                  </td>
                  <td>
                    {h.complete
                      ? "every declared part is present"
                      : (h.why_incomplete ?? "the plan declares no reason")}
                  </td>
                </tr>
              ))}
            </tbody>
          </OpsTable>
          <div className="src-inline-stats">
            <span data-control="sources.staged.total">
              <span className="src-state-k">staged in total </span>
              <span className="ops-mono">{bytesLabel(plan.staged_bytes_total)}</span>
            </span>
            <span data-control="sources.staged.headroom">
              <span className="src-state-k">headroom above the storage floor </span>
              <span className="ops-mono">{bytesLabel(plan.storage.headroom_bytes)}</span>
              {plan.storage.headroom_bytes > 0 ? null : (
                <>
                  {" "}
                  <OpsBadge tone="bad">none</OpsBadge>
                </>
              )}
            </span>
            <span data-control="sources.staged.free">
              <span className="src-state-k">free on the staging drive </span>
              <span className="ops-mono">{bytesLabel(plan.storage.free_bytes)}</span>
            </span>
          </div>
        </>
      )}
      <OpsDetails control="sources.staged.more" summary="Why fetching is absent here, and the headroom rule">
        {plan ? <div className="ops-note">{plan.storage.headroom_note}</div> : null}
        <div className="ops-note">
          A fragment with some of its planes is a partial download, and reporting it as present is
          how a run gets built on data that is not there.
        </div>
        <OpsDisabled
          control="sources.staged.fetch"
          label="Fetch more material"
          reason="Absent by design. Staging is a mutating action on a separate control plane that must be started deliberately; a reading surface never starts a large transfer."
        />
        <OpsSource route="/api/ingest/plan" field="holdings[], staged_bytes_total, storage" />
      </OpsDetails>
    </OpsSection>
  );
}

function ArchiveConveyor() {
  const st = usePoll<StoragePayload>("/api/storage", { intervalMs: 300000 });
  const d = st.data;
  const byProject = useMemo(() => {
    const m = new Map<string, StoragePayload["largest"]>();
    for (const a of d?.largest ?? []) {
      const k = a.project ?? "project not declared";
      const list = m.get(k);
      if (list) list.push(a);
      else m.set(k, [a]);
    }
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [d]);

  return (
    <OpsSection
      control="sources.archive"
      title="Archive state, from the storage conveyor"
      hint="Local, remote and conveyor state for each logical asset, stated once. Not keyed to the stores above."
      aside={
        d ? (
          <OpsBadge tone="idle" control="sources.archive.count">
            {d.largest.length} largest of {d.assets} logical assets
          </OpsBadge>
        ) : null
      }
    >
      {st.failure && !d ? (
        <ul className="ops-list">
          <OpsFailure what="Could not read /api/storage" failure={st.failure} onRetry={st.refresh} control="sources.archive" />
        </ul>
      ) : !d ? (
        <ul className="ops-list">
          <OpsUnknown what="The storage conveyor" why="/api/storage has not answered yet." />
        </ul>
      ) : !d.catalog_present ? (
        <ul className="ops-list">
          <OpsUnknown what="The storage conveyor catalogue" why="/api/storage answered and reports that its catalogue file is not present." />
        </ul>
      ) : (
        <>
          <div className="src-inline-stats">
            {Object.entries(d.by_state).map(([k, v]) => (
              <span key={k}>
                <span className="src-state-k">{k.toLowerCase().replace(/_/g, " ")} </span>
                <span className="ops-mono">
                  {v.assets} · {v.gib} GiB
                </span>
              </span>
            ))}
          </div>
          <OpsDetails
            control="sources.archive.rows"
            summary={`The ${d.largest.length} largest logical assets, by project (the route publishes no more than these of ${d.assets})`}
          >
            {byProject.map(([project, rows]) => (
              <div key={project} className="src-fam">
                <div className="src-fam-head">
                  <span className="src-fam-title">{project}</span>
                  <span className="src-fam-count">{rows.length} of the published rows</span>
                </div>
                <OpsTable control={`sources.archive.${project}`}>
                  <colgroup>
                    <col className="src-col-item" />
                    <col className="src-col-size" />
                    <col className="src-col-state" />
                  </colgroup>
                  <thead>
                    <tr>
                      <th>Asset</th>
                      <th>Size</th>
                      <th>Local · remote · conveyor state</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((a) => (
                      <tr key={a.asset_id}>
                        <td className="ops-mono">{a.name ?? a.asset_id}</td>
                        <td className="ops-mono">{a.gib} GiB</td>
                        <td>
                          <span className="src-state">
                            <span>
                              <span className="src-state-k">local </span>
                              {a.local ?? "not recorded"}
                            </span>
                            <span>
                              <span className="src-state-k">remote </span>
                              {(a.remote ?? "not recorded").replace(/_/g, " ")}
                            </span>
                            <span>
                              <span className="src-state-k">state </span>
                              {(a.state ?? "not recorded").toLowerCase().replace(/_/g, " ")}
                            </span>
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </OpsTable>
              </div>
            ))}
          </OpsDetails>
        </>
      )}
      {d?.mutations_are_not_here ? <div className="ops-note">{d.mutations_are_not_here}</div> : null}
      <OpsSource route="/api/storage" field="assets, by_state, largest[]" />
    </OpsSection>
  );
}

function ChunkCacheHold({ ds }: { ds: ReturnType<typeof datasetRegistry> }) {
  const hold = ds.registry?.chunk_cache_hold?.value ?? null;
  if (!hold) return null;
  return (
    <OpsSection
      control="sources.chunkcache"
      title="The unattributed chunk cache"
      hint="Upstream bytes held by a caching layer whose filenames are hashes of the URL they came from, with no index."
      aside={<OpsBadge tone="warn" control="sources.chunkcache.state">on hold</OpsBadge>}
    >
      <div className="src-inline-stats">
        <span>
          <span className="src-state-k">size </span>
          <span className="ops-mono">{bytesLabel(hold.bytes ?? null)}</span>
        </span>
        <span>
          <span className="src-state-k">files </span>
          <span className="ops-mono">{hold.files ?? "unknown"}</span>
        </span>
        <span>
          <span className="src-state-k">attributed to a scroll </span>
          <span className="ops-mono">
            {typeof hold.attributed_fraction === "number"
              ? `${Math.round(hold.attributed_fraction * 100)}%`
              : "unknown"}
          </span>
        </span>
      </div>
      <OpsDetails control="sources.chunkcache.more" summary="Hold status, what is unique in it, and why it cannot be pruned here">
        <OpsFacts>
          <OpsFact label="Status">{hold.status ?? "not declared"}</OpsFact>
          <OpsFact label="Unique information">{hold.unique_information ?? "not declared"}</OpsFact>
        </OpsFacts>
        <OpsDisabled
          control="sources.chunkcache.prune"
          label="Prune the chunk cache"
          reason="Absent by design: eviction belongs to the storage conveyor, and one owner of the eviction policy keeps bytes from being removed before a verified copy exists."
        />
        <OpsSource route="/api/receipts" field="receipts.dataset_registry.content.chunk_cache_hold" />
      </OpsDetails>
    </OpsSection>
  );
}


function Targets({
  targets,
  fl,
  ds,
}: {
  targets: ReturnType<typeof usePoll<TargetsPayload>>;
  fl: FirstLettersView | null;
  ds: ReturnType<typeof datasetRegistry>;
}) {
  const d = targets.data;
  const [params, setParams] = useSearchParams();
  const prizeAsked = params.get("prize");
  const prizes = useMemo(
    () =>
      Object.entries(d?.sets ?? {})
        .filter(
          ([, v]) => typeof v === "object" && v !== null && typeof v.count === "number",
        )
        .map(([k]) => k),
    [d],
  );
  const prize = prizes.includes(prizeAsked ?? "") ? (prizeAsked as string) : (prizes[0] ?? "");
  const rows = useMemo(
    () => (d?.targets ?? []).filter((t) => t.prizes.includes(prize)),
    [d, prize],
  );
  const page = usePage(rows.length, TARGET_PAGE, prize);

  const sealReasons = useMemo(() => {
    const m = new Map<string, string[]>();
    for (const t of rows) {
      const why = ds.registry?.scrolls?.[t.scroll]?.hashes?.reason ?? "no content hash is recorded";
      const list = m.get(why);
      if (list) list.push(t.scroll);
      else m.set(why, [t.scroll]);
    }
    return [...m.entries()];
  }, [rows, ds.registry]);

  if (targets.failure && !d) {
    return (
      <OpsSection control="sources.targets" title="Eligible targets">
        <ul className="ops-list">
          <OpsFailure
            what="Could not read /api/targets (the target freeze)"
            failure={targets.failure}
            onRetry={targets.refresh}
            control="sources.targets"
          />
        </ul>
      </OpsSection>
    );
  }
  if (!d) {
    return (
      <OpsSection control="sources.targets" title="Eligible targets">
        <ul className="ops-list">
          <OpsUnknown what="The target freeze" why="/api/targets has not answered yet." />
        </ul>
      </OpsSection>
    );
  }
  if (d.available === false) {
    const next = d.next_action;
    return (
      <OpsSection control="sources.targets" title="Eligible targets">
        <ul className="ops-list">
          <OpsUnknown
            what="The official target registry is not installed"
            why={`${d.why_v2}${next?.label ? ` Next: ${next.label}${next.where ? ` in ${next.where}` : ""}.` : ""}`}
          />
        </ul>
      </OpsSection>
    );
  }

  return (
    <>
      <OpsSection
        control="sources.targets"
        title="Eligible scroll volumes"
        hint="Two prizes, two lists, never merged."
        aside={
          <>
            {prizes.map((p) => {
              const set = d.sets[p];
              const n = typeof set === "object" && set !== null ? set.count : undefined;
              return (
                <button
                  key={p}
                  type="button"
                  className="ops-subtab"
                  aria-pressed={p === prize}
                  data-control={`sources.targets.prize.${p}`}
                  onClick={() => {
                    const next = new URLSearchParams(params);
                    next.set("prize", p);
                    setParams(next, { replace: true });
                  }}
                >
                  {p.replace(/_/g, " ")}
                  {typeof n === "number" ? ` · ${n}` : ""}
                </button>
              );
            })}
          </>
        }
      >
        <OpsQuote cite="/api/targets → why_v2">{d.why_v2}</OpsQuote>

        <OpsTable control="sources.targets.table">
          <thead>
            <tr>
              <th>Scroll</th>
              <th>Acquisition</th>
              <th>Upstream store</th>
              <th>Held here</th>
              <th>Sealed</th>
              <th>Fence</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(page.start, page.end).map((t) => {
              const row = ds.registry?.scrolls?.[t.scroll];
              const local = row?.local_state?.value ?? null;
              const heldBytes = local?.present ? (local.bytes ?? null) : null;
              return (
                <tr key={t.scroll} data-control={`sources.targets.row.${t.scroll}`}>
                  <td>
                    <strong>{t.scroll}</strong>
                    <div className="ops-mono">scan {t.scan_id}</div>
                  </td>
                  <td className="ops-mono">
                    {t.pitch_um ?? "?"} µm / {t.energy_kev ?? "?"} keV
                  </td>
                  <td>
                    <OpsBadge tone={t.store_state === "FOUND" ? "idle" : "warn"}>
                      {t.store_state === "FOUND" ? "published upstream" : "not found upstream"}
                    </OpsBadge>
                    {t.volume_store ? <div className="ops-mono src-cell-wrap">{t.volume_store}</div> : null}
                  </td>
                  <td>
                    {heldBytes === null ? (
                      <OpsBadge tone="idle">nothing catalogued</OpsBadge>
                    ) : (
                      <>
                        <OpsBadge tone="warn">bytes only</OpsBadge>
                        <div className="ops-mono">{bytesLabel(heldBytes)}</div>
                      </>
                    )}
                  </td>
                  <td>
                    <OpsBadge tone="bad">no identity binding</OpsBadge>
                  </td>
                  <td>
                    {t.operator_fence ? (
                      <OpsBadge tone="bad" title={t.operator_fence}>
                        fenced
                      </OpsBadge>
                    ) : (
                      <span className="ops-note">none</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </OpsTable>
        <SrcPager page={page} noun="targets" control="sources.targets.pager" />

        <OpsDetails control="sources.targets.why" summary="Why no row is sealed, fences, and where this table comes from">
          {sealReasons.map(([why, list]) => (
            <div key={why} className="ops-note">
              <strong>Sealed, {list.length} row{list.length === 1 ? "" : "s"}: </strong>
              {why}
            </div>
          ))}
          {Object.entries(d.operator_fences ?? {}).map(([s, why]) => (
            <div key={s} className="ops-refusal">
              <strong>{s} is fenced: </strong>
              {why}
            </div>
          ))}
          <OpsSource route="/api/targets" field={`targets[prizes∋${prize}], sets`} />
          {d.source ? (
            <OpsSource
              route={d.source.url}
              note={`captured ${d.source.bytes} bytes, sha256 ${d.source.sha256.slice(0, 16)}…`}
            />
          ) : null}
        </OpsDetails>
      </OpsSection>

      <OpsSection
        control="sources.families"
        title="Acquisition families"
        hint="Eligible scrolls grouped by their published acquisition family."
        aside={
          <OpsBadge tone="idle">
            {Object.keys(d.acquisition_families).length} families
          </OpsBadge>
        }
      >
        <div className="src-wrap-table">
        <OpsTable control="sources.families.table">
          <thead>
            <tr>
              <th>Family</th>
              <th>Targets</th>
              <th>Scrolls</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(d.acquisition_families).map(([fam, list]) => (
              <tr key={fam} data-control={`sources.families.${fam.replace(/[^a-z0-9]/gi, "")}`}>
                <td className="ops-mono">{fam}</td>
                <td className="ops-mono">{list.length}</td>
                <td>{list.join(" · ")}</td>
              </tr>
            ))}
          </tbody>
        </OpsTable>
        </div>
        <OpsDetails control="sources.families.caveats" summary="What a larger target list does not change">
          {d.what_this_does_not_change?.length ? (
            <ul className="ops-list">
              {d.what_this_does_not_change.map((w, i) => (
                <OpsItem key={i} tone="warn" title={w} />
              ))}
            </ul>
          ) : (
            <div className="ops-note">/api/targets lists no caveats in what_this_does_not_change.</div>
          )}
          <OpsSource route="/api/targets" field="acquisition_families, what_this_does_not_change" />
        </OpsDetails>
      </OpsSection>

      {d.v1_ids_that_no_longer_resolve.length || fl ? (
        <OpsSection
          control="sources.targets.more"
          title="Dead scan ids and per-target coverage"
          hint="Kept one disclosure away: both are reference lists, and the headline above carries their totals."
        >
          {d.v1_ids_that_no_longer_resolve.length ? (
            <div data-control="sources.deadids">
              <OpsDetails
                control="sources.deadids.list"
                summary={`${d.v1_ids_that_no_longer_resolve.length} scan ids from the superseded freeze no longer resolve`}
              >
                <OpsBadge tone="warn" control="sources.deadids.count">
                  {d.v1_ids_that_no_longer_resolve.length} dead
                </OpsBadge>
                <div className="ops-note">
                  Kept visible because a dead id in a manifest fails as though the volume were
                  missing rather than as though the identifier were wrong, and those need different
                  fixes.
                </div>
                <div className="ops-mono">{d.v1_ids_that_no_longer_resolve.join(" · ")}</div>
                <OpsSource route="/api/targets" field="v1_ids_that_no_longer_resolve" />
              </OpsDetails>
            </div>
          ) : null}
          {fl ? (
            <div data-control="sources.flcoverage">
              <OpsDetails control="sources.flcoverage.list" summary="First Letters coverage, target by target">
                <OpsFacts>
                  <OpsFact label="Declared set">{fl.all.length} targets</OpsFact>
                  <OpsFact label="In the local holdings index">
                    {fl.inHoldingsIndex.length === 0
                      ? "none. The holdings index lists only scrolls with staged, labelled or read material, and no First Letters target is in it."
                      : fl.inHoldingsIndex.join(" · ")}
                  </OpsFact>
                  <OpsFact label="With some bytes catalogued">
                    {fl.withLocalBytes.length === 0
                      ? "none"
                      : fl.withLocalBytes
                          .map((r) => `${r.scroll} (${bytesLabel(r.bytes)})`)
                          .join(" · ")}
                  </OpsFact>
                  <OpsFact label="With a content hash">
                    {fl.withContentHash.length === 0
                      ? "none. Without one, nothing held can be bound to the object upstream published."
                      : fl.withContentHash.join(" · ")}
                  </OpsFact>
                </OpsFacts>
                <OpsSource route="/api/targets + /api/scrolls + receipt dataset_registry" field="derived intersection" />
              </OpsDetails>
            </div>
          ) : null}
        </OpsSection>
      ) : null}
    </>
  );
}


function Upstream({
  sources,
  surfaces,
  ds,
}: {
  sources: ReturnType<typeof usePoll<SourcesPayload>>;
  surfaces: SurfacesPayload | null;
  ds: ReturnType<typeof datasetRegistry>;
}) {
  const rel = surfaces?.surfaces?.public_release ?? null;
  const upd = surfaces?.surfaces?.updates ?? null;

  return (
    <>
      <OpsSection
        control="sources.registry"
        title="Upstream hosts"
        hint="Reachable and held are separate facts, and neither is shown in green."
        aside={
          sources.data ? (
            <OpsBadge tone="idle" control="sources.registry.count">
              {sources.data.sources.length} declared
            </OpsBadge>
          ) : null
        }
      >
        {sources.failure && !sources.data ? (
          <ul className="ops-list">
            <OpsFailure
              what="Could not read /api/sources (the upstream registry)"
              failure={sources.failure}
              onRetry={sources.refresh}
              control="sources.registry"
            />
          </ul>
        ) : !sources.data ? (
          <ul className="ops-list">
            <OpsUnknown what="The upstream registry" why="/api/sources has not answered yet." />
          </ul>
        ) : (
          <ul className="ops-list">
            {sources.data.sources.map((s) => {
              const up = s.probe_result.reachable;
              const held = s.local.any_present;
              return (
                <OpsItem
                  key={s.id}
                  control={`sources.registry.${s.id}`}
                  tone={up === false ? "bad" : "idle"}
                  title={s.name}
                  badges={
                    <>
                      <span className="src-tag">{s.kind}</span>
                      <OpsBadge
                        tone={!s.probe_result.checked ? "idle" : up ? "info" : "bad"}
                        control={`sources.registry.${s.id}.reachable`}
                      >
                        {!s.probe_result.checked
                          ? "not probed"
                          : up
                            ? `reachable${s.probe_result.status ? ` · HTTP ${s.probe_result.status}` : ""}`
                            : "unreachable"}
                      </OpsBadge>
                      <OpsBadge tone="idle" control={`sources.registry.${s.id}.held`}>
                        {held ? "some local path present" : "nothing held"}
                      </OpsBadge>
                    </>
                  }
                >
                  <div className="ops-item-body">{s.authoritative_for}</div>
                  {s.caution ? <div className="ops-refusal">{s.caution}</div> : null}
                  <OpsDetails control={`sources.registry.${s.id}.more`} summary="Endpoint, local paths and notes">
                    <OpsFacts>
                      <OpsFact label="Endpoint">
                        <span className="ops-mono">{s.url}</span>
                        {typeof s.probe_result.ms === "number" ? (
                          <div className="ops-note">answered in {s.probe_result.ms} ms</div>
                        ) : null}
                        {s.probe_result.note ? <div className="ops-note">{s.probe_result.note}</div> : null}
                        {!s.probe_result.checked && s.probe_result.why ? (
                          <div className="ops-note">{s.probe_result.why}</div>
                        ) : null}
                        {s.probe_result.error ? (
                          <div className="ops-refusal">{s.probe_result.error}</div>
                        ) : null}
                      </OpsFact>
                      <OpsFact label="Local paths">
                        {s.local.paths.length === 0 ? (
                          "no local path is declared for this source"
                        ) : (
                          <ul className="ops-list">
                            {s.local.paths.map((p) => (
                              <li key={p.path} className="ops-mono">
                                {p.path}
                                {p.present
                                  ? ` · present${p.entries !== undefined ? `, ${p.entries} entries` : ""}`
                                  : " · absent"}
                                {p.error ? ` · ${p.error}` : ""}
                              </li>
                            ))}
                          </ul>
                        )}
                      </OpsFact>
                      {s.notes ? <OpsFact label="Notes">{s.notes}</OpsFact> : null}
                      {s.assets?.length ? (
                        <OpsFact label="Declared downloadable objects">
                          <ul className="ops-list" data-control={`sources.registry.${s.id}.assets`}>
                            {s.assets.map((asset) => (
                              <li key={asset.url}>
                                <a href={asset.url} target="_blank" rel="noreferrer" className="ops-mono">
                                  {asset.scroll} · pass{asset.pass}.zarr
                                </a>
                                {` · ${asset.state} · ${asset.role}`}
                              </li>
                            ))}
                          </ul>
                        </OpsFact>
                      ) : null}
                      {s.acquisition_policy ? (
                        <OpsFact label="Acquisition policy">
                          <span className="ops-mono">{s.acquisition_policy}</span>
                        </OpsFact>
                      ) : null}
                    </OpsFacts>
                  </OpsDetails>
                </OpsItem>
              );
            })}
          </ul>
        )}
        <OpsDetails control="sources.registry.more" summary="Why there is no pull control">
          <div className="ops-note">
            A source that is up while we hold nothing from it is a source we have not used, and
            that is exactly the state worth seeing.
          </div>
          {sources.data?.note ? <div className="ops-note">{sources.data.note}</div> : null}
          <OpsDisabled
            control="sources.registry.sync"
            label="Pull latest from this host"
            reason="Absent by design. A probe is one small GET the service performs; a pull is hundreds of gigabytes with a disk cost and a licence attached. There is no route that starts one, and there will not be one on a reading surface."
          />
          <OpsSource route="/api/sources?live=true" field="sources[]" />
        </OpsDetails>
      </OpsSection>

      <OpsSection
        control="sources.licence"
        title="Licence"
        hint="Two different licence questions, and only one of them has an answer on this machine."
      >
        <ul className="ops-list">
          <OpsItem
            control="sources.licence.components"
            tone={rel ? (rel.undeclared_licences?.length ? "bad" : "idle") : "idle"}
            title="Software and components shipped by this project"
            state={
              rel
                ? rel.undeclared_licences?.length
                  ? `${rel.undeclared_licences.length} undeclared`
                  : "every component declares a licence"
                : "not read"
            }
          >
            <div className="ops-item-body">
              {rel
                ? rel.undeclared_licences?.length
                  ? `Undeclared: ${rel.undeclared_licences.join(", ")}. A publish is BLOCKED while any component carries no declared licence.`
                  : "The release surface resolves a licence for every component it tracks, so the licence gate is not what is holding anything up."
                : "/api/surfaces has not answered yet."}
            </div>
            <OpsSource route="/api/surfaces" field="surfaces.public_release.undeclared_licences" />
          </OpsItem>
          <li className="src-li">
            <OpsDetails control="sources.licence.pervolume" summary="Per-volume data licence: not available from any read-only route">
              <OpsUnknown
                what="The data licence of each individual scroll volume"
                why="No read-only route publishes a per-volume licence. The upstream registry states what each host is authoritative for and carries its cautions, and the volumes themselves are governed by the terms of the host that published them — but this interface cannot show you a per-target licence field, so it does not pretend to."
                next="read the terms at the publishing host named in the registry, or the acquisition receipt the operator keeps outside this interface."
              />
            </OpsDetails>
          </li>
        </ul>
      </OpsSection>

      {upd ? (
        <OpsSection
          control="sources.pins"
          title="Pinned upstream versions"
          hint="What this project builds against, and whether upstream has moved since. Discovery is automatic; promotion is not."
          aside={
            <OpsBadge tone={upd.drifted?.length ? "warn" : "idle"} control="sources.pins.state">
              {upd.headline}
            </OpsBadge>
          }
        >
          <OpsTable control="sources.pins.table">
            <thead>
              <tr>
                <th>Source</th>
                <th>Kind</th>
                <th>State</th>
                <th>Pinned</th>
                <th>Upstream now</th>
                <th>Behind</th>
              </tr>
            </thead>
            <tbody>
              {(upd.sources ?? []).map((s) => (
                <tr key={s.key}>
                  <td className="ops-mono">{s.key}</td>
                  <td>{s.kind.replace(/_/g, " ")}</td>
                  <td>
                    <OpsBadge tone={s.state === "IN_SYNC" ? "idle" : "warn"}>
                      {s.state.toLowerCase().replace(/_/g, " ")}
                    </OpsBadge>
                  </td>
                  <td className="ops-mono">{s.stable ?? "unpinned"}</td>
                  <td className="ops-mono">{s.candidate ?? "unknown"}</td>
                  <td className="ops-mono">{s.behind ?? "not reported"}</td>
                </tr>
              ))}
            </tbody>
          </OpsTable>
          {upd.nothing_was_promoted ? <div className="ops-note">{upd.nothing_was_promoted}</div> : null}
          <OpsSource route="/api/surfaces" field="surfaces.updates" note={`checked ${upd.checked_utc ?? "at an unrecorded time"}`} />
        </OpsSection>
      ) : null}

      {ds.registry?.sources ? (
        <OpsSection
          control="sources.derived-from"
          title="What the dataset registry was composed from"
          hint="Inputs captured on different days against a bucket that moves."
        >
          <OpsDetails control="sources.derived-from.list" summary={`${Object.keys(ds.registry.sources).length} input documents, and how old the registry is`}>
            <OpsFacts>
              {Object.entries(ds.registry.sources).map(([k, v]) => (
                <OpsFact key={k} label={k.replace(/_/g, " ")}>
                  <span className="ops-mono">{v}</span>
                </OpsFact>
              ))}
            </OpsFacts>
            <div className="ops-note">
              The registry itself was written {ds.mtime ?? "at an unrecorded time"}
              {ds.mtime ? ` (${agoLabel(ageSeconds(ds.mtime))})` : ""}, covers{" "}
              {ds.registry.scroll_count} scrolls, and records {ds.registry.eligible_count} of them
              as prize-eligible. Read that eligibility figure against the live target freeze on the
              Eligible targets tab: where the two disagree, the freeze is the newer document and
              this receipt is the older one.
            </div>
            <OpsSource route="/api/receipts" field="receipts.dataset_registry" />
          </OpsDetails>
        </OpsSection>
      ) : null}
    </>
  );
}

function ageSeconds(utc: string): number | null {
  const t = Date.parse(utc);
  if (Number.isNaN(t)) return null;
  return Math.max(0, (Date.now() - t) / 1000);
}


function IngestTab({
  selectedScroll,
  targets,
  sources,
  plan,
}: {
  selectedScroll: string | null;
  targets: TargetsPayload | null;
  sources: SourcesPayload | null;
  plan: IngestPlanPayload | null;
}) {
  const { mode } = useArgusContext();
  return (
    <>
      <ScrollStatusBar control="sources.scroll.status" />
      <RetrievalPlanner
        selectedScroll={selectedScroll}
        targets={targets}
        sources={sources}
        mode={mode}
        storage={plan?.storage ?? null}
      />
      <ProfilePlanner selectedScroll={selectedScroll} />
      <SegmentImportPanel />
      <ExternalEvidencePanel />
      <ScienceAttachPanel />
      {mode === "expert" ? (
        <OpsSection
          control="sources.ingest"
          title="Command and staging details"
          hint="The command-service allowlist and staging diagnostics. These are implementation controls, not the user journey above."
        >
          <Ingest />
        </OpsSection>
      ) : null}
    </>
  );
}

function RetrievalPlanner({
  selectedScroll,
  targets,
  sources,
  mode,
  storage,
}: {
  selectedScroll: string | null;
  targets: TargetsPayload | null;
  sources: SourcesPayload | null;
  mode: ArgusMode;
  storage: IngestPlanPayload["storage"] | null;
}) {
  const prefill = useMemo(() => retrievalPrefill(selectedScroll, targets, sources), [selectedScroll, targets, sources]);
  const targetRegistryUnavailable = targets?.available === false;
  const { status } = useScrollStatus(selectedScroll);
  const [scroll, setScroll] = useState(selectedScroll?.trim() ?? "");
  const [url, setUrl] = useState("");
  const [volumeId, setVolumeId] = useState("");
  const [phase, setPhase] = useState("A0");
  const [result, setResult] = useState<RetrievalPlanPayload | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [busy, setBusy] = useState(false);
  const appliedPrefill = useRef<string | null>(null);

  useEffect(() => {
    if (!selectedScroll) {
      setScroll("");
      setUrl("");
      setVolumeId("");
      appliedPrefill.current = null;
      return;
    }
    setScroll(selectedScroll.trim());
    if (prefill && appliedPrefill.current !== selectedScroll) {
      setUrl(prefill.url);
      setVolumeId(prefill.volumeId);
      appliedPrefill.current = selectedScroll;
    }
  }, [selectedScroll, prefill]);

  const plan = useCallback(async () => {
    setBusy(true);
    const q = new URLSearchParams({ scroll, phase });
    if (url.trim()) q.set("url", url.trim());
    if (volumeId.trim()) q.set("volume_id", volumeId.trim());
    const response = await getJson<RetrievalPlanPayload>(`/api/retrieval/plan?${q}`);
    if (response.ok) {
      setResult(response.data);
      setFailure(null);
    } else {
      setFailure(response);
    }
    setBusy(false);
  }, [phase, scroll, url, volumeId]);

  const automaticPlanKey = useRef<string | null>(null);
  useEffect(() => {
    if (mode !== "guided" || !scroll.trim() || !url.trim() || !volumeId.trim()) return;
    const key = [scroll.trim(), url.trim(), volumeId.trim(), phase].join("\n");
    if (automaticPlanKey.current === key) return;
    automaticPlanKey.current = key;
    void plan();
  }, [mode, phase, plan, scroll, url, volumeId]);

  return (
    <OpsSection
      control="sources.retrieval"
      title="Use the CT already here, or fill only the gaps"
      hint="ARGUS keeps existing renders visible, checks the exact CT identity separately, and never starts a download from this page without deliberate approval."
      aside={<OpsBadge tone="info">local first</OpsBadge>}
    >
      {mode === "guided" ? (
        <>
          <div className="ops-facts" data-control="sources.retrieval.local-summary">
            <div><span className="ops-fact-label">Selected scroll</span><strong>{selectedScroll ?? "choose a scroll"}</strong></div>
            <div><span className="ops-fact-label">Exact CT</span><strong>{volumeId || (targetRegistryUnavailable ? "official registry not configured" : "not declared")}</strong></div>
            <div><span className="ops-fact-label">On this computer</span><strong>{status?.questions.local.answer ?? "checking local inventory"}</strong></div>
            <div><span className="ops-fact-label">Existing evidence</span><strong>{status?.progress_summary?.verified_outputs
              ? `${status.progress_summary.verified_outputs} verified output(s) remain viewable`
              : "no verified outputs recorded"}</strong></div>
          </div>
          <ol className="ops-list" data-control="sources.retrieval.journey" aria-label="CT acquisition steps">
            <li><strong>Keep what already works.</strong> Existing renders and evidence stay available; they are not erased because the raw CT receipt is incomplete.</li>
            <li><strong>ARGUS checks identity and destination automatically.</strong> Selecting the scroll makes a zero-download plan for the official CT and shows where its verified copy belongs.</li>
            <li><strong>Fill only a proved gap.</strong> A later operator-approved acquisition may retrieve missing objects. Until then, nothing moves.</li>
          </ol>
          <p className="ops-note" data-control="sources.retrieval.guided-explanation">
            <strong>Why this step is still open:</strong>{" "}
            {targetRegistryUnavailable
              ? `${targets?.why_v2 ?? "The official target registry is not installed."} The official scan, volume, and publication status are unknown.`
              : status?.steps.find((step) => step.id === "acquisition")?.why
              ?? "ARGUS has not finished reading the selected scroll's local CT status."}
          </p>
        </>
      ) : (
        <>
          <ol className="ops-list" data-control="sources.retrieval.journey" aria-label="CT acquisition steps">
            <li><strong>Confirm the exact input.</strong> ARGUS fills the official source and exact volume for the selected physical scroll.</li>
            <li><strong>Separate inventory from proof.</strong> Existing catalogue/cache bytes are leads, not a completed CT, until the canonical store verifies the same volume identity and required objects.</li>
            <li><strong>Check the zero-fetch plan.</strong> No volume bytes move until the source, identity, phase, size ceiling and destination are visible.</li>
            <li><strong>Complete the canonical store.</strong> The governed acquisition resumes its own partial objects, fetches its missing objects, seals the identity and returns a receipt.</li>
            <li><strong>Continue in Workbench.</strong> The receipt-bound CT becomes the input for surface prediction, tracing, flattening, ink review, transcription and translation.</li>
          </ol>
          <details data-control="sources.retrieval.agent-contract">
            <summary>AI and automation contract</summary>
            <p className="ops-note">
              Read <code>/api/journey</code> for the ordered process and action IDs, then read
              <code>/api/scroll_truth?scroll=&lt;physical-scroll&gt;</code> before every step. Follow the
              returned <code>next_action.to</code>; governed mutations use the session, plan and action
              routes named by the journey contract. The same contract drives the buttons on this page.
            </p>
          </details>
        </>
      )}
      <div className="ops-facts" data-control="sources.retrieval.form">
        {mode === "expert" ? <>
          <label>
            <span className="ops-fact-label">Physical scroll</span>
            <input value={scroll} onChange={(e) => setScroll(e.target.value)} placeholder="PHercParis4" />
          </label>
          <label>
            <span className="ops-fact-label">Official mirror URL</span>
            <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="Paste the official mirror URL" />
          </label>
          <label>
            <span className="ops-fact-label">Exact volume ID</span>
            <input value={volumeId} onChange={(e) => setVolumeId(e.target.value)} placeholder="catalogue volume identity" />
          </label>
        </> : null}
        {mode === "expert" ? <>
          <label>
            <span className="ops-fact-label">Planning phase</span>
            <select value={phase} onChange={(e) => setPhase(e.target.value)}>
              <option value="A0">Identity and file list only (up to 1 MiB)</option>
              <option value="A1">Coarse geometry sample</option>
              <option value="A2">Exact working region</option>
            </select>
          </label>
          <button type="button" className="ops-button" onClick={() => void plan()} disabled={busy || !scroll.trim() || !url.trim() || !volumeId.trim()}>
            {busy ? "Checking…" : "Plan without downloading"}
          </button>
        </> : (
          <div role="status" data-control="sources.retrieval.automatic-check">
            <span className="ops-fact-label">Automatic check</span>
            <strong>{targetRegistryUnavailable
              ? "Waiting for the official target registry"
              : busy
                ? "Checking official identity, local coverage and destination…"
                : result
                  ? "Complete — no download started"
                  : "Waiting for the selected scroll's official source"}</strong>
          </div>
        )}
      </div>
      {targetRegistryUnavailable ? (
        <p className="ops-muted" data-control="sources.retrieval.registry-unavailable" role="status">
          {targets?.why_v2 ?? "The official target registry is not installed."}{" "}
          <Link to="/system?tab=updates#target-registry" data-control="sources.retrieval.configure-registry">
            {targets?.next_action?.label ?? "Install or refresh the official target registry"}
          </Link>
          {targets?.next_action?.where ? ` in ${targets.next_action.where}` : ""}. This fetches metadata only; it does not download CT volumes.
        </p>
      ) : !selectedScroll ? (
        <p className="ops-muted" data-control="sources.retrieval.context" role="status">
          Choose a physical scroll first. This planner will not silently fall back to a different scroll.
        </p>
      ) : prefill ? (
        <p className="ops-muted" data-control="sources.retrieval.prefill" role="status">
          Source and volume are prefilled from the authoritative target/source registries. ARGUS
          still proves the official identity before a download; it never treats a pasted ID as proof.
        </p>
      ) : null}
      {failure && !result ? <OpsFailure what="Retrieval planner" failure={failure} onRetry={() => void plan()} control="sources.retrieval" /> : null}
      {result ? (
        <div className="ops-note" data-control="sources.retrieval.result" aria-live="polite">
          <OpsBadge tone={result.state === "PLANNED" ? "ok" : result.state === "REFUSED" ? "bad" : "warn"}>
            {result.state.toLowerCase().replace(/_/g, " ")}
          </OpsBadge>{" "}
          {result.why ?? result.plan?.phase_means ?? "plan returned"}. Requests: {result.requests_made}; bytes fetched: {result.bytes_fetched}.
          {result.plan ? <span> Maximum for this check: {bytesLabel(result.plan.planned_upper_bound_bytes ?? 0)}.</span> : null}
          {mode === "expert" && result.plan ? <span> Store {result.plan.store_id ?? "unknown"} · acquisition {result.plan.acquisition_id ?? "unknown"}.</span> : null}
          <div data-control="sources.retrieval.destination">
            <strong>ARGUS will place it here:</strong> <code>{result.destination ?? "canonical acquisition directory unavailable"}</code>.
            The destination is chosen by the installation path contract, not by an arbitrary browser path.
          </div>
          <div>No download started. {mode === "expert" ? (result.execution ?? "No execution door is exposed from this page.") : "A real transfer remains a separate operator-approved action."}</div>
        </div>
      ) : null}
      {targetRegistryUnavailable ? null : (
        <AcquisitionExecutionForm scroll={scroll} url={url} volumeId={volumeId} phase={phase} prefill={prefill} mode={mode} storage={storage} />
      )}
      {mode === "expert" ? <>
        <OpsSource route="/api/retrieval/plan" field="state, plan, destination, requests_made, bytes_fetched" />
        <OpsSource route="/api/journey" field="steps, actions, agent_contract" />
      </> : null}
    </OpsSection>
  );
}

function AcquisitionExecutionForm({
  scroll, url, volumeId, phase, prefill, mode, storage,
}: {
  scroll: string; url: string; volumeId: string; phase: string; prefill: RetrievalPrefill | null;
  mode: ArgusMode; storage: IngestPlanPayload["storage"] | null;
}) {
  const [arrayPath, setArrayPath] = useState("");
  const [byteCeiling, setByteCeiling] = useState("");
  const [identityJson, setIdentityJson] = useState("");
  const [authorityJson, setAuthorityJson] = useState(prefill?.authorityJson ?? "{}");
  const [declaredJson, setDeclaredJson] = useState(prefill?.declaredJson ?? "{}");
  const [authorizationId, setAuthorizationId] = useState("");
  const [launchPacketJson, setLaunchPacketJson] = useState("");
  const [busy, setBusy] = useState(false);
  const [identityBusy, setIdentityBusy] = useState(false);
  const [identityResult, setIdentityResult] = useState<string | null>(null);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const missing = [
    !url.trim() ? "official mirror URL" : null,
    !scroll.trim() ? "physical scroll" : null,
    !volumeId.trim() ? "exact volume ID" : null,
    !identityJson.trim() ? "official identity envelope" : null,
    !authorizationId.trim() ? "launch authorization ID" : null,
    !launchPacketJson.trim() ? "launch packet" : null,
  ].filter((value): value is string => Boolean(value));
  const appliedPrefill = useRef<string | null>(null);

  useEffect(() => {
    if (!prefill || appliedPrefill.current === prefill.scroll) return;
    setAuthorityJson(prefill.authorityJson);
    setDeclaredJson(prefill.declaredJson);
    appliedPrefill.current = prefill.scroll;
  }, [prefill]);

  async function resolveOfficialIdentity() {
    setIdentityBusy(true);
    setIdentityResult(null);
    try {
      const declared = JSON.parse(declaredJson || "{}");
      if (!declared || Array.isArray(declared) || typeof declared !== "object") {
        throw new Error("declared metadata must be a JSON object");
      }
      if (!sessionIsOpen()) await openSession();
      const params = {
        scroll: scroll.trim(), volume_id: volumeId.trim(), source_url: url.trim(),
        array_path: arrayPath.trim() || "0", declared,
      };
      const planned = await planGoverned("identity.resolve", params);
      if (isRefusal(planned)) {
        setIdentityResult(`REFUSED - ${planned.reason}`);
        return;
      }
      if (planned.plan.ready !== true) {
        setIdentityResult(`REFUSED - ${String(planned.plan.why_not_ready ?? "identity plan is incomplete")}`);
        return;
      }
      const executed = await runGoverned("identity.resolve", params);
      if (isRefusal(executed)) {
        setIdentityResult(`REFUSED - ${executed.reason}`);
        return;
      }
      const inner = (executed.result?.result ?? {}) as {
        status?: string; why?: string; official_identity?: Record<string, unknown>;
      };
      if (inner.official_identity) setIdentityJson(JSON.stringify(inner.official_identity, null, 2));
      setIdentityResult(
        inner.status === "OK"
          ? "PROVEN - official catalogue and store metadata agree; the proof is filled below."
          : `${inner.status ?? "REFUSED"} - ${inner.why ?? "official metadata did not prove every field"}`,
      );
    } catch (error) {
      setIdentityResult(`REFUSED - ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setIdentityBusy(false);
    }
  }

  async function prepareAndRun() {
    setBusy(true);
    setResult(null);
    try {
      const officialIdentity = JSON.parse(identityJson || "{}");
      const targetAuthority = JSON.parse(authorityJson || "{}");
      const declared = JSON.parse(declaredJson || "{}");
      const launchPacket = JSON.parse(launchPacketJson || "{}");
      for (const [label, value] of [["official identity", officialIdentity], ["target authority", targetAuthority],
        ["declared metadata", declared], ["launch packet", launchPacket]] as const) {
        if (!value || Array.isArray(value) || typeof value !== "object") throw new Error(`${label} must be a JSON object`);
      }
      if (!sessionIsOpen()) await openSession();
      const params: Record<string, unknown> = {
        url: url.trim(), scroll: scroll.trim(), volume_id: volumeId.trim(), phase,
        array_path: arrayPath.trim(), official_identity: officialIdentity,
        target_authority: targetAuthority, declared, authorization_id: authorizationId.trim(),
        launch_packet: launchPacket,
      };
      if (byteCeiling.trim()) params.byte_ceiling = Number(byteCeiling);
      const prepared = await planGoverned("acquire.execute", params);
      if (isRefusal(prepared)) {
        setResult({ state: "REFUSED", why: prepared.reason, detail: prepared.detail });
        return;
      }
      if (prepared.plan.ready !== true) {
        setResult({ state: "REFUSED", why: "the governed acquisition plan is not executable", plan: prepared.plan });
        return;
      }
      const executed = await runGoverned("acquire.execute", {
        ...params, approved_plan_sha256: approvedHash(prepared),
      });
      setResult(isRefusal(executed) ? { state: "REFUSED", why: executed.reason, detail: executed.detail } :
        { state: "SUBMITTED", ...executed });
    } catch (error) {
      setResult({ state: "REFUSED", why: error instanceof Error ? error.message : String(error) });
    } finally {
      setBusy(false);
    }
  }

  if (mode === "guided") {
    const belowFloor = storage ? storage.headroom_bytes < 0 : false;
    return (
      <div className="ops-note" style={{ marginTop: 12 }} data-control="sources.retrieval.execute-guided">
        <strong>Download status: not started</strong>
        <p>
          ARGUS automatically checks identity, local coverage and destination without moving CT bytes. If a verified working region is still missing, ARGUS can
          prepare a bounded operator-approved transfer without touching the existing cache or renders.
        </p>
        {storage ? (
          <p role={belowFloor ? "alert" : "status"} data-control="sources.retrieval.storage-safety">
            <strong>Storage safety:</strong> {bytesLabel(storage.free_bytes)} free; the configured safety
            floor is {bytesLabel(storage.floor_bytes)}. {belowFloor
              ? "This machine is below the floor, so a CT transfer must not start yet."
              : `${bytesLabel(storage.headroom_bytes)} remains above the floor.`}
          </p>
        ) : null}
        <details>
          <summary>Why renders can exist before this route step closes</summary>
          <p>
            A render is a saved, hashable output from a bounded region. The route step asks a different
            question: whether ARGUS can prove the exact CT input needed for the next operation. The first
            can be true while the second still needs a receipt.
          </p>
        </details>
        <p>
          Raw identity envelopes, authorization packets and runner fields are hidden in Guided mode.
          Expert mode keeps them available for an operator audit; ordinary users do not need to edit JSON
          or code to understand this state.
        </p>
      </div>
    );
  }

  return (
    <details style={{ marginTop: 12 }} data-control="sources.retrieval.execute">
      <summary>Download into ARGUS (operator approval)</summary>
      <p className="ops-note">
        This remains a deliberate operator action. ARGUS requires the official identity envelope,
        launch authorization and packet, re-plans the exact request, and submits only that hash.
        A successful job is an acquisition receipt, not a detector or reading claim.
      </p>
      <p className="ops-note" data-control="sources.retrieval.execute.instructions">
        Start with <strong>Check source, size and destination</strong> above. If the plan is
        permitted, ARGUS names every remaining authorization item here instead of leaving the
        download control inert or asking you to guess where the files go.
      </p>
      <div className="ops-facts">
        <div><span className="ops-fact-label">Exact volume ID</span><strong>{volumeId || "not selected"}</strong></div>
        <label><span className="ops-fact-label">Array path (optional)</span><input value={arrayPath} onChange={(e) => setArrayPath(e.target.value)} placeholder="e.g. 0" /></label>
        <label><span className="ops-fact-label">Byte ceiling (optional)</span><input value={byteCeiling} onChange={(e) => setByteCeiling(e.target.value)} inputMode="numeric" placeholder="phase limit" /></label>
      </div>
      <button type="button" className="ops-button" onClick={() => void resolveOfficialIdentity()}
        disabled={identityBusy || !url.trim() || !scroll.trim() || !volumeId.trim()}>
        {identityBusy ? "Checking official metadata..." : "Prove official identity"}
      </button>
      {identityResult ? <p className="ops-note" data-control="sources.retrieval.identity-result" role="status">{identityResult}</p> : null}
      <label className="ops-field" style={{ marginTop: 8 }}>Official identity proof (filled above; advanced JSON)
        <textarea value={identityJson} onChange={(e) => setIdentityJson(e.target.value)} rows={4} spellCheck={false} placeholder='{"state":"PROVEN","contract":"...","resolved_identity":{},"identity_sha256":"..."}' />
      </label>
      <label className="ops-field" style={{ marginTop: 8 }}>Target authority (JSON, if applicable)
        <textarea value={authorityJson} onChange={(e) => setAuthorityJson(e.target.value)} rows={2} spellCheck={false} />
      </label>
      <label className="ops-field" style={{ marginTop: 8 }}>Declared metadata (JSON)
        <textarea value={declaredJson} onChange={(e) => setDeclaredJson(e.target.value)} rows={2} spellCheck={false} />
      </label>
      <label className="ops-field" style={{ marginTop: 8 }}>Launch authorization ID
        <input value={authorizationId} onChange={(e) => setAuthorizationId(e.target.value)} placeholder="issued authorization" />
      </label>
      <label className="ops-field" style={{ marginTop: 8 }}>Launch packet (JSON)
        <textarea value={launchPacketJson} onChange={(e) => setLaunchPacketJson(e.target.value)} rows={4} spellCheck={false} placeholder='{"runner_rel":"...","modules":[]}' />
      </label>
      <button type="button" className="ops-button" onClick={prepareAndRun}
        disabled={busy || !url.trim() || !scroll.trim() || !volumeId.trim() || !identityJson.trim() || !authorizationId.trim() || !launchPacketJson.trim()}>
        {busy ? "Preparing..." : "Approve plan and acquire"}
      </button>
      {missing.length ? (
        <p className="ops-muted" data-control="sources.retrieval.execute.missing" role="status">
          Waiting for: {missing.join(", ")}. This action is intentionally unavailable until the
          exact identity and authorization packet are present.
        </p>
      ) : null}
      {result ? <div className="ops-note" role="status" style={{ marginTop: 8, whiteSpace: "pre-wrap" }}>
        <strong>{String(result.state ?? "UNKNOWN")}</strong>{result.why ? ` - ${String(result.why)}` : ""}
        {result.state === "SUBMITTED" ? <div style={{ marginTop: 8 }}>
          <a href={`/workbench?scroll=${encodeURIComponent(scroll.trim())}`}>Open this material in Workbench</a>
          <span> after the governed acquisition job reports its receipt.</span>
        </div> : null}
      </div> : null}
    </details>
  );
}

function ProfilePlanner({ selectedScroll }: { selectedScroll: string | null }) {
  const [scroll, setScroll] = useState(selectedScroll?.trim() ?? "");
  useEffect(() => setScroll(selectedScroll?.trim() ?? ""), [selectedScroll]);
  const [segment, setSegment] = useState("");
  const [result, setResult] = useState<ProfilePlanPayload | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [busy, setBusy] = useState(false);

  const plan = async () => {
    setBusy(true);
    const q = new URLSearchParams({ scroll });
    if (segment.trim()) q.set("segment", segment.trim());
    const response = await getJson<ProfilePlanPayload>(`/api/profile/plan?${q}`);
    if (response.ok) {
      setResult(response.data);
      setFailure(null);
    } else {
      setFailure(response);
    }
    setBusy(false);
  };

  return (
    <OpsSection
      control="sources.profile"
      title="Profile this scroll and match its recipe"
      hint="Measures are identity-bound. ARGUS recommends only a qualified recipe within the declared domain ceiling; an unknown domain opens qualification instead of guessing."
      aside={<OpsBadge tone="info">bounded match</OpsBadge>}
    >
      <div className="ops-facts" data-control="sources.profile.form">
        <label>
          <span className="ops-fact-label">Physical scroll</span>
          <input value={scroll} onChange={(e) => setScroll(e.target.value)} placeholder="PHercParis4" />
        </label>
        <label>
          <span className="ops-fact-label">Segment (optional)</span>
          <input value={segment} onChange={(e) => setSegment(e.target.value)} placeholder="seg-A" />
        </label>
        <button type="button" className="ops-button" onClick={() => void plan()} disabled={busy || !scroll.trim()}>
          {busy ? "Checking…" : "Check profile handoff"}
        </button>
      </div>
      {failure && !result ? <OpsFailure what="Profile planner" failure={failure} onRetry={() => void plan()} control="sources.profile" /> : null}
      {result ? (
        <div className="ops-note" data-control="sources.profile.result" aria-live="polite">
          <OpsBadge tone={result.state === "RECOMMEND" ? "ok" : result.state === "REFUSED" ? "bad" : "warn"}>
            {result.state.toLowerCase().replace(/_/g, " ")}
          </OpsBadge>{" "}
          {result.why}
          {result.matching?.recipe_id ? <span> Recipe: {result.matching.recipe_id}.</span> : null}
          {result.profile_fingerprint ? <div className="ops-mono">Profile: {result.profile_fingerprint}</div> : null}
          {result.next ? <div>{result.next}</div> : null}
          {result.required?.length ? <div>Required receipt fields: {result.required.join(", ")}.</div> : null}
        </div>
      ) : null}
      <OpsSource route="/api/profile/plan" field="state, profile_fingerprint, matching, required" />
    </OpsSection>
  );
}

function ModelsTab() {
  return (
    <OpsSection
      control="sources.models"
      title="Model inventory"
      hint="A checkpoint is provenance too: which detector may be believed, on what evidence, and what it was exposed to. Every fact comes from the model registry receipt."
    >
      <Models embedded />
    </OpsSection>
  );
}

export default Sources;
