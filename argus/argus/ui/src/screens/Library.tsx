import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  ChevronRight,
  Filter,
  Layers,
  Lock,
  ScrollText,
  Search,
  X,
} from "lucide-react";
import type { Certified, FeedState, Operational, RunRecord } from "../api";
import { rankCertified } from "../api";
import {
  groupBookcases,
  groupTargets,
  recoveredTexts,
  UNCATALOGUED,
  type TargetGroup,
} from "../lib/targets";
import { routes, runDestinations } from "../lib/nav";
import { CERT_LABEL, CertLadder, Chip, ProgressReadout, operationalTone } from "../components/Status";
import { Holdings } from "./Holdings";
import { buildHoldingsView, fetchScrollIds, type ScrollIds } from "../lib/holdings";
import { fetchReceipts, type ReceiptsState } from "../lib/receipts";
import { useFocusTrap } from "../lib/useFocusTrap";
import { PublicRuns } from "../components/evidence/PublicRunReceipt";

export type Mode = "archive" | "texts" | "holdings";
type SortKey = "newest" | "target" | "status" | "certification";

export function Library({
  feed,
  hideModeTabs = false,
  modeOverride,
}: {
  feed: FeedState;
  hideModeTabs?: boolean;
  modeOverride?: Mode;
}) {
  const [params, setParams] = useSearchParams();
  const runs = useMemo(() => feed.data?.runs ?? [], [feed.data]);
  const targets = useMemo(() => groupTargets(runs), [runs]);

  const mode: Mode = modeOverride ?? (params.get("mode") as Mode) ?? "archive";
  const selectedTarget = params.get("target");
  const selectedRun = params.get("run");

  const [q, setQ] = useState(params.get("q") ?? "");
  const [fCollection, setFCollection] = useState(params.get("collection") ?? "");
  const [fCert, setFCert] = useState(params.get("cert") ?? "");
  const [fState, setFState] = useState(params.get("state") ?? "");
  const [fSeal, setFSeal] = useState(params.get("seal") ?? "");
  const [sort, setSort] = useState<SortKey>((params.get("sort") as SortKey) ?? "target");

  const filtered = useMemo(
    () => applyFilters(targets, { q, fCollection, fCert, fState, fSeal, sort }),
    [targets, q, fCollection, fCert, fState, fSeal, sort],
  );
  const bookcases = useMemo(() => groupBookcases(filtered), [filtered]);
  const texts = useMemo(() => recoveredTexts(runs), [runs]);
  const loaded = feed.data !== null;

  const [rec, setRec] = useState<ReceiptsState>({
    index: null,
    failure: null,
    settled: false,
  });
  const [ids, setIds] = useState<ScrollIds | null>(null);
  useEffect(() => {
    let live = true;
    fetchReceipts().then((s) => live && setRec(s));
    fetchScrollIds().then((s) => live && setIds(s));
    return () => {
      live = false;
    };
  }, []);
  const holdings = useMemo(() => buildHoldingsView(rec.index, ids), [rec.index, ids]);

  const open = (targetId: string, runId?: string | null) => {
    const next = new URLSearchParams(params);
    next.set("target", targetId);
    if (runId) next.set("run", runId);
    else next.delete("run");
    setParams(next);
  };
  const close = () => {
    const next = new URLSearchParams(params);
    next.delete("target");
    next.delete("run");
    setParams(next);
  };
  const setMode = (m: Mode) => {
    const next = new URLSearchParams(params);
    next.set("mode", m);
    setParams(next);
  };

  const target = filtered.find((t) => t.id === selectedTarget)
    ?? targets.find((t) => t.id === selectedTarget)
    ?? null;

  return (
    <div style={{ display: "grid", gridTemplateRows: "auto minmax(0,1fr)", height: "100%" }}>
      <div
        style={{
          display: "flex",
          gap: 14,
          alignItems: "center",
          padding: "14px 22px",
          borderBottom: "1px solid var(--line)",
          background: "var(--bg-sunken)",
          flexWrap: "wrap",
        }}
      >
        <div
          role="tablist"
          aria-label="Library mode"
          style={{ display: hideModeTabs ? "none" : "flex", gap: 6 }}
        >
          <ModeTab on={mode === "archive"} onClick={() => setMode("archive")}>
            Run archive
          </ModeTab>
          <ModeTab on={mode === "holdings"} onClick={() => setMode("holdings")}>
            Holdings
            <span className="meta" style={{ marginLeft: 8 }}>
              {holdings.present ? holdings.scrolls.length : "—"}
            </span>
          </ModeTab>
          <ModeTab on={mode === "texts"} onClick={() => setMode("texts")}>
            Recovered texts
            <span className="meta" style={{ marginLeft: 8 }}>
              {texts.length}
            </span>
          </ModeTab>
        </div>

        <div style={{ flex: 1, minWidth: 12 }} />

        {
}
        {mode === "holdings" ? null : (
          <>
        <label
          style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 240 }}
          title="search by scroll, volume or run id"
        >
          <Search size={16} aria-hidden style={{ color: "var(--ink-faint)" }} />
          <span className="visually-hidden">Search</span>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="scroll, volume or run id"
            className="interactive"
            style={{
              flex: 1,
              background: "var(--bg-raised)",
              border: "1px solid var(--line-strong)",
              borderRadius: "var(--radius-sm)",
              color: "var(--ink)",
              padding: "7px 10px",
              font: "inherit",
            }}
          />
        </label>

        <Filter size={16} aria-hidden style={{ color: "var(--ink-faint)" }} />
        <Select label="Collection" value={fCollection} onChange={setFCollection}
          options={[["", "any"], ...collectionOptions(targets)]} />
        <Select label="Certified" value={fCert} onChange={setFCert}
          options={[["", "any"], ["CERTIFIED_SURFACE", "surface"],
            ["CERTIFIED_2D", "2D"], ["CERTIFIED_INK_CANDIDATE", "ink candidate"],
            ["NONE", "nothing certified"]]} />
        <Select label="State" value={fState} onChange={setFState}
          options={[["", "any"], ["COMPLETE", "complete"], ["RUNNING", "running"],
            ["REFUSED", "refused"], ["STALLED", "stalled"], ["UNKNOWN", "unknown"]]} />
        <Select label="Seal" value={fSeal} onChange={setFSeal}
          options={[["", "any"], ["sealed", "sealed"], ["open", "unsealed"]]} />
        <Select label="Sort" value={sort} onChange={(v) => setSort(v as SortKey)}
          options={[["target", "by target"], ["newest", "newest"], ["status", "by status"],
            ["certification", "by certification"]]} />
        </>
        )}
      </div>

      {mode === "archive" ? (
        <div className="ops-note archive-signal-note" data-control="archive.progress-signalling">
          Progress signalling: {runs.filter((r) => ["RUNNING", "PENDING"].includes(r.operational_state)).length} active run(s),{" "}
          {runs.filter((r) => ["RUNNING", "PENDING"].includes(r.operational_state) && r.progress.present).length} of{" "}
          {runs.filter((r) => ["RUNNING", "PENDING"].includes(r.operational_state)).length} publish a heartbeat.
          {runs.filter((r) => ["RUNNING", "PENDING"].includes(r.operational_state)).length === 0
            ? " Idle board, not a silent one."
            : " Silent runs publish no progress signal."}
        </div>
      ) : null}

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr)", minHeight: 0 }}>
        {mode === "texts" ? (
          <RecoveredTexts texts={texts} loaded={loaded} />
        ) : mode === "holdings" ? (
          <Holdings view={holdings} settled={rec.settled} />
        ) : (
          <div style={{ overflow: "auto", padding: "22px 22px 48px" }}>
            {
}
            <div style={{ marginBottom: 26 }}>
              <PublicRuns control="archive.public-runs" expanded />
            </div>
            {bookcases.length === 0 ? (
              <div className="panel" style={{ padding: 22 }}>
                <div className="muted">
                  {loaded
                    ? "Nothing matches these filters."
                    : "Still reading the run feed — this is not yet a statement about what exists."}
                </div>
              </div>
            ) : null}
            {bookcases.map((bc) => (
              <section key={bc.label} style={{ marginBottom: 34 }}>
                <div
                  style={{
                    display: "flex",
                    alignItems: "baseline",
                    gap: 12,
                    marginBottom: 12,
                  }}
                >
                  <h2 className="display" style={{ fontSize: "var(--t-h2)" }}>
                    {bc.label}
                  </h2>
                  {bc.collection === null ? (
                    <span className="small faint">
                      no collection was declared for these targets, and one is never
                      inferred from a name
                    </span>
                  ) : (
                    <span className="meta">{bc.targets.length} targets</span>
                  )}
                </div>

                {bc.shelves.map((sh) => (
                  <Shelf
                    key={sh.shelf}
                    shelf={sh.shelf}
                    targets={sh.targets}
                    selected={selectedTarget}
                    onOpen={open}
                  />
                ))}
              </section>
            ))}
          </div>
        )}
      </div>

      {target ? (
        <Dossier target={target} selectedRun={selectedRun} onClose={close} onPickRun={open} />
      ) : null}
    </div>
  );
}


