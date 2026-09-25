import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle,
  Boxes,
  Cpu,
  FileWarning,
  ListChecks,
  ShieldAlert,
  Terminal,
} from "lucide-react";
import type { FeedState } from "../api";
import { Chip, type Tone } from "../components/Status";
import { routes } from "../lib/nav";
import {
  MODEL_STATUSES,
  STATUS_MEANING,
  STATUS_TONE,
  VOCABULARY_MAP,
  buildModelsView,
  isKnown,
  reproductionFor,
  unknownReason,
  type LocalCapability,
  type ModelRow,
  type ModelStatus,
  type ModelsView,
  type Triple,
} from "../lib/models";
import { fetchReceipts, type ReceiptsState } from "../lib/receipts";
import { Disclosure } from "../components/Disclosure";

interface OversightGpu {
  present: boolean;
  why?: string;
  consequence?: string;
  cards?: { name: string; vram_total_mib: number | null; driver: string }[];
}

export function Models({ feed, embedded = false }: { feed?: FeedState; embedded?: boolean }) {
  const [rec, setRec] = useState<ReceiptsState>({
    index: null,
    failure: null,
    settled: false,
  });
  const [gpu, setGpu] = useState<OversightGpu | null>(null);

  useEffect(() => {
    let live = true;
    fetchReceipts().then((s) => live && setRec(s));
    fetch("/api/oversight")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => live && d && setGpu(d.gpu as OversightGpu))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);

  const capability: LocalCapability = useMemo(
    () => ({
      gpuPresent: gpu ? gpu.present : null,
      gpuDetail: gpu
        ? gpu.present
          ? (gpu.cards ?? [])
              .map(
                (c) =>
                  c.name +
                  (c.vram_total_mib
                    ? ` (${(c.vram_total_mib / 1024).toFixed(1)} GiB VRAM)`
                    : ""),
              )
              .join(", ") || "an accelerator is visible but reported no name"
          : (gpu.consequence ?? gpu.why ?? "nvidia-smi reported nothing")
        : "oversight has not answered yet",
    }),
    [gpu],
  );

  const detectorRuns = useMemo(() => {
    const m = new Map<string, string[]>();
    for (const r of feed?.data?.runs ?? []) {
      const name = r.detector?.name;
      if (!name) continue;
      const list = m.get(name);
      if (list) list.push(r.run_id);
      else m.set(name, [r.run_id]);
    }
    return m;
  }, [feed?.data]);

  const view = useMemo(
    () => buildModelsView(rec.index, capability, detectorRuns),
    [rec.index, capability, detectorRuns],
  );

  return (
    <div
      style={{
        padding: embedded ? 0 : "20px 22px 48px",
        display: "grid",
        gap: embedded ? 12 : 18,
        alignContent: "start",
        maxWidth: 1180,
        minWidth: 0,
      }}
    >
      <header style={{ display: "grid", gap: 8 }}>
        {embedded ? null : <h1>Models</h1>}
        <p className="muted" style={{ margin: 0, maxWidth: 780 }}>
          Every detector this project can reach, what each has already seen, and whether a
          frozen evaluation has spoken about it. Each fact carries the class of evidence
          that established it; a fact nobody established is printed as the gap it is.
        </p>
        <Freshness rec={rec} view={view} />
      </header>

      {!rec.settled ? (
        <div className="panel" style={{ padding: 18 }}>
          <span className="muted">Asking the service for the receipt index…</span>
        </div>
      ) : !view.registryPresent ? (
        <RegistryMissing rec={rec} view={view} />
      ) : (
        <>
          <StatusVocabulary view={view} />
          {embedded ? (
            <Disclosure
              className="ops-details"
              data-control="models.ladder.disclosure"
              summary={
                view.ladder
                  ? `Decision ladder (frozen by ${view.ladderId}) and verdict inputs`
                  : "Decision ladder: no frozen protocol is on disk"
              }
            >
              <div className="ops-details-body">
                <Ladder view={view} />
                <VerdictChain view={view} />
              </div>
            </Disclosure>
          ) : (
            <>
              <Ladder view={view} />
              <VerdictChain view={view} />
            </>
          )}
          <section style={{ display: "grid", gap: 14 }}>
            <div
              style={{ display: "flex", gap: 12, alignItems: "baseline", flexWrap: "wrap" }}
            >
              <h2 className="eyebrow">
                <Boxes size={13} aria-hidden /> {view.rows.length} model families
              </h2>
              {
}
              <span className="meta">
                {view.checkpointsHashed === null || view.checkpointsHashed === undefined
                  ? "the registry does not state how many checkpoint files are hashed"
                  : `${view.checkpointsHashed} checkpoint files hashed`}
                {" · "}
                {view.unknownCount === null || view.unknownCount === undefined
                  ? "it does not count its own declared gaps"
                  : `${view.unknownCount} declared gaps`}
              </span>
            </div>
            {view.rows.map((r) => (
              <ModelCard key={r.id} row={r} rec={rec} collapse={embedded} />
            ))}
          </section>
        </>
      )}
    </div>
  );
}

