import type { ReceiptEnvelope, ReceiptIndex } from "./receipts";
import { content, receipt } from "./receipts";

export type ModelStatus =
  | "QUALIFIED"
  | "EXPLORATION_ONLY"
  | "FAILED_GENERALIZATION"
  | "BLOCKED"
  | "LOCAL_RESOURCE_REFUSAL"
  | "UNTESTED"
  | "INCOMPATIBLE";

export const MODEL_STATUSES: ModelStatus[] = [
  "QUALIFIED",
  "EXPLORATION_ONLY",
  "FAILED_GENERALIZATION",
  "BLOCKED",
  "INCOMPATIBLE",
  "LOCAL_RESOURCE_REFUSAL",
  "UNTESTED",
];

export const STATUS_MEANING: Record<ModelStatus, string> = {
  QUALIFIED:
    "cleared the frozen held-out gate on every held-out scroll. Eligible for candidate search.",
  EXPLORATION_ONLY:
    "materially above chance but below the qualification gate. Usable to explore, never to certify.",
  FAILED_GENERALIZATION:
    "the gate closed it. The checkpoints and every number measured against them are preserved; what is withdrawn is eligibility for cross-scroll certification.",
  INCOMPATIBLE:
    "cannot be screened with what this project holds. Its training exposure is undeclared, so a pass on any holdout would be uninterpretable.",
  LOCAL_RESOURCE_REFUSAL:
    "screenable in principle, but this machine cannot run it — no weights on disk, or an accelerator it needs and this box does not have. A fact about the box, not about the model.",
  BLOCKED:
    "a held-out measurement cannot be MADE, so no number about it would mean anything. Distinct from UNTESTED, where nobody has run it yet: a blocked model is not a task waiting to be picked up.",
  UNTESTED:
    "no held-out measurement exists. Not a negative result and not a positive one.",
};

export type StatusTone = "certified" | "blocked" | "refused" | "active";

export const STATUS_TONE: Record<ModelStatus, StatusTone> = {
  QUALIFIED: "certified",
  EXPLORATION_ONLY: "blocked",
  FAILED_GENERALIZATION: "refused",
  BLOCKED: "blocked",
  INCOMPATIBLE: "refused",
  LOCAL_RESOURCE_REFUSAL: "blocked",
  UNTESTED: "active",
};

export const VOCABULARY_MAP: Record<string, { to: ModelStatus; why: string }> = {
  HUNT_QUALIFIED: { to: "QUALIFIED", why: "the registry's gate is this interface's gate" },
  ADAPTATION_ONLY: {
    to: "EXPLORATION_ONLY",
    why: "above chance, below the gate — the same band under a different name",
  },
  RETIRED: {
    to: "FAILED_GENERALIZATION",
    why: "closed by a held-out measurement, which is what this status records",
  },
  UNSCREENABLE: {
    to: "INCOMPATIBLE",
    why: "exposure undeclared: no hardware and no holdout would make a pass interpretable",
  },
  WEIGHTS_ABSENT: {
    to: "LOCAL_RESOURCE_REFUSAL",
    why: "nothing on this box to run. The model is not at fault and is not being judged",
  },
  BLOCKED: {
    to: "BLOCKED",
    why: "stated directly: the measurement cannot be made, so no number about it would mean anything",
  },
  INDETERMINATE: {
    to: "BLOCKED",
    why: "exposure is indeterminate, so a held-out score would not be a held-out score",
  },
  EXPOSURE_INDETERMINATE: {
    to: "BLOCKED",
    why: "exposure is indeterminate, so a held-out score would not be a held-out score",
  },
  NOT_A_DETECTOR: {
    to: "UNTESTED",
    why: "a backbone has no ink output to gate; it is untested here rather than incompatible, because it can be qualified together with a head",
  },
  UNTESTED: { to: "UNTESTED", why: "stated directly" },
};

export type Triple<T = unknown> =
  | { value: T; source: string; evidence?: string; reason?: undefined }
  | { value: null; source: "UNKNOWN"; reason: string; evidence?: undefined };

export function isKnown<T>(t: Triple<T> | undefined | null): t is {
  value: T;
  source: string;
  evidence?: string;
} {
  return !!t && t.value !== null && t.value !== undefined;
}

export function unknownReason(t: Triple | undefined | null): string {
  if (!t) return "this registry does not carry the field";
  return t.reason ?? "recorded as unknown with no reason given";
}

export interface Checkpoint {
  path: string;
  bytes?: number;
  sha256?: string;
  step?: number;
  seed?: number;
  n_params?: number;
  qualification?: string;
}

