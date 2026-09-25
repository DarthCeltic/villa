"""Governed pairwise ranking: simple to answer, severe to promote."""
from __future__ import annotations

import dataclasses
import hashlib
import math
import random
from typing import Iterable

PAIRWISE_ID = "argus-pairwise-v1"

CHOICES = ("A_BETTER", "B_BETTER", "EQUIVALENT", "NEITHER", "UNSURE", "CORRECTION_DRAWN")

REASON_CODES = (
  "SHEET_SWITCH",
  "WRONG_DEPTH",
  "BROKEN_CONTINUITY",
  "MERGED_SHEETS",
  "MISSING_SHEET",
  "ARTIFACT_OR_NOISE",
  "CANNOT_DETERMINE",
)

INCONCLUSIVE_REASONS = frozenset({"CANNOT_DETERMINE"})

ORDERING_CHOICES = frozenset({"A_BETTER", "B_BETTER"})
TIE_CHOICES = frozenset({"EQUIVALENT"})
ABSTENTIONS = frozenset({"UNSURE", "NEITHER", "CORRECTION_DRAWN"})

CLAIM = ("people consistently prefer this candidate over the others it was compared against")
NOT_A_CLAIM = ("this candidate is physically correct")


class PairwiseError(ValueError):
    """Raised when an answer would carry more weight than it earned."""


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


@dataclasses.dataclass(frozen=True)
class Candidate:
    """One thing being compared."""

    candidate_id: str
    source_hash: str
    produced_by: str
    asset_path: str

    def __post_init__(self):
        if len(self.source_hash) != 64:
            raise PairwiseError("candidate %s needs a full source hash so the comparison "
                                "stays auditable" % self.candidate_id)


@dataclasses.dataclass(frozen=True)
class Presentation:
    """One pair, as shown."""

    pair_id: str
    left: str
    right: str
    seed: int
    is_control: bool = False
    expected: str | None = None

    def visible(self) -> dict:
        """Exactly what the interface may render."""
        return {
          "pair_id": self.pair_id,
          "left_asset": self.left, "right_asset": self.right,
          "question": "Which one follows a single sheet of papyrus?",
          "choices": ["A_BETTER", "B_BETTER", "EQUIVALENT", "NEITHER", "UNSURE"],
          "reason_codes": list(REASON_CODES),
          "reason_required_for": sorted(ORDERING_CHOICES),
          "model_confidence": "withheld",
          "votes_so_far": "withheld",
          "running_winner": "withheld",
          "candidate_source": "withheld",
          "is_control": "not disclosed",
        }


@dataclasses.dataclass(frozen=True)
class Comparison:
    """One answered presentation, in TRUE candidate terms rather than left/right."""

    pair_id: str
    a: str
    b: str
    choice: str
    reviewer_id: str
    reviewer_class: str
    duration_s: float
    utc: str
    presented_left: str = ""
    reason: str = ""

    def __post_init__(self):
        if self.choice not in CHOICES:
            raise PairwiseError("unknown choice %r" % (self.choice,))
        if self.a == self.b:
            raise PairwiseError("a candidate cannot be compared with itself")
        if self.reason and self.reason not in REASON_CODES:
            raise PairwiseError(
              "unknown reason code %r. The list is closed (%s) because an open text box "
              "produces a hundred spellings of one reason and no way to count them."
              % (self.reason, ", ".join(REASON_CODES)))
        if self.choice in ORDERING_CHOICES and not self.reason:
            raise PairwiseError(
              "a preference needs a reason code. 'B is better' and 'B is better BECAUSE A "
              "switches sheets' cost the reviewer the same half-second and differ entirely in "
              "what anyone can do next.")
        if self.choice in ORDERING_CHOICES and self.reason in INCONCLUSIVE_REASONS:
            raise PairwiseError(
              "CANNOT_DETERMINE contradicts a preference. If the volume does not settle it, "
              "the answer is UNSURE -- recording it as a preference makes an abstention look "
              "like evidence.")


def present(pair_id: str, a: str, b: str, *, seed: int, is_control: bool = False,
            expected: str | None = None) -> Presentation:
    """Randomise which candidate appears on the left, deterministically from a seed."""
    rng = random.Random(seed)
    left, right = (a, b) if rng.random() < 0.5 else (b, a)
    return Presentation(pair_id=pair_id, left=left, right=right, seed=seed,
                        is_control=is_control, expected=expected)


def resolve(presentation: Presentation, side_choice: str, **kw) -> Comparison:
    """Turn a left/right answer into a candidate-terms answer."""
    if side_choice not in CHOICES:
        raise PairwiseError("unknown choice %r" % (side_choice,))
    a, b = presentation.left, presentation.right
    choice = side_choice
    return Comparison(pair_id=presentation.pair_id, a=a, b=b, choice=choice,
                      presented_left=presentation.left, **kw)



