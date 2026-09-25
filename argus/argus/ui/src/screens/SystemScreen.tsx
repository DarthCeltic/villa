import { useMemo } from "react";
import { Link, useSearchParams, Navigate } from "react-router-dom";
import type { FeedState } from "../api";
import { usePoll } from "../lib/poll";
import type { Failure } from "../lib/http";
import { useHealth, type Health } from "../components/SystemPanels";
import { useActivity, useIntegrity, certificationIsSuppressed } from "../lib/systemTruth";
import { useCapabilityGraph } from "../lib/argusTruth";
import { partitionFixtures, type FixtureVerdict } from "../lib/fixtures";
import { GovernedAction } from "../components/GovernedAction";
import { PlanApprove } from "../components/PlanApprove";
import { StructuredIntentConsole } from "../components/StructuredIntentConsole";
import { lifecycleTone, ProviderPlanForm, type ProviderRecord, stateLabel } from "../components/ProviderPlanForm";
import { Chip } from "../components/Status";
import { OperationsPanel } from "../components/OperationsPanel";
import { InstallTiersPanel } from "../components/InstallTiersPanel";
import { UpdatesPanel } from "../components/UpdatesPanel";
import { TargetRegistryStatus } from "../components/TargetRegistryStatus";
import { StacksPanel } from "../components/StacksPanel";
import { ConnectorPanel } from "../components/ConnectorPanel";
import { OpenProblemsPanel } from "../components/OpenProblemsPanel";
import { Private, demoText } from "../lib/publicDemo";
import type { UnrollIndex } from "../api";
import { ScrollStatusBar } from "../components/ScrollStatusBar";
import "../theme/ops.css";
import "../theme/about.css";
import {
  OpsBadge,
  OpsDetails,
  OpsDisabled,
  OpsFact,
  OpsFacts,
  OpsFailure,
  OpsFixture,
  OpsHeadline,
  OpsItem,
  OpsMeter,
  OpsPage,
  OpsQuote,
  OpsSection,
  OpsSource,
  OpsStat,
  OpsTable,
  OpsUnknown,
  agoLabel,
  bytesLabel,
  type OpsTone,
} from "../components/OpsKit";


interface Oversight {
  host: {
    platform: string;
    python: string;
    python_executable: string;
    cpu_logical: number;
    ram_gib: number | null;
    ram_source: string;
    cwd?: string;
  };
  gpu: {
    present: boolean;
    why?: string;
    consequence?: string;
    cards?: { name: string; vram_total_mib: number | null; vram_used_mib?: number | null; driver: string }[];
    source?: string;
  };
  disks: {
    root: string;
    present: boolean;
    free_gib?: number;
    total_gib?: number;
    used_pct?: number;
  }[];
  toolchain: {
    tools: Record<
      string,
      { present: boolean; path: string | null; version?: string | null; unlocks: string }
    >;
    packages: Record<string, { present: boolean; unlocks: string }>;
  };
  boundary: Record<string, boolean | string>;
  environment_flags: Record<string, boolean>;
  recommendations: {
    priority: string;
    title: string;
    because: string;
    unblocks: string;
    action: string;
  }[];
  note: string;
}

interface RuntimePayload {
  schema: string;
  service_identity?: { build_sha?: string; source_revision?: string; source_provenance?: string };
  interpreter: { running?: string; declared?: string; matches?: boolean };
  platform: string;
  torch: {
    running?: string | null;
    declared?: string | null;
    cuda_running?: string | null;
    cuda_declared?: string | null;
    matches?: boolean;
    optional?: boolean;
  };
  lock: {
    path: string;
    present: boolean;
    sha256?: string | null;
    declared_sha256?: string | null;
    matches?: boolean;
    installed_matches?: boolean;
    missing?: string[];
    mismatched?: { package: string; declared: string; running: string }[];
    why_it_matters?: string;
  };
  services: Record<string, boolean>;
  service_ports?: Record<string, number>;
  content_stores: Record<string, { present: boolean; entries: number | null }>;
  disks: Record<string, { free_gib?: number; total_gib?: number; error?: string }>;
  credentials: { served_here: boolean; where: string };
}

interface UpstreamStatusPayload {
  state: "BLOCKED" | "UNKNOWN" | "STALE_SNAPSHOT" | "DIRTY_CHECKOUT" | "PINNED_WITH_DRIFT";
  checked_utc?: string;
  pin?: {
    declared?: string | null;
    checkout_head?: string | null;
    matches_checkout?: boolean;
  };
  checkout?: {
    path?: string;
    present?: boolean;
    dirty?: boolean;
    dirty_paths?: string[];
    dirty_paths_truncated?: boolean;
    git_error?: string | null;
  };
  remote_tracking?: {
    ref?: string;
    sha?: string | null;
    subject?: string | null;
    commit_date?: string | null;
    known_locally?: boolean;
    git_error?: string | null;
  };
  candidate?: {
    ref?: string;
    commit?: string | null;
    matches_remote?: boolean;
    materialization?: string;
    review_status?: string;
    capabilities?: string[];
  };
  differential?: {
    receipt?: string | null;
    receipt_written_utc?: string | null;
    refresh_ran?: boolean | null;
    refs_last_fetched_utc?: string | null;
    commits_main_ahead_of_pin?: number | null;
    files_changed_since_pin?: number | null;
    basis?: string | null;
  };
  actions?: { next?: string };
}

interface VillaSciencePayload {
  state: string;
  stable?: string | null;
  candidate?: string | null;
  counts?: { components: number; changed: number; not_adapted: number; partially_adapted: number };
  components: {
    id: string;
    state: string;
    commits_since_stable?: number | null;
    merged_changes?: { subject: string; pull_request?: number | null }[];
  }[];
  claim_ceiling?: string;
}

interface SelectedScrollStatusPayload {
  status: string;
  display?: string;
  next_action?: {
    step: string;
    label: string;
    to: string;
    why: string;
    operator_only?: boolean;
  } | null;
  blocker?: { code?: string; text?: string; at_next_step?: boolean } | null;
  steps?: { n: number; label: string; state: string; why: string }[];
  claim_ceiling?: string;
}

interface StoragePayload {
  ok: boolean;
  read_only: boolean;
  catalog: string;
  catalog_present: boolean;
  assets: number;
  free_gib: Record<string, number | null>;
  by_state: Record<string, { assets: number; gib: number }>;
  would_free_gib_if_evicted_now: number;
  pinned: number;
  blocked: Record<string, number>;
  restore_ready: number;
  evicted_without_restore_command: string[];
  largest: {
    asset_id?: string;
    name?: string;
    project?: string;
    state?: string;
    gib?: number;
    local?: string;
    remote?: string;
  }[];
  mutations_are_not_here: string;
}

interface SurfacesPayload {
  worst_state?: string;
  surfaces: {
    resources?: {
      state: string;
      headline: string;
      floors_measured?: boolean;
      floors?: Record<string, unknown>;
      free?: Record<string, number>;
      leases?: number;
      orphan_jobs?: unknown[];
      orphan_working_set_mb?: number;
      why_floors_are_measured?: string;
    };
  };
}

interface ChainPayload {
  single_gpu_owner?: boolean;
  single_gpu_owner_means?: string;
  live_workers?: unknown[];
  resources?: { floors?: Record<string, number>; below_floor?: string[] };
}

interface BlockersPayload {
  gpu_policy?: string;
}

interface SystemReceiptsPayload {
  receipts?: Record<
    string,
    { content?: { working_tree?: { dirty_source_count?: number; dirty_generated_count?: number } } | null }
  >;
}

interface HealthWithInterpreter extends Health {
  interpreter?: {
    executable?: string;
    prefix?: string;
    base_prefix?: string;
    in_a_virtualenv?: boolean;
    version?: string;
    why_this_is_reported?: string;
  };
  run_roots?: string[];
}

interface WorkspaceRow {
  slug?: string;
  name?: string;
  kind?: string;
  fixture_only?: boolean;
}
interface WorkspacesPayload {
  workspaces?: WorkspaceRow[];
  items?: WorkspaceRow[];
}

interface ProcessContractPayload {
  schema: string;
  scroll?: string | null;
  complete: boolean;
  headline: string;
  counts: { total: number; wired: number; open: number };
  stages: { id: string; label: string; state: string; gap: string }[];
  providers: {
    id: string;
    stage: string;
    role: string;
    lifecycle: string;
    path?: string | null;
    detail: string;
  }[];
  retrieval: {
    packet_type_built: boolean;
    generic_fetch_ui: boolean;
    state: string;
    detail: string;
    receipt?: string;
    failed_gates?: string[];
    fetched_bytes?: number;
  };
  auto_configuration: {
    fingerprint_built: boolean;
    qualified_recipe_registry_built: boolean;
    library_workflow_wired: boolean;
    known_scroll: string;
    unknown_scroll: string;
  };
  replacement_policy: {
    automatic: string[];
    operator_only: string;
    existing_projects: string;
    contract: string;
  };
  hecate_needs: string[];
  blender_role: string;
}

interface ProviderInventoryPayload {
  schema: string;
  read_only: boolean;
  counts: {
    total: number;
    available: number;
    adapter_ready: number;
    qualified: number;
    by_lifecycle_state?: Record<string, number>;
  };
  providers: (ProviderRecord & {
    source: string;
    input_contract: string;
    output_contract: string;
    coordinate_schema: string;
    license: string;
    controls: string[];
    available: boolean;
  })[];
  capability_ledger?: Record<string, { callable?: string[] }>;
}

interface PipelinePlanPayload {
  schema: string;
  scroll?: string | null;
  read_only: boolean;
  complete: boolean;
  first_gap: { stage: string; state: string; why: string } | null;
  next: string;
  claim_ceiling: string;
  stages: { id: string; plan_state: string; provider_id: string | null; why: string }[];
}

interface UserDataPayload {
  contract: string;
  user_data_root: string;
  root_ok: boolean;
  root_exists: boolean;
  mode: { mode: string; reason?: string; quarantine?: string };
  public_cache_roots: string[];
  categories: Record<string, { subdir: string; rationale: string }>;
  inventory: { items: number; present: number; bytes: number;
    by_category: Record<string, { items: number; present: number; bytes: number }> };
  detach_plan: { ready: boolean; files: number; bytes: number; root: string; quarantine: string;
    changes: string[]; keeps: string[]; deletes: number; plan_sha256: string; note: string;
    refusal?: { code: string; why: string } };
  release_boundary: { rule: string; verify_command: string; public_data_is_not_user_data: boolean };
}


const TABS = [
  { id: "machine", label: "Machine", hint: "accelerator, disks, host" },
  { id: "process", label: "Full process", hint: "raw CT through translation, provider by provider" },
  { id: "safe", label: "Safe to run now", hint: "measured floors against free resources" },
  { id: "storage", label: "Storage lifecycle", hint: "the conveyor's catalog" },
  { id: "privacy", label: "Local data", hint: "personal data root, release boundary and recoverable detach" },
  { id: "environment", label: "Environment", hint: "interpreter, lock, dependencies" },
  { id: "health", label: "Service health", hint: "what is answering, and the ledger" },
  { id: "updates", label: "Provider updates", hint: "what changed upstream, tested or not, activate, roll back" },
  { id: "problems", label: "Open problems", hint: "official challenge fronts and every current Villa issue mapped to the ARGUS route" },
  { id: "install", label: "Install tiers", hint: "Core, Geometry, Advanced, Remote: what is ready, and the plan for the rest" },
  { id: "operations", label: "Operations surfaces", hint: "resources, updates, downloads, release" },
  { id: "act", label: "Governed actions", hint: "the only place this interface writes" },
  { id: "lifecycle", label: "Checkpoint lifecycle", hint: "which learned state may travel where, and why" },
  { id: "developer", label: "Developer data", hint: "test fixtures, and why each is one" },
  { id: "about", label: "About", hint: "what ARGUS is, and what it has not done" },
] as const;

