import type { ReceiptIndex } from "./receipts";
import { content, receipt } from "./receipts";
import type { Triple } from "./models";

export type ProjectionState =
  | "NO_LABELS"
  | "LABELS_PRESENT_BUT_PROJECTION_UNAVAILABLE"
  | "RAW_CT_PAIR_CONSTRUCTIBLE";

export const PROJECTION_MEANING: Record<ProjectionState, string> = {
  RAW_CT_PAIR_CONSTRUCTIBLE:
    "labels, raw CT and a coordinate map are all present for this same segment, so a labelled example can be built from it.",
  LABELS_PRESENT_BUT_PROJECTION_UNAVAILABLE:
    "ink labels exist but nothing can project them onto the volume — the coordinate map or the raw CT for THIS segment is missing. The labels are real; the pair is not.",
  NO_LABELS: "no ink labels for this segment. Raw material only.",
};

export const PROJECTION_TONE: Record<ProjectionState, "certified" | "blocked" | "refused"> =
  {
    RAW_CT_PAIR_CONSTRUCTIBLE: "certified",
    LABELS_PRESENT_BUT_PROJECTION_UNAVAILABLE: "blocked",
    NO_LABELS: "refused",
  };

export interface AssetRow {
  asset_kind: string;
  files: number;
  bytes: number;
  size_truncated?: boolean;
  grouping_key: string;
  grouping_key_is_verified: boolean;
  local_state?: string;
  cache_state?: string;
  declared_pitch_um?: Triple<number>;
  declared_volume?: Triple<string>;
  axis_order?: Triple<string>;
  shape?: Triple<unknown>;
  segment_id?: Triple<string>;
  scroll_id?: Triple<string>;
  scroll_path_hint?: Triple<string>;
  hashes?: Triple<unknown>;
  pitch_conflict?: Triple<unknown>;
  pyramid_levels?: Triple<unknown>;
}

export interface SegmentRow {
  labels: boolean;
  raw_ct: boolean;
  coordinate_map: boolean;
  supervision_mask: boolean;
  validation_mask: boolean;
  declared_pitch_um: number | number[] | null;
  label_pitch_um?: number | number[] | null;
  raw_ct_pitch_um?: number | number[] | null;
  pitch_ratio_label_to_raw?: number | null;
  pitch_scale_caveat?: string | null;
  declared_axis_order: string | null;
  raw_chunk_axis_caveat: string | null;
  projection_state: ProjectionState;
}

export interface Pairability {
  canonical_id: string;
  label_segments: string[];
  raw_ct_segments: string[];
  coordinate_map_segments: string[];
  per_segment: Record<string, SegmentRow>;
}

export interface AssetCatalogue {
  schema: string;
  utc: string;
  why: string;
  assets: AssetRow[];
  asset_count: number;
  asset_kind_counts: Record<string, number>;
  total_bytes_catalogued: number;
  total_files_catalogued: number;
  pairability: Record<string, Pairability>;
  canonical_id_coverage: {
    resolved: string[];
    refused: string[];
    canonical_set_size: number;
    note: string;
  };
  chunk_cache: Triple<{
    path?: string;
    walked?: boolean;
    known_shape?: { files: number; file_bytes: number; total_gib: number };
    status?: string;
  }>;
  unknowns?: unknown[];
  unknown_count?: number;
}

export interface DatasetScroll {
  scroll: string;
  eligibility?: Triple<{ first_letters_2027?: boolean; usd_if_first_letters?: number }>;
  prize_lane?: Triple<string[]>;
  volumes?: Triple<{ name: string; voxel_um?: number }[]> | { name: string; voxel_um?: number }[];
  best_resolution_um?: Triple<number>;
  local_state?: Triple<string> | string;
  segments?: { observations?: { date: string; count: number }[]; note?: string };
}

export interface DatasetRegistry {
  schema: string;
  utc: string;
  scrolls: Record<string, DatasetScroll>;
  scroll_count: number;
  eligible_count: number;
  canonical_id_gap?: Triple<{ canonical_ids: string[]; why_it_matters?: string }>;
}