function Freshness({ rec, view }: { rec: ReceiptsState; view: ModelsView }) {
  if (!rec.settled) return null;
  if (!rec.index)
    return (
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <Chip tone="refused" size="sm">
          receipt index unavailable
        </Chip>
        <span className="meta">
          {rec.failure?.status ? `HTTP ${rec.failure.status} — ` : ""}
          {rec.failure?.detail}
        </span>
      </div>
    );
  return (
    <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
      <Chip tone="active" size="sm">
        receipt index
      </Chip>
      <span className="meta">
        indexed by the service{" "}
        {view.indexAgeS === null
          ? "at an unreported time"
          : `${Math.round(view.indexAgeS)}s ago`}
        {rec.index.ttl_s ? `, rebuilt every ${rec.index.ttl_s}s` : ""}
        {view.registryUtc ? ` · registry written ${view.registryUtc}` : ""}
      </span>
      {view.redactions ? (
        <Chip
          tone="blocked"
          size="sm"
          title="absolute paths and credential-shaped values are replaced by the service before they reach the browser"
        >
          {view.redactions} value(s) withheld in transit
        </Chip>
      ) : null}
    </div>
  );
}

function RegistryMissing({ rec, view }: { rec: ReceiptsState; view: ModelsView }) {
  const wanted = [
    ["model_registry", "artifacts/registries/models.json"],
  ];
  const sealed = view.registrySealed;
  return (
    <section
      className="panel"
      style={{
        padding: 20,
        display: "grid",
        gap: 12,
        borderColor: sealed ? "var(--status-active-edge)" : "var(--status-blocked-edge)",
      }}
    >
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <FileWarning size={16} aria-hidden style={{ color: "var(--status-blocked)" }} />
        <h2 className="eyebrow" style={{ color: "var(--ink)" }}>
          {sealed
            ? "the model registry is under an active seal"
            : rec.index
              ? "the model registry has not been written"
              : "the service is not serving a receipt index"}
        </h2>
      </div>
      <p className="small muted" style={{ margin: 0, maxWidth: 780 }}>
        {sealed
          ? "The service is withholding the bytes because this receipt sits under an active blinding marker. That is the seal working, not a fault."
          : rec.index
            ? "The service answered and does not hold this receipt. The stage that writes it has not run."
            : "This screen reads receipts from the ARGUS service and holds no copy of its own — a bundled copy would be stale the moment a stage wrote a new one. Until the route answers, nothing below can be shown, and nothing is guessed in its place."}
      </p>
      <div style={{ display: "grid", gap: 6 }}>
        <span className="eyebrow">Receipts this screen reads</span>
        {wanted.map(([key, path]) => {
          const env = rec.index?.receipts?.[key as string];
          return (
            <div
              key={key}
              style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}
            >
              <Chip tone={env?.present ? "active" : "blocked"} size="sm">
                {env?.present ? "present" : "absent"}
              </Chip>
              <code className="mono">{env?.relpath ?? path}</code>
              {env?.missing_reason ? (
                <span className="meta">{env.missing_reason}</span>
              ) : null}
            </div>
          );
        })}
      </div>
      {!rec.index ? (
        <p className="small faint" style={{ margin: 0, maxWidth: 780 }}>
          The route is <code className="mono">GET /api/receipts</code>. It is read-only like
          every other route on this service, resolves its roots through the path contract,
          and refuses anything under an active seal.
        </p>
      ) : null}
    </section>
  );
}

