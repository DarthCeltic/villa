import { useCallback, useState } from "react";

import {
  closeSession,
  GovernedRefusal,
  GovernedResult,
  isRefusal,
  openSession,
  runGoverned,
  sessionIsOpen,
  sessionSecondsLeft,
} from "../lib/governed";
import { ResourceTierBadge } from "./ResourceTierBadge";

export function GovernedAction({
  action,
  params,
  label,
  why,
}: {
  action: string;
  params: Record<string, unknown>;
  label: string;
  why: string;
}) {
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState<GovernedResult | GovernedRefusal | null>(null);
  const [session, setSession] = useState(sessionIsOpen());
  const [sessionError, setSessionError] = useState<string | null>(null);

  const open = useCallback(async () => {
    setSessionError(null);
    try {
      await openSession();
      setSession(true);
    } catch (e) {
      setSessionError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const run = useCallback(async () => {
    setBusy(true);
    try {
      setOut(await runGoverned(action, params));
      setSession(sessionIsOpen());
    } finally {
      setBusy(false);
    }
  }, [action, params]);

  const jobId = out && !isRefusal(out) ? out.result?.job_id : undefined;
  const jobStatus = out && !isRefusal(out) ? out.result?.status : undefined;
  const duplicate = jobStatus === "DUPLICATE";

  return (
    <section
      aria-label={`Governed action: ${label}`}
      style={{
        border: "1px solid var(--line-strong)",
        borderRadius: "var(--radius-sm)",
        padding: 14,
        display: "grid",
        gap: 10,
      }}
    >
      <div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <div style={{ fontWeight: 600 }}>{label}</div>
          <ResourceTierBadge operationId={action} />
        </div>
        <div className="meta" style={{ color: "var(--ink-dim)" }}>
          This WRITES: it submits a governed job and produces an audited receipt. Everything
          else on this screen only reads.
        </div>
        <div className="meta" style={{ color: "var(--ink-dim)", marginTop: 4 }}>{why}</div>
      </div>

      {session ? (
        <div className="meta" style={{ color: "var(--ok, var(--accent))" }}>
          Session open · {sessionSecondsLeft()}s remaining · no credential is held by this page
        </div>
      ) : (
        <div style={{ display: "grid", gap: 6 }}>
          <button
            type="button"
            className="interactive"
            onClick={open}
            style={{ minHeight: "var(--control-h)" }}
          >
            Open a governed session
          </button>
          <div className="meta" style={{ color: "var(--ink-dim)" }}>
            Reaching this page is not authorisation. A session is short-lived, bound to this
            origin, and opened on purpose.
          </div>
          {sessionError ? (
            <div className="meta" style={{ color: "var(--warn)" }}>{sessionError}</div>
          ) : null}
        </div>
      )}

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button
          type="button"
          className="interactive"
          disabled={!session || busy}
          onClick={run}
          style={{ minHeight: "var(--control-h)" }}
          title={session ? why : "open a governed session first"}
        >
          {busy ? "Submitting…" : label}
        </button>
        {session ? (
          <button
            type="button"
            className="interactive"
            onClick={() => {
              closeSession();
              setSession(false);
            }}
            style={{ minHeight: "var(--control-h)" }}
          >
            Close session
          </button>
        ) : null}
      </div>

      {out && isRefusal(out) ? (
        <div
          role="status"
          style={{
            border: "1px solid var(--warn)",
            borderRadius: "var(--radius-sm)",
            padding: 10,
          }}
        >
          <div style={{ fontWeight: 600, color: "var(--warn)" }}>
            Refused (HTTP {out.status})
          </div>
          {}
          <div className="meta" style={{ marginTop: 4 }}>{out.reason}</div>
        </div>
      ) : null}

      {out && !isRefusal(out) ? (
        <div
          role="status"
          style={{
            border: "1px solid var(--line)",
            borderRadius: "var(--radius-sm)",
            padding: 10,
            display: "grid",
            gap: 4,
          }}
        >
          <div style={{ fontWeight: 600 }}>
            Job submitted{duplicate ? " — already ran" : ""}
          </div>
          {duplicate ? (
            <div className="meta">
              The same request was already submitted, so no second job was created. That is the
              idempotency guarantee working, not an error.
            </div>
          ) : null}
          <div className="meta">job: <code>{jobId ?? "—"}</code></div>
          <div className="meta">status: <code>{jobStatus ?? "—"}</code></div>
          <div className="meta">plan hash: <code>{out.plan_hash.slice(0, 16)}…</code></div>
          {}
          <div className="meta" style={{ color: "var(--ink-dim)", marginTop: 4 }}>
            The job ran. That is not a scientific result: whatever this action computed keeps
            its own verdict, which may be a refusal.
          </div>
        </div>
      ) : null}
    </section>
  );
}
