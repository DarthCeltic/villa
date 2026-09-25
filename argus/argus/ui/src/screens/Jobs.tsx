import { useId, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { usePoll } from "../lib/poll";
import type { Failure } from "../lib/http";
import { useActivity, type Activity, type ActivityClass } from "../lib/systemTruth";
import { ACTIVITY_LABEL } from "../lib/systemTruth";
import { useGates, type GatesPayload } from "../lib/argusTruth";
import { classifyFixture } from "../lib/fixtures";
import { routes } from "../lib/nav";
import { ScreenBrief, type BriefItem } from "../components/ScreenBrief";
import { useProductState, type SurfacesLite } from "../lib/productState";
import type { Polled } from "../lib/poll";
import { ScrollStatusBar } from "../components/ScrollStatusBar";
import "../theme/ops.css";
import "../theme/jobs.css";
import {
  OpsBadge,
  OpsButton,
  OpsDetails,
  OpsDisabled,
  OpsFact,
  OpsFacts,
  OpsFailure,
  OpsFixture,
  OpsHeadline,
  OpsItem,
  OpsPage,
  OpsQuote,
  OpsSection,
  OpsSource,
  OpsStat,
  OpsTable,
  OpsUnknown,
  agoLabel,
  elapsedLabel,
  type OpsTone,
} from "../components/OpsKit";
import { JobResumePanel } from "../components/JobResumePanel";


interface LedgerJob {
  job_id: string;
  action: string;
  actor: string;
  state: string;
  started_utc: string | null;
  finished_utc: string | null;
  terminal: boolean;
  cancellable_as_recorded: boolean;
  effective_cancellable: boolean;
  refusal: string | null;
  why: string | null;
  age_s: number | null;
}

interface Registry {
  module: string;
  count: number;
  mutating?: number;
  actions: string[];
  reached_by: string;
}

interface LedgerPayload {
  schema: string;
  utc: string;
  total: number;
  by_state: Record<string, number>;
  jobs: LedgerJob[];
  stuck: LedgerJob[];
  stuck_note: string;
  cancellable_but_finished: number;
  cancellable_note: string;
  registries: {
    command_surface: Registry;
    stage_surface: Registry;
    why_two_numbers: string;
  };
}

interface ChainProgress {
  state: string;
  why?: string;
  done?: number | null;
  total?: number | null;
}

interface ChainRoot {
  root: string;
  name: string;
  arm: string;
  stage: string;
  stage_source?: string;
  superseded?: boolean;
  superseded_note?: string;
  sealed?: boolean;
  scores?: unknown;
  score_note?: string;
  progress?: ChainProgress;
  artifacts?: unknown[];
}

interface ChainLofo {
  state: string;
  id: string;
  labels?: string;
  extent?: string;
  active_fold?: string;
  folds_total?: number;
  fold_sealed?: string[];
  progress?: ChainProgress;
  why_no_verdict?: string;
  held_out_source?: string;
  memory_pressure?: { available_gib?: number; commit_percent?: number };
}

interface ChainPayload {
  schema: string;
  utc: string;
  arms_expected: string[];
  roots: ChainRoot[];
  live_workers: { name?: string; arm?: string; pid?: number }[];
  single_gpu_owner: boolean;
  single_gpu_owner_means: string;
  current_arm: string | null;
  current_stage: string | null;
  lofo?: ChainLofo;
  resources?: {
    floors?: Record<string, number>;
    below_floor?: string[];
    ram_free_gib?: number;
  };
}

interface MonitorPayload {
  schema: string;
  utc: string;
  n_runs: number;
  active: { id?: string; state?: string; stage?: string }[];
  attention: { id?: string; state?: string; reason?: string }[];
  campaign: {
    stage?: string;
    lane?: string;
    done?: number;
    total?: number;
    note?: string;
    beat_utc?: string;
    age_s?: number;
    stale_after_s?: number;
    state?: string;
    state_basis?: string;
    progress_pct?: number;
  } | null;
  resources: {
    gpu: { used_mib: number; total_mib: number; util_pct: number; busy: boolean } | null;
    cpu_pct: number | null;
    ram: { used_gib: number; total_gib: number; pct: number } | null;
    disks: Record<string, { free_gib: number; total_gib: number }>;
  };
}

interface StoragePayload {
  assets: number;
  catalog_present: boolean;
  by_state: Record<string, { assets: number; gib: number }>;
  would_free_gib_if_evicted_now: number;
  pinned: number;
  blocked: Record<string, number>;
  restore_ready: number;
  evicted_without_restore_command: string[];
  mutations_are_not_here: string;
}


function ledgerTone(state: string): OpsTone {
  switch (state) {
    case "REFUSED":
    case "CANCELLED":
      return "bad";
    default:
      return "idle";
  }
}

function stageTone(stage: string): OpsTone {
  if (stage === "SEALED") return "info";
  if (stage === "SUPERSEDED") return "bad";
  return "idle";
}

function rootIsLive(r: ChainRoot, chain: ChainPayload): boolean {
  return (chain.live_workers ?? []).some((w) => w.name === r.name || (w.arm && w.arm === r.arm));
}

function campaignIsLive(c: MonitorPayload["campaign"]): boolean {
  if (!c || c.state !== "RUNNING") return false;
  if (typeof c.age_s !== "number" || typeof c.stale_after_s !== "number") return false;
  return c.age_s <= c.stale_after_s;
}

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

function activityTone(c: ActivityClass): OpsTone {
  if (c.state === "ACTIVE") return "info";
  if (c.state === "UNKNOWN") return "warn";
  return "idle";
}

function verdictTone(a: Activity | null): OpsTone {
  if (!a) return "idle";
  if (a.verdict === "ACTIVE") return "info";
  if (a.verdict === "IDLE_OBSERVED") return "idle";
  return "warn";
}


export function Jobs() {
  const ledger = usePoll<LedgerPayload>("/api/command-jobs?limit=200", { intervalMs: 20000 });
  const chain = usePoll<ChainPayload>("/api/chain", { intervalMs: 20000 });
  const monitor = usePoll<MonitorPayload>("/api/monitor", { intervalMs: 30000 });
  const storage = usePoll<StoragePayload>("/api/storage", { intervalMs: 60000 });
  const gates = useGates(30000);
  const activity = useActivity(20000);

  const [open, setOpen] = useState<Record<string, boolean>>({
    history: true,
    orphaned: true,
    refused: true,
  });
  const toggle = (k: string) => setOpen((o) => ({ ...o, [k]: !o[k] }));

  const surfaces = useProductState().surfaces;
  const leases = leaseFacts(surfaces);

  const live = liveLine(chain, leases);
  const history = historyLine(ledger, chain, monitor.data);
  const orphans = orphanLine(ledger, chain, monitor.data);
  const refusals = refusedLine(ledger, monitor.data);
  const brief = jobsBrief({ chain, ledger, leases, activity });

  return (
    <OpsPage control="jobs" title="Jobs">
      <ScrollStatusBar control="jobs.scroll.status" quietWithoutScroll />
      {}
      <p className="ops-note" data-control="jobs.monitor-link">
        <Link to="/m" data-control="jobs.open-monitor">Open the lightweight monitor</Link>: one live view of running work, for a second screen or a phone.
      </p>
      <ScreenBrief
        route="jobs"
        looking="Work on this machine that a live process backs, and every record that only claims to be running"
        ready={brief.ready}
        missing={brief.missing}
        about={
          <>
            What is running on this machine, what each thing is for, and what a person can do
            about it. Reading this page asks for nothing: every control that would change
            something is shown disabled with its reason. Open any line for its detail. LIVE means
            a process was observed: a science worker the chain found by process, or a lease whose
            recorded process tree is still alive. A ledger record, a run record or a heartbeat
            that says RUNNING with no process check is not live and is listed as orphaned or
            unverified.
          </>
        }
      />
      {
}
      <ul className="jobs-folds" data-control="jobs.summary" aria-label="Operations summary">
        <JobsFold
          id="live"
          label="Live work"
          line={live}
          open={!!open.live}
          onToggle={() => toggle("live")}
          primary
        >
          <LiveWork chain={chain.data} chainFailure={chain.failure} leases={leases} />
        </JobsFold>
        <JobsFold
          id="history"
          label="Historical jobs"
          line={history}
          open={!!open.history}
          onToggle={() => toggle("history")}
          primary
        >
          <NotInFlight chain={chain.data} monitor={monitor.data} />
          <Ledger
            data={ledger.data}
            failure={ledger.failure}
            onRetry={ledger.refresh}
          />
          <JobResumePanel />
        </JobsFold>
        <JobsFold
          id="orphaned"
          label="Orphaned or unverified"
          line={orphans}
          open={!!open.orphaned}
          onToggle={() => toggle("orphaned")}
          primary
        >
          <OrphanedRecords ledger={ledger.data} failure={ledger.failure} />
          <UnverifiedClaims chain={chain.data} monitor={monitor.data} />
        </JobsFold>
        <JobsFold
          id="refused"
          label="Refused requests"
          line={refusals}
          open={!!open.refused}
          onToggle={() => toggle("refused")}
          primary
        >
          <NeedsAttention
            ledger={ledger.data}
            failure={ledger.failure}
            monitor={monitor.data}
            gates={gates.data}
          />
        </JobsFold>
      </ul>

      <ActivityHeadline activity={activity.data} failure={activity.failure} />

      {
}
      <section className="jobs-context" data-control="jobs.context">
        <h2 className="jobs-context-title">Machine and pipeline context</h2>
        <ul className="jobs-folds">
          <JobsFold
            id="conveyor"
            label="Storage conveyor"
            line={conveyorLine(storage, activity.data)}
            open={!!open.conveyor}
            onToggle={() => toggle("conveyor")}
          >
            <ConveyorRow storage={storage.data} failure={storage.failure} activity={activity.data} />
          </JobsFold>
          <JobsFold
            id="resources"
            label="Machine resources"
            line={resourcesLine(monitor, chain.data, activity.data)}
            open={!!open.resources}
            onToggle={() => toggle("resources")}
          >
            <MachineResources monitor={monitor.data} failure={monitor.failure} chain={chain.data} />
          </JobsFold>
          <JobsFold
            id="gates"
            label="Stage gates"
            line={gatesLine(gates)}
            open={!!open.gates}
            onToggle={() => toggle("gates")}
          >
            <StageLadder gates={gates.data} failure={gates.failure} />
          </JobsFold>
          <JobsFold
            id="blindspots"
            label="Blind spots"
            line={blindLine(activity)}
            open={!!open.blindspots}
            onToggle={() => toggle("blindspots")}
          >
            <BlindSpots activity={activity.data} />
          </JobsFold>
          <JobsFold
            id="asking"
            label="Asking for something"
            line={askingLine(ledger)}
            open={!!open.asking}
            onToggle={() => toggle("asking")}
          >
            <AskingPanel registries={ledger.data?.registries} />
          </JobsFold>
        </ul>
      </section>

      <nav className="ops-controls" aria-label="Related screens">
        <Link className="ops-btn" to={routes.observatory()} data-control="jobs.nav.observatory">
          Observatory
        </Link>
        <Link className="ops-btn" to={routes.evidence()} data-control="jobs.nav.evidence">
          Evidence
        </Link>
        <Link className="ops-btn" to={routes.system()} data-control="jobs.nav.system">
          System
        </Link>
      </nav>
    </OpsPage>
  );
}


interface Line {
  tone: OpsTone;
  state: string;
  text: string;
  source: string;
}

function pending(routes: string[], failed: string[]): Line {
  return failed.length
    ? {
        tone: "bad",
        state: "could not read",
        text: `Could not read ${failed.join(" or ")}. Nothing is known here, which is not the same as nothing.`,
        source: routes.join(", "),
      }
    : {
        tone: "idle",
        state: "reading",
        text: `Waiting for ${routes.join(" and ")} to answer. Nothing is assumed meanwhile.`,
        source: routes.join(", "),
      };
}

function failedRoutes(pairs: [string, Polled<unknown>][]): string[] {
  return pairs.filter(([, p]) => !p.data && p.failure).map(([r]) => r);
}

interface LeaseFacts {
  read: boolean;
  why: string | null;
  leases: number | null;
  alive: string[];
}

function leaseFacts(p: Polled<SurfacesLite>): LeaseFacts {
  const r = p.data && !p.stale ? p.data.surfaces?.resources : null;
  if (!r) {
    return {
      read: false,
      why: p.failure ? `/api/surfaces: ${p.failure.message}` : "/api/surfaces has not answered",
      leases: null,
      alive: [],
    };
  }
  return {
    read: true,
    why: null,
    leases: typeof r.leases === "number" ? r.leases : null,
    alive: (r.orphan_jobs ?? []).filter((x): x is string => typeof x === "string"),
  };
}

function liveLine(chain: Polled<ChainPayload>, leases: LeaseFacts): Line {
  const source = "/api/chain → live_workers, /api/surfaces → resources.leases, orphan_jobs";
  if (!chain.data && !leases.read) {
    return pending(["/api/chain", "/api/surfaces"], failedRoutes([["/api/chain", chain]]));
  }
  const workers = chain.data?.live_workers ?? [];
  const pids = workers.map((w) => w.pid).filter((p): p is number => typeof p === "number");
  const parts: string[] = [];
  if (chain.data) {
    parts.push(
      workers.length
        ? `${plural(workers.length, "science worker")} found by process${pids.length ? ` (pid ${pids.join(", ")})` : ""}`
        : "no science worker in the process table",
    );
  } else {
    parts.push("/api/chain unread, so workers are unknown");
  }
  if (leases.read) {
    parts.push(
      leases.alive.length
        ? `${plural(leases.alive.length, "lease")} with a live process tree: ${leases.alive.join(", ")}`
        : `${leases.leases ?? "an unknown number of"} lease(s), none with a live process`,
    );
  } else {
    parts.push("leases unread");
  }
  const n = workers.length + leases.alive.length;
  const unknown = !chain.data || !leases.read;
  return {
    tone: n > 0 ? "info" : unknown ? "warn" : "idle",
    state: n > 0 ? `${n} live` : unknown ? "partly unknown" : "none live",
    text: parts.join(" · "),
    source,
  };
}

function jobsBrief({
  chain,
  ledger,
  leases,
  activity,
}: {
  chain: Polled<ChainPayload>;
  ledger: Polled<LedgerPayload>;
  leases: LeaseFacts;
  activity: Polled<Activity>;
}): { ready: BriefItem[]; missing: BriefItem[] } {
  const ready: BriefItem[] = [];
  const missing: BriefItem[] = [];
  const workers = chain.data ? chain.data.live_workers.length : null;
  const live = (workers ?? 0) + leases.alive.length;
  if (workers === null || !leases.read) {
    missing.push({ text: "live process check partly unread", tone: "unknown" });
  }
  ready.push({
    text: live ? `${live} live, process-backed` : "nothing running (process-checked)",
    tone: live ? "info" : "idle",
  });
  if (ledger.data) {
    const finished = ledger.data.jobs.filter((j) => j.terminal && j.state !== "REFUSED").length;
    ready.push({ text: `${finished} finished records`, tone: "idle" });
    const stuck = ledger.data.stuck?.length ?? 0;
    if (stuck) missing.push({ text: `${stuck} orphaned: recorded RUNNING, no process`, tone: "warn" });
    const refused = ledger.data.by_state?.REFUSED ?? 0;
    if (refused) missing.push({ text: `${refused} refused requests`, tone: "bad" });
  } else {
    missing.push({ text: ledger.failure ? "job ledger could not be read" : "job ledger still reading", tone: "unknown" });
  }
  const lofo = chain.data?.lofo;
  if (lofo && lofo.state === "RUNNING" && !chain.data?.live_workers.length) {
    missing.push({ text: `${lofo.id} claims RUNNING with no process`, tone: "warn" });
  }
  const unobservable = activity.data ? activity.data.unknown_classes.length : 0;
  if (unobservable) missing.push({ text: `${unobservable} activity classes unobservable`, tone: "unknown" });
  return { ready, missing };
}

function historyLine(
  ledger: Polled<LedgerPayload>,
  chain: Polled<ChainPayload>,
  monitor: MonitorPayload | null,
): Line {
  const source = "/api/command-jobs, /api/chain";
  if (!ledger.data) {
    return pending(["/api/command-jobs"], failedRoutes([["/api/command-jobs", ledger]]));
  }
  const d = ledger.data;
  const finished = d.jobs.filter((j) => j.terminal && j.state !== "REFUSED");
  const byState: Record<string, number> = {};
  for (const j of finished) byState[j.state] = (byState[j.state] ?? 0) + 1;
  const breakdown = Object.entries(byState)
    .map(([s, n]) => `${n} ${s.toLowerCase()}`)
    .join(", ");
  const parts = [
    `${plural(finished.length, "finished ledger record")}${breakdown ? ` (${breakdown})` : ""}`,
  ];
  if (d.jobs.length < d.total) parts.push(`counted among ${d.jobs.length} fetched of ${d.total} recorded`);
  if (chain.data) {
    const notLive = chain.data.roots.filter((r) => !rootIsLive(r, chain.data!)).length;
    parts.push(`${plural(notLive, "chain pass", "chain passes")} not in flight`);
  } else {
    parts.push(chain.failure ? "could not read /api/chain" : "chain passes still reading");
  }
  if (monitor?.campaign && !campaignIsLive(monitor.campaign)) {
    parts.push(`campaign heartbeat ${(monitor.campaign.state ?? "state not declared").toLowerCase()}`);
  }
  return {
    tone: "idle",
    state: finished.length ? `${finished.length} finished` : "none finished",
    text: parts.join(" · "),
    source,
  };
}

function orphanLine(
  ledger: Polled<LedgerPayload>,
  chain: Polled<ChainPayload>,
  monitor: MonitorPayload | null,
): Line {
  const source = "/api/command-jobs → stuck[], /api/chain → lofo, /api/monitor → active[]";
  if (!ledger.data) {
    return pending(["/api/command-jobs"], failedRoutes([["/api/command-jobs", ledger]]));
  }
  const stuck = ledger.data.stuck ?? [];
  const lofo = chain.data?.lofo;
  const lofoClaim = lofo && lofo.state === "RUNNING" && !chain.data?.live_workers.length ? 1 : 0;
  const claimed = monitor?.active?.length ?? 0;
  const parts: string[] = [];
  if (stuck.length) {
    const actions = Array.from(new Set(stuck.map((j) => j.action))).join(", ");
    parts.push(`${plural(stuck.length, "ledger record")} recorded RUNNING with no process (${actions})`);
  }
  if (lofoClaim && lofo) parts.push(`${lofo.id} declared RUNNING by the chain with no process behind it`);
  if (claimed) parts.push(`${plural(claimed, "run record")} say RUNNING with no process check`);
  const n = stuck.length + lofoClaim + claimed;
  if (!n) {
    return { tone: "idle", state: "none", text: "No record claims RUNNING without a process behind it.", source };
  }
  return {
    tone: "warn",
    state: `${n} orphaned or unverified`,
    text: `${parts.join(" · ")} · bookkeeping, not work`,
    source,
  };
}

function refusedLine(ledger: Polled<LedgerPayload>, monitor: MonitorPayload | null): Line {
  const source = "/api/command-jobs → by_state.REFUSED, jobs[state=REFUSED]";
  if (!ledger.data) {
    return pending(["/api/command-jobs"], failedRoutes([["/api/command-jobs", ledger]]));
  }
  const d = ledger.data;
  const recorded = d.by_state?.REFUSED ?? 0;
  const fetched = d.jobs.filter((j) => j.state === "REFUSED");
  const flagged = monitor?.attention?.length ?? 0;
  const flaggedText = flagged ? ` · ${plural(flagged, "run")} the monitor flags` : "";
  if (recorded === 0 && fetched.length === 0) {
    return {
      tone: flagged ? "warn" : "idle",
      state: "none",
      text: `The ledger records no refused request${flaggedText}.`,
      source,
    };
  }
  const newest = [...fetched].sort(
    (x, y) => (x.age_s ?? Number.POSITIVE_INFINITY) - (y.age_s ?? Number.POSITIVE_INFINITY),
  )[0];
  const count = Math.max(recorded, fetched.length);
  return {
    tone: "bad",
    state: `${count} refused`,
    text: newest
      ? `most recent: ${newest.action}, ${agoLabel(newest.age_s)} — ${newest.refusal ?? "no refusal code"}: ${newest.why ?? "no reason recorded"}${flaggedText}`
      : `none of the ${recorded} refused record(s) is among the fetched rows, so no reason can be quoted${flaggedText}`,
    source,
  };
}

function conveyorLine(storage: Polled<StoragePayload>, activity: Activity | null): Line {
  const source = "/api/storage, /api/activity → ARCHIVE_STORAGE";
  if (!storage.data) return pending(["/api/storage"], failedRoutes([["/api/storage", storage]]));
  const s = storage.data;
  const archive = activity?.classes?.ARCHIVE_STORAGE ?? null;
  const liveness = !archive
    ? "liveness not read"
    : archive.state === "ACTIVE"
      ? "observed active"
      : archive.state === "UNKNOWN"
        ? "liveness unobservable"
        : "no activity observed";
  const blocked = Object.values(s.blocked ?? {}).reduce((x, y) => x + y, 0);
  return {
    tone: blocked ? "warn" : "idle",
    state: s.catalog_present ? `${s.assets} assets` : "no catalog",
    text: `${liveness} · ${s.pinned} pinned · ${blocked} blocked with no verified remote copy · no control here touches it`,
    source,
  };
}

function resourcesLine(monitor: Polled<MonitorPayload>, chain: ChainPayload | null, activity: Activity | null): Line {
  const source = "/api/monitor → resources, /api/chain → resources.below_floor";
  if (!monitor.data) return pending(["/api/monitor"], failedRoutes([["/api/monitor", monitor]]));
  const r = monitor.data.resources;
  const below = chain?.resources?.below_floor ?? null;
  const parts = [
    r.gpu ? `accelerator ${r.gpu.used_mib} / ${r.gpu.total_mib} MiB` : "accelerator unknown",
    r.cpu_pct === null ? "CPU unknown" : `CPU ${r.cpu_pct}%`,
    r.ram ? `memory ${r.ram.used_gib} / ${r.ram.total_gib} GiB` : "memory unknown",
    below === null
      ? "floors not read"
      : below.length
        ? `below floor: ${below.join(", ")}`
        : "no declared floor breached",
  ];
  const busy = activity
    ? Object.entries(activity.classes).filter(([, c]) => c.state === "ACTIVE").map(([k]) => (ACTIVITY_LABEL[k] ?? k).toLowerCase())
    : [];
  if (busy.length) parts.push(`machine busy: ${busy.join(", ")} (not attributed to any job)`);
  return {
    tone: below && below.length ? "bad" : "idle",
    state: below === null ? "floors unknown" : below.length ? `${below.length} below floor` : "above floors",
    text: `${parts.join(" · ")} · whole machine, not any one pass`,
    source,
  };
}

function gatesLine(gates: Polled<GatesPayload>): Line {
  const source = "/api/gates";
  if (!gates.data) return pending(["/api/gates"], failedRoutes([["/api/gates", gates]]));
  const g = gates.data;
  return {
    tone: g.first_closed ? "warn" : "idle",
    state: `${g.n_open} of ${g.n_total} open`,
    text: g.first_closed
      ? `${g.first_closed} is the gate holding things up · ${g.next_action ?? "the ladder declares no next action"}`
      : "no gate is shut",
    source,
  };
}

function blindLine(activity: Polled<Activity>): Line {
  const source = "/api/activity → unknown_classes";
  if (!activity.data) return pending(["/api/activity"], failedRoutes([["/api/activity", activity]]));
  const a = activity.data;
  const n = a.unknown_classes.length;
  return {
    tone: n ? "warn" : "idle",
    state: `${n} of ${Object.keys(a.classes).length} unobservable`,
    text: "plus host processes and worker logs, which no route here can see at all",
    source,
  };
}

function askingLine(ledger: Polled<LedgerPayload>): Line {
  const source = "/api/command-jobs → registries";
  if (!ledger.data) return pending(["/api/command-jobs"], failedRoutes([["/api/command-jobs", ledger]]));
  const r = ledger.data.registries;
  if (!r) {
    return { tone: "idle", state: "not declared", text: "the ledger payload carries no registries", source };
  }
  return {
    tone: "idle",
    state: "no submit here",
    text: `command surface ${r.command_surface.count} actions · stage surface ${r.stage_surface.count} actions · reached through System`,
    source,
  };
}

function JobsFold({
  id,
  label,
  line,
  open,
  onToggle,
  primary,
  children,
}: {
  id: string;
  label: string;
  line: Line;
  open: boolean;
  onToggle: () => void;
  primary?: boolean;
  children: ReactNode;
}) {
  const bodyId = useId();
  return (
    <li className="jobs-fold" data-tone={line.tone} data-open={open ? "true" : "false"}>
      <button
        type="button"
        className="jobs-line"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={onToggle}
        title={`Read from ${line.source}`}
        data-control={`jobs.summary.${id}`}
        data-summary-line={primary ? "true" : undefined}
      >
        <span className="jobs-line-label">{label}</span>
        <span className="jobs-line-state">
          <OpsBadge tone={line.tone}>{line.state}</OpsBadge>
        </span>
        <span className="jobs-line-text">{line.text}</span>
        <span className="jobs-line-cue" aria-hidden>
          {open ? "hide" : "details"}
        </span>
      </button>
      <div id={bodyId} className="jobs-fold-body" hidden={!open}>
        {children}
      </div>
    </li>
  );
}

function CapNote({
  cap,
  total,
  all,
  onToggle,
  noun,
  control,
}: {
  cap: number;
  total: number;
  all: boolean;
  onToggle: () => void;
  noun: string;
  control: string;
}) {
  if (total <= cap) return null;
  return (
    <div className="ops-filterbar">
      <span className="ops-note">
        {all
          ? `All ${total} ${noun} shown.`
          : `Showing ${cap} of ${total} ${noun}; ${total - cap} hidden, not discarded.`}
      </span>
      <OpsButton onClick={onToggle} control={control}>
        {all ? `Show the first ${cap} only` : `Show all ${total}`}
      </OpsButton>
    </div>
  );
}


function ActivityHeadline({
  activity,
  failure,
}: {
  activity: Activity | null;
  failure: Failure | null;
}) {
  if (!activity) {
    return failure ? (
      <OpsFailure what="Is anything running" failure={failure} control="jobs.activity" />
    ) : (
      <OpsHeadline
        tone="idle"
        what="Reading the activity derivation…"
        detail="Nothing is being assumed in the meantime. An empty console is not an idle machine."
        control="jobs.headline"
      />
    );
  }
  const busy = activity.verdict !== "IDLE_OBSERVED" && activity.verdict !== "ACTIVITY_UNKNOWN";
  const tone = verdictTone(activity) === "info" ? "idle" : verdictTone(activity);
  return (
    <OpsHeadline
      tone={tone}
      what={`Machine activity, not jobs: ${activity.headline.replace(/^Work in flight:\s*/i, busy ? "busy with " : "")}`}
      control="jobs.headline"
      detail={
        <>
          Load on the whole machine, observed by class. It is not attributed to any ARGUS job; Live
          work above counts only what a process backs. {activity.rule}
          <OpsSource route="/api/activity" field="verdict, headline, rule" />
        </>
      }
    />
  );
}


function LiveWork({
  chain,
  chainFailure,
  leases,
}: {
  chain: ChainPayload | null;
  chainFailure: Failure | null;
  leases: LeaseFacts;
}) {
  const lofo = chain?.lofo ?? null;
  const confirmed = (chain?.live_workers ?? []).length > 0;
  const liveRoots = chain
    ? chain.roots.filter((r) => !r.superseded && r.stage !== "SUPERSEDED" && rootIsLive(r, chain))
    : [];

  return (
    <OpsSection
      control="jobs.live"
      title="Live work"
      hint="Only work a process backs: a science worker found in the process table, or an owned-process-tree lease whose recorded pids are still alive. Records, heartbeats and machine load that say busy without a process check are listed under Orphaned or unverified, and machine load under Machine resources."
      aside={
        chain ? (
          <OpsBadge tone={chain.single_gpu_owner ? "idle" : "warn"} control="jobs.live.gpu-owner">
            {chain.single_gpu_owner ? "one GPU owner" : "more than one GPU owner"}
          </OpsBadge>
        ) : null
      }
    >
      <ul className="ops-list">
        {!chain ? (
          chainFailure ? (
            <OpsFailure what="The science chain" failure={chainFailure} control="jobs.live.chain" />
          ) : (
            <OpsUnknown what="The science chain" why="/api/chain has not answered yet. Nothing is being substituted for it." />
          )
        ) : (
          <>
            {lofo && lofo.state === "RUNNING" && confirmed ? <LofoRow lofo={lofo} confirmed /> : null}
            {liveRoots.map((r) => (
              <ArmRow key={r.root} r={r} live />
            ))}
            {chain.live_workers.map((w, i) => (
              <OpsItem
                key={`${w.pid ?? i}`}
                control={`jobs.live.worker.${w.pid ?? i}`}
                tone="info"
                title={w.name ?? `worker ${w.pid ?? i}`}
                state="process observed"
              >
                <div className="ops-item-body">
                  {w.arm ? <>Arm <span className="ops-mono">{w.arm}</span>. </> : null}
                  {w.pid ? <>pid <span className="ops-mono">{w.pid}</span>, </> : null}
                  found in the process table by the chain status probe.
                </div>
              </OpsItem>
            ))}
            {chain.live_workers.length === 0 ? (
              <OpsItem tone="idle" control="jobs.live.workers" title="No science worker is in the process table" state="none">
                <div className="ops-item-body">
                  {chain.single_gpu_owner_means} A pass launched by hand outside the worker registry in{" "}
                  <span className="ops-mono">argus/service/worker_logs.json</span> may still be invisible to
                  this probe, so this is a statement about what the probe can see.
                </div>
              </OpsItem>
            ) : null}
          </>
        )}

        {!leases.read ? (
          <OpsUnknown what="Owned process tree leases" why={leases.why ?? "not read"} />
        ) : leases.alive.length ? (
          leases.alive.map((job) => (
            <OpsItem key={job} control={`jobs.live.lease.${job}`} tone="info" title={job} state="lease, process alive">
              <div className="ops-item-body">
                A lease under owned_process_tree records this job's pids, and at least one of them is
                still alive. The audit cannot tell a running job from one whose shell exited and left
                its workers behind; Machine resources shows the memory they hold.
              </div>
            </OpsItem>
          ))
        ) : (
          <OpsItem tone="idle" control="jobs.live.leases" title={`${leases.leases ?? "Unknown"} lease(s) on this machine, none with a live process`} state="none" />
        )}
      </ul>
      <OpsSource route="/api/chain" field="live_workers[], roots[], lofo, single_gpu_owner" />
      <OpsSource route="/api/surfaces" field="resources.leases, resources.orphan_jobs (owned_process_tree.audit)" />
    </OpsSection>
  );
}

function UnverifiedClaims({ chain, monitor }: { chain: ChainPayload | null; monitor: MonitorPayload | null }) {
  const lofo = chain?.lofo ?? null;
  const lofoClaim = !!lofo && lofo.state === "RUNNING" && !(chain?.live_workers ?? []).length;
  const runs = monitor?.active ?? [];
  const campaign = monitor?.campaign ?? null;
  if (!lofoClaim && !runs.length && !(campaign && campaignIsLive(campaign))) return null;
  return (
    <OpsSection
      control="jobs.unverified"
      title="Claims of running with no process check"
      hint="Each of these says RUNNING. None of them is backed by a process this page can see, so none is counted as live."
    >
      <ul className="ops-list">
        {lofoClaim && lofo ? <LofoRow lofo={lofo} confirmed={false} /> : null}
        {runs.map((run, i) => (
          <OpsItem
            key={`${run.id ?? i}`}
            control={`jobs.unverified.run.${run.id ?? i}`}
            tone="warn"
            title={run.id ?? "run id not recorded"}
            state="orphaned or unverified"
          >
            <div className="ops-item-body">
              The run feed records it as {(run.state ?? "state not declared").toLowerCase()}
              {run.stage ? <>, at stage <span className="ops-mono">{run.stage}</span></> : null}. No process
              check backs the record.
            </div>
            <OpsSource route="/api/monitor" field="active[]" />
          </OpsItem>
        ))}
        {campaign && campaignIsLive(campaign) ? <CampaignRow campaign={campaign} /> : null}
      </ul>
    </OpsSection>
  );
}

function NotInFlight({
  chain,
  monitor,
}: {
  chain: ChainPayload | null;
  monitor: MonitorPayload | null;
}) {
  const campaign = monitor?.campaign ?? null;
  const roots = chain ? chain.roots.filter((r) => !rootIsLive(r, chain)) : [];
  const current = roots.filter((r) => !r.superseded && r.stage !== "SUPERSEDED");
  const superseded = roots.filter((r) => r.superseded || r.stage === "SUPERSEDED");
  return (
    <OpsSection
      control="jobs.history"
      title="Chain passes not in flight"
      hint="Purpose, stage, progress and outcome for every chain root no registered worker owns. Superseded roots carry no scientific result and are listed separately."
    >
      {!chain ? (
        <ul className="ops-list">
          <OpsUnknown
            what="The science chain"
            why="/api/chain has not answered, so which passes are finished is not known."
          />
        </ul>
      ) : (
        <ul className="ops-list">
          {chain.lofo && chain.lofo.state !== "RUNNING" ? (
            <LofoRow lofo={chain.lofo} confirmed={false} />
          ) : null}
          {current.map((r) => (
            <ArmRow key={r.root} r={r} live={false} />
          ))}
          {campaign && !campaignIsLive(campaign) ? <CampaignRow campaign={campaign} /> : null}
          {current.length === 0 && !(chain.lofo && chain.lofo.state !== "RUNNING") ? (
            <OpsItem tone="idle" control="jobs.history.none" title="Every current root is in flight" state="none" />
          ) : null}
        </ul>
      )}
      {superseded.length ? (
        <OpsDetails
          control="jobs.history.superseded"
          summary={`${plural(superseded.length, "superseded root")} — no scientific result`}
        >
          <ul className="ops-list">
            {superseded.map((r) => (
              <ArmRow key={r.root} r={r} live={false} />
            ))}
          </ul>
        </OpsDetails>
      ) : null}
      <OpsSource route="/api/chain" field="roots[], lofo" />
    </OpsSection>
  );
}

function ArmRow({ r, live }: { r: ChainRoot; live: boolean }) {
  return (
            <OpsItem
              key={r.root}
              control={`jobs.live.arm.${r.arm}`}
              tone={live ? "info" : stageTone(r.stage)}
              title={`Arm ${r.arm} · ${r.name}`}
              state={r.stage.toLowerCase().replace(/_/g, " ")}
              badges={
                <>
                  <OpsBadge tone={live ? "info" : "idle"}>
                    {live ? "in flight" : "not in flight"}
                  </OpsBadge>
                  {r.sealed && r.stage !== "SEALED" ? (
                    <OpsBadge tone="info">sealed</OpsBadge>
                  ) : null}
                </>
              }
            >
              <OpsFacts>
                <OpsFact label="Purpose">
                  one arm of the qualification chain
                </OpsFact>
                <OpsFact label="Stage">
                  {r.stage}
                  {r.stage_source ? (
                    <div className="ops-note">read from {r.stage_source}</div>
                  ) : null}
                </OpsFact>
                <OpsFact label="Progress">
                  {r.progress ? (
                    <>
                      {r.progress.state}
                      {r.progress.why ? <div className="ops-note">{r.progress.why}</div> : null}
                    </>
                  ) : (
                    "the chain declares no progress field for this root"
                  )}
                </OpsFact>
                <OpsFact label="Outcome">
                  {r.scores
                    ? "a score artifact exists; it is shown on Evidence, not here"
                    : (r.score_note ??
                      "no score artifact exists yet, so no number is shown")}
                </OpsFact>
              </OpsFacts>
              {live ? (
                <CancelUnavailable
                  what={`arm ${r.arm}`}
                  control={`jobs.live.arm.${r.arm}.cancel`}
                />
              ) : (
                <div className="ops-note" data-control={`jobs.live.arm.${r.arm}.no-cancel`}>
                  No cancel affordance is offered: no registered worker owns this root, so
                  there is nothing running here to stop. The absence is deliberate — a cancel
                  control on work that has already finished is exactly what the stale{" "}
                  <span className="ops-mono">cancellable</span> flag on finished ledger records
                  used to produce, and that count is reported from the ledger under Historical
                  jobs rather than written into this sentence.
                </div>
              )}
            </OpsItem>
  );
}

function LofoRow({ lofo, confirmed }: { lofo: ChainLofo; confirmed: boolean }) {
  const running = lofo.state === "RUNNING";
  const tone: OpsTone = running ? (confirmed ? "info" : "warn") : "idle";
  return (
    <OpsItem
      control="jobs.live.lofo"
      tone={tone}
      title={`${lofo.id} · evaluation pass`}
      state={
        running
          ? confirmed
            ? "running"
            : "declared running · no registered worker"
          : lofo.state.toLowerCase()
      }
    >
      <OpsFacts>
        <OpsFact label="Purpose">
          a held-out evaluation pass
        </OpsFact>
        <OpsFact label="Target">
          {lofo.labels ?? "labels not declared"}
          {lofo.extent ? ` · ${lofo.extent}` : ""}
        </OpsFact>
        <OpsFact label="Stage">
          {lofo.active_fold
            ? `fold ${lofo.active_fold} of ${lofo.folds_total ?? "?"}`
            : "no fold declared active"}
          {lofo.fold_sealed ? ` · ${lofo.fold_sealed.length} fold(s) sealed` : ""}
        </OpsFact>
        <OpsFact label="Progress">
          {lofo.progress ? (
            <>
              {lofo.progress.state}
              {lofo.progress.why ? <div className="ops-note">{lofo.progress.why}</div> : null}
            </>
          ) : (
            "no progress field is declared"
          )}
        </OpsFact>
        <OpsFact label="Outcome">
          {lofo.why_no_verdict ?? "no verdict field is declared"}
          {lofo.held_out_source ? (
            <div className="ops-note">results come from {lofo.held_out_source}</div>
          ) : null}
        </OpsFact>
        {lofo.memory_pressure ? (
          <OpsFact label="Memory headroom at last read">
            {typeof lofo.memory_pressure.available_gib === "number"
              ? `${lofo.memory_pressure.available_gib} GiB available`
              : "unknown"}
            {typeof lofo.memory_pressure.commit_percent === "number"
              ? ` · commit ${lofo.memory_pressure.commit_percent}%`
              : ""}
          </OpsFact>
        ) : null}
      </OpsFacts>
      {lofo.state === "RUNNING" ? (
        <CancelUnavailable what={`the ${lofo.id} pass`} control="jobs.live.lofo.cancel" />
      ) : (
        <div className="ops-note" data-control="jobs.live.lofo.no-cancel">
          This pass is not running, so no cancel affordance is offered.
        </div>
      )}
    </OpsItem>
  );
}

function CampaignRow({
  campaign,
}: {
  campaign: NonNullable<MonitorPayload["campaign"]>;
}) {
  const stalled = campaign.state === "STALLED";
  return (
    <OpsItem
      control="jobs.live.campaign"
      tone={stalled ? "warn" : "info"}
      title={`Campaign heartbeat · ${campaign.stage ?? "stage not declared"}`}
      state={(campaign.state ?? "state not declared").toLowerCase()}
    >
      <OpsFacts>
        <OpsFact label="Lane">{campaign.lane ?? "not declared"}</OpsFact>
        <OpsFact label="Units">
          {typeof campaign.done === "number" && typeof campaign.total === "number"
            ? `${campaign.done} of ${campaign.total}`
            : "the heartbeat declares no unit count"}
        </OpsFact>
        <OpsFact label="Last beat">
          {campaign.beat_utc ?? "never"} · {agoLabel(campaign.age_s ?? null)}
        </OpsFact>
        <OpsFact label="Why that state">
          {campaign.state_basis ?? "the heartbeat declares no basis for its state"}
        </OpsFact>
        <OpsFact label="Note">{campaign.note ?? "none recorded"}</OpsFact>
      </OpsFacts>
      {stalled ? (
        <div className="ops-refusal">
          A completed unit count next to a stalled heartbeat is not a finished campaign: the
          writer stopped renewing. Treat the count as the last thing that was true, not as
          the current state.
        </div>
      ) : null}
      <OpsSource route="/api/monitor" field="campaign" />
    </OpsItem>
  );
}

function CancelUnavailable({ what, control }: { what: string; control: string }) {
  return (
    <OpsDisabled
      control={control}
      label={`Cancel ${what}`}
      reason={
        <>
          Not available, and deliberately so. This interface reads: the service it talks to
          has no write route and a test asserts its route table. Beyond that, a science pass
          runs under a single-use authorisation that binds its contract, plan and identity —
          a crashed-and-retried run cannot quietly become a second attempt, so an interrupted
          pass is a spent one. Stopping it is an operator act at the console that started it.
        </>
      }
    />
  );
}


function ConveyorRow({
  storage,
  failure,
  activity,
}: {
  storage: StoragePayload | null;
  failure: Failure | null;
  activity: Activity | null;
}) {
  const archive = activity?.classes?.ARCHIVE_STORAGE ?? null;
  const blocked = storage ? Object.values(storage.blocked ?? {}).reduce((a, b) => a + b, 0) : null;

  return (
    <OpsSection
      control="jobs.conveyor"
      title="Storage conveyor"
      hint="A host daemon that archives, verifies, evicts and re-hydrates catalogued assets. It is not a governed job and it does not appear in the job ledger."
      aside={
        archive ? (
          <OpsBadge tone={activityTone(archive)} control="jobs.conveyor.state">
            {archive.state === "ACTIVE"
              ? "observed active"
              : archive.state === "UNKNOWN"
                ? "liveness unobservable"
                : "no activity observed"}
          </OpsBadge>
        ) : null
      }
    >
      {storage ? (
        <>
          <OpsFacts>
            <OpsFact label="Catalogued assets">
              {storage.catalog_present
                ? `${storage.assets}`
                : "the catalog file is absent, so nothing is catalogued"}
            </OpsFact>
            <OpsFact label="Pinned (never evicted)">{storage.pinned}</OpsFact>
            <OpsFact label="Evicted with a restore command">{storage.restore_ready}</OpsFact>
            <OpsFact label="Evicted WITHOUT a restore command">
              {storage.evicted_without_restore_command.length === 0
                ? "none — every evicted asset carries a hydrate command"
                : storage.evicted_without_restore_command.join(", ")}
            </OpsFact>
            <OpsFact label="Would free if evicted now">
              {storage.would_free_gib_if_evicted_now} GiB of EVICTABLE assets
            </OpsFact>
            <OpsFact label="BLOCKED_UNKNOWN">
              {blocked === null ? "unknown" : `${blocked} asset(s)`} — no verified remote copy,
              so eviction would be data loss rather than a cache miss
            </OpsFact>
          </OpsFacts>

          <OpsTable control="jobs.conveyor.states">
            <thead>
              <tr>
                <th>Lifecycle state</th>
                <th>Assets</th>
                <th>Size</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(storage.by_state).map(([k, v]) => (
                <tr key={k}>
                  <td>
                    <OpsBadge
                      tone={
                        k.startsWith("BLOCKED")
                          ? "bad"
                          : k === "HOT_PINNED"
                            ? "info"
                            : "idle"
                      }
                    >
                      {k.toLowerCase().replace(/_/g, " ")}
                    </OpsBadge>
                  </td>
                  <td className="ops-mono">{v.assets}</td>
                  <td className="ops-mono">{v.gib} GiB</td>
                </tr>
              ))}
            </tbody>
          </OpsTable>

          <div className="ops-note">{storage.mutations_are_not_here}</div>

          <OpsDisabled
            control="jobs.conveyor.stop"
            label="Stop the conveyor"
            reason="Absent by design. The conveyor owns the eviction lifecycle; a second owner is how an asset gets evicted while its only verified copy is still uploading. It is stopped at the console that started it, never from a reading surface."
          />
          <OpsDisabled
            control="jobs.conveyor.evict"
            label="Evict eligible assets now"
            reason="Absent by design. Eviction is the conveyor's decision and its alone. Nothing on this screen may touch a BLOCKED_UNKNOWN asset under any circumstances — those have no verified remote copy, so evicting one destroys the only copy."
          />
        </>
      ) : (
        <ul className="ops-list">
          {failure ? (
            <OpsFailure what="The conveyor catalog" failure={failure} control="jobs.conveyor.read" />
          ) : (
            <OpsUnknown
              what="The conveyor catalog"
              why="/api/storage has not answered yet. An absent catalog and an empty one are opposite facts and neither is being assumed."
            />
          )}
        </ul>
      )}

      {archive ? (
        <OpsDetails control="jobs.conveyor.liveness" summary="Why its liveness reads as it does">
          <div className="ops-note">{archive.means}</div>
          {archive.unknown_because.map((u, i) => (
            <OpsQuote key={i} cite="/api/activity → classes.ARCHIVE_STORAGE.unknown_because">
              {u}
            </OpsQuote>
          ))}
          <div className="ops-note">
            The daemon's own heartbeat record lives behind the localhost-only storage UI, which
            is not reachable from this origin. So this screen can report what the conveyor has
            DONE, from its catalog, and cannot report whether it is breathing right now.
          </div>
        </OpsDetails>
      ) : null}

      <OpsSource route="/api/storage" field="by_state, pinned, blocked, restore_ready" />
    </OpsSection>
  );
}