function StatusVocabulary({ view }: { view: ModelsView }) {
  const counts = new Map<ModelStatus, number>();
  for (const r of view.rows) counts.set(r.status, (counts.get(r.status) ?? 0) + 1);
  return (
    <section className="panel" style={{ padding: 18, display: "grid", gap: 14 }}>
      <h2 className="eyebrow">
        <ListChecks size={13} aria-hidden /> Status vocabulary
      </h2>
      <div
        style={{
          display: "grid",
          gap: 10,
          gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 330px), 1fr))",
        }}
      >
        {MODEL_STATUSES.map((s) => {
          const n = counts.get(s) ?? 0;
          return (
            <div
              key={s}
              style={{
                display: "grid",
                gap: 6,
                padding: "10px 12px",
                borderRadius: "var(--radius-sm)",
                border: "1px solid var(--line)",
                background: n ? "var(--bg-raised-2)" : "transparent",
                opacity: n ? 1 : 0.62,
              }}
            >
              <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                <Chip tone={STATUS_TONE[s] as Tone} size="sm">
                  {s.replace(/_/g, " ")}
                </Chip>
                <span className="mono" style={{ color: "var(--ink-dim)" }}>
                  {n} {n === 1 ? "model" : "models"}
                </span>
              </div>
              <span className="small faint">{STATUS_MEANING[s]}</span>
            </div>
          );
        })}
      </div>

      <details data-control="models.vocabulary">
        {
}
        <summary className="small muted" data-control="models.vocabulary.toggle">
          How the registry&rsquo;s terms map onto these {MODEL_STATUSES.length}
        </summary>
        <div style={{ display: "grid", gap: 6, paddingTop: 10 }}>
          {Object.entries(VOCABULARY_MAP).map(([term, m]) => (
            <div
              key={term}
              style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}
            >
              <code className="mono" style={{ minWidth: 140 }}>
                {term}
              </code>
              <span aria-hidden className="faint">
                &rarr;
              </span>
              <Chip tone={STATUS_TONE[m.to] as Tone} size="sm">
                {m.to.replace(/_/g, " ")}
              </Chip>
              <span className="small faint">{m.why}</span>
            </div>
          ))}
          {view.registryVocabulary ? (
            <p className="small faint" style={{ margin: "6px 0 0" }}>
              The registry defines its own terms; those definitions travel with the receipt
              and are not restated here, so the two cannot drift.
            </p>
          ) : null}
        </div>
      </details>

      {view.unmappedTerms.length ? (
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <Chip tone="blocked" size="sm">
            unmapped
          </Chip>
          <span className="small">
            the registry used {view.unmappedTerms.join(", ")}, which this interface has no
            translation for. Shown as UNTESTED and reported here rather than being quietly
            absorbed.
          </span>
        </div>
      ) : null}
    </section>
  );
}

