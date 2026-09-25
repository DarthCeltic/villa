import { useMemo, useState } from "react";
import { type UnrollIndex } from "../api";
import { partitionFixtures } from "../lib/fixtures";
import { useArgusContext } from "../lib/context";
import { submitReviewValidation } from "../lib/governed";
import {
  EXPERT_CLASSES,
  PROPOSAL_LABEL,
  PROPOSAL_NOTICE,
  READING_NOTICE,
  claimBlockedReason,
  claimParams,
  extentLabel,
  glyphAlphabet,
  glyphParams,
  mayValidate,
  pickTarget,
  reviewerProblem,
  translationBlockedReason,
  translationParams,
  useReviewer,
  type BoardCell,
  type InterpretationState,
  type Reviewer,
} from "../lib/interpretation";
import { usePoll } from "../lib/poll";
import { PlanApprove } from "./PlanApprove";
import { ReviewerIdentityForm } from "./ReviewerIdentity";
import { OpsBadge, OpsFact, OpsFacts, OpsSection, OpsSource, OpsUnknown } from "./OpsKit";


export function InterpretationPanel() {
  const index = usePoll<UnrollIndex>("/api/unroll", { intervalMs: 120000 });
  const { ctx } = useArgusContext();
  const targets = index.data?.targets ?? [];
  const real = useMemo(() => partitionFixtures(targets).real, [targets]);
  const fromScroll = pickTarget(targets, ctx.scroll);
  const [chosen, setChosen] = useState("");
  const key = fromScroll?.key ?? (chosen || null);

  return (
    <OpsSection
      control="review.interpretation"
      title="Interpretation — from reviewed regions to a packet"
      hint="Candidates, a reading board, letters, a transcription claim and translation proposals. Every stage says what it is not: nothing here reads an unread scroll. No reading, OCR, transcription or translation consumes a sealed review task until two named people have independently reviewed it with the image in front of them."
    >
      {fromScroll ? (
        <div className="ops-note" data-control="review.interpretation.target">
          Target <span className="ops-mono">{fromScroll.key}</span> follows the selected scroll.
        </div>
      ) : (
        <div className="ops-note" data-control="review.interpretation.choose">
          {ctx.scroll
            ? `No exported layer stack matches the selected scroll (${ctx.scroll}). `
            : "No scroll is selected. "}
          Choose the exported target to work on; this panel does not pick one for you.{" "}
          <select
            value={chosen}
            aria-label="exported target"
            data-control="review.interpretation.choose.select"
            onChange={(e) => setChosen(e.target.value)}
            style={{ font: "inherit" }}
          >
            <option value="">— choose a target —</option>
            {real.map((t) => (
              <option key={t.key} value={t.key}>
                {t.name}
              </option>
            ))}
          </select>
        </div>
      )}
      {key ? (
        <InterpretationBody target={key} />
      ) : (
        <MissingInterpretationStages why="no exported target is chosen, so there is no reading board to transcribe or translate. Nothing is substituted. Select an exported target above, or return after this scroll's layer-stack export exists." />
      )}
    </OpsSection>
  );
}

function InterpretationBody({ target }: { target: string }) {
  const s = usePoll<InterpretationState>(`/api/interpretation?target=${encodeURIComponent(target)}`, {
    intervalMs: 20000,
  });
  if (!s.data) {
    return (
      <MissingInterpretationStages
        why={s.failure
          ? `this target's interpretation state failed to load: ${s.failure.kind}. Nothing is assumed in its place.`
          : "this target's interpretation state is still loading. Nothing is assumed in its place."}
      />
    );
  }
  return <InterpretationView state={s.data} target={target} onChanged={s.refresh} />;
}

export function MissingInterpretationStages({ why }: { why: string }) {
  const { ctx } = useArgusContext();
  const q = ctx.scroll ? `scroll=${encodeURIComponent(ctx.scroll)}` : "";
  const reviewQueue = `/review${q ? `?${q}` : ""}#review.queue`;
  const transcription = `/review${q ? `?${q}` : ""}#review.interpretation.transcription`;
  return (
    <div style={{ display: "grid", gap: 14 }} data-control="review.interpretation.unavailable">
      <div id="review.interpretation.transcription" data-control="review.interpretation.transcription" style={{ display: "grid", gap: 6 }}>
        <h3 style={{ margin: 0 }}>3 · Transcription worksheet</h3>
        <ul className="ops-list">
          <OpsUnknown what="A transcription worksheet" why={why} next="Go to candidate review: finish the reading board for this scroll" nextTo={reviewQueue} nextControl="review.interpretation.transcription.next" />
        </ul>
      </div>
      <div id="review.interpretation.translation" data-control="review.interpretation.translation" style={{ display: "grid", gap: 6 }}>
        <h3 style={{ margin: 0 }}>4 · Language and translation worksheet</h3>
        <ul className="ops-list">
          <OpsUnknown what="A translation worksheet" why={why} next="Go to the transcription worksheet: a reviewed transcription comes first" nextTo={transcription} nextControl="review.interpretation.translation.next" />
        </ul>
      </div>
    </div>
  );
}


