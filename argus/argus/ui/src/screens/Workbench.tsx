import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { AlertCircle, Ruler } from "lucide-react";
import { api, type FeedState, type RunRecord, type UnrollTarget } from "../api";
import { PipelineLedger } from "../components/PipelineStrip";
import { AxisLegend, TruthChip } from "../components/Status";
import { runCertification } from "../lib/certification";
import { useIntegrity, certificationIsSuppressed } from "../lib/systemTruth";
import { SurfaceViewer } from "../components/SurfaceViewer";
import { PlaneViewer } from "../components/PlaneViewer";
import { RawVolumeViewer } from "../components/RawVolumeViewer";
import { FiberLayerViewer } from "../components/FiberLayerViewer";
import { HecateLayerViewer } from "../components/HecateLayerViewer";
import { SegmentationStatus } from "../components/SegmentationStatus";
import { WindingInspector } from "../components/WindingInspector";
import { WindingSummary } from "../components/WindingSummary";
import { InlineStatus } from "../components/StatusDock";
import { BlenderRoundtrip } from "../components/BlenderRoundtrip";
import { VigilesPanel } from "../components/VigilesPanel";
import { LayerStack, type UnrollData } from "../components/LayerStack";
import { ReviewQueue } from "../components/ReviewQueue";
import { CERT_LABEL, Chip, SealBadge } from "../components/Status";
import { SurfaceStatusChips, SurfaceStatusDetail, useSurfaceStatus } from "../components/SurfaceStatusFacts";
import { routes } from "../lib/nav";
import { deriveRoute, useCapabilityGraph } from "../lib/argusTruth";
import { WbRouteRail } from "../components/WbRouteRail";
import { WbLanding } from "../components/WbLanding";
import { useArgusContext, contextIsCoherent } from "../lib/context";
import { usePoll } from "../lib/poll";
import { SpatialStateProvider, useSpatialState } from "../lib/spatialState";
import { useSharedUniverse } from "../lib/sharedUniverse";
import { BenchFrame } from "../components/workbench/BenchFrame";
import { ScrollStatusView, useScrollStatus } from "../components/ScrollStatusBar";
import type { ScrollStatus } from "../lib/scrollStatus";
import { EvidenceRoleChip } from "../components/ControlEvidenceKind";
import { Clamshell, NextStep, usePersistedChoice } from "../components/workbench/Clamshell";
import { WbLayoutProvider, WbPanel, usePanelFocus, useWbClass, useWbRootObserver } from "../components/workbench/WbLayout";
import { acquisitionLine, qualify } from "../lib/identity";
import { parseTaskParam } from "../lib/taskBinding";
import "../theme/workbench.css";
import "../theme/wbresponsive.css";

const MeshCanvas = lazy(() => import("../components/MeshCanvas"));

const PANELS = ["layers", "inspector", "none"] as const;

type LayerKey = "ct" | "surface" | "fibers" | "hecate" | "ink" | "overlay";
type ViewKey = "local" | "mesh" | "surface" | "ct" | "planes" | "rawvolume" | "segmentation" | "ink" | "overlay" | "fibers" | "hecate" | "windings";

interface PlaneInventory {
  selected_scroll: string | null;
  staged: string[];
  unmatched: { fragment: string; scroll?: string | null; why?: string }[];
  not_reachable: string[];
}

interface LocalDiscoveryAsset {
  asset_id: string;
  physical_scroll: string | null;
  identity_state: string;
  kind: string;
  status: string;
  name: string;
  bytes: number;
  sha256?: string | null;
  display_path: string;
  viewable?: boolean;
  preview_url?: string | null;
}

interface LocalDiscoveryPayload {
  assets: LocalDiscoveryAsset[];
  scanned_utc: string;
  counts: { assets: number; renders: number; verified_renders: number; viewable_renders?: number };
}

interface ScrollTruthPayload {
  schema: string;
  physical_scroll: string | null;
  requested: string;
  route: ScrollStatus;
  local_material: {
    scanned_utc?: string;
    assets: LocalDiscoveryAsset[];
    counts: { assets: number; renders: number; meshes: number; science_records: number; verified_renders: number; viewable_renders?: number };
    raw_arrays_skipped: boolean;
  };
  claim_boundary: string;
}

interface VolumeInventory {
  selected_scroll: string | null;
  stores: { store: string; scroll?: string | null }[];
  unmatched?: { store: string; scroll?: string | null; why?: string }[];
}

interface FiberInventory {
  selected_scroll: string | null;
  available: boolean;
  why?: string;
}

interface HecateInventory {
  selected_scroll: string | null;
  available: boolean;
  entries: { physical_scroll: string; acquisition_id: string }[];
  why?: string | null;
}

interface SegmentationInventory {
  selected_scroll: string | null;
  available: boolean;
  rows: { seal: string }[];
  why?: string | null;
}

interface MaterialReadiness {
  schema: string;
  canonical_scroll: string | null;
  state: string;
  next?: string;
  why?: string;
  route: { stage: string; label: string; state: string; why: string }[];
  detectors: { id: string; verdict: string; semantic_state: string; licence: string }[];
}

interface IntelClaim {
  key: string;
  claim: string;
  reported_by: string;
  would_reproduce: string;
  never_used_for?: string;
  status?: string;
}

interface Layer {
  key: LayerKey;
  label: string;
  producedBy: string;
}

const LAYERS: Layer[] = [
  { key: "ct", label: "Direct CT", producedBy: "an identity-matched raw CT volume" },
  { key: "surface", label: "Flattened surface", producedBy: "the verified render stage" },
  { key: "fibers", label: "Fibres", producedBy: "the registered fibre-model inference output" },
  { key: "hecate", label: "Hecate", producedBy: "an identity-bound Hecate provider run" },
  { key: "ink", label: "Ink map", producedBy: "an exported layer stack" },
  { key: "overlay", label: "Overlay", producedBy: "an exported layer stack" },
];

const VIEWS: { key: ViewKey; plain: string; hint: string }[] = [
  {
    key: "local",
    plain: "What exists here",
    hint: "every identity-matched local asset ARGUS found for this scroll, including material not yet mounted in a viewer",
  },
  { key: "mesh", plain: "The sheet in 3-D", hint: "the traced surface where it sits in the volume" },
  { key: "surface", plain: "The surface, flat", hint: "that sheet laid out as a picture" },
  { key: "ct", plain: "The raw scan", hint: "the X-ray volume as it was measured" },
  {
    key: "planes",
    plain: "The CT surface volume, plane by plane",
    hint: "step through the surface volume plane by plane",
  },
  {
    key: "rawvolume",
    plain: "Raw CT, real pyramid",
    hint: "synchronized XY/XZ/YZ through a real OME-Zarr multiresolution volume — the same physical location survives a level change",
  },
  {
    key: "segmentation",
    plain: "Official segmentation",
    hint: "identity-bound Villa model output and its receipt, not a scientific reading",
  },
  { key: "ink", plain: "Where a detector saw ink", hint: "a detector's output. Not a reading" },
  { key: "overlay", plain: "Ink over the surface", hint: "the same output drawn onto the sheet" },
  { key: "fibers", plain: "Fibres", hint: "individual papyrus fibres" },
  {
    key: "hecate",
    plain: "Hecate output",
    hint: "an already-recorded Hecate run receipt -- not a reading",
  },
  {
    key: "windings",
    plain: "Windings, plane by plane",
    hint: "the winding inspector's XY, XZ and YZ views of the traced sheet -- geometry, not ink",
  },
];

const SAVED_LAYOUTS: { id: string; label: string; view: ViewKey | null; panel: "layers" | "inspector" | null; why?: string }[] = [
  { id: "inspect-ct", label: "Inspect CT", view: "planes", panel: "inspector" },
  { id: "segment", label: "Segment", view: "segmentation", panel: "inspector" },
  { id: "correct-surface", label: "Correct surface", view: "mesh", panel: "inspector" },
  { id: "flatten", label: "Flatten", view: "surface", panel: null },
  { id: "read-ink", label: "Read ink", view: "ink", panel: "inspector" },
  { id: "review", label: "Review", view: "overlay", panel: "inspector" },
];

function pickTarget(
  targets: UnrollTarget[],
  asked: string | null,
  scroll: string | null,
  runTarget: string | null,
): string | null {
  if (asked && targets.some((t) => t.key === asked)) return asked;
  const byName = (needle: string | null) => {
    if (!needle) return null;
    const s = /PHerc[0-9A-Za-z]+/i.exec(needle)?.[0] ?? null;
    if (!s) return null;
    return targets.find((t) => t.name.toLowerCase().includes(s.toLowerCase())) ?? null;
  };
  return (byName(scroll) ?? byName(runTarget))?.key ?? null;
}