function Ladder({ view }: { view: ModelsView }) {
  if (!view.ladder)
    return (
      <section className="panel" style={{ padding: 18, display: "grid", gap: 8 }}>
        <h2 className="eyebrow">Decision ladder</h2>
        <span className="small muted">
          No frozen protocol is on disk, so no ladder can be shown. Until one exists, a
          status here can only come from the registry&rsquo;s own record — never from a
          measured verdict, because none has been frozen to measure against.
        </span>
      </section>
    );
  return (
    <section className="panel" style={{ padding: 18, display: "grid", gap: 12 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
        <h2 className="eyebrow">Decision ladder</h2>
        <span className="meta">frozen by {view.ladderId} before the evaluation ran</span>
      </div>
      <p className="small faint" style={{ margin: 0, maxWidth: 800 }}>
        The boundaries are numbers, fixed in advance. That is what makes a verdict a
        measurement rather than an opinion formed after seeing the result.
      </p>
      <div style={{ display: "grid", gap: 10 }}>
        {Object.entries(view.ladder).map(([name, l]) =>
          typeof l === "string" ? (
            <p key={name} className="small faint" style={{ margin: 0, maxWidth: 820 }}>
              <strong className="mono">{name}</strong> — {l}
            </p>
          ) : (
            <div
              key={name}
              style={{
                display: "grid",
                gap: 4,
                paddingLeft: 12,
                borderLeft: "2px solid var(--line-strong)",
              }}
            >
              <strong className="mono" style={{ fontSize: "var(--t-small)" }}>
                {name}
              </strong>
              <span className="small">{l.condition}</span>
              <span className="small faint">then: {l.next}</span>
            </div>
          ),
        )}
      </div>
    </section>
  );
}

function VerdictChain({ view }: { view: ModelsView }) {
  if (!view.verdictChain.length) return null;
  return (
    <section className="panel" style={{ padding: 18, display: "grid", gap: 10 }}>
      <h2 className="eyebrow">
        <ShieldAlert size={13} aria-hidden /> Verdict inputs
      </h2>
      {view.verdictChain.map((c) => (
        <div
          key={c.name}
          style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}
        >
          <Chip tone={c.match ? "certified" : "blocked"} size="sm">
            {c.match ? "hash matches" : c.onDisk ? "hash differs" : "not on disk"}
          </Chip>
          <span className="small">{c.name}</span>
          <span className="meta">
            cited {c.cited.slice(0, 16)}
            {c.onDisk ? ` · on disk ${c.onDisk}` : ""}
          </span>
        </div>
      ))}
      <span className="small faint">
        The verdict names its inputs by hash. This compares those names against what the
        service reports is present, so a silently replaced input is visible rather than
        assumed away.
      </span>
    </section>
  );
}

function ModelCard({
  row,
  rec,
  collapse = false,
}: {
  row: ModelRow;
  rec: ReceiptsState;
  collapse?: boolean;
}) {
  const tone = STATUS_TONE[row.status] as Tone;
  const repro = reproductionFor(rec.index, "model_registry");
  return (
    <article
      className="panel"
      style={{
        padding: 18,
        display: "grid",
        gap: 14,
        borderLeft: `3px solid var(--status-${tone})`,
      }}
    >
      <div style={{ display: "flex", gap: 12, alignItems: "baseline", flexWrap: "wrap" }}>
        <h3 className="display" style={{ fontSize: "var(--t-h2)" }}>
          {row.id}
        </h3>
        <Chip tone={tone}>{row.status.replace(/_/g, " ")}</Chip>
        {row.repo ? <span className="meta">{row.repo}</span> : null}
      </div>

      <p className="small" style={{ margin: 0, maxWidth: 840 }}>
        <strong className="faint">because </strong>
        {row.because}
        {row.registryTerm ? (
          <span className="meta">
            {" "}
            · registry term {row.registryTerm}
            {row.becauseSource ? `, established by ${row.becauseSource}` : ""}
          </span>
        ) : null}
      </p>

      <RunnableHere row={row} />
      {collapse ? (
        <Disclosure
          className="ops-details"
          data-control={`models.card.${row.id}.evidence`}
          summary={`Evidence for ${row.id}: facts, ${row.traps.length ? "traps, " : ""}exposure, controls, held-out, checkpoints, reproduction`}
        >
          <div className="ops-details-body">
            <CardEvidence row={row} repro={repro} />
          </div>
        </Disclosure>
      ) : (
        <CardEvidence row={row} repro={repro} />
      )}
    </article>
  );
}

function CardEvidence({ row, repro }: { row: ModelRow; repro: string[] }) {
  return (
    <>
      <Facts row={row} />
      {row.traps.length ? <Traps row={row} /> : null}
      <Exposure row={row} />
      <Controls row={row} />
      <HeldOut row={row} />
      {row.knownFailures.length ? (
        <Block label="Known failures">
          <ul style={{ margin: 0, paddingLeft: 18, display: "grid", gap: 4 }}>
            {row.knownFailures.map((f) => (
              <li key={f} className="small">
                {f}
              </li>
            ))}
          </ul>
        </Block>
      ) : null}
      <Checkpoints row={row} />
      <Reproduction commands={repro} />
      {row.declaredByRuns.length ? (
        <Block
          label={`Declared by ${row.declaredByRuns.length} run${row.declaredByRuns.length === 1 ? "" : "s"} in the feed`}
        >
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {row.declaredByRuns.map((id) => (
              <Link
                key={id}
                className="mono"
                to={routes.workbench(id)}
                data-control={`models.run.open.${id}`}
              >
                {id}
              </Link>
            ))}
          </div>
        </Block>
      ) : null}
    </>
  );
}

function RunnableHere({ row }: { row: ModelRow }) {
  const r = row.runnableHere;
  const tone: Tone = r.ok ? "certified" : "blocked";
  return (
    <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
      <Cpu size={13} aria-hidden style={{ color: "var(--ink-faint)" }} />
      <Chip tone={tone} size="sm">
        {r.ok === null
          ? "runnable here: unmeasured"
          : r.ok
            ? "runnable here"
            : "not runnable here"}
      </Chip>
      <span className="small muted">{r.because}</span>
    </div>
  );
}

function Fact({ label, t }: { label: string; t: Triple<unknown> | null }) {
  const known = isKnown(t);
  return (
    <div style={{ display: "grid", gap: 3 }}>
      <span className="eyebrow">{label}</span>
      {known ? (
        <>
          <span className="small">{render(t.value)}</span>
          <span className="meta">
            {t.source}
            {t.evidence ? ` · ${t.evidence}` : ""}
          </span>
        </>
      ) : (
        <span className="small" style={{ color: "var(--status-blocked)" }}>
          not established — {unknownReason(t)}
        </span>
      )}
    </div>
  );
}

function render(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (Array.isArray(v)) return v.map(render).join(", ");
  return Object.entries(v as Record<string, unknown>)
    .map(([k, x]) => `${k.replace(/_/g, " ")}: ${render(x)}`)
    .join(" · ");
}

function Facts({ row }: { row: ModelRow }) {
  return (
    <div
      style={{
        display: "grid",
        gap: 12,
        gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 320px), 1fr))",
        paddingTop: 12,
        borderTop: "1px solid var(--line-soft)",
      }}
    >
      <Fact label="role" t={row.role} />
      <Fact label="representation" t={row.representation} />
      <Fact label="pitch (micrometres)" t={row.pitch} />
      <Fact label="axes" t={row.axes} />
      <Fact label="patch geometry" t={row.patchGeometry} />
      <Fact label="normalization" t={row.normalization} />
      <Fact label="output semantics" t={row.outputSemantics} />
      <Fact label="architecture" t={row.architecture} />
      <Fact label="hardware" t={row.hardware as Triple<unknown> | null} />
    </div>
  );
}

