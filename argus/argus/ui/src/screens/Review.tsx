import { useMemo, useState, type MouseEvent } from "react";
import { PrizeRouteBoard, PrizeRouteTargets } from "../components/PrizeRouteBoard";
import { Link } from "react-router-dom";
import { usePoll } from "../lib/poll";
import { ScrollFactsTable } from "../components/ScrollFacts";
import { useSharedUniverse } from "../lib/sharedUniverse";
import { useGates, type GatesPayload } from "../lib/argusTruth";
import { useIntegrity, certificationIsSuppressed, type Integrity } from "../lib/systemTruth";
import { partitionFixtures } from "../lib/fixtures";
import { scrollOfTarget } from "../lib/unrollScroll";
import { useArgusContext } from "../lib/context";
import { InterpretationPanel } from "../components/InterpretationPanel";
import { SealedReviewQueue } from "../components/SealedReviewQueue";
import { ScrollStatusBar } from "../components/ScrollStatusBar";
import type { FeedState, UnrollIndex } from "../api";
import "../theme/ops.css";
import {
  OpsBadge,
  OpsDetails,
  OpsDisabled,
  OpsFact,
  OpsFacts,
  OpsFailure,
  OpsHeadline,
  OpsItem,
  OpsPage,
  OpsQuote,
  OpsSection,
  OpsSource,
  OpsUnknown,
  type OpsTone, coordText } from "../components/OpsKit";


const REVIEW_SECTIONS: { control: string; label: string }[] = [
  { control: "review.decisions", label: "Decisions" },
  { control: "review.prizeroutes", label: "Prize routes" },
  { control: "review.authority", label: "Who may decide" },
  { control: "review.stopped", label: "Work stopped" },
  { control: "review.queue", label: "Queue" },
  { control: "review.interpretation", label: "Interpretation" },
  { control: "review.settled", label: "Settled" },
];

function ReviewJumps({ decisions }: { decisions: number }) {
  const go = (control: string) => (e: MouseEvent<HTMLAnchorElement>) => {
    const el = document.querySelector<HTMLElement>(`[data-control="${control}"]`);
    if (!el) return;
    e.preventDefault();
    el.scrollIntoView({ block: "start" });
    el.querySelector<HTMLElement>("h2")?.focus?.({ preventScroll: true });
  };
  return (
    <nav className="ops-subtabs review-jumps" aria-label="On this page" data-control="review.jumps">
      <span className="review-jumps-label">On this page</span>
      <Link className="ops-subtab" to="/review?tab=models" data-control="review.jump.models">
        Detectors
      </Link>
      {REVIEW_SECTIONS.map((s) => (
        <a
          key={s.control}
          href={`#${s.control}`}
          className="ops-subtab"
          data-control={`review.jump.${s.control.split(".")[1]}`}
          onClick={go(s.control)}
        >
          {s.label}
          {s.control === "review.decisions" ? ` · ${decisions}` : ""}
        </a>
      ))}
    </nav>
  );
}


export type Authority = "HUMAN_ONLY" | "DERIVED_MIXED" | "MACHINE_GATED" | "UNKNOWN";

const AUTHORITY: Record<Authority, { tone: OpsTone; means: string }> = {
  HUMAN_ONLY: {
    tone: "info",
    means:
      "proven, from provenance and stored bytes, to be human work. This is the only class " +
      "that may serve as a qualification witness.",
  },
  DERIVED_MIXED: {
    tone: "warn",
    means:
      "human and machine contributions exist and cannot be separated in what is stored. A " +
      "flattened raster is silent about its own origin.",
  },
  MACHINE_GATED: {
    tone: "warn",
    means:
      "a machine made or filtered the decision, whatever a person did afterwards.",
  },
  UNKNOWN: {
    tone: "bad",
    means:
      "it cannot be established from what exists. THIS IS A REFUSAL, not a presumption: it " +
      "does not mean probably-human, it defaults to nothing, and it may never be counted " +
      "toward a qualification witness.",
  },
};


interface SurfacesPayload {
  surfaces: {
    detector?: {
      state: string;
      headline: string;
      next_machine_action?: string;
      training_authorised?: boolean;
      nothing_is_authorised?: string;
    };
  };
}