const REFUSED_CAP = 5;
const LEDGER_CAP = 25;

function LedgerUnread({ failure }: { failure: Failure | null }) {
  return (
    <ul className="ops-list">
      {failure ? (
        <OpsFailure what="The governed job ledger" failure={failure} control="jobs.attention.ledger" />
      ) : (
        <OpsUnknown what="The governed job ledger" why="/api/command-jobs has not answered yet." />
      )}
    </ul>
  );
}

function OrphanedRecords({
  ledger,
  failure,
}: {
  ledger: LedgerPayload | null;
  failure: Failure | null;
}) {
  const stuck = ledger?.stuck ?? [];
  const runningRecorded = ledger?.by_state?.RUNNING ?? 0;
  return (
    <OpsSection
      control="jobs.orphaned"
      title="Orphaned state records"
      hint="Ledger records written RUNNING whose process is gone. Nothing failed in the work — the record failed to close. Read them as bookkeeping, never as work in flight."
      aside={
        ledger ? (
          <OpsBadge tone={stuck.length ? "warn" : "idle"} control="jobs.orphaned.count">
            {stuck.length ? plural(stuck.length, "orphaned record") : "no orphaned record"}
          </OpsBadge>
        ) : null
      }
    >
      {!ledger ? (
        <LedgerUnread failure={failure} />
      ) : (
        <ul className="ops-list">
          {stuck.length ? (
            <OpsItem
              control="jobs.attention.stuck"
              tone="warn"
              title={`${stuck.length} record${stuck.length === 1 ? "" : "s"} recorded RUNNING with no process behind ${stuck.length === 1 ? "it" : "them"}`}
              state="orphaned record"
            >
              <OpsFacts>
                <OpsFact label="What failed">
                  nothing failed in the work — the RECORD failed to close.
                </OpsFact>
                <OpsFact label="Why">{ledger.stuck_note}</OpsFact>
                <OpsFact label="Next action">
                  clearing a job record is a mutation and cannot be done from any reading
                  surface. Until it is cleared, read these rows as bookkeeping, never as work
                  in flight.
                </OpsFact>
              </OpsFacts>
              <OpsTable control="jobs.attention.stuck.table">
                <thead>
                  <tr>
                    <th>Action</th>
                    <th>Actor</th>
                    <th>Started</th>
                    <th>Open for</th>
                  </tr>
                </thead>
                <tbody>
                  {stuck.map((j) => (
                    <tr key={j.job_id}>
                      <td className="ops-mono">{j.action}</td>
                      <td>{j.actor}</td>
                      <td className="ops-mono">{j.started_utc ?? "not recorded"}</td>
                      <td className="ops-mono">{elapsedLabel(j.age_s)}</td>
                    </tr>
                  ))}
                </tbody>
              </OpsTable>
            </OpsItem>
          ) : (
            <OpsItem
              tone="idle"
              control="jobs.orphaned.none"
              title="The ledger lists no record left RUNNING without a process"
              state="none"
            />
          )}
          {runningRecorded > stuck.length ? (
            <OpsItem
              tone="idle"
              control="jobs.orphaned.unflagged"
              title={`${runningRecorded - stuck.length} more record(s) are RUNNING in the ledger and not listed as stuck`}
              state="open in the record"
            >
              <div className="ops-item-body">
                The service does not flag these, so they are not called orphaned here — and no
                route confirms a process behind them either, so they are not called live. They
                appear in the full ledger under Historical jobs.
              </div>
            </OpsItem>
          ) : null}
        </ul>
      )}
      <OpsSource route="/api/command-jobs" field="stuck[], stuck_note, by_state.RUNNING" />
    </OpsSection>
  );
}

