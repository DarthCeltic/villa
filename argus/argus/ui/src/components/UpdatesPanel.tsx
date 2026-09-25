import { useEffect, useState } from "react";

import { usePoll } from "../lib/poll";
import { PlanApprove } from "./PlanApprove";

interface Diff {
  available?: boolean;
  why?: string;
  commits?: number | null;
  files_changed?: number;
  truncated?: boolean;
  risk?: string;
  impacted_capabilities?: string[];
  io_contract_touched?: boolean;
  licence_touched?: boolean;
  dependencies_touched?: boolean;
  by_area?: Record<string, number>;
  notable_commits?: [string, string][];
  io_contract_paths?: string[];
}

interface UpdateRow {
  source_id: string;
  revision: string;
  stage: string;
  held_reason: string | null;
  admitted_revision: string | null;
  providers: string[];
  diff: Diff;
  contract: { defined?: boolean; gates?: Record<string, boolean> | null };
  adapter_tests: { ran?: boolean | null; passed?: boolean | null };
  tested: { contract: boolean; sabotage_selfcheck: boolean; adapter_tests: boolean | null; real_data_control: boolean };
  readiness: Record<string, { allowed: boolean; missing: string[] }>;
}

interface SourceRow {
  source_id: string;
  endpoint: string;
  type: string;
  classification: string;
  channel: string;
  admitted_revision: string | null;
  pin_status: string;
  license: string | null;
  providers: string[];
  watch_state: string;
  status_line: string;
  up_to_date_claim_allowed: boolean;
  observed_revision: string | null;
  active_pins: Record<string, { revision: string; since_utc: string } | null>;
  rollback_available: Record<string, boolean>;
  note?: string | null;
}

interface WatchRow {
  id: string;
  title: string;
  state: string;
  label: string | null;
  state_source: string;
  base_branch: string | null;
  merge_commit: string | null;
  argus_status: string;
  why: string;
  adoption_condition: string;
  affects: string[];
}

interface UpdatesDoc {
  upstream_mode?: string;
  policy: {
    mode: string;
    decided_utc: string | null;
    prompt: { question: string; options: { mode: string; label: string; detail: string; recommended?: boolean }[] } | null;
  };
  sources: SourceRow[];
  updates: UpdateRow[];
  watchlist: WatchRow[];
  last_checked_utc?: string | null;
  rule: string;
}

interface ControlHealth {
  ok: boolean;
  command_reachable?: boolean;
  command_token_ready?: boolean;
  operator_key_ready?: boolean;
  note?: string;
}

const POLICY_OPTIONS = [
  {
    mode: "AUTOMATIC",
    label: "Check upstream automatically",
    detail: "ARGUS periodically reads upstream metadata and records exact candidates. Testing and activation remain governed.",
  },
  {
    mode: "ASK_FIRST",
    label: "Check only when I ask",
    detail: "ARGUS makes no scheduled upstream requests. Use Check now when you want a fresh observation.",
  },
  {
    mode: "NEVER",
    label: "Never contact upstream",
    detail: "ARGUS stays on its installed pins and makes no update-check request.",
  },
] as const;

const short = (r: string | null | undefined) => (r ? (r.startsWith("sha256:") ? r.slice(0, 19) : r.slice(0, 10)) : "none");

function Chip({ ok, children }: { ok: boolean | null; children: string }) {
  const mark = ok === null ? "not run" : ok ? "passed" : "failed";
  return (
    <span
      className="ops-mono meta"
      style={{ border: "1px solid var(--line-strong)", borderRadius: "var(--radius-sm)", padding: "0 6px", marginRight: 6 }}
    >
      {children}: {mark}
    </span>
  );
}

