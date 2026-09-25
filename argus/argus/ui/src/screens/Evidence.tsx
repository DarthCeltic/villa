import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { Artifact, RunRecord } from "../api";
import { api } from "../api";
import { usePoll } from "../lib/poll";
import { failureLine } from "../lib/http";
import { useCapabilityGraph, useGates, type CapabilityRow } from "../lib/argusTruth";
import { useIntegrity, certificationIsSuppressed } from "../lib/systemTruth";
import { runCertification } from "../lib/certification";
import { fetchReceipts, type ReceiptEnvelope, type ReceiptsState } from "../lib/receipts";
import { PublicRuns } from "../components/evidence/PublicRunReceipt";
import type { PublicRun } from "../lib/publicRuns";
import { contextIsCoherent, requireScroll, useArgusContext } from "../lib/context";
import { scrollOfTarget } from "../lib/unrollScroll";
import { useEffect } from "react";
import "../theme/ops.css";
import "../theme/evidence.css";
import {
  OpsBadge,
  OpsButton,
  OpsDetails,
  OpsFact,
  OpsFacts,
  OpsFixture,
  OpsItem,
  OpsPage,
  OpsQuote,
  OpsSection,
  OpsSource,
  OpsStat,
  TechnicalDetails,
  OpsUnknown,
  agoLabel,
  bytesLabel,
  type OpsTone,
} from "../components/OpsKit";
import { useSharedUniverse } from "../lib/sharedUniverse";
import { GovernedAction } from "../components/GovernedAction";
import { useStacks } from "../components/grail/useGrailData";
import {
  EvidenceGallery,
  EvidenceHash,
  EvidencePager,
} from "../components/evidence/EvidenceFront";
import { PacketPanel } from "../components/evidence/PacketPanel";
import { ControlEvidenceKind } from "../components/ControlEvidenceKind";
import { stageAnchorId } from "../components/PipelineStrip";
import { ScrollContextPicker } from "../components/ScrollContextPicker";
import { ScrollStatusBar } from "../components/ScrollStatusBar";
import { SurfaceStatusChips, SurfaceStatusDetail, useSurfaceStatus } from "../components/SurfaceStatusFacts";


const STAGES = [
  {
    id: "PIPELINE_OPERATIONAL",
    plain: "The pipeline runs",
    means:
      "raw CT enters and a coordinate-bound 2D ink map comes out. Established by controls; it " +
      "needs no labels at all, and it says nothing about whether anything in the map is ink.",
  },
  {
    id: "INTERNALLY_CORROBORATED_CANDIDATE",
    plain: "A candidate is mechanically real",
    means:
      "the apparent text survives independent, preregistered, LABEL-FREE tests — nulls, " +
      "wrong-axis, wrong-scale, geometry-only comparators, renderer agreement, coordinate " +
      "round-trips. Reproducible and not a pipeline artefact. Still not a reading.",
  },
  {
    id: "EXTERNALLY_VALIDATED",
    plain: "A person or a known label confirms it",
    means:
      "a human transcription, a published label or an independent expert confirms the reading. " +
      "Nothing constructed or transformed reaches here, ever.",
  },
] as const;


interface SurfacesPayload {
  surfaces: {
    public_release?: {
      state: string;
      headline: string;
      tracked_files?: number;
      publishable?: number;
      withheld?: number;
      unclassified?: number;
      by_class?: Record<string, number>;
      fail_closed?: string;
      publishing_is_not_automatic?: string;
    };
  };
}


