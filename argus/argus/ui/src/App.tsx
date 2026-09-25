import { lazy, Suspense, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigationType,
  useParams,
  useSearchParams,
} from "react-router-dom";
import "./theme/tokens.css";
import "./theme/shell.css";
import "./theme/clarity.css";
import { subscribe, type FeedState, type RunRecord, type RuntimeReport } from "./api";
import { getJson } from "./lib/http";
import { useArgusContext } from "./lib/context";
import { Masthead } from "./components/Masthead";
import { NavRail } from "./components/NavRail";
import { GrailDiary } from "./components/GrailDiary";
import { ContextStrip } from "./components/ContextStrip";
import { StatusContext, StatusDock } from "./components/StatusDock";
import { useDisplayPrefs } from "./lib/displayPrefs";
import { UniverseProvider } from "./lib/sharedUniverse";
import { ProductStateProvider } from "./lib/productState";
import { usePublicDemo } from "./lib/publicDemo";
import { ProductBar } from "./components/ProductBar";
import { UpdateNotice } from "./components/UpdateNotice";
import { COLLECTIONS, Collections } from "./screens/Collections";

const Home = lazy(() => import("./screens/Home").then((m) => ({ default: m.Home })));
const Explore = lazy(() => import("./screens/Explore").then((m) => ({ default: m.Explore })));
const Review = lazy(() => import("./screens/Review").then((m) => ({ default: m.Review })));
const SystemHub = lazy(() => import("./screens/SystemHub").then((m) => ({ default: m.SystemHub })));
const Workbench = lazy(() => import("./screens/Workbench").then((m) => ({ default: m.Workbench })));
const Evidence = lazy(() => import("./screens/Evidence").then((m) => ({ default: m.Evidence })));
const Sources = lazy(() => import("./screens/Sources").then((m) => ({ default: m.Sources })));
const Models = lazy(() => import("./screens/Models").then((m) => ({ default: m.Models })));
const Jobs = lazy(() => import("./screens/Jobs").then((m) => ({ default: m.Jobs })));
const Leaderboard = lazy(() => import("./screens/Leaderboard").then((m) => ({ default: m.Leaderboard })));
const Monitor = lazy(() => import("./screens/Monitor").then((m) => ({ default: m.Monitor })));
const NotFound = lazy(() => import("./screens/NotFound").then((m) => ({ default: m.NotFound })));

function RoomLoading() {
  return <p className="meta" role="status" data-control="shell.room.loading" style={{ padding: 24 }}>Opening this room…</p>;
}

declare const __ARGUS_UI_SOURCE_ROOT__: string;
declare const __ARGUS_UI_BUILD_SHA__: string;

const UI_CONTRACT = "argus-ui-contract-20260921-v1";

