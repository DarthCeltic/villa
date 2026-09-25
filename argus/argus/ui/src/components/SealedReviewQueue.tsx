import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { OpsBadge, OpsDetails, OpsFact, OpsFacts, OpsSection, OpsTable, coordText } from "./OpsKit";
import { PlanApprove } from "./PlanApprove";
import { ReviewerIdentityForm } from "./ReviewerIdentity";
import { reviewerProblem, useReviewer } from "../lib/interpretation";
import { taskLink } from "../lib/taskBinding";
import {
  READING_GATE_STATEMENT,
  answerParams,
  mountWords,
  missingRequired,
  promotionWords,
  scienceApi,
  type ReviewChoice,
  type SealedLayer,
  type SealedQueue as Queue,
  type SealedResults,
  type SealedTask,
} from "../lib/science";

const QUEUES: { kind: string; label: string }[] = [
  { kind: "scroll_blocks", label: "Scroll blocks" },
];

const CONFIDENCE: { label: string; value: number }[] = [
  { label: "low", value: 0.3 },
  { label: "medium", value: 0.6 },
  { label: "high", value: 0.9 },
];

export function LayerTile({ layer }: { layer: SealedLayer }) {
  const mounted = layer.mount?.status === "MOUNTED_VERIFIED";
  return (
    <figure
      data-control={`review.blind.layer.${layer.name}`}
      style={{ margin: 0, display: "grid", gap: 3, justifyItems: "start", maxWidth: 240 }}
    >
      {mounted ? (
        <a href={layer.url} target="_blank" rel="noreferrer" title={`sha256 ${layer.sha256}`}>
          <img
            src={layer.url}
            alt={layer.label + " " + layer.name}
            loading="lazy"
            style={{ maxWidth: 240, maxHeight: 190, background: "var(--bg-sunken)" }}
          />
        </a>
      ) : (
        <div
          role="note"
          data-control={`review.blind.layer.${layer.name}.unmounted`}
          style={{ border: "1px solid var(--line-strong)", padding: 6, fontSize: "var(--t-small)" }}
        >
          <b>{layer.mount ? mountWords(layer.mount.status).toUpperCase() : "NOT MOUNTED"}</b>
          <div className="ops-mono" style={{ wordBreak: "break-all" }}>
            expected {layer.mount?.expected_path ?? "(path withheld)"}
            <br />
            sha256 {layer.sha256}
          </div>
        </div>
      )}
      <figcaption className="meta">
        {layer.name.replace(/\.png$/, "")}
        {layer.derived ? <> · <b>DERIVED</b></> : null}
      </figcaption>
    </figure>
  );
}

function FalsePositiveControls({ data }: { data: Record<string, unknown> }) {
  const scores = (data.scores as { name: string; value: number | null }[] | undefined) ?? [];
  const flags = (data.flags as string[] | undefined) ?? [];
  return (
    <div data-control="review.blind.controls" style={{ display: "grid", gap: 4 }}>
      {scores.length ? (
        <OpsTable control="review.blind.controls.scores">
          <tbody>
            {scores.map((s) => (
              <tr key={s.name}>
                <td>{s.name}</td>
                <td className="ops-mono">{s.value === null ? "n/a" : s.value}</td>
              </tr>
            ))}
            <tr>
              <td>raised flags</td>
              <td>{flags.length ? flags.join(", ") : "none"}</td>
            </tr>
          </tbody>
        </OpsTable>
      ) : null}
      <div className="meta">{String(data.note ?? "")}</div>
    </div>
  );
}

function TaskFacts({ task }: { task: SealedTask }) {
  const id = task.identity as Record<string, unknown>;
  return (
    <OpsFacts>
      <OpsFact label="Scroll and exact volume">
        {String(id.physical_scroll)} · <code>{String(id.exact_volume)}</code>
      </OpsFact>
      {id.geometry_state ? <OpsFact label="Geometry">{String(id.geometry_state)}{id.watermarked ? " · watermarked, diagnostic only" : ""}</OpsFact> : null}
      {id.orientation ? <OpsFact label="Orientation">{String(id.orientation)}</OpsFact> : null}
      <OpsFact label="Mesh">
        {String((task.mesh as Record<string, unknown>).identity ?? (task.mesh as Record<string, unknown>).segment)}
      </OpsFact>
      <OpsFact label="Scale">{task.scale.note}</OpsFact>
      <OpsFact label="Coordinates">
        <span className="ops-mono" title={JSON.stringify(task.coordinates)}>{coordText(task.coordinates)}</span>
      </OpsFact>
    </OpsFacts>
  );
}