interface BlockerItem {
  id: string;
  state: string;
  label?: string;
  summary: string;
  receipt?: string;
  defects?: { n: number; name: string; severity: string }[];
}

interface BlockersPayload {
  schema: string;
  items: BlockerItem[];
}

interface EstablishedPayload {
  present?: boolean;
  why?: string;
  models_learn?: boolean;
  evidence?: { claim: string; value: unknown; receipt?: string; caveat?: string }[];
  what_is_actually_open?: string[];
}

interface ResultClass {
  target?: string;
  target_class?: string;
  exposure_basis?: string;
  detector?: string;
  detector_cross_scroll_qualified?: boolean;
  acquisition?: string;
  metric?: string | null;
  score?: number | null;
  presentation?: string;
  banner?: string;
  may_claim_discovery?: boolean;
  may_claim_ink_found?: boolean;
  not_established?: string[];
}

interface ReviewTask {
  task_id: string;
  task_type: string;
  state: string;
  control?: string | null;
  may_enter_training?: boolean;
  binding?: {
    surface_or_candidate_id?: string;
    selected_because?: string;
    exposure_state?: string;
    license_state?: string;
    producing_model_or_tool?: string;
    confidence_shown_to_reviewer?: boolean;
    physical_coordinates?: Record<string, unknown>;
    source_hashes?: Record<string, string>;
  };
  permitted_answers?: string[];
  tally?: {
    total?: number;
    human_answers?: number;
    ai_answers?: number;
    agreement?: number;
    top?: string | null;
  };
  composition?: { ai_only?: boolean; disclosure?: string; by_class?: Record<string, number> };
}

interface ReviewTasksPayload {
  schema?: string;
  id?: string;
  result_class?: ResultClass;
  banner_required_on_every_surface?: string;
  counts?: { uncertainty_tasks?: number; tasks?: number; blind_controls?: number };
  controls_note?: string;
  state_note?: string;
  tasks?: ReviewTask[];
}

interface UnrollDetail {
  target?: { name?: string; key?: string };
  target_row?: { name?: string };
  result_class?: ResultClass;
  review_tasks?: ReviewTasksPayload | null;
}


export function Review({ feed }: { feed?: FeedState }) {
  const surfaces = usePoll<SurfacesPayload>("/api/surfaces", { intervalMs: 120000 });
  const blockers = usePoll<BlockersPayload>("/api/blockers", { intervalMs: 60000 });
  const established = usePoll<EstablishedPayload>("/api/established", { intervalMs: 300000 });
  const gates = useGates(30000);
  const integrity = useIntegrity(60000);

  const decisions = useMemo(
    () =>
      countDecisions({
        detector: surfaces.data?.surfaces?.detector ?? null,
        blockers: blockers.data,
        gates: gates.data,
        integrity: integrity.data,
      }),
    [surfaces.data, blockers.data, gates.data, integrity.data],
  );

  return (
    <OpsPage
      control="review"
      title="Review"
      lede={
        <>
          Only what genuinely needs a person. Each item states what is being asked, the
          evidence behind it, the options, the consequence of each option, and who may
          legitimately decide. Work that a machine can finish is not a decision and is not
          here — it is on Operations.
        </>
      }
    >
      <ScrollStatusBar control="review.scroll.status" />
      <div data-novice="missing">
        <OpsHeadline
          tone={decisions === 0 ? "ok" : "warn"}
          control="review.headline"
          what={
            decisions === 0
              ? "No item on this screen is currently waiting on a person's decision."
              : `${decisions} item${decisions === 1 ? "" : "s"} cannot proceed without a person deciding.`
          }
          detail="Each item below states its evidence, its options and who may decide."
        />
      </div>

      <ReviewJumps decisions={decisions} />

      {
}
      <OpsSection
        control="review.prizeroutes"
        title="The prize routes"
        collapsible
        hint="First Letters and Grand Prize as their frozen receipts state them. Two receipts, never one progress number, and nothing here authorises acquisition, a detector run or a submission."
      >
        {
}
        {
}
        <PrizeRouteFacts />
        <OpsDetails
          control="review.prizeroutes.boards"
          summary="Show the boards: First Letters targets, Grand Prize stage matrix, Paris 4 title requirements"
        >
          <PrizeRouteBoard />
          <PrizeRouteTargets />
        </OpsDetails>
      </OpsSection>

      <AuthorityVocabulary />

      <div data-novice="ready">
        <Decisions
          detector={surfaces.data?.surfaces?.detector ?? null}
          gates={gates.data}
          integrity={integrity.data}
        />
      </div>

      <WorkStopped blockers={blockers} />

      <Queue feed={feed} />

      <SealedReviewQueue />

      <InterpretationPanel />

      <Settled established={established.data} />

      <nav className="ops-controls" aria-label="Related screens">
        <Link className="ops-btn" to="/jobs" data-control="review.nav.jobs">
          Operations
        </Link>
        <Link className="ops-btn" to="/evidence" data-control="review.nav.evidence">
          Evidence
        </Link>
        <Link className="ops-btn" to="/sources" data-control="review.nav.sources">
          Sources
        </Link>
      </nav>
    </OpsPage>
  );
}