type TabId = (typeof TABS)[number]["id"];

function resolveTab(asked: string | null): TabId {
  if (!asked) return "machine";
  if (asked === "overview" || asked === "capability") return "machine";
  if (asked === "jobs" || asked === "ingest" || asked === "workspace" || asked === "collections") {
    return "machine";
  }
  return (TABS.some((t) => t.id === asked) ? asked : "machine") as TabId;
}


export function SystemScreen({ feed }: { feed?: FeedState }) {
  const [params] = useSearchParams();
  if (params.get("tab") === "jobs") {
    return <Navigate to="/jobs" replace />;
  }
  if (params.get("tab") === "observatory") {
    const q = new URLSearchParams(params);
    q.set("tab", "archive");
    return <Navigate to={`/explore?${q.toString()}`} replace />;
  }
  return <SystemScreenBody feed={feed} />;
}

function SystemScreenBody({ feed }: { feed?: FeedState }) {
  const [params, setParams] = useSearchParams();
  const tab = resolveTab(params.get("tab"));

  const oversight = usePoll<Oversight>("/api/oversight", { intervalMs: 60000 });
  const runtime = usePoll<RuntimePayload>("/api/runtime", { intervalMs: 60000 });
  const storage = usePoll<StoragePayload>("/api/storage", { intervalMs: 60000 });
  const userData = usePoll<UserDataPayload>("/api/user_data", { intervalMs: 120000, timeoutMs: 120000 });
  const surfaces = usePoll<SurfacesPayload>("/api/surfaces", { intervalMs: 120000 });
  const chain = usePoll<ChainPayload>("/api/chain", { intervalMs: 60000 });
  const blockers = usePoll<BlockersPayload>("/api/blockers", { intervalMs: 120000 });
  const { health, error: healthErr } = useHealth();
  const integrity = useIntegrity(60000);
  const activity = useActivity(30000);
  const cg = useCapabilityGraph(120000);
  const upstream = usePoll<UpstreamStatusPayload>("/api/upstream_status", { intervalMs: 120000 });
  const villaScience = usePoll<VillaSciencePayload>("/api/villa/science", { intervalMs: 120000 });
  const receiptIndex = usePoll<SystemReceiptsPayload>("/api/receipts", { intervalMs: 120000 });
  const selectedScroll = params.get("scroll");
  const processContract = usePoll<ProcessContractPayload>(
    `/api/process_contract${selectedScroll ? `?scroll=${encodeURIComponent(selectedScroll)}` : ""}`,
    { intervalMs: 120000 },
  );
  const providerInventory = usePoll<ProviderInventoryPayload>("/api/providers", { intervalMs: 120000 });
  const pipelinePlan = usePoll<PipelinePlanPayload>(
    `/api/pipeline/plan${selectedScroll ? `?scroll=${encodeURIComponent(selectedScroll)}` : ""}`,
    { intervalMs: 120000 },
  );

  const suppressed = certificationIsSuppressed(integrity.data);
  const res = surfaces.data?.surfaces?.resources ?? null;

  return (
    <OpsPage
      control="system"
      title="System"
      lede={
        <>
          What this machine is, what it can do right now, and what it is permitted to do. There
          is deliberately no overall score and no all-clear: each row carries how it was
          measured, so a real problem cannot hide behind a summary.
        </>
      }
    >
      <ScrollStatusBar control="system.scroll.status" quietWithoutScroll />
      {}
      <div data-novice={res?.state === "BELOW_FLOOR" ? "missing" : "ready"}>
        <OpsHeadline
          tone={
            res?.state === "BELOW_FLOOR" ? "bad" : res ? "ok" : suppressed.suppressed ? "warn" : "idle"
          }
          control="system.headline"
          what={
            res
              ? res.state === "BELOW_FLOOR"
                ? `Not safe to start a job of the measured class: ${res.headline}.`
                : `Resources clear: ${res.headline}.`
              : "Measuring this machine…"
          }
          detail={
            <>
              {suppressed.suppressed
                ? "Separately: the command ledger's hash chain does not verify, so no green scientific certification is displayed anywhere in this interface. That is a statement about the record of what ran, not about this hardware."
                : null}
              {activity.data ? <div className="ops-note">{activity.data.headline}</div> : null}
              <OpsSource route="/api/surfaces" field="surfaces.resources.state, .headline" />
            </>
          }
        />
      </div>

      <div className="ops-badges system-signal-strip" data-control="system.signal-summary">
        <OpsBadge tone={health?.scan?.ttl_exceeds_poll ? "ok" : "warn"} control="system.signal.cache">
          snapshot cache {health?.scan?.ttl_exceeds_poll ? "outlives" : "expires before"} the poll
          <span className="ops-badge-detail">
            · ttl {health?.scan?.snapshot_ttl_s ?? "?"}s vs poll {health?.scan?.ws_poll_s ?? "?"}s
          </span>
        </OpsBadge>
        <OpsBadge
          tone={
            receiptIndex.data?.receipts?.upstream_lock?.content?.working_tree?.dirty_source_count == null
              ? "warn"
              : receiptIndex.data.receipts.upstream_lock.content.working_tree.dirty_source_count > 0
                ? "bad"
                : "ok"
          }
          control="system.signal.source-dirt"
        >
          {receiptIndex.data?.receipts?.upstream_lock?.content?.working_tree?.dirty_source_count ?? "?"} source files modified
          <span className="ops-badge-detail">· must be zero</span>
        </OpsBadge>
        <OpsBadge tone="info" control="system.signal.generated-dirt">
          {receiptIndex.data?.receipts?.upstream_lock?.content?.working_tree?.dirty_generated_count ?? "?"} generated files differ
        </OpsBadge>
        <span className="ops-note system-not-measured">
          Not measured here: WSL, Docker and VHDX virtual disks.
        </span>
      </div>

      <nav className="ops-subtabs system-tabs" aria-label="System sections">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            className="ops-subtab"
            aria-current={t.id === tab ? "true" : undefined}
            aria-pressed={t.id === tab}
            title={t.hint}
            data-control={`system.tab.${t.id}`}
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

      {tab === "process" ? (
        <>
          <SelectedScrollHandoff scroll={selectedScroll} />
          <ProcessContract payload={processContract.data} inventory={providerInventory.data} pipeline={pipelinePlan.data} selectedScroll={selectedScroll} />
        </>
      ) : tab === "machine" ? (
        <Machine oversight={oversight} cg={cg.data} upstream={upstream.data} villaScience={villaScience.data} />
      ) : tab === "safe" ? (
        <SafeToRun res={res} chain={chain.data} blockers={blockers.data} oversight={oversight.data} />
      ) : tab === "storage" ? (
        <StorageLifecycle storage={storage} />
      ) : tab === "privacy" ? (
        <LocalDataBoundary payload={userData.data} failure={userData.failure} onRefresh={userData.refresh} />
      ) : tab === "environment" ? (
        <Environment runtime={runtime} oversight={oversight.data} health={health as HealthWithInterpreter | null} />
      ) : tab === "health" ? (
        <ServiceHealthTab
          health={health as HealthWithInterpreter | null}
          error={healthErr}
          runtime={runtime.data}
          integrity={integrity}
          feed={feed}
        />
      ) : tab === "operations" ? (
        <OpsSection
          control="system.operations"
          title="Operations surfaces"
          hint="Status surfaces from one assembler: resources, updates, downloads and release. Deliberately not separate routes — two readers of the same fact drift, and the drift is invisible because each is internally consistent."
        >
          <OperationsPanel />
          <OpsSource route="/api/surfaces" field="surfaces" />
        </OpsSection>
      ) : tab === "updates" ? (
        <OpsSection control="system.updates" title="Provider updates" hint="Upstream providers change. ARGUS watches them, compares each change against the adapter contract, and never lets an untested revision touch a scientific run. Existing runs stay pinned.">
          <TargetRegistryStatus />
          <UpdatesPanel />
        </OpsSection>
      ) : tab === "problems" ? (
        <OpsSection control="system.problems" title="Official open problems" hint="The whole ScrollPrize problem, not only the selected scroll: what ARGUS covers, what it merely guards, and what still needs real external proof.">
          <OpenProblemsPanel />
        </OpsSection>
      ) : tab === "install" ? (
        <OpsSection control="system.install" title="Install tiers" hint="Four tiers, each READY, DEGRADED, MISSING or UNKNOWN. Core setup belongs to the packaged Docker/launcher path; optional host-tool repair stays outside the standard UI until its installer is signed and governed.">
          <InstallTiersPanel />
        </OpsSection>
      ) : tab === "act" ? (
        <GovernedActions />
      ) : tab === "lifecycle" ? (
        <>
          {}
          <p className="ops-note" data-control="system.lifecycle.models-link">
            <Link to="/models" data-control="system.open-models">Open the model registry</Link>: every detector ARGUS can reach, what it has seen, and whether a frozen evaluation has spoken about it.
          </p>
          <CheckpointLifecycleTab />
        </>
      ) : tab === "about" ? (
        <About />
      ) : (
        <DeveloperData feed={feed} />
      )}
    </OpsPage>
  );
}


