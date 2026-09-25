import { useState } from "react";
import { closeSession } from "../lib/governed";
import {
  CLASS_MEANS,
  HUMAN_CLASSES,
  IDENTITY_NOTICE,
  reviewerProblem,
  setReviewer,
  tidyName,
  useReviewer,
  type HumanClass,
  type Reviewer,
} from "../lib/interpretation";

export function ReviewerIdentityView({
  reviewer,
  onSave,
  onClear,
}: {
  reviewer: Reviewer | null;
  onSave: (r: Reviewer) => void;
  onClear: () => void;
}) {
  const [name, setName] = useState(reviewer?.id ?? "");
  const [cls, setCls] = useState<HumanClass>(reviewer?.cls ?? "COMMUNITY");
  const problem = reviewerProblem({ id: name, cls });
  return (
    <div className="ops-note" data-control="review.identity" style={{ display: "grid", gap: 6 }}>
      <div>
        <b>Reviewing as</b>{" "}
        {reviewer ? (
          <span data-control="review.identity.current">
            {reviewer.id} · {reviewer.cls.replace(/_/g, " ").toLowerCase()}
          </span>
        ) : (
          <span data-control="review.identity.unset">nobody yet — answers are attributed to a person</span>
        )}
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <input
          type="text"
          value={name}
          placeholder="your name"
          aria-label="your name"
          data-control="review.identity.name"
          onChange={(e) => setName(e.target.value)}
          style={{ font: "inherit", padding: "3px 6px" }}
        />
        <select
          value={cls}
          aria-label="your reviewer class"
          data-control="review.identity.class"
          onChange={(e) => setCls(e.target.value as HumanClass)}
          style={{ font: "inherit" }}
        >
          {HUMAN_CLASSES.map((c) => (
            <option key={c} value={c}>
              {c.replace(/_/g, " ").toLowerCase()} — {CLASS_MEANS[c]}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="ops-btn"
          disabled={Boolean(problem)}
          data-control="review.identity.save"
          onClick={() => onSave({ id: tidyName(name), cls })}
        >
          Use this identity
        </button>
        {reviewer ? (
          <button type="button" className="ops-btn" data-control="review.identity.clear" onClick={onClear}>
            Clear
          </button>
        ) : null}
      </div>
      {problem && name ? <div data-control="review.identity.problem">{problem}</div> : null}
      <div style={{ color: "var(--ink-faint)" }}>{IDENTITY_NOTICE}</div>
    </div>
  );
}

export function ReviewerIdentityForm() {
  const reviewer = useReviewer();
  return (
    <ReviewerIdentityView
      key={reviewer?.id ?? "none"}
      reviewer={reviewer}
      onSave={(r) => {
        if (reviewer && tidyName(reviewer.id).toLowerCase() !== r.id.toLowerCase()) closeSession();
        setReviewer(r);
      }}
      onClear={() => {
        closeSession();
        setReviewer(null);
      }}
    />
  );
}
