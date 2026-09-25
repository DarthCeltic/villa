import { useEffect, useMemo, useState } from "react";

import {
  closeSession,
  confirmStructuredIntent,
  GovernedRefusal,
  isRefusal,
  openSession,
  previewStructuredIntent,
  sessionIsOpen,
  sessionSecondsLeft,
  structuredIntentCatalog,
  StructuredIntentDefinition,
  StructuredIntentPreview,
  StructuredIntentResult,
} from "../lib/governed";

type IntentOutput = StructuredIntentPreview | StructuredIntentResult | GovernedRefusal;

export function StructuredIntentConsole() {
  const [catalog, setCatalog] = useState<StructuredIntentDefinition[]>([]);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [text, setText] = useState("run preflight");
  const [preview, setPreview] = useState<IntentOutput | null>(null);
  const [result, setResult] = useState<IntentOutput | null>(null);
  const [busy, setBusy] = useState(false);
  const [session, setSession] = useState(sessionIsOpen());
  const [sessionError, setSessionError] = useState<string | null>(null);

  useEffect(() => {
    structuredIntentCatalog()
      .then(setCatalog)
      .catch((e) => setCatalogError(e instanceof Error ? e.message : String(e)));
  }, []);

  const resolvedPreview = useMemo(() => {
    if (!preview || isRefusal(preview) || preview.status !== "PREVIEW") return null;
    return preview as StructuredIntentPreview;
  }, [preview]);

  async function open() {
    setSessionError(null);
    try {
      await openSession();
      setSession(true);
    } catch (e) {
      setSessionError(e instanceof Error ? e.message : String(e));
    }
  }

  async function inspect() {
    setBusy(true);
    setResult(null);
    try {
      setPreview(await previewStructuredIntent(text));
      setSession(sessionIsOpen());
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!resolvedPreview) return;
    setBusy(true);
    try {
      setResult(await confirmStructuredIntent(
        text,
        resolvedPreview.confirmation_required ? resolvedPreview.preview_sha256 : undefined,
      ));
      setSession(sessionIsOpen());
    } finally {
      setBusy(false);
    }
  }

  function output(label: string, out: IntentOutput | null) {
    if (!out) return null;
    if (isRefusal(out)) {
      return (
        <div role="status" className="ops-note" style={{ color: "var(--warn)" }}>
          {label}: refused (HTTP {out.status}) — {out.reason}
        </div>
      );
    }
    if (out.status === "REFUSED") {
      return (
        <div role="status" className="ops-note" style={{ color: "var(--warn)" }}>
          {label}: refused{out.code ? ` (${out.code})` : ""} — {out.why ?? "see details"}
        </div>
      );
    }
    return (
      <div role="status" className="ops-note">
        {label}: <strong>{out.status}</strong>
        {out.action ? <> · action <code>{out.action}</code></> : null}
        {"job_id" in out && out.job_id ? <> · job <code>{out.job_id}</code></> : null}
      </div>
    );
  }

  return (
    <section
      data-control="system.act.structured-intent"
      aria-label="Structured intent control"
      style={{ border: "1px solid var(--line-strong)", borderRadius: "var(--radius-sm)", padding: 14, display: "grid", gap: 10 }}
    >
      <div>
        <div style={{ fontWeight: 600 }}>Tell ARGUS what to do</div>
        <div className="ops-note">
          A closed, inspectable sentence grammar—not an AI guesser. ARGUS maps an exact phrase
          to one typed action, shows the real plan, and refuses unmapped or ambiguous language.
          The browser never receives the command credential.
        </div>
      </div>

      {session ? (
        <div className="ops-note" style={{ color: "var(--ok, var(--accent))" }}>
          Governed session open · {sessionSecondsLeft()}s remaining
        </div>
      ) : (
        <div style={{ display: "grid", gap: 6 }}>
          <button type="button" className="interactive" onClick={open}>
            Open a governed session
          </button>
          {sessionError ? <div className="ops-note" style={{ color: "var(--warn)" }}>{sessionError}</div> : null}
        </div>
      )}

      <label style={{ display: "grid", gap: 6 }}>
        <span className="ops-note">Request</span>
        <textarea
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setPreview(null);
            setResult(null);
          }}
          rows={3}
          maxLength={512}
          spellCheck={false}
          style={{ width: "100%", resize: "vertical" }}
        />
      </label>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button type="button" className="interactive" disabled={!session || busy || !text.trim()} onClick={inspect}>
          {busy ? "Working…" : "Preview exact plan"}
        </button>
        <button type="button" className="interactive" disabled={!resolvedPreview || busy} onClick={confirm}>
          {resolvedPreview?.confirmation_required ? "Confirm this plan and run" : "Run read-only request"}
        </button>
        {session ? (
          <button type="button" className="interactive" onClick={() => { closeSession(); setSession(false); }}>
            Close session
          </button>
        ) : null}
      </div>

      {output("Preview", preview)}
      {resolvedPreview ? (
        <details>
          <summary>
            Exact plan · {resolvedPreview.confirmation_required ? "confirmation required" : "read-only"}
          </summary>
          <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
            {JSON.stringify(resolvedPreview.plan, null, 2)}
          </pre>
        </details>
      ) : null}
      {output("Result", result)}

      <details>
        <summary>{catalog.length} supported request shapes</summary>
        {catalogError ? <div className="ops-note" style={{ color: "var(--warn)" }}>{catalogError}</div> : null}
        <div style={{ display: "grid", gap: 6, marginTop: 8 }}>
          {catalog.map((item) => (
            <button
              type="button"
              className="interactive"
              key={`${item.intent}:${item.example}`}
              onClick={() => { setText(item.example); setPreview(null); setResult(null); }}
              style={{ textAlign: "left" }}
            >
              <code>{item.example}</code> <span className="ops-note">→ {item.action}</span>
            </button>
          ))}
        </div>
      </details>
      <div className="ops-note">
        High-consequence actions—credentials, real acquisition/provider execution, GPU starts,
        job resume, and final scientific authority—are deliberately unreachable through text.
      </div>
    </section>
  );
}
