import { useMemo } from "react";
import type { Certified, Operational, RunRecord } from "../api";
import { usePoll, type Polled } from "../lib/poll";
import type { ScrollIds } from "../lib/holdings";
import { classifyFixture } from "../lib/fixtures";
import { groupByDeclaredTarget, partitionRuns } from "../lib/runIdentity";
import { blockerLine, type Journey, type ScrollRecommendation, type ScrollStatus, type ScrollStatusBatch } from "../lib/scrollStatus";

export type ShelfScroll = {
  scroll: string;
  display: string;
  shelf: string;
  prizes: string[];
  acquisition_families: string[];
  local_data: "HELD" | "PARTIAL" | "NONE";
  furthest_stage: string;
  labelled: boolean;
  label_authority: string | null;
  label_representations: number;
  physical_segments: number;
  operator_fence: string | null;
  aliases: string[];
  published_upstream?: boolean;
  filters?: Record<string, boolean>;
};


export interface TargetRow {
  scroll: string;
  scan_id: string;
  prizes: string[];
  volume_store: string | null;
  store_state: string;
  pitch_um: number | null;
  energy_kev: number | null;
  operator_fence?: string | null;
  volumes_published_for_this_scroll?: string[];
}

export interface TargetsPayload {
  schema: string;
  available?: boolean;
  sets: Record<string, { count: number; scrolls: string[] }>;
  targets: TargetRow[];
  source?: { url?: string; sha256?: string };
  why_v2?: string;
  next_action?: { label?: string; where?: string; effect?: string };
}

export type TargetRegistryState = {
  state: "AVAILABLE" | "READING" | "UNAVAILABLE" | "FAILED";
  why: string | null;
  nextAction: TargetsPayload["next_action"] | null;
};

export function targetRegistryState(
  payload: TargetsPayload | null | undefined,
  loading: boolean,
  failure: { message: string } | null,
): TargetRegistryState {
  if (payload?.available === false) {
    return {
      state: "UNAVAILABLE",
      why: payload.why_v2 ?? "No official target registry is installed.",
      nextAction: payload.next_action ?? null,
    };
  }
  if (payload) return { state: "AVAILABLE", why: null, nextAction: null };
  if (loading) return { state: "READING", why: null, nextAction: null };
  return {
    state: "FAILED",
    why: failure?.message ?? "The target registry did not answer.",
    nextAction: null,
  };
}

export interface ScrollIndexRow {
  scroll: string;
  display?: string;
  physical_segments?: number;
  label_representations?: number;
  labelled?: number;
  aligned?: number;
  results_complete?: number;
  results_partial?: number;
  cached_ct_regions?: number;
  blocked_by?: string[];
}

export interface ScrollIndexPayload {
  scrolls: ScrollIndexRow[];
  totals?: Record<string, number>;
}

export interface ShelfPayload {
  scrolls?: ShelfScroll[];
  count?: number;
  error?: string;
  detail?: string;
}

export interface BoardsPayload {
  boards?: Record<string, { count: number; rows: { scroll: string; readiness: string }[] }>;
}
export interface EffortPayload {
  holdings_source?: { generated_utc?: string | null; present?: boolean };
  effort_source?: { present?: boolean };
  rows?: {
    scroll: string;
    holdings: { present: boolean; bytes: number; files: number; evidence?: string | null };
    effort: { findings_mentioning: number | null; latest_finding: string | null };
  }[];
  most_worked?: { scroll: string; findings_mentioning: number; lead_over_next: number; next: string | null } | null;
}


export type Lane = "FIRST_LETTERS" | "GRAND_PRIZE" | "PARIS4_TITLE" | "CONTROL_OR_DEV" | "UNCLASSIFIED";

export type LocalState = "MATERIAL_INDEXED" | "BYTES_CATALOGUED" | "NOTHING_INDEXED";

export interface Sourced<T> {
  value: T;
  route: string;
  field: string;
}