function countDecisions({
  detector,
  blockers,
  gates,
  integrity,
}: {
  detector: SurfacesPayload["surfaces"]["detector"] | null;
  blockers: BlockersPayload | null;
  gates: GatesPayload | null;
  integrity: Integrity | null;
}): number {
  let n = 0;
  if (detector && detector.training_authorised === false) n += 1;
  n += (blockers?.items ?? []).length;
  const shut = (gates?.gates ?? []).find((g) => g.state === "SHUT");
  if (shut?.satisfied_by) n += 1;
  if (certificationIsSuppressed(integrity).suppressed) n += 1;
  return n;
}

function AuthorityVocabulary() {
  return (
    <OpsSection
      control="review.authority"
      title="Who may decide: the four authority classes"
      collapsible
      hint="Every item on this screen is labelled with one of these. They are classes of EVIDENCE, not job titles: they say what would make a decision legitimate, not who is allowed to click."
    >
      {
}
      <OpsDetails
        control="review.authority.defs"
        summary={`What each class means: ${(Object.keys(AUTHORITY) as Authority[]).join(" · ")}`}
      >
        <ul className="ops-list">
          {(Object.keys(AUTHORITY) as Authority[]).map((a) => (
            <OpsItem
              key={a}
              control={`review.authority.${a}`}
              tone={AUTHORITY[a].tone}
              title={a}
              state={a === "UNKNOWN" ? "a refusal" : "a class"}
            >
              <div className="ops-item-body">{AUTHORITY[a].means}</div>
            </OpsItem>
          ))}
        </ul>
        <div className="ops-refusal">
          No read-only route publishes the per-scroll authority class; the module that owns that
          question is not served. So this screen names the vocabulary, and
          it will not print a class against a scroll it cannot read one for — an UNKNOWN invented
          here would be indistinguishable from an UNKNOWN that was established.
        </div>
      </OpsDetails>
    </OpsSection>
  );
}


interface Option {
  label: string;
  consequence: string;
  irreversible?: boolean;
}

function Decision({
  id,
  tone,
  ask,
  evidence,
  options,
  authority,
  who,
  children,
}: {
  id: string;
  tone: OpsTone;
  ask: string;
  evidence: React.ReactNode;
  options: Option[];
  authority: Authority;
  who: string;
  children?: React.ReactNode;
}) {
  return (
    <OpsItem
      control={`review.decision.${id}`}
      tone={tone}
      title={ask}
      badges={
        <>
          <OpsBadge tone={AUTHORITY[authority].tone} control={`review.decision.${id}.authority`}>
            {authority}
          </OpsBadge>
        </>
      }
    >
      <OpsFacts>
        <OpsFact label="The evidence">{evidence}</OpsFact>
        <OpsFact label="Who may decide">{who}</OpsFact>
      </OpsFacts>
      <div className="ops-note">The options, and what each one costs:</div>
      <ul className="ops-list">
        {options.map((o) => (
          <li key={o.label} className="ops-item" data-tone={o.irreversible ? "bad" : "idle"}>
            <div className="ops-item-head">
              <span className="ops-item-title">{o.label}</span>
              {o.irreversible ? (
                <OpsBadge tone="bad">cannot be undone</OpsBadge>
              ) : null}
            </div>
            <div className="ops-item-body">{o.consequence}</div>
          </li>
        ))}
      </ul>
      {children}
      <OpsDisabled
        control={`review.decision.${id}.record`}
        label="Record this decision"
        reason="Recording a decision is a governed write and this interface has no write route: the service it reads from has none by construction, and the command service answers 401 to a browser until an operator opens an explicit, short-lived, origin-bound session. The decision is made at that console, and this screen exists so it is made with the evidence in front of the person making it."
      />
    </OpsItem>
  );
}