type WorkbenchProps = {
  run: RunRecord | null;
  runs: RunRecord[];
  requestedId: string | null;
  requestedTarget: string | null;
  loaded: boolean;
  feed?: FeedState;
  requestedIntel?: string | null;
};

export function Workbench(props: WorkbenchProps) {
  return (
    <WbLayoutProvider>
      <WorkbenchInner {...props} />
    </WbLayoutProvider>
  );
}

function WorkbenchInner({
  run,
  runs,
  requestedId,
  requestedTarget,
  requestedIntel,
  loaded,
  feed,
}: WorkbenchProps) {
  const integrity = useIntegrity();
  const gate = certificationIsSuppressed(integrity.data);
  const { ctx, mode } = useArgusContext();
  const universe = useSharedUniverse();
  const scrollFacts = ctx.scroll ? (universe.byId.get(ctx.scroll) ?? null) : null;
  const { status: surfaceStatus } = useSurfaceStatus(ctx.scroll);
  const { status: polledScrollStatus } = useScrollStatus(ctx.scroll);
  const [intel, setIntel] = useState<IntelClaim | null>(null);
  const [intelLoading, setIntelLoading] = useState(false);
  const [intelError, setIntelError] = useState<string | null>(null);

  useEffect(() => {
    if (!requestedIntel) {
      setIntel(null);
      setIntelError(null);
      setIntelLoading(false);
      return;
    }
    let dead = false;
    setIntel(null);
    setIntelError(null);
    setIntelLoading(true);
    fetch(`/api/community?key=${encodeURIComponent(requestedIntel)}`, { credentials: "same-origin" })
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status} from /api/community`);
        return (await r.json()) as { claims?: IntelClaim[] };
      })
      .then((d) => {
        if (dead) return;
        const found = (d.claims ?? []).find((x) => x.key === requestedIntel);
        if (!found) throw new Error("that community report is not registered");
        setIntel(found);
      })
      .catch((e) => {
        if (!dead) setIntelError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!dead) setIntelLoading(false);
      });
    return () => {
      dead = true;
    };
  }, [requestedIntel]);

  const planeInventory = usePoll<PlaneInventory>(
    `/api/planes${ctx.scroll ? `?scroll=${encodeURIComponent(ctx.scroll)}` : ""}`,
    { intervalMs: 120000 },
  );
  const volumeInventory = usePoll<VolumeInventory>(
    `/api/volumes${ctx.scroll ? `?scroll=${encodeURIComponent(ctx.scroll)}` : ""}`,
    { intervalMs: 120000 },
  );
  const fiberInventory = usePoll<FiberInventory>(
    `/api/fiber_layers${ctx.scroll ? `?scroll=${encodeURIComponent(ctx.scroll)}` : ""}`,
    { intervalMs: 120000 },
  );
  const hecateInventory = usePoll<HecateInventory>(
    `/api/providers/hecate/layer_inventory${ctx.scroll ? `?scroll=${encodeURIComponent(ctx.scroll)}` : ""}`,
    { intervalMs: 120000 },
  );
  const segmentationInventory = usePoll<SegmentationInventory>(
    `/api/segmentation_status${ctx.scroll ? `?scroll=${encodeURIComponent(ctx.scroll)}` : ""}`,
    { intervalMs: 120000 },
  );
  const materialReadiness = usePoll<MaterialReadiness>(
    `/api/material/readiness?scroll=${encodeURIComponent(ctx.scroll ?? "__no_scroll_selected__")}`,
    { intervalMs: 120000 },
  );
  const localDiscovery = usePoll<LocalDiscoveryPayload>("/api/discovery", { intervalMs: 120000, timeoutMs: 20000 });
  const scrollTruth = usePoll<ScrollTruthPayload>(
    `/api/scroll_truth?scroll=${encodeURIComponent(ctx.scroll ?? "__no_scroll_selected__")}`,
    { intervalMs: 30000, timeoutMs: 20000 },
  );
  const scrollStatus = scrollTruth.data?.route ?? polledScrollStatus;
  const discoveredForScroll = useMemo(
    () => scrollTruth.data?.local_material.assets
      ?? (ctx.scroll ? (localDiscovery.data?.assets ?? []).filter((a) => a.physical_scroll === ctx.scroll) : []),
    [ctx.scroll, localDiscovery.data, scrollTruth.data],
  );

  const [view, setView] = useState<ViewKey>("surface");
  const explicitViewSelection = useRef(false);
  const chooseView = (next: ViewKey) => {
    explicitViewSelection.current = true;
    setView(next);
  };
  useWbRootObserver();
  const wbClass = useWbClass();
  const [wbParams] = useSearchParams();
  const requestedPanel = wbParams.get("panel");
  const requestedFocus = wbParams.get("focus");
  const requestedView = wbParams.get("view");
  const [panelChoice, setPanelChoice] = usePersistedChoice("panel", PANELS, "none", ["wide"]);
  const panel = panelChoice === "none" ? null : panelChoice;
  useEffect(() => {
    if (requestedPanel === "layers" || requestedPanel === "inspector") setPanelChoice(requestedPanel);
  }, [requestedPanel, setPanelChoice]);
  useEffect(() => {
    if (requestedFocus !== "blender" && requestedFocus !== "windings") return;
    requestAnimationFrame(() => document.getElementById(`wb-clam-${requestedFocus}`)?.scrollIntoView({ block: "center" }));
  }, [requestedFocus]);
  useEffect(() => {
    if (!requestedView || !VIEWS.some((candidate) => candidate.key === requestedView)) return;
    explicitViewSelection.current = true;
    setView(requestedView as ViewKey);
  }, [requestedView, ctx.scroll]);
  const setPanel = (
    next: "layers" | "inspector" | null | ((p: "layers" | "inspector" | null) => "layers" | "inspector" | null),
  ) =>
    setPanelChoice((cur) => {
      const curPanel = cur === "none" ? null : cur;
      const v = typeof next === "function" ? next(curPanel) : next;
      return v ?? "none";
    });
  const pf = usePanelFocus(panel, (p) => setPanel(p));

  const canvasRef = useRef<HTMLElement | null>(null);
  const jumpToCanvas = () => {
    canvasRef.current?.scrollIntoView({ block: "start" });
    canvasRef.current?.focus({ preventScroll: true });
  };
  const [portalHost, setPortalHost] = useState<HTMLDivElement | null>(null);

  const [targets, setTargets] = useState<UnrollTarget[] | null>(null);
  const [targetsErr, setTargetsErr] = useState<string | null>(null);
  const [stack, setStack] = useState<UnrollData | null>(null);
  const [stackErr, setStackErr] = useState<string | null>(null);

  useEffect(() => {
    let dead = false;
    api
      .unrollTargets()
      .then((r) => {
        if (dead) return;
        setTargets(r.targets);
      })
      .catch((e) => {
        if (!dead) {
          setTargets([]);
          setTargetsErr(e instanceof Error ? e.message : String(e));
        }
      });
    return () => {
      dead = true;
    };
  }, []);

  const targetKey = useMemo(
    () => pickTarget(targets ?? [], requestedTarget, ctx.scroll, run?.target ?? null),
    [targets, requestedTarget, ctx.scroll, run?.target],
  );

  useEffect(() => {
    if (!targetKey) return;
    let dead = false;
    setStack(null);
    setStackErr(null);
    api
      .unroll(targetKey)
      .then((r) => {
        if (!dead) setStack(r as UnrollData);
      })
      .catch((e) => {
        if (!dead) setStackErr(String(e?.message ?? e));
      });
    return () => {
      dead = true;
    };
  }, [targetKey]);

  const showsStack = view === "ink" || view === "overlay";
  const available = useMemo(
    () => availableLayers(run, Boolean(targets && targets.length && targetKey)),
    [run, targets, targetKey],
  );
  const geom = run?.geometry ?? run?.geometry_refusal ?? null;

  const hasTaskLink = Boolean(parseTaskParam(wbParams.get("task")));

  const viewState = (k: ViewKey): { available: boolean; path: string | null; why: string } => {
    if (k === "mesh" || k === "windings") {
      return run?.mesh_dir
        ? { available: true, path: null, why: "" }
        : {
            available: false,
            path: null,
            why: run
              ? "This run recorded no mesh directory; the mesh-producing stage has not run or did not produce one. Nothing has been read here, so this is not an absent finding."
              : "No run is open, so no mesh directory is declared and the mesh-producing stage has not run. Nothing has been read here, so this is not an absent finding.",
          };
    }
    if (!ctx.scroll) {
      return {
        available: false,
        path: null,
        why: "The identity-bound stage has not run because no physical scroll is selected. Nothing has been read here, so this is not an absent finding. Select a physical scroll before opening a rendering; ARGUS will not choose a global fragment.",
      };
    }
    if (k === "local") {
      if (scrollTruth.failure && !scrollTruth.data) {
        return {
          available: false,
          path: null,
          why: `ARGUS could not read the identity-matched local inventory: ${scrollTruth.failure.message}`,
        };
      }
      if (!scrollTruth.data) return { available: true, path: null, why: "Checking local material..." };
      return discoveredForScroll.length || scrollStatus?.science_artifacts?.state === "VERIFIED_LOCAL_OUTPUTS"
        ? { available: true, path: null, why: "" }
        : {
            available: false,
            path: null,
            why: `The bounded local inventory found no identity-matched material for ${ctx.scroll}. This is an inventory result, not a negative scientific finding.`,
          };
    }
    if (k === "planes") {
      const inv = planeInventory.data;
      if (!inv || inv.selected_scroll !== ctx.scroll) {
        return { available: true, path: null, why: "Checking for an identity-matched surface rendering…" };
      }
      if (inv.staged.length) return { available: true, path: null, why: "" };
      const loose = inv.unmatched
        .map((a) =>
          a.scroll
            ? `${a.fragment} is registered for ${a.scroll}, not ${ctx.scroll}`
            : `${a.fragment} has no explicit local identity`,
        )
        .join(", ");
      return {
        available: false,
        path: null,
        why: loose
          ? `${loose} is present locally but has no explicit identity for ${ctx.scroll}; it cannot be substituted as a rendering. The surface-rendering stage has not run for this identity. Nothing has been read here, so this is not an absent finding.`
          : `No identity-matched surface rendering is registered for ${ctx.scroll}; the surface-rendering stage has not run for this identity. Nothing has been read here, so this is not an absent finding.`,
      };
    }
    if (k === "rawvolume") {
      if (hasTaskLink) {
        return { available: true, path: null, why: "This task is bound to the Raw CT route; the viewer will show the exact store or its refusal." };
      }
      const inv = volumeInventory.data;
      if (!inv || inv.selected_scroll !== ctx.scroll) {
        return { available: true, path: null, why: "Checking for an identity-matched OME-Zarr volume…" };
      }
      return inv.stores.length
        ? { available: true, path: null, why: "" }
        : {
            available: false,
            path: null,
            why: inv.unmatched?.length
              ? `${inv.unmatched.length} OME-Zarr volume${inv.unmatched.length === 1 ? " is" : "s are"} present locally, but none is identity-matched to ${ctx.scroll}; they cannot be substituted as raw CT. Raw CT acquisition has not run or is not identity-bound for this scroll. Nothing has been read here, so this is not an absent finding.`
              : `No identity-matched OME-Zarr volume is registered for ${ctx.scroll}; raw CT acquisition has not run or is not identity-bound for this scroll. Nothing has been read here, so this is not an absent finding.`,
          };
    }
    if (k === "fibers") {
      const inv = fiberInventory.data;
      if (!inv || inv.selected_scroll !== ctx.scroll) {
        return { available: true, path: null, why: "Checking the identity-matched fibre-model output…" };
      }
      return inv.available
        ? { available: true, path: null, why: "" }
        : {
            available: false,
            path: null,
            why: `${inv.why ?? `No identity-matched fibre output is registered for ${ctx.scroll}.`} The governed fibre-output stage has not run for this identity. Nothing has been read here, so this is not an absent finding.`,
          };
    }
    if (k === "hecate") {
      const inv = hecateInventory.data;
      if (!inv || inv.selected_scroll !== ctx.scroll) {
        return { available: true, path: null, why: "Checking the identity-matched Hecate output…" };
      }
      return inv.available
        ? { available: true, path: null, why: "" }
        : {
            available: false,
            path: null,
            why: `${inv.why ?? `No identity-matched Hecate run is registered for ${ctx.scroll}.`} The Hecate provider has not run for this identity. Nothing has been read here, so this is not an absent finding.`,
          };
    }
    if (k === "segmentation") {
      const inv = segmentationInventory.data;
      if (!inv || inv.selected_scroll !== ctx.scroll) {
        return { available: true, path: null, why: "Checking identity-bound Villa segmentation seals…" };
      }
      return inv.available
        ? { available: true, path: null, why: "" }
        : {
            available: false,
            path: null,
            why: `${inv.why ?? `No official segmentation seal is registered for ${ctx.scroll}.`} The official segmentation stage has not run for this identity. Nothing has been read here, so this is not an absent finding.`,
          };
    }
    return available[k];
  };

  const identity = useMemo(
    () =>
      qualify({
        alias: run?.target ?? run?.run_id ?? ctx.scroll ?? "no run open",
        scroll: ctx.scroll,
        upstreamSegmentId: null,
        volumeId: run?.acquisition?.volume_id ?? null,
      }),
    [run, ctx.scroll],
  );
  const capabilityGraph = useCapabilityGraph();
  const route = useMemo(() => deriveRoute(capabilityGraph.data), [capabilityGraph.data]);

  const acqLine = acquisitionLine(
    run?.acquisition?.voxel_um ?? null,
    run?.acquisition?.energy_kev ?? null,
  );

  const coherence = contextIsCoherent(
    ctx,
    run?.target ? (run.target.split("/")[0] ?? null) : null,
  );

  useEffect(() => {
    if (explicitViewSelection.current) return;
    if (viewState(view).available) return;
    const firstReal = VIEWS.find((v) => viewState(v.key).available);
    if (firstReal) setView(firstReal.key);
  }, [run, available, targets, view, ctx.scroll, discoveredForScroll, scrollStatus, scrollTruth.data, scrollTruth.failure, planeInventory.data, volumeInventory.data, fiberInventory.data, hecateInventory.data, segmentationInventory.data]);

  useEffect(() => {
    if (hasTaskLink) setView("rawvolume");
  }, [hasTaskLink, ctx.scroll, volumeInventory.data]);

  if (requestedId && !run) {
    return (
      <Empty
        title={loaded ? "No such run" : "Loading the feed…"}
        body={
          loaded
            ? `The feed has no run called "${requestedId}". It may have been produced by another tool, or removed from disk.`
            : "The service is building its first snapshot. This link will resolve as soon as it arrives."
        }
      />
    );
  }

  if (!run && !ctx.scroll && !requestedIntel) {
    return (
      <BenchFrame universe={universe} runs={runs} title="Workbench" rail={<WbRouteRail route={route} scrollStatus={scrollStatus} />}>
        <WbLanding
          runs={runs}
          loaded={loaded}
          sealedExperiments={feed?.data?.sealed_experiments ?? []}
          registered={universe.counts.registered.value}
          firstLetters={universe.counts.firstLetters.value}
        />
      </BenchFrame>
    );
  }

  const cert = run
    ? runCertification(run, { chainSuppressed: gate.suppressed, chainWhy: gate.why })
    : null;
  const unavailableViews = VIEWS.filter((v) => !viewState(v.key).available);
  const currentView = VIEWS.find((v) => v.key === view) ?? null;

  const nextFor = (k: ViewKey) => {
    if (run?.blinding.sealed)
      return <NextStep id={`canvas.${k}`} to="/review" label="See what is sealed, and who decides, on Review" />;
    if (k === "local")
      return <NextStep id={`canvas.${k}`} to={`/sources?tab=discovered&scroll=${encodeURIComponent(ctx.scroll ?? "")}`} label="Open the complete identity-matched inventory" />;
    if (k === "fibers")
      return <NextStep id={`canvas.${k}`} to={routes.system()} label="See the capability list on System" />;
    if (k === "hecate")
      return (
        <NextStep
          id={`canvas.${k}`}
          to={`/system?tab=process&scroll=${encodeURIComponent(ctx.scroll ?? "")}`}
          label="See this scroll's Hecate status and next step on System"
        />
      );
    if (k === "segmentation")
      return <NextStep id={`canvas.${k}`} to={`/system?tab=process&scroll=${encodeURIComponent(ctx.scroll ?? "")}`} label="See this scroll's segmentation status and next step on System" />;
    if (k === "planes")
      return (
        <NextStep
          id={`canvas.${k}`}
          to={`/system?tab=process&scroll=${encodeURIComponent(ctx.scroll ?? "")}`}
          label="See what this scroll needs before surface viewing"
        />
      );
    if (k === "rawvolume")
      return (
        <NextStep
          id={`canvas.${k}`}
          to={`/system?tab=process&scroll=${encodeURIComponent(ctx.scroll ?? "")}`}
          label="See this scroll's CT status and next step on System"
        />
      );
    if (k === "ink" || k === "overlay")
      return (
        <NextStep
          id={`canvas.${k}`}
          to={`/sources?scroll=${encodeURIComponent(ctx.scroll ?? "")}`}
          label="See this scroll's available renderings on Sources"
        />
      );
    if (run)
      return (
        <NextStep
          id={`canvas.${k}`}
          to={routes.evidence(run.run_id)}
          label="Read this run's stage record on Evidence"
        />
      );
    return <NextStep id={`canvas.${k}`} to="/jobs" label="Find a run for this scroll on Jobs" />;
  };
  const activeViewState = viewState(view);

  return (
    <BenchFrame
      compact
      universe={universe}
      runs={runs}
      title={`Workbench: ${run ? identity.label : (scrollFacts?.display ?? ctx.scroll ?? "")}`}
      rail={<WbRouteRail route={route} scrollStatus={scrollStatus} />}
    >
    {
}
    <SpatialStateProvider>
    <MeshIdentitySync meshDir={run?.mesh_dir ?? null} />
    <div className="wb" data-stacks={targets === null ? "loading" : targetsErr ? "failed" : "ready"}>
      {}
      <header className="wb-head" data-wb-chrome="true">
        <a
          href="#wb-canvas"
          className="wb-skip"
          data-control="wb.jumpToCanvas"
          onClick={(e) => {
            e.preventDefault();
            jumpToCanvas();
          }}
        >
          Jump to canvas
        </a>
        <div className="wb-id">
          <h2 className="wb-title" data-novice="looking">{run ? identity.label : (scrollFacts?.display ?? ctx.scroll)}</h2>
          <span className="wb-sub">
            {run
              ? `${acqLine ?? "acquisition not declared by this run"}${run.blinding.sealed ? " · sealed" : ""}`
              : `${scrollFacts?.familyLine ?? "acquisition not declared"} · no run open on this scroll`}
          </span>
        </div>

        {
}
        <div className="wb-head-status">
          {run && cert ? <RunBadge cert={cert} /> : null}
          {run?.blinding.sealed ? <SealBadge marker={run.blinding.marker} /> : null}
          {scrollFacts?.firstLetters ? (
            <Chip tone="active" size="sm">
              First Letters target
            </Chip>
          ) : null}
          {scrollFacts?.grandPrize ? (
            <Chip tone="blocked" size="sm">
              Grand Prize target
            </Chip>
          ) : null}
          <SurfaceStatusChips status={surfaceStatus} size="sm" />
        </div>

        <div className="wb-head-actions">
          <div className="wb-panel-toggles" role="group" aria-label="Side panels">
            <button
              type="button"
              className="link-control interactive"
              aria-pressed={panel === "layers"}
              aria-expanded={panel === "layers"}
              aria-controls="wb-drawer"
              data-control="wb.panel.layers"
              onClick={(e) => pf.toggle("layers", e.currentTarget)}
            >
              Layers
            </button>
            <button
              type="button"
              className="link-control interactive"
              aria-pressed={panel === "inspector"}
              aria-expanded={panel === "inspector"}
              aria-controls="wb-inspector"
              data-control="wb.panel.inspector"
              onClick={(e) => pf.toggle("inspector", e.currentTarget)}
            >
              Checks and evidence
            </button>
          </div>

          {run ? (
            <Link
              to={routes.evidence(run.run_id)}
              data-action="primary"
              data-control="wb.evidence"
              className="link-control interactive wb-primary"
            >
              View the evidence
            </Link>
          ) : (
            <Link
              to="/"
              data-action="primary"
              data-control="wb.backToShelf"
              className="link-control interactive wb-primary"
            >
              Back to the shelf
            </Link>
          )}
        </div>

        {}
        <InlineStatus />

        {
}
        {cert?.withheldBecause ? (
          <p className="wb-head-why" data-control="wb.head.why" data-novice="missing">
            <span>{cert.withheldBecause}</span>{" "}
            {gate.suppressed ? (
              <NextStep id="head.why" to="/review" label="See the ledger decision on Review" />
            ) : run ? (
              <NextStep id="head.why" to={routes.evidence(run.run_id)} label="Read the refusal on Evidence" />
            ) : null}
          </p>
        ) : null}
      </header>

      {!coherence.coherent ? (
        <p className="ag-collection-note" role="note" data-control="wb.incoherent">
          <b>These are two different objects.</b> {coherence.why}
        </p>
      ) : null}

      {ctx.scroll ? (
        <ScrollStatusView
          status={scrollStatus ?? null}
          mode={mode}
          control="workbench.scroll.status"
          note={scrollTruth.failure?.message ?? "Reading this scroll's recorded progress."}
        />
      ) : null}

      {ctx.scroll ? (
        <details className="wb-material-route" data-control="wb.material-route">
          <summary>
            <span className="wb-material-route-title">Raw CT to 3-D to 2-D</span>
            {materialReadiness.loading ? (
              <span>checking this material...</span>
            ) : materialReadiness.failure && !materialReadiness.data ? (
              <span>readiness route unavailable</span>
            ) : (
              <>
                <span>{materialReadiness.data?.state.replaceAll("_", " ").toLowerCase()}</span>
                <span className="wb-material-route-next" data-control="wb.scroll.next">
                  {}
                  {scrollStatus
                    ? `${scrollStatus.next_action.label}: ${scrollStatus.next_action.why}`
                    : "next step not read yet"}
                </span>
              </>
            )}
          </summary>
          {materialReadiness.data ? (
            <div className="wb-material-route-detail">
              <ol aria-label="Selected material route">
                {materialReadiness.data.route.map((stage) => (
                  <li key={stage.stage} data-state={stage.state} title={stage.why}>
                    <b>{stage.label}</b>
                    <span>{stage.state.replaceAll("_", " ").toLowerCase()}</span>
                  </li>
                ))}
              </ol>
              <p>
                {materialReadiness.data.detectors.length} detector/model candidates, each kept separate.
                Candidate output is evidence, not an automatic reading.
              </p>
            </div>
          ) : null}
        </details>
      ) : null}


      {ctx.scroll && scrollTruth.data ? (
        <section className="wb-truth-source" data-control="wb.truth-source" aria-label="Selected-scroll truth source">
          <strong>Selected-scroll truth source</strong>
          <span>
            Live route + identity-matched local inventory - {scrollTruth.data.local_material.counts.assets} record(s)
            {scrollTruth.data.local_material.scanned_utc ? ` - observed ${scrollTruth.data.local_material.scanned_utc}` : ""}
          </span>
          <small>{scrollTruth.data.claim_boundary}</small>
        </section>
      ) : null}

      {ctx.scroll && scrollTruth.failure ? (
        <section className="wb-truth-error" data-control="wb.truth-source.error" role="alert" aria-label="Selected-scroll truth unavailable">
          <strong>ARGUS could not read the selected-scroll truth source.</strong>
          <span>{scrollTruth.failure.message}</span>
          <span>Do not interpret empty canvases as missing science; check Sources or restart the service and interface from the same release checkout.</span>
          <Link className="link-control interactive" to={`/sources?tab=discovered&scroll=${encodeURIComponent(ctx.scroll)}`}>Open the local discovery inventory</Link>
        </section>
      ) : null}

      {ctx.scroll && discoveredForScroll.length ? (
        <section className="wb-discovered-summary" data-control="wb.discovered-summary" aria-label="Other local material found">
          <div>
            <strong>Other local material found for {ctx.scroll}</strong>
            <span>{discoveredForScroll.length} identity-matched records in the bounded local scan</span>
          </div>
          <div>
            <span>{discoveredForScroll.filter((a) => a.kind === "RENDER").length} renders · {discoveredForScroll.filter((a) => a.kind === "MESH").length} meshes · {discoveredForScroll.filter((a) => a.kind === "SCIENCE_RECORD").length} science records. Discovery does not import or infer.</span>
            <Link className="link-control interactive" to={`/sources?tab=discovered&scroll=${encodeURIComponent(ctx.scroll)}`}>Review everything found</Link>
          </div>
        </section>
      ) : null}

      {requestedIntel ? (
        <section className="wb-intel-context" data-control="wb.community-intelligence" aria-label="Community intelligence">
          <div className="wb-intel-kicker">Community intelligence · pending reproduction</div>
          {intelLoading ? <p className="wb-intel-muted">Reading the registered report…</p> : null}
          {intelError ? <p className="wb-intel-error">Could not read this report: {intelError}</p> : null}
          {intel ? (
            <>
              <h3>{intel.key}</h3>
              <p className="wb-intel-claim">{intel.claim}</p>
              <dl>
                <dt>What would reproduce it</dt>
                <dd>{intel.would_reproduce}</dd>
                <dt>Never use it for</dt>
                <dd>{intel.never_used_for ?? "not declared"}</dd>
              </dl>
              <p className="wb-intel-foot">Reported by {intel.reported_by}. This is a research lead, not a finding, gate result, or reading.</p>
            </>
          ) : null}
        </section>
      ) : null}

      {}
      {
}
      {
}
      <div className="wb-canvasbar" role="group" aria-label="Layout and canvas" data-wb-chrome="true">
        <span className="wb-canvasbar-label">Layout</span>
        {SAVED_LAYOUTS.map((l) => {
          const av = l.view ? viewState(l.view).available : false;
          const why = l.why ?? (l.view && !av ? viewState(l.view).why : "");
          return (
            <button
              key={l.id}
              type="button"
              className={`wb-view interactive${av ? "" : " wb-view-unavailable"}`}
              aria-disabled={av ? undefined : true}
              data-available={av ? "true" : "false"}
              title={why || l.label}
              aria-pressed={view === l.view && (av ? panel === l.panel : true)}
              data-control={`wb.layout.${l.id}`}
              onClick={() => {
                if (!l.view) return;
                chooseView(l.view);
                setPanel(av ? (wbClass === "narrow" ? null : l.panel) : wbClass === "narrow" ? null : "layers");
              }}
            >
              <span className="wb-view-label">{l.label}</span>
            </button>
          );
        })}
        <span className="wb-canvasbar-divider" aria-hidden="true" />
        <span className="wb-canvasbar-label">Canvas</span>
        {VIEWS.map((v) => {
          const available = viewState(v.key).available;
          const why = viewState(v.key).why;
          return (
            <button
              key={v.key}
              type="button"
              className={`wb-view interactive${available ? "" : " wb-view-unavailable"}`}
              aria-disabled={available ? undefined : true}
              data-available={available ? "true" : "false"}
              aria-pressed={view === v.key}
              data-control={`wb.canvas.${v.key}`}
              title={available ? v.hint : why}
              onClick={() => {
                chooseView(v.key);
                if (!available && wbClass !== "narrow") setPanel("layers");
              }}
            >
              <span className="wb-view-label">{v.plain}</span>
            </button>
          );
        })}
        {unavailableViews.length ? (
          <button
            type="button"
            className="wb-canvasbar-why interactive"
            aria-expanded={panel === "layers"}
            aria-controls="wb-drawer"
            data-control="wb.canvas.whyUnavailable"
            onClick={(e) => pf.open("layers", e.currentTarget)}
          >
            {unavailableViews.length} not available, and why
          </button>
        ) : null}
        {currentView ? <span className="wb-canvasbar-hint">{currentView.hint}</span> : null}
        {currentView ? <EvidenceRoleChip view={currentView.key} /> : null}
      </div>

      {}
      <div className="wb-body" data-panel={panel ?? "none"}>
        {}
        {panel === "layers" ? (
          <WbPanel id="wb-drawer" name="layers" title="Canvases and layers" className="wb-drawer" panelRef={pf.panelRef} onClose={pf.close} onKeyDown={pf.onKeyDown}>
            {
}
            {unavailableViews.length ? (
              <div className="ag-unavail">
                <h3 className="eyebrow wb-drawer-head">Not available</h3>
                {unavailableViews.map((v) => (
                  <div className="ag-unavail-row" key={v.key} data-control={`wb.canvas.${v.key}.unavailable`}>
                    <span>
                      <span className="ag-unavail-name">{v.plain}</span> — unavailable
                    </span>
                    <span className="ag-unavail-why">{viewState(v.key).why}</span>
                    {nextFor(v.key)}
                  </div>
                ))}
              </div>
            ) : null}

            {
}
            <h3 className="eyebrow wb-drawer-head">Layer studio</h3>
            {showsStack ? (
              <div className="wb-stack-controls">
                <div ref={setPortalHost} />
              </div>
            ) : (
              <p className="ag-prose" data-control="wb.studio.unavailable">
                The layer studio composes an exported layer stack. This canvas is not one, so
                the studio has nothing to compose — choose “Where a detector saw ink” or “Ink
                over the surface” when a stack exists for this scroll.{" "}
                <NextStep
                  id="studio"
                  to={`/sources?tab=holdings&scroll=${encodeURIComponent(ctx.scroll ?? "")}`}
                  label="See this scroll's available renderings on Sources"
                />
              </p>
            )}

            {
}
            <Clamshell id="identifiers" title="Technical identifiers">
              <ul className="wb-ids">
                {run ? (
                  <li>
                    <span className="small">run</span>
                    <span className="meta">{run.run_id}</span>
                  </li>
                ) : null}
                {ctx.scroll ? (
                  <li>
                    <span className="small">selected scroll</span>
                    <span className="meta">{ctx.scroll}</span>
                  </li>
                ) : null}
                {targetKey ? (
                  <li>
                    <span className="small">exported layer stack</span>
                    <span className="meta">{targetKey}</span>
                  </li>
                ) : null}
                {run && identity.missing.length ? (
                  <li>
                    <span className="small">name is not publishable</span>
                    <span className="meta">{identity.why}</span>
                  </li>
                ) : run ? (
                  <li>
                    <span className="small">public name</span>
                    <span className="meta">{identity.publicName}</span>
                  </li>
                ) : null}
                {run?.mesh_dir ? (
                  <li>
                    <span className="small">mesh</span>
                    <span className="meta">{run.mesh_dir}</span>
                  </li>
                ) : null}
                {run?.mesh_sha256
                  ? Object.entries(run.mesh_sha256).map(([k, v]) => (
                      <li key={k}>
                        <span className="small">{k}</span>
                        <span className="meta">{v}</span>
                      </li>
                    ))
                  : null}
              </ul>

              {
}
              <OtherStacks
                targets={targets}
                targetsErr={targetsErr}
                targetKey={targetKey}
                runId={run?.run_id ?? null}
                scroll={ctx.scroll}
              />
            </Clamshell>
          </WbPanel>
        ) : null}

        {}
        <section
          id="wb-canvas"
          ref={canvasRef}
          tabIndex={-1}
          className="wb-canvas"
          data-wb="canvas"
          data-novice="ready"
          aria-label="Workspace"
          {...(wbClass === "narrow" && panel ? ({ inert: "", "aria-hidden": "true" } as Record<string, string>) : {})}
        >
          {!coherence.coherent ? (
            <ContextRefusal selectedScroll={ctx.scroll} runScroll={run?.target?.split("/")[0] ?? null} />
          ) : !activeViewState.available ? (
            <UnavailableCanvas label={currentView?.plain ?? "This canvas"} why={activeViewState.why} next={nextFor(view)} />
          ) : view === "local" ? (
            <LocalMaterialCanvas
              scroll={ctx.scroll}
              assets={discoveredForScroll}
              status={scrollStatus ?? null}
              loading={!scrollTruth.data && !scrollTruth.failure}
              failure={scrollTruth.failure?.message ?? null}
            />
          ) : view === "mesh" && run ? (
            <Suspense
              fallback={
                <div className="ag-panel">
                  <p className="ag-prose">Loading the 3D renderer…</p>
                </div>
              }
            >
              <MeshCanvas meshDir={run.mesh_dir} runId={run.run_id} />
            </Suspense>
          ) : view === "windings" ? (
            <div className="wb-windings-canvas" data-control="wb.windings.canvas">
              <WindingInspector
                meshPath={run?.mesh_dir ?? undefined}
                pitchUm={run?.acquisition?.voxel_um ?? null}
                volumeId={run?.acquisition?.volume_id ?? null}
                onOpenCt={() => chooseView(viewState("rawvolume").available ? "rawvolume" : "planes")}
                next={
                  run ? (
                    <NextStep id="windings" to={routes.evidence(run.run_id)} label="Read this run's mesh record on Evidence" />
                  ) : (
                    <NextStep id="windings" to="/jobs" label="Find a run with a mesh on Jobs" />
                  )
                }
              />
            </div>
          ) : view === "planes" ? (
            <PlaneViewer selectedScroll={ctx.scroll} />
          ) : view === "rawvolume" ? (
            <RawVolumeViewer selectedScroll={ctx.scroll} />
          ) : view === "fibers" ? (
            <FiberLayerViewer selectedScroll={ctx.scroll} />
          ) : view === "hecate" ? (
            <HecateLayerViewer selectedScroll={ctx.scroll} />
          ) : view === "segmentation" ? (
            <SegmentationStatus selectedScroll={ctx.scroll} />
          ) : showsStack ? (
            <StackCanvas
              stack={stack}
              err={stackErr}
              mode={view === "overlay" ? "overlay" : "ink"}
              portalHost={panel === "layers" ? portalHost : null}
              runTarget={run?.target ?? null}
              noTarget={!targetKey}
              scroll={ctx.scroll}
              targetKey={targetKey}
            />
          ) : (
            <SurfaceViewer
              path={viewState(view).path}
              sealed={run?.blinding.sealed ?? false}
              missingBecause={viewState(view).why}
              label={VIEWS.find((v) => v.key === view)?.plain ?? ""}
              next={viewState(view).available ? undefined : nextFor(view)}
            />
          )}
        </section>

        {}
        {panel === "inspector" ? (
          <WbPanel id="wb-inspector" name="inspector" title="Checks and evidence" className="wb-inspector" panelRef={pf.panelRef} onClose={pf.close} onKeyDown={pf.onKeyDown}>
            {run ? (
              <Clamshell id="vigiles" title="VIGILES checks">
                <VigilesPanel run={run} evidenceTo={routes.evidence(run.run_id)} />
              </Clamshell>
            ) : null}
            {surfaceStatus ? (
              <Clamshell id="surfaceStatus" title="Status — every fact on its own, sourced">
                <SurfaceStatusDetail status={surfaceStatus} />
              </Clamshell>
            ) : null}
            {geom ? (
              <Clamshell
                id="geometry"
                title="Geometry verdict"
                aside={
                  <>
                    <Ruler size={16} aria-hidden style={{ color: "var(--ink-faint)" }} />
                    <Chip
                      tone={
                        geom.sheet_following === null || geom.sheet_following === undefined
                          ? "blocked"
                          : geom.sheet_following
                            ? "certified"
                            : "refused"
                      }
                      size="sm"
                    >
                      {geom.verdict}
                    </Chip>
                  </>
                }
              >
                <div className="wb-geom">
                  {geom.sheet_following === null || geom.sheet_following === undefined ? (
                    <span className="small faint">sheet-following was not measured</span>
                  ) : null}
                  <dl className="wb-stats">
                    <Stat k="inside volume" v={pct(geom.vertices_inside_volume)} />
                    <Stat k="jump fraction" v={pct(geom.jump_fraction, 3)} />
                    <Stat k="max / median" v={num(geom.step_max_ratio, 1, "×")} />
                    <Stat k="median edge" v={num(geom.step_median_vox, 1, " vox")} />
                    <Stat k="coverage" v={pct(geom.coverage)} />
                    <Stat k="edges" v={geom.n_edges ?? "—"} />
                    <Stat k="chunks sampled" v={geom.chunks_sampled ?? "—"} />
                  </dl>
                  {geom.sheet_following !== true && run ? (
                    <NextStep
                      id="geometry"
                      to={routes.evidence(run.run_id)}
                      label="Read the geometry record on Evidence"
                    />
                  ) : null}
                </div>
              </Clamshell>
            ) : null}

            {
}
            <div className="wb-check">
              <h3 className="eyebrow">The CT, plane by plane</h3>
              <p className="ag-prose">
                The plane viewer is on the canvas, where the depth scrubber and the
                orientation toggle are full-size controls.
              </p>
              <button
                type="button"
                className="ag-btn"
                data-control="wb.inspector.openPlanes"
                onClick={() => chooseView("planes")}
              >
                Put it on the canvas
              </button>
            </div>

            {
}
            <Clamshell id="windings" title="Windings" defaultOpen={requestedFocus === "windings"}>
              <WindingSummary
                meshPath={run?.mesh_dir ?? undefined}
                onOpenCanvas={() => {
                  chooseView("windings");
                  if (wbClass !== "wide") setPanel(null);
                }}
                next={
                  run ? (
                    <NextStep id="windings" to={routes.evidence(run.run_id)} label="Read this run's mesh record on Evidence" />
                  ) : (
                    <NextStep id="windings" to="/jobs" label="Find a run with a mesh on Jobs" />
                  )
                }
              />
            </Clamshell>

            {
}
            <Clamshell id="blender" title="Blender round trip" defaultOpen={requestedFocus === "blender"}>
              <BlenderRoundtrip meshPath={run?.mesh_dir ?? undefined} />
            </Clamshell>

            {showsStack && stack ? (
              <Clamshell id="review" title="Review queue">
                {
}
                <p className="small faint" style={{ margin: "0 0 6px" }}>
                  Answers recorded here are real. For the standing decisions this target sits
                  under -- qualification, gates, who may decide --{" "}
                  <Link to="/review">see Review Lab</Link>.
                </p>
                <ReviewQueue payload={stack.review_tasks as never} target={targetKey} />
              </Clamshell>
            ) : null}

            {
}
            {run ? (
              <Clamshell id="pipeline" title="What this run did, stage by stage">
                <PipelineLedger run={run} integritySuppressed={gate.suppressed} />
                <AxisLegend compact />
                {(run.stages ?? []).some((st) => st.status !== "PASS") ? (
                  <NextStep
                    id="pipeline"
                    to={routes.evidence(run.run_id)}
                    label="Read the stages that did not pass on Evidence"
                  />
                ) : null}
              </Clamshell>
            ) : null}
          </WbPanel>
        ) : null}
      </div>

      {
}
    </div>
    </SpatialStateProvider>
    </BenchFrame>
  );
}

function availableLayers(run: RunRecord | null, hasStack: boolean) {
  const out = {} as Record<LayerKey, { available: boolean; path: string | null; why: string }>;
  const stages = run?.stages ?? [];
  const render = stages.find((s) => s.module === "stage2.verified_render");
  const ink = stages.find((s) => s.module === "stage3.ink_maps");
  const images = (run?.artifacts ?? []).filter(
    (a) => a.path && /\.(png|jpe?g)$/i.test(a.name),
  );

  const NOT_A_FINDING =
    " Nothing has been read here, so this is not an absent finding -- it is a stage that has not produced output.";

  const stageWhy = (label: string, row: typeof render, producedBy: string) => {
    if (!run)
      return `No run is open, so ${label.toLowerCase()} has no producer to report on.${NOT_A_FINDING}`;
    if (run.blinding.sealed)
      return `This run is under an active blinding marker, so ${label.toLowerCase()} is withheld by the service.${NOT_A_FINDING}`;
    if (!row)
      return `No ${label.toLowerCase()} exists: ${producedBy} has not run for this run, which stopped at ${
        stages.at(-1)?.module ?? "its first stage"
      }.${NOT_A_FINDING}`;
    if (row.status !== "PASS")
      return `${producedBy} refused: ${row.refusal_reason ?? row.refusal_class}.${NOT_A_FINDING}`;
    return `${producedBy} passed but wrote no image for this layer.${NOT_A_FINDING}`;
  };

  for (const l of LAYERS) {
    if (l.key === "surface") {
      const path = images[0]?.path ?? null;
      out[l.key] = {
        available: Boolean(path) && !run?.blinding.sealed,
        path,
        why: stageWhy(l.label, render, l.producedBy),
      };
    } else if (l.key === "ct") {
      out[l.key] = {
        available: false,
        path: null,
        why:
          "Direct CT is not part of an exported layer stack, so the direct-CT layer has not run " +
          "here. Open the raw-scan canvas; a surface render is not a substitute for the measured " +
          "volume. Nothing has been read here, so this is not an absent finding.",
      };
    } else if (l.key === "ink" || l.key === "overlay") {
      out[l.key] = {
        available: hasStack && !run?.blinding.sealed,
        path: null,
        why: run?.blinding.sealed
          ? `This run is under an active blinding marker, so ${l.label.toLowerCase()} is withheld by the service.`
          : hasStack
            ? ""
            : `No exported layer stack names the selected scroll. ${stageWhy(l.label, ink, l.producedBy)}`,
      };
    } else {
      out[l.key] = {
        available: false,
        path: null,
        why:
          "ARGUS has no governed raster/layer adapter for this layer yet, " +
          "so this is not an absent finding.",
      };
    }
  }
  return out;
}

function OtherStacks({
  targets,
  targetsErr,
  targetKey,
  runId,
  scroll,
}: {
  targets: UnrollTarget[] | null;
  targetsErr: string | null;
  targetKey: string | null;
  runId: string | null;
  scroll: string | null;
}) {
  if (targets === null) {
    return <p className="ag-prose">Asking the service which targets have an exported stack…</p>;
  }
  if (targets.length === 0) {
    return (
      <p className="ag-prose">
        {targetsErr
          ? `The layer-stack list could not be read (${targetsErr}). This is not a statement that no layer stack exists — the question was never answered.`
          : "No layer stack has been exported. Nothing is hidden here; there is simply no artifact yet."}
        {targetsErr ? (
          <>
            {" "}
            <NextStep id="stacks.error" to={`/sources?scroll=${encodeURIComponent(scroll ?? "")}`} label="See this scroll's available renderings on Sources" />
          </>
        ) : null}
      </p>
    );
  }
  const others = targets.filter((t) => t.key !== targetKey);
  if (!others.length) {
    return (
      <p className="ag-prose">
        {targets.length} exported stack{targets.length === 1 ? "" : "s"} exist and this is the
        only one; nothing else is available to open.
      </p>
    );
  }
  return (
    <>
      <p className="ag-prose">
        {others.length} other exported stack{others.length === 1 ? "" : "s"}. Opening one
        changes the URL, so the canvas always names the object it is drawing — and a stack for a
        different scroll stays visibly a different object.
      </p>
      <ul className="ag-list">
        {others.map((t) => (
          <li key={t.key}>
            <Link
              className="ag-item"
              data-control={`wb.stack.${t.key}`}
              to={`${routes.workbench(runId)}?target=${encodeURIComponent(t.key)}${
                scroll ? `&scroll=${encodeURIComponent(scroll)}` : ""
              }`}
              title={t.one_line}
            >
              <span className="ag-item-title">{t.name}</span>
              <span className="ag-item-sub">
                {t.acquisition}
                {t.has_ground_truth ? " · control, with ground truth" : ""}
              </span>
              <span className="ag-item-id">{t.key}</span>
            </Link>
          </li>
        ))}
      </ul>
    </>
  );
}

function StackCanvas({
  stack,
  err,
  mode,
  portalHost,
  runTarget,
  noTarget,
  scroll,
  targetKey,
}: {
  stack: UnrollData | null;
  err: string | null;
  mode: "ink" | "overlay";
  portalHost: HTMLElement | null;
  runTarget: string | null;
  noTarget: boolean;
  scroll: string | null;
  targetKey: string | null;
}) {
  if (noTarget)
    return (
      <Empty
        title="No exported layer stack for this selection"
        body={
          `Nothing in the exported-stack index names ${scroll ?? "the selected scroll"}, so there ` +
          "is nothing to compose. This is an absence of an export, not a reading: no detector " +
          "output has been examined and found empty."
        }
        next={
          <NextStep
            id="stack.none"
            to={`/sources?scroll=${encodeURIComponent(scroll ?? "")}`}
            label="See this scroll's available renderings on Sources"
          />
        }
      />
    );
  if (err)
    return (
      <Empty
        title="Could not load the layer stack"
        body={err}
        next={
          targetKey ? (
            <NextStep
              id="stack.error"
              to={`/sources?scroll=${encodeURIComponent(scroll ?? "")}`}
              label="See this scroll's available renderings on Sources"
            />
          ) : undefined
        }
      />
    );
  if (!stack) return <Empty title="Loading" body="Reading the layer contract and its value planes." />;

  const claimed = runTarget ?? scroll;
  const claimedScroll = claimed ? (/PHerc[0-9A-Za-z]+/i.exec(claimed)?.[0] ?? null) : null;
  const mismatch =
    claimedScroll !== null && !stack.target.name.toLowerCase().includes(claimedScroll.toLowerCase());

  return (
    <div className="wb-stack">
      {mismatch ? (
        <p className="wb-stack-mismatch" role="note" data-control="wb.stack.mismatch">
          <strong>These are two different objects.</strong> The selection is {claimed}; the
          layers on this canvas are {stack.target.name}
          {stack.target.acquisition ? ` (${stack.target.acquisition})` : ""}. They may be placed
          side by side and may NOT be compared numerically.
        </p>
      ) : null}
      <div className="wb-stack-banner">
        <span className="tag">{stack.result_class.presentation.replace(/_/g, " ")}</span>
        <span className="small">{stack.banner_required_on_every_surface}</span>
      </div>
      {stack.ground_truth && stack.ground_truth.state !== "PRESENT" ? (
        <p className="small faint wb-stack-gt">
          <b className="muted">Ground truth is absent for this target.</b>{" "}
          {stack.ground_truth.why}
        </p>
      ) : null}
      <div className="wb-stack-canvas">
        <LayerStack data={stack} mode={mode} controlsPortal={portalHost} />
      </div>
    </div>
  );
}

function MeshIdentitySync({ meshDir }: { meshDir: string | null }) {
  const spatial = useSpatialState();
  useEffect(() => {
    spatial.setMesh(meshDir);
  }, [meshDir]);
  return null;
}

function LocalMaterialCanvas({
  scroll,
  assets,
  status,
  loading,
  failure,
}: {
  scroll: string | null;
  assets: LocalDiscoveryAsset[];
  status: ScrollStatus | null;
  loading: boolean;
  failure: string | null;
}) {
  if (!scroll) return <div data-control="wb.local-material" data-loading="false"><Empty title="No scroll selected" body="Choose a physical scroll before reading its local inventory." /></div>;
  if (loading) return <div data-control="wb.local-material" data-loading="true"><Empty title={`Checking ${scroll}`} body="Reading the bounded identity-matched local inventory. This is not an empty result." /></div>;
  if (failure) {
    return (
      <div data-control="wb.local-material" data-loading="false">
        <Empty
          title="Local inventory unavailable"
          body={`${failure}. ARGUS has not concluded that no material exists.`}
          next={<NextStep id="local.failure" to={`/sources?tab=discovered&scroll=${encodeURIComponent(scroll)}`} label="Open Sources and retry the inventory" />}
        />
      </div>
    );
  }

  const renders = assets.filter((a) => a.kind === "RENDER");
  const meshes = assets.filter((a) => a.kind === "MESH");
  const records = assets.filter((a) => a.kind === "SCIENCE_RECORD");
  const unique = (rows: LocalDiscoveryAsset[]) => {
    const seen = new Set<string>();
    return rows.filter((asset) => {
      const key = asset.sha256 || asset.asset_id;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  };
  const viewable = unique(renders.filter((a) => a.viewable && a.preview_url));
  const viewableHashes = new Set(viewable.map((a) => a.sha256).filter(Boolean));
  const unmounted = unique(renders.filter((a) => !a.viewable && (!a.sha256 || !viewableHashes.has(a.sha256))));
  const renderGroups = new Map<string, LocalDiscoveryAsset[]>();
  for (const asset of viewable) {
    const folder = asset.display_path.replace(/\/[^/]+$/, "");
    const group = renderGroups.get(folder) ?? [];
    group.push(asset);
    renderGroups.set(folder, group);
  }
  const orderedRenderGroups = [...renderGroups.entries()].sort(([a], [b]) => a.localeCompare(b));
  const groupLabel = (folder: string) => {
    return folder.split("/").slice(-2).join(" / ");
  };
  const science = status?.science_artifacts;
  const work = status?.work_queue ?? [];
  const result = status?.questions.result;
  const detector = status?.questions.detector;
  const inkLine = science?.detector_run
    ? science.presented_as_ink
      ? "A detector result is presented as ink by the attached science manifest."
      : "A detector ran, but its output is not an ink finding."
    : result?.answer && result.answer !== "none recorded"
      ? "A detector result exists, but it is operational evidence from an unqualified detector - not a reading."
      : "No detector result is recorded for this scroll, so ARGUS has not found ink here."

  return (
    <div className="wb-local-material" data-control="wb.local-material" data-loading="false" data-scroll={scroll}>
      <header className="wb-local-head">
        <div>
          <span className="eyebrow">Identity-matched local material</span>
          <h3>{scroll}: what actually exists on this machine</h3>
          <p>{assets.length} records found: {renders.length} render{renders.length === 1 ? "" : "s"}, {meshes.length} mesh{meshes.length === 1 ? "" : "es"}, and {records.length} science record{records.length === 1 ? "" : "s"}.</p>
        </div>
        <Link className="link-control interactive" to={`/sources?tab=discovered&scroll=${encodeURIComponent(scroll)}`}>
          Open all {assets.length} records
        </Link>
      </header>

      <section className="wb-local-answer" aria-label="Ink answer" data-control="wb.local-material.ink-answer">
        <strong>Did ARGUS find ink?</strong>
        <span>{inkLine}</span>
        <small>{detector?.detail ?? status?.claim_ceiling ?? "No detector qualification record was returned."}</small>
      </section>

      <section className="wb-local-route" aria-label="What is still missing">
        <div>
          <h4>What is still missing</h4>
          {work.length ? (
            <ol>
              {work.slice(0, 5).map((item) => (
                <li key={item.step} data-state={item.state}>
                  <strong>Step {item.n}: {item.label}</strong>
                  <span>{item.why}</span>
                  <Link to={item.to}>{item.action_label}</Link>
                </li>
              ))}
            </ol>
          ) : <p>No open work item was returned. Read the sixteen-step route before treating that as completion.</p>}
        </div>
        <div className="wb-local-next">
          <span className="eyebrow">Next honest action</span>
          <strong>{status?.next_action.label ?? "No next action returned"}</strong>
          <p>{status?.next_action.why ?? "The selected-scroll route did not answer."}</p>
          {status?.next_action.to ? <Link className="link-control interactive wb-primary" to={status.next_action.to}>Go to this step</Link> : null}
          {science?.to ? (
            <Link className="link-control interactive" to={science.to}>
              {science.state === "DISCOVERED_NOT_ATTACHED" ? "Review and attach the discovered material" : "Open the verified science collection"}
            </Link>
          ) : null}
        </div>
      </section>

      {viewable.length ? (
        <section className="wb-local-library" aria-label="All viewable local renders">
          <header>
            <div>
              <h4>All viewable renders</h4>
              <p>{viewable.length} unique render{viewable.length === 1 ? "" : "s"}, grouped by the folder that produced them. Exact duplicate bytes are shown once.</p>
            </div>
            <span>{renderGroups.size} source group{renderGroups.size === 1 ? "" : "s"}</span>
          </header>
          {orderedRenderGroups.map(([folder, group]) => (
            <section className="wb-local-render-group" key={folder} data-control={`wb.local-material.group.${group[0]?.asset_id ?? "empty"}`}>
              <div className="wb-local-render-group-head">
                <strong>{groupLabel(folder)}</strong>
                <code>{folder}</code>
                <span>{group.length} render{group.length === 1 ? "" : "s"}</span>
              </div>
              <div className="wb-local-previews">
                {group.map((asset) => (
                  <figure key={asset.asset_id} data-control={`wb.local-material.preview.${asset.asset_id}`}>
                    <img src={asset.preview_url ?? ""} alt={`${scroll} ${asset.name}`} loading="lazy" />
                    <figcaption>
                      <strong>{asset.name}</strong>
                      <span>{asset.status.replaceAll("_", " ").toLowerCase()} - derived visualization</span>
                    </figcaption>
                  </figure>
                ))}
              </div>
            </section>
          ))}
        </section>
      ) : (
        <section className="wb-local-unmounted" data-control="wb.local-material.unmounted">
          <strong>{renders.length ? `${renders.length} render${renders.length === 1 ? " is" : "s are"} present, but not mounted for browser viewing.` : "No local render is registered."}</strong>
          <span>
            {renders.length
              ? "ARGUS still lists the exact names and hashes; it will not silently serve private bytes from an undeclared root."
              : "This is an artifact/inventory state, not evidence that the scroll contains no ink."}
          </span>
        </section>
      )}

      {unmounted.length ? (
        <section className="wb-local-unmounted-list" data-control="wb.local-material.unmounted-list">
          <header>
            <h4>Present but not browser-mounted</h4>
            <span>{unmounted.length} unique render{unmounted.length === 1 ? "" : "s"}; these do not duplicate a viewable image above.</span>
          </header>
          <div>
            {unmounted.map((asset) => (
              <article key={asset.asset_id} data-control={`wb.local-material.unmounted.${asset.asset_id}`}>
                <strong>{asset.name}</strong>
                <span>{asset.status.replaceAll("_", " ").toLowerCase()}</span>
                <code>{asset.display_path}</code>
              </article>
            ))}
          </div>
          <p>
            ARGUS knows these bytes exist and records their hashes, but refuses to expose a private undeclared path. {science?.state === "DISCOVERED_NOT_ATTACHED"
              ? "No verified manifest is attached for this material."
              : "Mount the declared root to view these files."} Do not copy the files just to make this screen green.
          </p>
        </section>
      ) : null}

      <section className="wb-local-register" aria-label="Representative local assets">
        <h4>Files you have</h4>
        <div>
          {[...meshes.slice(0, 4), ...records.slice(0, 8)].map((asset) => (
            <article key={asset.asset_id} data-control={`wb.local-material.asset.${asset.asset_id}`}>
              <strong>{asset.name}</strong>
              <span>{asset.kind.replaceAll("_", " ").toLowerCase()} - {asset.status.replaceAll("_", " ").toLowerCase()}</span>
              <code>{asset.display_path}</code>
              {asset.kind === "RENDER" && !asset.viewable ? <small>present and indexed; viewer mount still required</small> : null}
            </article>
          ))}
        </div>
      </section>

    </div>
  );
}

function Stat({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div>
      <dt className="small faint">{k}</dt>
      <dd className="mono" style={{ margin: 0, fontSize: "var(--t-small)" }}>
        {v}
      </dd>
    </div>
  );
}

function Empty({ title, body, next }: { title: string; body: string; next?: React.ReactNode }) {
  return (
    <div style={{ padding: 22 }}>
      <div className="panel" style={{ padding: 26, maxWidth: 660, display: "grid", gap: 10 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <AlertCircle size={17} aria-hidden style={{ color: "var(--status-blocked)" }} />
          <h2 style={{ fontSize: "var(--t-h2)" }}>{title}</h2>
        </div>
        <p className="muted" style={{ margin: 0 }}>
          {body}
        </p>
        {next ?? null}
      </div>
    </div>
  );
}

function UnavailableCanvas({
  label,
  why,
  next,
}: {
  label: string;
  why: string;
  next?: React.ReactNode;
}) {
  return (
    <div className="ag-panel wb-unavailable-canvas" data-testid="workbench-unavailable-canvas" role="status">
      <span className="eyebrow">Not available for this scroll</span>
      <h3 className="ag-panel-title">{label}</h3>
      <p className="ag-prose">{why}</p>
      <div className="wb-unavailable-actions">
        {next}
      </div>
    </div>
  );
}

function ContextRefusal({ selectedScroll, runScroll }: { selectedScroll: string | null; runScroll: string | null }) {
  return (
    <div className="ag-panel" data-testid="workbench-context-refusal">
      <h3 className="ag-panel-title">The selected scroll and run do not match</h3>
      <p className="ag-prose">
        Refusing to display the run for {runScroll ?? "an undeclared scroll"} under {selectedScroll ?? "no selected scroll"}.
        Change the URL context or open a run belonging to the selected scroll.
      </p>
    </div>
  );
}

function pct(v: number | null | undefined, dp = 1) {
  return v === null || v === undefined ? "—" : `${(v * 100).toFixed(dp)}%`;
}
function num(v: number | null | undefined, dp = 1, suffix = "") {
  return v === null || v === undefined ? "—" : `${v.toFixed(dp)}${suffix}`;
}

function RunBadge({ cert: c }: { cert: ReturnType<typeof runCertification> }) {
  return (
    <span className="wb-badge" title={c.withheldBecause ?? c.descriptor.means}>
      <TruthChip state={c.state} size="sm" />
      {c.rung ? (
        <span className="meta faint wb-badge-extra">{CERT_LABEL[c.rung] ?? c.rung}</span>
      ) : null}
      {
}
      {c.stagesTotalDeclared ? (
        <span className="meta faint wb-badge-extra">
          {c.stagesRun}/{c.stagesTotalDeclared} stages passed
          {c.stagesRefused ? `, ${c.stagesRefused} refused` : ""}
        </span>
      ) : null}
    </span>
  );
}