function Shelf({
  shelf,
  targets,
  selected,
  onOpen,
}: {
  shelf: string;
  targets: TargetGroup[];
  selected: string | null;
  onOpen: (id: string) => void;
}) {
  return (
    <div style={{ marginBottom: 22 }}>
      <div
        className="eyebrow"
        style={{ marginBottom: 8, display: "flex", alignItems: "center", gap: 8 }}
      >
        <Layers size={13} aria-hidden />
        {shelf}
        <span className="faint" style={{ letterSpacing: 0, textTransform: "none" }}>
          · {targets.length} {targets.length === 1 ? "scroll" : "scrolls"}
        </span>
      </div>

      {}
      <div
        style={{
          display: "flex",
          gap: 14,
          flexWrap: "wrap",
          padding: "18px 18px 0",
          background:
            "linear-gradient(180deg, var(--bg-sunken) 0%, var(--bg-raised) 100%)",
          border: "1px solid var(--line)",
          borderRadius: "var(--radius)",
          borderBottom: "3px solid var(--accent)",
        }}
      >
        {targets.map((t) => (
          <ScrollCase
            key={t.id}
            t={t}
            selected={selected === t.id}
            onOpen={() => onOpen(t.id)}
          />
        ))}
        <div style={{ height: 18, flexBasis: "100%" }} />
      </div>
    </div>
  );
}

