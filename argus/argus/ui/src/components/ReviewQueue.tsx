import { useRef, useState } from "react";
import { submitReviewAnswer, submitReviewValidation } from "../lib/governed";
import { mayValidate, reviewerProblem, useReviewer } from "../lib/interpretation";
import { ReviewerIdentityForm } from "./ReviewerIdentity";

const CONFIDENCE_LEVELS: { label: string; value: number }[] = [
  { label: "low", value: 0.3 },
  { label: "medium", value: 0.6 },
  { label: "high", value: 0.9 },
];

export interface ReviewTask {
  task_id: string;
  task_type: string;
  state: string;
  control?: string | null;
  binding: {
    surface_or_candidate_id: string;
    physical_coordinates: Record<string, unknown>;
    selected_because?: string;
    confidence_shown_to_reviewer?: boolean;
    exposure_state?: string;
    license_state?: string;
  };
  permitted_answers: string[];
  tally?: {
    total: number;
    agreement: number;
    human_answers?: number;
    ai_answers?: number;
    independent_human_reviewers?: number;
    consensus_top?: string | null;
  };
  answers?: { reviewer_id: string; reviewer_class: string; evidence_role?: string }[];
  validations?: { reviewer_id: string; reviewer_class: string; applied: boolean }[];
}

export interface ReviewPayload {
  counts: { tasks?: number; uncertainty_tasks?: number; blind_controls: number };
  tasks: ReviewTask[];
  no_controls_and_why?: string;
  controls_note?: string;
  task_type_and_why?: string;
  state_note?: string;
  selection?: string;
}