function LocalDataBoundary({ payload, failure, onRefresh }: {
  payload: UserDataPayload | null;
  failure: Failure | null;
  onRefresh: () => void;
}) {
  return <Private kind="local_path">
    <OpsSection
      control="system.user-data"
      title="Local personal data and the release boundary"
      hint="ARGUS code and public scientific stores stay separate from the operator's notes, identity, strategy, annotations, candidate maps, private receipts and credential pointers. This panel is absent in public-demo mode."
      aside={<OpsBadge tone={payload?.root_ok && payload?.mode.mode === "CONNECTED" ? "ok" : "warn"}>{payload?.mode.mode ?? "reading…"}</OpsBadge>}
    >
      {failure ? <OpsFailure what="The local user-data contract" failure={failure} onRetry={onRefresh} control="system.user-data.read" /> : null}
      {!payload ? <OpsUnknown what="The local user-data contract" why="/api/user_data has not answered yet." /> : <>
        <OpsFacts>
          <OpsFact label="Active private root">{demoText(payload.user_data_root)}</OpsFact>
          <OpsFact label="Private inventory">{payload.inventory.present}/{payload.inventory.items} items · {bytesLabel(payload.inventory.bytes)}</OpsFact>
          <OpsFact label="Release rule">{payload.release_boundary.rule}</OpsFact>
          <OpsFact label="Public stores kept">{payload.public_cache_roots.length} declared roots</OpsFact>
        </OpsFacts>
        <p className="ops-note">
          A release contains the product and explicitly allowlisted public evidence. It does not contain this root,
          a renamed copy of one of its files, credential values, operator identity, private strategy, candidate maps,
          or local machine paths. Historical receipts are not rewritten; the release exporter withholds them when
          their path, content hash or content shape is private.
        </p>
        <ul className="ops-list" data-control="system.user-data.categories">
          {Object.entries(payload.categories).map(([name, meta]) => {
            const row = payload.inventory.by_category[name];
            const present = row?.present ?? 0;
            const items = row?.items ?? 0;
            return <OpsItem key={name} tone={present ? "warn" : "idle"}
              title={name.replaceAll("_", " ")}
              state={`${present}/${items} present`}>
              <div className="ops-item-body"><strong>{bytesLabel(row?.bytes ?? 0)}</strong> on this machine</div>
              <div className="ops-item-body">{meta.rationale}</div>
            </OpsItem>;
          })}
        </ul>
        <OpsDetails control="system.user-data.detach" summary="Detach my personal ARGUS layer">
          <p className="ops-note">
            Detach is a recoverable same-volume move, not deletion. It closes legacy fallback first, moves only the
            active private root into quarantine, and leaves scans, cache, models, runtimes, source and historical
            evidence untouched. Permanent deletion is deliberately a separate decision.
          </p>
          <OpsFacts>
            <OpsFact label="Would detach">{payload.detach_plan.files} files · {bytesLabel(payload.detach_plan.bytes)}</OpsFact>
            <OpsFact label="Would delete">{payload.detach_plan.deletes} bytes</OpsFact>
            <OpsFact label="Recoverable quarantine">{demoText(payload.detach_plan.quarantine)}</OpsFact>
          </OpsFacts>
          {payload.detach_plan.refusal ? <p className="ops-note">Not ready: {payload.detach_plan.refusal.code} — {payload.detach_plan.refusal.why}</p> : null}
          <PlanApprove action="user_data.detach" controlId="user-data-detach"
            params={{ confirmation: "DETACH LOCAL USER DATA" }} label="detach the personal data layer"
            why="moves the active private root to recoverable quarantine; deletes nothing"
            disabledReason={payload.detach_plan.ready ? null : payload.detach_plan.refusal?.why ?? "detach plan is not ready"}
            onDone={onRefresh} />
        </OpsDetails>
        <OpsSource route="/api/user_data" field="mode, inventory, categories, detach_plan, release_boundary" />
      </>}
    </OpsSection>
  </Private>;
}


function SelectedScrollHandoff({ scroll }: { scroll: string | null }) {
  if (!scroll) return null;
  return <SelectedScrollHandoffBody scroll={scroll} />;
}

function SelectedScrollHandoffBody({ scroll }: { scroll: string }) {
  const live = usePoll<SelectedScrollStatusPayload>(
    `/api/scroll_status?scroll=${encodeURIComponent(scroll)}`,
    { intervalMs: 30000 },
  );
  const status = live.data;
  const next = status?.next_action ?? null;
  const blocked = status?.blocker?.text ?? next?.why ?? "The selected scroll status has not answered yet.";
  const nextSteps = (status?.steps ?? []).filter((step) => step.state !== "DONE").slice(0, 3);
  return (
    <OpsSection
      control="system.selected-scroll.handoff"
      title={`What to do next for ${status?.display ?? scroll}`}
      hint="This is the selected scroll's own next action. It is not a generic machine recommendation and it never exposes a shell command."
      aside={next?.operator_only ? <OpsBadge tone="warn">operator action</OpsBadge> : null}
    >
      {!status ? (
        <OpsUnknown what={`The live status for ${scroll}`} why="/api/scroll_status has not answered yet." />
      ) : (
        <>
          <OpsHeadline
            tone={next ? "warn" : "idle"}
            control="system.selected-scroll.next"
            what={next?.label ?? "No next action is currently declared"}
            detail={
              <>
                <div className="ops-note">{blocked}</div>
                {status.claim_ceiling ? <div className="ops-note">Claim ceiling: {status.claim_ceiling}</div> : null}
                <OpsSource route="/api/scroll_status" field="next_action, blocker, steps, claim_ceiling" />
              </>
            }
          />
          {nextSteps.length ? (
            <OpsFacts>
              {nextSteps.map((step) => (
                <OpsFact key={step.n} label={`Stage ${step.n}: ${step.label}`}>
                  {step.state.toLowerCase().replace(/_/g, " ")} — {step.why}
                </OpsFact>
              ))}
            </OpsFacts>
          ) : null}
          <div className="ops-actions">
            {next?.step === "acquisition" ? (
              <Link className="ag-btn-primary" to={`/sources?scroll=${encodeURIComponent(scroll)}`} data-control="system.selected-scroll.sources">
                Inspect available sources
              </Link>
            ) : null}
            <Link className="ag-btn" to={`/explore?tab=scrolls&scroll=${encodeURIComponent(scroll)}`} data-control="system.selected-scroll.back-to-scroll">
              Back to this scroll
            </Link>
          </div>
        </>
      )}
    </OpsSection>
  );
}

function processTone(state: string): OpsTone {
  if (state === "WIRED" || state === "ADAPTER_READY_AVAILABLE") return "ok";
  if (state.includes("NOT_BUILT") || state.includes("NOT_INSTALLED") || state.includes("UNQUALIFIED")) return "bad";
  if (state.includes("ON_HOLD") || state.includes("NOT_REGISTERED")) return "warn";
  return "info";
}

function ProcessContract({
  payload,
  inventory,
  pipeline,
  selectedScroll,
}: {
  payload: ProcessContractPayload | null;
  inventory: ProviderInventoryPayload | null;
  pipeline: PipelinePlanPayload | null;
  selectedScroll: string | null;
}) {
  if (!payload) {
    return (
      <OpsSection
        control="system.process"
        title="Raw CT through translation"
        hint="The complete physical and product process, including stages that are not built."
      >
        <OpsUnknown what="The full process contract" why="/api/process_contract has not answered yet." />
      </OpsSection>
    );
  }
  const blender = payload.providers.find((p) => p.id === "blender");
  const hecate = payload.providers.find((p) => p.id === "hecate_96um");
  return (
    <>
      <OpsHeadline
        tone={payload.complete ? "ok" : "bad"}
        control="system.process.headline"
        what={payload.complete ? "Raw CT to translation is complete." : "Raw CT to translation is not complete yet."}
        detail={
          <>
            {payload.headline}
            {selectedScroll ? <div className="ops-note">Selected material: {selectedScroll}</div> : null}
            <OpsSource route="/api/process_contract" field="headline, stages[]" />
          </>
        }
      />

      <OpsSection
        control="system.process.next"
        title="The next governed handoff"
        hint="One compact answer from the same ordered pipeline plan. Expand the route below for every stage."
        aside={<OpsBadge tone={pipeline?.complete ? "ok" : "warn"}>{pipeline?.complete ? "ready to run gates" : "action required"}</OpsBadge>}
      >
        {!pipeline ? <OpsUnknown what="The ordered pipeline plan" why="/api/pipeline/plan has not answered yet." /> : (
          <OpsFacts>
            <OpsFact label="First gap">{pipeline.first_gap ? `${pipeline.first_gap.stage}: ${pipeline.first_gap.why}` : "none declared"}</OpsFact>
            {selectedScroll ? null : <OpsFact label="Next">{pipeline.next}</OpsFact>}
            <OpsFact label="Claim ceiling">{pipeline.claim_ceiling}</OpsFact>
          </OpsFacts>
        )}
        <OpsSource route="/api/pipeline/plan" field="first_gap, next, stages[]" />
      </OpsSection>

      <div className="ops-stats">
        <OpsStat control="system.process.total" label="Physical stages named" value={payload.counts.total} source="/api/process_contract" />
        <OpsStat control="system.process.wired" label="Fully wired" value={payload.counts.wired} tone={payload.counts.wired === payload.counts.total ? "ok" : "warn"} source="/api/process_contract" />
        <OpsStat control="system.process.open" label="Still bounded or open" value={payload.counts.open} tone={payload.counts.open ? "bad" : "ok"} source="/api/process_contract" />
      </div>

      <OpsSection
        control="system.process.route"
        title="One process, in physical order"
        hint="A compact map. Open states remain in the route rather than disappearing from the product."
      >
        <OpsTable control="system.process.route.table">
          <thead><tr><th>Stage</th><th>State</th><th>What remains</th></tr></thead>
          <tbody>
            {payload.stages.map((stage) => (
              <tr key={stage.id} data-control={`system.process.stage.${stage.id}`}>
                <td>{stage.label}</td>
                <td><OpsBadge tone={processTone(stage.state)}>{stage.state.toLowerCase().replace(/_/g, " ")}</OpsBadge></td>
                <td>{stage.gap}</td>
              </tr>
            ))}
          </tbody>
        </OpsTable>
        <OpsSource route="/api/process_contract" field="stages[]" />
      </OpsSection>

      <OpsSection
        control="system.process.retrieval"
        title={selectedScroll ? `Retrieve ${selectedScroll}` : "Library retrieval and automatic setup"}
        hint="Retrieval is governed separately from whether upstream has published the bytes."
        aside={<OpsBadge tone={payload.retrieval.generic_fetch_ui ? "ok" : "warn"}>{payload.retrieval.state.toLowerCase().replace(/_/g, " ")}</OpsBadge>}
      >
        <OpsFacts>
          <OpsFact label="Acquisition packet">{payload.retrieval.packet_type_built ? "built" : "not built"}</OpsFact>
          <OpsFact label="Library fetch control">{payload.retrieval.generic_fetch_ui ? "wired" : "not exposed yet"}</OpsFact>
          <OpsFact label="Current result">{payload.retrieval.detail}</OpsFact>
          {payload.retrieval.failed_gates?.length ? <OpsFact label="Failed or unknown gates">{payload.retrieval.failed_gates.join(", ")}</OpsFact> : null}
        </OpsFacts>
        <OpsQuote cite="/api/process_contract -> auto_configuration.unknown_scroll">
          Known material may receive only a qualified recipe inside the declared distance ceiling. Unknown material returns NO_MATCH and opens a bounded qualification lane; ARGUS does not guess settings until something looks convincing.
        </OpsQuote>
        <OpsSource route="/api/process_contract" field="retrieval, auto_configuration" />
      </OpsSection>

      <OpsSection
        control="system.process.providers"
        title="Blender, Hecate and upstream replacement"
        hint="Provider lifecycle is distinct from the physical stage: optional, active, candidate and on-hold are not synonyms."
      >
        <OpsFacts>
          <OpsFact label="Blender">
            <OpsBadge tone={blender ? processTone(blender.lifecycle) : "idle"}>{blender?.lifecycle.toLowerCase().replace(/_/g, " ") ?? "unknown"}</OpsBadge>
            <div className="ops-note">{blender?.detail ?? payload.blender_role}</div>
          </OpsFact>
          <OpsFact label="Hecate">
            <OpsBadge tone={hecate ? processTone(hecate.lifecycle) : "idle"}>{hecate?.lifecycle.toLowerCase().replace(/_/g, " ") ?? "unknown"}</OpsBadge>
            <div className="ops-note">{hecate?.detail ?? "No Hecate provider record."}</div>
          </OpsFact>
          <OpsFact label="Automatic refit">{payload.replacement_policy.automatic.join(" -> ")}</OpsFact>
          <OpsFact label="Promotion boundary">{payload.replacement_policy.operator_only}. {payload.replacement_policy.existing_projects}.</OpsFact>
        </OpsFacts>
        <OpsDetails summary="What remains before Hecate can produce admissible evidence" control="system.process.hecate-needs">
          <ul className="ops-list">{payload.hecate_needs.map((need) => <li key={need}>{need}</li>)}</ul>
        </OpsDetails>
        <OpsDetails summary="Every registered provider and lifecycle" control="system.process.providers-all">
          <OpsTable control="system.process.providers.table">
            <thead><tr><th>Provider</th><th>Role</th><th>Lifecycle</th><th>Meaning</th></tr></thead>
            <tbody>{payload.providers.map((provider) => (
              <tr key={provider.id}><td className="ops-mono">{provider.id}</td><td>{provider.role.replace(/_/g, " ")}</td><td><OpsBadge tone={processTone(provider.lifecycle)}>{provider.lifecycle.toLowerCase().replace(/_/g, " ")}</OpsBadge></td><td>{provider.detail}</td></tr>
            ))}</tbody>
          </OpsTable>
        </OpsDetails>
        <OpsDetails summary="Adapter registry: immutable revisions and controls" control="system.process.provider-registry">
          {!inventory ? <OpsUnknown what="The adapter registry" why="/api/providers has not answered yet." /> : (
            <>
              <div className="ops-inline-stats">
                {inventory.counts.available} available · {inventory.counts.adapter_ready} adapter-ready · {inventory.counts.qualified} qualified · all promotion operator-only
                {inventory.counts.by_lifecycle_state ? (
                  <> · {Object.entries(inventory.counts.by_lifecycle_state).filter(([, n]) => n > 0).map(([state, n]) => `${n} ${stateLabel(state)}`).join(" · ")}</>
                ) : null}
              </div>
              <OpsTable control="system.process.provider-registry.table">
                <thead><tr><th>Provider / revision</th><th>State</th><th>Blocker</th><th>Hardware</th><th>Stage</th><th>Input → output</th><th>Coordinates</th><th>Controls</th></tr></thead>
                <tbody>{inventory.providers.map((provider) => (
                  <tr key={`${provider.id}-${provider.revision}`} data-control={`system.process.provider.${provider.id}`}>
                    <td className="ops-mono">{provider.id}<br /><span className="small">{provider.revision}</span></td>
                    <td>
                      <OpsBadge tone={lifecycleTone(provider.lifecycle_state)}>{stateLabel(provider.lifecycle_state)}</OpsBadge>
                      {provider.claim_ceiling ? <><br /><span className="small">{provider.claim_ceiling}</span></> : null}
                    </td>
                    <td>
                      {provider.blocker ? (
                        <>
                          <OpsBadge tone="bad" title={provider.blocker.text}>{provider.blocker.kind}</OpsBadge>
                          <br /><span className="small">{provider.blocker.text}</span>
                        </>
                      ) : "none"}
                    </td>
                    <td>
                      {provider.hardware_profile ? (
                        <>{provider.hardware_profile.summary}<br /><span className="small">{provider.hardware_profile.state.toLowerCase().replace(/_/g, " ")}</span></>
                      ) : "not reported"}
                    </td>
                    <td>{provider.stage}</td>
                    <td>{provider.input_contract}<br />→ {provider.output_contract}</td>
                    <td>{provider.coordinate_schema}</td>
                    <td>{provider.controls.join(" · ")}</td>
                  </tr>
                ))}</tbody>
              </OpsTable>
            </>
          )}
          <OpsSource route="/api/providers" field="providers[], counts" />
        </OpsDetails>
        {inventory ? <ProviderPlanForm inventory={inventory} selectedScroll={selectedScroll} /> : null}
        <OpsSource route="/api/process_contract" field="providers[], replacement_policy, hecate_needs" />
      </OpsSection>
    </>
  );
}