function ScrollCase({
  t,
  selected,
  onOpen,
}: {
  t: TargetGroup;
  selected: boolean;
  onOpen: () => void;
}) {
  const tone = operationalTone(t.state, t.sealed);
  return (
    <button
      onClick={onOpen}
      data-control={`library.target.${t.scroll ?? t.volumeId ?? "unknown"}`}
      aria-expanded={selected}
      className="interactive scroll-case"
      style={{
        width: 128,
        minHeight: 210,
        display: "grid",
        alignContent: "start",
        gap: 10,
        padding: "14px 12px",
        textAlign: "left",
        borderRadius: "10px 10px 4px 4px",
        border: "1px solid var(--line-strong)",
        background:
          "linear-gradient(180deg, var(--bg-raised-2) 0%, var(--bg-raised) 62%, var(--bg-sunken) 100%)",
        boxShadow: selected ? "var(--shadow-2)" : "var(--shadow-1)",
        transform: selected ? "translateY(-10px)" : "none",
        outline: t.sealed ? "2px solid var(--status-active-edge)" : "none",
        outlineOffset: t.sealed ? "-2px" : undefined,
      }}
    >
      <ScrollText size={18} strokeWidth={1.5} aria-hidden style={{ color: "var(--accent)" }} />
      <div
        className="display"
        style={{ fontSize: "var(--t-small)", lineHeight: 1.25, wordBreak: "break-word" }}
      >
        {t.scroll ?? "Uncatalogued"}
      </div>
      <div className="meta" style={{ lineHeight: 1.6 }}>
        {t.volumeId ?? "no volume recorded"}
        <br />
        {t.runs.length} {t.runs.length === 1 ? "run" : "runs"}
      </div>
      <Chip tone={tone} size="sm">
        {t.sealed ? "sealed" : t.state}
      </Chip>
      <div className="small faint" style={{ lineHeight: 1.4 }}>
        {t.highestCertified ? CERT_LABEL[t.highestCertified] : "nothing certified"}
      </div>
      {t.sealed ? (
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <Lock size={12} aria-hidden style={{ color: "var(--status-active)" }} />
          <span className="meta" style={{ color: "var(--status-active)" }}>
            under seal
          </span>
        </div>
      ) : null}
    </button>
  );
}