function ReceiptAttach({ u, reload }: { u: UpdateRow; reload: () => void }) {
  const [path, setPath] = useState("");
  const controlKey = `${u.source_id}.${short(u.revision)}`;
  return (
    <div style={{ display: "grid", gap: 4 }}>
      <label className="meta">
        Real-data control receipt (a JSON receipt of a control run on real scroll data)
        <input
          value={path}
          onChange={(e) => setPath(e.target.value)}
          placeholder="path to the receipt file"
          style={{ display: "block", width: "100%" }}
          data-control={`updates.receipt.${controlKey}`}
        />
      </label>
      <PlanApprove
        action="provider.update.attach_real_data_control"
        controlId={`provider.update.attach_real_data_control.${controlKey}`}
        params={{ source_id: u.source_id, revision: u.revision, receipt_path: path }}
        label="attach the real-data control"
        onDone={reload}
        disabledReason={path.trim() ? null : "enter the receipt path first"}
      />
    </div>
  );
}

function UpdateCard({ u, reload }: { u: UpdateRow; reload: () => void }) {
  const d = u.diff;
  const gates = u.contract.gates ?? {};
  const controlKey = `${u.source_id}.${short(u.revision)}`;
  return (
    <div
      data-control={`updates.update.${controlKey}`}
      style={{ border: "1px solid var(--line-strong)", borderRadius: "var(--radius-sm)", padding: 12, display: "grid", gap: 6 }}
    >
      <div style={{ fontWeight: 600 }}>
        {u.source_id} · <span className="ops-mono">{short(u.revision)}</span> · {u.stage}
        {u.held_reason ? ` (held: ${u.held_reason})` : ""}
      </div>
      <div className="meta">
        Currently used: <span className="ops-mono">{short(u.admitted_revision)}</span> · affects: {u.providers.join(", ") || "—"}
      </div>
      <div className="meta">
        <b>What changed.</b>{" "}
        {d.available
          ? `${d.commits ?? "?"} commits, ${d.files_changed ?? "?"} files${d.truncated ? " (list truncated)" : ""}; risk ${d.risk}. ` +
            Object.entries(d.by_area ?? {}).map(([k, v]) => `${k}: ${v}`).join(", ")
          : `not available — ${d.why ?? "no diff recorded"}`}
      </div>
      {(d.notable_commits ?? []).length ? (
        <div className="meta">
          {(d.notable_commits ?? []).slice(-4).map(([sha, msg]) => (
            <div key={sha}>
              <span className="ops-mono">{sha}</span> {msg}
            </div>
          ))}
        </div>
      ) : null}
      <div className="meta">
        <b>Inputs or outputs changed?</b>{" "}
        {d.available
          ? d.io_contract_touched
            ? `a path that defines an input or output changed (${(d.io_contract_paths ?? []).slice(0, 3).join(", ")}) — this is where the change lands, not proof of what it does`
            : "no input or output path changed"
          : "unknown"}
        {" · "}licence touched: {d.available ? String(Boolean(d.licence_touched)) : "unknown"} · dependencies touched:{" "}
        {d.available ? String(Boolean(d.dependencies_touched)) : "unknown"}
      </div>
      <div className="meta">
        <b>Compatibility.</b>{" "}
        {u.contract.defined
          ? Object.entries(gates).map(([k, v]) => `${k}: ${v ? "ok" : "FAILED"}`).join(" · ")
          : "no adapter contract is declared for this source, so compatibility cannot be shown"}
      </div>
      <div>
        <Chip ok={u.tested.contract}>adapter contract</Chip>
        <Chip ok={u.tested.sabotage_selfcheck}>check can fail</Chip>
        <Chip ok={u.tested.adapter_tests}>ARGUS adapter tests</Chip>
        <Chip ok={u.tested.real_data_control ? true : null}>real-data control</Chip>
      </div>
      <div className="meta">
        <b>Apparatus use:</b> {u.readiness.apparatus?.allowed ? "allowed" : `not yet — ${u.readiness.apparatus?.missing.join("; ")}`}
        {" · "}
        <b>Scientific use:</b> {u.readiness.scientific?.allowed ? "allowed" : `not yet — ${u.readiness.scientific?.missing.join("; ")}`}
      </div>
      <div style={{ display: "grid", gap: 4 }}>
        {u.stage === "DISCOVERED" || u.stage === "CLASSIFIED" || u.stage === "LICENSE_CHECKED" || u.stage === "STAGED" || u.stage === "BUILD_PASSED" ? (
          <PlanApprove action="provider.update.stage" controlId={`provider.update.stage.${controlKey}`} params={{ source_id: u.source_id, revision: u.revision }} label="check compatibility" onDone={reload} />
        ) : null}
        <PlanApprove action="provider.update.test" controlId={`provider.update.test.${controlKey}`} params={{ source_id: u.source_id, revision: u.revision }} label="run ARGUS adapter tests" onDone={reload} />
        {u.stage === "SYNTHETIC_CONTROL_PASSED" ? <ReceiptAttach u={u} reload={reload} /> : null}
        {["SYNTHETIC_CONTROL_PASSED", "REAL_DATA_CONTROL_PASSED", "ADMITTED", "CURRENT"].includes(u.stage) ? (
          <>
            <PlanApprove
              action="provider.update.activate"
              controlId={`provider.update.activate.${controlKey}.apparatus`}
              params={{ source_id: u.source_id, revision: u.revision, scope: "apparatus" }}
              label="use for apparatus runs"
              onDone={reload}
            />
            <PlanApprove
              action="provider.update.activate"
              controlId={`provider.update.activate.${controlKey}.scientific`}
              params={{ source_id: u.source_id, revision: u.revision, scope: "scientific" }}
              label="use for scientific runs"
              onDone={reload}
              disabledReason={u.readiness.scientific?.allowed ? null : `needs: ${u.readiness.scientific?.missing.join("; ")}`}
            />
          </>
        ) : null}
      </div>
    </div>
  );
}

