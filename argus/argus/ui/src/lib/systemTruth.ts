import { usePoll, type Polled } from "./poll";

export type ActivityState = "ACTIVE" | "NONE_OBSERVED" | "UNKNOWN";
export type ActivityVerdict = "ACTIVE" | "IDLE_OBSERVED" | "ACTIVITY_UNKNOWN";

export interface ActivityEvidence {
  witness: string;
  what?: string;
  root?: string;
  receipt?: string;
  age_s?: number;
  fresh?: boolean;
  note?: string;
  segment?: string;
  blocks_done?: number;
  blocks_total?: number;
  percent?: number;
  eta_min?: number;
  eta_utc?: string;
  elapsed_min?: number;
  current_orientation?: string;
  observed_utc?: string;
  observed_by?: string;
  used_mib?: number;
  total_mib?: number;
  util_pct?: number;
  cpu_pct?: number;
  running?: number;
  records?: number;
  abandoned_running_records?: number;
  newest_running_age_s?: number;
  [k: string]: unknown;
}

export interface ActivityClass {
  state: ActivityState;
  witnesses: string[];
  evidence: ActivityEvidence[];
  unknown_because: string[];
  means: string;
}

export interface RootStatus {
  root: string;
  readable: boolean;
  why: string | null;
}

export interface Activity {
  schema: string;
  observed_utc: string;
  verdict: ActivityVerdict;
  headline: string;
  active_classes: string[];
  unknown_classes: string[];
  classes: Record<string, ActivityClass>;
  roots: RootStatus[];
  unreadable_roots: RootStatus[];
  thresholds: Record<string, number>;
  rule: string;
  feed_coverage?: {
    observatory_feed_roots: number;
    activity_roots: number;
    note: string;
  };
}

export interface ChainStatus {
  status: "INTACT" | "BROKEN" | "EMPTY" | string;
  at?: number;
  why?: string;
  n?: number;
  head?: string;
}

export interface Integrity {
  schema: string;
  utc: string;
  chain: ChainStatus | null;
  historical?: { state?: string; [k: string]: unknown };
  current?: { state?: string; [k: string]: unknown };
  certification?: { historical_records?: string; new_records?: string; [k: string]: unknown };
  readable: boolean;
  why_unreadable: string | null;
  ledger: string;
  verified_by: string;
  means: Record<string, string>;
  consequence_of_broken: string;
}

export const ACTIVITY_LABEL: Record<string, string> = {
  ARGUS_SCIENTIFIC_JOB: "ARGUS scientific job",
  ACQUISITION_DOWNLOAD: "Acquisition download",
  ARCHIVE_STORAGE: "Archive storage",
  EXTERNAL_TRAINING: "External training",
  GPU_COMPUTE: "GPU compute",
  CPU_BACKGROUND: "CPU background",
};

export function useActivity(intervalMs = 10000): Polled<Activity> {
  return usePoll<Activity>("/api/activity", { intervalMs, staleAfterS: 45 });
}

export function useIntegrity(intervalMs = 30000): Polled<Integrity> {
  return usePoll<Integrity>("/api/integrity", { intervalMs, staleAfterS: 120 });
}

export function certificationIsSuppressed(i: Integrity | null): {
  suppressed: boolean;
  why: string | null;
} {
  if (!i || !i.readable || !i.chain) return { suppressed: false, why: null };
  if (i.current?.state) {
    if (i.current.state === "CURRENT_CHAIN_VERIFIED") {
      return { suppressed: i.certification?.new_records === "SUPPRESSED", why: (
        i.certification?.new_records === "SUPPRESSED"
          ? "the current v2 ledger is not authorising certification"
          : null)};
    }
    return {
      suppressed: true,
      why: `the current command ledger is ${i.current.state}; its records cannot attest what ran`,
    };
  }
  if (i.chain.status === "BROKEN") {
    return {
      suppressed: true,
      why:
        `the command ledger's hash chain is BROKEN at record ${i.chain.at ?? "?"}` +
        (i.chain.why ? ` (${i.chain.why})` : "") +
        ". Until it verifies, the record of what ran cannot be used as evidence.",
    };
  }
  return { suppressed: false, why: null };
}