export function Evidence({
  run,
  requestedId,
  loaded,
  runs,
}: {
  run?: RunRecord | null;
  requestedId?: string | null;
  loaded?: boolean;
  runs?: RunRecord[];
}) {
  const surfaces = usePoll<SurfacesPayload>("/api/surfaces", { intervalMs: 120000 });
  const cg = useCapabilityGraph(60000);
  const gates = useGates(60000);
  const integrity = useIntegrity(60000);
  const [rec, setRec] = useState<ReceiptsState>({ index: null, failure: null, settled: false });
  useEffect(() => {
    let live = true;
    fetchReceipts().then((s) => live && setRec(s));
    return () => {
      live = false;
    };
  }, []);

  const release = surfaces.data?.surfaces?.public_release ?? null;
  const suppressed = certificationIsSuppressed(integrity.data);

  const { ctx } = useArgusContext();
  const allRuns = runs ?? [];
  const queryRun = !run && !requestedId && ctx.run
    ? (allRuns.find((r) => r.run_id === ctx.run) ?? null)
    : null;
  const activeRun = run ?? queryRun;
  const runScroll = activeRun?.target ? scrollOfTarget({ name: activeRun.target }) : null;
  const scroll = ctx.scroll ?? runScroll;
  const coherence = contextIsCoherent({ ...ctx, run: activeRun?.run_id ?? null }, runScroll);
  const universe = useSharedUniverse();
  const scrollObj = scroll ? (universe.byId.get(scroll) ?? null) : null;
  const scrollLabel = scrollObj?.display ?? scroll;
  const stacks = useStacks();
  const { status: surfaceStatus } = useSurfaceStatus(scroll);

  const title = scroll
    ? `Evidence for ${scrollLabel}${activeRun ? ` · run ${activeRun.run_id}` : ""}`
    : activeRun
      ? `Evidence for run ${activeRun.run_id}`
      : "Evidence — no scroll selected";

  return (
    <OpsPage control="evidence" title={title}>
      <ScrollStatusBar control="evidence.scroll.status" />
      {!scroll ? (
        <div className="ev-context" data-control="evidence.context.none">
          {
}
          <span title={requireScroll(ctx).why}>
            No scroll is selected, and this page will not pick one. Showing every receipt-backed
            image and the most recent receipts.
          </span>
          <ScrollContextPicker
            control="evidence.context.picker"
            buttonClassName="ops-btn"
            buttonLabel="Choose a scroll"
          />
        </div>
      ) : !ctx.scroll && runScroll ? (
        <p className="ev-context" data-control="evidence.context.fromrun">
          The scroll {runScroll} is taken from the open run's declared target, not chosen for you.
        </p>
      ) : null}
      {!coherence.coherent ? (
        <p className="ev-fail" role="status" data-control="evidence.context.incoherent">
          {coherence.why}
        </p>
      ) : null}
      {ctx.run && !run && !requestedId && !queryRun ? (
        <p className="ev-none" data-control="evidence.context.runmissing">
          The link names run {ctx.run}, and the feed{" "}
          {loaded ? "has no run with that id" : "has not delivered its first snapshot yet"}.
          Nothing is substituted for it.
        </p>
      ) : null}

      <GateStrip
        surfaces={surfaces}
        suppressed={suppressed}
        integrityFailed={Boolean(integrity.failure) && !integrity.data}
      />

      <ControlEvidenceKind where="evidence" />

      {surfaceStatus ? (
        <OpsSection
          control="evidence.surfaceStatus"
          title="Status — every fact on its own, sourced"
          hint="Bytes present, a render existing, geometric admissibility and prize eligibility are different claims with different failure modes, from argus.core.surface_status, and are never merged into one badge."
          aside={<SurfaceStatusChips status={surfaceStatus} size="sm" />}
        >
          <SurfaceStatusDetail status={surfaceStatus} />
        </OpsSection>
      ) : null}

      <EvidenceGallery scroll={scroll} scrollLabel={scrollLabel} stacks={stacks} runs={allRuns} />

      {activeRun ? <RunEvidence run={activeRun} suppressed={suppressed} /> : null}

      <ReceiptBrowser rec={rec} release={release} />

      <PublicRuns control="evidence.public-runs" />
      <IndexedRuns />

      <PacketPanel scroll={scroll} />

      {activeRun ? null : (
        <RunPicker requestedId={requestedId} loaded={loaded} runs={allRuns} scroll={scroll} />
      )}

      <div data-novice="ready">
        <StageLadder cg={cg.data} gates={gates.data} />
      </div>

      <details className="ops-details ev-more" data-control="evidence.more">
        <summary data-control="evidence.more.toggle">
          Capability axes and every claim with what would falsify it
        </summary>
        <div className="ops-details-body">
          <Axes cg={cg.data} />
          <Claims cg={cg.data} />
        </div>
      </details>

      <nav className="ops-controls" aria-label="Related screens">
        <Link className="ops-btn" to="/jobs" data-control="evidence.nav.jobs">
          Operations
        </Link>
        <Link className="ops-btn" to="/review" data-control="evidence.nav.review">
          Review
        </Link>
        <Link className="ops-btn" to="/sources" data-control="evidence.nav.sources">
          Sources
        </Link>
      </nav>
    </OpsPage>
  );
}


function GateStrip({
  surfaces,
  suppressed,
  integrityFailed,
}: {
  surfaces: { loading: boolean; failure: Parameters<typeof failureLine>[0] | null };
  suppressed: { suppressed: boolean; why: string | null };
  integrityFailed: boolean;
}) {
  const unread = surfaces.failure ? failureLine(surfaces.failure) : null;
  return (
    <section
      className="ev-gates"
      data-control="evidence.headline"
      data-novice="missing"
      aria-label="Standing gates"
      aria-live="polite"
    >
      <div className="ev-gates-row">
        <span className="ev-gates-label">Standing gates</span>
        {suppressed.suppressed ? (
          <OpsBadge tone="bad" control="evidence.headline.chain">
            certification suppressed
          </OpsBadge>
        ) : integrityFailed ? (
          <OpsBadge tone="idle" control="evidence.headline.chain">
            ledger chain not read
          </OpsBadge>
        ) : null}
        <OpsBadge tone="warn" control="evidence.headline.probability.chip">
          probability &gt; 0.5 is display only
        </OpsBadge>
      </div>
      <OpsDetails
        control="evidence.headline.details"
        summary="Why these gates stand, and where each is read from"
      >
        {unread ? <div className="ops-refusal">{unread}</div> : null}
        {suppressed.why ? <div className="ops-refusal">{suppressed.why}</div> : null}
        <div className="ops-refusal" data-control="evidence.headline.probability">
          A probability above 0.5 is a DISPLAY threshold and never a scientific criterion.
          Wherever a probability appears in this interface it is there so a person can look at a
          place on a surface; it does not promote anything, it satisfies no gate, and it may not
          be quoted as a finding.
        </div>
        <OpsSource route="/api/integrity" field="chain.status, .at, .why" />
        </OpsDetails>
    </section>
  );
}


