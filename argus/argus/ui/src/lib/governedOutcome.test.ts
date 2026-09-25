import { describe, expect, it } from "vitest";
import { runOutcome, type GovernedResult } from "./governed";

const res = (result: GovernedResult["result"]): GovernedResult => ({ plan_hash: "p", request_hash: "r", result });

describe("what a submitted run came to", () => {
  it("does not call a job that refused inside its action Done", () => {
    const o = runOutcome(res({ status: "REFUSED", job_id: "job_1", result: { code: "NOT_ENABLED", why: "the switch is off" } }));
    expect(o.kind).toBe("refused");
    expect(o.text).toContain("Refused by the action itself (NOT_ENABLED): the switch is off.");
    expect(o.text).toContain("job_1");
    expect(o.text).not.toContain("Done");
  });

  it("names what blocks a provider that has no execution adapter", () => {
    const o = runOutcome(res({ status: "REFUSED", job_id: "job_9", result: { code: "EXECUTION_NOT_ADAPTED", why: "no adapter", blocker_kind: "CODE_MISSING" } }));
    expect(o.text).toContain("Blocked by: CODE_MISSING.");
  });

  it("says Failed for a failed job and keeps the reason", () => {
    const o = runOutcome(res({ status: "FAILED", job_id: "job_2", result: { reason: "disk full" } }));
    expect(o.kind).toBe("refused");
    expect(o.text.startsWith("Failed by the action itself")).toBe(true);
    expect(o.text).toContain("disk full");
  });

  it("reports a refusal with no recorded reason as such instead of inventing one", () => {
    expect(runOutcome(res({ status: "REFUSED" })).text).toContain("no reason was recorded");
  });

  it("reports success only for a job that succeeded", () => {
    const o = runOutcome(res({ status: "SUCCEEDED", job_id: "job_3" }));
    expect(o.kind).toBe("done");
    expect(o.text).toBe("Done — SUCCEEDED. Recorded as job job_3.");
  });

  it("says when there is no job id", () => {
    expect(runOutcome(res({ status: "OK" })).text).toContain("(no job id)");
  });
});
