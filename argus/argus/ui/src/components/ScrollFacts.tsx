import { Link } from "react-router-dom";
import { type Lane, type ScrollObject, type Universe } from "./ShelfUniverse";
import { useArgusContext } from "../lib/context";
import { usePublicDemo } from "../lib/publicDemo";
import { QUESTION_LABELS, QUESTION_ORDER, type QuestionKey, type ScrollStatus } from "../lib/scrollStatus";
import "../theme/facts.css";

export type FactKey = QuestionKey;

export type FactTone = "ok" | "warn" | "bad" | "idle" | "unknown";

export interface Fact {
  key: FactKey;
  label: string;
  value: string;
  unknown: boolean;
  tone: FactTone;
  why: string;
  route: string;
  to?: string;
}

export const FACT_LABELS: Record<FactKey, string> = QUESTION_LABELS;

const UNKNOWN = "UNKNOWN";

export function scrollFacts(
  status: ScrollStatus | null,
  opts: { redactLocal?: boolean; statusRead?: boolean } = {},
): Fact[] {
  return QUESTION_ORDER.map((key): Fact => {
    const q = status?.questions[key];
    if (!q || q.answer === null) {
      const why = q?.refusal?.why
        ?? (opts.statusRead === false
          ? "The per-scroll status has not been read, so nothing is asserted either way."
          : "The per-scroll status carries no answer for this scroll.");
      return {
        key, label: FACT_LABELS[key], value: UNKNOWN, unknown: true, tone: "unknown", why,
        route: q ? q.basis.join(" + ") : "/api/scroll_status",
      };
    }
    if (key === "local" && opts.redactLocal) {
      return {
        key, label: FACT_LABELS[key], value: "withheld in public demo", unknown: false, tone: "idle",
        route: q.basis.join(" + "),
        why: "Local holdings of prize-eligible scrolls are not shown in public demo mode.",
      };
    }
    return {
      key, label: FACT_LABELS[key], value: q.answer, unknown: false, tone: q.tone,
      why: q.detail, route: q.basis.join(" + "), to: q.to ?? undefined,
    };
  });
}

function Cell({ f, scroll, raw }: { f: Fact; scroll: string; raw: boolean }) {
  const body = (
    <>
      {f.unknown ? <span className="sf-unknown">UNKNOWN</span> : f.value}
    </>
  );
  return (
    <td className="sf-cell" data-fact={f.key} data-tone={f.tone} data-unknown={f.unknown ? "true" : undefined}
        title={`${f.label}: ${f.unknown ? "UNKNOWN. " : ""}${f.why}${raw ? ` (source: ${f.route})` : ""}`}>
      {f.to ? (
        <Link to={f.to} data-control={`facts.${scroll}.${f.key}`}>{body}</Link>
      ) : body}
    </td>
  );
}

const COLUMN_ORDER: FactKey[] = QUESTION_ORDER;

export function ScrollFactsTable({
  universe, lane, caption, control,
}: {
  universe: Universe;
  lane: Lane | "ALL";
  caption: string;
  control: string;
}) {
  const demo = usePublicDemo();
  const { ctx, set, mode } = useArgusContext();
  const raw = mode === "expert";
  const rows = universe.scrolls.filter((s) => {
    if (lane === "ALL") return true;
    if (lane === "FIRST_LETTERS") return s.firstLetters;
    if (lane === "GRAND_PRIZE") return s.grandPrize;
    if (lane === "PARIS4_TITLE") return s.titleLane;
    return s.lane === "CONTROL_OR_DEV";
  });
  return (
    <section className="sf" data-control={control} aria-label={caption}>
      <header className="sf-head">
        <h2 className="sf-title">{caption}</h2>
        <p className="sf-lede">
          Eight facts per scroll, each the server's answer to one question{raw ? ", with its source named on hover" : ""}.
          UNKNOWN means the answer was refused or not read; it is never a pass.
        </p>
      </header>
      {universe.loading && !rows.length ? (
        <p className="sf-empty">Reading the scroll universe…</p>
      ) : !rows.length ? (
        <p className="sf-empty">No scroll is registered in this collection on the routes that were read.</p>
      ) : (
        <div className="sf-scroll" role="region" aria-label={`${caption}, scrollable`} tabIndex={0}>
          <table className="sf-table">
            <thead>
              <tr>
                <th scope="col">Scroll</th>
                {COLUMN_ORDER.map((k) => <th key={k} scope="col">{FACT_LABELS[k]}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => {
                const facts = scrollFacts(s.status, {
                  redactLocal: demo && (s.firstLetters || s.grandPrize || s.titleLane),
                  statusRead: universe.statusRead,
                });
                const by = new Map(facts.map((f) => [f.key, f]));
                return (
                  <tr key={s.id} data-selected={ctx.scroll === s.id ? "true" : undefined}>
                    <th scope="row">
                      <button type="button" className="sf-name" data-control={`facts.select.${s.id}`}
                              aria-pressed={ctx.scroll === s.id}
                              onClick={() => set({ scroll: s.id })}>
                        {s.display}
                      </button>
                    </th>
                    {COLUMN_ORDER.map((k) => <Cell key={k} f={by.get(k)!} scroll={s.id} raw={raw} />)}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function ScrollFactsList({ s, universe }: { s: ScrollObject; universe: Universe }) {
  const demo = usePublicDemo();
  const { mode } = useArgusContext();
  const facts = scrollFacts(s.status, {
    redactLocal: demo && (s.firstLetters || s.grandPrize || s.titleLane),
    statusRead: universe.statusRead,
  });
  return (
    <dl className="sf-list" data-control={`grail.brief.facts.${s.id}`}>
      {facts.map((f) => (
        <div key={f.key} className="sf-list-row" data-fact={f.key} data-tone={f.tone}
             data-unknown={f.unknown ? "true" : undefined}>
          <dt>{f.label}</dt>
          <dd title={mode === "expert" ? `${f.why} (source: ${f.route})` : f.why}>
            {f.to ? <Link to={f.to} data-control={`grail.brief.fact.${s.id}.${f.key}`}>
              {f.unknown ? "UNKNOWN" : f.value}</Link>
              : (f.unknown ? <span className="sf-unknown">UNKNOWN</span> : f.value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function scrollContradictions(s: ScrollObject): string[] {
  const out: string[] = [];
  if (s.localDisagreement) out.push(`Local data: ${s.localDisagreement}`);
  if (s.confusableWith) out.push(`Identity: easily confused with ${s.confusableWith}; check the id before acting.`);
  if (!s.inCanonicalIds) out.push("Identity: not in the canonical identifier list.");
  if (s.fixture.fixture) out.push(`Fixture: ${s.fixture.why ?? "a declared test fixture, not a real scroll"}`);
  if (s.publishedUpstream && s.local.state === "NOTHING_INDEXED") {
    out.push("Published upstream but not held here: upstream publication is not local material.");
  }
  return out;
}
