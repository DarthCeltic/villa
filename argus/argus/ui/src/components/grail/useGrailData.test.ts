import { describe, expect, it } from "vitest";
import { interpretScrollMetadataResponse } from "./useGrailData";

describe("interpretScrollMetadataResponse", () => {
  it("reads a 404 as NO_RECORD, not as a failure", () => {
    const s = interpretScrollMetadataResponse(404, { detail: "no ScrollDatasetMetadata record for 'X'" });
    expect(s).toEqual({ state: "NO_RECORD" });
  });

  it("reads the absent_ok 200 NO_RECORD body as NO_RECORD, not as data", () => {
    const s = interpretScrollMetadataResponse(200, { state: "NO_RECORD", scroll_id: "X", why: "none saved" });
    expect(s).toEqual({ state: "NO_RECORD" });
  });

  it("reads a 200 body as OK data, unmodified", () => {
    const body = {
      scroll_id: "PHercFixture1",
      winding_count: { count: 42, derivation_method: "upstream measurement", evidence: { authority: "TEAM_ACCEPTED" } },
      record_sha256: "abc123",
      validation_problems: [] as string[],
    };
    const s = interpretScrollMetadataResponse(200, body);
    expect(s).toEqual({ state: "OK", data: body });
  });

  it("reads the endpoint's 409 (a saved record that no longer validates) as FAILED with the real reason, not silently as no-record", () => {
    const s = interpretScrollMetadataResponse(409, { detail: "record no longer passes validate_record: ['bad frame']" });
    expect(s.state).toBe("FAILED");
    if (s.state === "FAILED") {
      expect(s.why).toBe("record no longer passes validate_record: ['bad frame']");
    }
  });

  it("reads an unexpected 500 as FAILED with a generic reason when the body carries no detail", () => {
    const s = interpretScrollMetadataResponse(500, null);
    expect(s).toEqual({ state: "FAILED", why: "HTTP 500 from /api/scroll_metadata" });
  });
});