function About() {
  return (
    <section className="about" data-control="system.about" aria-label="About ARGUS">
      <img
        className="about-logo"
        src="/brand/argus-logo-primary.png"
        alt="ARGUS"
        width={1648}
        height={822}
      />
      <p className="about-lede">
        Given a registered CT volume, ARGUS chooses a valid route, produces a verified 2-D
        representation when the evidence supports one, and refuses — with a class and a reason —
        when geometry or ink confidence is insufficient.
      </p>
      <div className="about-standing">
        <h2>Where it stands</h2>
        <p>
          Every figure elsewhere in this interface comes from a receipt, and every refusal carries
          its reason.
        </p>
      </div>
      <dl className="about-facts">
        <dt>Built by</dt>
        <dd>DarthCeltic and clexious.</dd>
        <dt>Licence</dt><dd>Apache-2.0 (see LICENSE and NOTICE in the repository)</dd>
        <dt>Upstream</dt>
        <dd>
          The Vesuvius Challenge villa tooling runs as a pinned external dependency. Copyleft tools
          stay behind a subprocess boundary and are never copied into this codebase.
        </dd>
        <dt>Mode</dt><dd>This interface reads. Work that changes anything goes through the governed job path.</dd>
      </dl>

      {
}
      <div className="about-standing" data-control="system.about.thanks">
        <h2>Thanks</h2>
        <p>
          ARGUS exists because other people built and published real tools and shared real
          data, and we used a great deal of it — pulled in wholesale, stripped down, and put to
          work. That is not a footnote; it is most of the foundation this instrument stands on.
        </p>
        <ul className="ops-list">
          <li>
            <b>The Vesuvius Challenge</b> — for scanning these scrolls, publishing the data
            openly, and building the community, tooling, and prize structure that makes any of
            this possible in the first place.
          </li>
          <li>
            <b>ScrollPrize/villa</b> (volume-cartographer, VC3D, the segmentation and rendering
            tools) — GPL-3.0, run as a pinned external dependency and never copied into this
            codebase, exactly because we mean to respect the terms it ships under, not route
            around them.
          </li>
          <li>
            <b>hengck23</b> — for open-sourcing real, working ink-detection solution code (MIT
            licence), which ARGUS ships under its licence.
          </li>
          <li>
            <b>timm, PyTorch, OpenCV, and zarr</b> — the model and array tooling underneath
            nearly everything here (Apache-2.0 / BSD-3-Clause).
          </li>
          <li>
            <b>The Kaggle team</b> — for the official Python client (Apache-2.0) that ARGUS's
            governed kernel path runs on.
          </li>
        </ul>
        <p className="ops-hint">
          The complete, generated list — including what is used but never redistributed, and
          what is deliberately left UNDECLARED rather than guessed at — is in
          THIRD_PARTY_ACKNOWLEDGEMENTS.md in the repository.
        </p>
      </div>
    </section>
  );
}