export interface ScrollObject {
  id: string;
  display: string;
  firstLetters: boolean;
  grandPrize: boolean;
  lane: Lane;
  acquisitions: {
    scanId: string | null;
    family: string | null;
    pitchUm: number | null;
    energyKev: number | null;
    storeState: string | null;
    volumeStore: string | null;
    publishedUpstream: boolean;
  }[];
  families: string[];
  familyLine: string;
  publishedUpstream: boolean;
  local: { state: LocalState; line: string; route: string };
  localDisagreement: string | null;
  surface: { line: string; stage: Stage; route: string };
  labels: { present: boolean; representations: number; authority: string | null; line: string };
  work: {
    results: number;
    latest: RunRecord | null;
    state: Operational | null;
    highest: Certified;
    sealed: boolean;
    line: string;
    runIds: string[];
  };
  blockedBy: string[];
  next: { label: string; to: string; why: string };
  status: ScrollStatus | null;
  fence: string | null;
  inCanonicalIds: boolean;
  aliases: string[];
  confusableWith: string | null;
  shelfRow: ShelfScroll | null;
  fixture: { fixture: boolean; why: string | null };
  titleLane: boolean;
  holdings: { bytes: number; files: number; catalogued: boolean; evidence: string | null } | null;
  effort: { findings: number; latest: string | null } | null;
}

export const STAGE_ORDER = [
  "NOTHING",
  "PUBLISHED_UPSTREAM",
  "MATERIAL_HELD",
  "GEOMETRY",
  "RENDERED",
  "LABELLED",
  "RESULT",
] as const;
export type Stage = (typeof STAGE_ORDER)[number];

export const STAGE_WORD: Record<Stage, string> = {
  NOTHING: "no material progress recorded",
  PUBLISHED_UPSTREAM: "published upstream, not indexed here",
  MATERIAL_HELD: "material indexed here",
  GEOMETRY: "surface traced",
  RENDERED: "surface rendered",
  LABELLED: "ink labels indexed",
  RESULT: "a result exists",
};

export interface Collection {
  lane: Lane;
  title: string;
  count: Sourced<number | null>;
  why: string;
  note: string | null;
  scrolls: ScrollObject[];
}

export interface Universe {
  loading: boolean;
  failures: string[];
  scrolls: ScrollObject[];
  byId: Map<string, ScrollObject>;
  collections: Collection[];
  counts: {
    registered: Sourced<number>;
    firstLetters: Sourced<number | null>;
    grandPrize: Sourced<number | null>;
    controlsAndDev: Sourced<number | null>;
    firstLettersWithLocalMaterial: Sourced<number | null>;
    grandPrizeWithCertifiedInk: Sourced<number | null>;
    firstLettersWithBytesHere: Sourced<number | null>;
  };
  titleLaneCount: Sourced<number> | null;
  mostWorked: { scroll: string; findings: number; lead: number; next: string | null } | null;
  held: { scroll: string; bytes: number; files: number; indexed: boolean }[];
  holdingsAsOf: string | null;
  identityFromEngine: boolean;
  missingFromCanonicalIds: string[];
  placeholders: string[];
  shelfServed: boolean;
  statusRead: boolean;
  statusFailed: boolean;
  journey: Journey | null;
  recommendations: {
    bestNext: ScrollRecommendation[];
    mostComplete: ScrollRecommendation[];
    method: string;
  };
  targetRegistry: TargetRegistryState;
  sources: { route: string; what: string; ok: boolean }[];
}

const FL = "FIRST_LETTERS";
const GP = "GRAND_PRIZE_2027";

function prizeKeys(sets: Record<string, { count: number; scrolls: string[] }> | undefined) {
  const keys = Object.keys(sets ?? {});
  const first = keys.find((k) => k.toUpperCase().startsWith("FIRST_LETTERS")) ?? FL;
  const grand = keys.find((k) => k.toUpperCase().startsWith("GRAND_PRIZE")) ?? GP;
  return { first, grand };
}


export function useScrollUniverse(runs: RunRecord[]): Universe {
  const targets: Polled<TargetsPayload> = usePoll<TargetsPayload>("/api/targets", {
    intervalMs: 120000,
  });
  const ids: Polled<ScrollIds> = usePoll<ScrollIds>("/api/scroll-ids", { intervalMs: 300000 });
  const index: Polled<ScrollIndexPayload> = usePoll<ScrollIndexPayload>("/api/scrolls", {
    intervalMs: 120000,
  });
  const shelf: Polled<ShelfPayload> = usePoll<ShelfPayload>("/api/shelf", { intervalMs: 300000 });
  const boards: Polled<BoardsPayload> = usePoll<BoardsPayload>("/api/prize-boards?route=PARIS4_TITLE", {
    intervalMs: 300000,
  });
  const effort: Polled<EffortPayload> = usePoll<EffortPayload>("/api/scroll-effort", { intervalMs: 300000 });
  const status: Polled<ScrollStatusBatch> = usePoll<ScrollStatusBatch>("/api/scroll_status", {
    intervalMs: 120000,
    timeoutMs: 60000,
  });

  return useMemo(
    () => buildUniverse(targets, ids, index, shelf, runs, boards, effort, status),
    [targets.data, targets.failure, ids.data, index.data, shelf.data, runs, targets, ids, index, shelf,
     boards.data, effort.data, boards, effort, status.data, status.failure, status],
  );
}

