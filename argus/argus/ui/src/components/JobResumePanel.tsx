import { useMemo } from "react";
import { usePoll } from "../lib/poll";
import {
  kindLabel,
  orderRows,
  pendingLine,
  resumeParams,
  rowKey,
  stateTone,
  stateWord,
  stoppedLine,
  switchLine,
  type ResumablePayload,
  type ResumableRow,
} from "../lib/jobResume";
import {
  OpsBadge,
  OpsDisabled,
  OpsFact,
  OpsFacts,
  OpsFailure,
  OpsItem,
  OpsSection,
  OpsSource,
  OpsUnknown,
} from "./OpsKit";
import { PlanApprove } from "./PlanApprove";

const SHOWN = 30;

export function JobResumeRow({ row, onDone }: { row: ResumableRow; onDone?: () => void }) {
  const params = useMemo(() => resumeParams(row), [row]);
  const key = rowKey(row);
  const sw = switchLine(row);
  return (
    <OpsItem
      control={`jobs.resume.row.${key}`}
      tone={stateTone(row.state)}
      title={
        <span className="ops-mono">
          {row.job_id}
          {row.stage ? ` / ${row.stage}` : ""}
        </span>
      }
      state={stateWord(row.state)}
    >
      <OpsFacts>
        <OpsFact label="Kind">{kindLabel(row.kind)}{row.action ? ` (${row.action})` : ""}</OpsFact>
        <OpsFact label="Where it stopped">{stoppedLine(row)}</OpsFact>
        <OpsFact label="Progress">{pendingLine(row)}</OpsFact>
        {row.heartbeat && typeof row.heartbeat.age_s === "number" ? (
          <OpsFact label="Last heartbeat">
            {Math.round(row.heartbeat.age_s)} s ago{row.heartbeat.stale ? " (stale: the worker is gone)" : ""}
          </OpsFact>
        ) : null}
        {!row.resumable && row.why_not ? <OpsFact label="Why it cannot be resumed">{row.why_not}</OpsFact> : null}
        {sw ? <OpsFact label="Switch">{sw}</OpsFact> : null}
      </OpsFacts>
      {row.resumable ? (
        <PlanApprove
          action="job.resume"
          params={params}
          label="resume this job"
          why="lists what would be resumed and what will not be run again; nothing changes until you approve"
          onDone={onDone}
        />
      ) : (
        <OpsDisabled
          control={`jobs.resume.disabled.${key}`}
          label="Resume this job"
          reason={row.why_not ?? "the service gave no reason"}
        />
      )}
    </OpsItem>
  );
}

export function JobResumePanel() {
  const jobs = usePoll<ResumablePayload>("/api/resumable-jobs", { intervalMs: 20000 });
  const rows = useMemo(() => orderRows(jobs.data?.jobs ?? []), [jobs.data]);
  const shown = rows.slice(0, SHOWN);
  return (
    <OpsSection
      control="jobs.resume"
      title="Interrupted and resumable jobs"
      hint="Jobs that stopped part-way, and exactly where. Resuming is a governed action: review the plan, then approve it by its own hash. Units already completed are never run again."
      aside={
        jobs.data ? (
          <OpsBadge tone={jobs.data.resumable ? "warn" : "idle"} control="jobs.resume.count">
            {jobs.data.resumable} resumable of {jobs.data.jobs.length}
          </OpsBadge>
        ) : null
      }
    >
      {!jobs.data ? (
        <ul className="ops-list">
          {jobs.failure ? (
            <OpsFailure what="The resumable-jobs listing" failure={jobs.failure} onRetry={jobs.refresh} control="jobs.resume" />
          ) : (
            <OpsUnknown what="The resumable-jobs listing" why="/api/resumable-jobs has not answered yet." />
          )}
        </ul>
      ) : rows.length === 0 ? (
        <ul className="ops-list">
          <OpsItem tone="idle" control="jobs.resume.none" title="No job workspace or resumable command job was found" state="none" />
        </ul>
      ) : (
        <ul className="ops-list" data-control="jobs.resume.list">
          {shown.map((r) => (
            <JobResumeRow key={rowKey(r)} row={r} onDone={jobs.refresh} />
          ))}
          {rows.length > shown.length ? (
            <OpsItem
              tone="idle"
              control="jobs.resume.more"
              title={`${rows.length - shown.length} more job(s) are not shown`}
              state="capped"
            />
          ) : null}
        </ul>
      )}
      <OpsSource route="/api/resumable-jobs" field="jobs[], by_state, resumable" />
    </OpsSection>
  );
}
