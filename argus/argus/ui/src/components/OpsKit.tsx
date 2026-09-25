import { useId, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { failureLine, type Failure } from "../lib/http";


export type OpsTone = "ok" | "warn" | "bad" | "info" | "idle";

const GLYPH: Record<OpsTone, string> = {
  ok: "✓",
  warn: "!",
  bad: "✕",
  info: "◆",
  idle: "·",
};


export function OpsPage({
  title,
  lede,
  children,
  control,
}: {
  title: string;
  lede?: ReactNode;
  children: ReactNode;
  control: string;
}) {
  return (
    <div className="ops-page" data-control={`${control}.page`}>
      <header className="ops-head">
        {
}
        <h1 data-novice="looking">{title}</h1>
        {lede ? <p className="ops-lede">{lede}</p> : null}
      </header>
      {children}
    </div>
  );
}

export function OpsHeadline({
  tone,
  what,
  detail,
  children,
  control,
}: {
  tone: OpsTone;
  what: ReactNode;
  detail?: ReactNode;
  children?: ReactNode;
  control?: string;
}) {
  return (
    <section
      className="ops-headline"
      data-tone={tone}
      data-control={control}
      aria-live="polite"
    >
      <div className="ops-badges">
        <OpsBadge tone={tone}>{tone === "ok" ? "clear" : tone === "bad" ? "blocked" : tone === "warn" ? "attention" : tone === "info" ? "live" : "unclaimed"}</OpsBadge>
        <strong>{what}</strong>
      </div>
      {detail ? <div className="ops-note">{detail}</div> : null}
      {children}
    </section>
  );
}

export function OpsSection({
  title,
  hint,
  aside,
  children,
  control,
  tone,
  collapsible = false,
}: {
  title: ReactNode;
  hint?: ReactNode;
  aside?: ReactNode;
  children: ReactNode;
  control: string;
  tone?: OpsTone;
  collapsible?: boolean;
}) {
  const bar = (
    <div className="ops-sec-bar">
      <div className="ops-sec-head">
        <h2>{title}</h2>
        {hint ? <div className="ops-hint">{hint}</div> : null}
      </div>
      {aside ? <div className="ops-badges">{aside}</div> : null}
    </div>
  );
  if (collapsible) {
    return (
      <section id={control} className="ops-sec ops-sec-collapsible" data-control={control} data-tone={tone}>
        <details>
          <summary>{bar}</summary>
          <div className="ops-sec-body">{children}</div>
        </details>
      </section>
    );
  }
  return (
    <section id={control} className="ops-sec" data-control={control} data-tone={tone}>
      {bar}
      {children}
    </section>
  );
}


export function OpsBadge({
  tone,
  children,
  title,
  control,
}: {
  tone: OpsTone;
  children: ReactNode;
  title?: string;
  control?: string;
}) {
  return (
    <span className="ops-badge" data-tone={tone} title={title} data-control={control}>
      <span className="ops-badge-glyph" aria-hidden>
        {GLYPH[tone]}
      </span>
      {children}
    </span>
  );
}

export function OpsFixture({ why }: { why?: string | null }) {
  return (
    <span
      className="ops-fixture"
      title={why ?? "this record describes the test corpus, not any physical scroll"}
      data-control="ops.fixture"
    >
      fixture
    </span>
  );
}


export function OpsSource({
  route,
  field,
  note,
}: {
  route: string;
  field?: string;
  note?: string;
}) {
  return (
    <details className="ops-source-details">
      <summary>Source</summary>
      <div className="ops-src">
        {route}
        {field ? ` → ${field}` : ""}
        {note ? ` · ${note}` : ""}
      </div>
    </details>
  );
}

export function OpsSources({ items }: { items: { route: string; field?: string }[] }) {
  return (
    <details className="ops-source-details">
      <summary>Sources ({items.length})</summary>
      <ul className="ops-src-list">
        {items.map((i) => (
          <li key={i.route + (i.field ?? "")} className="ops-src">
            {i.route}
            {i.field ? ` → ${i.field}` : ""}
          </li>
        ))}
      </ul>
    </details>
  );
}


const ROUTE_NAMES: [string, string][] = [
  ["ingest/plan", "the staged-holdings plan"],
  ["ingest", "the ingest plan"],
  ["scroll_status", "the scroll status"],
  ["scrolls", "the scroll index"],
  ["receipts", "the receipts index"],
  ["storage", "the storage catalogue"],
  ["targets", "the target freeze"],
  ["sources", "the upstream registry"],
  ["surfaces", "the status surfaces"],
  ["control_evidence_kinds", "the evidence-kind rules"],
  ["providers", "the provider inventory"],
  ["runs", "the run list"],
  ["jobs", "the job list"],
  ["unroll", "the review-task queue"],
  ["upstream", "the upstream status"],
];
const ROUTE_RE = /\/api\/[A-Za-z0-9_/]+(?:\?[^\s,;)]*)?/g;

