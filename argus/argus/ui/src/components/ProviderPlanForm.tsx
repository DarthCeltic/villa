import { useEffect, useMemo, useState } from "react";
import { approvedHash, isRefusal, openSession, planGoverned, runGoverned, sessionIsOpen } from "../lib/governed";
import { OpsBadge, OpsDetails, type OpsTone } from "./OpsKit";
import { PlanApprove } from "./PlanApprove";

interface CapabilityGroup {
  callable?: string[];
}

export interface ProviderRecord {
  id: string;
  stage: string;
  role: string;
  revision: string;
  lifecycle: string;
  lifecycle_state?: string;
  family?: string;
  blocker?: { kind: string; text: string } | null;
  hardware_profile?: { state: string; summary: string } | null;
  claim_ceiling?: string;
  plan_action?: string | null;
  invoke_action?: string | null;
  plan_available?: boolean;
}

export interface ProviderPlanInventory {
  capability_ledger?: Record<string, CapabilityGroup>;
  providers?: ProviderRecord[];
}

export const CANDIDATE_PLAN_ACTION = "provider.candidate.plan";

export function lifecycleTone(state: string | undefined): OpsTone {
  if (state === "BLOCKED") return "bad";
  if (state === "PLAN_ONLY" || state === "RESEARCH_ONLY") return "warn";
  if (state === "GATED_EXECUTABLE" || state === "EXECUTABLE") return "info";
  return "idle";
}

export function stateLabel(state: string | undefined): string {
  return state ? state.toLowerCase().replace(/_/g, " ") : "not reported";
}

export function callableCapabilities(inventory: ProviderPlanInventory): string[] {
  const ids = Object.values(inventory.capability_ledger ?? {})
    .flatMap((group) => (group && typeof group === "object" ? group.callable ?? [] : []))
    .filter(Boolean);
  return [...new Set(ids)].sort();
}

export function rowsWithPlanAction(providers: ProviderRecord[] | undefined): ProviderRecord[] {
  return (providers ?? []).filter((p) => Boolean(p.plan_action));
}