export interface ScrollHoldings {
  scroll: string;
  inCanonicalSet: boolean;
  confusableWith: string | null;
  upstream: DatasetScroll | null;
  nothingLocal: boolean;
  declared: AssetRow[];
  inferred: AssetRow[];
  bytes: number;
  files: number;
  kinds: Record<string, number>;
  segments: { id: string; row: SegmentRow }[];
  constructible: number;
  labelsWithoutProjection: number;
  canonical: boolean;
  conflicts: { kind: string; t: Triple<unknown> }[];
}

export interface HoldingsView {
  present: boolean;
  canonicalKnown: boolean;
  canonicalCount: number;
  identityFromEngine: boolean;
  placeholders: string[];
  eligibleCount: number | null;
  relpath: string | null;
  sealed: boolean;
  utc: string | null;
  assetCount: number;
  totalBytes: number;
  totalFiles: number;
  kindCounts: Record<string, number>;
  scrolls: ScrollHoldings[];
  ungrouped: AssetRow[];
  refusedIds: string[];
  coverageMeasured: boolean;
  canonicalSetSize: number | null;
  coverageNote: string | null;
  chunkCache: AssetCatalogue["chunk_cache"] | null;
  unknownCount: number | null;
}

const UNGROUPED = "UNGROUPED";

export interface ScrollIds {
  schema: string;
  canonical: string[];
  canonical_count: number;
  aliases: Record<string, string>;
  placeholders: string[];
  confusable_pairs: [string, string][];
  index_age_s?: number;
}

export const SCROLL_IDS_ROUTE = "/api/scroll-ids";

export async function fetchScrollIds(): Promise<ScrollIds | null> {
  try {
    const r = await fetch(SCROLL_IDS_ROUTE);
    return r.ok ? ((await r.json()) as ScrollIds) : null;
  } catch {
    return null;
  }
}

export function confusableMap(pairs: [string, string][]): Map<string, string> {
  const out = new Map<string, string>();
  for (const [a, b] of pairs ?? []) {
    out.set(a, b);
    out.set(b, a);
  }
  return out;
}