function HashAnchor() {
  const loc = useLocation();
  useEffect(() => {
    if (!loc.hash) return undefined;
    let id = loc.hash.slice(1);
    try {
      id = decodeURIComponent(id);
    } catch {
    }
    let userMoved = false;
    let focused = false;
    const stopFollowing = () => { userMoved = true; };
    const userEvents = ["wheel", "touchstart", "keydown", "pointerdown"] as const;
    userEvents.forEach((e) => window.addEventListener(e, stopFollowing, { passive: true, capture: true }));
    const move = () => {
      if (userMoved) return true;
      const target = document.getElementById(id);
      if (!target) return false;
      const main = target.closest<HTMLElement>(".argus-main");
      if (main) {
        const mainRect = main.getBoundingClientRect();
        const targetRect = target.getBoundingClientRect();
        const stickyOffset = Number.parseFloat(getComputedStyle(main).scrollPaddingTop) || 0;
        main.scrollTo({
          top: main.scrollTop + targetRect.top - mainRect.top - stickyOffset,
          behavior: "auto",
        });
      } else {
        target.scrollIntoView({ block: "start", behavior: "auto" });
      }
      if (window.scrollX || window.scrollY) window.scrollTo({ left: 0, top: 0, behavior: "auto" });
      if (focused && document.activeElement === document.body) focused = false;
      if (!focused) {
        const focusable = target.matches("[tabindex],button,a[href],input,select,textarea")
          ? target
          : target.querySelector<HTMLElement>("[tabindex='-1'],h1,h2,h3,h4,button,a[href],input,select,textarea");
        if (focusable) {
          if (/^H[1-4]$/.test(focusable.tagName) && !focusable.hasAttribute("tabindex")) focusable.setAttribute("tabindex", "-1");
          focusable.focus({ preventScroll: true });
          focused = document.activeElement === focusable;
        }
      }
      return true;
    };
    const resizeObserver = new ResizeObserver(() => move());
    const watchLayout = () => {
      const target = document.getElementById(id);
      if (!target) return;
      resizeObserver.observe(target);
      const page = target.closest<HTMLElement>(".ops-page, .home-page, .bench-work, [data-route]");
      if (page) resizeObserver.observe(page);
    };
    const observer = new MutationObserver(() => {
      move();
      watchLayout();
    });
    observer.observe(document.body, { childList: true, subtree: true });
    move();
    watchLayout();
    const interval = window.setInterval(move, 250);
    const timeout = window.setTimeout(() => {
      observer.disconnect();
      resizeObserver.disconnect();
      window.clearInterval(interval);
    }, 15_000);
    return () => {
      observer.disconnect();
      resizeObserver.disconnect();
      window.clearInterval(interval);
      window.clearTimeout(timeout);
      userEvents.forEach((e) => window.removeEventListener(e, stopFollowing, { capture: true }));
    };
  }, [loc.pathname, loc.search, loc.hash]);
  return null;
}

function BuildIdentityBanner({ report, failure }: { report: RuntimeReport | null; failure?: string | null }) {
  const identity = report?.service_identity;
  const uiRoot = typeof __ARGUS_UI_SOURCE_ROOT__ === "string" ? __ARGUS_UI_SOURCE_ROOT__ : "unknown";
  const uiSha = typeof __ARGUS_UI_BUILD_SHA__ === "string" ? __ARGUS_UI_BUILD_SHA__ : "unknown";
  const normalizeRoot = (value: string) => value.replaceAll("\\", "/").replace(/\/+$/, "").toLowerCase();
  const rootsMatch = uiRoot === "not-embedded" || Boolean(
    identity?.source_root &&
    identity.source_root !== "unknown" &&
    uiRoot !== "unknown" &&
    normalizeRoot(identity.source_root) === normalizeRoot(uiRoot),
  );
  const buildsKnown = Boolean(identity?.build_sha && identity.build_sha !== "unknown" && uiSha !== "unknown");
  const buildsMatch = Boolean(buildsKnown && identity?.build_sha === uiSha);
  const compatible = Boolean(identity && identity.api_contract === UI_CONTRACT && rootsMatch && buildsMatch);
  if (compatible) return null;
  const reason = report
    ? identity
      ? identity.api_contract !== UI_CONTRACT
        ? `The service advertises ${identity.api_contract}, but this interface requires ${UI_CONTRACT}.`
        : !buildsKnown
          ? "This interface and service do not carry a known release SHA. Start both from the exact frozen release identity."
          : !buildsMatch
            ? `The service is build ${identity.build_sha}, but this interface is build ${uiSha}.`
        : `The service is running from ${identity.source_root}, but this interface was built from ${uiRoot}.`
      : "The service answered, but it does not advertise the ARGUS build identity contract."
    : failure
      ? `Runtime identity check failed: ${failure}. Retrying automatically.`
      : "The interface has not established which ARGUS service it is reading.";
  return (
    <section className="build-mismatch" role="alert" data-control="build.mismatch">
      <strong>This ARGUS workspace is not safe to use yet.</strong>
      <span>{reason}</span>
      <span className="build-mismatch-detail">
        UI root: {uiRoot} · UI build: {uiSha} · service build: {identity?.build_sha ?? "unknown"} · restart both from one exact checkout.
      </span>
    </section>
  );
}