export function InterpretationView({
  state,
  target,
  onChanged,
}: {
  state: InterpretationState;
  target: string;
  onChanged: () => void;
}) {
  const reviewer = useReviewer();
  return (
    <div style={{ display: "grid", gap: 14 }} data-control="review.interpretation.view">
      <OpenCandidates target={target} tasks={state.review_tasks.total} onChanged={onChanged} />
      <ReviewerIdentityForm />
      <ReadingBoardView state={state} target={target} reviewer={reviewer} onChanged={onChanged} />
      <TranscriptionWorksheet state={state} target={target} reviewer={reviewer} onChanged={onChanged} />
      <TranslationWorksheet state={state} target={target} reviewer={reviewer} onChanged={onChanged} />
      <HtrSlotView state={state} target={target} onChanged={onChanged} />
      <LimitationsView state={state} />
      <OpsSource route={`/api/interpretation?target=${target}`} field="board, claim, proposed_reading, translation" />
    </div>
  );
}


export function OpenCandidates({
  target,
  tasks,
  onChanged,
}: {
  target: string;
  tasks: number;
  onChanged: () => void;
}) {
  const [segment, setSegment] = useState("");
  const [region, setRegion] = useState("0,0,0,0");
  const parsed = region.split(",").map((v) => Number(v.trim()));
  const regionOk = parsed.length === 4 && parsed.every((v) => Number.isInteger(v));
  return (
    <div data-control="review.interpretation.open" style={{ display: "grid", gap: 6 }}>
      <h3 style={{ margin: 0 }}>1 · Open candidates for review</h3>
      <div className="ops-note">
        Ranks tiles of the sealed probability planes under a frozen rule and appends them as review tasks. They are places
        for a person to look, not findings. Existing tasks and
        every recorded answer are kept.{" "}
        <b>{tasks} task{tasks === 1 ? "" : "s"} exist for this target.</b>
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <input
          value={segment}
          placeholder="segment id"
          aria-label="segment"
          data-control="review.interpretation.open.segment"
          onChange={(e) => setSegment(e.target.value)}
          style={{ font: "inherit", padding: "3px 6px" }}
        />
        <input
          value={region}
          placeholder="y0,y1,x0,x1"
          aria-label="region y0,y1,x0,x1"
          data-control="review.interpretation.open.region"
          onChange={(e) => setRegion(e.target.value)}
          style={{ font: "inherit", padding: "3px 6px" }}
        />
      </div>
      <PlanApprove
        action="candidate.open"
        label="open candidates"
        params={{ target, segment: segment.trim(), region: parsed, limit: 20 }}
        disabledReason={
          !segment.trim() ? "name the segment" : !regionOk ? "region is four integers: y0,y1,x0,x1" : null
        }
        onDone={onChanged}
      />
    </div>
  );
}


