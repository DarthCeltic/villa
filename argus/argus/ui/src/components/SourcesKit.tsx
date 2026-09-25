import { useEffect, useState, type ReactNode } from "react";

export function usePage(total: number, size: number, resetKey: string) {
  const [start, setStart] = useState(0);
  useEffect(() => {
    setStart(0);
  }, [resetKey]);
  const last = total === 0 ? 0 : Math.floor((total - 1) / size) * size;
  const s = Math.min(start, last);
  return {
    start: s,
    end: Math.min(total, s + size),
    size,
    total,
    prev: () => setStart(Math.max(0, s - size)),
    next: () => setStart(Math.min(last, s + size)),
  };
}

export function SrcPager({
  page,
  noun,
  control,
}: {
  page: ReturnType<typeof usePage>;
  noun: string;
  control: string;
}) {
  const { start, end, total } = page;
  const fits = total <= page.size;
  return (
    <div className="src-pager" data-control={control}>
      <span className="src-pager-count" aria-live="polite">
        {total === 0 ? `no ${noun} to show` : `showing ${start + 1}–${end} of ${total} ${noun}`}
      </span>
      {fits ? null : (
        <span className="src-pager-moves">
          <button
            type="button"
            className="ops-btn"
            onClick={page.prev}
            disabled={start === 0}
            data-control={`${control}.prev`}
          >
            Previous {page.size}
          </button>
          <button
            type="button"
            className="ops-btn"
            onClick={page.next}
            disabled={end >= total}
            data-control={`${control}.next`}
          >
            Next {page.size}
          </button>
        </span>
      )}
    </div>
  );
}

export function SrcGroup({
  open,
  onToggle,
  summary,
  children,
  control,
  selected = false,
}: {
  open: boolean;
  onToggle: (open: boolean) => void;
  summary: ReactNode;
  children: ReactNode;
  control: string;
  selected?: boolean;
}) {
  return (
    <details
      className="src-group"
      open={open}
      data-control={control}
      data-selected={selected ? "true" : undefined}
      onToggle={(e) => {
        const now = (e.currentTarget as HTMLDetailsElement).open;
        if (now !== open) onToggle(now);
      }}
    >
      <summary className="src-group-summary" data-control={`${control}.toggle`}>
        {summary}
      </summary>
      {open ? <div className="src-group-body">{children}</div> : null}
    </details>
  );
}