function Machine({
  oversight,
  cg,
  upstream,
  villaScience,
}: {
  oversight: ReturnType<typeof usePoll<Oversight>>;
  cg: ReturnType<typeof useCapabilityGraph>["data"];
  upstream: UpstreamStatusPayload | null;
  villaScience: VillaSciencePayload | null;
}) {
  const o = oversight.data;
  return (
    <>
      <OpsSection
        control="system.accelerator"
        title="Accelerator"
        hint="Measured by querying the card, not read from a config file. A card that did not answer is reported as unknown — never as an idle card and never as zero."
        aside={
          o ? (
            <OpsBadge tone={o.gpu.present ? "ok" : "bad"} control="system.accelerator.present">
              {o.gpu.present ? "visible" : "none visible"}
            </OpsBadge>
          ) : null
        }
      >
        {oversight.failure && !o ? (
          <ul className="ops-list">
            <OpsFailure
              what="The oversight probe"
              failure={oversight.failure}
              onRetry={oversight.refresh}
              control="system.accelerator"
            />
          </ul>
        ) : !o ? (
          <ul className="ops-list">
            <OpsUnknown what="The accelerator" why="/api/oversight has not answered yet." />
          </ul>
        ) : o.gpu.present ? (
          <>
            <div className="ops-stats">
              {(o.gpu.cards ?? []).map((c) => (
                <OpsStat
                  key={c.name}
                  control={`system.accelerator.card.${c.name.replace(/[^a-z0-9]/gi, "")}`}
                  label={`${c.name} · driver ${c.driver}`}
                  value={
                    c.vram_total_mib
                      ? `${((c.vram_used_mib ?? 0) / 1024).toFixed(1)} / ${(c.vram_total_mib / 1024).toFixed(1)} GiB`
                      : "VRAM unknown"
                  }
                  source={`measured by ${o.gpu.source ?? "an unnamed probe"}`}
                />
              ))}
            </div>
          </>
        ) : (
          <ul className="ops-list">
            <OpsUnknown
              what="An accelerator"
              why={o.gpu.consequence ?? o.gpu.why ?? "the probe reported nothing"}
            />
          </ul>
        )}
        <OpsSource route="/api/oversight" field="gpu" />
      </OpsSection>

      <OpsSection
        control="system.upstream"
        title="Upstream dependency"
        hint="The pinned Villa checkout, the local origin/main ref, and the last differential receipt are separate facts. This panel never fetches or moves the pin."
        aside={
          upstream ? (
            <OpsBadge
              tone={upstream.state === "PINNED_WITH_DRIFT" ? "info" : upstream.state === "BLOCKED" ? "bad" : "warn"}
              control="system.upstream.state"
            >
              {upstream.state.toLowerCase().replace(/_/g, " ")}
            </OpsBadge>
          ) : null
        }
      >
        {!upstream ? (
          <ul className="ops-list">
            <OpsUnknown what="Upstream status" why="/api/upstream_status has not answered yet." />
          </ul>
        ) : (
          <>
            <OpsFacts>
              <OpsFact label="Declared pin">
                <span className="ops-mono">{upstream.pin?.declared ?? "unknown"}</span>
              </OpsFact>
              <OpsFact label="Checkout agrees">
                {upstream.pin?.matches_checkout === true
                  ? "yes"
                  : upstream.pin?.matches_checkout === false
                    ? "no — the checkout must not be treated as the pin"
                    : "unknown"}
              </OpsFact>
              <OpsFact label="Known origin/main">
                {upstream.remote_tracking?.sha ? (
                  <span className="ops-mono">{upstream.remote_tracking.sha}</span>
                ) : (
                  "unknown — no local remote ref"
                )}
              </OpsFact>
              <OpsFact label="Differential">
                {typeof upstream.differential?.commits_main_ahead_of_pin === "number"
                  ? `${upstream.differential.commits_main_ahead_of_pin} commits / ${upstream.differential.files_changed_since_pin ?? "unknown"} changed paths since the pin`
                  : "not measured"}
              </OpsFact>
            </OpsFacts>
            {upstream.candidate?.commit ? (
              <OpsItem
                control="system.upstream.candidate"
                tone={upstream.candidate.review_status === "CANDIDATE_WIRED_NOT_PROMOTED" ? "warn" : "info"}
                title="Current upstream candidate"
                state={upstream.candidate.review_status?.toLowerCase().replace(/_/g, " ") ?? "review"}
              >
                <span className="ops-mono">{upstream.candidate.commit}</span>
                {upstream.candidate.matches_remote === true ? " matches local origin/main. " : " is not confirmed against local origin/main. "}
                {upstream.candidate.materialization ?? "Materialization is not recorded."}
                {upstream.candidate.capabilities?.length ? (
                  <div className="ops-note">
                    {upstream.candidate.capabilities.join(" · ")}
                  </div>
                ) : null}
              </OpsItem>
            ) : null}
            {upstream.checkout?.dirty ? (
              <OpsItem
                control="system.upstream.dirty"
                tone="bad"
                title="The pinned checkout is dirty"
                state="not clean"
              >
                {`${upstream.checkout.dirty_paths?.length ?? "one or more"} path(s) are dirty. A dirty upstream tree is not evidence for a clean pin.`}
                {upstream.checkout.dirty_paths?.length ? (
                  <div className="ops-note">{upstream.checkout.dirty_paths.join(", ")}</div>
                ) : null}
              </OpsItem>
            ) : null}
            <div className="ops-note">
              {upstream.actions?.next ?? "No automatic upstream action is configured."}
              {upstream.differential?.refs_last_fetched_utc
                ? ` Last recorded fetch: ${upstream.differential.refs_last_fetched_utc}.`
                : " No fetch timestamp is recorded."}
            </div>
            {villaScience ? (
              <details className="ops-disclosure" data-control="system.upstream.science-map">
                <summary>
                  Villa science map: {villaScience.counts?.changed ?? 0} changed component(s),{" "}
                  {villaScience.counts?.not_adapted ?? 0} not adapted
                </summary>
                <div className="ops-note">
                  {villaScience.components.map((component) => (
                    <div key={component.id}>
                      <span className="ops-mono">{component.id}</span>: {component.state.toLowerCase().replace(/_/g, " ")}
                      {typeof component.commits_since_stable === "number"
                        ? ` (${component.commits_since_stable} commit(s) touched it)`
                        : ""}
                    </div>
                  ))}
                  <p>{villaScience.claim_ceiling}</p>
                </div>
              </details>
            ) : null}
            <OpsSource route="/api/upstream_status" field="state, pin, remote_tracking, differential" />
            <OpsSource route="/api/villa/science" field="stable, candidate, components[], counts" />
          </>
        )}
      </OpsSection>

      <OpsSection
        control="system.disks"
        title="Disks"
        hint="Free space on every declared root. A nearly full data drive is an operational fact with consequences for anything that stages material, so it is shown as one rather than as a colour."
      >
        {!o ? (
          <ul className="ops-list">
            <OpsUnknown what="Disk usage" why="/api/oversight has not answered yet." />
          </ul>
        ) : (
          <div className="ops-stats">
            {o.disks
              .filter((d) => d.present)
              .map((d, i) => {
                const tight = (d.free_gib ?? 0) < 200;
                const critical = (d.free_gib ?? 0) < 50;
                return (
                  <div
                    className="ops-stat"
                    key={`${i}:${d.root}`}
                    data-control={`system.disks.${d.root.replace(/[^a-z0-9]/gi, "")}`}
                  >
                    <span className="ops-stat-value">
                      {d.free_gib ?? "unknown"} GiB free
                    </span>
                    <span className="ops-stat-label">
                      <span className="ops-mono">{demoText(d.root)}</span> of {d.total_gib ?? "unknown"}{" "}
                      GiB · {d.used_pct ?? "unknown"}% used
                    </span>
                    <OpsMeter
                      fraction={typeof d.used_pct === "number" ? d.used_pct / 100 : null}
                      tone={critical ? "bad" : tight ? "warn" : "ok"}
                      label={`${d.root} used`}
                    />
                    <span className="ops-badges">
                      <OpsBadge tone={critical ? "bad" : tight ? "warn" : "ok"}>
                        {critical ? "critically low" : tight ? "tight" : "comfortable"}
                      </OpsBadge>
                    </span>
                  </div>
                );
              })}
          </div>
        )}
        <div className="ops-note">
          One row per DECLARED root, not per physical volume. A declared root that lives on a
          volume already listed above shows the same figures twice, and that repetition is
          deliberate: de-duplicating by free space would silently drop a root the service
          declares, and a root that has quietly stopped being declared is exactly the kind of
          thing this panel exists to make visible.
        </div>
        <OpsSource route="/api/oversight" field="disks[]" />
      </OpsSection>

      <OpsSection
        control="system.host"
        title="Host"
        hint="Platform, processors and memory, with the source of each measurement."
      >
        {!o ? (
          <ul className="ops-list">
            <OpsUnknown what="The host" why="/api/oversight has not answered yet." />
          </ul>
        ) : (
          <OpsFacts>
            <OpsFact label="Platform">{o.host.platform}</OpsFact>
            <OpsFact label="Processors">{o.host.cpu_logical} logical</OpsFact>
            <OpsFact label="Memory">
              {o.host.ram_gib === null
                ? `unknown (${o.host.ram_source})`
                : `${o.host.ram_gib} GiB, measured by ${o.host.ram_source}`}
            </OpsFact>
            <Private kind="local_path">
              <OpsFact label="Working directory">
                <span className="ops-mono">{o.host.cwd ?? "not reported"}</span>
              </OpsFact>
            </Private>
          </OpsFacts>
        )}
        <OpsSource route="/api/oversight" field="host" />
      </OpsSection>

      <OpsSection
        control="system.boundary"
        title="Execution boundary"
        hint="What this interface and its service are permitted to do. `ui_can_start_work: false` is the good value: it is what makes the rest of the interface's read-only claims true rather than aspirational."
      >
        {!o ? null : (
          <>
            <div className="ops-badges">
              {Object.entries(o.boundary)
                .filter(([, v]) => typeof v === "boolean")
                .map(([k, v]) => (
                  <OpsBadge
                    key={k}
                    control={`system.boundary.${k}`}
                    tone={
                      k === "ui_can_start_work" ? (v ? "bad" : "ok") : v ? "ok" : "bad"
                    }
                  >
                    {k.replace(/_/g, " ")}: {String(v)}
                  </OpsBadge>
                ))}
            </div>
            {typeof o.boundary.note === "string" ? (
              <div className="ops-note">{o.boundary.note}</div>
            ) : null}
          </>
        )}
        {cg ? (
          <>
            <div className="ops-stats">
              <OpsStat
                control="system.boundary.installed"
                label="Capabilities installed here"
                value={`${cg.counts.installed} / ${cg.counts.total}`}
                source="/api/capability_graph → counts.installed"
              />
              <OpsStat
                control="system.boundary.control"
                label="Capabilities whose mechanical controls pass"
                value={`${cg.counts.control_passed} / ${cg.counts.total}`}
                source="/api/capability_graph → counts.control_passed"
              />
              <OpsStat
                control="system.boundary.admissible"
                label="Scientific capabilities that are admissible"
                value={`${cg.counts.scientifically_admissible} / ${cg.counts.scientific_capabilities}`}
                tone={cg.counts.scientifically_admissible > 0 ? "ok" : "bad"}
                toneLabel={
                  cg.counts.scientifically_admissible > 0 ? "some admissible" : "none admissible"
                }
                source="/api/capability_graph → counts.scientifically_admissible"
              />
            </div>
            <OpsQuote cite="/api/capability_graph → headline_rule">{cg.headline_rule}</OpsQuote>
          </>
        ) : null}
        <OpsSource route="/api/oversight" field="boundary" />
      </OpsSection>
    </>
  );
}