export interface RegistryFamily {
  repo?: string;
  local_root?: string;
  role?: Triple<string>;
  architecture?: Triple<string>;
  architecture_trap?: Triple<string>;
  representation?: Triple<string>;
  axes?: Triple<string>;
  physical_pitch_um?: Triple<Record<string, unknown> | number>;
  patch_geometry?: Triple<Record<string, unknown>>;
  normalization?: Triple<Record<string, unknown>>;
  normalization_trap?: Triple<string>;
  output_semantics?: Triple<unknown>;
  exposure?: Record<string, Triple<unknown>>;
  controls?: Record<string, Triple<unknown>> & Triple<unknown>;
  held_out_performance?: Record<string, Triple<unknown>> & Triple<unknown>;
  qualification?: Triple<string>;
  known_failures?: string[];
  hardware?: Triple<{
    checkpoint_bytes?: number;
    params_measured?: number;
    dtypes?: string[];
    gpu_required?: boolean;
    note?: string;
    why?: string;
  }>;
  checkpoints?: Checkpoint[];
  weights_present_locally?: Triple<boolean>;
  source_conflict?: Triple<unknown>;
}

export interface ModelRegistry {
  schema: string;
  utc: string;
  why: string;
  contract?: string;
  qualification_vocabulary: Record<string, string>;
  families: Record<string, RegistryFamily>;
  family_count: number;
  checkpoint_files_hashed: number;
  unknown_count: number;
}

export interface HeldoutProtocol {
  id: string;
  metrics?: { primary: string; secondary: string[]; why_within_patch: string };
  one_shot?: { runs: number; scrolls: string; forbidden: string[]; enforcement: string };
  decision_ladder: Record<string, { condition: string; next: string } | string>;
}

export interface LocalCapability {
  gpuPresent: boolean | null;
  gpuDetail: string;
}

export interface ModelRow {
  id: string;
  status: ModelStatus;
  registryTerm: string | null;
  because: string;
  becauseSource: string | null;
  repo: string | null;
  role: Triple<string> | null;
  architecture: Triple<string> | null;
  representation: Triple<string> | null;
  axes: Triple<string> | null;
  pitch: Triple<unknown> | null;
  patchGeometry: Triple<unknown> | null;
  normalization: Triple<unknown> | null;
  outputSemantics: Triple<unknown> | null;
  traps: { label: string; t: Triple<string> }[];
  exposure: { channel: string; t: Triple<unknown> }[];
  controls: { label: string; t: Triple<unknown> }[];
  heldOut: { scroll: string; t: Triple<unknown> }[];
  knownFailures: string[];
  hardware: Triple<RegistryFamily["hardware"] extends Triple<infer H> ? H : never> | null;
  runnableHere: { ok: boolean | null; because: string };
  checkpoints: Checkpoint[];
  weightsPresent: Triple<boolean> | null;
  declaredByRuns: string[];
}

export function translate(term: string | null | undefined): {
  status: ModelStatus;
  mappedFrom: string | null;
  unmapped: boolean;
} {
  const parts = String(term ?? "")
    .split("/")
    .map((s) => s.trim().toUpperCase())
    .filter(Boolean);
  for (const p of parts) {
    const hit = VOCABULARY_MAP[p];
    if (hit) return { status: hit.to, mappedFrom: p, unmapped: false };
  }
  return { status: "UNTESTED", mappedFrom: parts[0] ?? null, unmapped: true };
}

function runnable(f: RegistryFamily, cap: LocalCapability): ModelRow["runnableHere"] {
  const wpl = f.weights_present_locally;
  if (wpl && wpl.value === false)
    return {
      ok: false,
      because:
        "no weights on this machine — " + (wpl.evidence ?? "measured against the model root"),
    };
  const hw = isKnown(f.hardware) ? f.hardware.value : null;
  if (hw?.gpu_required) {
    if (cap.gpuPresent === null)
      return { ok: null, because: "needs an accelerator; this machine has not been probed yet" };
    if (!cap.gpuPresent)
      return {
        ok: false,
        because: "needs an accelerator and none is visible — " + cap.gpuDetail,
      };
    return { ok: true, because: "needs an accelerator, and one is visible: " + cap.gpuDetail };
  }
  if ((f.checkpoints ?? []).length === 0)
    return { ok: false, because: "no checkpoint file is catalogued on this machine" };
  return {
    ok: true,
    because: hw?.note ?? "no accelerator is required and a checkpoint is on disk",
  };
}

function tripleMap(o: unknown): { channel: string; t: Triple<unknown> }[] {
  if (!o || typeof o !== "object") return [];
  return Object.entries(o as Record<string, Triple<unknown>>).map(([channel, t]) => ({
    channel,
    t,
  }));
}

