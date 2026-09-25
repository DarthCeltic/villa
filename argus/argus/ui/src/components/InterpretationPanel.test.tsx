import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { InterpretationState, PacketListRow, PacketVerification } from "../lib/interpretation";
import {
  HtrSlotView,
  LimitationsView,
  MissingInterpretationStages,
  OpenCandidates,
  ReadingBoardView,
  TranscriptionWorksheet,
  TranslationWorksheet,
} from "./InterpretationPanel";
import { ReviewerIdentityView } from "./ReviewerIdentity";
import { ReviewQueue } from "./ReviewQueue";
import { PacketListView, PacketVerificationView } from "./evidence/PacketPanel";

const alice = { id: "alice", cls: "OPERATOR" as const };
const noop = () => undefined;

function cell(over: Record<string, unknown> = {}) {
  return {
    cell_id: "cell-abc123",
    extent: [0, 100, 0, 100],
    state: "ACCEPTED",
    review: {
      task_id: "RT-0",
      answers: [
        { reviewer_id: "alice", reviewer_class: "OPERATOR", evidence_role: "HUMAN_JUDGMENT" },
        { reviewer_id: "bob", reviewer_class: "COMMUNITY", evidence_role: "HUMAN_JUDGMENT" },
        { reviewer_id: "ai:ocr@1", reviewer_class: "AI_AGENT", evidence_role: "PREDICTION_DISCOVERY_EVIDENCE" },
      ],
    },
    glyph: null,
    ...over,
  };
}

function state(over: Record<string, unknown> = {}): InterpretationState {
  return {
    schema: "argus-interpretation-state-v1",
    target: "target-a",
    review_tasks: { present: true, ink: 1, glyph: 0, total: 1 },
    board_state: "BOARD",
    why: null,
    board: { class: "READING_BOARD", cell_count: 1, orientation: "forward", cells: [cell()], this_is_not: ["a transcription"] },
    board_sha256: "x",
    refused_regions: [],
    claim: { state: "NONE", claim: null, ready: false, blockers: ["cell cell-abc123 (task RT-0) has no letter judgment settled by two independent people"] },
    human_review_preview: null,
    proposed_reading: {
      text: "α█β", slots: 3, supported: 2, gaps: 1, gap_marker: "█", characters: [],
      reading_order_established: false, what_this_is_not: ["a reading of an unread scroll"],
    },
    regions_accepted: 1,
    reviewers: [
      { reviewer_id: "alice", reviewer_class: "OPERATOR", evidence_role: "HUMAN_JUDGMENT", identity_attestation: "SELF_DECLARED", answers: 1 },
      { reviewer_id: "ai:ocr@1", reviewer_class: "AI_AGENT", evidence_role: "PREDICTION_DISCOVERY_EVIDENCE", identity_attestation: "MODEL_SOURCE", answers: 1 },
    ],
    language: null,
    translation: {
      plan: { state: "REFUSED", why: "translation accepts only a promoted transcription" },
      claim_state: "NONE",
      candidates: [],
      label: "PROPOSAL",
      no_machine_translation: "there is no machine translation of Ancient Greek or Latin in this system",
      what_a_candidate_is: "",
    },
    htr_slot: {
      state: "PROVIDER_NOT_INSTALLED",
      why: "no OCR/HTR model is installed",
      accepts: "a proposal from an external source",
      recorded_as: "an AI_AGENT-class answer",
      never: ["counted as a person agreeing", "a second reviewer"],
      action: "htr.propose",
    },
    reviewer_rules: { min_independent_humans: 2 },
    claim_limits: { unread_scroll_reading_claimed: false },
    limitations: [
      { id: "NO_UNREAD_SCROLL_READING", text: "This packet contains no reading of an unread scroll." },
      { id: "NOT_PUBLISHED", text: "Nothing here was published." },
    ],
    ...over,
  } as unknown as InterpretationState;
}

const html = (el: React.ReactElement) => renderToStaticMarkup(el);

describe("the review queue asks who is answering", () => {
  const payload = {
    counts: { tasks: 1, blind_controls: 0 },
    tasks: [
      {
        task_id: "RT-0", task_type: "JUDGE_INK_CANDIDATE", state: "PROPOSED",
        binding: { surface_or_candidate_id: "cand-1", physical_coordinates: {} },
        permitted_answers: ["INK", "NOT_INK", "CANNOT_TELL"],
      },
    ],
  };

  it("shows the reviewer form when the queue can write, and not when it is read only", () => {
    expect(html(<ReviewQueue payload={payload} target="target-a" />)).toContain("Reviewing as");
    expect(html(<ReviewQueue payload={payload} target={null} />)).not.toContain("Reviewing as");
  });
});