function ProviderDoor({ providers }: { providers: ProviderRecord[] | undefined }) {
  const rows = useMemo(() => rowsWithPlanAction(providers), [providers]);
  const [providerId, setProviderId] = useState("");
  const [requestJson, setRequestJson] = useState("{}");

  useEffect(() => {
    const first = rows.find((row) => row.plan_available) ?? rows[0];
    if (first && !rows.some((row) => row.id === providerId)) setProviderId(first.id);
  }, [rows, providerId]);

  const selected = rows.find((row) => row.id === providerId) ?? null;
  const request = useMemo((): { ok: true; value: Record<string, unknown> } | { ok: false; why: string } => {
    try {
      const parsed = JSON.parse(requestJson || "{}");
      if (parsed && !Array.isArray(parsed) && typeof parsed === "object") {
        return { ok: true, value: parsed as Record<string, unknown> };
      }
      return { ok: false, why: "the plan request must be a JSON object" };
    } catch (error) {
      return { ok: false, why: error instanceof Error ? error.message : String(error) };
    }
  }, [requestJson]);
  const viaDoor = selected?.plan_action === CANDIDATE_PLAN_ACTION;

  if (!rows.length) return null;
  return (
    <details style={{ marginTop: 12 }} data-control="system.process.provider-door">
      <summary>Registered providers and their governed door ({rows.length})</summary>
      <p className="ops-note">
        Each row names the one governed action that plans it. A provider with no plan function is still
        asked, and refuses naming what blocks it. Approving a plan here records its hash and runs nothing.
      </p>
      <label className="ops-field">Provider
        <select value={providerId} onChange={(event) => setProviderId(event.target.value)} data-control="system.process.provider-door.select">
          {rows.map((row) => (
            <option key={row.id} value={row.id}>
              {row.id} — {stateLabel(row.lifecycle_state)}{row.plan_available ? "" : " (no plan function)"}
            </option>
          ))}
        </select>
      </label>
      {selected ? (
        <div className="ops-note" data-control="system.process.provider-door.selected">
          <OpsBadge tone={lifecycleTone(selected.lifecycle_state)}>{stateLabel(selected.lifecycle_state)}</OpsBadge>{" "}
          plan <code>{selected.plan_action}</code> · invoke <code>{selected.invoke_action ?? "none"}</code>
          {selected.claim_ceiling ? <> · ceiling {selected.claim_ceiling}</> : null}
          {selected.blocker ? <><br />Blocked by <strong>{selected.blocker.kind}</strong>: {selected.blocker.text}</> : null}
        </div>
      ) : null}
      {selected && !viaDoor ? (
        <p className="ops-note">
          This provider runs through <code>{selected.plan_action}</code>, not this door; use the planner above or
          the governed control for that action.
        </p>
      ) : null}
      {selected && viaDoor ? (
        <>
          <label className="ops-field" style={{ marginTop: 8 }}>Plan request (JSON: the provider's own plan arguments)
            <textarea value={requestJson} onChange={(event) => setRequestJson(event.target.value)} rows={4} spellCheck={false} />
          </label>
          <PlanApprove
            action="provider.candidate.invoke"
            params={{ provider_id: selected.id, request: request.ok ? request.value : {} }}
            label={`${selected.id} plan`}
            why="builds the provider's own plan under its own hash; approving records the hash and then refuses to execute"
            disabledReason={request.ok ? null : request.why}
          />
        </>
      ) : null}
    </details>
  );
}

export function ProviderPlanForm({
  inventory,
  selectedScroll,
}: {
  inventory: ProviderPlanInventory;
  selectedScroll?: string | null;
}) {
  const capabilities = useMemo(() => callableCapabilities(inventory), [inventory]);
  const [capability, setCapability] = useState("");
  const [inputPath, setInputPath] = useState("");
  const [outputPath, setOutputPath] = useState("");
  const [scroll, setScroll] = useState(selectedScroll ?? "");
  const [volumeId, setVolumeId] = useState("");
  const [acquisitionId, setAcquisitionId] = useState("");
  const [optionsJson, setOptionsJson] = useState("{}");
  const [authorizationId, setAuthorizationId] = useState("");
  const [launchPacketJson, setLaunchPacketJson] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [actionPlanHash, setActionPlanHash] = useState<string | null>(null);

  useEffect(() => {
    if (!capabilities.length) return;
    if (!capabilities.includes(capability)) setCapability(capabilities[0] ?? "");
  }, [capabilities, capability]);

  useEffect(() => {
    if (selectedScroll) setScroll(selectedScroll);
  }, [selectedScroll]);

  async function buildPlan() {
    setBusy(true);
    setResult(null);
    let options: Record<string, unknown>;
    try {
      const parsed = JSON.parse(optionsJson || "{}");
      if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
        throw new Error("options must be a JSON object");
      }
      options = parsed;
    } catch (error) {
      setResult({ state: "REFUSED", why: error instanceof Error ? error.message : String(error) });
      setBusy(false);
      return;
    }
    try {
      const query = new URLSearchParams({
        capability_id: capability,
        input_path: inputPath,
        output_path: outputPath,
        physical_scroll: scroll,
        volume_id: volumeId,
        acquisition_id: acquisitionId,
        options_json: JSON.stringify(options),
      });
      const response = await fetch(`/api/providers/plan?${query.toString()}`);
      setResult(await response.json());
      setActionPlanHash(null);
    } catch (error) {
      setResult({ state: "REFUSED", why: error instanceof Error ? error.message : String(error) });
    } finally {
      setBusy(false);
    }
  }

  async function prepareAndRun() {
    setBusy(true);
    setResult(null);
    let options: Record<string, unknown>;
    let launchPacket: Record<string, unknown>;
    try {
      const parsedOptions = JSON.parse(optionsJson || "{}");
      const parsedPacket = JSON.parse(launchPacketJson || "{}");
      if (!parsedOptions || Array.isArray(parsedOptions) || typeof parsedOptions !== "object") {
        throw new Error("typed options must be a JSON object");
      }
      if (!parsedPacket || Array.isArray(parsedPacket) || typeof parsedPacket !== "object") {
        throw new Error("launch packet must be a JSON object");
      }
      options = parsedOptions;
      launchPacket = parsedPacket;
    } catch (error) {
      setResult({ state: "REFUSED", why: error instanceof Error ? error.message : String(error) });
      setBusy(false);
      return;
    }
    try {
      if (!sessionIsOpen()) await openSession();
      const params: Record<string, unknown> = {
        capability_id: capability,
        input_path: inputPath,
        output_path: outputPath,
        source_binding: { physical_scroll: scroll, volume_id: volumeId, acquisition_id: acquisitionId },
        options,
        authorization_id: authorizationId,
        launch_packet: launchPacket,
      };
      const prepared = await planGoverned("provider.invoke", params);
      if (isRefusal(prepared)) {
        setResult({ state: "REFUSED", why: prepared.reason, detail: prepared.detail });
        return;
      }
      setActionPlanHash(prepared.plan_hash);
      if (prepared.plan.ready !== true) {
        setResult({ state: "REFUSED", why: "the governed provider plan is not executable", plan: prepared.plan });
        return;
      }
      const executed = await runGoverned("provider.invoke", {
        ...params,
        approved_plan_sha256: approvedHash(prepared),
      });
      setResult(isRefusal(executed) ? { state: "REFUSED", why: executed.reason, detail: executed.detail } :
        { state: "SUBMITTED", ...executed });
    } catch (error) {
      setResult({ state: "REFUSED", why: error instanceof Error ? error.message : String(error) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <OpsDetails
      control="system.process.provider-plan"
      summary="Developer details: plan a provider run (paths, JSON options, launch packet)"
    >
      <p className="ops-note">
        Read-only preflight for any adapted Villa capability. It checks the real local input,
        requires a new output and binds the material. The governed control below can submit only
        after an explicit launch packet is supplied; it never promotes science.
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 8 }}>
        <label className="ops-field">Capability
          <select value={capability} onChange={(event) => setCapability(event.target.value)} disabled={!capabilities.length}>
            {capabilities.map((id) => <option key={id} value={id}>{id}</option>)}
          </select>
        </label>
        <label className="ops-field">Existing input path
          <input value={inputPath} onChange={(event) => setInputPath(event.target.value)} placeholder="path to an existing local file or folder" />
        </label>
        <label className="ops-field">New output path
          <input value={outputPath} onChange={(event) => setOutputPath(event.target.value)} placeholder="path for a new output folder" />
        </label>
        <label className="ops-field">Physical scroll
          <input value={scroll} onChange={(event) => setScroll(event.target.value)} placeholder="physical scroll id" />
        </label>
        <label className="ops-field">Volume ID
          <input value={volumeId} onChange={(event) => setVolumeId(event.target.value)} placeholder="exact volume identity" />
        </label>
        <label className="ops-field">Acquisition ID
          <input value={acquisitionId} onChange={(event) => setAcquisitionId(event.target.value)} placeholder="exact acquisition identity" />
        </label>
      </div>
      <label className="ops-field" style={{ marginTop: 8 }}>Typed options (JSON)
        <textarea value={optionsJson} onChange={(event) => setOptionsJson(event.target.value)} rows={2} spellCheck={false} />
      </label>
      <button type="button" className="interactive" onClick={buildPlan} disabled={busy || !capability} style={{ marginTop: 8 }}>
        {busy ? "Checking…" : "Build read-only plan"}
      </button>
      <details style={{ marginTop: 12 }}>
        <summary>Prepare and submit a governed provider run</summary>
        <p className="ops-note">
          This is a deliberate operator action. The launch packet is validated again by the
          command service, the plan is hashed first, and execution uses the existing
          CSRF/session/idempotency transport.
        </p>
        <label className="ops-field">Launch authorization ID
          <input value={authorizationId} onChange={(event) => setAuthorizationId(event.target.value)} placeholder="issued authorization" />
        </label>
        <label className="ops-field" style={{ marginTop: 8 }}>Launch packet (JSON)
          <textarea value={launchPacketJson} onChange={(event) => setLaunchPacketJson(event.target.value)} rows={4} spellCheck={false} placeholder='{"runner_rel":"...","modules":[]}' />
        </label>
        <button type="button" className="interactive" onClick={prepareAndRun}
          disabled={busy || !capability || !inputPath || !outputPath || !scroll || !volumeId || !acquisitionId || !authorizationId || !launchPacketJson}
          style={{ marginTop: 8 }}>
          {busy ? "Preparing..." : "Approve plan and submit"}
        </button>
        {actionPlanHash ? <div className="ops-note" style={{ marginTop: 8 }}>approved plan: <code>{actionPlanHash}</code></div> : null}
      </details>
      <ProviderDoor providers={inventory.providers} />
      {result ? (
        <div role="status" className="ops-note" style={{ marginTop: 8, whiteSpace: "pre-wrap" }}>
          <strong>{String(result.state ?? "UNKNOWN")}</strong>{result.why ? ` — ${String(result.why)}` : ""}
          {result.plan_sha256 ? <><br />plan: <code>{String(result.plan_sha256).slice(0, 16)}…</code></> : null}
          {result.execution ? <><br />{String(result.execution)}</> : null}
        </div>
      ) : null}
    </OpsDetails>
  );
}