def fit_bradley_terry(comparisons: Iterable[Comparison], *, iters: int = 200,
                      tol: float = 1e-9) -> dict:
    """Maximum-likelihood strengths over all comparisons at once."""
    comps = [c for c in comparisons if c.choice in (ORDERING_CHOICES | TIE_CHOICES)]
    ids = sorted({c.a for c in comps} | {c.b for c in comps})
    if not ids:
        return {"strengths": {}, "n_comparisons": 0, "converged": True,
                "claim": CLAIM, "not_a_claim": NOT_A_CLAIM,
                "note": "no ordering information: every answer was a tie or an abstention"}
    wins = {i: 0.0 for i in ids}
    games = {i: {} for i in ids}
    for c in comps:
        if c.choice == "A_BETTER":
            wins[c.a] += 1.0
        elif c.choice == "B_BETTER":
            wins[c.b] += 1.0
        else:
            wins[c.a] += 0.5
            wins[c.b] += 0.5
        games[c.a][c.b] = games[c.a].get(c.b, 0) + 1
        games[c.b][c.a] = games[c.b].get(c.a, 0) + 1

    p = {i: 1.0 for i in ids}
    converged = False
    for _ in range(iters):
        newp = {}
        for i in ids:
            denom = 0.0
            for j, n in games[i].items():
                denom += n / (p[i] + p[j])
            newp[i] = (wins[i] / denom) if denom > 0 else p[i]
        g = math.exp(sum(math.log(max(v, 1e-12)) for v in newp.values()) / len(newp))
        newp = {i: v / g for i, v in newp.items()}
        delta = max(abs(newp[i] - p[i]) for i in ids)
        p = newp
        if delta < tol:
            converged = True
            break
    return {"strengths": p, "n_comparisons": len(comps), "converged": converged,
            "ties_counted_as": "half a win to each side",
            "abstentions_excluded": True,
            "order_independent": True,
            "claim": CLAIM, "not_a_claim": NOT_A_CLAIM}


def win_probability(strengths: dict, a: str, b: str) -> float:
    pa, pb = strengths.get(a, 1.0), strengths.get(b, 1.0)
    return pa / (pa + pb)


def most_informative_pairs(strengths: dict, candidates: Iterable[str], *, k: int = 5) -> list:
    """Pairs whose outcome is least predictable, which is where an answer buys the most."""
    cs = sorted(candidates)
    scored = []
    for i, a in enumerate(cs):
        for b in cs[i + 1:]:
            p = win_probability(strengths, a, b)
            scored.append(({"a": a, "b": b, "win_probability_a": round(p, 4),
                            "information": round(1 - abs(p - 0.5) * 2, 4)}))
    scored.sort(key=lambda d: -d["information"])
    return scored[:k]



def reviewer_reliability(comparisons: Iterable[Comparison],
                         presentations: dict, reviewer_id: str) -> dict:
    """Score a reviewer against planted controls with a known answer -- never the majority."""
    seen = scored = correct = 0
    for c in comparisons:
        if c.reviewer_id != reviewer_id:
            continue
        seen += 1
        p = presentations.get(c.pair_id)
        if p is None or not p.is_control or not p.expected:
            continue
        if c.choice in ABSTENTIONS:
            continue
        scored += 1
        picked = c.a if c.choice == "A_BETTER" else (c.b if c.choice == "B_BETTER" else None)
        if picked == p.expected:
            correct += 1
    return {"reviewer_id": reviewer_id, "comparisons_answered": seen,
            "control_comparisons_scored": scored,
            "control_accuracy": (correct / scored) if scored else None,
            "measured_against": "planted controls with a known answer, never the majority"}


def consistency(comparisons: Iterable[Comparison], reviewer_id: str) -> dict:
    """Does a reviewer answer the same repeated pair the same way?"""
    by_pair = {}
    for c in comparisons:
        if c.reviewer_id != reviewer_id or c.choice in ABSTENTIONS:
            continue
        by_pair.setdefault(frozenset((c.a, c.b)), []).append(c.choice)
    repeats = {k: v for k, v in by_pair.items() if len(v) > 1}
    if not repeats:
        return {"reviewer_id": reviewer_id, "repeated_pairs": 0, "self_agreement": None}
    agree = sum(1 for v in repeats.values() if len(set(v)) == 1)
    return {"reviewer_id": reviewer_id, "repeated_pairs": len(repeats),
            "self_agreement": agree / len(repeats)}


MIN_CONTROL_ACCURACY = 0.75
MIN_SELF_AGREEMENT = 0.70
MIN_CONTROLS_SCORED = 4
MIN_REPEATED_PAIRS = 3