function PrizeRouteFacts() {
  const universe = useSharedUniverse();
  return (
    <>
      <ScrollFactsTable universe={universe} lane="FIRST_LETTERS"
        caption="First Letters targets (Grand Prize targets overlap them)" control="review.facts.first_letters" />
      <ScrollFactsTable universe={universe} lane="PARIS4_TITLE"
        caption="Paris 4 title" control="review.facts.title" />
    </>
  );
}

function Decisions({
  detector,
  gates,
  integrity,
}: {
  detector: SurfacesPayload["surfaces"]["detector"] | null;
  gates: GatesPayload | null;
  integrity: Integrity | null;
}) {
  const shut = (gates?.gates ?? []).find((g) => g.state === "SHUT") ?? null;
  const suppressed = certificationIsSuppressed(integrity);

  return (
    <OpsSection
      control="review.decisions"
      title="Waiting on a person"
      hint="Each of these is stalled on a judgement, not on compute. Where the only honest option is to wait, that is stated rather than padded out with alternatives nobody can take."
    >
      <ul className="ops-list">
        {shut ? (
          <Decision
            id={`gate.${shut.gate}`}
            tone="bad"
            ask={`Open the ${shut.gate} gate?`}
            authority="HUMAN_ONLY"
            who="the operator. Nothing in the system is permitted to take the declared step on its own."
            evidence={
              <>
                {shut.why}
                <div className="ops-note">
                  What the ladder says would open it: {shut.satisfied_by ?? "nothing is declared"}
                </div>
                <OpsSource route="/api/gates" field={`gates[gate=${shut.gate}]`} />
              </>
            }
            options={[
              {
                label: shut.satisfied_by ?? "take the declared step",
                consequence:
                  "the holdout is spent. It may be opened exactly once, and the number it produces is the only number it will ever produce — a crashed or retried attempt does not get a second look at it.",
                irreversible: true,
              },
              {
                label: "wait",
                consequence:
                  "nothing downstream of this gate can be produced while it stays shut.",
              },
            ]}
          />
        ) : null}

        {suppressed.suppressed ? (
          <Decision
            id="integrity.chain"
            tone="bad"
            ask="What is done about the command ledger's broken hash chain?"
            authority="MACHINE_GATED"
            who="the operator, on the strength of a machine verification. The chain's state is not a judgement — it is computed — but what to do about a ledger that can no longer attest to what ran is."
            evidence={
              <>
                {suppressed.why}
                <div className="ops-note">
                  {integrity?.consequence_of_broken ??
                    "the integrity route declares no consequence"}
                </div>
                <OpsSource route="/api/integrity" field="chain.status, chain.at" />
              </>
            }
            options={[
              {
                label: "leave it broken and accept the suppression",
                consequence:
                  "no green scientific certification is displayed anywhere in this interface while the chain does not verify. Work continues; nothing downstream of the ledger may be presented as certified on its evidence.",
              },
              {
                label: "re-seal the ledger from the break onward",
                consequence:
                  "the records after the break can never again be used as evidence of what ran, because re-sealing proves only that they chain NOW. Whatever the ledger said about those runs is lost as attestation.",
                irreversible: true,
              },
            ]}
          />
        ) : null}

        {detector && detector.training_authorised === false ? (
          <Decision
            id="detector.launch"
            tone="warn"
            ask="Is any further detector training authorised?"
            authority="HUMAN_ONLY"
            who="the operator. The detector graph reports state and explicitly authorises nothing."
            evidence={
              <>
                {detector.headline}
                {detector.next_machine_action ? (
                  <div className="ops-note">
                    <strong>What the graph says the next machine action is: </strong>
                    {detector.next_machine_action}
                  </div>
                ) : null}
                <OpsSource route="/api/surfaces" field="detector" />
              </>
            }
            options={[
              {
                label: "authorise nothing",
                consequence:
                  "nothing is launched; compute stays with the work already in flight.",
              },
              {
                label: "authorise a training launch",
                consequence:
                  "compute time is committed to training and taken from whatever else would have used it.",
              },
            ]}
          />
        ) : null}

        {!shut && !suppressed.suppressed ? (
          <OpsItem
            tone="ok"
            control="review.decisions.none"
            title="No decision is outstanding in the payloads this screen reads"
            state="clear"
          >
            <div className="ops-item-body">
              That is a statement about the gate ladder and the ledger's
              integrity. It is not a promise that nothing needs deciding elsewhere.
            </div>
          </OpsItem>
        ) : null}
      </ul>
    </OpsSection>
  );
}


