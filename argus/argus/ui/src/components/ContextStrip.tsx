import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { BookOpen } from "lucide-react";

import type { FeedState, RunRecord } from "../api";
import { CONTEXT_KEYS, MODES, useArgusContext } from "../lib/context";
import { scrollOf } from "../lib/targets";
import { useSharedUniverse } from "../lib/sharedUniverse";
import { usePublicDemo } from "../lib/publicDemo";
import { blockerLine, noStatusLine, positionLine, type ScrollStatus } from "../lib/scrollStatus";
import { ScrollContextPicker } from "./ScrollContextPicker";
import { useScrollStatus } from "./ScrollStatusBar";
import "../theme/status.css";

const DIARY_HANDLE = '[data-control="diary.toggle"]';

function useDiaryCollapsed(): boolean | null {
  const [collapsed, setCollapsed] = useState<boolean | null>(null);
  useEffect(() => {
    const read = () => {
      const raw = document.documentElement.dataset.diaryCollapsed;
      setCollapsed(raw === undefined ? null : raw !== "false");
    };
    read();
    const mo = new MutationObserver(read);
    mo.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-diary-collapsed"],
    });
    return () => mo.disconnect();
  }, []);
  return collapsed;
}

function currentRun(runs: RunRecord[], scroll: string | null, runId: string | null) {
  if (runId) {
    const exact = runs.find((r) => r.run_id === runId);
    if (exact) return exact;
  }
  if (!scroll) return null;
  const mine = runs.filter((r) => scrollOf(r) === scroll);
  if (!mine.length) return null;
  return mine.reduce((a, b) => (a.run_id.localeCompare(b.run_id) >= 0 ? a : b));
}

type Signal = { tone: "attention" | "refused"; glyph: string; label: string; to: string };

export function attentionOf(runs: RunRecord[]): Signal | null {
  const refused = runs.filter((r) => r.operational_state === "REFUSED").length;
  if (refused) {
    return {
      tone: "refused",
      glyph: "✕",
      label: refused === 1 ? "1 refused run" : `${refused} refused runs`,
      to: "/jobs",
    };
  }
  const stalled = runs.filter(
    (r) => r.operational_state === "STALLED" || r.operational_state === "UNKNOWN",
  ).length;
  if (stalled) {
    return {
      tone: "attention",
      glyph: "!",
      label: stalled === 1 ? "1 needs a decision" : `${stalled} need a decision`,
      to: "/review",
    };
  }
  return null;
}

export function stripAnswers(
  scroll: string | null,
  status: ScrollStatus | null,
  loading: boolean,
  publicDemo: boolean,
  shelfHref: string,
) {
  if (!scroll) {
    return {
      stage: { text: "none — nothing selected", known: true },
      blocker: { text: "no scroll is selected", full: "Choose a scroll: every blocker is a blocker for a particular scroll.", source: "the address" },
      next: null,
    };
  }
  if (!status) {
    const why = noStatusLine(scroll, loading);
    return {
      stage: { text: loading ? "reading…" : "status not read", known: false },
      blocker: { text: loading ? "reading…" : "unknown: status not read", full: why, source: "/api/scroll_status" },
      next: { label: "Back to the shelf", to: shelfHref, why },
    };
  }
  const b = blockerLine(status, publicDemo);
  const decisions = status.progress_summary?.decisions ?? 0;
  const need = !status.blocker && decisions > 0
    ? {
        text: decisions === 1 ? "1 human decision" : `${decisions} human decisions`,
        full: `${decisions} required route decision${decisions === 1 ? " is" : "s are"} waiting for a person. Open the work queue for the exact controls.`,
        source: "/api/scroll_status progress_summary",
      }
    : { text: b.text, full: b.full, source: "/api/scroll_status" };
  return {
    stage: { text: positionLine(status), known: true },
    blocker: need,
    next: { label: status.next_action.label, to: status.next_action.to, why: status.next_action.why },
  };
}

function useStickyOffset(ref: React.RefObject<HTMLElement>) {
  useLayoutEffect(() => {
    const el = ref.current;
    const main = el?.closest<HTMLElement>(".argus-main");
    if (!el || !main) return undefined;
    const set = () => {
      const sticky = getComputedStyle(el).position === "sticky" && getComputedStyle(el).display !== "none";
      main.style.setProperty("--ctx-h", sticky ? `${Math.ceil(el.getBoundingClientRect().height)}px` : "0px");
    };
    set();
    const ro = new ResizeObserver(set);
    ro.observe(el);
    window.addEventListener("resize", set);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", set);
      main.style.removeProperty("--ctx-h");
    };
  }, [ref]);
}

