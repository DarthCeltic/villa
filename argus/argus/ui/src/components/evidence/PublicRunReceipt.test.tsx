import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { PublicRunCard, PublicRunsView } from "./PublicRunReceipt";
import { ScrollStatusView } from "../ScrollStatusBar";
import { fixtureStatus } from "../../lib/scrollStatusFixture";
import { onlyPublicRuns, publicRunsUrl, scoreLine, type PublicRunRow } from "../../lib/publicRuns";

const BANNER =
  "KNOWN-DOMAIN HELD-OUT CONTROL — a labelled fragment or labelled-scroll region read out-of-sample. This proves the pipeline, not a discovery.";

const ROW: PublicRunRow = {
  run_id: "pherc0139-w016-ink9um-control-20260925T134612Z",
  collection: "public_pipeline_runs",
  category: "PUBLIC_PIPELINE_RUN",
  receipt_sha256: "r".repeat(64),
  public_run: {
    target: "pherc0139-w016-ink9um-control",
    scroll: "PHerc0139",
    run_id: "20260925T134612Z",
    outcome: "COMPLETED",
    started_utc: "2026-09-25T13:46:12Z",
    finished_utc: "2026-09-25T13:48:41Z",
    manifest_sha256: "b".repeat(64),
    source_commit: "d5c87c0bce0d87e6ef53d0edf558f5c12ce3dc12",
    crop_manifest_sha256: "1".repeat(64),
    launch_ceiling: "DEVELOPMENT_ONLY",
    result_class: {
      presentation: "KNOWN_DOMAIN_HELD_OUT_CONTROL",
      banner: BANNER,
      public_name: "PHerc0139 / w016",
      target_class: "LABELLED_SCROLL",
      exposure_basis: "HELD_OUT_BY_FOLD",
      detector: "scrollprize/ink_9um hybrid_3d2d seed 42",
      detector_cross_scroll_qualified: false,
      acquisition: "9.596 um surface volume, level 2",
      metric: "AUC (argus-metric-v1)",
      score: 0.7722380370197259,
      may_claim_discovery: false,
      may_claim_ink_found: false,
      not_established: ["nothing about an unread scroll"],
    },
    stages: [
      { stage: "identify", state: "RAN", seconds: 0, uses: "argus.core.official_identity" },
      { stage: "infer", state: "RAN", seconds: 11.5, uses: "villa" },
    ],
    outputs: { "prediction.tif": { bytes: 287892, sha256: "4".repeat(64) } },
    terms: { ct_data: "CC BY-NC 4.0, Vesuvius Challenge open data (scrollprize.org, open data)." },
    limits: ["a control proves the pipeline, not a discovery"],
  },
};

const html = (node: JSX.Element) => renderToStaticMarkup(<MemoryRouter>{node}</MemoryRouter>);

describe("a scored public run receipt", () => {
  it("leads with the receipt's own banner and states the claims it does not permit", () => {
    const out = html(<PublicRunCard row={ROW} />);
    expect(out.indexOf(BANNER)).toBeGreaterThan(-1);
    expect(out.indexOf(BANNER)).toBeLessThan(out.indexOf("public-run.target"));
    expect(out).toContain("proves the pipeline, not a discovery");
    expect(out).toContain("AUC 0.7722");
    expect(out).toContain("Discovery claim: not permitted");
    expect(out).toContain("detector not qualified across scrolls");
  });

  it("shows target, stages, hashes and the data terms once opened", () => {
    const closed = html(<PublicRunCard row={ROW} />);
    expect(closed).toContain("Stages, hashes and data terms (2 stages)");
    expect(closed).not.toContain("b".repeat(64));
    const out = html(<PublicRunCard row={ROW} expanded />);
    expect(out).toContain("PHerc0139 / w016");
    expect(out).toContain("infer");
    expect(out).toContain("b".repeat(64));
    expect(out).toContain("4".repeat(64));
    expect(out).toContain("r".repeat(64));
    expect(out).toContain("CC BY-NC 4.0");
    expect(out).toContain("nothing about an unread scroll");
    expect(out).toContain("ceiling DEVELOPMENT_ONLY");
  });

  it("names a receipt with no banner rather than inventing a result class", () => {
    const bare = { ...ROW, public_run: { ...ROW.public_run!, result_class: { ...ROW.public_run!.result_class, banner: null } } };
    expect(html(<PublicRunCard row={bare} />)).toContain("states no result class");
  });

  it("the list separates empty, failed and unread", () => {
    expect(html(<PublicRunsView runs={[]} settled failure={null} />)).toContain("No scored public run is indexed here");
    expect(html(<PublicRunsView runs={[]} settled failure="HTTP 500" />)).toContain("failed read, not an empty archive");
    expect(html(<PublicRunsView runs={[]} settled={false} failure={null} />)).toContain("reading");
    expect(html(<PublicRunsView runs={[]} settled failure={null} quiet />)).toBe("");
    expect(html(<PublicRunsView runs={[ROW]} settled failure={null} />)).toContain("1 listed");
  });

  it("the scroll status panel carries the run for the selected scroll", () => {
    const extra = <PublicRunsView runs={[ROW]} settled failure={null} quiet control="scroll.status.public-runs" />;
    const out = html(<ScrollStatusView status={fixtureStatus()} mode="guided" extra={extra} />);
    expect(out).toContain('data-control="scroll.status.public-runs"');
    expect(out).toContain(BANNER);
  });
});

describe("the public run reader", () => {
  it("asks the evidence index by category and by the scroll the receipt names", () => {
    expect(publicRunsUrl()).toBe("/api/evidence-index?category=PUBLIC_PIPELINE_RUN&sealed=exclude&limit=50");
    expect(publicRunsUrl("PHerc0139")).toContain("scroll=PHerc0139");
  });

  it("drops rows with no receipt payload and orders newest first", () => {
    const older: PublicRunRow = { ...ROW, run_id: "older", public_run: { ...ROW.public_run!, finished_utc: "2026-09-01T00:00:00Z" } };
    const none: PublicRunRow = { run_id: "none" };
    expect(onlyPublicRuns([older, none, ROW]).map((r) => r.run_id)).toEqual([ROW.run_id, "older"]);
    expect(onlyPublicRuns(undefined)).toEqual([]);
  });

  it("formats the score as the receipt states it and never invents one", () => {
    expect(scoreLine(ROW.public_run!.result_class)).toBe("AUC 0.7722");
    expect(scoreLine({ ...ROW.public_run!.result_class, score: null })).toBeNull();
  });
});