function Dossier({
  target,
  selectedRun,
  onClose,
  onPickRun,
}: {
  target: TargetGroup;
  selectedRun: string | null;
  onClose: () => void;
  onPickRun: (targetId: string, runId: string) => void;
}) {
  const ref = useRef<HTMLDivElement | null>(null);

  useFocusTrap(ref);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const run = target.runs.find((r) => r.run_id === selectedRun) ?? target.runs[0] ?? null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Dossier for ${target.scroll ?? "uncatalogued target"}`}
      tabIndex={-1}
      ref={ref}
      className="dossier argus-drawer"
    >
      <header
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          padding: "16px 18px",
          borderBottom: "1px solid var(--line)",
          background: "var(--bg-sunken)",
        }}
      >
        <ScrollText size={18} aria-hidden style={{ color: "var(--accent)" }} />
        <div style={{ minWidth: 0 }}>
          <h2 style={{ fontSize: "var(--t-h2)" }}>{target.scroll ?? "Uncatalogued"}</h2>
          <div className="meta">
            {target.acquisitionFamily ?? "acquisition not recorded"} ·{" "}
            {target.volumeId ?? "no volume id"}
          </div>
        </div>
        <div style={{ flex: 1 }} />
        {target.sealed ? <Chip tone="active">Sealed</Chip> : null}
        <button
          onClick={onClose}
          aria-label="Close dossier"
          className="interactive"
          style={{
            background: "transparent",
            border: "1px solid var(--line-strong)",
            borderRadius: "var(--radius-sm)",
            color: "var(--ink-dim)",
            padding: 7,
            display: "grid",
            placeItems: "center",
          }}
        >
          <X size={15} aria-hidden />
        </button>
      </header>

      <div style={{ overflow: "auto", padding: 18, display: "grid", gap: 16 }}>
        <section style={{ display: "grid", gap: 8 }}>
          <h3 className="eyebrow">Run history · {target.runs.length}</h3>
          <div style={{ display: "grid", gap: 6 }}>
            {target.runs.map((r) => (
              <button
                key={r.run_id}
                onClick={() => onPickRun(target.id, r.run_id)}
                className="panel interactive"
                aria-current={r.run_id === run?.run_id ? "true" : undefined}
                style={{
                  display: "grid",
                  gridTemplateColumns: "minmax(0,1fr) auto",
                  gap: 10,
                  alignItems: "center",
                  padding: "10px 12px",
                  textAlign: "left",
                  borderLeft: `3px solid var(--status-${operationalTone(
                    r.operational_state,
                    r.blinding.sealed,
                  )})`,
                  background:
                    r.run_id === run?.run_id ? "var(--bg-raised-2)" : "var(--bg-raised)",
                }}
              >
                <span style={{ minWidth: 0 }}>
                  <span className="mono" style={{ display: "block" }}>
                    {r.run_id}
                  </span>
                  <span className="small faint">
                    {r.stage ?? "no stage recorded"}
                  </span>
                </span>
                <Chip
                  tone={operationalTone(r.operational_state, r.blinding.sealed)}
                  size="sm"
                >
                  {r.blinding.sealed ? "sealed" : r.operational_state}
                </Chip>
              </button>
            ))}
          </div>
        </section>

        {run ? <RunDossier run={run} targetId={target.id} /> : null}
      </div>
    </div>
  );
}

function RunDossier({ run, targetId }: { run: RunRecord; targetId: string }) {
  const geom = run.geometry ?? run.geometry_refusal;
  const sealed = run.blinding.sealed;
  return (
    <>
      <section className="panel" style={{ padding: 14, display: "grid", gap: 10 }}>
        <h3 className="eyebrow">Run · {run.run_id}</h3>
        <CertLadder highest={run.highest_certified_stage} />
        <ProgressReadout p={run.progress} />
        {sealed ? (
          <div className="small muted">
            <Chip tone="active" size="sm">
              Under seal
            </Chip>{" "}
            Marker <span className="mono">{run.blinding.marker}</span>. The decision on this
            run belongs to that experiment&rsquo;s own controller and is taken once. No
            score, verdict, candidate count or preview from it reaches this client.
          </div>
        ) : null}
      </section>

      {!sealed && geom ? (
        <section className="panel" style={{ padding: 14, display: "grid", gap: 8 }}>
          <h3 className="eyebrow">Geometry</h3>
          {
}
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
          {geom.sheet_following === null || geom.sheet_following === undefined ? (
            <span className="small faint">
              Whether this mesh follows the sheet was not measured. This is neither a pass nor
              a failure.
            </span>
          ) : null}
          <dl style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "4px 14px", margin: 0 }}>
            <Row k="inside volume" v={pct(geom.vertices_inside_volume)} />
            <Row k="jump fraction" v={pct(geom.jump_fraction, 3)} />
            <Row k="max / median edge" v={num(geom.step_max_ratio, 1, "×")} />
            <Row k="median edge" v={num(geom.step_median_vox, 1, " vox")} />
            <Row k="edges measured" v={geom.n_edges ?? "—"} />
            <Row k="coverage" v={pct(geom.coverage)} />
            <Row k="chunks sampled" v={geom.chunks_sampled ?? "—"} />
          </dl>
        </section>
      ) : null}

      {!sealed && run.mesh_sha256 ? (
        <section className="panel" style={{ padding: 14, display: "grid", gap: 6 }}>
          <h3 className="eyebrow">Mesh identity</h3>
          {Object.entries(run.mesh_sha256).map(([k, v]) => (
            <div key={k} className="meta">
              <span className="faint">{k} </span>
              {v.slice(0, 16)}…
            </div>
          ))}
        </section>
      ) : null}

      {!sealed && run.detector ? (
        <section className="panel" style={{ padding: 14, display: "grid", gap: 6 }}>
          <h3 className="eyebrow">Detector</h3>
          <div className="small">
            {run.detector.name ?? "unnamed"}{" "}
            <span className="meta">{run.detector.checkpoint_sha256_16 ?? ""}</span>
          </div>
          <div className="small faint">
            {run.detector.qualified_family
              ? `qualified at ${run.detector.qualified_family}`
              : (run.detector.qualified_family_note ?? "no qualified acquisition family")}
          </div>
        </section>
      ) : null}

      {run.refusal_class ? (
        <section
          className="panel"
          style={{ padding: 14, borderColor: "var(--status-refused-edge)" }}
        >
          <Chip tone="refused" size="sm">
            {run.refusal_class}
          </Chip>
          <div className="small" style={{ marginTop: 8 }}>
            {run.refusal_reason}
          </div>
        </section>
      ) : null}

      <section className="panel" style={{ padding: 14, display: "grid", gap: 8 }}>
        <h3 className="eyebrow">Artifacts · {run.artifacts.length}</h3>
        {run.artifacts.length === 0 ? (
          <div className="small faint">no artifacts recorded</div>
        ) : (
          run.artifacts.slice(0, 8).map((a) => (
            <div key={a.relpath} className="meta" style={{ wordBreak: "break-all" }}>
              {a.relpath} {a.path === null ? "· withheld" : ""}
            </div>
          ))
        )}
      </section>

      <nav style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {runDestinations(run.run_id, targetId)
          .filter((d) => d.key !== "library")
          .map((d) => (
            <Link
              key={d.key}
              to={d.to}
              className="panel interactive"
              style={{
                padding: "9px 14px",
                textDecoration: "none",
                color: "var(--ink)",
                display: "inline-flex",
                alignItems: "center",
                gap: 8,
              }}
            >
              {d.label}
              <ChevronRight size={14} aria-hidden />
            </Link>
          ))}
      </nav>
      <p className="small faint" style={{ margin: 0 }}>
        These are navigation only. Nothing in this interface can start, rerun or alter work.
      </p>
    </>
  );
}


function RecoveredTexts({ texts, loaded }: { texts: RunRecord[]; loaded: boolean }) {
  if (!loaded) {
    return (
      <div style={{ padding: "22px 22px 48px" }}>
        <div className="panel" style={{ padding: 26, maxWidth: 720 }}>
          <h2 style={{ fontSize: "var(--t-h2)" }}>Reading the run feed…</h2>
          <p className="muted" style={{ margin: 0 }}>
            Nothing on this shelf is known yet. This is not a result.
          </p>
        </div>
      </div>
    );
  }
  if (texts.length === 0) {
    return (
      <div style={{ padding: "22px 22px 48px" }}>
        <div className="panel" style={{ padding: 26, maxWidth: 720, display: "grid", gap: 12 }}>
          <h2 style={{ fontSize: "var(--t-h2)" }}>No recovered texts</h2>
          <p className="muted" style={{ margin: 0 }}>
            This shelf holds only results certified as an ink candidate. No run has reached
            that rung, so it is empty — which is a statement about our results, not about
            the scrolls.
          </p>
          <p className="small faint" style={{ margin: 0 }}>
            It will not show a rendering, a reconstruction or a sample of writing before a
            real certified artifact exists. Simulated text in an archive of recovered text
            is the one thing this room can never contain.
          </p>
        </div>
      </div>
    );
  }
  return (
    <div style={{ padding: "22px 22px 48px", display: "grid", gap: 14 }}>
      {texts.map((r) => (
        <Link
          key={r.run_id}
          to={routes.evidence(r.run_id)}
          className="panel interactive"
          style={{ padding: 16, textDecoration: "none", color: "inherit" }}
        >
          <Chip tone="certified" size="sm">
            Ink candidate
          </Chip>
          <div className="display" style={{ marginTop: 8 }}>
            {r.target ?? r.run_id}
          </div>
        </Link>
      ))}
    </div>
  );
}


function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <>
      <dt className="small faint">{k}</dt>
      <dd className="mono" style={{ margin: 0 }}>
        {v}
      </dd>
    </>
  );
}

function pct(v: number | null | undefined, dp = 1) {
  return v === null || v === undefined ? "—" : `${(v * 100).toFixed(dp)}%`;
}
function num(v: number | null | undefined, dp = 1, suffix = "") {
  return v === null || v === undefined ? "—" : `${v.toFixed(dp)}${suffix}`;
}

function ModeTab({
  on,
  onClick,
  children,
}: {
  on: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      role="tab"
      aria-selected={on}
      onClick={onClick}
      className="interactive"
      style={{
        padding: "8px 14px",
        borderRadius: "var(--radius-sm)",
        border: "1px solid " + (on ? "var(--accent)" : "var(--line-strong)"),
        background: on ? "var(--bg-raised-2)" : "transparent",
        color: on ? "var(--ink)" : "var(--ink-dim)",
      }}
    >
      {children}
    </button>
  );
}

function Select({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: [string, string][];
}) {
  return (
    <label style={{ display: "grid", gap: 3 }}>
      {
}
      <span className="eyebrow">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="interactive"
        style={{
          background: "var(--bg-raised)",
          border: "1px solid var(--line-strong)",
          borderRadius: "var(--radius-sm)",
          color: "var(--ink)",
          padding: "6px 8px",
          font: "inherit",
          fontSize: "var(--t-small)",
        }}
      >
        {options.map(([v, l]) => (
          <option key={v} value={v}>
            {l}
          </option>
        ))}
      </select>
    </label>
  );
}

function collectionOptions(targets: TargetGroup[]): [string, string][] {
  const set = new Set<string>();
  for (const t of targets) set.add(t.collection ?? UNCATALOGUED);
  return [...set].map((c) => [c, c === UNCATALOGUED ? "uncatalogued" : c]);
}

function applyFilters(
  targets: TargetGroup[],
  o: {
    q: string;
    fCollection: string;
    fCert: string;
    fState: string;
    fSeal: string;
    sort: SortKey;
  },
): TargetGroup[] {
  const q = o.q.trim().toLowerCase();
  let out = targets.filter((t) => {
    if (o.fCollection && (t.collection ?? UNCATALOGUED) !== o.fCollection) return false;
    if (o.fSeal === "sealed" && !t.sealed) return false;
    if (o.fSeal === "open" && t.sealed) return false;
    if (o.fCert) {
      if (o.fCert === "NONE") {
        if (t.highestCertified !== null) return false;
      } else if (t.highestCertified !== o.fCert) return false;
    }
    if (o.fState && !t.runs.some((r) => r.operational_state === o.fState)) return false;
    if (!q) return true;
    const hay = [
      t.scroll ?? "",
      t.volumeId ?? "",
      t.acquisitionFamily ?? "",
      ...t.runs.map((r) => r.run_id),
      ...t.runs.map((r) => r.target ?? ""),
    ]
      .join(" ")
      .toLowerCase();
    return hay.includes(q);
  });

  const stateOrder: Operational[] = ["REFUSED", "STALLED", "UNKNOWN", "RUNNING", "PENDING", "COMPLETE"];
  out = [...out].sort((a, b) => {
    switch (o.sort) {
      case "newest":
        return newestId(b).localeCompare(newestId(a));
      case "status":
        return stateOrder.indexOf(a.state) - stateOrder.indexOf(b.state);
      case "certification":
        return rankCertified(b.highestCertified as Certified) -
          rankCertified(a.highestCertified as Certified);
      default:
        return (a.scroll ?? "￿").localeCompare(b.scroll ?? "￿");
    }
  });
  return out;
}

function newestId(t: TargetGroup) {
  return t.runs.map((r) => r.run_id).sort().at(-1) ?? "";
}