function NeedsAttention({
  ledger,
  failure,
  monitor,
}: {
  ledger: LedgerPayload | null;
  failure: Failure | null;
  monitor: MonitorPayload | null;
  gates?: GatesPayload | null;
}) {
  const [all, setAll] = useState(false);
  const refused = useMemo(
    () =>
      (ledger?.jobs ?? [])
        .filter((j) => j.state === "REFUSED")
        .sort(
          (x, y) =>
            (x.age_s ?? Number.POSITIVE_INFINITY) - (y.age_s ?? Number.POSITIVE_INFINITY),
        ),
    [ledger],
  );
  const recorded = ledger?.by_state?.REFUSED ?? refused.length;
  const shown = all ? refused : refused.slice(0, REFUSED_CAP);
  const attention = monitor?.attention ?? [];
  const total = refused.length + attention.length;

  return (
    <OpsSection
      control="jobs.attention"
      title="Refused requests and flagged runs"
      hint="A refusal states what failed, why, and what would change it. A bare status word is not a report. Newest first."
      aside={
        <OpsBadge tone={total === 0 ? "idle" : "warn"} control="jobs.attention.count">
          {total === 0 ? "nothing outstanding" : `${total} item(s)`}
        </OpsBadge>
      }
    >
      {!ledger ? (
        <LedgerUnread failure={failure} />
      ) : (
        <ul className="ops-list">
          {recorded > refused.length ? (
            <li className="ops-note">
              The ledger records {recorded} refusals; {refused.length} are among the fetched
              rows and listed here. The other {recorded - refused.length} were not fetched.
            </li>
          ) : null}
          {shown.map((j) => (
            <OpsItem
              key={j.job_id}
              control={`jobs.attention.refused.${j.job_id}`}
              tone="bad"
              title={j.action}
              state={j.refusal ?? "refused"}
            >
              <OpsFacts>
                <OpsFact label="What failed">
                  <span className="ops-mono">{j.action}</span> asked for by {j.actor},{" "}
                  {agoLabel(j.age_s)}
                </OpsFact>
                <OpsFact label="Why">
                  {j.why ?? "the ledger records a refusal code and no reason for it."}
                </OpsFact>
                <OpsFact label="Next action">
                  none declared by the ledger for this refusal. A refusal is the system correctly
                  declining; asking again the same way will be refused the same way.
                </OpsFact>
              </OpsFacts>
            </OpsItem>
          ))}

          {refused.length > REFUSED_CAP ? (
            <li>
              <CapNote
                cap={REFUSED_CAP}
                total={refused.length}
                all={all}
                onToggle={() => setAll((v) => !v)}
                noun="refused requests"
                control="jobs.attention.refused.show-all"
              />
            </li>
          ) : null}

          {attention.map((r, i) => (
            <OpsItem
              key={`${r.id ?? i}`}
              control={`jobs.attention.run.${r.id ?? i}`}
              tone="warn"
              title={r.id ?? "run id not recorded"}
              state={(r.state ?? "unknown").toLowerCase()}
            >
              <div className="ops-item-body">
                {r.reason ?? "the monitor records no reason for this state."}
              </div>
            </OpsItem>
          ))}

          {total === 0 ? (
            <OpsItem tone="idle" control="jobs.attention.none" title="No refusal or flagged run is reported" state="none">
              <div className="ops-item-body">
                This is a statement about the ledger and the monitor snapshot, not a promise
                that nothing is wrong elsewhere.
              </div>
            </OpsItem>
          ) : null}
        </ul>
      )}
      <OpsSource route="/api/command-jobs" field="jobs[state=REFUSED], by_state.REFUSED" />
      <OpsSource route="/api/monitor" field="attention[]" />
    </OpsSection>
  );
}