export function buildHoldingsView(
  index: ReceiptIndex | null,
  ids: ScrollIds | null,
): HoldingsView {
  const cat = content<AssetCatalogue>(index, "asset_catalogue");
  const ds = content<DatasetRegistry>(index, "dataset_registry");
  const env = receipt(index, "asset_catalogue");
  const empty: HoldingsView = {
    present: false,
    canonicalKnown: false,
    canonicalCount: 0,
    identityFromEngine: false,
    placeholders: [],
    eligibleCount: null,
    relpath: env?.relpath ?? null,
    sealed: !!env?.sealed,
    utc: null,
    assetCount: 0,
    totalBytes: 0,
    totalFiles: 0,
    kindCounts: {},
    scrolls: [],
    ungrouped: [],
    refusedIds: [],
    coverageMeasured: false,
    canonicalSetSize: null,
    coverageNote: null,
    chunkCache: null,
    unknownCount: null,
  };
  if (!cat) return empty;

  const byScroll = new Map<string, AssetRow[]>();
  for (const a of cat.assets) {
    const k = a.grouping_key || UNGROUPED;
    const list = byScroll.get(k);
    if (list) list.push(a);
    else byScroll.set(k, [a]);
  }

  const resolved = new Set(cat.canonical_id_coverage?.resolved ?? []);

  const canonical = ids?.canonical ?? ds?.canonical_id_gap?.value?.canonical_ids ?? [];
  const canonicalSet = new Set(canonical);
  const confusable = confusableMap(ids?.confusable_pairs ?? []);
  const shelf = new Set<string>(canonical);
  for (const k of byScroll.keys()) if (k !== UNGROUPED) shelf.add(k);

  const scrolls: ScrollHoldings[] = [];
  for (const scroll of shelf) {
    const assets = byScroll.get(scroll) ?? [];
    const pair = cat.pairability?.[scroll];
    const segments = Object.entries(pair?.per_segment ?? {}).map(([id, row]) => ({
      id,
      row,
    }));
    segments.sort(
      (a, b) =>
        rank(a.row.projection_state) - rank(b.row.projection_state) ||
        a.id.localeCompare(b.id),
    );
    const kinds: Record<string, number> = {};
    for (const a of assets) kinds[a.asset_kind] = (kinds[a.asset_kind] ?? 0) + 1;
    scrolls.push({
      scroll,
      inCanonicalSet: canonicalSet.has(scroll),
      confusableWith: confusable.get(scroll) ?? null,
      upstream: ds?.scrolls?.[scroll] ?? null,
      nothingLocal: assets.length === 0,
      declared: assets.filter((a) => a.grouping_key_is_verified),
      inferred: assets.filter((a) => !a.grouping_key_is_verified),
      bytes: assets.reduce((n, a) => n + (a.bytes || 0), 0),
      files: assets.reduce((n, a) => n + (a.files || 0), 0),
      kinds,
      segments,
      constructible: segments.filter(
        (s) => s.row.projection_state === "RAW_CT_PAIR_CONSTRUCTIBLE",
      ).length,
      labelsWithoutProjection: segments.filter(
        (s) => s.row.projection_state === "LABELS_PRESENT_BUT_PROJECTION_UNAVAILABLE",
      ).length,
      canonical: resolved.has(scroll),
      conflicts: assets
        .filter((a) => a.pitch_conflict && a.pitch_conflict.value !== null)
        .map((a) => ({ kind: a.asset_kind, t: a.pitch_conflict as Triple<unknown> })),
    });
  }
  scrolls.sort(
    (a, b) =>
      b.constructible - a.constructible ||
      b.labelsWithoutProjection - a.labelsWithoutProjection ||
      b.bytes - a.bytes ||
      a.scroll.localeCompare(b.scroll),
  );

  return {
    present: true,
    canonicalKnown: canonical.length > 0,
    canonicalCount: canonical.length,
    identityFromEngine: !!ids,
    placeholders: ids?.placeholders ?? [],
    eligibleCount: ds?.eligible_count ?? null,
    relpath: env?.relpath ?? null,
    sealed: !!env?.sealed,
    utc: cat.utc ?? null,
    assetCount: cat.asset_count ?? cat.assets.length,
    totalBytes: cat.total_bytes_catalogued ?? 0,
    totalFiles: cat.total_files_catalogued ?? 0,
    kindCounts: cat.asset_kind_counts ?? {},
    scrolls,
    ungrouped: byScroll.get(UNGROUPED) ?? [],
    refusedIds: cat.canonical_id_coverage?.refused ?? [],
    coverageMeasured: !!cat.canonical_id_coverage,
    canonicalSetSize: cat.canonical_id_coverage?.canonical_set_size ?? null,
    coverageNote: cat.canonical_id_coverage?.note ?? null,
    chunkCache: cat.chunk_cache ?? null,
    unknownCount: cat.unknown_count ?? null,
  };
}

function rank(s: ProjectionState): number {
  return s === "RAW_CT_PAIR_CONSTRUCTIBLE"
    ? 0
    : s === "LABELS_PRESENT_BUT_PROJECTION_UNAVAILABLE"
      ? 1
      : 2;
}

export function pitch(v: number | number[] | null | undefined): {
  text: string;
  disagree: boolean;
} {
  if (v === null || v === undefined) return { text: "undeclared", disagree: false };
  const list = (Array.isArray(v) ? v : [v]).filter((x) => x !== null && x !== undefined);
  if (!list.length) return { text: "undeclared", disagree: false };
  const distinct = [...new Set(list.map(Number))];
  return {
    text: distinct.join(" / "),
    disagree: distinct.length > 1,
  };
}

export function gib(bytes: number): string {
  if (!bytes) return "0";
  const g = bytes / 1024 ** 3;
  if (g >= 1) return `${g.toFixed(1)} GiB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MiB`;
}