export function ReviewQueue({
  payload,
  target,
}: {
  payload: ReviewPayload | null | undefined;
  target?: string | null;
}) {
  const [openId, setOpenId] = useState<string | null>(null);
  const [tasks, setTasks] = useState<ReviewTask[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<Record<string, string>>({});
  const openedAtRef = useRef<Record<string, number>>({});
  const [confidence, setConfidence] = useState<Record<string, number>>({});
  const reviewer = useReviewer();
  const nameProblem = reviewerProblem(reviewer);
  const live = tasks ?? payload?.tasks ?? [];

  const toggleOpen = (taskId: string) => {
    setOpenId((cur) => {
      const next = cur === taskId ? null : taskId;
      if (next) openedAtRef.current[next] = Date.now();
      return next;
    });
  };

  const validate = async (taskId: string, value: string) => {
    if (!target || !reviewer) return;
    setBusy(taskId);
    setErr((e) => ({ ...e, [taskId]: "" }));
    try {
      const t = (await submitReviewValidation(target, taskId, value, reviewer)) as ReviewTask;
      setTasks((cur) => (cur ?? payload?.tasks ?? []).map((row) => (row.task_id === taskId ? t : row)));
    } catch (e) {
      setErr((cur) => ({ ...cur, [taskId]: e instanceof Error ? e.message : String(e) }));
    } finally {
      setBusy(null);
    }
  };

  const answer = async (taskId: string, value: string) => {
    if (!target || !reviewer) return;
    setBusy(taskId);
    setErr((e) => ({ ...e, [taskId]: "" }));
    const openedAt = openedAtRef.current[taskId] ?? Date.now();
    const conf = confidence[taskId] ?? CONFIDENCE_LEVELS[1]!.value;
    try {
      const t = (await submitReviewAnswer(
        target, taskId, value, conf, (Date.now() - openedAt) / 1000, undefined, reviewer,
      )) as ReviewTask;
      setTasks((cur) => (cur ?? payload?.tasks ?? []).map((row) => (row.task_id === taskId ? t : row)));
    } catch (e) {
      setErr((cur) => ({ ...cur, [taskId]: e instanceof Error ? e.message : String(e) }));
    } finally {
      setBusy(null);
    }
  };

  if (!payload) {
    return (
      <p style={{ color: "var(--ink-faint)", fontSize: "var(--t-small)", margin: 0 }}>
        No review tasks have been cut from this surface yet.
      </p>
    );
  }
  const total = payload.counts.tasks ?? payload.counts.uncertainty_tasks ?? live.length;
  const controls = payload.counts.blind_controls;

  return (
    <div style={{ display: "grid", gap: 8, minHeight: 0 }}>
      {target ? <ReviewerIdentityForm /> : null}
      <div style={{ fontSize: "var(--t-small)", color: "var(--ink-dim)" }}>
        <b style={{ color: "var(--ink)" }}>{total}</b> task{total === 1 ? "" : "s"}
        {controls > 0 ? (
          <>
            {" "}
            · <b style={{ color: "var(--ink)" }}>{controls}</b> blind control
            {controls === 1 ? "" : "s"} mixed in, not marked
          </>
        ) : (
          <> · no controls possible on this target</>
        )}
      </div>

      {(payload.no_controls_and_why || payload.controls_note) && (
        <p
          style={{
            margin: 0,
            fontSize: "var(--t-small)",
            color: "var(--ink-faint)",
            borderLeft: "2px solid var(--line-strong)",
            paddingLeft: 8,
          }}
        >
          {payload.no_controls_and_why ?? payload.controls_note}
        </p>
      )}
      {payload.task_type_and_why && (
        <p style={{ margin: 0, fontSize: "var(--t-small)", color: "var(--ink-faint)", borderLeft: "2px solid var(--line-strong)", paddingLeft: 8 }}>
          {payload.task_type_and_why}
        </p>
      )}

      <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 4, maxHeight: "34vh", overflowY: "auto" }}>
        {live.map((t) => {
          const on = openId === t.task_id;
          return (
            <li key={t.task_id}>
              <button
                onClick={() => toggleOpen(t.task_id)}
                className="interactive"
                style={{
                  width: "100%",
                  textAlign: "left",
                  background: on ? "var(--bg-raised-2)" : "transparent",
                  border: "1px solid var(--line)",
                  borderRadius: "var(--radius-sm)",
                  padding: "7px 9px",
                  color: "var(--ink)",
                  cursor: "pointer",
                  font: "inherit",
                  fontSize: "var(--t-small)",
                }}
              >
                <span style={{ fontFamily: "var(--mono)", fontSize: "var(--t-meta)", color: "var(--ink-faint)" }}>
                  {t.task_id}
                </span>
                <span style={{ display: "block", color: "var(--ink-dim)", fontSize: "var(--t-meta)" }}>
                  {t.task_type.replace(/_/g, " ").toLowerCase()} · {t.binding.surface_or_candidate_id}
                </span>
              </button>
              {on && (
                <div style={{ padding: "7px 9px 9px 12px", fontSize: "var(--t-small)", color: "var(--ink-dim)", display: "grid", gap: 4 }}>
                  <div>
                    <b style={{ color: "var(--ink)" }}>state</b> {t.state.replace(/_/g, " ").toLowerCase()}
                  </div>
                  <div>
                    <b style={{ color: "var(--ink)" }}>selected because</b>{" "}
                    {(t.binding.selected_because ?? "unrecorded").replace(/_/g, " ").toLowerCase()}
                  </div>
                  <div>
                    <b style={{ color: "var(--ink)" }}>model confidence</b>{" "}
                    <span style={{ color: "var(--ink-faint)" }}>
                      recorded, withheld from the reviewer on purpose
                    </span>
                  </div>
                  {t.tally && t.tally.total > 0 ? (
                    <div data-control={`review.tally.${t.task_id}`}>
                      <b style={{ color: "var(--ink)" }}>answered</b>{" "}
                      {t.tally.independent_human_reviewers ?? t.tally.human_answers ?? t.tally.total}{" "}
                      independent person{(t.tally.independent_human_reviewers ?? t.tally.total) === 1 ? "" : "s"}
                      {(t.tally.ai_answers ?? 0) > 0 ? (
                        <>
                          {" "}
                          · {t.tally.ai_answers} model proposal{t.tally.ai_answers === 1 ? "" : "s"} (never counted
                          as a person)
                        </>
                      ) : null}{" "}
                      — one data point on the ladder, not a result. Two independent people are needed.
                    </div>
                  ) : null}
                  {(t.answers ?? []).length > 0 ? (
                    <div style={{ color: "var(--ink-faint)" }} data-control={`review.answered-by.${t.task_id}`}>
                      answered by{" "}
                      {(t.answers ?? [])
                        .map((a) =>
                          a.reviewer_class === "AI_AGENT"
                            ? `${a.reviewer_id} (model proposal)`
                            : `${a.reviewer_id} (${a.reviewer_class.replace(/_/g, " ").toLowerCase()})`,
                        )
                        .join(", ")}
                    </div>
                  ) : null}
                  {target && nameProblem ? (
                    <div style={{ color: "var(--ink-faint)" }} data-control={`review.name-first.${t.task_id}`}>
                      {nameProblem}. An answer is a person's judgment and is recorded under their name.
                    </div>
                  ) : target && (t.answers ?? []).some((a) => a.reviewer_id.toLowerCase() === (reviewer?.id ?? "").toLowerCase()) ? (
                    <div style={{ color: "var(--ink-faint)" }}>
                      {reviewer?.id} already answered this task; a second independent answer needs a different
                      reviewer in their own session
                    </div>
                  ) : target ? (
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center" }}>
                      <label
                        style={{ display: "flex", gap: 4, alignItems: "center" }}
                        title="your own confidence in the answer you are about to give -- not the model's, which stays withheld"
                      >
                        <span className="small" style={{ color: "var(--ink-dim)" }}>
                          your confidence
                        </span>
                        <select
                          value={confidence[t.task_id] ?? CONFIDENCE_LEVELS[1]!.value}
                          data-control={`review.confidence.${t.task_id}`}
                          onChange={(e) =>
                            setConfidence((c) => ({ ...c, [t.task_id]: Number(e.target.value) }))
                          }
                          style={{ fontSize: "var(--t-small)" }}
                        >
                          {CONFIDENCE_LEVELS.map((lvl) => (
                            <option key={lvl.label} value={lvl.value}>
                              {lvl.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      <span style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
                      <b style={{ color: "var(--ink)" }}>answer</b>
                      {t.permitted_answers.map((a) => (
                        <button
                          key={a}
                          type="button"
                          className="interactive"
                          disabled={busy === t.task_id}
                          data-control={`review.answer.${t.task_id}.${a}`}
                          onClick={() => answer(t.task_id, a)}
                          style={{
                            padding: "4px 10px",
                            border: "1px solid var(--line)",
                            borderRadius: "var(--radius-sm)",
                            background: "var(--bg-raised-2)",
                            color: "var(--ink)",
                            cursor: busy === t.task_id ? "wait" : "pointer",
                            fontSize: "var(--t-small)",
                          }}
                        >
                          {busy === t.task_id ? "…" : a.replace(/_/g, " ").toLowerCase()}
                        </button>
                      ))}
                      </span>
                    </div>
                  ) : (
                    <div>
                      <b style={{ color: "var(--ink)" }}>answers</b> {t.permitted_answers.join(" · ")}
                    </div>
                  )}
                  {target && t.state === "CONSENSUS_REACHED" ? (
                    mayValidate(reviewer) && !nameProblem ? (
                      <div data-control={`review.validate.${t.task_id}`} style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                        <b style={{ color: "var(--ink)" }}>adjudicate</b>
                        <span>confirm what {t.tally?.independent_human_reviewers ?? 2} people agreed on, as {reviewer?.id}:</span>
                        {t.permitted_answers.map((a) => (
                          <button
                            key={a}
                            type="button"
                            className="interactive"
                            disabled={busy === t.task_id}
                            data-control={`review.validate.${t.task_id}.${a}`}
                            onClick={() => validate(t.task_id, a)}
                            style={{ padding: "4px 10px", border: "1px solid var(--line)", borderRadius: "var(--radius-sm)", background: "var(--bg-raised-2)", color: "var(--ink)", fontSize: "var(--t-small)" }}
                          >
                            {a === t.tally?.consensus_top ? `confirm ${a.replace(/_/g, " ").toLowerCase()}` : `dispute: ${a.replace(/_/g, " ").toLowerCase()}`}
                          </button>
                        ))}
                      </div>
                    ) : (
                      <div style={{ color: "var(--ink-faint)" }} data-control={`review.validate-blocked.${t.task_id}`}>
                        expert validation needs a named operator or specialist who did not answer this task
                      </div>
                    )
                  ) : null}
                  {(t.validations ?? []).filter((v) => v.applied).map((v) => (
                    <div key={v.reviewer_id} style={{ color: "var(--ink-faint)" }}>
                      validated by {v.reviewer_id} ({v.reviewer_class.replace(/_/g, " ").toLowerCase()})
                    </div>
                  ))}
                  {err[t.task_id] ? (
                    <div style={{ color: "var(--status-refused)" }}>{err[t.task_id]}</div>
                  ) : null}
                  {t.binding.exposure_state && (
                    <div style={{ color: "var(--ink-faint)" }}>{t.binding.exposure_state}</div>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>

      <p style={{ margin: 0, fontSize: "var(--t-small)", color: "var(--ink-faint)" }}>
        {payload.state_note ??
          "Every task starts PROPOSED. No single answer — least of all an AI answer — can become a training label."}{" "}
        {target
          ? "Answering records one named person's answer through the governed write door; the ladder above still requires two independent people, then an attributed expert validation, before anything is training-eligible."
          : "Answering is a governed write; this view has no target to write against, so it stays read only."}
      </p>
    </div>
  );
}