function ResultsView({ r }: { r: SealedResults }) {
  if (r.withheld) return <div className="meta">{r.why}</div>;
  const p = r.promotion;
  return (
    <div data-control="review.blind.results" style={{ display: "grid", gap: 4 }}>
      <b>{p ? promotionWords(p.status) : "results"}</b>
      {p && p.reasons.length ? <div className="meta">why not: {p.reasons.join(", ")}</div> : null}
      {p?.disagreement ? <div className="meta">The reviewers disagree. That is shown, not averaged.</div> : null}
      {p && p.abstentions.length ? <div className="meta">Abstained: {p.abstentions.join(", ")}</div> : null}
      <ul style={{ margin: 0, paddingLeft: 18 }}>
        {(r.answers ?? []).map((a) => (
          <li key={a.reviewer_id}>
            {a.reviewer_id} ({a.reviewer_class.toLowerCase().replace(/_/g, " ")}): <b>{a.value}</b>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function SealedReviewQueue() {
  const [search] = useSearchParams();
  const reviewer = useReviewer();
  const [kind, setKind] = useState<string>(search.get("queue") ?? QUEUES[0]!.kind);
  const [queue, setQueue] = useState<Queue | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [taskId, setTaskId] = useState<string | null>(search.get("task"));
  const [task, setTask] = useState<SealedTask | null>(null);
  const [results, setResults] = useState<SealedResults | null>(null);
  const [choice, setChoice] = useState<ReviewChoice | null>(null);
  const [looked, setLooked] = useState(false);
  const [noImage, setNoImage] = useState(false);
  const [confidence, setConfidence] = useState(CONFIDENCE[1]!.value);
  const openedAt = useRef<number>(Date.now());
  const name = reviewer?.id ?? "";

  useEffect(() => {
    if (search.get("task")) document.querySelector('[data-control="review.blind"]')?.scrollIntoView?.();
  }, [search]);

  const loadQueue = useCallback(() => {
    void scienceApi.queue(kind, name || undefined).then((r) => {
      if (r.ok) {
        setQueue(r.data);
        setError(null);
      } else setError(r.message);
    });
  }, [kind, name]);
  useEffect(loadQueue, [loadQueue]);

  const loadTask = useCallback(() => {
    if (!taskId) {
      setTask(null);
      return;
    }
    void scienceApi.task(kind, taskId).then((r) => {
      if (r.ok) {
        setTask(r.data);
        openedAt.current = Date.now();
      } else setError(r.message);
    });
    if (name) void scienceApi.results(kind, taskId, name).then((r) => setResults(r.ok ? r.data : null));
    else setResults(null);
  }, [kind, taskId, name]);
  useEffect(loadTask, [loadTask]);

  const missing = useMemo(() => (task ? missingRequired(task) : []), [task]);
  const problem = reviewerProblem(reviewer);
  const params =
    task && choice && reviewer && !problem
      ? answerParams(
          kind,
          task.task_id,
          choice,
          reviewer,
          looked && !noImage,
          confidence,
          (Date.now() - openedAt.current) / 1000,
        )
      : null;
  const disabledReason = !task
    ? "open a task first"
    : problem
      ? problem
      : noImage
        ? null
        : missing.length
        ? `a review without the image is not a review: ${missing.length} required layer${missing.length === 1 ? " is" : "s are"} not mounted and hash-verified`
        : !looked
          ? "tick that you looked at the layers"
          : !choice
            ? "choose one of the four answers"
            : null;

  return (
    <OpsSection
      control="review.blind"
      title="Blinded review of sealed regions"
      hint="A queue cut from a sealed task file. Judge only what the layers show. Other reviewers' answers, and which windows are controls, are withheld by the server."
      aside={<OpsBadge tone="info">UNREVIEWED until two people agree</OpsBadge>}
    >
      <div className="ops-note" data-control="review.blind.gate">
        {READING_GATE_STATEMENT} A promoted task is a candidate — a place worth a closer look — not ink and not a
        reading.
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {QUEUES.map((q) => (
          <button
            key={q.kind}
            type="button"
            className="ops-subtab"
            aria-pressed={q.kind === kind}
            data-control={`review.blind.queue.${q.kind}`}
            onClick={() => {
              setKind(q.kind);
              setTaskId(null);
              setTask(null);
              setChoice(null);
              setLooked(false);
            }}
          >
            {q.label}
          </button>
        ))}
      </div>
      <ReviewerIdentityForm />
      {error ? (
        <div className="ops-note" data-control="review.blind.error">
          {error} A sealed review queue exists only when a project supplies its own sealed task file; none ships in this build, so there is nothing to review here.
          To use this screen, place a sealed task file in the service's sealed-task directory and reload the page.
        </div>
      ) : null}
      {queue?.state === "NOT_INSTALLED" ? (
        <div className="ops-note" data-control="review.blind.not-installed">
          <b>Sealed review data is not installed.</b> {queue.why} Add the separately governed review package to use this queue.
        </div>
      ) : null}
      {queue ? (
        <div className="meta" data-control="review.blind.counts">
          {queue.n_tasks} sealed tasks · {queue.answered_tasks} with at least one answer · {queue.promotable_tasks}{" "}
          promoted to a candidate{queue.root_sha256 ? <> · seal root <code>{queue.root_sha256.slice(0, 16)}…</code></> : null}
        </div>
      ) : null}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", maxHeight: 160, overflowY: "auto" }}>
        {(queue?.tasks ?? []).map((t) => (
          <button
            key={t.task_id}
            type="button"
            className="interactive"
            aria-pressed={t.task_id === taskId}
            data-control={`review.blind.task.${t.task_id}`}
            title={`${t.task_id} · ${t.answers} answer${t.answers === 1 ? "" : "s"} · ${promotionWords(t.status)}`}
            onClick={() => {
              setTaskId(t.task_id);
              setChoice(null);
              setLooked(false);
            }}
          >
            {t.group ? `${t.group.slice(-4)}·` : ""}
            {t.label}
            {t.you_answered ? " ✓" : ""}
          </button>
        ))}
      </div>
      {task ? (
        <div style={{ display: "grid", gap: 8 }} data-control="review.blind.detail">
          <h3 style={{ margin: 0 }}>{task.task_id}</h3>
          <div>
            <Link
              className="ag-btn"
              data-control="review.blind.open-in-workbench"
              to={taskLink(String((task.identity as Record<string, unknown>).physical_scroll), kind, task.task_id)}
              title="open this task's exact bound region in the Workbench's 3D CT viewer (raw CT and geometry only; no model output is shown)"
            >
              Open in Workbench (3D CT at the bound region)
            </Link>
          </div>
          <TaskFacts task={task} />
          {task.layers.length ? (
            <div style={{ display: "grid", gap: 4 }}>
              <b>Layers</b>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                {task.layers.map((l) => (
                  <LayerTile key={l.name} layer={l} />
                ))}
              </div>
            </div>
          ) : null}
          <OpsDetails control="review.blind.controls-open" summary="False-positive controls">
            <FalsePositiveControls data={task.false_positive_controls} />
          </OpsDetails>
          {results ? <ResultsView r={results} /> : null}
          <fieldset data-control="review.blind.answer" style={{ border: "1px solid var(--line)", padding: 8 }}>
            <legend>Your answer</legend>
            <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <input
                type="checkbox"
                checked={looked && !noImage}
                data-control="review.blind.looked"
                onChange={(e) => {
                  setLooked(e.target.checked);
                  if (e.target.checked) setNoImage(false);
                }}
              />
              I looked at the layers above.
            </label>
            <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <input
                type="checkbox"
                checked={noImage}
                data-control="review.blind.no-image-seen"
                onChange={(e) => {
                  setNoImage(e.target.checked);
                  if (e.target.checked) setLooked(false);
                }}
              />
              I did not see the image. This is recorded as such, and the answer is refused with its reason.
            </label>
            {missing.length ? (
              <div className="ops-note" data-control="review.blind.no-image">
                A review without the image is not a review. Not mounted or not hash-verified:{" "}
                {missing.map((m) => `${m.name} (${mountWords(m.mount?.status ?? "NOT_MOUNTED")})`).join(", ")}. This
                answer would be refused.
              </div>
            ) : null}
            <div style={{ display: "grid", gap: 4, marginTop: 6 }}>
              {task.choices.map((c) => (
                <label key={c.value} style={{ display: "flex", gap: 6, alignItems: "flex-start" }}>
                  <input
                    type="radio"
                    name="review-choice"
                    checked={choice === c.value}
                    data-control={`review.blind.choice.${c.value}`}
                    onChange={() => setChoice(c.value)}
                  />
                  <span>
                    <b>{c.value}</b> — {c.meaning}
                  </span>
                </label>
              ))}
            </div>
            <label style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 6 }}>
              <span className="ops-fact-label">your confidence</span>
              <select
                value={confidence}
                data-control="review.blind.confidence"
                onChange={(e) => setConfidence(Number(e.target.value))}
              >
                {CONFIDENCE.map((c) => (
                  <option key={c.label} value={c.value}>
                    {c.label}
                  </option>
                ))}
              </select>
            </label>
            {params ? (
              <PlanApprove
                key={`${task.task_id}|${choice}|${String(looked)}|${confidence}|${reviewer?.id ?? ""}`}
                action="review.blind.answer"
                params={params}
                label="record my answer"
                why="records one named person's answer; nothing is promoted by this alone"
                disabledReason={disabledReason}
                onDone={() => {
                  loadQueue();
                  loadTask();
                }}
              />
            ) : (
              <div className="meta" data-control="review.blind.answer.disabled">
                {disabledReason}
              </div>
            )}
          </fieldset>
          <div className="meta">{task.notice.evidence}</div>
        </div>
      ) : (
        <div className="meta">Open a task to see its layers.</div>
      )}
    </OpsSection>
  );
}
