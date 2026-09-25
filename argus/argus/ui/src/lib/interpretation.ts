import { useSyncExternalStore } from "react";
import { type UnrollTarget } from "../api";
import { partitionFixtures } from "./fixtures";


export const HUMAN_CLASSES = ["OPERATOR", "SPECIALIST", "TRUSTED_COLLABORATOR", "COMMUNITY"] as const;
export type HumanClass = (typeof HUMAN_CLASSES)[number];
export const EXPERT_CLASSES: readonly string[] = ["OPERATOR", "SPECIALIST"];

export const CLASS_MEANS: Record<HumanClass, string> = {
  OPERATOR: "the person running this system",
  SPECIALIST: "a papyrologist or other domain specialist",
  TRUSTED_COLLABORATOR: "a collaborator the operator trusts",
  COMMUNITY: "a community reviewer",
};

export const PROPOSAL_LABEL = "PROPOSAL";
export const PROPOSAL_NOTICE =
  "A proposal drafted by a named person from human-agreed letters. It is not a reading of an " +
  "unread scroll, not a detector result and not prize evidence.";
export const READING_NOTICE =
  "PROPOSED READING — not a measurement. Gaps are gaps: a cell nobody lettered is never filled, " +
  "and reading order across cells is not established.";
export const HUMAN_ROLE = "HUMAN_JUDGMENT";
export const MODEL_ROLE = "PREDICTION_DISCOVERY_EVIDENCE";
export const IDENTITY_NOTICE =
  "Your name and class are self-declared and recorded as such. Independence is attested by a " +
  "distinct name and a distinct browser session, not authenticated: switching to another name " +
  "starts a new session, which is what a second person taking the keyboard is. It cannot stop " +
  "one person from impersonating two, and the packet says so.";


export interface Reviewer {
  id: string;
  cls: HumanClass;
}

const NAME_RE = /^[\p{L}\p{N}_][\p{L}\p{N}_ .@'-]{0,62}$/u;

export function tidyName(raw: string): string {
  return raw.split(/\s+/).filter(Boolean).join(" ");
}

export function reviewerProblem(r: { id: string; cls: string } | null): string | null {
  if (!r || !tidyName(r.id)) return "name yourself first: answers are attributed to a person";
  if (!NAME_RE.test(tidyName(r.id))) {
    return "a reviewer name is 1-63 characters: letters, digits, spaces and . _ @ ' - only";
  }
  if (!(HUMAN_CLASSES as readonly string[]).includes(r.cls)) return "choose a reviewer class";
  return null;
}

export function mayValidate(r: { cls: string } | null): boolean {
  return Boolean(r && EXPERT_CLASSES.includes(r.cls));
}

const STORE_KEY = "argus.reviewer";
let current: Reviewer | null = null;
let loaded = false;
const listeners = new Set<() => void>();

function load(): void {
  if (loaded) return;
  loaded = true;
  try {
    const raw = sessionStorage.getItem(STORE_KEY);
    if (raw) {
      const p = JSON.parse(raw) as Reviewer;
      if (!reviewerProblem(p)) current = { id: tidyName(p.id), cls: p.cls };
    }
  } catch {
  }
}

export function getReviewer(): Reviewer | null {
  load();
  return current;
}

export function setReviewer(r: Reviewer | null): void {
  load();
  current = r && !reviewerProblem(r) ? { id: tidyName(r.id), cls: r.cls } : null;
  try {
    if (current) sessionStorage.setItem(STORE_KEY, JSON.stringify(current));
    else sessionStorage.removeItem(STORE_KEY);
  } catch {
  }
  listeners.forEach((l) => l());
}

export function useReviewer(): Reviewer | null {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    getReviewer,
    () => null,
  );
}


export interface GlyphView {
  task_id: string;
  state: string;
  settled: boolean;
  human_top: string | null;
  independent_humans: number;
  human_agreement: number;
  char: string | null;
  accepted_by: string[];
  expert_validated: boolean;
  ai_answers: number;
  alphabet: string | null;
}

export interface BoardCell {
  cell_id: string;
  extent: number[];
  state: string;
  review: { task_id?: string; reviewers?: string[]; answers?: { reviewer_id: string; reviewer_class: string; evidence_role?: string }[] };
  glyph?: GlyphView | null;
}

export interface ClaimSummary {
  state: "NONE" | "ACTIVE" | "STALE";
  claim: { claimed_by?: string; claimed_by_class?: string; utc?: string } | null;
  why?: string | null;
  ready: boolean;
  blockers: string[];
}

export interface HumanReviewPreview {
  independent_human_answers: number;
  expert_validated: boolean;
  agreement: number;
  ai_answers_ignored: number;
  cells_covered: number;
  expert_validators: { reviewer_id: string; reviewer_class: string; task_id: string }[];
}

export interface ProposedReading {
  text: string;
  slots: number;
  supported: number;
  gaps: number;
  gap_marker: string;
  characters: { index: number; cell_id: string; char: string; gap: boolean }[];
  reading_order_established: boolean;
  what_this_is_not: string[];
}

export interface TranslationCandidate {
  id: string;
  label: string;
  proposed_by: string;
  proposed_by_class: string;
  language: string | null;
  superseded: boolean;
  candidate: { text: string; source_token_ids: string[]; alternatives: string[] };
  is_a_reading_of_an_unread_scroll: boolean;
  utc: string;
}

