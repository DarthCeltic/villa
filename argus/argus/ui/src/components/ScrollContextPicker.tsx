import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, Search } from "lucide-react";
import { scrollBlocker, searchMatches, type ScrollObject } from "./ShelfUniverse";
import { useSharedUniverse } from "../lib/sharedUniverse";
import { useArgusContext } from "../lib/context";
import { usePublicDemo } from "../lib/publicDemo";

const RESULT_CAP = 40;

function laneLabel(s: ScrollObject): string {
  if (s.grandPrize) return "Grand Prize";
  if (s.firstLetters) return "First Letters";
  if (s.lane === "PARIS4_TITLE") return "Title lane";
  return "Control / dev";
}

export interface ScrollContextPickerProps {
  buttonLabel?: string;
  buttonClassName?: string;
  className?: string;
  control?: string;
  onPicked?: (s: ScrollObject) => void;
}

export function ScrollContextPicker({
  buttonLabel = "Choose a scroll",
  buttonClassName,
  className,
  control = "scroll.picker",
  onPicked,
}: ScrollContextPickerProps) {
  const universe = useSharedUniverse();
  const { set } = useArgusContext();
  const demo = usePublicDemo();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const rootRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const toggleRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    const onDocPointer = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onDocKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setOpen(false);
      toggleRef.current?.focus();
    };
    document.addEventListener("mousedown", onDocPointer);
    document.addEventListener("keydown", onDocKey);
    return () => {
      document.removeEventListener("mousedown", onDocPointer);
      document.removeEventListener("keydown", onDocKey);
    };
  }, [open]);

  const results = useMemo(() => {
    const list = universe.scrolls.filter((s) => searchMatches(s, q));
    return list.slice(0, RESULT_CAP);
  }, [universe.scrolls, q]);

  const pick = (s: ScrollObject) => {
    set({ scroll: s.id });
    setOpen(false);
    setQ("");
    onPicked?.(s);
  };

  return (
    <div
      className={`scroll-picker${className ? ` ${className}` : ""}`}
      ref={rootRef}
      data-control={control}
    >
      <button
        type="button"
        ref={toggleRef}
        className={`scroll-picker-btn interactive ${buttonClassName || "scroll-picker-btn-default"}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        data-control={`${control}.toggle`}
        onClick={() => setOpen((v) => !v)}
      >
        <span>{buttonLabel}</span>
        <ChevronDown size={14} aria-hidden="true" />
      </button>
      {open ? (
        <div className="scroll-picker-panel" role="listbox" aria-label="Choose a scroll">
          <div className="scroll-picker-search">
            <Search size={14} aria-hidden="true" />
            <input
              ref={inputRef}
              type="search"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Find a scroll by id, name or alias"
              aria-label="Find a scroll"
              data-control={`${control}.search`}
            />
          </div>
          <ul className="scroll-picker-list">
            {universe.loading && !universe.scrolls.length ? (
              <li className="scroll-picker-empty">Reading the registered scrolls…</li>
            ) : results.length === 0 ? (
              <li className="scroll-picker-empty">
                {q ? `No registered scroll matches "${q}".` : "No scroll is registered."}
              </li>
            ) : (
              results.map((s) => {
                const blocker = scrollBlocker(s, demo);
                return (
                  <li key={s.id}>
                    <button
                      type="button"
                      role="option"
                      className="scroll-picker-item interactive"
                      data-control={`${control}.pick.${s.id}`}
                      onClick={() => pick(s)}
                      title={`${blocker.full} Next: ${s.next.label}.`}
                    >
                      <span className="scroll-picker-item-name">{s.display}</span>
                      <span className="scroll-picker-item-lane" data-lane={s.lane}>
                        {laneLabel(s)}
                      </span>
                      <span className="scroll-picker-item-blocker">{blocker.text}</span>
                    </button>
                  </li>
                );
              })
            )}
          </ul>
          {universe.failures.length ? (
            <p className="scroll-picker-note">
              {universe.failures.length} route{universe.failures.length === 1 ? "" : "s"} behind
              this list did not answer; what is shown came from the routes that did.
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export default ScrollContextPicker;
