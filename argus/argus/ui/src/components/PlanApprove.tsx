import { useCallback, useEffect, useState } from "react";

import {
  approvedHash,
  idempotencyKey,
  type GovernedPlan,
  type GovernedRefusal,
  type GovernedResult,
  isRefusal,
  openSession,
  runOutcome,
  planGoverned,
  runGoverned,
  sessionIsOpen,
  subscribeGovernedSession,
} from "../lib/governed";

interface Props {
  action: string;
  controlId?: string;
  params: Record<string, unknown>;
  label: string;
  why?: string;
  onDone?: () => void;
  disabledReason?: string | null;
}

function asList(v: unknown): string[] {
  return Array.isArray(v) ? v.map((x) => String(x)) : [];
}

export function PlanApprove({ action, controlId, params, label, why, onDone, disabledReason }: Props) {
  const [session, setSession] = useState(sessionIsOpen());
  const [plan, setPlan] = useState<GovernedPlan | GovernedRefusal | null>(null);
  const [out, setOut] = useState<GovernedResult | GovernedRefusal | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const intentKey = idempotencyKey(action, params);

  useEffect(
    () => subscribeGovernedSession(() => setSession(sessionIsOpen())),
    [],
  );

  useEffect(() => {
    setPlan(null);
    setOut(null);
    setErr(null);
  }, [intentKey]);

  const open = useCallback(async () => {
    setErr(null);
    try {
      await openSession();
      setSession(true);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const review = useCallback(async () => {
    setBusy(true);
    setErr(null);
    setOut(null);
    try {
      setPlan(await planGoverned(action, params));
      setSession(sessionIsOpen());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [action, params]);

  const approve = useCallback(async () => {
    if (!plan || isRefusal(plan)) return;
    setBusy(true);
    setErr(null);
    try {
      const res = await runGoverned(action, { ...params, approved_plan_sha256: approvedHash(plan) });
      setOut(res);
      setSession(sessionIsOpen());
      if (!isRefusal(res)) {
        setPlan(null);
        onDone?.();
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [action, params, plan, onDone]);

  const p = plan && !isRefusal(plan) ? plan.plan : null;
  const changes = asList(p?.changes);
  const readiness = (p?.readiness ?? null) as { allowed?: boolean; missing?: string[] } | null;
  const wouldRefuse = p?.would_be_refused === true;
  const outcome = out && !isRefusal(out) ? runOutcome(out) : null;
  const refusal = (p?.refusal ?? null) as { code?: string; why?: string } | null;
  const control = controlId ?? action;

  return (
    <div data-control={`plan-approve.${control}`} style={{ display: "grid", gap: 6, marginTop: 6 }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        {!session ? (
          <button type="button" className="interactive" onClick={open} data-control={`plan-approve.${control}.session`}>
            Open a governed session
          </button>
        ) : null}
        <button
          type="button"
          className="interactive"
          disabled={!session || busy || Boolean(disabledReason)}
          onClick={review}
          title={disabledReason ?? why ?? "ask what this would do; nothing changes yet"}
          data-control={`plan-approve.${control}.review`}
        >
          {busy && !plan ? "Reading the plan…" : `Review: ${label}`}
        </button>
        {p && !wouldRefuse ? (
          <button
            type="button"
            className="interactive"
            disabled={busy}
            onClick={approve}
            data-control={`plan-approve.${control}.approve`}
          >
            {busy ? "Working…" : "Approve and run"}
          </button>
        ) : null}
      </div>
      {!plan && !out ? (
        <div className="meta" style={{ color: "var(--ink-dim)" }} data-control={`plan-approve.${control}.contract`}>
          Plan, then approve: reviewing shows exactly what would change and changes nothing; only &ldquo;Approve and run&rdquo; acts, after you open a governed session.
        </div>
      ) : null}
      {disabledReason ? <div className="meta" style={{ color: "var(--ink-dim)" }}>{disabledReason}</div> : null}
      {err ? <div className="meta" style={{ color: "var(--warn)" }}>{err}</div> : null}
      {plan && isRefusal(plan) ? (
        <div className="meta" role="note" data-control={`plan-approve.${control}.refused`}>
          Refused: {plan.reason}
        </div>
      ) : null}
      {p ? (
        <div className="meta" data-control={`plan-approve.${control}.plan`}>
          {changes.map((c, i) => (
            <div key={i}>• {c}</div>
          ))}
          {typeof p.note === "string" ? <div style={{ color: "var(--ink-dim)" }}>{p.note}</div> : null}
          {refusal ? (
            <div style={{ fontWeight: 600 }} data-control={`plan-approve.${control}.would-refuse`}>
              This would be refused ({refusal.code ?? "REFUSED"}): {refusal.why ?? "no reason was given"}
            </div>
          ) : null}
          {readiness && readiness.allowed === false ? (
            <div style={{ fontWeight: 600 }}>
              Not allowed yet — missing: {(readiness.missing ?? []).join("; ") || "see the reason above"}
            </div>
          ) : null}
        </div>
      ) : null}
      {out && isRefusal(out) ? (
        <div className="meta" style={{ fontWeight: 600 }} data-control={`plan-approve.${control}.result`}>
          Refused: {out.reason}
        </div>
      ) : null}
      {outcome ? (
        <div
          className="meta"
          style={{ fontWeight: 600 }}
          data-control={`plan-approve.${control}.result`}
          data-outcome={outcome.kind}
        >
          {outcome.text}
        </div>
      ) : null}
    </div>
  );
}
