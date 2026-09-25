import type { StageCell } from "../lib/argusTruth";
import { stepWord, type ScrollStatus } from "../lib/scrollStatus";
import type { ScrollObject } from "./ShelfUniverse";

type Mine = { has: boolean | null; word: string };
export function mineAt(
  cell: string,
  status: ScrollStatus | null,
  vocabulary: Record<string, string> | undefined,
): Mine {
  if (!status) return { has: null, word: "status not read" };
  if (status.status === "REFUSED") return { has: null, word: `refused: ${status.refusal?.code ?? "unknown"}` };
  const stepId = vocabulary?.[cell];
  if (!stepId) return { has: null, word: "not mapped to a journey step" };
  const step = status.steps.find((x) => x.id === stepId);
  if (!step) return { has: null, word: "step not in the status" };
  const has = step.state === "DONE" ? true : step.state === "AVAILABLE" || step.state === "HUMAN_GATED" ? null : false;
  return { has, word: stepWord(step) };
}

export function RouteStrip({
  route,
  label = "Route to a reading",
  scroll = null,
  vocabulary,
}: {
  route: StageCell[];
  label?: string;
  scroll?: ScrollObject | null;
  vocabulary?: Record<string, string>;
}) {
  return (
    <nav className="home-route" data-home="route-strip" aria-label={label}>
      {route.map((s, i) => (
        <div
          key={s.stage}
          className="home-stage"
          data-stage={s.stage}
          data-tone={scroll ? "scroll" : s.tone}
          data-mine={scroll ? String(mineAt(s.stage, scroll.status, vocabulary).has) : undefined}
          data-control={`home.route.stage.${s.stage}`}
          title={scroll ? `${scroll.display}: ${mineAt(s.stage, scroll.status, vocabulary).word}. Instrument: ${s.why}` : s.why}
        >
          <span className="home-stage-n" aria-hidden>
            {i + 1}
          </span>
          <span className="home-stage-label">
            <span className="home-stage-plain">{s.plain}</span>
            {scroll ? (
              <span className="home-stage-mine">
                <span aria-hidden="true">{mineAt(s.stage, scroll.status, vocabulary).has ? "●" : "○"}</span>{" "}
                {mineAt(s.stage, scroll.status, vocabulary).word}
              </span>
            ) : null}
            <span className="home-stage-short" data-secondary={scroll ? "true" : undefined}>
              {scroll ? <span className="home-stage-who">tool: </span> : null}
              <Glyph tone={s.tone} /> {s.short}
            </span>
          </span>
        </div>
      ))}
    </nav>
  );
}

export function Glyph({ tone }: { tone: string }) {
  const g = tone === "certified" ? "✓" : tone === "refused" ? "✕" : tone === "blocked" ? "!" : "·";
  return <span aria-hidden>{g}</span>;
}