function WorkStopped({
  blockers,
}: {
  blockers: ReturnType<typeof usePoll<BlockersPayload>>;
}) {
  const split = useMemo(
    () => partitionFixtures(blockers.data?.items ?? []),
    [blockers.data],
  );

  return (
    <OpsSection
      control="review.stopped"
      title="Work that is stopped, and what it is waiting for"
      collapsible
      hint="Every entry is loaded from a receipt on disk. A blocker whose receipt is missing reports that rather than being described from recollection."
      aside={
        blockers.data ? (
          <OpsBadge tone={split.real.length ? "warn" : "ok"} control="review.stopped.count">
            {split.real.length} stopped
          </OpsBadge>
        ) : null
      }
    >
      {blockers.failure && !blockers.data ? (
        <ul className="ops-list">
          <OpsFailure
            what="The blocker register"
            failure={blockers.failure}
            onRetry={blockers.refresh}
            control="review.stopped"
          />
        </ul>
      ) : !blockers.data ? (
        <ul className="ops-list">
          <OpsUnknown what="The blocker register" why="/api/blockers has not answered yet." />
        </ul>
      ) : split.real.length === 0 ? (
        <ul className="ops-list">
          <OpsItem tone="ok" control="review.stopped.none" title="The register lists nothing stopped" state="clear" />
        </ul>
      ) : (
        <ul className="ops-list">
          {split.real.map((b) => (
            <OpsItem
              key={b.id}
              control={`review.stopped.${b.id}`}
              tone={b.state === "QUARANTINED" ? "bad" : "warn"}
              title={`${b.id} · ${b.label ?? b.state}`}
              state={b.state.toLowerCase()}
            >
              <OpsFacts>
                <OpsFact label="What is stopped">{b.summary}</OpsFact>
                <OpsFact label="Who may decide">
                  the operator. A disqualifying defect is not cleared by re-running the thing
                  that carried it.
                </OpsFact>
                {b.defects?.length ? (
                  <OpsFact label="Declared defects">
                    <ul className="ops-list">
                      {b.defects.map((d) => (
                        <li key={d.n} className="ops-item" data-tone={d.severity === "disqualifying" ? "bad" : "warn"}>
                          <div className="ops-item-head">
                            <span className="ops-item-title">{d.name}</span>
                            <OpsBadge tone={d.severity === "disqualifying" ? "bad" : "warn"}>
                              {d.severity}
                            </OpsBadge>
                          </div>
                        </li>
                      ))}
                    </ul>
                  </OpsFact>
                ) : null}
                {b.receipt ? (
                  <OpsFact label="Receipt">
                    <span className="ops-mono">{b.receipt}</span>
                  </OpsFact>
                ) : null}
              </OpsFacts>
            </OpsItem>
          ))}
        </ul>
      )}
      {split.fixtures.length ? (
        <div className="ops-note">
          {split.fixtures.length} fixture record hidden from this view. Fixtures are developer
          data and appear under <Link to="/system?tab=developer">System → Developer data</Link>,
          where the same judgement is shown with its reason.
        </div>
      ) : null}
      <OpsSource route="/api/blockers" field="items[]" />
    </OpsSection>
  );
}


