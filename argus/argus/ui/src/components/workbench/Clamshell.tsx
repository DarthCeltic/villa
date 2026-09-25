import { useCallback, useEffect, useId, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ChevronRight } from "lucide-react";
import { LAYOUT_EVENT, readState, writeState, type LayoutAction, type WbClass } from "../../lib/wbLayout";
import { useWbClass } from "./WbLayout";

export function usePersistedChoice<T extends string>(
  key: string,
  allowed: readonly T[],
  fallback: T,
  persistIn?: readonly WbClass[],
): [T, (next: T | ((cur: T) => T)) => void] {
  const cls: WbClass = useWbClass();
  const remembered = !persistIn || persistIn.includes(cls);
  const read = useCallback((): T => {
    const got = typeof window === "undefined" || !remembered ? null : readState(cls, key);
    return got !== null && (allowed as readonly string[]).includes(got) ? (got as T) : fallback;
  }, [cls, key, remembered]);
  const [value, setValue] = useState<T>(read);
  useEffect(() => {
    setValue(read());
  }, [read]);
  useEffect(() => {
    const onLayout = (e: Event) => {
      const a = (e as CustomEvent<LayoutAction>).detail;
      if (!a) return;
      if (a.kind === "reset") setValue(fallback);
      else if (a.kind === "collapse" && a.cls === cls && key.startsWith("clam.") && (allowed as readonly string[]).includes("closed")) setValue("closed" as T);
    };
    window.addEventListener(LAYOUT_EVENT, onLayout);
    return () => window.removeEventListener(LAYOUT_EVENT, onLayout);
  }, [cls, key]);
  const set = useCallback(
    (next: T | ((cur: T) => T)) => {
      setValue((cur) => {
        const v = typeof next === "function" ? (next as (c: T) => T)(cur) : next;
        if (remembered) writeState(cls, key, v);
        return v;
      });
    },
    [cls, key, remembered],
  );
  return [value, set];
}

const OPEN_CLOSED = ["open", "closed"] as const;

export function Clamshell({
  id,
  title,
  aside,
  children,
  defaultOpen = false,
  className = "",
  persistIn,
}: {
  id: string;
  title: string;
  aside?: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  className?: string;
  persistIn?: readonly WbClass[];
}) {
  const [state, setState] = usePersistedChoice(
    `clam.${id}`,
    OPEN_CLOSED,
    defaultOpen ? "open" : "closed",
    persistIn,
  );
  const open = state === "open";
  const bodyId = useId();
  return (
    <section id={`wb-clam-${id}`} className={`wb-clam ${className}`} data-clamshell={id} data-open={open ? "true" : "false"}>
      <div className="wb-clam-head">
        <button
          type="button"
          className="wb-clam-toggle"
          aria-expanded={open}
          aria-controls={bodyId}
          data-control={`wb.clam.${id}`}
          onClick={() => setState(open ? "closed" : "open")}
        >
          <ChevronRight size={16} aria-hidden className="wb-clam-chev" />
          <span>{title}</span>
        </button>
        {aside ? <div className="wb-clam-aside">{aside}</div> : null}
      </div>
      {open ? (
        <div id={bodyId} className="wb-clam-body">
          {children}
        </div>
      ) : null}
    </section>
  );
}

export function NextStep({
  id,
  label,
  to,
  href,
  command,
}: {
  id: string;
  label: string;
  to?: string;
  href?: string;
  command?: string;
}) {
  const [copied, setCopied] = useState<"idle" | "copied" | "failed">("idle");
  const [detailsOpen, setDetailsOpen] = useState(false);
  const copy = () => {
    if (!command) return;
    try {
      const p = navigator.clipboard?.writeText(command);
      if (!p) {
        setCopied("failed");
        return;
      }
      p.then(() => setCopied("copied")).catch(() => setCopied("failed"));
    } catch {
      setCopied("failed");
    }
  };
  return (
    <span className="wb-next" data-next-step={id}>
      <span className="wb-next-lead">Next step:</span>{" "}
      {to ? (
        <Link to={to} className="wb-next-link" data-control={`wb.next.${id}`}>
          {label}
        </Link>
      ) : href ? (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="wb-next-link"
          data-control={`wb.next.${id}`}
        >
          {label}
        </a>
      ) : command ? (
        <span className="wb-next-copy">
          <span className="wb-next-copy-label">{label}</span>
          <button
            type="button"
            className="wb-next-details-toggle"
            aria-expanded={detailsOpen}
            data-control={`wb.next.${id}.toggle`}
            onClick={() => setDetailsOpen((o) => !o)}
          >
            Developer details
          </button>
          {detailsOpen ? (
            <span className="wb-next-details-body">
              <code className="wb-next-code">{command}</code>
              <button
                type="button"
                className="wb-next-copybtn"
                data-control={`wb.next.${id}.copy`}
                onClick={copy}
              >
                {copied === "copied" ? "Copied" : "Copy"}
              </button>
              {copied === "failed" ? (
                <span className="wb-next-copy-note" role="status">
                  The clipboard is not available here; select the text above instead.
                </span>
              ) : null}
            </span>
          ) : null}
        </span>
      ) : null}
    </span>
  );
}
