import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Disclosure } from "./Disclosure";
import { getJson } from "../lib/http";
import { useSharedUniverse } from "../lib/sharedUniverse";
import { plainReason, useArgusContext, type ArgusMode } from "../lib/context";
import { usePublicDemo } from "../lib/publicDemo";
import { PublicRuns } from "./evidence/PublicRunReceipt";
import {
  STATE_GLYPH, STATE_TONE, STATE_WORD, blockerLine, noStatusLine, positionLine, stepWord,
  type ScrollStatus, type StatusStep, type CapabilityLane, type WorkQueueItem,
} from "../lib/scrollStatus";
import "../theme/status.css";

type ScrollWorkspace = { route: ScrollStatus };

export function useScrollStatus(scroll: string | null): { status: ScrollStatus | null; loading: boolean } {
  const universe = useSharedUniverse();
  const fromBatch = scroll ? (universe.byId.get(scroll)?.status ?? null) : null;
  const ask = !!scroll;
  const [own, setOwn] = useState<ScrollStatus | null>(null);
  useEffect(() => {
    if (!scroll) {
      setOwn(null);
      return;
    }
    let live = true;
    void getJson<ScrollWorkspace>(`/api/scroll_workspace/${encodeURIComponent(scroll)}`).then((truth) => {
      if (!live) return;
      if (truth.ok && truth.data.route) {
        setOwn(truth.data.route);
        return;
      }
      void getJson<ScrollStatus>(`/api/scroll_status?scroll=${encodeURIComponent(scroll)}`).then((res) => {
        if (live && res.ok) setOwn(res.data);
      });
    });
    return () => {
      live = false;
    };
  }, [scroll]);
  const status = own && own.requested === scroll ? own : fromBatch;
  return { status, loading: ask && !status };
}

function RouteDots({ status, control }: { status: ScrollStatus; control: string }) {
  return (
    <ol className="ssb-route" aria-label={`Journey, ${status.steps.length} steps`} data-control={`${control}.route`}>
      {status.steps.map((st) => (
        <li
          key={st.id}
          className="ssb-dot"
          data-state={st.state}
          data-tone={STATE_TONE[st.state]}
          data-current={status.next_action.step === st.id ? "true" : undefined}
          title={`${st.n}. ${st.label}: ${stepWord(st)}. ${st.why}`}
        >
          <span aria-hidden="true">{STATE_GLYPH[st.state]}</span>
          <span className="visually-hidden">{`${st.n}. ${st.label}: ${stepWord(st)}`}</span>
        </li>
      ))}
    </ol>
  );
}

function StepList({ steps, raw }: { steps: StatusStep[]; raw: boolean }) {
  return (
    <ol className="ssb-steps">
      {steps.map((st) => (
        <li key={st.id} className="ssb-step" data-state={st.state} data-tone={STATE_TONE[st.state]}>
          <span className="ssb-step-n">{st.n}</span>
          <span className="ssb-step-body">
            <strong>{st.label}</strong>{" "}
            <span className="ssb-step-state">{stepWord(st)}{st.optional ? " (optional)" : ""}</span>
            <span className="ssb-step-why">{st.why}</span>
            {st.to && st.action_label ? (
              <Link className="ssb-step-action interactive" to={st.to} data-control={`status.step.${st.id}.action`}>
                {st.action_label}
              </Link>
            ) : null}
            {raw && (st.receipt || st.basis.length) ? (
              <span className="ssb-step-raw">
                {st.basis.length ? <span>source: {st.basis.join("; ")}</span> : null}
                {st.receipt ? <code className="ssb-receipt">{st.receipt}</code> : null}
              </span>
            ) : null}
          </span>
        </li>
      ))}
    </ol>
  );
}