function Traps({ row }: { row: ModelRow }) {
  return (
    <Block label="Traps">
      <div style={{ display: "grid", gap: 8 }}>
        {row.traps.map((t) => (
          <div
            key={t.label}
            style={{
              display: "grid",
              gap: 3,
              paddingLeft: 12,
              borderLeft: "2px solid var(--status-blocked-edge)",
            }}
          >
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <AlertTriangle
                size={13}
                aria-hidden
                style={{ color: "var(--status-blocked)" }}
              />
              <span className="eyebrow">{t.label}</span>
            </div>
            <span className="small">{render(t.t.value)}</span>
            <span className="meta">
              {t.t.source}
              {t.t.evidence ? ` · ${t.t.evidence}` : ""}
            </span>
          </div>
        ))}
      </div>
    </Block>
  );
}

function Exposure({ row }: { row: ModelRow }) {
  return (
    <Block label="Exposure — which scrolls this model has already seen, by channel">
      {row.exposure.length ? (
        <div style={{ display: "grid", gap: 10 }}>
          {row.exposure.map((c) => (
            <Fact key={c.channel} label={c.channel.replace(/_/g, " ")} t={c.t} />
          ))}
        </div>
      ) : (
        <span className="small" style={{ color: "var(--status-blocked)" }}>
          the registry records no exposure for this family, so no holdout can be called clean
          for it
        </span>
      )}
    </Block>
  );
}