export function ContextStrip({ feed }: { feed: FeedState }) {
  const { ctx, mode, setMode } = useArgusContext();
  const stripRef = useRef<HTMLDivElement>(null);
  useStickyOffset(stripRef);
  const [params] = useSearchParams();
  const loc = useLocation();
  const collapsed = useDiaryCollapsed();
  const universe = useSharedUniverse();
  const demo = usePublicDemo(loc.search);

  const runs = useMemo(() => feed.data?.runs ?? [], [feed.data]);
  const run = useMemo(() => currentRun(runs, ctx.scroll, ctx.run), [runs, ctx.scroll, ctx.run]);
  const s = ctx.scroll ? (universe.byId.get(ctx.scroll) ?? null) : null;
  const { status, loading } = useScrollStatus(ctx.scroll);

  const shelfHref = useMemo(() => {
    const next = new URLSearchParams(params);
    for (const k of CONTEXT_KEYS) next.delete(k);
    const q = next.toString();
    return q ? `/?${q}` : "/";
  }, [params]);

  const [expanded, setExpanded] = useState(false);
  const toggleDiary = useCallback(() => {
    document.querySelector<HTMLElement>(DIARY_HANDLE)?.click();
    setExpanded(false);
  }, []);

  const quiet = !ctx.scroll;

  const { stage, blocker, next } = stripAnswers(ctx.scroll, status, loading, demo, shelfHref);
  const raw = mode === "expert";

  return (
    <div
      ref={stripRef}
      className="argus-context"
      data-expanded={expanded ? "true" : "false"}
      data-quiet={quiet ? "true" : "false"}
      data-context-header
      data-control="context.strip"
      data-loading={loading ? "true" : "false"}
      role="group"
      aria-label="Working context"
    >
      {ctx.scroll ? (
        <Link
          to={shelfHref}
          className="ctx-scroll interactive"
          data-control="context.scroll"
          data-novice="scroll"
          title={`${ctx.scroll} — clear the selection and return to the scroll shelf`}
        >
          <span className="ctx-scroll-id">{s?.display ?? ctx.scroll}</span>
          <span className="ctx-scroll-x" aria-hidden="true">
            {"✕"}
          </span>
          <span className="visually-hidden">
            Selected scroll {ctx.scroll}. Clear it and return to the scroll shelf.
          </span>
        </Link>
      ) : (
        <span className="ctx-scroll ctx-scroll-none" data-control="context.scroll.none" data-novice="scroll">
          No scroll selected
        </span>
      )}

      <span className="ctx-field" id="argus-context-details" data-novice="stage" title={run?.stage ? `latest run stage: ${run.stage}` : undefined}>
        <span className="ctx-key">Route + evidence</span>
        <span className="ctx-val" data-known={stage.known ? "true" : "false"}>{stage.text}</span>
      </span>

      <span className="ctx-field ctx-blocker" data-novice="blocker" title={raw ? `${blocker.full} (read from ${blocker.source})` : blocker.full}>
        <span className="ctx-key">Needs</span>
        <span className="ctx-val">{blocker.text}</span>
      </span>

      <span className="ctx-gap" aria-hidden="true" />

      <span className="ctx-field ctx-next" data-novice="next">
        <span className="ctx-key">Next</span>
        {next ? (
          <Link to={next.to} className="ctx-next-link interactive" data-control="context.next" title={next.why}>
            {next.label}
          </Link>
        ) : (
          <ScrollContextPicker
            control="context.next.picker"
            buttonClassName="ctx-next-link"
            buttonLabel="Choose a scroll"
          />
        )}
      </span>

      {
}
      <button
        type="button"
        className="ctx-more interactive"
        aria-expanded={expanded}
        aria-controls="argus-context-details"
        data-control="context.more"
        onClick={() => setExpanded((v) => !v)}
      >
        {expanded ? "Less" : "More"}
      </button>

      <span className="ctx-mode" role="group" aria-label="Interface mode" data-control="context.mode">
        {MODES.map((m) => (
          <button
            key={m}
            type="button"
            className="ctx-mode-btn interactive"
            aria-pressed={mode === m}
            data-control={`context.mode.${m}`}
            title={m === "guided"
              ? "Guided: the one next action, with raw receipts and ids behind a disclosure"
              : "Expert: every receipt, hash and ledger id inline"}
            onClick={() => setMode(m)}
          >
            {m === "guided" ? "Guided" : "Expert"}
          </button>
        ))}
      </span>

      {collapsed === null ? null : (
        <button
          type="button"
          className="ctx-grail interactive"
          data-control="context.grail"
          aria-pressed={!collapsed}
          onClick={toggleDiary}
          title={collapsed ? "Open the Grail Diary" : "Close the Grail Diary"}
        >
          <BookOpen size={15} strokeWidth={1.7} aria-hidden />
          Grail
        </button>
      )}
    </div>
  );
}
