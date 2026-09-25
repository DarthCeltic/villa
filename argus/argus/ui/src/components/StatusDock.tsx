import { createContext, useContext } from "react";
import { Link, useLocation } from "react-router-dom";
import type { FeedState } from "../api";
import { attentionOf } from "./ContextStrip";
import { feedCondition, FEED_STALE_AFTER_S } from "./Masthead";
import { DisplayControl } from "./DisplayControl";
import type { DeviceClass, DisplayPrefs } from "../lib/displayPrefs";
import { ageLabel } from "../lib/http";
import "../theme/statusdock.css";


export interface StatusInputs {
  feed: FeedState;
  prefs: DisplayPrefs;
  deviceClass: DeviceClass;
  update: (p: Partial<DisplayPrefs>) => void;
  reset: () => void;
}

export const StatusContext = createContext<StatusInputs | null>(null);

export function InlineStatus() {
  const s = useContext(StatusContext);
  if (!s) return null;
  return (
    <div className="wb-status-row" data-control="wb.status" role="region" aria-label="Feed and display">
      <StatusCluster {...s} />
    </div>
  );
}

export function StatusDock(props: StatusInputs) {
  const inWorkbench = useLocation().pathname.startsWith("/workbench");
  if (inWorkbench) return null;
  return (
    <div className="sdock" data-control="shell.statusdock" role="region" aria-label="Feed and display">
      <StatusCluster {...props} />
    </div>
  );
}

function StatusCluster({
  feed, prefs, deviceClass, update, reset,
}: StatusInputs) {
  const cond = feedCondition(feed);
  const runs = feed.data?.runs ?? [];
  const running = runs.filter((r) => r.operational_state === "RUNNING" || r.operational_state === "PENDING").length;
  const attention = attentionOf(runs);
  const refusedIds = runs.filter((r) => r.operational_state === "REFUSED").map((r) => r.run_id);
  const stale = feed.ageS > FEED_STALE_AFTER_S;
  return (
    <>
      {
}
      <Link to="/jobs" className="sdock-signal" data-tone={running > 0 ? "active" : "idle"}
            data-control="context.jobs"
            title={running > 0
              ? "Open Jobs: work the feed reports as running or pending"
              : "Open Jobs: nothing is running or pending"}>
        <span aria-hidden="true">◆</span>
        <span className="sdock-word">{running} running</span>
      </Link>
      <Link to={attention?.to ?? "/jobs"} className="sdock-signal"
            data-tone={attention?.tone ?? "idle"} data-control="context.attention"
            title={attention
              ? (refusedIds.length
                  ? `Refused: ${refusedIds.join(", ")}. Open Jobs to read the reason.`
                  : "Open Jobs to see what needs a decision")
              : "Open Jobs: nothing currently needs attention"}>
        <span aria-hidden="true">{attention?.glyph ?? "○"}</span>
        <span className="sdock-word">{attention?.label ?? "No attention"}</span>
      </Link>
      <span
        className="sdock-feed"
        data-tone={cond.tone}
        data-control="shell.feed"
        title={`last message ${feed.ageS}s ago`}
      >
        <span className="sdock-dot" aria-hidden="true" />
        <span className="sdock-word">{cond.label}</span>
        {stale ? <span className="sdock-age sdock-word">· {ageLabel(feed.ageS, FEED_STALE_AFTER_S)}</span> : null}
      </span>
      <DisplayControl prefs={prefs} deviceClass={deviceClass} update={update} reset={reset} />
    </>
  );
}
