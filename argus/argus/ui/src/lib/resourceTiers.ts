
export type ResourceTier =
  | "LOCAL_CPU"
  | "LOCAL_6GB_VERIFIED"
  | "REMOTE_GPU_REQUIRED"
  | "NOT_YET_MEASURED";

export interface ResourceTierEntry {
  tier: ResourceTier;
  label: string;
  evidence: string;
}

export const RESOURCE_TIERS: Record<string, ResourceTierEntry> = {
  "acquire.execute": {
    tier: "LOCAL_CPU",
    label: "Acquire (bounded volume read)",
    evidence: "network/disk-bound byte fetch; no GPU code path exists in this action.",
  },
  "blender.launch": {
    tier: "LOCAL_CPU",
    label: "Blender launch",
    evidence: "launches the external Blender process for mesh/roundtrip work; CPU-bound here.",
  },
  "blender.verify_roundtrip": {
    tier: "LOCAL_CPU",
    label: "Blender roundtrip verification",
    evidence: "geometry comparison only; no GPU code path.",
  },
  "candidate.open": {
    tier: "LOCAL_CPU",
    label: "Open candidates for review",
    evidence: "ranks tiles of already-saved probability planes with numpy and appends review " +
      "tasks; no model is loaded and no GPU code path exists in this action " +
      "(argus.core.candidates.rank reads saved arrays only).",
  },
  "glyph.annotate": {
    tier: "LOCAL_CPU",
    label: "Record a letter judgment",
    evidence: "one JSON line appended to the review store; no GPU code path.",
  },
  "htr.propose": {
    tier: "LOCAL_CPU",
    label: "Record an external OCR/HTR proposal",
    evidence: "records what an external source proposed; no OCR/HTR model is installed and this " +
      "action runs none (asking it to returns PROVIDER_NOT_INSTALLED).",
  },
  "transcription.claim": {
    tier: "LOCAL_CPU",
    label: "Claim a transcription",
    evidence: "recomputes the merged review tally from JSON files; no GPU code path.",
  },
  "language.write": {
    tier: "LOCAL_CPU",
    label: "Declare the language",
    evidence: "writes one small JSON file; no GPU code path.",
  },
  "translation.propose": {
    tier: "LOCAL_CPU",
    label: "Record a translation proposal",
    evidence: "one JSON line appended to the proposal store; no machine translation exists here.",
  },
  "packet.export": {
    tier: "LOCAL_CPU",
    label: "Export a publication packet",
    evidence: "hashes and writes a handful of small JSON files; no GPU code path, no network.",
  },
  "packet.verify": {
    tier: "LOCAL_CPU",
    label: "Verify a packet",
    evidence: "re-hashes the packet's files and its cited receipts; reads only.",
  },
  "evidence.reproduce": {
    tier: "NOT_YET_MEASURED",
    label: "Reproduce (argus/run.py --manifest ...)",
    evidence: "stage-dependent on the manifest's own --stop-after value; no single peak-VRAM " +
      "receipt covers every possible manifest this action can run. See the per-stage rows " +
      "below (rendering vs. detector inference) for the stages it can include.",
  },
  "inventory.scan": {
    tier: "LOCAL_CPU",
    label: "Inventory scan",
    evidence: "filesystem walk over already-local bytes; no GPU code path.",
  },
  preflight: {
    tier: "LOCAL_CPU",
    label: "Preflight",
    evidence: "reads registries and evaluates gates; no GPU code path.",
  },
  "provider.invoke": {
    tier: "LOCAL_CPU",
    label: "Provider invoke (pinned Villa/VC3D adapter)",
    evidence: "the renderer-parity receipt: the real Villa " +
      "vc_render_tifxyz invocation takes no --gpus flag and uses no local GPU.",
  },
  "seed.grow.execute": {
    tier: "LOCAL_CPU",
    label: "Seed growth",
    evidence: "mesh growth algorithm; no GPU code path.",
  },
  "surface.prepare_exact_eligible": {
    tier: "LOCAL_CPU",
    label: "Prepare exact-eligible surface",
    evidence: "the action's own live plan response declares \"cost\":{\"gpu\":\"none\"} -- " +
      "confirmed against a real plan computed through the containerized command service, " +
      "2026-09-18 (job class surface-prepare; identity+growth+flattening+rendering, no " +
      "detector stage).",
  },
  "vigiles.final_decision": {
    tier: "LOCAL_CPU",
    label: "Vigiles final decision",
    evidence: "evidence-gate decision logic over already-computed facts; no GPU code path.",
  },

  "detector.inference": {
    tier: "NOT_YET_MEASURED",
    label: "Detector inference",
    evidence: "peak memory depends on the checkpoint and the window size; measure it on your own hardware.",
  },
};

export function resourceTier(key: string): ResourceTierEntry {
  return (
    RESOURCE_TIERS[key] ?? {
      tier: "NOT_YET_MEASURED",
      label: key,
      evidence: "no entry recorded for this operation id yet.",
    }
  );
}