export function ReadingBoardView({
  state,
  target,
  reviewer,
  onChanged,
}: {
  state: InterpretationState;
  target: string;
  reviewer: Reviewer | null;
  onChanged: () => void;
}) {
  const board = state.board;
  return (
    <div data-control="review.interpretation.board" style={{ display: "grid", gap: 6 }}>
      <h3 style={{ margin: 0 }}>2 · Reading board and letters</h3>
      <div className="ops-note">
        A spatial layout of regions at least {String(state.reviewer_rules.min_independent_humans)} independent{" "}
        <b>people</b> accepted as ink. It is a board, not a transcription and not OCR
        {state.claim.state === "ACTIVE" ? " — until the claim below, which covers this exact board" : ""}. A model's
        proposal is never one of the people. Accept regions as ink in the{" "}
        <a href="/workbench" data-control="review.interpretation.board.workbench">
          Workbench Review layout
        </a>
        , beside the image.
      </div>
      {!board ? (
        <ul className="ops-list">
          <OpsUnknown what="A reading board" why={state.why ?? "no region has been accepted yet"} />
        </ul>
      ) : (
        <div className="ops-scroll-x">
          <table className="ops-table" data-control="review.interpretation.board.table">
            <thead>
              <tr>
                <th>Region</th>
                <th>Accepted by</th>
                <th>State</th>
                <th>Letter</th>
                <th>Record a letter</th>
              </tr>
            </thead>
            <tbody>
              {board.cells.map((c) => (
                <CellRow key={c.cell_id} cell={c} target={target} reviewer={reviewer} onChanged={onChanged} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      {state.refused_regions.length > 0 ? (
        <div className="ops-note" data-control="review.interpretation.board.refused">
          {state.refused_regions.length} settled task(s) were refused for the board, not dropped:{" "}
          {state.refused_regions.map((r) => `${r.task_id} — ${r.reason}`).join("; ")}
        </div>
      ) : null}
      <div style={{ color: "var(--ink-faint)" }}>
        Reviewers so far:{" "}
        {state.reviewers.length === 0
          ? "none"
          : state.reviewers
              .map((r) =>
                r.reviewer_class === "AI_AGENT"
                  ? `${r.reviewer_id} (model proposal, ${r.answers})`
                  : `${r.reviewer_id} (${r.reviewer_class.replace(/_/g, " ").toLowerCase()}, ${r.answers})`,
              )
              .join(" · ")}
      </div>
    </div>
  );
}

function ValidateButton({
  target,
  taskId,
  value,
  reviewer,
  label,
  onChanged,
}: {
  target: string;
  taskId: string;
  value: string;
  reviewer: Reviewer | null;
  label: string;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const blocked =
    reviewerProblem(reviewer) ??
    (mayValidate(reviewer) ? null : `only ${EXPERT_CLASSES.join(" or ").toLowerCase()} may validate`);
  return (
    <span>
      <button
        type="button"
        className="ops-btn"
        disabled={busy || Boolean(blocked)}
        title={blocked ?? "an attributed expert validation; you may not have answered this task yourself"}
        data-control={`review.interpretation.validate.${taskId}`}
        onClick={async () => {
          if (!reviewer) return;
          setBusy(true);
          setErr(null);
          try {
            await submitReviewValidation(target, taskId, value, reviewer);
            onChanged();
          } catch (e) {
            setErr(e instanceof Error ? e.message : String(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        {label}
      </button>
      {err ? <span style={{ color: "var(--status-refused)" }}> {err}</span> : null}
    </span>
  );
}

function CellRow({
  cell,
  target,
  reviewer,
  onChanged,
}: {
  cell: BoardCell;
  target: string;
  reviewer: Reviewer | null;
  onChanged: () => void;
}) {
  const g = cell.glyph ?? null;
  const [alphabet, setAlphabet] = useState(g?.alphabet ?? "GREEK");
  const [letter, setLetter] = useState("");
  const taskId = cell.review.task_id ?? "";
  const people = (cell.review.answers ?? []).filter((a) => a.reviewer_class !== "AI_AGENT");
  const blocked = reviewerProblem(reviewer) ?? (letter ? null : "choose a letter, or 'not a letter' / 'cannot tell'");
  const letters = glyphAlphabet(g?.alphabet ?? alphabet);
  return (
    <tr data-control={`review.interpretation.cell.${cell.cell_id}`}>
      <td>
        <span className="ops-mono">{extentLabel(cell.extent)}</span>
      </td>
      <td>{people.map((a) => a.reviewer_id).join(", ") || "—"}</td>
      <td>
        <OpsBadge tone={cell.state === "ACCEPTED" ? "info" : "ok"}>{cell.state.replace(/_/g, " ").toLowerCase()}</OpsBadge>
        {cell.state === "ACCEPTED" ? (
          <ValidateButton
            target={target}
            taskId={taskId}
            value="INK"
            reviewer={reviewer}
            label="Validate as ink"
            onChanged={onChanged}
          />
        ) : null}
      </td>
      <td data-control={`review.interpretation.cell.${cell.cell_id}.letter`}>
        {!g ? (
          "no letter judgment yet"
        ) : g.settled ? (
          <>
            <b>{g.char ?? (g.human_top ?? "").replace(/_/g, " ").toLowerCase()}</b> · {g.accepted_by.join(", ")}
            {g.expert_validated ? " · validated" : ""}
          </>
        ) : (
          <>
            {g.independent_humans} of 2 people so far ({g.state.replace(/_/g, " ").toLowerCase()})
          </>
        )}
        {g && g.settled && !g.expert_validated && g.human_top ? (
          <ValidateButton
            target={target}
            taskId={g.task_id}
            value={g.human_top}
            reviewer={reviewer}
            label="Validate letter"
            onChanged={onChanged}
          />
        ) : null}
      </td>
      <td>
        <div style={{ display: "grid", gap: 4 }}>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {!g ? (
              <select
                value={alphabet}
                aria-label="alphabet"
                onChange={(e) => {
                  setAlphabet(e.target.value);
                  setLetter("");
                }}
                style={{ font: "inherit" }}
              >
                <option value="GREEK">Greek</option>
                <option value="LATIN">Latin</option>
              </select>
            ) : (
              <span style={{ color: "var(--ink-faint)" }}>{(g.alphabet ?? "").toLowerCase()}</span>
            )}
            <select
              value={letter}
              aria-label="letter"
              data-control={`review.interpretation.cell.${cell.cell_id}.pick`}
              onChange={(e) => setLetter(e.target.value)}
              style={{ font: "inherit" }}
            >
              <option value="">— letter —</option>
              {letters.map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
              <option value="NOT_A_LETTER">not a letter</option>
              <option value="CANNOT_TELL">cannot tell</option>
            </select>
          </div>
          <PlanApprove
            action="glyph.annotate"
            label="record my letter"
            params={reviewer ? glyphParams(target, taskId, letter, g?.alphabet ?? alphabet, reviewer) : {}}
            disabledReason={blocked}
            onDone={onChanged}
          />
        </div>
      </td>
    </tr>
  );
}


export function TranscriptionWorksheet({
  state,
  target,
  reviewer,
  onChanged,
}: {
  state: InterpretationState;
  target: string;
  reviewer: Reviewer | null;
  onChanged: () => void;
}) {
  const [why, setWhy] = useState("");
  const reading = state.proposed_reading;
  const hr = state.human_review_preview;
  const blocked = claimBlockedReason(state) ?? reviewerProblem(reviewer);
  return (
    <div id="review.interpretation.transcription" data-control="review.interpretation.transcription" style={{ display: "grid", gap: 6 }}>
      <h3 style={{ margin: 0 }}>3 · Transcription worksheet</h3>
      <div className="ops-note" data-control="review.interpretation.transcription.notice">
        {READING_NOTICE}
      </div>
      {reading ? (
        <div
          className="ops-mono"
          data-control="review.interpretation.transcription.text"
          style={{ fontSize: "1.3em", letterSpacing: "0.15em", wordBreak: "break-all" }}
          aria-label="proposed reading with gaps"
        >
          {reading.text}
        </div>
      ) : (
        <OpsUnknown what="A proposed reading" why="there is no reading board to lay letters on" />
      )}
      {reading ? (
        <div style={{ color: "var(--ink-faint)" }}>
          {reading.supported} lettered · {reading.gaps} gap{reading.gaps === 1 ? "" : "s"} ({reading.gap_marker}) · reading
          order {reading.reading_order_established ? "established" : "not established"}
        </div>
      ) : null}
      <OpsFacts>
        <OpsFact label="Claim on this board">
          <OpsBadge tone={state.claim.state === "ACTIVE" ? "ok" : state.claim.state === "STALE" ? "warn" : "idle"}>
            {state.claim.state.toLowerCase()}
          </OpsBadge>{" "}
          {state.claim.claim
            ? `by ${state.claim.claim.claimed_by ?? "unknown"}${state.claim.claim.utc ? ` at ${state.claim.claim.utc}` : ""}`
            : "no transcription is claimed"}
          {state.claim.why ? <div className="ops-note">{state.claim.why}</div> : null}
        </OpsFact>
        <OpsFact label="Human review, computed">
          {hr ? (
            <>
              {hr.independent_human_answers} independent people (the fewest on any cell) · agreement{" "}
              {hr.agreement.toFixed(2)} · expert validation{" "}
              {hr.expert_validated
                ? `by ${Array.from(new Set(hr.expert_validators.map((v) => v.reviewer_id))).join(", ")}`
                : "not complete"}{" "}
              · {hr.ai_answers_ignored} model answer(s) ignored
            </>
          ) : (
            "not computable until a board exists"
          )}
        </OpsFact>
      </OpsFacts>
      {state.claim.blockers.length > 0 && state.claim.state !== "ACTIVE" ? (
        <ul className="ops-list" data-control="review.interpretation.transcription.blockers">
          {state.claim.blockers.map((b, i) => (
            <li key={i} className="ops-item" data-tone="warn">
              <div className="ops-item-body">blocked: {b}</div>
            </li>
          ))}
        </ul>
      ) : null}
      <input
        value={why}
        placeholder="why you are claiming it (optional)"
        aria-label="claim rationale"
        onChange={(e) => setWhy(e.target.value)}
        style={{ font: "inherit", padding: "3px 6px" }}
      />
      <PlanApprove
        action="transcription.claim"
        label="claim this transcription"
        params={reviewer ? claimParams(target, reviewer, why) : {}}
        disabledReason={blocked}
        why="the human review is computed from the merged review tally; it accepts no counts"
        onDone={onChanged}
      />
    </div>
  );
}


export function TranslationWorksheet({
  state,
  target,
  reviewer,
  onChanged,
}: {
  state: InterpretationState;
  target: string;
  reviewer: Reviewer | null;
  onChanged: () => void;
}) {
  const [language, setLanguage] = useState("");
  const [tokens, setTokens] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [alts, setAlts] = useState("");
  const cells = state.board?.cells ?? [];
  const blocked = translationBlockedReason(state);
  const proposeBlocked =
    blocked ??
    reviewerProblem(reviewer) ??
    (tokens.length === 0 ? "cite at least one accepted token" : !text.trim() ? "write the phrase" : null);
  const t = state.translation;
  return (
    <div id="review.interpretation.translation" data-control="review.interpretation.translation" style={{ display: "grid", gap: 6 }}>
      <h3 style={{ margin: 0 }}>4 · Language and translation worksheet</h3>
      <div className="ops-note" data-control="review.interpretation.translation.notice">
        {PROPOSAL_NOTICE} {t.no_machine_translation}.
      </div>
      <OpsFacts>
        <OpsFact label="Declared language">
          {state.language
            ? `${state.language.language} — declared by ${state.language.declared_by}`
            : "not declared"}
        </OpsFact>
        <OpsFact label="Worksheet">
          <OpsBadge tone={t.plan.state === "READY_FOR_REVIEW" ? "ok" : "idle"}>
            {t.plan.state.replace(/_/g, " ").toLowerCase()}
          </OpsBadge>{" "}
          {t.plan.why ?? t.plan.next ?? ""}
        </OpsFact>
      </OpsFacts>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <input
          value={language}
          placeholder="language, e.g. Ancient Greek"
          aria-label="language"
          data-control="review.interpretation.language"
          onChange={(e) => setLanguage(e.target.value)}
          style={{ font: "inherit", padding: "3px 6px" }}
        />
        <PlanApprove
          action="language.write"
          label="declare the language"
          params={reviewer ? { target, language: language.trim(), declared_by: reviewer.id } : {}}
          disabledReason={reviewerProblem(reviewer) ?? (language.trim() ? null : "name the language")}
          onDone={onChanged}
        />
      </div>
      <div className="ops-note">Cite the accepted tokens (cells) the phrase rests on:</div>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        {cells.map((c) => (
          <label key={c.cell_id} style={{ display: "flex", gap: 4, alignItems: "center" }}>
            <input
              type="checkbox"
              checked={tokens.includes(c.cell_id)}
              onChange={(e) =>
                setTokens((cur) => (e.target.checked ? [...cur, c.cell_id] : cur.filter((x) => x !== c.cell_id)))
              }
            />
            <span className="ops-mono">{c.glyph?.char ?? "█"}</span>
            <span style={{ color: "var(--ink-faint)" }}>{c.cell_id.slice(-6)}</span>
          </label>
        ))}
      </div>
      <textarea
        value={text}
        placeholder="your proposed translation of the cited tokens"
        aria-label="proposed translation"
        data-control="review.interpretation.translation.text"
        onChange={(e) => setText(e.target.value)}
        rows={2}
        style={{ font: "inherit", padding: "3px 6px" }}
      />
      <textarea
        value={alts}
        placeholder="alternatives you considered, one per line (optional)"
        aria-label="alternatives"
        onChange={(e) => setAlts(e.target.value)}
        rows={2}
        style={{ font: "inherit", padding: "3px 6px" }}
      />
      <PlanApprove
        action="translation.propose"
        label={`record as a ${PROPOSAL_LABEL}`}
        params={reviewer ? translationParams(target, tokens, text, alts, reviewer) : {}}
        disabledReason={proposeBlocked}
        onDone={onChanged}
      />
      <ul className="ops-list" data-control="review.interpretation.translation.list">
        {t.candidates.length === 0 ? (
          <OpsUnknown what="Translation proposals" why="none has been recorded for this target" />
        ) : (
          t.candidates.map((c) => (
            <li key={c.id} className="ops-item" data-tone={c.superseded ? "idle" : "info"}>
              <div className="ops-item-head">
                <OpsBadge tone="info">{c.label}</OpsBadge>
                <span className="ops-item-title">{c.candidate.text}</span>
                {c.superseded ? <OpsBadge tone="idle">superseded</OpsBadge> : null}
              </div>
              <div className="ops-item-body">
                {c.proposed_by} ({c.proposed_by_class.replace(/_/g, " ").toLowerCase()}) · {c.language ?? "language not declared"} ·
                cites {c.candidate.source_token_ids.length} token(s)
                {c.candidate.alternatives.length ? ` · alternatives: ${c.candidate.alternatives.join(" / ")}` : ""} · not a
                reading of an unread scroll
              </div>
            </li>
          ))
        )}
      </ul>
    </div>
  );
}


export function HtrSlotView({
  state,
  target,
  onChanged,
}: {
  state: InterpretationState;
  target: string;
  onChanged: () => void;
}) {
  const slot = state.htr_slot;
  const [task, setTask] = useState("");
  const [answer, setAnswer] = useState("INK");
  const [sid, setSid] = useState("");
  const [ver, setVer] = useState("");
  return (
    <div data-control="review.interpretation.htr" style={{ display: "grid", gap: 6 }}>
      <h3 style={{ margin: 0 }}>5 · OCR / HTR slot</h3>
      <div className="ops-note" data-control="review.interpretation.htr.state">
        <OpsBadge tone="warn">{slot.state.replace(/_/g, " ").toLowerCase()}</OpsBadge> {slot.why}
      </div>
      <div style={{ color: "var(--ink-faint)" }}>
        Accepts: {slot.accepts}. Recorded as: {slot.recorded_as}. Never: {slot.never.join("; ")}.
      </div>
      <div className="ops-control">
        <button type="button" className="ops-btn" aria-disabled="true" data-disabled-reason="true" data-control="review.interpretation.htr.run">
          Run a provider
        </button>
        <span className="ops-control-reason">PROVIDER_NOT_INSTALLED — there is no OCR/HTR model to run</span>
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <input value={task} placeholder="task id" aria-label="task id" onChange={(e) => setTask(e.target.value)} style={{ font: "inherit", padding: "3px 6px" }} />
        <input value={answer} placeholder="proposed answer" aria-label="proposed answer" onChange={(e) => setAnswer(e.target.value)} style={{ font: "inherit", padding: "3px 6px" }} />
        <input value={sid} placeholder="source id" aria-label="source id" onChange={(e) => setSid(e.target.value)} style={{ font: "inherit", padding: "3px 6px" }} />
        <input value={ver} placeholder="source version" aria-label="source version" onChange={(e) => setVer(e.target.value)} style={{ font: "inherit", padding: "3px 6px" }} />
      </div>
      <PlanApprove
        action="htr.propose"
        label="record an external proposal"
        params={{ target, task_id: task.trim(), answer: answer.trim(), source_id: sid.trim(), source_version: ver.trim() }}
        disabledReason={!task.trim() || !answer.trim() || !sid.trim() || !ver.trim() ? "name the task, the answer, the source and its version" : null}
        onDone={onChanged}
      />
    </div>
  );
}


export function LimitationsView({ state }: { state: InterpretationState }) {
  return (
    <details className="ops-details" data-control="review.interpretation.limits">
      <summary>What none of this claims ({state.limitations.length} limitations)</summary>
      <div className="ops-details-body">
        <ul className="ops-list">
          {state.limitations.map((l) => (
            <li key={l.id} className="ops-item" data-tone="idle">
              <div className="ops-item-head">
                <span className="ops-item-title ops-mono">{l.id}</span>
              </div>
              <div className="ops-item-body">{l.text}</div>
            </li>
          ))}
        </ul>
      </div>
    </details>
  );
}