export function humanize(text: string): { human: string; technical: string | null } {
  let touched = false;
  const human = text.replace(ROUTE_RE, (m) => {
    touched = true;
    const path = (m.split("?")[0] ?? m).replace(/^\/api\//, "");
    const hit = ROUTE_NAMES.find(([r]) => path === r || path.startsWith(r + "/"));
    return hit ? hit[1] : "the service";
  });
  if (!touched) return { human: text, technical: null };
  return { human: human.replace(/^the /, "The "), technical: text };
}

export function TechnicalDetails({ text }: { text: string | null }) {
  if (!text) return null;
  return (
    <details className="ops-source-details">
      <summary>Technical details</summary>
      <div className="ops-src">{text}</div>
    </details>
  );
}

export function OpsUnknown({
  what,
  why,
  next,
  nextTo,
  nextControl,
}: {
  what: string;
  why: ReactNode;
  next?: ReactNode;
  nextTo?: string;
  nextControl?: string;
}) {
  const w = humanize(what);
  const y = typeof why === "string" ? humanize(why) : { human: why, technical: null as string | null };
  const technical = [w.technical, y.technical].filter(Boolean).join(" · ") || null;
  return (
    <div className="ops-item" data-tone="idle">
      <div className="ops-item-head">
        <OpsBadge tone="idle">not available</OpsBadge>
        <span className="ops-item-title">{w.human}</span>
      </div>
      <div className="ops-item-body">{y.human}</div>
      <TechnicalDetails text={technical} />
      {next && nextTo ? (
        <div className="ops-item-body">
          <Link className="ag-btn interactive ops-next-action" to={nextTo} data-control={nextControl}>
            {next}
          </Link>
        </div>
      ) : next ? (
        <div className="ops-item-body">
          <strong>To establish it: </strong>
          {next}
        </div>
      ) : null}
    </div>
  );
}

export function OpsFailure({
  what,
  failure,
  onRetry,
  control,
}: {
  what: string;
  failure: Failure;
  onRetry?: () => void;
  control: string;
}) {
  const w = humanize(what);
  const f = humanize(failureLine(failure));
  const technical = [w.technical, failureLine(failure)].filter(Boolean).join(" · ");
  return (
    <div className="ops-item" data-tone="bad">
      <div className="ops-item-head">
        <OpsBadge tone="bad">read failed</OpsBadge>
        <span className="ops-item-title">{w.human}</span>
      </div>
      <div className="ops-item-body">{f.technical ? f.human : "This could not be read just now; nothing is inferred from the gap."}</div>
      <TechnicalDetails text={technical} />
      {onRetry && failure.retryable ? (
        <button className="ops-btn" onClick={onRetry} data-control={`${control}.retry`}>
          read again
        </button>
      ) : null}
    </div>
  );
}


export function OpsDisabled({
  label,
  reason,
  control,
  weight = "normal",
}: {
  label: ReactNode;
  reason: ReactNode;
  control: string;
  weight?: "normal" | "primary" | "destructive";
}) {
  const id = useId();
  return (
    <div className="ops-control">
      <button
        type="button"
        className="ops-btn"
        data-weight={weight === "normal" ? undefined : weight}
        aria-disabled="true"
        aria-describedby={id}
        data-control={control}
        data-disabled-reason="true"
        onClick={(e) => e.preventDefault()}
      >
        {label}
      </button>
      <span className="ops-control-reason" id={id}>
        {reason}
      </span>
    </div>
  );
}

export function OpsLink({
  to,
  children,
  control,
  weight = "normal",
}: {
  to: string;
  children: ReactNode;
  control: string;
  weight?: "normal" | "primary";
}) {
  return (
    <a
      className="ops-btn"
      href={to}
      data-weight={weight === "normal" ? undefined : weight}
      data-control={control}
    >
      {children}
    </a>
  );
}

export function OpsButton({
  onClick,
  children,
  control,
  weight = "normal",
}: {
  onClick: () => void;
  children: ReactNode;
  control: string;
  weight?: "normal" | "primary";
}) {
  return (
    <button
      type="button"
      className="ops-btn"
      data-weight={weight === "normal" ? undefined : weight}
      onClick={onClick}
      data-control={control}
    >
      {children}
    </button>
  );
}

export function OpsConfirm({
  label,
  willDo,
  onConfirm,
  control,
}: {
  label: string;
  willDo: string;
  onConfirm: () => void;
  control: string;
}) {
  const [armed, setArmed] = useState(false);
  if (!armed) {
    return (
      <div className="ops-control">
        <button
          type="button"
          className="ops-btn"
          data-weight="destructive"
          onClick={() => setArmed(true)}
          data-control={control}
        >
          {label}
        </button>
        <span className="ops-control-reason">{willDo}</span>
      </div>
    );
  }
  return (
    <div className="ops-control">
      <div className="ops-controls">
        <button
          type="button"
          className="ops-btn"
          data-weight="destructive"
          onClick={() => {
            setArmed(false);
            onConfirm();
          }}
          data-control={`${control}.confirm`}
        >
          Confirm: {label}
        </button>
        <button
          type="button"
          className="ops-btn"
          onClick={() => setArmed(false)}
          data-control={`${control}.cancel`}
        >
          Keep everything as it is
        </button>
      </div>
      <span className="ops-control-reason">{willDo} This cannot be undone from here.</span>
    </div>
  );
}


export function OpsFacts({ children }: { children: ReactNode }) {
  return <dl className="ops-kv">{children}</dl>;
}

export function OpsFact({
  label,
  children,
}: {
  label: ReactNode;
  children: ReactNode;
}) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </>
  );
}