function MachineResources({
  monitor,
  failure,
  chain,
}: {
  monitor: MonitorPayload | null;
  failure: Failure | null;
  chain: ChainPayload | null;
}) {
  const r = monitor?.resources ?? null;
  const floors = chain?.resources ?? null;
  const belowFloor = floors?.below_floor ?? [];

  return (
    <OpsSection
      control="jobs.resources"
      title="Machine resources"
      hint="Whole-machine, at the moment of the last read. These are NOT attributed to any single pass: no route reports per-job resource use, so none is claimed."
      aside={
        floors ? (
          <OpsBadge tone={belowFloor.length ? "bad" : "idle"} control="jobs.resources.floors">
            {belowFloor.length
              ? `${belowFloor.length} resource below its floor`
              : "every declared floor is clear"}
          </OpsBadge>
        ) : (
          <OpsBadge tone="idle" control="jobs.resources.floors">
            floors not read
          </OpsBadge>
        )
      }
    >
      {!r ? (
        <ul className="ops-list">
          {failure ? (
            <OpsFailure what="Resource probes" failure={failure} control="jobs.resources.read" />
          ) : (
            <OpsUnknown
              what="Resource probes"
              why="/api/monitor has not answered yet. A card that did not answer is not an idle card."
            />
          )}
        </ul>
      ) : (
        <div className="ops-stats">
          <OpsStat
            control="jobs.resources.gpu"
            label="Accelerator memory"
            value={
              r.gpu ? `${r.gpu.used_mib} / ${r.gpu.total_mib} MiB` : "unknown"
            }
            tone={r.gpu ? (r.gpu.busy ? "info" : "idle") : "idle"}
            toneLabel={r.gpu ? (r.gpu.busy ? "busy" : "not busy") : "not answered"}
            source={r.gpu ? "/api/monitor → resources.gpu" : "nvidia-smi did not answer; unknown, not zero"}
          />
          <OpsStat
            control="jobs.resources.gpu-util"
            label="Accelerator utilisation"
            value={r.gpu ? `${r.gpu.util_pct}%` : "unknown"}
            source="/api/monitor → resources.gpu.util_pct"
          />
          <OpsStat
            control="jobs.resources.cpu"
            label="CPU"
            value={r.cpu_pct === null ? "unknown" : `${r.cpu_pct}%`}
            source="/api/monitor → resources.cpu_pct"
          />
          <OpsStat
            control="jobs.resources.ram"
            label="Memory"
            value={r.ram ? `${r.ram.used_gib} / ${r.ram.total_gib} GiB` : "unknown"}
            tone={r.ram ? (r.ram.pct > 90 ? "warn" : "idle") : "idle"}
            toneLabel={r.ram ? (r.ram.pct > 90 ? "above 90% used" : "90% used or less") : "not answered"}
            source="/api/monitor → resources.ram"
          />
          {Object.entries(r.disks).map(([root, d]) => (
            <OpsStat
              key={root}
              control={`jobs.resources.disk.${root.replace(/[^a-z0-9]/gi, "")}`}
              label={`${root} free`}
              value={`${d.free_gib} / ${d.total_gib} GiB`}
              tone={d.free_gib < 50 ? "bad" : d.free_gib < 200 ? "warn" : "idle"}
              toneLabel={
                d.free_gib < 50 ? "under 50 GiB free" : d.free_gib < 200 ? "under 200 GiB free" : "200 GiB or more free"
              }
              source="/api/monitor → resources.disks"
            />
          ))}
        </div>
      )}
      {floors?.floors ? (
        <OpsDetails control="jobs.resources.floor-detail" summary="The declared floors a pass has to fit above">
          <OpsFacts>
            {Object.entries(floors.floors).map(([k, v]) => (
              <OpsFact key={k} label={k.replace(/_/g, " ")}>
                {v}
              </OpsFact>
            ))}
          </OpsFacts>
          <div className="ops-note">
            {belowFloor.length
              ? `Below floor right now: ${belowFloor.join(", ")}.`
              : "Nothing is below its floor at the moment of the last read."}
          </div>
          <OpsSource route="/api/chain" field="resources.floors, resources.below_floor" />
        </OpsDetails>
      ) : null}
    </OpsSection>
  );
}


