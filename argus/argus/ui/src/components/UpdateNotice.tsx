import { useEffect } from "react";
import { Link, useLocation } from "react-router-dom";

import { usePoll } from "../lib/poll";

interface UpdateNoticeDoc {
  policy?: { mode?: string };
  watchlist?: {
    id: string;
    title: string;
    state: string;
    state_source: string;
    argus_status: string;
  }[];
}

export function UpdateNotice() {
  const updates = usePoll<UpdateNoticeDoc>("/api/updates", { intervalMs: 60000 });
  const location = useLocation();
  useEffect(() => {
    const refresh = () => updates.refresh();
    window.addEventListener("argus:update-policy-changed", refresh);
    return () => window.removeEventListener("argus:update-policy-changed", refresh);
  }, [updates.refresh]);
  if (updates.data?.policy?.mode !== "UNSET") return null;
  if (location.pathname !== "/") return null;

  const worthEvaluating = (updates.data.watchlist ?? []).filter(
    (row) => row.state === "MERGED_TO_MAIN" && row.argus_status === "MERGED",
  );
  const params = new URLSearchParams(location.search);
  const scroll = params.get("scroll");
  const target = `/system?tab=updates${scroll ? `&scroll=${encodeURIComponent(scroll)}` : ""}#updates-policy`;
  const configuredOnly = worthEvaluating.some((row) => row.state_source !== "OBSERVED");

  return (
    <section className="update-notice" role="alert" data-control="updates.global-unset">
      <div
        title={`${worthEvaluating.length ? `${worthEvaluating.length} configured candidate ${worthEvaluating.length === 1 ? "change is" : "changes are"} marked merged. ` : ""}ARGUS has not contacted upstream or installed anything.${configuredOnly ? " This is checked-in watchlist information, not fresh upstream verification." : ""}`}
      >
        <strong>Update policy not chosen.</strong>{" "}
        <span>ARGUS has not contacted upstream or installed anything.</span>
      </div>
      <Link className="interactive update-notice-link" to={target} data-control="updates.global-unset.open">
        Review {worthEvaluating.length || "known"} {worthEvaluating.length === 1 ? "candidate" : "candidates"} and choose a policy
      </Link>
    </section>
  );
}