function CapabilityLanes({ lanes, control }: { lanes: CapabilityLane[]; control: string }) {
  if (!lanes.length) return null;
  return (
    <section className="ssb-lanes" aria-label="Parallel capability lanes" data-control={`${control}.lanes`}>
      <div className="ssb-lanes-head">
        <strong>What ARGUS can show for this scroll</strong>
        <span>Independent lanes — later evidence is not hidden by an earlier blocker.</span>
      </div>
      <div className="ssb-lanes-grid">
        {lanes.map((lane) => (
          <article key={lane.id} className="ssb-lane" data-state={lane.state} data-control={`${control}.lane.${lane.id}`}>
            <div className="ssb-lane-top">
              <strong>{lane.label}</strong>
              <span className="ssb-lane-state">{STATE_WORD[lane.state]}</span>
            </div>
            <div className="ssb-lane-count">{lane.done}/{lane.total} route stages</div>
            <p>{lane.summary}</p>
            {lane.to && lane.next ? <Link to={lane.to} className="ssb-lane-link interactive">{lane.next}</Link> : null}
          </article>
        ))}
      </div>
    </section>
  );
}

function HumanWorkQueue({ items, control, raw = false }: { items: WorkQueueItem[]; control: string; raw?: boolean }) {
  if (!items.length) return null;
  return (
    <section className="ssb-work" aria-label="Work available now" data-control={`${control}.work`}>
      <div className="ssb-work-head">
        <strong>Work available now</strong>
        <span>Each button opens the exact screen and control for this scroll.</span>
      </div>
      <div className="ssb-work-list">
        {items.map((item, index) => (
          <article key={item.step} className="ssb-work-item" data-state={item.state} data-control={`${control}.work.${item.step}`}>
            <div>
              <span className="ssb-work-order">{index === 0 ? "Work next" : "Also ready"}</span>
              <strong>{item.label}</strong>
              <p title={raw ? undefined : item.why}>{raw ? item.why : plainReason(item.why)}</p>
            </div>
            <Link className="ssb-work-link interactive" to={item.to} data-control={`${control}.work.${item.step}.open`}>
              {item.state === "HUMAN_GATED" ? "Make this decision" : item.state === "BLOCKED" ? "Resolve this blocker" : "Continue this step"}
            </Link>
          </article>
        ))}
      </div>
    </section>
  );
}

