import { describe, expect, it } from "vitest";
import type { UnrollTarget } from "../api";
import {
  claimBlockedReason,
  claimParams,
  getReviewer,
  glyphAlphabet,
  glyphParams,
  mayValidate,
  pickTarget,
  receiptsFrom,
  reviewerProblem,
  setReviewer,
  statusTone,
  tidyName,
  translationBlockedReason,
  translationParams,
  verdictLine,
  type InterpretationState,
  type PacketVerification,
} from "./interpretation";

const state = (over: Partial<InterpretationState> = {}): InterpretationState =>
  ({
    board_state: "BOARD",
    why: null,
    claim: { state: "NONE", claim: null, ready: false, blockers: ["cell c1 has no settled letter"] },
    language: null,
    translation: { claim_state: "NONE", candidates: [], plan: { state: "REFUSED" } },
    ...over,
  }) as unknown as InterpretationState;

describe("reviewer identity", () => {
  it("requires a name a person can be held to", () => {
    expect(reviewerProblem(null)).toMatch(/name yourself/);
    expect(reviewerProblem({ id: "  ", cls: "OPERATOR" })).toMatch(/name yourself/);
    expect(reviewerProblem({ id: "<script>", cls: "OPERATOR" })).toMatch(/1-63 characters/);
    expect(reviewerProblem({ id: "a".repeat(64), cls: "OPERATOR" })).toMatch(/1-63 characters/);
    expect(reviewerProblem({ id: "Alice Reader", cls: "COMMUNITY" })).toBeNull();
  });

  it("refuses a model class: a model is recorded through the HTR slot, never as a reviewer", () => {
    expect(reviewerProblem({ id: "ocr-bot", cls: "AI_AGENT" })).toMatch(/choose a reviewer class/);
  });

  it("tidies whitespace so 'Alice' and ' alice ' are one name to the server too", () => {
    expect(tidyName("  Alice   Reader ")).toBe("Alice Reader");
  });

  it("lets only an operator or a specialist validate", () => {
    expect(mayValidate({ cls: "SPECIALIST" })).toBe(true);
    expect(mayValidate({ cls: "OPERATOR" })).toBe(true);
    expect(mayValidate({ cls: "COMMUNITY" })).toBe(false);
    expect(mayValidate({ cls: "TRUSTED_COLLABORATOR" })).toBe(false);
    expect(mayValidate(null)).toBe(false);
  });

  it("stores only a valid identity and starts unset when storage is unavailable", () => {
    setReviewer({ id: "Alice", cls: "OPERATOR" });
    expect(getReviewer()).toEqual({ id: "Alice", cls: "OPERATOR" });
    setReviewer({ id: "", cls: "OPERATOR" });
    expect(getReviewer()).toBeNull();
    setReviewer(null);
    expect(getReviewer()).toBeNull();
  });
});

describe("governed params", () => {
  const alice = { id: "alice", cls: "OPERATOR" as const };

  it("a glyph annotation carries the reviewer's own name and class, and no session", () => {
    const p = glyphParams("fixture-target", "RT-1", "α", "GREEK", alice);
    expect(p).toEqual({
      target: "fixture-target", task_id: "RT-1", answer: "α", alphabet: "GREEK",
      reviewer_id: "alice", reviewer_class: "OPERATOR",
    });
    expect("reviewer_session" in p).toBe(false);
  });

  it("a transcription claim carries NO counts: the server computes them from the review tally", () => {
    const p = claimParams("fixture-target", alice, "checked");
    expect(Object.keys(p).sort()).toEqual(["claimed_by", "claimed_by_class", "target", "why"]);
    for (const k of ["independent_human_answers", "expert_validated", "agreement", "human_review"]) {
      expect(k in p).toBe(false);
    }
    expect(claimParams("fixture-target", alice, "")).not.toHaveProperty("why");
  });

  it("a translation proposal is attributed, cites tokens and splits alternatives", () => {
    const p = translationParams("fixture-target", ["c1", "c2"], " a phrase ", "one\n\n two ", alice);
    expect(p).toMatchObject({
      source_token_ids: ["c1", "c2"], text: "a phrase", alternatives: ["one", "two"],
      proposed_by: "alice", proposed_by_class: "OPERATOR",
    });
    expect(translationParams("t", ["c"], "x", "", alice, "TC-1")).toHaveProperty("supersedes", "TC-1");
  });

  it("receipts are one path per non-empty line", () => {
    expect(receiptsFrom("a.json\n\n  b.json  \n")).toEqual(["a.json", "b.json"]);
  });
});