def reliability_gate(comparisons, presentations) -> dict:
    """May a ranking be fitted from these answers at all?"""
    comps = list(comparisons)
    reviewers = sorted({c.reviewer_id for c in comps})
    passed, failed, unmeasurable = [], [], []
    for r in reviewers:
        rel = reviewer_reliability(comps, presentations, r)
        con = consistency(comps, r)
        acc, agree = rel["control_accuracy"], con["self_agreement"]
        if (rel["control_comparisons_scored"] < MIN_CONTROLS_SCORED
                or con["repeated_pairs"] < MIN_REPEATED_PAIRS):
            unmeasurable.append({
              "reviewer_id": r, "controls_scored": rel["control_comparisons_scored"],
              "repeated_pairs": con["repeated_pairs"],
              "why": "not enough planted controls or repeated pairs to measure this reviewer. "
                     "UNMEASURED is not PASSED -- excluded from the fit until measurable."})
            continue
        if (acc is not None and acc >= MIN_CONTROL_ACCURACY
                and agree is not None and agree >= MIN_SELF_AGREEMENT):
            passed.append({"reviewer_id": r, "control_accuracy": acc, "self_agreement": agree})
        else:
            failed.append({"reviewer_id": r, "control_accuracy": acc, "self_agreement": agree,
                           "why": "below the frozen thresholds (controls >= %.2f, "
                                  "self-agreement >= %.2f)"
                                  % (MIN_CONTROL_ACCURACY, MIN_SELF_AGREEMENT)})
    return {
      "passes": bool(passed), "eligible_reviewers": [x["reviewer_id"] for x in passed],
      "passed": passed, "failed": failed, "unmeasurable": unmeasurable,
      "thresholds": {"control_accuracy": MIN_CONTROL_ACCURACY,
                     "self_agreement": MIN_SELF_AGREEMENT,
                     "min_controls_scored": MIN_CONTROLS_SCORED,
                     "min_repeated_pairs": MIN_REPEATED_PAIRS},
      "frozen_before": "any ranking was fitted",
    }


def fit_bradley_terry_gated(comparisons, presentations, **kw) -> dict:
    """Bradley-Terry, but only over reviewers whose reliability has been MEASURED and passed."""
    gate = reliability_gate(comparisons, presentations)
    if not gate["passes"]:
        raise PairwiseError(
          "no reviewer has passed reliability, so no ranking may be fitted. Failed: %s. "
          "Unmeasurable: %s. A ranking fitted from unmeasured reviewers is a measurement of "
          "who answered, not of which surface is better."
          % ([f["reviewer_id"] for f in gate["failed"]],
             [u["reviewer_id"] for u in gate["unmeasurable"]]))
    eligible = set(gate["eligible_reviewers"])
    kept = [c for c in comparisons if c.reviewer_id in eligible]
    out = fit_bradley_terry(kept, **kw)
    out["reliability_gate"] = gate
    out["comparisons_excluded"] = len(list(comparisons)) - len(kept)
    out["used_for"] = ("PRIORITISING which candidates and which uncertain cases get attention. "
                       "NOT for deciding which surface is physically correct.")
    return out


PREFERENCE_CEILING = (
  "A preference is never a label. A ranking fitted from blinded comparisons may reach "
  "PAIRWISE_PREFERRED and no further; it PRIORITISES which candidates and which uncertain "
  "cases get attention. Only FROZEN_SUPERVISION enters a training contract, and the distance "
  "between those states is human correction followed by independent validation."
)

AI_MAY = (
  "perform a cheap first-pass triage over pairs",
  "identify obvious failures",
  "route ambiguous cases to humans",
  "supply NON-AUTHORITATIVE reason codes",
)
AI_MAY_NOT = (
  "act as the final expert validator",
  "turn agreement among AI reviewers into ground truth",
  "promote a correction into training data without governed human validation",
  "evaluate itself, or any model descended from its own choices",
)


def assert_ai_reviewer_is_independent(reviewer_id: str, candidate: Candidate,
                                      lineage: dict) -> None:
    """Refuse an AI reviewer judging its own work, or work by a model trained on its choices."""
    if reviewer_id not in lineage:
        raise PairwiseError(
          "no lineage declared for AI reviewer %r. UNDECLARED is a refusal: after one retrain "
          "cycle a descendant relationship is not visible in a candidate's name, so it has to "
          "be recorded rather than inferred." % reviewer_id)
    ancestry = set(lineage.get(reviewer_id) or ())
    if candidate.produced_by == reviewer_id:
        raise PairwiseError(
          "AI reviewer %r would be judging a candidate it produced itself." % reviewer_id)
    if candidate.produced_by in ancestry:
        raise PairwiseError(
          "AI reviewer %r is descended from %r, which produced this candidate. Its judgement "
          "is that model's opinion arriving a second time, and counting it would look like "
          "corroboration." % (reviewer_id, candidate.produced_by))


def promotion_blockers(comparisons: Iterable[Comparison]) -> list:
    """Why this comparison set may not yet become supervision."""
    comps = list(comparisons)
    reviewers = {c.reviewer_id for c in comps}
    human = {c.reviewer_id for c in comps if c.reviewer_class != "AI_AGENT"}
    out = []
    if len(reviewers) < 2:
        out.append("only one reviewer has answered; independent review needs at least two")
    if not human:
        out.append("every answer came from an AI reviewer; an all-AI consensus may inform but "
                   "may never become a training label")
    if not any(c.choice in ORDERING_CHOICES for c in comps):
        out.append("no answer expressed a preference, so there is nothing to promote")
    if any(c.choice in ORDERING_CHOICES and not c.reason for c in comps):
        out.append("a preference was recorded without a reason code, so nobody can audit or "
                   "learn from why it was preferred")
    return out