export function buildUniverse(
  targets: Polled<TargetsPayload>,
  ids: Polled<ScrollIds>,
  index: Polled<ScrollIndexPayload>,
  shelf: Polled<ShelfPayload>,
  runs: RunRecord[],
  boards: Polled<BoardsPayload>,
  effort: Polled<EffortPayload>,
  statusPoll: Polled<ScrollStatusBatch>,
): Universe {
  const targetRegistry = targetRegistryState(targets.data, targets.loading, targets.failure);
  const failures: string[] = [];
  if (statusPoll.failure) failures.push(`/api/scroll_status: ${statusPoll.failure.message}`);
  const statusByScroll = new Map<string, ScrollStatus>();
  for (const st of statusPoll.data?.scrolls ?? []) if (st.scroll) statusByScroll.set(st.scroll, st);
  if (boards.failure) failures.push(`/api/prize-boards: ${boards.failure.message}`);
  if (effort.failure) failures.push(`/api/scroll-effort: ${effort.failure.message}`);
  if (targets.failure) failures.push(`/api/targets: ${targets.failure.message}`);
  if (ids.failure) failures.push(`/api/scroll-ids: ${ids.failure.message}`);
  if (index.failure) failures.push(`/api/scrolls: ${index.failure.message}`);
  if (shelf.failure) failures.push(`/api/shelf: ${shelf.failure.message}`);
  if (shelf.data?.error) failures.push(`/api/shelf: ${shelf.data.detail ?? shelf.data.error}`);

  const sets = targetRegistry.state === "AVAILABLE" ? targets.data?.sets : undefined;
  const { first, grand } = prizeKeys(sets);
  const flSet = new Set(sets?.[first]?.scrolls ?? []);
  const gpSet = new Set(sets?.[grand]?.scrolls ?? []);
  const canonical = ids.data?.canonical ?? [];
  const placeholders = new Set(ids.data?.placeholders ?? []);
  const aliases = ids.data?.aliases ?? {};
  const confusable = new Map<string, string>();
  for (const pair of ids.data?.confusable_pairs ?? []) {
    const [a, b] = pair;
    if (a && b) {
      confusable.set(a, b);
      confusable.set(b, a);
    }
  }

  const rowsByScroll = new Map<string, TargetRow[]>();
  for (const t of targets.data?.targets ?? []) {
    if (!t.scroll) continue;
    const list = rowsByScroll.get(t.scroll);
    if (list) list.push(t);
    else rowsByScroll.set(t.scroll, [t]);
  }

  const indexByScroll = new Map<string, ScrollIndexRow>();
  for (const r of index.data?.scrolls ?? []) if (r.scroll) indexByScroll.set(r.scroll, r);

  const shelfByScroll = new Map<string, ShelfScroll>();
  for (const r of shelf.data?.scrolls ?? []) if (r.scroll) shelfByScroll.set(r.scroll, r);

  const universe = new Set<string>(canonical);
  for (const s of rowsByScroll.keys()) universe.add(s);
  for (const s of indexByScroll.keys()) universe.add(s);
  for (const s of shelfByScroll.keys()) universe.add(s);
  for (const p of placeholders) universe.delete(p);

  const missingFromCanonicalIds = [...rowsByScroll.keys()]
    .filter((s) => canonical.length > 0 && !canonical.includes(s))
    .sort();

  const work = workIndex(runs);

  const titleBoard = boards.data?.boards?.PARIS4_TITLE ?? null;
  const titleSet = new Set((titleBoard?.rows ?? []).map((r) => r.scroll));
  const effortByScroll = new Map((effort.data?.rows ?? []).map((r) => [r.scroll, r]));

  const scrolls: ScrollObject[] = [...universe]
    .sort((a, b) => a.localeCompare(b))
    .map((id) =>
      compose(id, {
        flSet,
        gpSet,
        rows: rowsByScroll.get(id) ?? [],
        idx: indexByScroll.get(id) ?? null,
        shelfRow: shelfByScroll.get(id) ?? null,
        canonical,
        aliasList: Object.entries(aliases)
          .filter(([, v]) => v === id)
          .map(([k]) => k),
        confusableWith: confusable.get(id) ?? null,
        work: work.get(id) ?? null,
        titleLane: titleSet.has(id),
        registryKnown: targetRegistry.state === "AVAILABLE",
        effortRow: effort.data ? effortByScroll.get(id) ?? null : undefined,
        status: statusByScroll.get(id) ?? null,
      }),
    );

  const byId = new Map(scrolls.map((s) => [s.id, s]));
  const flScrolls = scrolls.filter((s) => s.firstLetters);
  const gpScrolls = scrolls.filter((s) => s.grandPrize);
  const titleScrolls = scrolls.filter((s) => !s.firstLetters && s.titleLane);
  const rest = scrolls.filter((s) => s.lane === "CONTROL_OR_DEV");

  const known = targetRegistry.state === "AVAILABLE";
  const flCount = known ? sets?.[first]?.count ?? flScrolls.length : null;
  const gpCount = known ? sets?.[grand]?.count ?? gpScrolls.length : null;
  const unclassified = scrolls.filter((s) => s.lane === "UNCLASSIFIED");

  const counts: Universe["counts"] = {
    registered: {
      value: targetRegistry.state === "UNAVAILABLE" ? canonical.length : scrolls.length,
      route: targetRegistry.state === "UNAVAILABLE" ? "/api/scroll-ids" : "/api/scroll-ids + /api/targets",
      field: targetRegistry.state === "UNAVAILABLE"
        ? "canonical.length (prize eligibility not classified)"
        : "union of canonical and every declared prize target",
    },
    firstLetters: { value: flCount, route: "/api/targets", field: `sets.${first}.count` },
    grandPrize: { value: gpCount, route: "/api/targets", field: `sets.${grand}.count` },
    controlsAndDev: {
      value: known ? rest.length : null,
      route: "/api/scroll-ids + /api/targets",
      field: "registered identities with no First Letters target and not in the Paris 4 title lane",
    },
    firstLettersWithLocalMaterial: {
      value: known ? flScrolls.filter((s) => s.local.state === "MATERIAL_INDEXED").length : null,
      route: "/api/scrolls",
      field: "scrolls[] joined onto the First Letters set",
    },
    firstLettersWithBytesHere: {
      value: known ? flScrolls.filter((s) => s.local.state !== "NOTHING_INDEXED").length : null,
      route: "/api/scrolls + /api/scroll-effort",
      field: "indexed material OR bytes catalogued by the dataset registry",
    },
    grandPrizeWithCertifiedInk: {
      value: known ? gpScrolls.filter((s) => s.work.highest === "CERTIFIED_INK_CANDIDATE").length : null,
      route: "/api/observatory",
      field: "runs[].highest_certified_stage on runs declaring a Grand Prize scroll",
    },
  };

  const titleCollections: Collection[] = (titleScrolls.length || titleBoard
      ? [{
          lane: "PARIS4_TITLE" as const,
          title: "Paris 4 title",
          count: {
            value: titleBoard?.count ?? titleScrolls.length,
            route: "/api/prize-boards",
            field: "boards.PARIS4_TITLE.count",
          },
          why:
            "the official PHerc. Paris 4 title prize: a single-scroll prize with its own published " +
            "requirements, separate from First Letters and Grand Prize.",
          note: "Being in a prize route is not a result.",
          scrolls: titleScrolls,
        }]
      : []);
  const unknownCollection: Collection = {
    lane: "UNCLASSIFIED",
    title: "Known scrolls — prize eligibility unknown",
    count: {
      value: unclassified.length,
      route: "/api/scroll-ids",
      field: "canonical identities; prize classification needs the official target registry",
    },
    why:
      `${targetRegistry.why ?? "The official target registry is not installed."} ` +
      "These are the known scroll identities. None is being called eligible, ineligible, a control or a development scroll.",
    note: targetRegistry.nextAction?.label ? `Next: ${targetRegistry.nextAction.label}.` : null,
    scrolls: unclassified,
  };
  const collections: Collection[] = known ? [
    {
      lane: "FIRST_LETTERS",
      title: "First Letters targets",
      count: counts.firstLetters,
      why:
        "on the published First Letters list. These are the scrolls a First Letters " +
        "submission may be made about.",
      note:
        `${counts.firstLettersWithLocalMaterial.value} of ${counts.firstLetters.value} have any ` +
        "material indexed on this machine. This is a statement about local holdings, not about the scrolls.",
      scrolls: flScrolls,
    },
    {
      lane: "GRAND_PRIZE",
      title: "Grand Prize targets",
      count: counts.grandPrize,
      why:
        "on the Grand Prize list. A Grand Prize submission needs the complete recto surface " +
        "of a whole scroll, column-ordered -- a different requirement from First Letters, not " +
        "a harder version of it.",
      note:
        `Most of these ${counts.grandPrize.value} are ALSO among the ` +
        `${counts.firstLetters.value} First Letters targets above (that list excludes scrolls where letters were found): this is an overlap, not a ` +
        `second cohort, so the registered total stays ${counts.registered.value}. ` +
        `${counts.grandPrizeWithCertifiedInk.value} of ${counts.grandPrize.value} carry a ` +
        "certified ink result on any run that declares them.",
      scrolls: gpScrolls,
    },
    ...titleCollections,
    {
      lane: "CONTROL_OR_DEV",
      title: "Controls and development scrolls",
      count: counts.controlsAndDev,
      why:
        "not on the First Letters list. These are registered identities that are not " +
        "prize-eligible.",
      note: null,
      scrolls: rest,
    },
  ] : [unknownCollection, ...titleCollections];

  return {
    loading: targets.loading || ids.loading || index.loading,
    failures,
    scrolls,
    byId,
    collections,
    counts,
    titleLaneCount: titleBoard
      ? { value: titleBoard.count, route: "/api/prize-boards", field: "boards.PARIS4_TITLE.count" }
      : null,
    mostWorked: effort.data?.most_worked
      ? {
          scroll: effort.data.most_worked.scroll,
          findings: effort.data.most_worked.findings_mentioning,
          lead: effort.data.most_worked.lead_over_next,
          next: effort.data.most_worked.next,
        }
      : null,
    held: scrolls
      .filter((x) => x.local.state !== "NOTHING_INDEXED")
      .map((x) => ({
        scroll: x.id,
        bytes: x.holdings?.bytes ?? 0,
        files: x.holdings?.files ?? 0,
        indexed: x.local.state === "MATERIAL_INDEXED",
      }))
      .sort((a, b) => b.bytes - a.bytes),
    holdingsAsOf: effort.data?.holdings_source?.generated_utc ?? null,
    identityFromEngine: !!ids.data,
    missingFromCanonicalIds,
    placeholders: [...placeholders],
    shelfServed: !!shelf.data?.scrolls?.length,
    statusRead: !!statusPoll.data,
    statusFailed: !statusPoll.data && !!statusPoll.failure,
    journey: statusPoll.data?.journey ?? null,
    recommendations: {
      bestNext: statusPoll.data?.recommendations?.best_next_work ?? [],
      mostComplete: statusPoll.data?.recommendations?.most_complete ?? [],
      method: statusPoll.data?.recommendations?.method ?? "The status service has not ranked the scrolls yet.",
    },
    targetRegistry,
    sources: [
      { route: "/api/scroll_status", what: "the one per-scroll status: eight questions, sixteen steps, next action and blocker", ok: !!statusPoll.data },
      {
        route: "/api/targets",
        what: "the prize target sets and each scroll's acquisition",
        ok: targetRegistry.state === "AVAILABLE",
      },
      { route: "/api/scroll-ids", what: "canonical identity, aliases, placeholders, confusable pairs", ok: !!ids.data },
      { route: "/api/scrolls", what: "what material is indexed on this machine, per scroll", ok: !!index.data },
      { route: "/api/shelf", what: "the engine's own shelf verdict, where it is served", ok: !!shelf.data?.scrolls?.length },
      { route: "/api/observatory", what: "the runs that declare each scroll", ok: runs.length > 0 },
      { route: "/api/prize-boards", what: "the Paris 4 title lane", ok: !!boards.data },
      { route: "/api/scroll-effort", what: "bytes catalogued here, and findings that mention each scroll", ok: !!effort.data },
    ],
  };
}