function StageLadder({
  cg,
  gates,
}: {
  cg: ReturnType<typeof useCapabilityGraph>["data"];
  gates: ReturnType<typeof useGates>["data"];
}) {
  const operational = cg
    ? cg.counts.control_passed > 0 && cg.route.first_blocked_stage === null
    : null;
  const candidateGate = (gates?.gates ?? []).find((g) => g.gate === "candidate") ?? null;
  const admissible = cg ? cg.counts.scientifically_admissible : null;

  const reached: Record<string, { tone: OpsTone; state: string; why: string; source: string }> = {
    PIPELINE_OPERATIONAL: {
      tone: operational === null ? "idle" : operational ? "info" : "warn",
      state:
        operational === null
          ? "not read"
          : operational
            ? "reached"
            : "not reached",
      why: cg
        ? `${cg.counts.control_passed} of ${cg.counts.total} capabilities pass their mechanical controls, and the route reports ${cg.route.first_blocked_stage ?? "no"} blocked stage.`
        : "the capability graph has not answered, so nothing is claimed about this stage.",
      source: "/api/capability_graph → counts.control_passed, route.first_blocked_stage",
    },
    INTERNALLY_CORROBORATED_CANDIDATE: {
      tone: candidateGate
        ? candidateGate.state === "OPEN"
          ? "info"
          : "bad"
        : "idle",
      state: candidateGate
        ? candidateGate.state === "OPEN"
          ? "reached"
          : "not reached"
        : "not read",
      why: candidateGate
        ? candidateGate.why
        : "the gate ladder has not answered, so nothing is claimed about this stage.",
      source: "/api/gates → gates[gate=candidate]",
    },
    EXTERNALLY_VALIDATED: {
      tone: admissible === null ? "idle" : admissible > 0 ? "ok" : "bad",
      state:
        admissible === null ? "not read" : admissible > 0 ? "reached" : "not reached",
      why: cg
        ? `${cg.counts.scientifically_admissible} of ${cg.counts.scientific_capabilities} scientific capabilities are admissible. No route reports an external transcription or an independent expert confirmation, and this rung requires one.`
        : "the capability graph has not answered, so nothing is claimed about this stage.",
      source: "/api/capability_graph → counts.scientifically_admissible",
    },
  };

  return (
    <OpsSection
      control="evidence.stages"
      title="The three evidence stages, in order"
      hint="Drawn whole, with the upper rungs visibly empty. Showing only what was reached makes a partial result look complete, and stage 1 is not a weak stage 3 — constructed and transformed evidence may corroborate and may never externally validate."
    >
      <ol className="ops-ladder">
        {STAGES.map((s) => {
          const r = reached[s.id];
          return (
            <li key={s.id} data-tone={r?.tone ?? "idle"} data-control={`evidence.stages.${s.id}`}>
              <span className="ops-rung-n" aria-hidden />
              <div style={{ display: "grid", gap: 6, minWidth: 0 }}>
                <div className="ops-item-head">
                  <span className="ops-item-title">{s.plain}</span>
                  <OpsBadge tone={r?.tone ?? "idle"}>{r?.state ?? "not read"}</OpsBadge>
                  {
}
                  <span className="ops-mono">stage {STAGES.indexOf(s) + 1}</span>
                </div>
                <div className="ops-item-body">{s.means}</div>
                <div className="ops-item-body">
                  <strong>Here: </strong>
                  {r?.why}
                </div>
                {r ? <TechnicalDetails text={r.source} /> : null}
              </div>
            </li>
          );
        })}
      </ol>
      <div className="ops-note">
        The stage names are the contract's own, from{" "}
        <span className="ops-mono">argus.core.scrollbench</span>. No read-only route publishes a
        per-claim stage assignment, so this ladder reports membership only from the two routes
        that speak to it and leaves the rest unasserted rather than inferring downward.
      </div>
    </OpsSection>
  );
}


function Axes({ cg }: { cg: ReturnType<typeof useCapabilityGraph>["data"] }) {
  return (
    <OpsSection
      control="evidence.axes"
      title="Three capability axes, never merged"
      hint="Availability, operational verification and scientific admissibility are independent. There is deliberately no combined readiness figure: an average of these three is a number that hides the one that mattered."
      aside={
        cg ? (
          <OpsBadge tone="idle" control="evidence.axes.total">
            {cg.counts.total} capabilities
          </OpsBadge>
        ) : null
      }
    >
      {!cg ? (
        <ul className="ops-list">
          <OpsUnknown
            what="The capability graph"
            why="/api/capability_graph has not answered yet. No axis is being filled in from another."
          />
        </ul>
      ) : (
        <>
          <div className="ops-stats">
            <OpsStat
              control="evidence.axes.availability"
              label="AVAILABILITY — installed on this machine"
              value={`${cg.counts.installed} / ${cg.counts.total}`}
              tone={cg.counts.installed === cg.counts.total ? "info" : "warn"}
              toneLabel={cg.counts.installed === cg.counts.total ? "all installed" : "some absent"}
              source="/api/capability_graph → counts.installed"
            />
            <OpsStat
              control="evidence.axes.operational"
              label="OPERATIONAL VERIFICATION — mechanical controls pass"
              value={`${cg.counts.control_passed} / ${cg.counts.total}`}
              tone={cg.counts.control_passed === cg.counts.total ? "info" : "warn"}
              toneLabel={cg.counts.control_passed === cg.counts.total ? "all pass" : "some untested"}
              source="/api/capability_graph → counts.control_passed"
            />
            <OpsStat
              control="evidence.axes.scientific"
              label="SCIENTIFIC ADMISSIBILITY — output may be evidence"
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
      )}
    </OpsSection>
  );
}


function claimTone(row: CapabilityRow): OpsTone {
  if (row.may_produce_evidence) return "ok";
  if (row.availability !== "INSTALLED") return "bad";
  return "warn";
}

