import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { usePoll } from "../lib/poll";
import { OpsBadge, OpsSource, OpsTable } from "./OpsKit";

interface Issue { number: number; title: string; url: string; updated_at: string | null; labels: string[] }
interface ProblemGroup {
  id: string; label: string; route_steps: string[]; argus_state: string;
  argus_answer: string; remaining: string; count: number; issues: Issue[];
}
interface OpenProblemsDoc {
  official_challenge: { url: string; tracks: { id: string; label: string; meaning: string }[] };
  villa: { repo: string; issues_url: string; open_count: number; observed_utc: string | null;
    snapshot_source: string; live: boolean; groups: ProblemGroup[] };
  claim_boundary: string;
  update_rule: string;
}

const tone = (state: string): "ok" | "warn" | "bad" | "info" | "idle" =>
  state === "GUARDED" ? "info" : state === "EXTERNAL_PROOF_REQUIRED" ? "bad" : "warn";

export function OpenProblemsPanel({ compact = false }: { compact?: boolean }) {
  const problems = usePoll<OpenProblemsDoc>("/api/open-problems", { intervalMs: 120000 });
  const [query, setQuery] = useState("");
  const doc = problems.data;
  const groups = useMemo(() => {
    if (!doc) return [];
    const q = query.trim().toLowerCase();
    if (!q) return doc.villa.groups;
    return doc.villa.groups.map((g) => ({
      ...g,
      issues: g.issues.filter((i) => `${i.number} ${i.title} ${i.labels.join(" ")}`.toLowerCase().includes(q)),
    })).filter((g) => g.issues.length || g.label.toLowerCase().includes(q));
  }, [doc, query]);

  if (!doc) return <p className="ops-note" data-control="open-problems.loading">
    {problems.failure ? `Could not read the official-problem map: ${problems.failure.message}.` : "Reading the official-problem map…"}
  </p>;

  if (compact) return (
    <section className="ops-sec" data-control="home.open-problems" style={{ margin: "16px 8px" }}>
      <div className="ops-sec-head">
        <div>
          <h2>What the whole ARGUS route is for</h2>
          <p className="ops-note">The official challenge still has two broad fronts. ARGUS connects both in one 16-step route and says which parts are guarded, partial, or still need external proof.</p>
        </div>
        <OpsBadge tone="warn">{doc.villa.open_count} open Villa issues mapped</OpsBadge>
      </div>
      <div className="ops-facts">
        {doc.official_challenge.tracks.map((track) => <div className="ops-fact" key={track.id} style={{ display: "grid", gap: 2 }}>
          <strong>{track.label}</strong><span>{track.meaning}</span>
        </div>)}
      </div>
      <p><Link className="ops-btn interactive" to="/system?tab=problems" data-control="home.open-problems.open">See every open problem and where ARGUS handles it</Link></p>
    </section>
  );

  return <div data-control="open-problems.panel" style={{ display: "grid", gap: 14 }}>
    <div className="ops-headline" data-tone="warn">
      <strong>{doc.villa.open_count} open issues in the latest official Villa snapshot</strong>
      <p>{doc.claim_boundary}</p>
      <p className="ops-note">Observed {doc.villa.observed_utc ?? "at an unknown time"} from {doc.villa.live ? "the last consented live update check" : "the checked-in offline snapshot"}. {doc.update_rule}</p>
    </div>
    <div className="ops-facts">
      {doc.official_challenge.tracks.map((track) => <div className="ops-fact" key={track.id} style={{ display: "grid", gap: 2 }}>
        <strong><a href={doc.official_challenge.url} target="_blank" rel="noreferrer">{track.label}</a></strong>
        <span>{track.meaning}</span>
      </div>)}
    </div>
    <label className="ops-note">Search all mapped issues
      <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="issue number, title or label" data-control="open-problems.search" style={{ display: "block", width: "100%", marginTop: 4 }} />
    </label>
    {groups.map((g, index) => <details key={g.id} className="ops-disclosure" open={index === 0 || Boolean(query)} data-control={`open-problems.group.${g.id}`}>
      <summary><b>{g.label}</b> · {g.count} issue{g.count === 1 ? "" : "s"} · {g.argus_state.toLowerCase().replaceAll("_", " ")}</summary>
      <div style={{ display: "grid", gap: 8, padding: "10px 0" }}>
        <div><OpsBadge tone={tone(g.argus_state)}>{g.argus_state.replaceAll("_", " ")}</OpsBadge>{g.route_steps.length ? <span className="ops-note"> route: {g.route_steps.join(" → ")}</span> : null}</div>
        <p><b>What ARGUS does:</b> {g.argus_answer}</p>
        <p><b>What remains:</b> {g.remaining}</p>
        <OpsTable control={`open-problems.table.${g.id}`}><thead><tr><th>Issue</th><th>Official title</th><th>Updated</th></tr></thead><tbody>
          {g.issues.map((i) => <tr key={i.number}><td><a href={i.url} target="_blank" rel="noreferrer">#{i.number}</a></td><td>{i.title}</td><td>{i.updated_at?.slice(0, 10) ?? "unknown"}</td></tr>)}
        </tbody></OpsTable>
      </div>
    </details>)}
    <p className="ops-note"><a href={doc.villa.issues_url} target="_blank" rel="noreferrer">Open the official Villa issue tracker</a>. Listing an issue here neither adopts upstream code nor claims the issue is solved.</p>
    <OpsSource route="/api/open-problems" field="official_challenge, villa.groups, claim_boundary" />
  </div>;
}