interface Ingredients {
  flSet: Set<string>;
  gpSet: Set<string>;
  rows: TargetRow[];
  idx: ScrollIndexRow | null;
  shelfRow: ShelfScroll | null;
  canonical: string[];
  aliasList: string[];
  confusableWith: string | null;
  work: WorkFacts | null;
  titleLane: boolean;
  registryKnown: boolean;
  effortRow: NonNullable<EffortPayload["rows"]>[number] | null | undefined;
  status: ScrollStatus | null;
}

function compose(id: string, i: Ingredients): ScrollObject {
  const firstLetters = i.flSet.has(id);
  const grandPrize = i.gpSet.has(id);
  const acquisitions = i.rows.map((t) => ({
    scanId: t.scan_id ?? null,
    family:
      t.pitch_um !== null && t.pitch_um !== undefined && t.energy_kev !== null && t.energy_kev !== undefined
        ? `${t.pitch_um} µm / ${t.energy_kev} keV`
        : null,
    pitchUm: t.pitch_um ?? null,
    energyKev: t.energy_kev ?? null,
    storeState: t.store_state ?? null,
    volumeStore: t.volume_store ?? null,
    publishedUpstream: t.store_state === "FOUND",
  }));
  const families = [...new Set(acquisitions.map((a) => a.family).filter((f): f is string => !!f))];
  const publishedUpstream = acquisitions.some((a) => a.publishedUpstream);

  const segments = i.idx?.physical_segments ?? 0;
  const aligned = i.idx?.aligned ?? 0;
  const idxLabelReps = i.idx?.label_representations ?? 0;
  const inventoryLabelReps = i.shelfRow?.label_representations ?? 0;
  const labelledLocally = (i.idx?.labelled ?? 0) > 0 || idxLabelReps > 0;
  const labelledDeclared = labelledLocally || !!i.shelfRow?.labelled || inventoryLabelReps > 0;
  const ctRegions = i.idx?.cached_ct_regions ?? 0;
  const results = (i.idx?.results_complete ?? 0) + (i.idx?.results_partial ?? 0);
  const anyMaterial = segments > 0 || ctRegions > 0 || idxLabelReps > 0;

  const catalogued = !!i.effortRow?.holdings.present;
  const bytes = i.effortRow?.holdings.bytes ?? 0;
  const local: ScrollObject["local"] = anyMaterial
    ? {
        state: "MATERIAL_INDEXED",
        line:
          `${segments} segment${segments === 1 ? "" : "s"}` +
          (ctRegions ? `, ${ctRegions} cached CT region${ctRegions === 1 ? "" : "s"}` : "") +
          (idxLabelReps
            ? `, ${idxLabelReps} label representation${idxLabelReps === 1 ? "" : "s"}`
            : ""),
        route: "/api/scrolls",
      }
    : catalogued
    ? {
        state: "BYTES_CATALOGUED",
        line:
          `${fmtBytes(bytes)} catalogued on this machine (${i.effortRow?.holdings.files ?? 0} files) — ` +
          "not indexed by the scroll index, and no sealed store binds their identity",
        route: "/api/scroll-effort",
      }
    : i.effortRow === undefined
    ? {
        state: "NOTHING_INDEXED",
        line: "nothing indexed here; bytes on this machine not yet known (the holdings catalogue has not answered)",
        route: "/api/scroll-effort",
      }
    : {
        state: "NOTHING_INDEXED",
        line: publishedUpstream
          ? "nothing on this machine — the volume is published upstream and has not been brought in"
          : "nothing on this machine",
        route: "/api/scrolls",
      };

  const shelfSays = i.shelfRow?.local_data ?? null;
  const localDisagreement =
    shelfSays === "HELD" && local.state === "NOTHING_INDEXED"
      ? "/api/shelf reports this scroll as HELD while /api/scrolls indexes no material for it. " +
        "The shelf derives that from the publisher's bucket listing in some builds, so the " +
        "conservative answer is shown and the disagreement is reported rather than resolved."
      : shelfSays === "NONE" && local.state === "MATERIAL_INDEXED"
        ? "/api/shelf reports nothing held while /api/scrolls indexes material for this scroll."
        : null;

  const stage: Stage =
    results > 0
      ? "RESULT"
      : labelledLocally
        ? "LABELLED"
        : aligned > 0
          ? "RENDERED"
          : segments > 0
            ? "GEOMETRY"
            : anyMaterial
              ? "MATERIAL_HELD"
              : publishedUpstream
                ? "PUBLISHED_UPSTREAM"
                : "NOTHING";

  const surface: ScrollObject["surface"] = {
    stage,
    line:
      stage === "RENDERED"
        ? `${aligned} of ${segments} segment${segments === 1 ? "" : "s"} rendered`
        : stage === "GEOMETRY"
          ? `${segments} segment${segments === 1 ? "" : "s"} traced, none rendered`
          : STAGE_WORD[stage],
    route: "/api/scrolls",
  };

  const labels: ScrollObject["labels"] = {
    present: labelledDeclared,
    representations: idxLabelReps || inventoryLabelReps,
    authority: i.shelfRow?.label_authority ?? null,
    line: labelledLocally
      ? `${idxLabelReps} label representation${idxLabelReps === 1 ? "" : "s"} indexed here` +
        (i.shelfRow?.label_authority ? ` · authority ${i.shelfRow.label_authority}` : "")
      : labelledDeclared
        ? `${inventoryLabelReps || "some"} label representation${inventoryLabelReps === 1 ? "" : "s"} declared by the label-authority inventory, none indexed here` +
          (i.shelfRow?.label_authority ? ` · authority ${i.shelfRow.label_authority}` : "")
        : "no ink labels declared or indexed for this scroll",
  };

  const w = i.work;
  const workFacts: ScrollObject["work"] = {
    results: w?.results ?? 0,
    latest: w?.latest ?? null,
    state: w?.latest?.operational_state ?? null,
    highest: w?.highest ?? null,
    sealed: w?.sealed ?? false,
    runIds: (w?.runs ?? []).map((r) => r.run_id),
    line: w
      ? `${w.results} result${w.results === 1 ? "" : "s"} declare this scroll · latest ${
          w.latest?.operational_state.toLowerCase() ?? "unknown"
        }${
          w.highest
            ? ` · ${w.highest.replace(/^CERTIFIED_/, "").replace(/_/g, " ").toLowerCase()} operationally verified, not scientifically admissible`
            : " · nothing operationally verified"
        }`
      : "no run in the feed declares this scroll",
  };

  const fence = i.shelfRow?.operator_fence ?? i.rows.find((r) => r.operator_fence)?.operator_fence ?? null;

  return {
    id,
    display: i.shelfRow?.display ?? i.idx?.display ?? id,
    firstLetters,
    grandPrize,
    lane: firstLetters ? "FIRST_LETTERS" : grandPrize ? "GRAND_PRIZE" : i.titleLane ? "PARIS4_TITLE" : i.registryKnown ? "CONTROL_OR_DEV" : "UNCLASSIFIED",
    acquisitions,
    families,
    familyLine: families.length
      ? families.join(" · ")
      : i.registryKnown
        ? "no acquisition declared by the prize registry"
        : "acquisition unknown: the official target registry is not installed",
    publishedUpstream,
    local,
    localDisagreement,
    surface,
    labels,
    work: workFacts,
    blockedBy: i.idx?.blocked_by ?? [],
    next: nextFromStatus(id, i.status),
    status: i.status,
    fence,
    inCanonicalIds: i.canonical.includes(id),
    aliases: i.aliasList,
    confusableWith: i.confusableWith,
    shelfRow: i.shelfRow,
    fixture: classifyFixture(id),
    titleLane: i.titleLane,
    holdings: i.effortRow === undefined
      ? null
      : {
          bytes,
          files: i.effortRow?.holdings.files ?? 0,
          catalogued,
          evidence: i.effortRow?.holdings.evidence ?? null,
        },
    effort: i.effortRow && i.effortRow.effort.findings_mentioning !== null
      ? { findings: i.effortRow.effort.findings_mentioning, latest: i.effortRow.effort.latest_finding }
      : null,
  };
}