function SafeToRun({
  res,
  chain,
  blockers,
  oversight,
}: {
  res: NonNullable<SurfacesPayload["surfaces"]["resources"]> | null;
  chain: ChainPayload | null;
  blockers: BlockersPayload | null;
  oversight: Oversight | null;
}) {
  const floors = (res?.floors ?? {}) as Record<string, unknown>;
  const free = res?.free ?? {};
  const pairs: { key: string; label: string; freeMb?: number; floorMb?: number }[] = [
    { key: "ram", label: "Working memory", freeMb: free.ram_free_mb, floorMb: numOf(floors.ram_floor_mb) },
    { key: "vram", label: "Accelerator memory", freeMb: free.vram_free_mb, floorMb: numOf(floors.vram_floor_mb) },
    { key: "disk", label: "Disk for outputs", freeMb: free.disk_free_mb, floorMb: numOf(floors.disk_floor_mb) },
  ];

  return (
    <>
      <OpsSection
        control="system.safe"
        title="Is it safe to start a job right now"
        hint="Free resources against the floors a real run of this class actually reached. Nothing here decides for you; it reports the comparison and the basis of each number."
        aside={
          res ? (
            <OpsBadge
              tone={res.state === "BELOW_FLOOR" ? "bad" : "ok"}
              control="system.safe.state"
            >
              {res.state.toLowerCase().replace(/_/g, " ")}
            </OpsBadge>
          ) : null
        }
      >
        {!res ? (
          <ul className="ops-list">
            <OpsUnknown
              what="The resource comparison"
              why="/api/surfaces has not answered with a resources surface, so no safety judgement is offered. An unmeasured floor is not a cleared floor."
            />
          </ul>
        ) : (
          <>
            <OpsTable control="system.safe.table">
              <thead>
                <tr>
                  <th>Resource</th>
                  <th>Free now</th>
                  <th>Measured floor</th>
                  <th>Verdict</th>
                </tr>
              </thead>
              <tbody>
                {pairs.map((p) => {
                  const known = typeof p.freeMb === "number" && typeof p.floorMb === "number";
                  const clear = known && (p.freeMb as number) >= (p.floorMb as number);
                  return (
                    <tr key={p.key} data-control={`system.safe.row.${p.key}`}>
                      <td>{p.label}</td>
                      <td className="ops-mono">
                        {typeof p.freeMb === "number" ? `${p.freeMb} MiB` : "unknown"}
                      </td>
                      <td className="ops-mono">
                        {typeof p.floorMb === "number" ? `${p.floorMb} MiB` : "not measured"}
                      </td>
                      <td>
                        <OpsBadge tone={!known ? "idle" : clear ? "ok" : "bad"}>
                          {!known
                            ? "cannot be compared"
                            : clear
                              ? "above the floor"
                              : "BELOW the floor"}
                        </OpsBadge>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </OpsTable>
            <OpsFacts>
              <OpsFact label="Leases held">{res.leases ?? "not reported"}</OpsFact>
              <OpsFact label="Orphaned jobs holding memory">
                {(res.orphan_jobs ?? []).length === 0
                  ? `none — ${res.orphan_working_set_mb ?? 0} MiB held by orphans`
                  : `${(res.orphan_jobs ?? []).length}, holding ${res.orphan_working_set_mb ?? 0} MiB`}
              </OpsFact>
              <OpsFact label="How the floors were measured">
                {String(floors.measured_from ?? "not declared")}
                {floors.measured_utc ? (
                  <div className="ops-note">measured {String(floors.measured_utc)}</div>
                ) : null}
              </OpsFact>
              <OpsFact label="What the floors are not">
                {String(
                  floors.single_observation ??
                    "the surface declares no caveat, which is itself worth noting: a floor from one run is a lower bound, not a distribution.",
                )}
              </OpsFact>
            </OpsFacts>
            {res.why_floors_are_measured ? (
              <OpsQuote cite="/api/surfaces → resources.why_floors_are_measured">
                {res.why_floors_are_measured}
              </OpsQuote>
            ) : null}
          </>
        )}
        <OpsSource route="/api/surfaces" field="surfaces.resources.free, .floors, .leases" />
      </OpsSection>

      <OpsSection
        control="system.gpu-policy"
        title="Who owns the accelerator"
        hint="On a single-card machine this is the whole scheduling policy, and it is a policy rather than a lock: nothing here enforces it."
      >
        <OpsFacts>
          <OpsFact label="Declared policy">
            {blockers?.gpu_policy ?? "the blocker register declares no GPU policy"}
          </OpsFact>
          <OpsFact label="Chain's own check">
            {chain
              ? chain.single_gpu_owner
                ? "at most one arm has a live worker"
                : "MORE THAN ONE arm has a live worker — the policy is being violated"
              : "/api/chain has not answered"}
            {chain?.single_gpu_owner_means ? (
              <div className="ops-note">{chain.single_gpu_owner_means}</div>
            ) : null}
          </OpsFact>
          <OpsFact label="Below any declared floor">
            {chain?.resources?.below_floor?.length
              ? chain.resources.below_floor.join(", ")
              : "nothing, at the moment of the last read"}
          </OpsFact>
        </OpsFacts>
        <OpsSource route="/api/blockers" field="gpu_policy" />
        <OpsSource route="/api/chain" field="single_gpu_owner, resources.below_floor" />
      </OpsSection>

      <OpsSection
        control="system.recommendations"
        title="Recommended next steps"
        hint="Every item names the measurement that produced it, what it would unblock, and what to do. None of them is applied automatically — an oversight module that can act on its own findings will eventually act on a wrong one."
        aside={
          oversight ? (
            <OpsBadge tone="idle" control="system.recommendations.count">
              {oversight.recommendations.length} item(s)
            </OpsBadge>
          ) : null
        }
      >
        {!oversight ? (
          <ul className="ops-list">
            <OpsUnknown what="Recommendations" why="/api/oversight has not answered yet." />
          </ul>
        ) : (
          <ul className="ops-list">
            {oversight.recommendations.map((r) => (
              <OpsItem
                key={r.title}
                control={`system.recommendations.${r.priority}.${r.title.slice(0, 24).replace(/[^a-z0-9]/gi, "")}`}
                tone={r.priority === "high" ? "bad" : r.priority === "medium" ? "warn" : "info"}
                title={r.title}
                state={r.priority}
              >
                <OpsFacts>
                  <OpsFact label="Because">{r.because}</OpsFact>
                  <OpsFact label="Would unblock">{r.unblocks}</OpsFact>
                  <OpsFact label="Do">{r.action}</OpsFact>
                </OpsFacts>
              </OpsItem>
            ))}
          </ul>
        )}
        <OpsDisabled
          control="system.recommendations.apply"
          label="Apply all recommendations"
          reason="Absent by design. These are sentences for a person. A cleanup recommendation must never be executed by something that cannot see what it would touch."
        />
        <OpsSource route="/api/oversight" field="recommendations[]" />
      </OpsSection>
    </>
  );
}

function numOf(v: unknown): number | undefined {
  return typeof v === "number" ? v : undefined;
}


function storageTone(state: string): OpsTone {
  if (state.startsWith("BLOCKED")) return "bad";
  if (state === "HOT_PINNED") return "info";
  if (state === "CLOUD_VERIFIED") return "ok";
  if (state === "EVICTED") return "idle";
  return "idle";
}

function StorageLifecycle({
  storage,
}: {
  storage: ReturnType<typeof usePoll<StoragePayload>>;
}) {
  const s = storage.data;
  const blockedTotal = s ? Object.values(s.blocked ?? {}).reduce((a, b) => a + b, 0) : null;

  return (
    <>
      <OpsSection
        control="system.storage"
        title="Storage lifecycle"
        hint="The conveyor's catalog: what is hot, what is archived, what has been evicted and what cannot be touched. An EVICTED asset is present-with-a-remote, never missing — treating absent local bytes as an absent asset would report data loss every time the conveyor did its job."
        aside={
          s ? (
            <>
              <OpsBadge tone="idle" control="system.storage.count">
                {s.assets} catalogued
              </OpsBadge>
              <OpsBadge tone={s.catalog_present ? "ok" : "bad"}>
                {s.catalog_present ? "catalog present" : "catalog file absent"}
              </OpsBadge>
            </>
          ) : null
        }
      >
        {storage.failure && !s ? (
          <ul className="ops-list">
            <OpsFailure
              what="The storage catalog"
              failure={storage.failure}
              onRetry={storage.refresh}
              control="system.storage"
            />
          </ul>
        ) : !s ? (
          <ul className="ops-list">
            <OpsUnknown what="The storage catalog" why="/api/storage has not answered yet." />
          </ul>
        ) : (
          <>
            <OpsTable control="system.storage.states">
              <thead>
                <tr>
                  <th>Lifecycle state</th>
                  <th>Assets</th>
                  <th>Size</th>
                  <th>May this screen evict it</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(s.by_state).map(([k, v]) => (
                  <tr key={k} data-control={`system.storage.state.${k}`}>
                    <td>
                      <OpsBadge tone={storageTone(k)}>
                        {k.toLowerCase().replace(/_/g, " ")}
                      </OpsBadge>
                    </td>
                    <td className="ops-mono">{v.assets}</td>
                    <td className="ops-mono">{v.gib} GiB</td>
                    <td>
                      <OpsBadge tone="bad">no</OpsBadge>
                      <div className="ops-note">
                        {k.startsWith("BLOCKED")
                          ? "never, under any circumstances: this class has no verified remote copy, so eviction is data loss and not a cache miss."
                          : k === "EVICTABLE"
                            ? "the conveyor owns eviction. Two owners of one eviction policy is how an asset is dropped while its only verified copy is still uploading."
                            : "nothing on this screen mutates the catalog; the service it reads has no write route."}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </OpsTable>

            <div className="ops-stats">
              <OpsStat
                control="system.storage.pinned"
                label="Pinned — never evicted"
                value={s.pinned}
                source="/api/storage → pinned"
              />
              <OpsStat
                control="system.storage.blocked"
                label="BLOCKED_UNKNOWN — no verified remote copy"
                value={blockedTotal ?? "unknown"}
                tone="bad"
                toneLabel="never evictable"
                source="/api/storage → blocked"
              />
              <OpsStat
                control="system.storage.restore"
                label="Evicted with a restore command"
                value={s.restore_ready}
                tone="ok"
                toneLabel="reversible"
                source="/api/storage → restore_ready"
              />
              <OpsStat
                control="system.storage.evictable"
                label="Would free if the conveyor evicted now"
                value={`${s.would_free_gib_if_evicted_now} GiB`}
                source="/api/storage → would_free_gib_if_evicted_now"
              />
            </div>

            {s.evicted_without_restore_command.length ? (
              <div className="ops-refusal">
                {s.evicted_without_restore_command.length} evicted asset(s) carry NO restore
                command: {s.evicted_without_restore_command.join(", ")}. Those are the ones to
                worry about — an eviction without a way back is indistinguishable from a
                deletion.
              </div>
            ) : (
              <div className="ops-note">
                Every evicted asset carries a hydrate command, so every eviction is reversible.
              </div>
            )}

            <div className="ops-note">{s.mutations_are_not_here}</div>

            <div className="ops-controls">
              <OpsDisabled
                control="system.storage.evict"
                label="Evict eligible assets"
                reason="Absent by design. Eviction belongs to the storage conveyor and to nothing else. A second place to press it is a second owner of one policy, and the failure mode is an asset dropped while its only verified remote copy is still in flight."
              />
              <OpsDisabled
                control="system.storage.evict-blocked"
                label="Evict a BLOCKED_UNKNOWN asset"
                reason="Forbidden, not merely unavailable. A BLOCKED_UNKNOWN asset has no verified remote copy at all, so evicting one destroys the only copy. No control anywhere in this interface may touch this class, and this row exists so that rule is visible rather than implied by an absent button."
                weight="destructive"
              />
              <OpsDisabled
                control="system.storage.hydrate"
                label="Re-hydrate an evicted asset"
                reason="Absent here. Hydration is a transfer with a disk cost, and it runs on the conveyor's own localhost-only surface where the transfer can be watched."
              />
            </div>

            <OpsDetails control="system.storage.largest" summary={`The ${s.largest.length} largest catalogued assets`}>
              <OpsTable control="system.storage.largest.table">
                <thead>
                  <tr>
                    <th>Asset</th>
                    <th>Project</th>
                    <th>State</th>
                    <th>Size</th>
                    <th>Local</th>
                    <th>Remote</th>
                  </tr>
                </thead>
                <tbody>
                  {s.largest.map((a, i) => (
                    <tr key={a.asset_id ?? i}>
                      <td>
                        <span className="ops-mono">{a.name ?? "unnamed"}</span>
                        <div className="ops-note">{a.asset_id}</div>
                      </td>
                      <td>{a.project ?? "unassigned"}</td>
                      <td>
                        <OpsBadge tone={storageTone(a.state ?? "")}>
                          {(a.state ?? "unknown").toLowerCase().replace(/_/g, " ")}
                        </OpsBadge>
                      </td>
                      <td className="ops-mono">{a.gib ?? "unknown"} GiB</td>
                      <td>{a.local ?? "unknown"}</td>
                      <td>{a.remote ?? "unknown"}</td>
                    </tr>
                  ))}
                </tbody>
              </OpsTable>
            </OpsDetails>

            <OpsFacts>
              <OpsFact label="Free space the conveyor sees">
                {Object.entries(s.free_gib)
                  .map(([k, v]) => `${k} ${v === null ? "unknown" : `${v} GiB`}`)
                  .join(" · ")}
              </OpsFact>
              <OpsFact label="Catalog">
                <span className="ops-mono">{s.catalog}</span>
              </OpsFact>
            </OpsFacts>
          </>
        )}
        <OpsSource route="/api/storage" field="by_state, largest[], blocked, restore_ready" />
      </OpsSection>
    </>
  );
}


function Environment({
  runtime,
  oversight,
  health,
}: {
  runtime: ReturnType<typeof usePoll<RuntimePayload>>;
  oversight: Oversight | null;
  health: HealthWithInterpreter | null;
}) {
  const r = runtime.data;
  const interpreterDeclared = !!r?.interpreter.declared;
  const lockDeclared = !!r?.lock.present && !!r?.lock.declared_sha256;
  const torchDeclared = !!r?.torch.declared || !!r?.torch.cuda_declared;
  return (
    <>
      <OpsSection
        control="system.interpreter"
        title="Interpreter identity"
        hint="Which Python is actually running this service, and whether it is the one the project declares. The executable path is a trampoline and is the same for every environment over one base — the prefix is what decides which packages are in play."
        aside={
          r ? (
            <OpsBadge
              tone={!interpreterDeclared ? "warn" : r.interpreter.matches ? "ok" : "bad"}
              control="system.interpreter.match"
            >
              {!interpreterDeclared
                ? "portable tier — no version pin installed"
                : r.interpreter.matches
                  ? "matches the declared version"
                  : "does NOT match"}
            </OpsBadge>
          ) : null
        }
      >
        {!r ? (
          <ul className="ops-list">
            <OpsUnknown what="Runtime identity" why="/api/runtime has not answered yet." />
          </ul>
        ) : (
          <OpsFacts>
            <OpsFact label="Running">{r.interpreter.running ?? "unknown"}</OpsFact>
            <OpsFact label="Declared">{r.interpreter.declared ?? "nothing declared"}</OpsFact>
            {r.service_identity ? (
              <OpsFact label="Built from">
                <span className="ops-mono" data-control="system.identity.source">
                  {r.service_identity.source_revision ?? "unknown"}
                </span>
                <div className="ops-note">
                  build {r.service_identity.build_sha ?? "unknown"} ·{" "}
                  {r.service_identity.source_provenance ?? "provenance not recorded"}
                </div>
              </OpsFact>
            ) : null}
            <OpsFact label="Platform">{r.platform}</OpsFact>
            {health?.interpreter ? (
              <>
                <OpsFact label="Executable">
                  <span className="ops-mono">{health.interpreter.executable ?? "unknown"}</span>
                </OpsFact>
                <OpsFact label="Environment prefix">
                  <span className="ops-mono">{health.interpreter.prefix ?? "unknown"}</span>
                </OpsFact>
                <OpsFact label="Inside a virtual environment">
                  <OpsBadge tone={health.interpreter.in_a_virtualenv ? "ok" : "warn"}>
                    {health.interpreter.in_a_virtualenv ? "yes" : "no"}
                  </OpsBadge>
                  {health.interpreter.why_this_is_reported ? (
                    <div className="ops-note">{health.interpreter.why_this_is_reported}</div>
                  ) : null}
                </OpsFact>
              </>
            ) : null}
            {oversight ? (
              <OpsFact label="Interpreter the oversight probe found on PATH">
                <span className="ops-mono">{oversight.host.python_executable}</span>
                <div className="ops-note">
                  If this differs from the executable above, two different Pythons are in play
                  on this box and a measurement is only as good as the one that took it.
                </div>
              </OpsFact>
            ) : null}
          </OpsFacts>
        )}
        <OpsSource route="/api/runtime" field="interpreter, platform" />
        <OpsSource route="/api/health" field="interpreter" />
      </OpsSection>

      <OpsSection
        control="system.deps"
        title="Dependency identity"
        hint="The active checkout's lock and every installed package are measured against the freeze."
        aside={
          r ? (
            <OpsBadge tone={!lockDeclared ? "warn" : r.lock.matches && r.lock.installed_matches ? "ok" : "bad"} control="system.deps.lock">
              {!lockDeclared
                ? "portable tier — no science freeze installed"
                : r.lock.matches && r.lock.installed_matches
                ? "runtime matches the freeze"
                : "runtime does NOT match the freeze"}
            </OpsBadge>
          ) : null
        }
      >
        {!r ? null : (
          <>
            <OpsFacts>
              <OpsFact label="Lock file">
                <span className="ops-mono">{r.lock.path}</span>
                <div className="ops-note">
                  {r.lock.present ? "present" : "ABSENT — nothing to compare"}
                </div>
              </OpsFact>
              <OpsFact label="Hash on disk">
                <span className="ops-mono">{r.lock.sha256 ?? "not hashed"}</span>
              </OpsFact>
              <OpsFact label="Hash declared by the freeze">
                <span className="ops-mono">{r.lock.declared_sha256 ?? "nothing declared"}</span>
              </OpsFact>
              <OpsFact label="Installed packages">
                <OpsBadge tone={!lockDeclared ? "warn" : r.lock.installed_matches ? "ok" : "bad"}>
                  {!lockDeclared
                    ? "not audited — install the science runtime to compare pins"
                    : r.lock.installed_matches
                      ? "every pinned version matches"
                      : "package drift found"}
                </OpsBadge>
                {lockDeclared && !r.lock.installed_matches ? (
                  <div className="ops-note">
                    Missing: {r.lock.missing?.join(", ") || "none"}. Mismatched:{" "}
                    {r.lock.mismatched?.map((row) => `${row.package} ${row.running} (needs ${row.declared})`).join(", ") || "none"}.
                  </div>
                ) : null}
              </OpsFact>
              <OpsFact label="Accelerator stack">
                torch {r.torch.running ?? "not importable"} / CUDA{" "}
                {r.torch.cuda_running ?? "unknown"}
                <div className="ops-note">
                  declared: torch {r.torch.declared ?? "nothing"} / CUDA{" "}
                  {r.torch.cuda_declared ?? "nothing"}
                </div>
                <span className="ops-badges">
                  <OpsBadge tone={r.torch.optional && !r.torch.running ? "ok" : !torchDeclared ? "warn" : r.torch.matches ? "ok" : "warn"}>
                    {r.torch.optional && !r.torch.running
                      ? "optional GPU extra, not part of this runtime (as declared)"
                      : !torchDeclared
                      ? "not installed in the portable tier"
                      : r.torch.matches
                      ? "matches the declared build"
                      : "DRIFTED from the declared build"}
                  </OpsBadge>
                </span>
              </OpsFact>
            </OpsFacts>
            {r.lock.why_it_matters ? (
              <OpsQuote cite="/api/runtime → lock.why_it_matters">{r.lock.why_it_matters}</OpsQuote>
            ) : null}
            {torchDeclared && !r.torch.matches ? (
              <div className="ops-refusal">
                The accelerator stack on this machine is not the one the freeze declares. That
                does not stop anything from running; it means a number produced here was not
                produced by the environment the declared numbers came from, and a comparison
                across the two carries that caveat.
              </div>
            ) : null}
          </>
        )}
        <OpsSource route="/api/runtime" field="lock, torch" />
      </OpsSection>

      <OpsSection
        control="system.toolchain"
        title="Toolchain and packages"
        hint="What is installed, and what each one unlocks. An absent tool is shown with the capability it would enable, so absence is actionable rather than merely reported."
      >
        {!oversight ? (
          <ul className="ops-list">
            <OpsUnknown what="The toolchain probe" why="/api/oversight has not answered yet." />
          </ul>
        ) : (
          <>
            <OpsTable control="system.toolchain.table">
              <thead>
                <tr>
                  <th>Tool</th>
                  <th>Present</th>
                  <th>Version</th>
                  <th>Unlocks</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(oversight.toolchain.tools).map(([k, v]) => (
                  <tr key={k} data-control={`system.toolchain.tool.${k}`}>
                    <td className="ops-mono">{k}</td>
                    <td>
                      <OpsBadge tone={v.present ? "ok" : "warn"}>
                        {v.present ? "present" : "absent"}
                      </OpsBadge>
                    </td>
                    <td className="ops-mono">{v.version ?? "not reported"}</td>
                    <td>{v.unlocks}</td>
                  </tr>
                ))}
                {Object.entries(oversight.toolchain.packages).map(([k, v]) => (
                  <tr key={k} data-control={`system.toolchain.package.${k}`}>
                    <td className="ops-mono">{k}</td>
                    <td>
                      <OpsBadge tone={v.present ? "ok" : "warn"}>
                        {v.present ? "present" : "absent"}
                      </OpsBadge>
                    </td>
                    <td className="ops-mono">python package</td>
                    <td>{v.unlocks}</td>
                  </tr>
                ))}
              </tbody>
            </OpsTable>
            <div className="ops-badges">
              {Object.entries(oversight.environment_flags).map(([k, v]) => (
                <OpsBadge
                  key={k}
                  tone={v ? "ok" : "idle"}
                  control={`system.toolchain.flag.${k}`}
                >
                  {k}: {v ? "set" : "unset"}
                </OpsBadge>
              ))}
            </div>
            <div className="ops-note">{oversight.note}</div>
            <OpsDisabled
              control="system.toolchain.install"
              label="Install what is missing"
              reason="Absent by design. Nothing in this interface installs, downloads or configures anything; the recommendations on the Safe-to-run tab and the repair plan on the Install tiers tab are sentences for a person, and an installer driven from a reading surface is a supply chain with a click for an approval step."
            />
          </>
        )}
        {r ? (
          <OpsFacts>
            <OpsFact label="Content stores">
              {Object.entries(r.content_stores)
                .map(
                  ([k, v]) =>
                    `${k}: ${v.present ? `${v.entries ?? "unknown"} entries` : "absent"}`,
                )
                .join(" · ")}
            </OpsFact>
            <OpsFact label="Credentials">
              {r.credentials.served_here
                ? "served by this route — which would be a defect"
                : `not served here; they live in ${r.credentials.where}`}
            </OpsFact>
          </OpsFacts>
        ) : null}
        <OpsSource route="/api/oversight" field="toolchain, environment_flags" />
      </OpsSection>
    </>
  );
}


function ServiceHealthTab({
  health,
  error,
  runtime,
  integrity,
  feed,
}: {
  health: HealthWithInterpreter | null;
  error: string | null;
  runtime: RuntimePayload | null;
  integrity: ReturnType<typeof useIntegrity>;
  feed?: FeedState;
}) {
  const chainStatus = integrity.data?.chain?.status ?? null;
  return (
    <>
      <StacksPanel />
      <ConnectorPanel />
      <OpsSection
        control="system.services"
        title="What is answering"
        hint="Ports probed by connecting to them, not read from a config. Where a service does not answer, that is what this says."
      >
        {!runtime ? (
          <ul className="ops-list">
            <OpsUnknown what="Service probes" why="/api/runtime has not answered yet." />
          </ul>
        ) : (
          <div className="ops-badges">
            {Object.entries(runtime.services).map(([k, v]) => (
              <OpsBadge key={k} tone={v ? "ok" : "warn"} control={`system.services.${k}`}>
                {k.replace(/_/g, " ")}
                {runtime.service_ports?.[k] ? ` :${runtime.service_ports[k]}` : ""}: {v ? "answering" : "not answering"}
              </OpsBadge>
            ))}
          </div>
        )}
        <OpsSource route="/api/runtime" field="services" />
      </OpsSection>

      <OpsSection
        control="system.readservice"
        title="The read-only service itself"
        hint="This panel reports the cost of polling, and is polled slowly on purpose."
        aside={
          health ? (
            <OpsBadge tone={health.read_only ? "ok" : "bad"} control="system.readservice.readonly">
              {health.read_only ? "read-only" : "NOT read-only"}
            </OpsBadge>
          ) : null
        }
      >
        {error && !health ? (
          <ul className="ops-list">
            <OpsUnknown what="Service health" why={error} />
          </ul>
        ) : !health ? (
          <div className="ops-note">Reading /api/health…</div>
        ) : (
          <OpsFacts>
            <OpsFact label="Process">
              pid {health.pid ?? "unknown"} ·{" "}
              {typeof health.process_cpu_seconds === "number"
                ? `${Math.round(health.process_cpu_seconds)}s of CPU consumed since start`
                : "CPU time unknown"}
            </OpsFact>
            <OpsFact label="Snapshot">
              {typeof health.scan?.runs === "number"
                ? `${health.scan.runs} run(s) in the last walk`
                : "run count unknown"}
              {typeof health.scan?.last_seconds === "number"
                ? ` · walk took ${health.scan.last_seconds}s`
                : ""}
              {typeof health.scan?.snapshot_age_s === "number"
                ? ` · snapshot ${agoLabel(health.scan.snapshot_age_s)}`
                : ""}
            </OpsFact>
            <OpsFact label="Listing cache">
              {typeof health.scan?.listings_reused === "number"
                ? `${health.scan.listings_reused} listing(s) reused, ${health.scan.listings_read ?? 0} read from disk`
                : "not reported"}
            </OpsFact>
            <Private kind="local_path">
            <OpsFact label="Roots it scans">
              {health.run_roots?.length ? (
                <ul className="ops-list">
                  {health.run_roots.map((x) => (
                    <li key={x} className="ops-mono">
                      {x}
                      {health.roots_present?.[x] === false ? " · ABSENT" : ""}
                    </li>
                  ))}
                </ul>
              ) : (
                "not reported"
              )}
              <div className="ops-note">
                This is the observatory feed's root list, and it is deliberately narrower than
                the activity derivation's. A count of zero runs in the feed is a statement about
                the feed, not about the machine.
              </div>
            </OpsFact>
            </Private>
            {feed ? (
              <OpsFact label="Live feed">
                <OpsBadge tone={feed.connected ? "ok" : feed.socketOpen ? "warn" : "bad"}>
                  {feed.connected
                    ? "delivering snapshots"
                    : feed.socketOpen
                      ? "socket open, no snapshot delivered"
                      : "not connected"}
                </OpsBadge>
                {feed.error ? <div className="ops-refusal">{feed.error}</div> : null}
                <div className="ops-note">
                  Connected means a valid snapshot arrived, not that a socket opened. An open
                  socket that never speaks used to report "feed live" over a board that was
                  minutes old, which is the worst failure shape available.
                </div>
              </OpsFact>
            ) : null}
          </OpsFacts>
        )}
        <OpsSource route="/api/health" field="scan, pid, read_only, run_roots" />
      </OpsSection>

      <OpsSection
        control="system.integrity"
        title="The command ledger's own integrity"
        hint="A hash-chained log that nobody verifies is a diary with extra steps. This is the verification."
        aside={
          chainStatus ? (
            <OpsBadge
              tone={chainStatus === "INTACT" ? "ok" : chainStatus === "EMPTY" ? "idle" : "bad"}
              control="system.integrity.state"
            >
              chain {chainStatus.toLowerCase()}
            </OpsBadge>
          ) : null
        }
      >
        {!integrity.data ? (
          <ul className="ops-list">
            <OpsUnknown what="The ledger chain" why="/api/integrity has not answered yet." />
          </ul>
        ) : (
          <OpsFacts>
            <OpsFact label="Status">
              {chainStatus ?? "unknown"}
              {typeof integrity.data.chain?.at === "number"
                ? ` at record ${integrity.data.chain.at}`
                : ""}
              {integrity.data.chain?.why ? (
                <div className="ops-note">{integrity.data.chain.why}</div>
              ) : null}
            </OpsFact>
            <OpsFact label="What that means">
              {chainStatus
                ? (integrity.data.means?.[chainStatus] ?? "no meaning is declared for this status")
                : "unknown"}
            </OpsFact>
            <OpsFact label="Consequence">{integrity.data.consequence_of_broken}</OpsFact>
            <OpsFact label="Verified by">
              <span className="ops-mono">{integrity.data.verified_by}</span>
            </OpsFact>
          </OpsFacts>
        )}
        <OpsDisabled
          control="system.integrity.reseal"
          label="Re-seal the ledger"
          reason="Absent here, and the decision is on Review rather than the control. Re-sealing proves only that the records chain NOW: whatever the ledger said about the runs after the break is permanently lost as attestation, so it is a judgement with an irreversible consequence and not a maintenance task."
        />
        <OpsSource route="/api/integrity" field="chain, means, consequence_of_broken" />
      </OpsSection>
    </>
  );
}



function GovernedActions() {
  return (
    <OpsSection
      control="system.act"
      title="Governed actions"
      hint="Every other screen in this interface reads. These two submit a job through the governed command service and produce an audited receipt. The credential lives in the local transport and never reaches this page; opening a session is a deliberate act, and the session is short-lived and bound to this origin."
    >
      <StructuredIntentConsole />
      {
}
      <div data-control="system.act.preflight">
        <GovernedAction
          action="preflight"
          params={{ action: "score" }}
          label="Run the scoring preflight"
          why="Computes what would block a scoring route: storage floors, GPU ownership, lease availability and holdout staging. It reads only — and its own answer is frequently BLOCKED, which is the useful part."
        />
      </div>
      <div data-control="system.act.inventory-scan">
        <GovernedAction
          action="inventory.scan"
          params={{}}
          label="Scan local inventory"
          why="Enumerates what is actually on disk. Reads only; changes no scientific status."
        />
      </div>
      <div className="ops-note">
        Both of these READ. Neither changes a scientific status, and neither is a substitute for
        the operator console: a governed job succeeding means the job ran, and nothing more.
      </div>
      <OpsDisabled
        control="system.act.pipeline-start"
        label="Start a pipeline"
        reason="Registered on the command surface and deliberately unreachable from a browser. It spends GPU time and produces a number people quote; the transport this page uses exposes a short list of named operations and there is no route on it that accepts an arbitrary action id, so this cannot be reached by editing this screen."
        weight="primary"
      />
    </OpsSection>
  );
}


interface CheckpointLifecyclePayload {
  contract: string;
  checkpoint_classes: { id: string; promotable: boolean; why: string }[];
  contamination_risk_kinds: string[];
  physical_evidence_kinds: string[];
  min_scrolls_for_promotion: number;
  quarantine_states: string[];
  invariant: string;
  training_status: string;
}

function CheckpointLifecycleTab() {
  const data = usePoll<CheckpointLifecyclePayload>("/api/checkpoint_lifecycle", { intervalMs: 300000 });
  const p = data.data;
  return (
    <OpsSection
      control="system.lifecycle"
      title="Checkpoint lifecycle"
      hint="Which learned state may travel between scrolls, and which may never. Enforced in argus.core.checkpoint_lifecycle -- a refusal below is a real one, raised by the same code a training pipeline would have to call, not a description of an intention."
    >
      {!p ? (
        <p className="ag-prose">{data.failure ? data.failure.message : "reading the contract…"}</p>
      ) : (
        <>
          <div className="ops-note" data-control="system.lifecycle.training-status">
            {p.training_status}
          </div>
          <dl className="ag-kv">
            {p.checkpoint_classes.map((c) => (
              <div key={c.id}>
                <dt>{c.id}</dt>
                <dd>
                  <Chip tone={c.promotable ? "active" : "certified"} size="sm">
                    {c.promotable ? "has a promotion path" : "no promotion path"}
                  </Chip>{" "}
                  <span className="small faint">{c.why}</span>
                </dd>
              </div>
            ))}
          </dl>
          <p className="ag-prose" data-control="system.lifecycle.invariant">
            {p.invariant}
          </p>
          <div className="ag-kv">
            <div>
              <dt>never moves laterally between scrolls</dt>
              <dd className="mono small">{p.contamination_risk_kinds.join(", ")}</dd>
            </div>
            <div>
              <dt>may persist per-scroll, flows upward into that scroll's packet</dt>
              <dd className="mono small">{p.physical_evidence_kinds.join(", ")}</dd>
            </div>
            <div>
              <dt>minimum distinct scrolls to promote a Reasoner improvement</dt>
              <dd className="mono small">{p.min_scrolls_for_promotion}</dd>
            </div>
            <div>
              <dt>quarantine states</dt>
              <dd className="mono small">{p.quarantine_states.join(" → ")}</dd>
            </div>
          </div>
          <OpsSource route="/api/checkpoint_lifecycle" />
        </>
      )}
    </OpsSection>
  );
}


function DeveloperData({ feed }: { feed?: FeedState }) {
  const ws = usePoll<WorkspacesPayload>("/api/workspaces", { intervalMs: 120000 });
  const index = usePoll<UnrollIndex>("/api/unroll", { intervalMs: 120000 });

  const wsRows = useMemo(
    () => partitionFixtures(ws.data?.workspaces ?? ws.data?.items ?? []),
    [ws.data],
  );
  const targetRows = useMemo(() => partitionFixtures(index.data?.targets ?? []), [index.data]);
  const runRows = useMemo(() => partitionFixtures(feed?.data?.runs ?? []), [feed?.data]);
  const total =
    wsRows.fixtures.length + targetRows.fixtures.length + runRows.fixtures.length;

  return (
    <>
      <OpsSection
        control="system.developer"
        title="Developer data"
        hint="Test fixtures: the records the ordinary views refuse to show, listed here with the reason each was refused. They are real rows in the service's own payloads; what makes them fixtures is that they describe the test corpus rather than any physical scroll."
        aside={
          <OpsBadge tone={total === 0 ? "ok" : "warn"} control="system.developer.count">
            {total} fixture record(s)
          </OpsBadge>
        }
      >
        <div className="ops-note">
          {total === 0
            ? "Nothing in the payloads this screen reads is currently classified as a fixture."
            : "A fixture may appear here and may never appear anywhere else unmarked. A long list that mixes fixtures with real rows would otherwise misstate what is being worked on."}
        </div>
      </OpsSection>

      <FixtureSection
        title="Workspaces"
        route="/api/workspaces"
        rows={wsRows.fixtures}
        realCount={wsRows.real.length}
        loading={ws.loading}
        failure={ws.failure ? ws.failure.message : null}
        control="system.developer.workspaces"
      />
      <FixtureSection
        title="Exported layer-stack targets"
        route="exported layer-stack inventory"
        rows={targetRows.fixtures}
        realCount={targetRows.real.length}
        loading={index.loading}
        failure={index.failure ? index.failure.message : null}
        control="system.developer.targets"
      />
      <FixtureSection
        title="Runs in the observatory feed"
        route="the observatory feed"
        rows={runRows.fixtures}
        realCount={runRows.real.length}
        loading={feed ? feed.data === null : false}
        failure={feed ? null : "no feed was passed to this screen"}
        control="system.developer.runs"
      />
    </>
  );
}

function FixtureSection<T>({
  title,
  route,
  rows,
  realCount,
  loading,
  failure,
  control,
}: {
  title: string;
  route: string;
  rows: { item: T; verdict: FixtureVerdict }[];
  realCount: number;
  loading: boolean;
  failure: string | null;
  control: string;
}) {
  return (
    <OpsSection control={control} title={`${title} · ${route}`}>
      {failure ? (
        <ul className="ops-list">
          <OpsUnknown what={title} why={failure} />
        </ul>
      ) : loading ? (
        <div className="ops-note">Reading…</div>
      ) : rows.length === 0 ? (
        <div className="ops-note">
          No fixture in this payload. {realCount} real record{realCount === 1 ? "" : "s"} are
          shown in the ordinary views.
        </div>
      ) : (
        <ul className="ops-list">
          {rows.map((r, i) => (
            <OpsItem
              key={i}
              control={`${control}.row.${i}`}
              tone="warn"
              title={
                <>
                  {label(r.item)} <OpsFixture why={r.verdict.why} />
                </>
              }
              state={
                r.verdict.basis === "DECLARED"
                  ? "the record declares itself a fixture"
                  : "identified by its generated name"
              }
            >
              <div className="ops-item-body">{r.verdict.why}</div>
            </OpsItem>
          ))}
        </ul>
      )}
    </OpsSection>
  );
}

function label(item: unknown): string {
  if (typeof item === "string") return item;
  const r = (item ?? {}) as Record<string, unknown>;
  for (const f of ["slug", "key", "name", "run_id", "id", "target"]) {
    const v = r[f];
    if (typeof v === "string" && v) return v;
  }
  return "(unnamed record)";
}

export default SystemScreen;