export function ScrollStatusView({
  status, mode, control = "scroll.status", publicDemo = false, note, extra,
}: {
  status: ScrollStatus | null;
  mode: ArgusMode;
  control?: string;
  publicDemo?: boolean;
  note?: string;
  extra?: ReactNode;
}) {
  if (!status) {
    return (
      <section className="ssb" data-control={control} data-mode={mode} data-state="none" aria-label="Scroll status">
        <p className="ssb-note">{note ?? noStatusLine(null, false)}</p>
      </section>
    );
  }
  if (status.status === "REFUSED") {
    return (
      <section className="ssb" data-control={control} data-mode={mode} data-state="refused" aria-label="Scroll status">
        <p className="ssb-head"><strong>{status.display}</strong> <span className="ssb-pos">refused</span></p>
        <p className="ssb-refusal" data-control={`${control}.refusal`}>
          <span className="ssb-code">{status.refusal?.code}</span> {status.refusal?.why}
        </p>
        <p className="ssb-next" data-novice="next">
          <span className="ssb-key">Next</span>{" "}
          <Link to={status.next_action.to} className="interactive" data-control={`${control}.next`}>
            {status.next_action.label}
          </Link>
        </p>
      </section>
    );
  }
  const next = status.next_action;
  const blocker = blockerLine(status, publicDemo);
  const raw = mode === "expert";
  const detail = (
    <>
      <StepList steps={status.steps} raw={raw} />
      <dl className="ssb-raw">
        <dt>Stage lineage</dt>
        <dd>
          {status.lineage
            ? `${status.lineage.counts?.attempted ?? 0} attempt(s), chain ${status.lineage.chain_state ?? "unknown"}`
            : "not read"}
          {status.lineage?.path ? <code className="ssb-receipt">{status.lineage.path}</code> : null}
        </dd>
        <dt>Composed</dt>
        <dd>{status.generated_utc} by argus.core.scroll_status</dd>
        <dt>Claim ceiling</dt>
        <dd>{status.claim_ceiling}</dd>
      </dl>
      {status.contradictions.length ? (
        <ul className="ssb-contradictions">
          {status.contradictions.map((c) => <li key={c}>{c}</li>)}
        </ul>
      ) : null}
    </>
  );
  return (
    <section className="ssb" data-control={control} data-mode={mode} data-state="ok" aria-label="Scroll status" data-novice="status">
      <p className="ssb-head">
        <strong className="ssb-scroll">{status.display}</strong>{" "}
        <span className="ssb-pos" data-control={`${control}.position`}>{positionLine(status)}</span>
      </p>
      {status.progress_summary ? (
        <p className="ssb-scope" data-control={`${control}.scope`}>
          {status.progress_summary.verified_outputs
            ? `Route receipts: ${status.progress_summary.done} of ${status.progress_summary.total} complete. ${status.progress_summary.evidence_attempted ?? 0} attempted stages produced ${status.progress_summary.verified_outputs} verified geometry/render output${status.progress_summary.verified_outputs === 1 ? "" : "s"}, already viewable. The canonical route is a reproducibility checklist: later evidence is retained, but it does not replace the missing input receipt.`
            : status.progress_summary.scope}
        </p>
      ) : null}
      <CapabilityLanes lanes={status.capability_lanes ?? []} control={control} />
      <HumanWorkQueue items={status.work_queue ?? []} control={control} raw={raw} />
      <RouteDots status={status} control={control} />
      <p className="ssb-next" data-novice="next" data-operator-only={next.operator_only ? "true" : undefined}>
        <span className="ssb-key">Primary next action</span>{" "}
        <Link to={next.to} className="ssb-next-link interactive" data-control={`${control}.next`} title={next.why}>
          {next.label}
        </Link>
        {next.operator_only ? <span className="ssb-chip">operator only</span> : null}
        <span className="ssb-why">{next.why}</span>
      </p>
      <p className="ssb-blocker" data-novice="blocker" data-tone={status.blocker ? "bad" : "idle"}>
        <span className="ssb-key">Required blocker</span> <span title={blocker.full}>{blocker.text}</span>
        {raw && status.blocker ? <span className="ssb-why">{blocker.full}</span> : null}
      </p>
      {raw ? (
        <div className="ssb-detail" data-control={`${control}.detail`}>{detail}</div>
      ) : (
        <Disclosure
          className="ssb-more"
          summaryClassName="ssb-more-sum"
          summary={`The whole route, its receipts and sources (${status.steps.length} steps)`}
          data-control={`${control}.more`}
        >
          <div className="ssb-detail">{detail}</div>
        </Disclosure>
      )}
      {extra}
      <p className="ssb-legend" aria-hidden="true">
        {(["DONE", "AVAILABLE", "HUMAN_GATED", "BLOCKED", "NOT_REACHED"] as const)
          .map((k) => `${STATE_GLYPH[k]} ${STATE_WORD[k]}`).join("  ")}
      </p>
    </section>
  );
}

export function ScrollStatusBar({ control = "scroll.status", quietWithoutScroll = false }: {
  control?: string;
  quietWithoutScroll?: boolean;
}) {
  const { ctx, mode } = useArgusContext();
  const demo = usePublicDemo();
  const { status, loading } = useScrollStatus(ctx.scroll);
  const note = !ctx.scroll || loading || !status ? noStatusLine(ctx.scroll, loading) : undefined;
  if (quietWithoutScroll && !ctx.scroll) return null;
  return (
    <ScrollStatusView
      status={status} mode={mode} control={control} publicDemo={demo} note={note}
      extra={ctx.scroll && status && status.status !== "REFUSED"
        ? <PublicRuns scroll={ctx.scroll} quiet control={`${control}.public-runs`} />
        : null}
    />
  );
}