export function fmtBytes(n: number): string {
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)} MB`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)} KB`;
  return `${n} B`;
}

function nextFromStatus(id: string, status: ScrollStatus | null): ScrollObject["next"] {
  if (!status) {
    return {
      label: "Status not read yet",
      to: `/explore?tab=scrolls&detail=${encodeURIComponent(id)}`,
      why: "/api/scroll_status has not answered, so this scroll's next step is not known",
    };
  }
  const n = status.next_action;
  return { label: n.label, to: n.to, why: n.why };
}


interface WorkFacts {
  results: number;
  latest: RunRecord | null;
  highest: Certified;
  sealed: boolean;
  runs: RunRecord[];
}

const RANK: Record<string, number> = {
  CERTIFIED_INK_CANDIDATE: 3,
  CERTIFIED_2D: 2,
  CERTIFIED_SURFACE: 1,
};

export function workIndex(all: RunRecord[]): Map<string, WorkFacts> {
  const out = new Map<string, WorkFacts>();
  const { runs } = partitionRuns(all.filter((r) => !r.foreign));
  for (const g of groupByDeclaredTarget(runs)) {
    const target = g.target;
    if (!target) continue;
    const head = target.split("/")[0];
    const scroll = head && head.trim() ? head.trim() : null;
    if (!scroll) continue;
    const cur =
      out.get(scroll) ?? { results: 0, latest: null, highest: null, sealed: false, runs: [] };
    cur.results += 1;
    cur.runs.push(...g.records);
    if ((RANK[g.primary.highest_certified_stage ?? ""] ?? 0) > (RANK[cur.highest ?? ""] ?? 0))
      cur.highest = g.primary.highest_certified_stage;
    if (g.records.some((r) => r.blinding.sealed)) cur.sealed = true;
    if (!cur.latest || g.primary.run_id.localeCompare(cur.latest.run_id) > 0)
      cur.latest = g.primary;
    out.set(scroll, cur);
  }
  return out;
}