function Queue({ feed }: { feed?: FeedState }) {
  const index = usePoll<UnrollIndex>("/api/unroll", { intervalMs: 120000 });
  const { ctx } = useArgusContext();
  const split = useMemo(() => partitionFixtures(index.data?.targets ?? []), [index.data]);
  const [openId, setOpenId] = useState<string | null>(null);

  const picked = ctx.scroll
    ? (split.real.find((t) => scrollOfTarget(t) === ctx.scroll) ?? null)
    : null;

  const detail = usePoll<UnrollDetail>(
    picked ? `/api/unroll?target=${encodeURIComponent(picked.key)}` : "/api/unroll",
    { intervalMs: 300000 },
  );

  const feedNote = feed?.connected
    ? null
    : "The observatory feed is not delivering snapshots; this queue does not depend on it.";

  if (!picked) {
    return (
      <OpsSection
        control="review.queue"
        title="Review queue"
        hint="Tasks cut from one exported surface, for one scroll at a time."
      >
        <ul className="ops-list">
          <OpsUnknown
            what="A queue to show"
            why={
              ctx.scroll
                ? `No exported layer stack matches the selected scroll (${ctx.scroll}), so there is no queue for it. Nothing is hidden — the export has not been run for that scroll.`
                : "No scroll is selected. This queue deliberately does not choose one: a review queue that silently picked its own subject would let two people answer about different scrolls with nothing saying which."
            }
            next="select a scroll, or run the layer-stack export for the one you want reviewed."
          />
        </ul>
        <OpsSource route="exported layer-stack inventory" field="targets[]" />
      </OpsSection>
    );
  }

  const rt = detail.data?.review_tasks ?? null;
  const rc = rt?.result_class ?? detail.data?.result_class ?? null;
  const tasks = rt?.tasks ?? [];

  return (
    <OpsSection
      control="review.queue"
      title={`Review queue · ${detail.data?.target?.name ?? picked.name}`}
      hint="Every task records the model's confidence so the answer stays auditable, and shows none of it, so the answer is not a measurement of suggestibility."
      aside={
        rt?.counts ? (
          <>
            <OpsBadge tone="info" control="review.queue.count">
              {rt.counts.uncertainty_tasks ?? rt.counts.tasks ?? tasks.length} task(s)
            </OpsBadge>
            <OpsBadge tone="idle" control="review.queue.controls">
              {rt.counts.blind_controls ?? 0} blind control(s) mixed in, unmarked
            </OpsBadge>
          </>
        ) : null
      }
    >
      {tasks.length > 0 ? (
        <div className="ops-note" data-control="review.queue.answer-elsewhere">
          This is a read view: what is being asked, the evidence, the options. Answering is a
          real, recorded write (<span className="ops-mono">POST /ui/review/answer</span>), and
          is deliberately not offered from this screen — a task answered away from the image it
          is about is exactly the condition under which a reviewer agrees with whatever the
          model suggested. Answer it in Workbench, beside the image, in the "Review" layout.
          <div style={{ marginTop: 6 }}>
            <Link
              className="ops-btn"
              data-weight="primary"
              to={`/workbench?scroll=${encodeURIComponent(ctx.scroll ?? "")}`}
              data-control="review.queue.gotoWorkbench"
            >
              Open {picked.name} in Workbench to answer
            </Link>
          </div>
        </div>
      ) : null}
      {rc ? (
        <>
          <OpsQuote cite="receipt-backed review-task result class">
            {rc.banner ?? "no banner is declared for this result class"}
          </OpsQuote>
          <OpsFacts>
            <OpsFact label="Target class">{rc.target_class ?? "not declared"}</OpsFact>
            <OpsFact label="Exposure basis">
              {rc.exposure_basis ?? "not declared"}
            </OpsFact>
            <OpsFact label="Detector">
              <span className="ops-mono">{rc.detector ?? "not declared"}</span>
              <div className="ops-badges">
                <OpsBadge
                  tone={rc.detector_cross_scroll_qualified ? "ok" : "bad"}
                  control="review.queue.qualified"
                >
                  {rc.detector_cross_scroll_qualified
                    ? "cross-scroll qualified"
                    : "not cross-scroll qualified"}
                </OpsBadge>
              </div>
            </OpsFact>
            <OpsFact label="Score, and what it is not">
              {rc.metric && typeof rc.score === "number"
                ? `${rc.score} (${rc.metric})`
                : "no score is declared"}
              {rc.not_established?.length ? (
                <ul className="ops-list">
                  {rc.not_established.map((n, i) => (
                    <li key={i} className="ops-item" data-tone="warn">
                      <div className="ops-item-body">not established: {n}</div>
                    </li>
                  ))}
                </ul>
              ) : null}
            </OpsFact>
            <OpsFact label="May this be called a discovery">
              <OpsBadge tone={rc.may_claim_discovery ? "ok" : "bad"}>
                {rc.may_claim_discovery ? "yes" : "no"}
              </OpsBadge>
            </OpsFact>
          </OpsFacts>
        </>
      ) : null}

      {rt?.controls_note ? <div className="ops-note">{rt.controls_note}</div> : null}
      {rt?.state_note ? <div className="ops-note">{rt.state_note}</div> : null}
      {feedNote ? <div className="ops-note">{feedNote}</div> : null}

      {tasks.length === 0 ? (
        <ul className="ops-list">
          <OpsUnknown
            what="Tasks for this surface"
            why="the export for this target carries no review tasks. An empty queue is not an answered one."
          />
        </ul>
      ) : (
        <ul className="ops-list">
          {tasks.map((t) => {
            const human = t.tally?.human_answers ?? 0;
            const ai = t.tally?.ai_answers ?? 0;
            const total = t.tally?.total ?? 0;
            const aiOnly = t.composition?.ai_only === true;
            const open = openId === t.task_id;
            return (
              <OpsItem
                key={t.task_id}
                control={`review.queue.task.${t.task_id}`}
                tone={total === 0 ? "idle" : aiOnly ? "warn" : "info"}
                title={
                  <>
                    {t.task_type.replace(/_/g, " ").toLowerCase()} ·{" "}
                    <span className="ops-mono">
                      {t.binding?.surface_or_candidate_id ?? t.task_id}
                    </span>
                  </>
                }
                state={t.state.toLowerCase()}
                badges={
                  <>
                    {
}
                    <OpsBadge tone={human > 0 ? "ok" : "idle"} control={`review.queue.task.${t.task_id}.human`}>
                      {human} human answer{human === 1 ? "" : "s"}
                    </OpsBadge>
                    <OpsBadge tone={ai > 0 ? "warn" : "idle"} control={`review.queue.task.${t.task_id}.machine`}>
                      {ai} machine answer{ai === 1 ? "" : "s"}
                    </OpsBadge>
                    {aiOnly ? (
                      <OpsBadge tone="bad">machine-answered only — not a human judgement</OpsBadge>
                    ) : null}
                  </>
                }
              >
                <OpsFacts>
                  <OpsFact label="What is being asked">
                    {t.task_type === "JUDGE_INK_CANDIDATE"
                      ? "look at this place on this surface and say whether there is ink there."
                      : t.task_type.replace(/_/g, " ").toLowerCase()}
                  </OpsFact>
                  <OpsFact label="Why this place">
                    {t.binding?.selected_because ?? "the export declares no selection reason"}
                  </OpsFact>
                  <OpsFact label="Answer composition">
                    {t.composition?.disclosure ?? `${total} answer(s) recorded`}
                  </OpsFact>
                  <OpsFact label="Who may decide">
                    a human reviewer. The model's confidence is recorded and deliberately
                    withheld from the reviewer
                    {t.binding?.confidence_shown_to_reviewer === false
                      ? " — the payload confirms it is not shown"
                      : ""}
                    , because an answer produced after seeing it measures suggestibility rather
                    than ink.
                  </OpsFact>
                  <OpsFact label="May the answer enter training">
                    <OpsBadge tone={t.may_enter_training ? "warn" : "ok"}>
                      {t.may_enter_training ? "yes" : "no"}
                    </OpsBadge>
                    {t.binding?.license_state ? (
                      <div className="ops-note">{t.binding.license_state}</div>
                    ) : null}
                  </OpsFact>
                </OpsFacts>

                <div className="ops-note">The options, and what each one means:</div>
                <ul className="ops-list">
                  {(t.permitted_answers ?? []).map((a) => (
                    <li key={a} className="ops-item" data-tone="idle">
                      <div className="ops-item-head">
                        <span className="ops-item-title">{a.replace(/_/g, " ")}</span>
                      </div>
                      <div className="ops-item-body">
                        {a === "INK"
                          ? "asserts ink is present here. One answer is not a label: the task state says every task starts PROPOSED and no single answer can promote one."
                          : a === "NOT_INK"
                            ? "asserts ink is absent here. Absence answers are what keep a queue from becoming a machine for confirming the model."
                            : "declines. A decline is a real answer and is counted; guessing to clear a queue is the failure this option exists to prevent."}
                      </div>
                    </li>
                  ))}
                </ul>

                <div className="ops-control">
                  <Link
                    className="ops-btn"
                    to={`/workbench?scroll=${encodeURIComponent(ctx.scroll ?? "")}`}
                    data-control={`review.queue.task.${t.task_id}.answer`}
                  >
                    Answer in Workbench
                  </Link>
                  <span className="ops-control-reason">
                    Answering is a real write, not offered from this screen: look for task{" "}
                    <span className="ops-mono">{t.task_id}</span> in the "Review" layout there,
                    beside the image it is actually about.
                  </span>
                </div>

                <button
                  type="button"
                  className="ops-btn"
                  onClick={() => setOpenId(open ? null : t.task_id)}
                  data-control={`review.queue.task.${t.task_id}.detail`}
                  aria-expanded={open}
                >
                  {open ? "Hide the binding" : "Show the binding"}
                </button>
                {open ? (
                  <OpsFacts>
                    <OpsFact label="Physical coordinates">
                      <span className="ops-mono">
                        {coordText(t.binding?.physical_coordinates)}
                      </span>
                    </OpsFact>
                    <OpsFact label="Producing model or tool">
                      <span className="ops-mono">
                        {t.binding?.producing_model_or_tool ?? "not declared"}
                      </span>
                    </OpsFact>
                    <OpsFact label="Exposure">
                      {t.binding?.exposure_state ?? "not declared"}
                    </OpsFact>
                    <OpsFact label="Source hashes">
                      {t.binding?.source_hashes ? (
                        <ul className="ops-list">
                          {Object.entries(t.binding.source_hashes).map(([k, v]) => (
                            <li key={k} className="ops-mono">
                              {k}: {v.slice(0, 16)}…
                            </li>
                          ))}
                        </ul>
                      ) : (
                        "none declared"
                      )}
                    </OpsFact>
                  </OpsFacts>
                ) : null}
              </OpsItem>
            );
          })}
        </ul>
      )}
      <OpsSource
        route="receipt-backed review-task result class"
        field="review_tasks.tasks[], review_tasks.result_class"
      />
    </OpsSection>
  );
}


