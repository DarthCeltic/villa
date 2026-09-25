import { useEffect, useState } from "react";
import { getJson } from "./http";

export interface PublicRunStage {
  stage: string | null;
  state: string | null;
  seconds: number | null;
  uses: string | null;
}

export interface PublicRunResultClass {
  presentation: string | null;
  banner: string | null;
  public_name: string | null;
  target_class: string | null;
  exposure_basis: string | null;
  detector: string | null;
  detector_cross_scroll_qualified: boolean | null;
  acquisition: string | null;
  metric: string | null;
  score: number | null;
  may_claim_discovery: boolean | null;
  may_claim_ink_found: boolean | null;
  not_established: string[];
}

export interface PublicRun {
  target: string | null;
  scroll: string | null;
  run_id: string | null;
  outcome: string | null;
  started_utc: string | null;
  finished_utc: string | null;
  manifest_sha256: string | null;
  source_commit: string | null;
  crop_manifest_sha256: string | null;
  launch_ceiling: string | null;
  result_class: PublicRunResultClass;
  stages: PublicRunStage[];
  outputs: Record<string, { bytes: number | null; sha256: string }>;
  terms: Record<string, string>;
  limits: string[];
}

export interface PublicRunRow {
  run_id: string;
  collection?: string | null;
  schema?: string | null;
  category?: string | null;
  terminal?: string | null;
  sealed?: boolean;
  receipt_sha256?: string | null;
  public_run?: PublicRun | null;
}

export interface PublicRunsState {
  runs: PublicRunRow[];
  settled: boolean;
  failure: string | null;
}

export const PUBLIC_RUN_CATEGORY = "PUBLIC_PIPELINE_RUN";

export function publicRunsUrl(scroll?: string | null): string {
  const q = new URLSearchParams({ category: PUBLIC_RUN_CATEGORY, sealed: "exclude", limit: "50" });
  if (scroll) q.set("scroll", scroll);
  return `/api/evidence-index?${q.toString()}`;
}

export function onlyPublicRuns(rows: PublicRunRow[] | undefined | null): PublicRunRow[] {
  return (rows ?? [])
    .filter((r) => !!r.public_run)
    .sort((a, b) => String(b.public_run?.finished_utc ?? "").localeCompare(String(a.public_run?.finished_utc ?? "")));
}

export function scoreLine(rc: PublicRunResultClass): string | null {
  if (rc.score === null || rc.score === undefined) return null;
  const m = (rc.metric ?? "score").replace(/\s*\(.*\)\s*$/, "");
  return `${m} ${rc.score.toFixed(4)}`;
}

export function usePublicRuns(scroll?: string | null): PublicRunsState {
  const [state, setState] = useState<PublicRunsState>({ runs: [], settled: false, failure: null });
  useEffect(() => {
    let live = true;
    setState({ runs: [], settled: false, failure: null });
    void getJson<{ runs?: PublicRunRow[] }>(publicRunsUrl(scroll)).then((res) => {
      if (!live) return;
      if (res.ok) setState({ runs: onlyPublicRuns(res.data.runs), settled: true, failure: null });
      else setState({ runs: [], settled: true, failure: res.message });
    });
    return () => {
      live = false;
    };
  }, [scroll]);
  return state;
}