export function OpsStat({
  label,
  value,
  source,
  tone,
  toneLabel,
  fixture,
  control,
}: {
  label: ReactNode;
  value: ReactNode;
  source?: string;
  tone?: OpsTone;
  toneLabel?: string;
  fixture?: boolean;
  control?: string;
}) {
  return (
    <div className="ops-stat" data-control={control}>
      <span className="ops-stat-value">{value === null || value === undefined ? "unknown" : value}</span>
      <span className="ops-stat-label">
        {label}
        {fixture ? <> <OpsFixture /></> : null}
      </span>
      {tone ? (
        <span className="ops-badges">
          <OpsBadge tone={tone}>
            {toneLabel ??
              (tone === "ok"
                ? "clear"
                : tone === "warn"
                  ? "needs attention"
                  : tone === "bad"
                    ? "blocked"
                    : tone === "info"
                      ? "live"
                      : "unclaimed")}
          </OpsBadge>
        </span>
      ) : null}
      {source ? (
        <details className="ops-source-details">
          <summary>Source</summary>
          <div className="ops-src">{source}</div>
        </details>
      ) : null}
    </div>
  );
}

export function OpsItem({
  tone,
  title,
  state,
  badges,
  children,
  control,
  id,
}: {
  tone: OpsTone;
  title: ReactNode;
  state?: ReactNode;
  badges?: ReactNode;
  children?: ReactNode;
  control?: string;
  id?: string;
}) {
  return (
    <li id={id} className="ops-item" data-tone={tone} data-control={control}>
      <div className="ops-item-head">
        <span className="ops-item-title">{title}</span>
        {state ? <OpsBadge tone={tone}>{state}</OpsBadge> : null}
        {badges}
      </div>
      {children}
    </li>
  );
}

export function OpsDetails({
  summary,
  children,
  control,
}: {
  summary: ReactNode;
  children: ReactNode;
  control: string;
}) {
  return (
    <details className="ops-details" data-control={control}>
      <summary data-control={`${control}.toggle`}>{summary}</summary>
      <div className="ops-details-body">{children}</div>
    </details>
  );
}

export function OpsQuote({ children, cite }: { children: ReactNode; cite?: string }) {
  return (
    <blockquote className="ops-quote">
      {children}
      {cite ? (
        <details className="ops-source-details">
          <summary>Source</summary>
          <div className="ops-src">{cite}</div>
        </details>
      ) : null}
    </blockquote>
  );
}

export function OpsMeter({
  fraction,
  tone,
  label,
}: {
  fraction: number | null;
  tone?: OpsTone;
  label: string;
}) {
  if (fraction === null) return null;
  const pct = Math.max(0, Math.min(100, Math.round(fraction * 100)));
  return (
    <div className="ops-meter" role="img" aria-label={`${label}: ${pct}%`}>
      <div className="ops-meter-fill" data-tone={tone} style={{ width: `${pct}%` }} />
    </div>
  );
}

export function OpsTable({ children, control }: { children: ReactNode; control: string }) {
  return (
    <div className="ops-scroll-x" data-control={control}>
      <table className="ops-table">{children}</table>
    </div>
  );
}


export function bytesLabel(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "unknown";
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KiB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MiB`;
  if (n < 1024 ** 4) return `${(n / 1024 ** 3).toFixed(2)} GiB`;
  return `${(n / 1024 ** 4).toFixed(2)} TiB`;
}

export function elapsedLabel(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return "age unknown";
  const s = Math.max(0, seconds);
  if (s < 90) return `${Math.round(s)}s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  if (s < 172800) return `${Math.round(s / 3600)} h`;
  return `${Math.round(s / 86400)} days`;
}

export function agoLabel(seconds: number | null | undefined): string {
  const l = elapsedLabel(seconds);
  return l === "age unknown" ? l : `${l} ago`;
}

export function freshnessTone(stale: boolean | null): OpsTone {
  if (stale === null) return "idle";
  return stale ? "warn" : "ok";
}

export function coordText(v: unknown): string {
  if (v === null || v === undefined) return "not recorded";
  if (typeof v !== "object") return String(v);
  const entries = Object.entries(v as Record<string, unknown>);
  if (!entries.length) return "not recorded";
  return entries.map(([k, x]) => `${k.replace(/_/g, " ")} ${Array.isArray(x) ? x.join(" × ") : typeof x === "object" && x !== null ? coordText(x) : String(x)}`).join(", ");
}