function Settled({ established }: { established: EstablishedPayload | null }) {
  if (!established) return null;
  if (established.present === false) {
    return (
      <OpsSection control="review.settled" title="Already answered — do not re-open" collapsible>
        <ul className="ops-list">
          <OpsUnknown
            what="The established-facts record"
            why={established.why ?? "no established-facts record exists in this checkout"}
          />
        </ul>
      </OpsSection>
    );
  }
  return (
    <OpsSection
      control="review.settled"
      title="Already answered — do not re-open"
      collapsible
      hint="Settled results, each with the caveat that stops it from answering a question it does not answer."
      aside={
        <OpsBadge tone="ok" control="review.settled.count">
          {(established.evidence ?? []).length} settled
        </OpsBadge>
      }
    >
      <ul className="ops-list">
        {(established.evidence ?? []).map((e, i) => (
          <OpsItem
            key={i}
            control={`review.settled.${i}`}
            tone="ok"
            title={e.claim}
            state="settled"
          >
            <OpsFacts>
              <OpsFact label="Value">
                <span className="ops-mono">{String(e.value)}</span>
              </OpsFact>
              {e.receipt ? (
                <OpsFact label="Receipt">
                  <span className="ops-mono">{e.receipt}</span>
                </OpsFact>
              ) : null}
              {e.caveat ? (
                <OpsFact label="Caveat — the reason this does not answer the open question">
                  <span className="ops-refusal">{e.caveat}</span>
                </OpsFact>
              ) : null}
            </OpsFacts>
          </OpsItem>
        ))}
      </ul>
      {established.what_is_actually_open?.length ? (
        <OpsDetails control="review.settled.open" summary="What is actually still open">
          <ul className="ops-list">
            {established.what_is_actually_open.map((w, i) => (
              <OpsItem key={i} tone="warn" title={w} />
            ))}
          </ul>
        </OpsDetails>
      ) : null}
      <OpsSource route="/api/established" field="evidence[], what_is_actually_open" />
    </OpsSection>
  );
}

export default Review;