export interface ModelsView {
  loaded: boolean;
  indexAgeS: number | null;
  redactions: number;
  registryPresent: boolean;
  registryRelpath: string | null;
  registrySealed: boolean;
  registryUtc: string | null;
  registryVocabulary: Record<string, string> | null;
  unknownCount: number | null;
  checkpointsHashed: number | null;
  ladder: HeldoutProtocol["decision_ladder"] | null;
  ladderId: string | null;
  verdictChain: { name: string; cited: string; onDisk: string | null; match: boolean }[];
  rows: ModelRow[];
  unmappedTerms: string[];
}

export function buildModelsView(
  index: ReceiptIndex | null,
  cap: LocalCapability,
  detectorRuns: Map<string, string[]>,
): ModelsView {
  const reg = content<ModelRegistry>(index, "model_registry");
  const proto = content<HeldoutProtocol>(index, "heldout_protocol");
  const regEnv: ReceiptEnvelope | null = receipt(index, "model_registry");

  const chain: ModelsView["verdictChain"] = [];

  const rows: ModelRow[] = [];
  const unmapped = new Set<string>();
  for (const [id, f] of Object.entries(reg?.families ?? {})) {
    const term = isKnown(f.qualification) ? f.qualification.value : null;
    const t = translate(term);
    if (t.unmapped && t.mappedFrom) unmapped.add(t.mappedFrom);

    const traps: ModelRow["traps"] = [];
    if (isKnown(f.architecture_trap))
      traps.push({ label: "architecture", t: f.architecture_trap });
    if (isKnown(f.normalization_trap))
      traps.push({ label: "normalization", t: f.normalization_trap });
    if (isKnown(f.source_conflict as Triple<string>))
      traps.push({ label: "source conflict", t: f.source_conflict as Triple<string> });

    const controls = isUnknownTriple(f.controls)
      ? [{ label: "control", t: f.controls as Triple<unknown> }]
      : tripleMap(f.controls).map((x) => ({ label: x.channel, t: x.t }));
    const heldOut = isUnknownTriple(f.held_out_performance)
      ? [{ scroll: "held-out performance", t: f.held_out_performance as Triple<unknown> }]
      : tripleMap(f.held_out_performance).map((x) => ({ scroll: x.channel, t: x.t }));

    rows.push({
      id,
      status: t.status,
      registryTerm: term,
      because: isKnown(f.qualification)
        ? (f.qualification.evidence ?? "the registry states this term with no evidence line")
        : unknownReason(f.qualification),
      becauseSource: f.qualification?.source ?? null,
      repo: f.repo ?? null,
      role: f.role ?? null,
      architecture: f.architecture ?? null,
      representation: f.representation ?? null,
      axes: f.axes ?? null,
      pitch: (f.physical_pitch_um as Triple<unknown>) ?? null,
      patchGeometry: (f.patch_geometry as Triple<unknown>) ?? null,
      normalization: (f.normalization as Triple<unknown>) ?? null,
      outputSemantics: f.output_semantics ?? null,
      traps,
      exposure: tripleMap(f.exposure),
      controls,
      heldOut,
      knownFailures: f.known_failures ?? [],
      hardware: (f.hardware as ModelRow["hardware"]) ?? null,
      runnableHere: runnable(f, cap),
      checkpoints: f.checkpoints ?? [],
      weightsPresent: f.weights_present_locally ?? null,
      declaredByRuns: detectorRuns.get(id) ?? [],
    });
  }
  rows.sort(
    (a, b) =>
      MODEL_STATUSES.indexOf(a.status) - MODEL_STATUSES.indexOf(b.status) ||
      a.id.localeCompare(b.id),
  );

  return {
    loaded: index !== null,
    indexAgeS: index ? index.index_age_s : null,
    redactions: Object.values(index?.receipts ?? {}).reduce(
      (n, r) => n + (r.content_redactions ?? 0),
      0,
    ),
    registryPresent: !!reg,
    registryRelpath: regEnv?.relpath ?? null,
    registrySealed: !!regEnv?.sealed,
    registryUtc: reg?.utc ?? null,
    registryVocabulary: reg?.qualification_vocabulary ?? null,
    unknownCount: reg?.unknown_count ?? null,
    checkpointsHashed: reg?.checkpoint_files_hashed ?? null,
    ladder: proto?.decision_ladder ?? null,
    ladderId: proto?.id ?? null,
    verdictChain: chain,
    rows,
    unmappedTerms: [...unmapped].sort(),
  };
}

function isUnknownTriple(o: unknown): boolean {
  return (
    !!o &&
    typeof o === "object" &&
    "source" in (o as Record<string, unknown>) &&
    (o as { source?: unknown }).source === "UNKNOWN"
  );
}

export function reproductionFor(index: ReceiptIndex | null, key: string): string[] {
  const env: ReceiptEnvelope | null = receipt(index, key);
  const scripts = env?.produced_by_scripts ?? [];
  return scripts.map((s) => "python " + s);
}
