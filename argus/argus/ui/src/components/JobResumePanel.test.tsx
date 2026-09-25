import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { JobResumeRow } from "./JobResumePanel";
import {
  orderRows,
  pendingLine,
  resumeParams,
  stateTone,
  stoppedLine,
  switchLine,
  type ResumableRow,
} from "../lib/jobResume";
import { approvedHash, type GovernedPlan } from "../lib/governed";


function row(over: Partial<ResumableRow> = {}): ResumableRow {
  return {
    job_id: "jobint01",
    kind: "UNIT_LEDGER",
    stage: "s1",
    action: null,
    state: "interrupted",
    resumable: true,
    why_not: null,
    unit_word: "unit",
    units_total: 5,
    units_done: 2,
    units_pending: 3,
    resume_unit: "u2.txt",
    stopped_after: "u1.txt",
    pending_preview: ["u2.txt", "u3.txt", "u4.txt"],
    heartbeat: { age_s: 10800, stale: true, state: "STALLED" },
    flag: { flag: "ARGUS_JOBS_API_ENABLED", enabled: true },
    resume_params: { job_id: "jobint01", stage: "s1" },
    ...over,
  };
}

describe("resume panel wording", () => {
  it("says exactly where the job stopped and where it would resume", () => {
    expect(stoppedLine(row())).toBe("stopped after unit u1.txt; would resume at unit u2.txt (2 of 5 done)");
    expect(pendingLine(row())).toBe("3 pending: u2.txt, u3.txt, u4.txt");
  });

  it("counts what the preview leaves out instead of hiding it", () => {
    const r = row({ units_pending: 14, pending_preview: ["a", "b"] });
    expect(pendingLine(r)).toBe("14 pending: a, b and 12 more");
  });

  it("does not invent a stopping point for a job that never completed a unit", () => {
    const r = row({ units_done: 0, stopped_after: null, units_pending: 5 });
    expect(stoppedLine(r)).toContain("no unit has completed");
  });

  it("says when no unit list could be read at all", () => {
    expect(stoppedLine(row({ units_total: 0, units_done: 0, units_pending: 0, resume_unit: null, stopped_after: null }))).toBe(
      "no unit list could be read for this job",
    );
  });

  it("is honest about the switch being off", () => {
    expect(switchLine(row({ flag: { flag: "ARGUS_JOBS_API_ENABLED", enabled: false } }))).toContain("refused until the operator sets it");
    expect(switchLine(row({ flag: null }))).toBeNull();
  });

  it("never shows anything as a pass: finished work is neutral, an interrupted job needs attention", () => {
    expect(stateTone("complete")).toBe("idle");
    expect(stateTone("interrupted")).toBe("warn");
    expect(stateTone("running")).toBe("info");
    expect(stateTone("unreadable")).toBe("bad");
  });

  it("lists what a person can act on before what only needs explaining", () => {
    const done = row({ job_id: "a-done", state: "complete", resumable: false });
    const live = row({ job_id: "b-live", state: "running", resumable: false });
    const cut = row({ job_id: "c-cut" });
    expect(orderRows([done, live, cut]).map((r) => r.job_id)).toEqual(["c-cut", "b-live", "a-done"]);
  });
});

describe("the governed resume parameters", () => {
  it("send the job and its stage and never a hash of their own", () => {
    expect(resumeParams(row())).toEqual({ job_id: "jobint01", stage: "s1" });
    expect(resumeParams(row({ resume_params: { job_id: "job_abc" } }))).toEqual({ job_id: "job_abc" });
    expect(Object.keys(resumeParams(row()))).not.toContain("approved_plan_sha256");
  });

  it("approve with the plan's own hash, never the envelope's", () => {
    const plan = { plan_hash: "envelope", request_hash: "r", read_only: true, plan: { plan_sha256: "own" } } as unknown as GovernedPlan;
    expect(approvedHash(plan)).toBe("own");
  });
});

describe("JobResumeRow", () => {
  it("offers a plan-then-approve control for a resumable job and shows where it stopped", () => {
    const html = renderToStaticMarkup(<JobResumeRow row={row()} />);
    expect(html).toContain("jobint01 / s1");
    expect(html).toContain("interrupted");
    expect(html).toContain("stopped after unit u1.txt");
    expect(html).toContain("3 pending: u2.txt, u3.txt, u4.txt");
    expect(html).toContain("stale: the worker is gone");
    expect(html).toContain('data-control="plan-approve.job.resume"');
    expect(html).toContain("Review: resume this job");
    expect(html).not.toContain("plan-approve.job.resume.approve");
    expect(html).not.toContain("jobs.resume.disabled");
  });

  it("keeps a disabled control with the service's reason for a job that cannot be resumed", () => {
    const html = renderToStaticMarkup(
      <JobResumeRow
        row={row({
          state: "running",
          resumable: false,
          why_not: "a worker heartbeat is fresh (4.0 s old); resuming would run the same units twice",
        })}
      />,
    );
    expect(html).toContain("jobs.resume.disabled.jobint01::s1");
    expect(html).toContain('aria-disabled="true"');
    expect(html).toContain("resuming would run the same units twice");
    expect(html).not.toContain("plan-approve.job.resume");
  });

  it("says the reason when the service gave none", () => {
    const html = renderToStaticMarkup(<JobResumeRow row={row({ resumable: false, state: "complete", why_not: null })} />);
    expect(html).toContain("the service gave no reason");
  });

  it("does not use a bare status colour: tone comes from the shared vocabulary", () => {
    const html = renderToStaticMarkup(<JobResumeRow row={row()} />);
    expect(html).toContain('data-tone="warn"');
    expect(html).not.toMatch(/#[0-9a-fA-F]{3,8}/);
  });
});