describe("reviewer identity", () => {
  it("says the name is self-declared and never authenticated", () => {
    const out = html(<ReviewerIdentityView reviewer={null} onSave={noop} onClear={noop} />);
    expect(out).toContain("nobody yet");
    expect(out).toContain("self-declared");
    expect(out).toContain("not authenticated");
    expect(out).not.toContain("AI_AGENT");
  });

  it("shows who is reviewing once named", () => {
    expect(html(<ReviewerIdentityView reviewer={alice} onSave={noop} onClear={noop} />)).toContain("alice · operator");
  });
});

describe("the reading board", () => {
  it("names the people who accepted a region and lists a model's proposal nowhere among them", () => {
    const out = html(<ReadingBoardView state={state()} target="target-a" reviewer={alice} onChanged={noop} />);
    expect(out).toContain("alice, bob");
    expect(out).not.toContain("alice, bob, ai:ocr");
    expect(out).toContain("not a transcription and not OCR");
    expect(out).toContain("model proposal");
    expect(out).toContain("rows 0–100 · cols 0–100");
  });

  it("says why there is no board rather than drawing an empty one", () => {
    const out = html(
      <ReadingBoardView
        state={state({ board: null, board_state: "NO_BOARD", why: "no region has been accepted as ink by at least 2 independent people" })}
        target="target-a"
        reviewer={alice}
        onChanged={noop}
      />,
    );
    expect(out).toContain("no region has been accepted as ink by at least 2 independent people");
    expect(out).toContain("not available");
  });

  it("reports refused regions instead of dropping them", () => {
    const out = html(
      <ReadingBoardView
        state={state({ refused_regions: [{ task_id: "RT-9", reason: "only 1 independent human reviewer answered it" }] })}
        target="target-a"
        reviewer={alice}
        onChanged={noop}
      />,
    );
    expect(out).toContain("RT-9");
    expect(out).toContain("refused for the board, not dropped");
  });

  it("asks an unnamed reviewer to name themselves before recording a letter", () => {
    const out = html(<ReadingBoardView state={state()} target="target-a" reviewer={null} onChanged={noop} />);
    expect(out).toContain("name yourself first");
  });
});

describe("the transcription worksheet", () => {
  it("keeps transcription and translation deep-link targets visible when no exported target exists", () => {
    const out = html(
      <MemoryRouter initialEntries={["/review?scroll=PHercParis4"]}>
        <MissingInterpretationStages why="no exported target is chosen" />
      </MemoryRouter>,
    );
    expect(out).toContain('id="review.interpretation.transcription"');
    expect(out).toContain('id="review.interpretation.translation"');
    expect(out).toContain('href="/review?scroll=PHercParis4#review.queue"');
    expect(out).toContain('href="/review?scroll=PHercParis4#review.interpretation.transcription"');
    expect(out).toContain("finish the reading board for this scroll");
    expect(out).toContain("a reviewed transcription comes first");
    expect(out).not.toContain("To establish it:");
  });

  it("labels the reading a proposal, keeps the gap visible and disclaims reading order", () => {
    const out = html(<TranscriptionWorksheet state={state()} target="target-a" reviewer={alice} onChanged={noop} />);
    expect(out).toContain("PROPOSED READING");
    expect(out).toContain("not a measurement");
    expect(out).toContain("α█β");
    expect(out).toContain("1 gap (█)");
    expect(out).toContain("reading order not established");
  });

  it("shows the gate's blockers and why the claim button is disabled", () => {
    const out = html(<TranscriptionWorksheet state={state()} target="target-a" reviewer={alice} onChanged={noop} />);
    expect(out).toContain("no letter judgment settled by two independent people");
    expect(out).toContain("claim this transcription");
    expect(out).toMatch(/disabled=""[^>]*[^>]*>Review: claim this transcription|Review: claim this transcription/);
  });

  it("shows the computed human review and its attributed validators", () => {
    const s = state({
      claim: { state: "ACTIVE", claim: { claimed_by: "dana", utc: "2026-09-18T00:00:00Z" }, ready: true, blockers: [] },
      human_review_preview: {
        independent_human_answers: 2, expert_validated: true, agreement: 1, ai_answers_ignored: 1,
        cells_covered: 1, expert_validators: [{ reviewer_id: "carol", reviewer_class: "SPECIALIST", task_id: "RT-0" }],
      },
    });
    const out = html(<TranscriptionWorksheet state={s} target="target-a" reviewer={alice} onChanged={noop} />);
    expect(out).toContain("by dana");
    expect(out).toContain("expert validation by carol");
    expect(out).toContain("1 model answer(s) ignored");
  });

  it("has no input for counts", () => {
    const out = html(<TranscriptionWorksheet state={state()} target="target-a" reviewer={alice} onChanged={noop} />);
    expect(out.toLowerCase()).not.toContain("independent_human_answers");
    expect(out).not.toMatch(/type="number"/);
  });
});