export function UpdatesPanel() {
  const doc = usePoll<UpdatesDoc>("/api/updates", { intervalMs: 20000 });
  const controls = usePoll<ControlHealth>("/ui/health", { intervalMs: 10000, timeoutMs: 3000 });
  const d = doc.data;
  const reload = () => {
    doc.refresh();
    window.dispatchEvent(new Event("argus:update-policy-changed"));
  };
  const [selectedPolicy, setSelectedPolicy] = useState("AUTOMATIC");

  useEffect(() => {
    if (d?.policy.mode && d.policy.mode !== "UNSET") setSelectedPolicy(d.policy.mode);
  }, [d?.policy.mode]);

  if (!d) {
    return (
      <div className="meta" data-control="updates.loading">
        {doc.failure ? `Could not read /api/updates: ${doc.failure.message}. This is not "no updates".` : "Reading provider updates…"}
      </div>
    );
  }
  const worthEvaluating = d.watchlist.filter(
    (row) => row.state === "MERGED_TO_MAIN" && row.argus_status === "MERGED",
  );
  const hasLiveCheck = Boolean(d.last_checked_utc);
  const policyOptions = d.policy.prompt?.options?.length ? d.policy.prompt.options : POLICY_OPTIONS;
  const currentUpdates: UpdateRow[] = [];
  const previousUpdates: UpdateRow[] = [];
  const updatesBySource = new Map<string, UpdateRow[]>();
  for (const update of d.updates) {
    const rows = updatesBySource.get(update.source_id) ?? [];
    rows.push(update);
    updatesBySource.set(update.source_id, rows);
  }
  for (const [sourceId, rows] of updatesBySource) {
    const observedRevision = d.sources.find((source) => source.source_id === sourceId)?.observed_revision;
    const current = rows.find((row) => observedRevision && row.revision === observedRevision) ?? rows[0];
    if (current) currentUpdates.push(current);
    previousUpdates.push(...rows.filter((row) => row !== current));
  }
  return (
    <div data-control="updates.panel" style={{ display: "grid", gap: 16 }}>
      <section
        role="status"
        data-control="updates.control-health"
        style={{
          border: "1px solid var(--line-strong)",
          borderRadius: "var(--radius-sm)",
          padding: 10,
          color: controls.data?.ok ? "var(--ok)" : "var(--warn)",
        }}
      >
        <b>{controls.data?.ok ? "Update controls ready" : "Update controls unavailable"}</b>{" "}
        <span className="meta">
          {controls.data?.note ?? (controls.loading ? "Checking the local command path…" : "The UI action bridge did not answer. Read-only status still works, but buttons cannot act.")}
        </span>
      </section>
      {d.upstream_mode === "FIXTURE" ? (
        <div className="meta" data-control="updates.fixture-banner" style={{ border: "1px solid var(--line-strong)", padding: 8 }}>
          Demonstration mode: upstream is a scripted offline fixture, not GitHub. The fixture source can be activated for apparatus runs
          only, never for scientific runs, and real sources show as unreachable here rather than as up to date.
        </div>
      ) : null}
      <section
        id="updates-policy"
        aria-labelledby="updates-policy-heading"
        data-control="updates.policy"
        className="anchor-target"
        style={{ display: "grid", gap: 6 }}
      >
        {
}
        <h3 id="updates-policy-heading" tabIndex={-1} style={{ fontWeight: 600, fontSize: "inherit", margin: 0 }}>
          Update policy: {d.policy.mode === "UNSET" ? "not chosen yet — ARGUS has not contacted upstream" : d.policy.mode}
        </h3>
        {d.policy.prompt ? <div className="meta">{d.policy.prompt.question}</div> : null}
        <div role="radiogroup" aria-label="Update policy choices" style={{ display: "grid", gap: 8 }}>
          {policyOptions.map((o) => (
            <label
              key={o.mode}
              className="interactive"
              data-control={`updates.policy.choice.${o.mode}`}
              style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "2px 10px", alignItems: "start", padding: 10, border: "1px solid var(--line-strong)", borderRadius: "var(--radius-sm)" }}
            >
              <input
                type="radio"
                name="argus-update-policy"
                value={o.mode}
                checked={selectedPolicy === o.mode}
                onChange={() => setSelectedPolicy(o.mode)}
                style={{ marginTop: 4 }}
              />
              <span style={{ fontWeight: 600 }}>
                {o.label}{"recommended" in o && o.recommended ? " (recommended)" : ""}
              </span>
              <span className="meta" style={{ gridColumn: 2, color: "var(--ink-dim)" }}>{o.detail}</span>
            </label>
          ))}
        </div>
        <PlanApprove
          action="provider.update.policy"
          controlId="provider.update.policy"
          params={{ mode: selectedPolicy }}
          label={`set policy to ${selectedPolicy}`}
          onDone={reload}
        />
        {d.policy.mode === "AUTOMATIC" || d.policy.mode === "ASK_FIRST" ? (
          <PlanApprove action="provider.update.check" controlId="provider.update.check" params={{}} label="check upstream now" onDone={reload} />
        ) : null}
        <div className="meta" style={{ color: "var(--ink-dim)" }}>{d.rule}</div>
      </section>

      <section aria-label="Updates" data-control="updates.list" style={{ display: "grid", gap: 10 }}>
        <div style={{ fontWeight: 600 }}>
          {!hasLiveCheck
            ? "Live update check not run"
            : d.updates.length
              ? `${d.updates.length} update${d.updates.length === 1 ? "" : "s"} found`
              : "No updates found by the last check"}
        </div>
        {hasLiveCheck ? <div className="meta">Last upstream observation: {d.last_checked_utc}</div> : null}
        {!hasLiveCheck ? (
          <div data-control="updates.worthwhile" style={{ display: "grid", gap: 6 }}>
            <p className="meta" style={{ margin: 0 }}>
              The configured watchlist contains {worthEvaluating.length} candidate {worthEvaluating.length === 1 ? "change" : "changes"} marked merged in its checked-in configuration.
              This is not live verification and not a claim that the installed runtime is current: choose a policy above, then run or await an upstream check.
            </p>
            {worthEvaluating.map((row) => (
              <div key={row.id} className="meta" data-control={`updates.worthwhile.${row.id}`}>
                <span className="ops-mono">{row.id}</span> · <b>{row.title}</b> — affects {row.affects.join(", ") || "an undeclared capability"}
              </div>
            ))}
          </div>
        ) : null}
        {currentUpdates.map((u) => (
          <UpdateCard key={`${u.source_id}@${u.revision}`} u={u} reload={reload} />
        ))}
        {previousUpdates.length ? (
          <details data-control="updates.previous-candidates">
            <summary className="interactive">
              {previousUpdates.length} earlier candidate{previousUpdates.length === 1 ? "" : "s"} (history; not the current upstream observation)
            </summary>
            <div style={{ display: "grid", gap: 10, marginTop: 10 }}>
              {previousUpdates.map((u) => (
                <UpdateCard key={`${u.source_id}@${u.revision}`} u={u} reload={reload} />
              ))}
            </div>
          </details>
        ) : null}
      </section>

      <section aria-label="Watched sources" data-control="updates.sources" style={{ display: "grid", gap: 6 }}>
        <div style={{ fontWeight: 600 }}>What ARGUS watches</div>
        {d.sources.map((s) => (
          <div key={s.source_id} className="meta" data-control={`updates.source.${s.source_id}`} style={{ borderTop: "1px solid var(--line-strong)", paddingTop: 4 }}>
            <b>{s.source_id}</b> ({s.classification} {s.type}, channel {s.channel}) — used: <span className="ops-mono">{short(s.admitted_revision)}</span>{" "}
            [{s.pin_status === "IMMUTABLE" ? "immutable pin" : "NO immutable pin: not usable for scientific runs"}] · licence {s.license ?? "undeclared"} ·{" "}
            {s.status_line}
            {s.observed_revision ? ` · seen: ${short(s.observed_revision)}` : ""}
            {s.up_to_date_claim_allowed ? "" : " · not claimed up to date"}
            <div>
              Active pins — apparatus: {short(s.active_pins.apparatus?.revision)} · scientific: {short(s.active_pins.scientific?.revision)}
            </div>
            {(["apparatus", "scientific"] as const).map((sc) =>
              s.rollback_available[sc] || s.active_pins[sc] ? (
                <PlanApprove key={sc} action="provider.update.rollback" controlId={`provider.update.rollback.${s.source_id}.${sc}`} params={{ source_id: s.source_id, scope: sc }} label={`roll back ${sc}`} onDone={reload} />
              ) : null,
            )}
            {s.note ? <div style={{ color: "var(--ink-dim)" }}>{s.note}</div> : null}
          </div>
        ))}
      </section>

      <section aria-label="Upstream watchlist" data-control="updates.watchlist" style={{ display: "grid", gap: 4 }}>
        <div style={{ fontWeight: 600 }}>Upstream changes being watched (none is adopted by being listed)</div>
        {d.watchlist.map((w) => (
          <div key={w.id} className="meta" data-control={`updates.watch.${w.id}`}>
            <span className="ops-mono">{w.id}</span> · <b>{w.state}</b>
            {w.label ? ` (${w.label})` : ""}
            {w.base_branch && w.state !== "OPEN" ? ` · into ${w.base_branch}${w.merge_commit ? ` @ ${short(w.merge_commit)}` : ""}` : ""}
            {w.state_source === "OBSERVED" ? "" : " · state configured, not verified against upstream"} · {w.title} — {w.why}. Adopt when: {w.adoption_condition}
          </div>
        ))}
      </section>
    </div>
  );
}
