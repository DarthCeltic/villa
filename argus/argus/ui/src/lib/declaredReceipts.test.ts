import { describe, expect, it } from "vitest";
import { declaredReceipt, declaredReceiptUrl } from "./declaredReceipts";

describe("declared receipts", () => {
  it("uses a named service key instead of exposing a file path", () => {
    expect(declaredReceiptUrl("diary evidence")).toBe("/api/receipts?key=diary%20evidence");
  });

  it("keeps honest absence distinct from an empty receipt", () => {
    const row = declaredReceipt(
      {
        receipts: {
          x: { key: "x", present: false, content: null, missing_reason: "not produced" },
        },
      },
      "x",
    );
    expect(row?.present).toBe(false);
    expect(row?.missing_reason).toBe("not produced");
    expect(declaredReceipt({ receipts: {} }, "x")).toBeNull();
  });
});