export type FilterKey =
  | "first_letters"
  | "grand_prize"
  | "local_material"
  | "published_upstream"
  | "has_geometry"
  | "has_labels"
  | "has_results";

export const FILTERS: { key: FilterKey; label: string; what: string }[] = [
  { key: "first_letters", label: "First Letters", what: "on the First Letters list" },
  { key: "grand_prize", label: "Grand Prize", what: "on the Grand Prize list" },
  { key: "local_material", label: "Material here", what: "has material indexed on this machine" },
  { key: "published_upstream", label: "Published upstream", what: "a volume is published upstream" },
  { key: "has_geometry", label: "Geometry", what: "at least one segment has been traced" },
  { key: "has_labels", label: "Ink labels", what: "carries indexed ink labels" },
  { key: "has_results", label: "Has results", what: "a run in the feed declares it" },
];

export function matches(s: ScrollObject, key: FilterKey): boolean {
  switch (key) {
    case "first_letters":
      return s.firstLetters;
    case "grand_prize":
      return s.grandPrize;
    case "local_material":
      return s.local.state === "MATERIAL_INDEXED";
    case "published_upstream":
      return s.publishedUpstream;
    case "has_geometry":
      return (
        s.surface.stage === "GEOMETRY" ||
        s.surface.stage === "RENDERED" ||
        s.surface.stage === "LABELLED" ||
        s.surface.stage === "RESULT"
      );
    case "has_labels":
      return s.labels.present;
    case "has_results":
      return s.work.results > 0;
  }
}