describe("the translation worksheet", () => {
  it("prints the proposal notice and the no-machine-translation statement", () => {
    const out = html(<TranslationWorksheet state={state()} target="target-a" reviewer={alice} onChanged={noop} />);
    expect(out).toContain("not a reading of an unread scroll");
    expect(out).toContain("there is no machine translation of Ancient Greek or Latin in this system");
    expect(out).toContain("record as a PROPOSAL");
  });

  it("labels every stored candidate PROPOSAL and says it is not a reading", () => {
    const s = state();
    s.translation.candidates = [
      {
        id: "TC-1", label: "PROPOSAL", proposed_by: "erin", proposed_by_class: "SPECIALIST",
        language: "Ancient Greek", superseded: false,
        candidate: { text: "in the beginning", source_token_ids: ["cell-abc123"], alternatives: ["at first"] },
        is_a_reading_of_an_unread_scroll: false, utc: "u",
      },
    ];
    const out = html(<TranslationWorksheet state={s} target="target-a" reviewer={alice} onChanged={noop} />);
    expect(out).toContain("PROPOSAL");
    expect(out).toContain("in the beginning");
    expect(out).toContain("erin (specialist)");
    expect(out).toContain("alternatives: at first");
    expect(out).toContain("not a reading of an unread scroll");
  });
});

describe("the OCR / HTR slot", () => {
  it("says PROVIDER_NOT_INSTALLED and offers no way to run one", () => {
    const out = html(<HtrSlotView state={state()} target="target-a" onChanged={noop} />);
    expect(out).toContain("provider not installed");
    expect(out).toContain("PROVIDER_NOT_INSTALLED");
    expect(out).toContain('aria-disabled="true"');
    expect(out).toContain("counted as a person agreeing");
    expect(out).toContain("record an external proposal");
  });
});

describe("candidates and limits", () => {
  it("says candidates are places to look, not findings, and blocks an unnamed segment", () => {
    const out = html(<OpenCandidates target="target-a" tasks={3} onChanged={noop} />);
    expect(out).toContain("not findings");
    expect(out).toContain("3 tasks exist");
    expect(out).toContain("name the segment");
  });

  it("lists every limitation by id", () => {
    const out = html(<LimitationsView state={state()} />);
    expect(out).toContain("NO_UNREAD_SCROLL_READING");
    expect(out).toContain("NOT_PUBLISHED");
  });
});

describe("packets", () => {
  const files = [
    { path: "READING_BOARD.json", status: "OK", sha256_declared: "a".repeat(64) },
    { path: "HUMAN_REVIEW_RECORDS.json", status: "HASH_MISMATCH", sha256_declared: "b".repeat(64) },
    { path: "TRANSLATION_CANDIDATES.json", status: "MISSING", sha256_declared: "c".repeat(64) },
  ];
  const base: PacketVerification = {
    packet_id: "PKT-1", verdict: "INVALID", valid: false, reasons: ["HUMAN_REVIEW_RECORDS.json does not hash as exported (HASH_MISMATCH)"],
    files: files as PacketVerification["files"], unlisted_files: [], roots: [{ path: "/r/root.json", status: "OK" }],
    citations: [{ declared_path: "/r/prep.json", status: "MISSING" }],
    limitations: { status: "MISSING", claim_limits: "OK", version: 1 }, manifest: { self_hash: "OK" },
    what_valid_means: "the files are byte-identical to what was exported. It does not mean anything in the packet is true.",
  };

  it("shows per-file OK / HASH_MISMATCH / MISSING and refuses to look valid", () => {
    const out = html(<PacketVerificationView v={base} />);
    expect(out).toContain("INVALID");
    for (const s of ["OK", "HASH_MISMATCH", "MISSING"]) expect(out).toContain(s);
    expect(out).toContain("READING_BOARD.json");
    expect(out).toContain("does not hash as exported");
    expect(out).toContain("Limitations block");
    expect(out).toContain("does not mean anything in the packet is true");
    expect(out).toContain("/r/prep.json");
  });

  it("a valid packet says what valid means", () => {
    const v: PacketVerification = {
      ...base, verdict: "VALID", valid: true, reasons: [],
      files: [files[0]] as PacketVerification["files"], citations: [], roots: [],
      limitations: { status: "OK", claim_limits: "OK", version: 1 },
    };
    const out = html(<PacketVerificationView v={v} />);
    expect(out).toContain("VALID");
    expect(out).toContain("byte-identical");
    expect(out).toContain("this packet cites no receipt");
  });

  it("lists exported packets as not published, each with a reopen control", () => {
    const rows: PacketListRow[] = [
      { packet_id: "PKT-abc", path: "/p", readable: true, generated_utc: "2026-09-18T00:00:00Z", files: 7, citations: { roots: 1, cited: 2 }, published: false },
    ];
    const out = html(<PacketListView packets={rows} onVerify={noop} busy={null} />);
    expect(out).toContain("PKT-abc");
    expect(out).toContain("not published");
    expect(out).toContain("Reopen and verify");
  });

  it("says when nothing was exported", () => {
    expect(html(<PacketListView packets={[]} onVerify={noop} busy={null} />)).toContain("none has been exported");
  });
});
