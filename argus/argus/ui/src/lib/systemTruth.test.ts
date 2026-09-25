import { describe, expect, it } from "vitest";
import { certificationIsSuppressed, type Integrity } from "./systemTruth";

const base = (current: string): Integrity => ({
  schema: "argus-integrity-v2",
  utc: "2026-09-15T00:00:00Z",
  chain: { status: current === "CURRENT_CHAIN_VERIFIED" ? "INTACT" : "BROKEN", at: 223 },
  historical: { state: "HISTORICAL_CHAIN_BROKEN" },
  current: { state: current },
  certification: { historical_records: "SUPPRESSED", new_records: current === "CURRENT_CHAIN_VERIFIED" ? "ALLOWED" : "SUPPRESSED" },
  readable: true,
  why_unreadable: null,
  ledger: "ledger",
  verified_by: "ledger_v2",
  means: {},
  consequence_of_broken: "",
});

describe("current ledger certification gate", () => {
  it("does not let historical v1 break suppress a verified v2 chain", () => {
    expect(certificationIsSuppressed(base("CURRENT_CHAIN_VERIFIED"))).toEqual({ suppressed: false, why: null });
  });

  it("suppresses certification when the current v2 chain breaks", () => {
    expect(certificationIsSuppressed(base("CURRENT_CHAIN_BROKEN")).suppressed).toBe(true);
  });
});
