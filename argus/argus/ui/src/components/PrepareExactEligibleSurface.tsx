import { useState } from "react";
import {
  GovernedPlan,
  GovernedRefusal,
  GovernedResult,
  approvedHash,
  isRefusal,
  openSession,
  planGoverned,
  runGoverned,
  sessionIsOpen,
} from "../lib/governed";

const ACTION = "surface.prepare_exact_eligible";

export function PrepareExactEligibleSurface({ scroll }: { scroll: string }) {
  const [busy, setBusy] = useState(false);
  const [plan, setPlan] = useState<GovernedPlan | GovernedRefusal | null>(null);
  const [result, setResult] = useState<GovernedResult | GovernedRefusal | null>(null);

  async function preview() {
    setBusy(true);
    setResult(null);
    try {
      if (!sessionIsOpen()) await openSession();
      setPlan(await planGoverned(ACTION, { scroll }));
    } finally {
      setBusy(false);
    }
  }

  async function approve() {
    if (!plan || isRefusal(plan)) return;
    setBusy(true);
    try {
      setResult(
        await runGoverned(ACTION, { scroll, approved_plan_sha256: approvedHash(plan) }),
      );
    } finally {
      setBusy(false);
    }
  }

  const body = plan && !isRefusal(plan) ? plan.plan : null;
  const ready = body?.ready === true;
  const gate = (body?.eligible_target_gate as { verdict?: string; reasons?: string[] } | undefined) ?? null;

  return (
    <section
      aria-label="Prepare exact eligible surface"
      style={{ border: "1px solid var(--line-strong)", borderRadius: "var(--radius-sm)", padding: 14, display: "grid", gap: 10 }}
    >
      <div>
        <div style={{ fontWeight: 600 }}>Prepare exact eligible surface</div>
        <div className="meta" style={{ color: "var(--ink-dim)" }}>
          Identity preflight, a bounded read of this scroll's own exact eligible volume,
          surface growth, flattening, multi-depth rendering and evidence recording -- run
          through the same resumable pipeline used elsewhere. It halts honestly, with a
          reason, at the first stage that is not ready; it never fabricates a finished
          surface.
        </div>
      </div>

      <button type="button" className="interactive" disabled={busy} onClick={preview} style={{ minHeight: "var(--control-h)" }}>
        {busy && !plan ? "Checking…" : "Check what would happen"}
      </button>

      {plan && isRefusal(plan) ? (
        <div role="status" style={{ border: "1px solid var(--warn)", borderRadius: "var(--radius-sm)", padding: 10 }}>
          <div style={{ fontWeight: 600, color: "var(--warn)" }}>Refused (HTTP {plan.status})</div>
          <div className="meta" style={{ marginTop: 4 }}>{plan.reason}</div>
        </div>
      ) : null}

      {body ? (
        <div role="status" style={{ border: "1px solid var(--line)", borderRadius: "var(--radius-sm)", padding: 10, display: "grid", gap: 6 }}>
          {typeof body.target_volume_source === "string" ? (
            <div className="meta">exact eligible volume: <code>{body.target_volume_source}</code></div>
          ) : null}
          {gate ? (
            <div className="meta">
              eligible-target gate: <code>{gate.verdict}</code>
              {gate.verdict !== "PERMITTED" && gate.reasons?.length ? ` — ${gate.reasons.join("; ")}` : ""}
            </div>
          ) : null}
          {!ready && typeof body.planning_refusal === "string" ? (
            <div className="meta" style={{ color: "var(--warn)" }}>{body.planning_refusal}</div>
          ) : null}
          {ready ? (
            <button type="button" className="interactive" disabled={busy} onClick={approve} style={{ minHeight: "var(--control-h)", justifySelf: "start" }}>
              {busy ? "Submitting…" : "Approve exactly this plan and run it"}
            </button>
          ) : null}
        </div>
      ) : null}

      {result && isRefusal(result) ? (
        <div role="status" style={{ border: "1px solid var(--warn)", borderRadius: "var(--radius-sm)", padding: 10 }}>
          <div style={{ fontWeight: 600, color: "var(--warn)" }}>Refused (HTTP {result.status})</div>
          <div className="meta" style={{ marginTop: 4 }}>{result.reason}</div>
        </div>
      ) : null}

      {result && !isRefusal(result) ? (
        <div role="status" style={{ border: "1px solid var(--line)", borderRadius: "var(--radius-sm)", padding: 10, display: "grid", gap: 4 }}>
          <div style={{ fontWeight: 600 }}>Job submitted</div>
          <div className="meta">job: <code>{result.result?.job_id ?? "—"}</code></div>
          <div className="meta">status: <code>{result.result?.status ?? "—"}</code></div>
          <div className="meta" style={{ color: "var(--ink-dim)", marginTop: 4 }}>
            The job ran. Whichever stage it reached or halted at keeps its own real verdict --
            check Jobs and this scroll's status facts for what actually happened.
          </div>
        </div>
      ) : null}
    </section>
  );
}