describe("blocked reasons say why", () => {
  it("a claim is blocked until the board is ready, with the gate's own reason", () => {
    expect(claimBlockedReason(null)).toMatch(/not loaded/);
    expect(claimBlockedReason(state({ board_state: "NO_BOARD", why: "no region accepted" }))).toBe("no region accepted");
    expect(claimBlockedReason(state())).toMatch(/no settled letter/);
    expect(
      claimBlockedReason(state({ claim: { state: "ACTIVE", claim: null, ready: true, blockers: [] } })),
    ).toMatch(/already claimed/);
    expect(claimBlockedReason(state({ claim: { state: "NONE", claim: null, ready: true, blockers: [] } }))).toBeNull();
  });

  it("translation waits for an ACTIVE claim and a declared language", () => {
    expect(translationBlockedReason(state())).toMatch(/ACTIVE transcription claim/);
    const claimed = state({ translation: { claim_state: "ACTIVE", candidates: [], plan: { state: "INPUT_REQUIRED" } } as never });
    expect(translationBlockedReason(claimed)).toMatch(/declare the language/);
    expect(translationBlockedReason({ ...claimed, language: { language: "Greek", declared_by: "a", utc: "u" } })).toBeNull();
  });
});

describe("packet verification words", () => {
  it("maps every status to a tone, and only OK is green", () => {
    expect(statusTone("OK")).toBe("ok");
    expect(statusTone("VALID")).toBe("ok");
    for (const bad of ["HASH_MISMATCH", "MISSING", "INVALID", "REFUSED"]) expect(statusTone(bad)).toBe("bad");
    expect(statusTone("ALTERED")).toBe("warn");
  });

  it("says what valid means and what refused means", () => {
    const base = { reasons: [], files: [], unlisted_files: [], roots: [], citations: [], what_valid_means: "" };
    expect(verdictLine({ ...base, verdict: "VALID", valid: true, packet_id: "p" } as PacketVerification)).toMatch(/byte-identical/);
    expect(verdictLine({ ...base, verdict: "INVALID", valid: false, reasons: ["a", "b"], packet_id: "p" } as PacketVerification)).toBe(
      "Invalid: 2 problem(s) found.",
    );
    expect(verdictLine({ ...base, verdict: "REFUSED", valid: false, reasons: ["no manifest"], packet_id: null } as PacketVerification)).toMatch(/no manifest/);
  });
});

describe("alphabets", () => {
  it("offers the closed Greek and Latin sets the server declares", () => {
    expect(glyphAlphabet("GREEK")).toHaveLength(25);
    expect(glyphAlphabet("GREEK")[0]).toBe("α");
    expect(glyphAlphabet("LATIN")).toHaveLength(26);
    expect(glyphAlphabet("LATIN")[25]).toBe("Z");
  });
});

describe("target choice", () => {
  const t = (key: string, name: string) => ({ key, base: key, name }) as unknown as UnrollTarget;
  it("follows the selected scroll by name, never by array order, and never a fixture", () => {
    const targets = [t("a", "PHercTest fixture"), t("b", "PHercFixture1 layer stack")];
    expect(pickTarget(targets, "PHercFixture1")?.key).toBe("b");
    expect(pickTarget(targets, "PHercTest")).toBeNull();
    expect(pickTarget(targets, null)).toBeNull();
    expect(pickTarget(targets, "PHerc9")).toBeNull();
  });
});