export interface TranslationState {
  plan: { state: string; why?: string; next?: string; accepted_token_ids?: string[]; language?: string | null };
  claim_state: string;
  candidates: TranslationCandidate[];
  label: string;
  no_machine_translation: string;
  what_a_candidate_is: string;
}

export interface InterpretationState {
  schema: string;
  target: string;
  review_tasks: { present: boolean; ink: number; glyph: number; total: number };
  board_state: "BOARD" | "NO_BOARD" | "NO_TASKS";
  why: string | null;
  board: { class: string; cell_count: number; orientation: string; cells: BoardCell[]; this_is_not: string[] } | null;
  board_sha256: string | null;
  refused_regions: { task_id: string; reason: string }[];
  claim: ClaimSummary;
  human_review_preview: HumanReviewPreview | null;
  proposed_reading: ProposedReading | null;
  regions_accepted: number;
  reviewers: { reviewer_id: string; reviewer_class: string; evidence_role: string; identity_attestation: string; answers: number; source?: { source_id: string; source_version: string } | null }[];
  language: { language: string; declared_by: string; utc: string } | null;
  translation: TranslationState;
  htr_slot: { state: string; why: string; accepts: string; recorded_as: string; never: string[]; action: string };
  reviewer_rules: Record<string, string | number>;
  claim_limits: Record<string, boolean>;
  limitations: { id: string; text: string }[];
}

export interface PacketListRow {
  packet_id: string;
  path: string;
  readable: boolean;
  generated_utc?: string;
  scroll?: string;
  files?: number;
  citations?: { roots: number; cited: number };
  published?: boolean;
}

export interface VerifiedFile {
  path: string;
  status: "OK" | "HASH_MISMATCH" | "MISSING";
  sha256_declared?: string | null;
  sha256_actual?: string | null;
}

export interface PacketVerification {
  packet_id: string | null;
  verdict: "VALID" | "INVALID" | "REFUSED";
  valid: boolean;
  reasons: string[];
  files: VerifiedFile[];
  unlisted_files: string[];
  roots: { path: string; status: string }[];
  citations: { declared_path: string; status: string }[];
  limitations?: { status: string; claim_limits: string; version?: number };
  manifest?: { self_hash: string };
  what_valid_means: string;
}


export function glyphAlphabet(name: string): string[] {
  if (name === "LATIN") return Array.from({ length: 26 }, (_, i) => String.fromCharCode(65 + i));
  return Array.from({ length: 0x3ca - 0x3b1 }, (_, i) => String.fromCharCode(0x3b1 + i));
}

export function extentLabel(e: number[]): string {
  return e.length === 4 ? `rows ${e[0]}–${e[1]} · cols ${e[2]}–${e[3]}` : "no extent";
}

export type Tone = "ok" | "warn" | "bad" | "info" | "idle";

export function statusTone(status: string): Tone {
  if (status === "OK" || status === "VALID") return "ok";
  if (status === "HASH_MISMATCH" || status === "MISSING" || status === "INVALID" || status === "REFUSED") return "bad";
  return "warn";
}

export function verdictLine(v: PacketVerification): string {
  if (v.verdict === "VALID") {
    return "Valid: every file is byte-identical to what was exported and the limitations are intact.";
  }
  if (v.verdict === "REFUSED") return `Refused: ${v.reasons[0] ?? "this is not a packet"}`;
  return `Invalid: ${v.reasons.length} problem(s) found.`;
}

export function claimBlockedReason(s: InterpretationState | null): string | null {
  if (!s) return "the interpretation state has not loaded";
  if (s.board_state !== "BOARD") return s.why ?? "there is no reading board yet";
  if (s.claim.state === "ACTIVE") return "this exact board is already claimed";
  if (!s.claim.ready) return s.claim.blockers[0] ?? "the claim gate is not satisfied";
  return null;
}

export function translationBlockedReason(s: InterpretationState | null): string | null {
  if (!s) return "the interpretation state has not loaded";
  if (s.translation.claim_state !== "ACTIVE") {
    return "translation only follows an ACTIVE transcription claim on the current board";
  }
  if (!s.language) return "declare the language first";
  return null;
}

export function glyphParams(
  target: string,
  taskId: string,
  answer: string,
  alphabet: string,
  reviewer: Reviewer,
): Record<string, unknown> {
  return {
    target, task_id: taskId, answer, alphabet, reviewer_id: reviewer.id, reviewer_class: reviewer.cls,
  };
}

export function claimParams(target: string, reviewer: Reviewer, why: string): Record<string, unknown> {
  return {
    target, claimed_by: reviewer.id, claimed_by_class: reviewer.cls, ...(why ? { why } : {}),
  };
}

export function translationParams(
  target: string,
  tokens: string[],
  text: string,
  alternatives: string,
  reviewer: Reviewer,
  supersedes?: string,
): Record<string, unknown> {
  return {
    target,
    source_token_ids: tokens,
    text: text.trim(),
    alternatives: alternatives.split("\n").map((a) => a.trim()).filter(Boolean),
    proposed_by: reviewer.id,
    proposed_by_class: reviewer.cls,
    ...(supersedes ? { supersedes } : {}),
  };
}

export function receiptsFrom(raw: string): string[] {
  return raw.split("\n").map((l) => l.trim()).filter(Boolean);
}

export function pickTarget(targets: UnrollTarget[], scroll: string | null | undefined): UnrollTarget | null {
  if (!scroll) return null;
  const { real } = partitionFixtures(targets);
  return real.find((t) => (t.name || "").toLowerCase().includes(scroll.toLowerCase())) ?? null;
}