function Ledger({
  data,
  failure,
  onRetry,
}: {
  data: LedgerPayload | null;
  failure: Failure | null;
  onRetry: () => void;
}) {
  const [q, setQ] = useState("");
  const [only, setOnly] = useState("all");
  const [all, setAll] = useState(false);
  const stuckIds = useMemo(() => new Set((data?.stuck ?? []).map((j) => j.job_id)), [data]);

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (data?.jobs ?? [])
      .filter((j) => (only === "all" ? true : j.state === only))
      .filter(
        (j) =>
          !needle ||
          [j.action, j.actor, j.state, j.refusal ?? "", j.job_id].some((s) =>
            String(s).toLowerCase().includes(needle),
          ),
      );
  }, [data, q, only]);

  const states = useMemo(() => Object.keys(data?.by_state ?? {}).sort(), [data]);

  return (
    <OpsSection
      control="jobs.ledger"
      title="The governed job ledger"
      hint="Every job the command service has recorded: what was asked for, by whom, and how it ended. This is the record of ASKING — it is not, and has never been, the record of what is running."
      aside={
        data ? (
          <>
            {states.map((s) => (
              <OpsBadge key={s} tone={ledgerTone(s)} control={`jobs.ledger.count.${s}`}>
                {data.by_state[s]} {s === "RUNNING" ? `recorded running, ${stuckIds.size} orphaned` : s.toLowerCase()}
              </OpsBadge>
            ))}
            <OpsBadge tone="idle">{data.total} recorded</OpsBadge>
          </>
        ) : null
      }
    >
      {failure && !data ? (
        <ul className="ops-list">
          <OpsFailure
            what="The job ledger"
            failure={failure}
            onRetry={onRetry}
            control="jobs.ledger"
          />
        </ul>
      ) : null}

      <div className="ops-filterbar">
        <label htmlFor="jobs-q">
          Search
          <input
            id="jobs-q"
            value={q}
            placeholder="action, actor, refusal code, job id"
            onChange={(e) => setQ(e.target.value)}
            className="jobs-search"
            data-control="jobs.ledger.search"
          />
        </label>
        <label htmlFor="jobs-state">
          State
          <select
            id="jobs-state"
            value={only}
            onChange={(e) => setOnly(e.target.value)}
            data-control="jobs.ledger.filter-state"
          >
            <option value="all">every state</option>
            {states.map((s) => (
              <option key={s} value={s}>
                {s.toLowerCase()}
              </option>
            ))}
          </select>
        </label>
        <OpsButton onClick={onRetry} control="jobs.ledger.refresh">
          Read again
        </OpsButton>
        <span className="ops-note">
          {data
            ? `${rows.length} of ${data.jobs.length} fetched record(s) match` +
              (data.jobs.length < data.total ? ` (${data.total} recorded, ${data.total - data.jobs.length} not fetched)` : "")
            : failure
              ? "could not read the ledger"
              : "reading…"}
        </span>
      </div>
      {data ? (
        <CapNote
          cap={LEDGER_CAP}
          total={rows.length}
          all={all}
          onToggle={() => setAll((v) => !v)}
          noun="matching records"
          control="jobs.ledger.show-all"
        />
      ) : null}

      {data ? (
        <OpsTable control="jobs.ledger.table">
          <thead>
            <tr>
              <th>State</th>
              <th>Action</th>
              <th>Asked by</th>
              <th>Started</th>
              <th>Elapsed</th>
              <th>Outcome</th>
              <th>Cancel</th>
            </tr>
          </thead>
          <tbody>
            {(all ? rows : rows.slice(0, LEDGER_CAP)).map((j) => {
              const fx = classifyFixture(j);
              const orphaned = j.state === "RUNNING" && stuckIds.has(j.job_id);
              return (
                <tr
                  key={j.job_id}
                  data-control={`jobs.ledger.row.${j.job_id}`}
                  data-orphaned={orphaned ? "true" : undefined}
                >
                  <td>
                    {orphaned ? (
                      <OpsBadge tone="warn">orphaned record</OpsBadge>
                    ) : j.state === "RUNNING" ? (
                      <OpsBadge tone="idle">recorded running</OpsBadge>
                    ) : (
                      <OpsBadge tone={ledgerTone(j.state)}>{j.state.toLowerCase()}</OpsBadge>
                    )}
                  </td>
                  <td>
                    <span className="ops-mono">{j.action}</span>
                    {fx.fixture ? (
                      <>
                        {" "}
                        <OpsFixture why={fx.why} />
                      </>
                    ) : null}
                  </td>
                  <td>{j.actor}</td>
                  <td className="ops-mono">{j.started_utc ?? "not recorded"}</td>
                  <td className="ops-mono">{elapsedLabel(j.age_s)}</td>
                  <td>
                    {j.refusal ? (
                      <>
                        <strong>{j.refusal}</strong>
                        {j.why ? <div className="ops-note">{j.why}</div> : null}
                      </>
                    ) : j.state === "SUCCEEDED" ? (
                      "completed; the ledger records no result body"
                    ) : orphaned ? (
                      "recorded RUNNING, no process behind it; bookkeeping, not work"
                    ) : j.state === "RUNNING" ? (
                      "still open in the record; no route here confirms a process"
                    ) : (
                      "no outcome recorded"
                    )}
                  </td>
                  <td>
                    {j.effective_cancellable ? (
                      <OpsDisabled
                        control={`jobs.ledger.row.${j.job_id}.cancel`}
                        label="Cancel"
                        reason="This job holds a lease and the service considers it cancellable — but cancelling is a mutation and this interface has no write route. It is cancelled through the command service with an operator session."
                      />
                    ) : (
                      <span className="ops-note">
                        {j.terminal
                          ? "already finished"
                          : "the service does not consider this cancellable"}
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </OpsTable>
      ) : null}

      {data && data.cancellable_but_finished ? (
        <div className="ops-note">
          {data.cancellable_but_finished} finished record(s) still carry{" "}
          <span className="ops-mono">cancellable: true</span>. {data.cancellable_note} No
          cancel is offered for any of them.
        </div>
      ) : null}
      <OpsSource
        route="/api/command-jobs?limit=200"
        field="jobs[], by_state, effective_cancellable"
      />
    </OpsSection>
  );
}


function StageLadder({ gates, failure }: { gates: GatesPayload | null; failure: Failure | null }) {
  return (
    <OpsSection
      control="jobs.gates"
      title="What the pipeline is allowed to do next"
      hint="Each gate carries its own reason and, where one exists, the concrete thing that would open it. A gate after a shut one reports BLOCKED_UPSTREAM rather than its own verdict, because a number computed on uncertified input is a number about nothing."
      aside={
        gates ? (
          <OpsBadge tone={gates.first_closed ? "warn" : "idle"} control="jobs.gates.count">
            {gates.n_open} of {gates.n_total} open
          </OpsBadge>
        ) : null
      }
    >
      {!gates ? (
        <ul className="ops-list">
          {failure ? (
            <OpsFailure what="The gate ladder" failure={failure} control="jobs.gates.read" />
          ) : (
            <OpsUnknown what="The gate ladder" why="/api/gates has not answered yet." />
          )}
        </ul>
      ) : (
        <ol className="ops-ladder">
          {gates.gates.map((g) => {
            const tone: OpsTone = g.state === "SHUT" ? "bad" : "idle";
            return (
              <li key={g.gate} data-tone={tone} data-control={`jobs.gates.${g.gate}`}>
                <span className="ops-rung-n" aria-hidden />
                <div style={{ display: "grid", gap: 6, minWidth: 0 }}>
                  <div className="ops-item-head">
                    <span className="ops-item-title">{g.gate}</span>
                    <OpsBadge tone={tone}>
                      {g.state === "OPEN"
                        ? "open"
                        : g.state === "SHUT"
                          ? "closed — this is the one holding things up"
                          : "not reached, because an earlier gate is closed"}
                    </OpsBadge>
                  </div>
                  <div className="ops-item-body">{g.why}</div>
                  {g.satisfied_by ? (
                    <div className="ops-item-body">
                      <strong>What would open it: </strong>
                      {g.satisfied_by}
                    </div>
                  ) : g.state !== "OPEN" ? (
                    <div className="ops-note">
                      The ladder declares nothing that would open it, so nothing is suggested.
                    </div>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ol>
      )}
      <OpsSource route="/api/gates" field="gates[], n_open, n_total, first_closed" />
    </OpsSection>
  );
}


function BlindSpots({ activity }: { activity: Activity | null }) {
  return (
    <OpsSection
      control="jobs.blindspots"
      title="What this console cannot see"
      hint="An instrument that lists only what it can observe teaches the reader that nothing else exists."
      aside={
        activity ? (
          <OpsBadge
            tone={activity.unknown_classes.length ? "warn" : "idle"}
            control="jobs.blindspots.count"
          >
            {activity.unknown_classes.length} of {Object.keys(activity.classes).length} classes
            unobservable
          </OpsBadge>
        ) : null
      }
    >
      <ul className="ops-list">
        {activity
          ? Object.entries(activity.classes)
              .filter(([, c]) => c.state !== "ACTIVE")
              .map(([k, c]) => (
                <OpsItem
                  key={k}
                  control={`jobs.blindspots.${k}`}
                  tone={activityTone(c)}
                  title={ACTIVITY_LABEL[k] ?? k}
                  state={c.state === "UNKNOWN" ? "could not be observed" : "nothing observed"}
                >
                  <div className="ops-item-body">{c.means}</div>
                  {c.unknown_because.map((u, i) => (
                    <div className="ops-item-body" key={i}>
                      <strong>Why unknown: </strong>
                      {u}
                    </div>
                  ))}
                  {c.evidence.map((e, i) => (
                    <div className="ops-note" key={`e${i}`}>
                      {e.witness}
                      {e.what ? ` — ${e.what}` : ""}
                      {typeof e.threshold_pct === "number"
                        ? ` (threshold ${String(e.threshold_pct)}%)`
                        : ""}
                    </div>
                  ))}
                </OpsItem>
              ))
          : null}

        <OpsItem
          control="jobs.blindspots.processes"
          tone="warn"
          title="Host processes launched outside the governed ledger"
          state="not enumerable from here"
        >
          <div className="ops-item-body">
            Nothing this interface can reach returns a process table. A pass started from a
            terminal — a scoring script, the storage daemon, an archive transfer — exists and
            is invisible to the ledger. It becomes visible only indirectly: through a progress
            receipt under a readable artifact root, through its effect on the catalog, or
            through the machine's own resource use above.
          </div>
          <div className="ops-item-body">
            <strong>Consequence for reading this page: </strong>
            an empty "work in flight" list means the routes above saw nothing. It does not mean
            the machine is idle, and the activity headline on this page is worded to keep
            that distinction.
          </div>
        </OpsItem>

        <OpsItem
          control="jobs.blindspots.logs"
          tone="warn"
          title="A worker's last log line"
          state="not served"
        >
          <div className="ops-item-body">
            There is no log route. <span className="ops-mono">/api/file</span> serves one
            artifact at a time and refuses any path outside the declared artifact roots — a
            worker writing its stdout to a scratch directory is therefore genuinely unreadable
            from this origin, and it answers{" "}
            <span className="ops-mono">403 outside the declared roots</span> rather than
            pretending the file is missing.
          </div>
          <div className="ops-item-body">
            <strong>What is available instead: </strong>
            the progress fields the science chain publishes, shown per pass above, and the
            receipts a stage writes, shown on Evidence. Where a worker publishes no heartbeat,
            progress reads "unknown" and is never estimated from elapsed time.
          </div>
        </OpsItem>
      </ul>
      {activity?.feed_coverage ? (
        <OpsQuote cite="/api/activity → feed_coverage.note">
          {activity.feed_coverage.note} (observatory feed roots:{" "}
          {activity.feed_coverage.observatory_feed_roots}; activity roots:{" "}
          {activity.feed_coverage.activity_roots})
        </OpsQuote>
      ) : null}
      {activity?.unreadable_roots?.length ? (
        <div className="ops-refusal">
          {activity.unreadable_roots.length} declared root(s) could not be read:{" "}
          {activity.unreadable_roots.map((r) => r.root).join(", ")}.
        </div>
      ) : null}
      <OpsSource route="/api/activity" field="classes[].unknown_because, roots[], feed_coverage" />
    </OpsSection>
  );
}


function AskingPanel({ registries }: { registries?: LedgerPayload["registries"] }) {
  if (!registries) {
    return (
      <ul className="ops-list">
        <OpsUnknown
          what="The action registries"
          why="/api/command-jobs has not supplied its registries, so no action count is shown."
        />
      </ul>
    );
  }
  return (
    <OpsSection
      control="jobs.asking"
      title="Asking for something"
      hint="Every action below is real and allowlisted, and none of them takes a command string. All of them are reached through a door this page cannot open."
    >
      {[registries.command_surface, registries.stage_surface].map((r, i) => (
        <OpsDetails
          key={r.module}
          control={`jobs.asking.${i === 0 ? "command" : "stage"}`}
          summary={
            <>
              <strong>{i === 0 ? "Command surface" : "Stage surface"}</strong> — {r.count}{" "}
              action{r.count === 1 ? "" : "s"}
              {typeof r.mutating === "number" ? `, ${r.mutating} of them mutating` : ""} ·{" "}
              <span className="ops-mono">{r.module}</span>
            </>
          }
        >
          <div className="ops-note">Reached by: {r.reached_by}</div>
          <div className="ops-badges">
            {r.actions.map((a) => (
              <span className="ops-mono" key={a}>
                {a}
              </span>
            ))}
          </div>
        </OpsDetails>
      ))}
      <div className="ops-note">{registries.why_two_numbers}</div>
      <OpsDisabled
        control="jobs.asking.submit"
        label="Submit an action"
        reason="No submit control exists here. Four buttons used to, and they posted into a door that answers 401 to a page by design — pressing one produced an identical screen before and after, which is worse than no button. Governed actions live on System, where the transport that holds a session is."
      />
      <OpsSource route="/api/command-jobs" field="registries" />
    </OpsSection>
  );
}

export default Jobs;