function Claims({ cg }: { cg: ReturnType<typeof useCapabilityGraph>["data"] }) {
  const [only, setOnly] = useState<"all" | "scientific" | "not-admissible">("all");
  const rows = useMemo(() => {
    const all = cg?.capabilities ?? [];
    if (only === "scientific") return all.filter((c) => c.scientific);
    if (only === "not-admissible") return all.filter((c) => !c.may_produce_evidence);
    return all;
  }, [cg, only]);

  return (
    <OpsSection
      control="evidence.claims"
      title="Claims, and what would overturn each one"
      hint="Every row is a capability's own declaration, with the receipts it names. A row whose payload declares no falsifier says so — an unfalsifiable claim is one nobody can check, and the gap is worth more than an empty cell."
      aside={
        <div className="ops-filterbar">
          <label htmlFor="evidence-claims-filter">
            Show
            <select
              id="evidence-claims-filter"
              value={only}
              onChange={(e) => setOnly(e.target.value as typeof only)}
              data-control="evidence.claims.filter"
            >
              <option value="all">every capability</option>
              <option value="scientific">only those making a scientific claim</option>
              <option value="not-admissible">only those whose output is not evidence</option>
            </select>
          </label>
        </div>
      }
    >
      {!cg ? (
        <ul className="ops-list">
          <OpsUnknown what="The claim list" why="/api/capability_graph has not answered yet." />
        </ul>
      ) : (
        <EvidencePager<CapabilityRow>
          items={rows}
          rowKey={(c) => c.key}
          filter={(c, q) =>
            `${c.key} ${c.name} ${c.does} ${c.human_status} ${c.detail ?? ""}`.toLowerCase().includes(q)
          }
          page={8}
          noun={["claim", "claims"]}
          control="evidence.claims.list"
          placeholder="Search claims by name, status or falsifier"
          listClass="ops-list"
          itemsAreListItems
          render={(c) => (
            <OpsItem
              key={c.key}
              control={`evidence.claims.${c.key}`}
              tone={claimTone(c)}
              title={c.name}
              state={c.human_status}
              badges={
                <>
                  <OpsBadge tone={c.availability === "INSTALLED" ? "idle" : "bad"}>
                    availability: {c.availability.toLowerCase().replace(/_/g, " ")}
                  </OpsBadge>
                  <OpsBadge
                    tone={
                      c.operational_verification === "CONTROL_PASSED" ||
                      c.operational_verification === "TESTED"
                        ? "info"
                        : "warn"
                    }
                  >
                    operational: {c.operational_verification.toLowerCase().replace(/_/g, " ")}
                  </OpsBadge>
                  <OpsBadge
                    tone={c.scientific_admissibility === "ADMISSIBLE" ? "ok" : "bad"}
                  >
                    scientific: {c.scientific_admissibility.toLowerCase().replace(/_/g, " ")}
                  </OpsBadge>
                </>
              }
            >
              <OpsFacts>
                <OpsFact label="The claim">{c.does}</OpsFact>
                <OpsFact label="Evidence stage it can reach">
                  {c.scientific
                    ? c.may_produce_evidence
                      ? "its output may be evidence at the stage its receipts establish"
                      : "PIPELINE_OPERATIONAL at most — it runs, and what it produces is not yet evidence"
                    : "it makes no scientific claim, so no stage applies to it"}
                  <div className="ops-note">
                    contract's own aggregate: <span className="ops-mono">{c.aggregate}</span>
                  </div>
                </OpsFact>
                <OpsFact label="What would falsify it">
                  {c.detail
                    ? c.detail
                    : c.scientific
                      ? "the payload declares no falsifier for this capability. That is a gap in the record, not a sign that the claim is safe."
                      : "nothing: it asserts no scientific claim to falsify."}
                </OpsFact>
                <OpsFact label="Provenance">
                  {c.evidence.length === 0 ? (
                    "the capability declares no evidence receipt"
                  ) : (
                    <ul className="ops-list">
                      {c.evidence.map((e) => (
                        <li key={e.receipt} className="ops-item" data-tone={e.present ? "idle" : "warn"}>
                          <div className="ops-item-head">
                            <span className="ops-mono">{e.receipt}</span>
                            <OpsBadge tone={e.present ? "idle" : "warn"}>
                              {e.present ? "present" : "declared but absent"}
                            </OpsBadge>
                          </div>
                          <div className="ops-note">
                            <span className="ops-mono">{e.path}</span>
                            {e.sha256 ? ` · sha ${e.sha256.slice(0, 16)}…` : ""}
                            {typeof e.bytes === "number" ? ` · ${bytesLabel(e.bytes)}` : ""}
                            {e.written_utc ? ` · written ${e.written_utc}` : ""}
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                  <div className="ops-note">
                    {c.evidence_present} of {c.evidence_declared} declared receipt(s) present.
                    A declared receipt that is absent is shown as absent: a screen that omitted
                    it would teach the reader it was never part of the picture.
                  </div>
                </OpsFact>
              </OpsFacts>
            </OpsItem>
          )}
        />
      )}
      <OpsSource route="/api/capability_graph" field="capabilities[]" />
    </OpsSection>
  );
}



export interface EvidenceIndexRun {
  run_id: string;
  collection?: string | null;
  schema?: string | null;
  category?: string | null;
  terminal?: string | null;
  sealed?: boolean;
  public_run?: PublicRun | null;
}

export interface EvidenceIndexPayload {
  root_present?: boolean;
  total?: number;
  returned?: number;
  truncated?: boolean;
  runs?: EvidenceIndexRun[];
}

export function IndexedRunsView({ data, failure }: { data: EvidenceIndexPayload | null; failure: string | null }) {
  const runs = data?.runs ?? [];
  return (
    <OpsSection
      control="evidence.indexedRuns"
      title="Indexed evidence runs"
      hint="Run folders found under the declared evidence roots, from /api/evidence-index. A listing says a folder with a receipt exists and which schema it declares. It is not a verdict and not an endorsement."
      aside={data ? <OpsBadge tone="idle" control="evidence.indexedRuns.count">{data.total ?? runs.length} listed</OpsBadge> : null}
    >
      {failure ? (
        <div className="ops-note" data-control="evidence.indexedRuns.failed">
          The evidence index could not be read ({failure}). That is a failed read, not an empty archive.
        </div>
      ) : !data ? (
        <div className="ops-note">Reading the evidence index…</div>
      ) : runs.length === 0 ? (
        <div className="ops-note" data-control="evidence.indexedRuns.empty">
          No run folder is indexed under the evidence roots
          {data.root_present === false ? " (no evidence root exists on this machine)" : ""}. Nothing here is a claim that no run has happened elsewhere.
        </div>
      ) : (
        <>
          <ul className="ops-list" data-control="evidence.indexedRuns.list">
            {runs.map((r) => (
              <li key={`${r.collection ?? ""}/${r.run_id}`} data-run-id={r.run_id}>
                <span className="ops-mono">{r.run_id}</span>{" "}
                {r.sealed ? (
                  <OpsBadge tone="warn">sealed, contents withheld</OpsBadge>
                ) : (
                  <>
                    <OpsBadge tone="idle">{r.category ?? "UNCLASSIFIED"}</OpsBadge>{" "}
                    <span className="ops-mono">{r.schema ?? "no schema declared"}</span>
                    {r.terminal ? <> · terminal {r.terminal}</> : null}
                    {r.public_run?.result_class.banner ? (
                      <div className="ops-note" data-control="evidence.indexedRuns.banner">{r.public_run.result_class.banner}</div>
                    ) : null}
                  </>
                )}
              </li>
            ))}
          </ul>
          {data.truncated || (data.total ?? 0) > runs.length ? (
            <div className="ops-note">Showing {runs.length} of {data.total ?? runs.length}.</div>
          ) : null}
        </>
      )}
    </OpsSection>
  );
}

function IndexedRuns() {
  const idx = usePoll<EvidenceIndexPayload>("/api/evidence-index?limit=25", { intervalMs: 120000 });
  return <IndexedRunsView data={idx.data ?? null} failure={idx.failure ? failureLine(idx.failure) : null} />;
}


function ReceiptBrowser({
  rec,
  release,
}: {
  rec: ReceiptsState;
  release: SurfacesPayload["surfaces"]["public_release"] | null;
}) {
  const envelopes = useMemo(() => {
    const map = rec.index?.receipts ?? {};
    return Object.keys(map)
      .sort()
      .map((k) => [k, map[k]] as [string, ReceiptEnvelope | undefined])
      .filter((pair): pair is [string, ReceiptEnvelope] => Boolean(pair[1]));
  }, [rec.index]);

  const publicEvidence = release?.by_class?.PUBLIC_EVIDENCE;

  return (
    <OpsSection
      control="evidence.receipts"
      title="Declared receipts"
      hint="Each one with its path, hash, size, when it was written, and whether it is under seal. Absence is a value here: a receipt that does not exist arrives as absent with the path it would occupy, so a screen can name the stage that has not run."
      aside={
        rec.index ? (
          <>
            <OpsBadge tone="idle" control="evidence.receipts.count">
              {envelopes.length} declared
            </OpsBadge>
            <OpsBadge
              tone={rec.index.index_age_s > (rec.index.ttl_s ?? 30) ? "warn" : "idle"}
              control="evidence.receipts.age"
            >
              index read {agoLabel(rec.index.index_age_s)}
            </OpsBadge>
          </>
        ) : null
      }
    >
      <div className="ops-stats">
        <OpsStat
          control="evidence.receipts.boundary"
          label="Files classified PUBLIC_EVIDENCE by the release exporter"
          value={publicEvidence === undefined ? "unknown" : publicEvidence}
          tone="info"
          toneLabel="the publication boundary"
          source="/api/surfaces → public_release.by_class.PUBLIC_EVIDENCE"
        />
        <OpsStat
          control="evidence.receipts.publishable"
          label="Tracked files a publish would include"
          value={
            release?.publishable === undefined || release?.tracked_files === undefined
              ? "unknown"
              : `${release.publishable} / ${release.tracked_files}`
          }
          source="/api/surfaces → public_release.publishable, .tracked_files"
        />
        <OpsStat
          control="evidence.receipts.unclassified"
          label="Tracked files matching no allow rule (withheld)"
          value={release?.unclassified === undefined ? "unknown" : release.unclassified}
          tone="warn"
          toneLabel="withheld, fail-closed"
          source="/api/surfaces → public_release.unclassified"
        />
      </div>
      {release?.fail_closed ? (
        <OpsQuote cite="/api/surfaces → public_release.fail_closed">{release.fail_closed}</OpsQuote>
      ) : null}

      {rec.failure ? (
        <ul className="ops-list">
          <OpsUnknown
            what="The receipt index"
            why={`the receipts route answered ${rec.failure.status ?? "with a transport failure"}: ${rec.failure.detail}`}
            next="start the read-only service on this origin and reload."
          />
        </ul>
      ) : !rec.settled ? (
        <div className="ops-note">Reading the receipt index…</div>
      ) : (
        <>
          <div className="ops-note">
            Publishable is shown as not computable on every row. The classification lives in{" "}
            <span className="ops-mono">argus/core/release_exporter.py</span> and no read-only
            route exposes it per path. A verdict copied into this screen would be a second
            authority that drifts, so none is shown.
          </div>
          <EvidencePager<[string, ReceiptEnvelope]>
            items={envelopes}
            rowKey={(p) => p[0]}
            filter={([key, e], q) =>
              `${key} ${e.relpath} ${e.produced_by ?? ""} ${(e.produced_by_scripts ?? []).join(" ")} ${e.sha256_16 ?? ""}`
                .toLowerCase()
                .includes(q)
            }
            page={15}
            noun={["receipt", "receipts"]}
            control="evidence.receipts.table"
            placeholder="Search receipts by name, path, generator or hash"
            render={([key, e]) => <ReceiptRow k={key} e={e} />}
          />
        </>
      )}

      {envelopes.some((p) => (p[1].content_redactions ?? 0) > 0) ? (
        <div className="ops-note">
          Some receipt contents lost values on the way out — a path outside every declared root,
          or something credential-shaped, replaced by a sentinel. The count travels with each
          receipt so a reader comparing this against the file on disk knows the difference is
          deliberate rather than a bug.
        </div>
      ) : null}
      <OpsSource route="/api/receipts" field="receipts[], index_age_s, ttl_s" />
    </OpsSection>
  );
}

function ReceiptRow({ k, e }: { k: string; e: ReceiptEnvelope }) {
  return (
    <div className="ev-line" data-control={`evidence.receipts.row.${k}`}>
      <div className="ev-line-head">
        <span className="ev-line-title">{k}</span>
        <OpsBadge
          tone={e.sealed ? "info" : e.present ? "idle" : "warn"}
          control={`evidence.receipts.row.${k}.state`}
        >
          {e.sealed ? "sealed — content withheld" : e.present ? "present" : "absent"}
        </OpsBadge>
        <span className="ev-mono ev-line-when">
          {e.mtime_utc ? `written ${e.mtime_utc}` : "write time not recorded"}
        </span>
        <span className="ev-muted">{bytesLabel(e.bytes ?? null)}</span>
      </div>
      {!e.present && e.missing_reason ? <p className="ev-muted">{e.missing_reason}</p> : null}
      {e.sealed && e.withheld_reason ? <p className="ev-muted">{e.withheld_reason}</p> : null}
      <details className="ev-details" data-control={`evidence.receipts.row.${k}.provenance`}>
        <summary data-control={`evidence.receipts.row.${k}.provenance.toggle`}>
          Provenance: path, generator and hash
        </summary>
        <div className="ev-details-body">
          <dl className="ev-kv">
            <dt>Receipt path</dt>
            <dd>
              <EvidenceHash
                value={e.relpath}
                label={`path of receipt ${k}`}
                control={`evidence.receipts.row.${k}.copy.path`}
              />
            </dd>
            <dt>Root</dt>
            <dd>{e.root ?? "not declared"}</dd>
            <dt>Generator</dt>
            <dd>
              {e.produced_by ?? "not declared"}
              {(e.produced_by_scripts ?? []).length ? (
                <div className="ev-mono">{(e.produced_by_scripts ?? []).join(", ")}</div>
              ) : null}
            </dd>
            <dt>sha256, first 16 hex</dt>
            <dd>
              {e.sha256_16 ? (
                <EvidenceHash
                  value={e.sha256_16}
                  label={`sha256 prefix of receipt ${k}`}
                  control={`evidence.receipts.row.${k}.copy.sha`}
                />
              ) : (
                "not hashed"
              )}
              <div className="ev-muted">The receipts route publishes the first 16 hex digits only.</div>
            </dd>
            <dt>Publishable</dt>
            <dd>
              <OpsBadge tone="idle" control={`evidence.receipts.row.${k}.publishable`}>
                not computable here
              </OpsBadge>
            </dd>
            {(e.content_redactions ?? 0) > 0 ? (
              <>
                <dt>Redactions</dt>
                <dd>{e.content_redactions} value(s) replaced by a sentinel on the way out</dd>
              </>
            ) : null}
          </dl>
        </div>
      </details>
    </div>
  );
}


function RunEvidence({
  run,
  suppressed,
}: {
  run: RunRecord;
  suppressed: { suppressed: boolean; why: string | null };
}) {
  const [copied, setCopied] = useState(false);
  const cert = runCertification(run, {
    chainSuppressed: suppressed.suppressed,
    chainWhy: suppressed.why,
  });
  const cmd = reproduce(run);

  return (
    <>
      <OpsSection
        control="evidence.run"
        title={`Run ${run.run_id}`}
        hint="What this run's own receipts say, and precisely what they do not say."
        aside={
          <>
            <OpsBadge
              tone={
                cert.state === "SCIENTIFIC_ADMISSIBLE"
                  ? "ok"
                  : cert.state === "RUN_REFUSED"
                    ? "bad"
                    : "warn"
              }
              control="evidence.run.cert"
            >
              {cert.descriptor.word} · {cert.descriptor.axis}
            </OpsBadge>
            {run.blinding.sealed ? (
              <OpsBadge tone="info" control="evidence.run.sealed">
                sealed{run.blinding.marker ? ` by ${run.blinding.marker}` : ""}
              </OpsBadge>
            ) : null}
          </>
        }
      >
        <OpsFacts>
          <OpsFact label="Target">{run.target ?? "the run declared none"}</OpsFact>
          <OpsFact label="Operational state">{run.operational_state}</OpsFact>
          <OpsFact label="Highest rung reached">
            {run.highest_certified_stage ?? "nothing certified"}
          </OpsFact>
          <OpsFact label="Why it does not carry a green certification">
            {cert.withheldBecause ?? "it does — a scientific check passed at this rung."}
          </OpsFact>
          <OpsFact label="Stages">
            {cert.stagesTotalDeclared === 0
              ? "this run declares no stage records: it has a heartbeat and no receipt yet."
              : `${cert.stagesRun} of ${cert.stagesTotalDeclared} declared stages passed; ${cert.stagesRefused} refused.`}
          </OpsFact>
          {run.acquisition ? (
            <OpsFact label="Acquisition">
              <span className="ops-mono">
                {run.acquisition.voxel_um} µm / {run.acquisition.energy_kev} keV ·{" "}
                {run.acquisition.volume_id}
              </span>
            </OpsFact>
          ) : null}
          {run.detector ? (
            <OpsFact label="Detector declared by this run">
              <span className="ops-mono">
                {run.detector.name ?? "unnamed"}
                {run.detector.checkpoint_sha256_16
                  ? ` · ${run.detector.checkpoint_sha256_16}`
                  : ""}
              </span>
              <div className="ops-note">
                {run.detector.qualified_family
                  ? `qualified family: ${run.detector.qualified_family}`
                  : (run.detector.qualified_family_note ??
                    "no qualified family is declared for this detector")}
              </div>
            </OpsFact>
          ) : null}
        </OpsFacts>
        <div className="ops-note">{cert.descriptor.means}</div>
      </OpsSection>

      <OpsSection
        control="evidence.run.stages"
        title="Stage records"
        hint="What each stage declared it certified, what it refused, and why."
      >
        {(run.stages ?? []).length === 0 ? (
          <ul className="ops-list">
            <OpsUnknown
              what="Stage records for this run"
              why="the run has a heartbeat but no stage receipt yet. Absence is shown as absence, not as a pass."
            />
          </ul>
        ) : (
          <ul className="ops-list">
            {(run.stages ?? []).map((s, i) => (
              <OpsItem
                key={`${s.module}-${i}`}
                id={stageAnchorId(s.module)}
                control={`evidence.run.stage.${s.module}`}
                tone={s.status === "PASS" ? "info" : "bad"}
                title={s.module}
                state={s.status}
                badges={
                  s.certified ? (
                    <OpsBadge tone="info">certified {s.certified}</OpsBadge>
                  ) : null
                }
              >
                {s.refusal_reason ? (
                  <OpsFacts>
                    <OpsFact label="What failed">{s.refusal_class ?? "unclassified"}</OpsFact>
                    <OpsFact label="Why">{s.refusal_reason}</OpsFact>
                    <OpsFact label="Next action">
                      the stage record declares none. A refusal is a result; what to do about it
                      is decided on Review, with this receipt in front of the person deciding.
                    </OpsFact>
                  </OpsFacts>
                ) : null}
                {typeof s.seconds === "number" ? (
                  <div className="ops-note">took {s.seconds}s</div>
                ) : null}
              </OpsItem>
            ))}
          </ul>
        )}
      </OpsSection>

      <OpsSection
        control="evidence.run.artifacts"
        title={`Artifacts · ${run.artifacts.length}`}
        hint="Every file this run wrote, with its hash. A withheld file is named and its bytes are not served."
      >
        {run.artifacts.length === 0 ? (
          <ul className="ops-list">
            <OpsUnknown what="Artifacts" why="this run declares none." />
          </ul>
        ) : (
          <EvidencePager<Artifact>
            items={run.artifacts}
            rowKey={(a) => a.relpath}
            filter={(a, q) => `${a.relpath} ${a.sha256 ?? ""}`.toLowerCase().includes(q)}
            page={12}
            noun={["artifact", "artifacts"]}
            control="evidence.run.artifacts.table"
            placeholder="Search this run's artifacts by path or hash"
            render={(a) => <ArtifactRow a={a} runId={run.run_id} />}
          />
        )}
        <OpsDetails control="evidence.run.hashes" summary="Receipt hashes and environment">
          {Object.entries(run.hashes).length === 0 ? (
            <div className="ops-note">no hashed receipt yet</div>
          ) : (
            <OpsFacts>
              {Object.entries(run.hashes).map(([k, v]) => (
                <OpsFact key={k} label={k}>
                  <span className="ops-mono">{v}</span>
                </OpsFact>
              ))}
            </OpsFacts>
          )}
          {run.environment ? (
            <div className="ops-mono">
              python {run.environment.python ?? "unknown"} · platform{" "}
              {run.environment.platform ?? "unknown"} · argus core{" "}
              {run.environment.argus_core_sha256?.slice(0, 16) ?? "unknown"}
            </div>
          ) : null}
        </OpsDetails>
      </OpsSection>

      <OpsSection
        control="evidence.run.reproduce"
        title="Reproduce"
        hint="Reproduction goes through the same governed door every other write in this interface uses: a deliberate session, a plan, an explicit submit, an audited receipt. do_evidence_reproduce (argus/core/action_registry.py) never executes anything -- read_only:true, reversible:true, cost.gpu:none -- it only returns the exact command and the identities the run must reproduce, exactly as the raw text below always did, but now with a hash-chained audit entry proving who asked and when. The raw command remains available for provenance, not as the workflow."
      >
        <GovernedAction
          action="evidence.reproduce"
          params={{ run: run.run_id }}
          label="Prepare reproduction"
          why="re-derives this run's own recorded command and fragment identities from its PLAN.json; does not execute the pipeline."
        />
        {
}
        <OpsDetails control="evidence.run.reproduce.details" summary="Raw command (provenance only)">
          <pre className="ops-quote ops-mono" style={{ whiteSpace: "pre-wrap" }}>
            {cmd}
          </pre>
          <div className="ops-controls">
            <OpsButton
              weight="primary"
              control="evidence.run.reproduce.copy"
              onClick={() => {
                void navigator.clipboard.writeText(cmd);
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              }}
            >
              {copied ? "Copied to the clipboard" : "Copy the command"}
            </OpsButton>
          </div>
        </OpsDetails>
      </OpsSection>
    </>
  );
}

function ArtifactRow({ a, runId }: { a: Artifact; runId: string }) {
  return (
    <div className="ev-line" data-control={`evidence.run.artifact.${a.relpath}`}>
      <div className="ev-line-head">
        {a.path ? (
          <a
            href={api.fileUrl(a.path)}
            target="_blank"
            rel="noreferrer"
            className="ops-btn ev-mono"
            data-control={`evidence.run.artifact.open.${runId}.${a.relpath}`}
          >
            {a.relpath}
          </a>
        ) : (
          <>
            <span className="ev-mono">{a.relpath}</span>
            <OpsBadge tone="info">withheld · {a.withheld ?? "no reason declared"}</OpsBadge>
          </>
        )}
        <span className="ev-muted">{bytesLabel(a.bytes)}</span>
      </div>
      <details className="ev-details" data-control={`evidence.run.artifact.${a.relpath}.provenance`}>
        <summary data-control={`evidence.run.artifact.${a.relpath}.provenance.toggle`}>
          Provenance: path and sha256
        </summary>
        <div className="ev-details-body">
          <dl className="ev-kv">
            <dt>Run</dt>
            <dd className="ev-mono">{runId}</dd>
            <dt>Path</dt>
            <dd>
              <EvidenceHash
                value={a.path ?? a.relpath}
                label={`path of ${a.relpath}`}
                control={`evidence.run.artifact.${a.relpath}.copy.path`}
              />
            </dd>
            <dt>sha256</dt>
            <dd>
              {a.sha256 ? (
                <EvidenceHash
                  value={a.sha256}
                  label={`sha256 of ${a.relpath}`}
                  control={`evidence.run.artifact.${a.relpath}.copy.sha`}
                />
              ) : (
                "not hashed"
              )}
            </dd>
          </dl>
        </div>
      </details>
    </div>
  );
}

function RunPicker({
  requestedId,
  loaded,
  runs,
  scroll,
}: {
  requestedId?: string | null;
  loaded?: boolean;
  runs: RunRecord[];
  scroll: string | null;
}) {
  const [onlyScroll, setOnlyScroll] = useState(true);
  const mine = useMemo(
    () =>
      scroll ? runs.filter((r) => (r.target ? scrollOfTarget({ name: r.target }) : null) === scroll) : runs,
    [runs, scroll],
  );
  const listed = scroll && onlyScroll ? mine : runs;

  return (
    <OpsSection
      control="evidence.runs"
      title="Runs with receipts"
      hint="Open one to read its stage records, artifact hashes and reproduction command."
      aside={
        <OpsBadge tone="idle" control="evidence.runs.count">
          {runs.length} in the feed
        </OpsBadge>
      }
    >
      {requestedId ? (
        <ul className="ops-list">
          <OpsUnknown
            what={`Run ${requestedId}`}
            why={
              loaded
                ? "the feed has no run with that id. Nothing is being substituted for it."
                : "the service is building its first snapshot; this link will resolve when it arrives."
            }
          />
        </ul>
      ) : null}
      {scroll ? (
        <div className="ev-context">
          <span>
            {onlyScroll
              ? `${mine.length} of ${runs.length} runs declare a target naming ${scroll}.`
              : `Every run in the feed, ${runs.length} in all.`}
          </span>
          <button
            type="button"
            className="ops-btn"
            aria-pressed={!onlyScroll}
            data-control="evidence.runs.scope"
            onClick={() => setOnlyScroll(!onlyScroll)}
          >
            {onlyScroll ? "Show every run" : `Show only runs for ${scroll}`}
          </button>
        </div>
      ) : null}
      {runs.length === 0 ? (
        <ul className="ops-list">
          <OpsUnknown
            what="Runs"
            why="the observatory feed has delivered no run records. That is a statement about the feed, which scans fewer roots than the activity derivation does — it is not a statement about the machine."
          />
        </ul>
      ) : (
        <EvidencePager<RunRecord>
          items={listed}
          rowKey={(r) => r.run_id}
          filter={(r, q) =>
            `${r.run_id} ${r.target ?? ""} ${r.operational_state} ${r.highest_certified_stage ?? ""}`
              .toLowerCase()
              .includes(q)
          }
          page={10}
          noun={["run", "runs"]}
          control="evidence.runs.table"
          placeholder="Search runs by id, target or state"
          render={(r) => (
            <div className="ev-line" data-control={`evidence.runs.row.${r.run_id}`}>
              <div className="ev-line-head">
                <span className="ev-mono ev-line-title">{r.run_id}</span>
                {r.foreign ? <OpsFixture why="this run.json belongs to another tool" /> : null}
                <OpsBadge
                  tone={
                    r.operational_state === "REFUSED"
                      ? "bad"
                      : r.operational_state === "COMPLETE" || r.operational_state === "RUNNING"
                        ? "info"
                        : "warn"
                  }
                >
                  {r.operational_state.toLowerCase()}
                </OpsBadge>
                {r.blinding.sealed ? <OpsBadge tone="info">sealed</OpsBadge> : null}
                <Link
                  className="ops-btn"
                  to={`/evidence/${encodeURIComponent(r.run_id)}`}
                  data-control={`evidence.runs.open.${r.run_id}`}
                >
                  Open
                </Link>
              </div>
              <p className="ev-muted">
                target {r.target ?? "none declared"} · highest rung{" "}
                {r.highest_certified_stage ?? "nothing certified"}
              </p>
            </div>
          )}
        />
      )}
    </OpsSection>
  );
}

function reproduce(run: RunRecord): string {
  const stages = run.stages ?? [];
  const stop = stages.some((s) => s.module === "stage4.vigiles_decision")
    ? "decision"
    : stages.some((s) => s.module === "stage3.ink_maps")
      ? "ink"
      : stages.some((s) => s.module === "stage2.verified_render")
        ? "render"
        : "mesh";
  return [
    "python argus/run.py \\",
    `  --manifest argus/targets/<manifest for ${run.target ?? run.run_id}>.json \\`,
    `  --out ${run.run_dir} \\`,
    `  --stop-after ${stop}`,
  ].join("\n");
}

export default Evidence;
