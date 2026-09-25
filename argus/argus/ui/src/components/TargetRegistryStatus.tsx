import { usePoll } from "../lib/poll";

type TargetsPayload = {
  available?: boolean;
  why_v2?: string;
  what_this_does_not_change?: string[];
  sets?: Record<string, { count?: number } | string>;
  source?: { url?: string; sha256?: string };
  id?: string;
};

const SET_NAME: Record<string, string> = { FIRST_LETTERS: "First Letters", GRAND_PRIZE_2027: "Grand Prize 2027" };

export function TargetRegistryStatus() {
  const targets = usePoll<TargetsPayload>("/api/targets", { intervalMs: 60_000 });
  const d = targets.data;
  const unknown = !d || d.available === false;
  return (
    <section
      id="target-registry"
      className="anchor-target"
      aria-labelledby="target-registry-heading"
      data-control="system.target-registry"
      data-novice={unknown ? "missing" : undefined}
      style={{ display: "grid", gap: 6, border: "1px solid var(--line-strong)", borderRadius: "var(--radius-sm)", padding: 12 }}
    >
      <h3 id="target-registry-heading" tabIndex={-1} style={{ margin: 0, fontSize: "inherit", fontWeight: 600 }}>
        Official target registry: {targets.loading && !d ? "checking…" : unknown ? "not installed — eligibility is unknown" : "installed"}
      </h3>
      {unknown ? (
        <>
          <p className="meta" style={{ margin: 0 }}>
            {d?.why_v2 ?? "The registry did not answer."} Prize eligibility, target counts and official acquisitions are
            unknown here, not zero. Every known scroll and everything on this machine stay visible.
          </p>
          <p className="meta" style={{ margin: 0 }} data-control="system.target-registry.how">
            This interface cannot install it. The operator places the provenance-bound registry file
            {" "}<code>scrollprize_crawl/OFFICIAL_ELIGIBLE_TARGETS_V2.json</code> under one of this ARGUS home's artifact
            folders; ARGUS reads it on the next refresh. No CT is downloaded by this step.
          </p>
        </>
      ) : (
        <>
          <p className="meta" style={{ margin: 0 }} data-control="system.target-registry.counts">
            {Object.entries(d?.sets ?? {})
              .filter((e): e is [string, { count?: number }] => typeof e[1] === "object" && e[1] !== null)
              .map(([k, v]) => `${SET_NAME[k] ?? k.replace(/_/g, " ").toLowerCase()}: ${typeof v.count === "number" ? `${v.count} scrolls` : "count not recorded"}`)
              .join(" · ")}
          </p>
          {typeof d?.sets?.rule === "string" ? (
            <p className="meta" style={{ margin: 0, color: "var(--ink-dim)" }}>{d.sets.rule}</p>
          ) : null}
          {d?.source?.url ? (
            <p className="meta" style={{ margin: 0, color: "var(--ink-dim)" }}>
              Read from{" "}
              <a href={d.source.url} target="_blank" rel="noopener noreferrer" data-control="system.target-registry.source">
                {d.source.url.replace(/^https?:\/\//, "")}
              </a>
              {d.source.sha256 ? <> · sha256 <span className="ops-mono">{d.source.sha256.slice(0, 12)}</span></> : null}
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}