function Controls({ row }: { row: ModelRow }) {
  return (
    <Block label="Controls">
      <div style={{ display: "grid", gap: 10 }}>
        {row.controls.map((c) => (
          <Fact key={c.label} label={c.label.replace(/_/g, " ")} t={c.t} />
        ))}
      </div>
    </Block>
  );
}

function HeldOut({ row }: { row: ModelRow }) {
  return (
    <Block label="Held-out performance">
      <div style={{ display: "grid", gap: 10 }}>
        {row.heldOut.map((h) => (
          <Fact key={h.scroll} label={h.scroll} t={h.t} />
        ))}
      </div>
    </Block>
  );
}

function Checkpoints({ row }: { row: ModelRow }) {
  if (!row.checkpoints.length)
    return (
      <Block label="Checkpoints">
        <span className="small" style={{ color: "var(--status-blocked)" }}>
          {row.weightsPresent && row.weightsPresent.value === false
            ? `no weights on this machine — ${row.weightsPresent.evidence ?? "measured against the model root"}`
            : "no checkpoint file is catalogued for this family"}
        </span>
      </Block>
    );
  return (
    <Block label={`Checkpoints — ${row.checkpoints.length} hashed`}>
      <div style={{ overflowX: "auto" }}>
        <table style={{ borderCollapse: "collapse", minWidth: 620 }}>
          <thead>
            <tr>
              {["file", "step", "seed", "params", "sha256", "standing"].map((h) => (
                <th
                  key={h}
                  className="eyebrow"
                  style={{
                    textAlign: "left",
                    padding: "4px 14px 6px 0",
                    borderBottom: "1px solid var(--line)",
                    whiteSpace: "nowrap",
                  }}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {row.checkpoints.map((c) => (
              <tr key={c.path}>
                <td className="mono" style={{ padding: "5px 14px 5px 0" }}>
                  {c.path.split("/").slice(-2).join("/")}
                </td>
                <td className="mono" style={{ padding: "5px 14px 5px 0" }}>
                  {c.step ?? ""}
                </td>
                <td className="mono" style={{ padding: "5px 14px 5px 0" }}>
                  {c.seed ?? ""}
                </td>
                <td className="mono" style={{ padding: "5px 14px 5px 0" }}>
                  {c.n_params ? c.n_params.toLocaleString() : ""}
                </td>
                <td className="mono" style={{ padding: "5px 14px 5px 0" }}>
                  {c.sha256 ? c.sha256.slice(0, 16) : ""}
                </td>
                <td className="small" style={{ padding: "5px 14px 5px 0" }}>
                  {c.qualification ?? ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Block>
  );
}

function Reproduction({ commands }: { commands: string[] }) {
  return (
    <Block label="Reproduction">
      {commands.length ? (
        <Disclosure className="ops-details" summary="Reproduction command">
          <pre
            className="mono"
            style={{
              margin: 0,
              padding: "10px 12px",
              background: "var(--bg-sunken)",
              border: "1px solid var(--line)",
              borderRadius: "var(--radius-sm)",
              overflowX: "auto",
              whiteSpace: "pre",
            }}
          >
            {commands.join("\n")}
          </pre>
          <div
            className="small faint"
            style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 6 }}
          >
            <Terminal size={13} aria-hidden />
            shown as text: this interface is observational and cannot run it
          </div>
        </Disclosure>
      ) : (
        <span className="small faint">
          The service records no script for this receipt, so the command that regenerates it
          is unrecorded. A plausible-looking command would be worse than none.
        </span>
      )}
    </Block>
  );
}

function Block({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <section
      style={{
        display: "grid",
        gap: 6,
        paddingTop: 12,
        borderTop: "1px solid var(--line-soft)",
      }}
    >
      <span className="eyebrow">{label}</span>
      {children}
    </section>
  );
}