export function searchMatches(s: ScrollObject, needle: string): boolean {
  if (!needle) return true;
  const hay = [
    s.id,
    s.display,
    ...s.aliases,
    ...s.families,
    ...s.acquisitions.map((a) => a.scanId ?? ""),
    ...s.acquisitions.map((a) => a.volumeStore ?? ""),
  ]
    .join(" ")
    .toLowerCase();
  return hay.includes(needle.trim().toLowerCase());
}

export function filterEffect(
  shown: number,
  total: number,
  active: { label: string }[],
  needle: string,
): { active: boolean; line: string } {
  const by = [
    ...active.map((a) => a.label),
    ...(needle.trim() ? [`search “${needle.trim()}”`] : []),
  ];
  if (!by.length) {
    return { active: false, line: `Showing all ${total}. No filter is active.` };
  }
  return {
    active: true,
    line: `Showing ${shown} of ${total} — filtered by ${by.join(", ")}.`,
  };
}


export function scrollGate(s: ScrollObject): { operatorOnly: boolean; why: string; publicWhy: string } {
  const n = s.status?.next_action;
  if (!n) {
    const why = "The scroll's status has not been read, so who may take its next step is not known.";
    return { operatorOnly: false, why, publicWhy: why };
  }
  return { operatorOnly: n.operator_only, why: n.why, publicWhy: n.why };
}

export function scrollBlocker(s: ScrollObject, publicDemo: boolean): { text: string; full: string; source: string } {
  if (!s.status) {
    return { text: "status not read", full: "/api/scroll_status has not answered for this scroll.", source: "/api/scroll_status" };
  }
  const b = blockerLine(s.status, publicDemo);
  return { text: b.text, full: b.full, source: "/api/scroll_status blocker" };
}

export function stageWord(s: ScrollObject): string {
  const ix = STAGE_ORDER.indexOf(s.surface.stage);
  let w: string = STAGE_WORD[s.surface.stage];
  if (s.local.state === "BYTES_CATALOGUED" && ix < STAGE_ORDER.indexOf("MATERIAL_HELD")) {
    w = "bytes held here, not indexed";
  }
  if (s.work.results > 0 && s.surface.stage !== "RESULT") {
    w += ` · ${s.work.results} run result${s.work.results === 1 ? "" : "s"} recorded`;
  }
  return w;
}
