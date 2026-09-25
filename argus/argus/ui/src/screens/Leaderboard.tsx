import { Link } from "react-router-dom";
import "../theme/pipeline-narrative.css";
import products from "../data/evidenceProducts.json";

type EvidenceProduct = {
  name: string;
  kind: "OPERATIONAL CONTROL" | "DEVELOPMENT EVIDENCE" | "SCIENTIFIC RESULT";
  result: string;
  detail: string;
  evidence_url: string;
  submitted_by?: string;
  submitted_utc?: string;
};

const rows = products as EvidenceProduct[];

function evidenceHref(url: string): string | undefined {
  return /^([a-z]+:\/\/|\/)/i.test(url) || url.includes("/") ? url : undefined;
}

export function Leaderboard() {
  return (
    <div className="room-page">
      <header className="room-head">
        <p className="eyebrow">Evidence products</p>
        <h1>ARGUS board</h1>
        <p className="room-lede">A small, honest index of reproducible work. This is not a prize ranking and no row is a submission.</p>
      </header>
      <section className="pipeline-narrative" aria-label="Evidence product board">
        <div className="pipeline-narrative-head">
          <div><p className="eyebrow">Frozen display</p><h2>What has been demonstrated</h2></div>
          <Link className="link-control" to="/evidence">Open Evidence</Link>
        </div>
        <div className="leaderboard-list">
          {rows.map((row) => {
            const href = evidenceHref(row.evidence_url);
            return (
              <article className="leaderboard-row" key={row.name}>
                <div>
                  <p className="eyebrow">{row.kind}</p>
                  <h3>{row.name}</h3>
                  <p>{row.detail}</p>
                  {href ? (
                    <a className="leaderboard-evidence-link" href={href}>{row.evidence_url}</a>
                  ) : (
                    <span className="leaderboard-evidence-broken">
                      no reachable evidence_url — this row does not meet its own schema
                    </span>
                  )}
                </div>
                <span className="leaderboard-result">{row.result}</span>
              </article>
            );
          })}
        </div>
      </section>
      <p className="room-note">
        Exact values, hashes and caveats belong to the append-only receipts. A visual board
        cannot promote a result. Adding a row: <code>docs/public/evidence_products/HOW_TO_ADD.md</code>.
      </p>
    </div>
  );
}

