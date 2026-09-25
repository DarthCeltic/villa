import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { SegmentImportPanel } from "./SegmentImportPanel";
import { EMPTY_SEGMENT_FORM, attachProblem, segmentParams } from "../lib/segmentImport";

describe("segment import parameters", () => {
  it("send nothing until a directory is named", () => {
    expect(segmentParams(EMPTY_SEGMENT_FORM)).toBeNull();
    expect(segmentParams({ ...EMPTY_SEGMENT_FORM, path: "   " })).toBeNull();
  });

  it("omit empty optional fields and never send an identity that was not typed", () => {
    expect(segmentParams({ ...EMPTY_SEGMENT_FORM, path: " inbox/seg1 " })).toEqual({ path: "inbox/seg1" });
  });

  it("send an attached identity only with its attribution", () => {
    const f = { path: "inbox/seg1", attach: "s3://example/volumes/1.zarr", attestedBy: "reviewer", reason: "traced on the released scan" };
    expect(segmentParams(f)).toEqual({
      path: "inbox/seg1",
      attach_volume_source: "s3://example/volumes/1.zarr",
      attested_by: "reviewer",
      attestation_reason: "traced on the released scan",
    });
    expect(attachProblem(f)).toBeNull();
  });

  it("refuse an attached identity that is anonymous or unexplained before any round trip", () => {
    const base = { path: "p", attach: "x", attestedBy: "", reason: "" };
    expect(attachProblem(base)).toContain("attested_by");
    expect(attachProblem({ ...base, attestedBy: "reviewer", reason: "short" })).toContain("at least 8");
    expect(attachProblem(EMPTY_SEGMENT_FORM)).toBeNull();
  });

  it("never carries a plan hash of its own", () => {
    const p = segmentParams({ path: "p", attach: "x", attestedBy: "reviewer", reason: "long enough reason" });
    expect(Object.keys(p ?? {})).not.toContain("approved_plan_sha256");
  });
});

describe("SegmentImportPanel", () => {
  it("starts with a disabled import that says why, and hides the attach fields behind a disclosure", () => {
    const html = renderToStaticMarkup(<SegmentImportPanel />);
    expect(html).toContain("Bring your own segment");
    expect(html).toContain("sources.import.disabled");
    expect(html).toContain("name a local directory first");
    expect(html).toContain("with your name and a reason");
    expect(html).toContain("ATTESTED, not verified");
    expect(html).not.toContain("plan-approve.import.segment");
  });
});