export type RailMode = "full" | "collapsed" | "bar";

export const BP_STACK = 760;
export const BP_RAIL = 1100;

function railModeFor(w: number): RailMode {
  if (w < BP_STACK) return "bar";
  if (w < BP_RAIL) return "collapsed";
  return "full";
}

export default function App() {
  const display = useDisplayPrefs();
  const [collection, setCollection] = useState("herculaneum");
  const [feed, setFeed] = useState<FeedState>({
    data: null,
    ageS: 0,
    connected: false,
    socketOpen: false,
    snapshotAgeS: null,
    error: null,
  });
  const [runtime, setRuntime] = useState<RuntimeReport | null>(null);
  const [runtimeFailure, setRuntimeFailure] = useState<string | null>(null);
  const loc = useLocation();
  const demo = usePublicDemo(loc.search);

  const isMonitor = loc.pathname === "/m" || loc.pathname.startsWith("/m/");

  useEffect(() => subscribe(setFeed), []);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof window.setTimeout> | undefined;
    let attempt = 0;
    let controller: AbortController | undefined;
    const read = async () => {
      controller = new AbortController();
      const result = await getJson<RuntimeReport>("/api/runtime", { timeoutMs: 8000, signal: controller.signal });
      if (!alive) return;
      if (result.ok) {
        setRuntime(result.data);
        setRuntimeFailure(null);
        return;
      }
      setRuntime(null);
      setRuntimeFailure(result.message);
      const delay = Math.min(1000 * 2 ** Math.min(attempt, 4), 15000);
      attempt += 1;
      timer = window.setTimeout(read, delay);
    };
    void read();
    return () => {
      alive = false;
      if (timer !== undefined) window.clearTimeout(timer);
      controller?.abort();
    };
  }, []);
  useEffect(() => {
    document.documentElement.setAttribute("data-collection", collection);
  }, [collection]);
  const { mode } = useArgusContext();
  useEffect(() => {
    document.documentElement.setAttribute("data-argus-mode", mode);
  }, [mode]);

  const runs = useMemo(() => feed.data?.runs ?? [], [feed.data]);
  const collectionName =
    COLLECTIONS.find((c) => c.id === collection)?.name ?? "Instrument";

  const [rail, setRail] = useState<RailMode>(() => railModeFor(window.innerWidth));
  useEffect(() => {
    const onResize = () => setRail(railModeFor(window.innerWidth));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const mainRef = useRef<HTMLElement | null>(null);
  const positions = useRef<Map<string, number>>(new Map());
  const navType = useNavigationType();
  const prevPath = useRef(loc.pathname);

  useLayoutEffect(() => {
    const el = mainRef.current;
    if (!el) return;
    if (prevPath.current !== loc.pathname) {
      positions.current.set(prevPath.current, el.scrollTop);
      prevPath.current = loc.pathname;
    }
    el.scrollTop = navType === "POP" ? (positions.current.get(loc.pathname) ?? 0) : 0;
  }, [loc.pathname, navType]);

  if (isMonitor) {
    return (
      <Suspense fallback={<RoomLoading />}>
      <Routes>
        <Route path="/m" element={<Monitor />} />
        <Route path="/m/*" element={<Navigate to="/m" replace />} />
      </Routes>
      </Suspense>
    );
  }

  return (
    <UniverseProvider runs={runs}>
    <ProductStateProvider>
    <StatusContext.Provider value={{ feed, prefs: display.prefs, deviceClass: display.deviceClass, update: display.update, reset: display.reset }}>
    <div className="argus-shell" data-rail={rail} data-public-demo={demo ? "true" : undefined}>
      {}
      <a className="skip-link" data-skip-link data-control="shell.skip" href="#argus-main">
        Skip to content
      </a>
      <Masthead collectionName={collectionName}
        rail={rail}

      />
      <NavRail mode={rail} />
      <main
        id="argus-main"
        ref={mainRef}
        className="argus-main"
        tabIndex={-1}
        aria-label="Room"
      >
        <HashAnchor />
        {


}
        <ContextStrip feed={feed} />
        <BuildIdentityBanner report={runtime} failure={runtimeFailure} />
        {
}
        <ProductBar />
        <UpdateNotice />
        {
}
        {
}
        <div style={{ marginBottom: "var(--s-4)" }}>
          <GrailDiary runs={runs} />
        </div>
        <Suspense fallback={<RoomLoading />}>
        <Routes>
          {}
          <Route path="/" element={<Home feed={feed} />} />
          <Route path="/explore" element={<Explore feed={feed} />} />
          <Route path="/workbench" element={<WorkbenchRoute runs={runs} feed={feed} />} />
          <Route
            path="/workbench/:runId"
            element={<WorkbenchRoute runs={runs} feed={feed} />}
          />
          <Route path="/review" element={<Review feed={feed} />} />
          <Route path="/evidence" element={<EvidenceRoute runs={runs} />} />
          <Route path="/evidence/:runId" element={<EvidenceRoute runs={runs} />} />
          <Route
            path="/system"
            element={
              <SystemHub feed={feed} collection={collection} onPickCollection={setCollection} />
            }
          />

          {
}
          <Route path="/jobs" element={<Jobs />} />
          <Route path="/sources" element={<Sources />} />
          {
}
          <Route path="/collections" element={<Collections current={collection} onPick={setCollection} runs={runs} />} />
          <Route path="/leaderboard" element={<Leaderboard />} />

          {



}
          <Route path="/observatory" element={<LegacyRoute to="/explore" tab="archive" />} />
          <Route path="/library" element={<LegacyRoute to="/explore" />} />
          <Route path="/models" element={<Models />} />
          <Route path="/ingest" element={<LegacyRoute to="/sources" tab="ingest" />} />
          <Route path="/holdings" element={<LegacyRoute to="/sources" tab="holdings" />} />
          <Route path="/workspace" element={<LegacyRoute to="/workbench" />} />
          <Route path="/monitor" element={<LegacyRoute to="/m" />} />

          <Route path="*" element={<NotFound />} />
        </Routes>
        </Suspense>
      </main>
      {
}
      <StatusDock feed={feed} prefs={display.prefs} deviceClass={display.deviceClass}
                  update={display.update} reset={display.reset} />
    </div>
    </StatusContext.Provider>
    </ProductStateProvider>
    </UniverseProvider>
  );
}

function LegacyRoute({ to, tab }: { to: string; tab?: string }) {
  const loc = useLocation();
  const q = new URLSearchParams(loc.search);
  if (tab && !q.has("tab")) q.set("tab", tab);
  const search = q.toString();
  return <Navigate to={to + (search ? `?${search}` : "") + loc.hash} replace />;
}

function useRun(runs: RunRecord[], feed?: FeedState) {
  const { runId } = useParams();
  const decoded = runId ? decodeURIComponent(runId) : null;
  const run = decoded ? (runs.find((r) => r.run_id === decoded) ?? null) : null;
  const loaded = feed ? feed.data !== null : runs.length > 0;
  return { runId: decoded, run, loaded };
}

function WorkbenchRoute({ runs, feed }: { runs: RunRecord[]; feed: FeedState }) {
  const { runId, run, loaded } = useRun(runs, feed);
  const [params] = useSearchParams();
  return (
    <Workbench
      run={run}
      runs={runs}
      requestedId={runId}
      requestedTarget={params.get("target")}
      requestedIntel={params.get("intel")}
      loaded={loaded}
    />
  );
}

function EvidenceRoute({ runs }: { runs: RunRecord[] }) {
  const { runId, run, loaded } = useRun(runs);
  return <Evidence run={run} requestedId={runId} loaded={loaded} runs={runs} />;
}
