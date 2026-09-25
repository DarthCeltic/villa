import { useEffect, useId, useRef, useState } from "react";
import { useProductState, type StateCell } from "../lib/productState";
import "../theme/product.css";

function Cell({ c, control }: { c: StateCell; control: string }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <li className="pb-cell" data-tone={c.tone} data-unknown={c.unknown ? "true" : "false"} data-control={control}>
      <button
        type="button"
        className="pb-cell-btn interactive"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((o) => !o)}
        title={`${c.label}: ${c.word}. ${c.detail}`}
      >
        <span className="pb-label">{c.label}</span>
        <span className="pb-word">{c.word}</span>
      </button>
      {open ? (
        <div className="pb-pop" id={id} role="note">
          <p>{c.detail}</p>
          <p className="pb-src">
            {c.source}
            {c.asOf ? ` · as of ${c.asOf}` : ""}
          </p>
        </div>
      ) : null}
    </li>
  );
}

function DetailsPopover({ summary, children }: { summary: string; children: React.ReactNode }) {
  const ref = useRef<HTMLDetailsElement>(null);
  const summaryRef = useRef<HTMLElement>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return undefined;
    const inside = (n: EventTarget | null) => !!ref.current && n instanceof Node && ref.current.contains(n);
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      const hadFocus = inside(document.activeElement);
      setOpen(false);
      if (hadFocus) summaryRef.current?.focus();
    };
    const onPointer = (e: PointerEvent) => {
      if (!inside(e.target)) setOpen(false);
    };
    const onFocusIn = (e: FocusEvent) => {
      const t = e.target;
      if (!inside(t) && !(t instanceof Node && ref.current?.contains(t) === false && t.contains(ref.current))) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer, true);
    document.addEventListener("focusin", onFocusIn);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer, true);
      document.removeEventListener("focusin", onFocusIn);
    };
  }, [open]);
  return (
    <details ref={ref} className="pb-more" open={open} onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
      <summary ref={summaryRef} className="pb-more-summary interactive">
        {summary}
      </summary>
      {children}
    </details>
  );
}

export function ProductBar() {
  const st = useProductState();
  const initialReadsSettled = !st.surfaces.loading;
  const operational = st.surfaces.data && !st.surfaces.stale
    ? { word: "observed", tone: "ok" as const, detail: "The service has returned a current operational snapshot." }
    : { word: "unknown", tone: "unknown" as const, detail: "The operational snapshot is missing or stale. Unknown is not idle." };
  const scientificBlocked = st.ceiling.detector.tone === "bad" || st.ceiling.transfer.tone === "bad";
  const scientificUnknown = st.ceiling.detector.unknown || st.ceiling.transfer.unknown;
  const scientific = {
    word: scientificUnknown ? "unknown" : scientificBlocked ? "bounded" : "qualified",
    tone: scientificUnknown ? "unknown" as const : scientificBlocked ? "warn" as const : "ok" as const,
    detail: scientificUnknown
      ? "The detector or transfer receipt is missing or stale. No scientific readiness is inferred."
      : scientificBlocked
      ? `${st.ceiling.detector.label}: ${st.ceiling.detector.word}. ${st.ceiling.transfer.label}: ${st.ceiling.transfer.word}.`
      : "The current detector and transfer gates report a qualified route.",
  };
  return (
    <section
      className="pb pb-compact"
      data-control="state.bar"
      data-settled={initialReadsSettled ? "true" : "false"}
      role="region"
      aria-label="Current system status"
    >
      <div className="pb-summary">
        <div className="pb-summary-item" data-control="state.operational" data-tone={operational.tone}>
          <span className="pb-label">Operational</span><strong>{operational.word}</strong>
        </div>
        <div className="pb-summary-item" data-control="state.scientific" data-tone={scientific.tone}>
          <span className="pb-label">Scientific ceiling</span><strong>{scientific.word}</strong>
        </div>
        <span className="pb-phone-label" aria-hidden="true">System status</span>
        <DetailsPopover summary="Details">
          <div className="pb-more-body" tabIndex={-1}>
            <p className="pb-more-lede">{operational.detail} {scientific.detail}</p>
            <div className="pb-detail-groups">
              <div><span className="pb-group-name">Prizes</span><ul className="pb-group" data-control="state.prizes">
                <Cell c={st.prizes.progress} control="state.prizes.progress" />
                <Cell c={st.prizes.firstLetters} control="state.prizes.firstLetters" />
                <Cell c={st.prizes.grandPrize} control="state.prizes.grandPrize" />
              </ul></div>
              <div><span className="pb-group-name">Ceiling</span><ul className="pb-group" data-control="state.ceiling">
                <Cell c={st.ceiling.detector} control="state.ceiling.detector" />
                <Cell c={st.ceiling.transfer} control="state.ceiling.transfer" />
              </ul></div>
            </div>
          </div>
        </DetailsPopover>
      </div>
    </section>
  );
}
