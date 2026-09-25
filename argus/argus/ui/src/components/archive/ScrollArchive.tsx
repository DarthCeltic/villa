import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle, BookOpen, ChevronLeft, ChevronRight, FileCheck2, Layers, Maximize2,
  Feather, HardDrive, Minus, Package, Plus, RotateCcw, Scan, Search, Trophy, X,
} from "lucide-react";
import type { Collection, Lane, ScrollObject, Universe } from "../ShelfUniverse";
import { STAGE_ORDER, fmtBytes } from "../ShelfUniverse";
import { NATURAL_H, NATURAL_W, useArchiveView } from "./useArchiveView";
import "../../theme/archive.css";
import { Private } from "../../lib/publicDemo";

const BAYS = [
  { parch: [130, 316, 252, 540], plaque: [109, 559, 260, 603] },
  { parch: [455, 316, 575, 539], plaque: [441, 559, 590, 602] },
  { parch: [777, 316, 895, 539], plaque: [761, 559, 911, 602] },
  { parch: [1094, 316, 1217, 540], plaque: [1084, 559, 1235, 602] },
  { parch: [1419, 316, 1541, 539], plaque: [1412, 559, 1563, 602] },
] as const;
const PER_SHELF = BAYS.length;

const MIN_NAMED_PLAQUE_PX = 100;
const MIN_MARKED_PARCH_PX = 118;

const BRAND = "/brand/argus-scroll-library-shelf-v1";

export type MarkKey =
  | "first_letters" | "grand_prize" | "title_lane" | "control" | "local" | "surface" | "result"
  | "action";

export interface Mark {
  key: MarkKey;
  on: boolean;
  label: string;
  explain: string;
}

export function marksFor(s: ScrollObject): Mark[] {
  const stageIx = STAGE_ORDER.indexOf(s.surface.stage);
  const surface = stageIx >= STAGE_ORDER.indexOf("GEOMETRY");
  const action = s.blockedBy.length > 0 || Boolean(s.localDisagreement);
  return [
    { key: "first_letters", on: s.firstLetters, label: "First Letters target",
      explain: s.firstLetters ? "On the published First Letters list."
                              : "Not on the First Letters list." },
    { key: "grand_prize", on: s.grandPrize, label: "Grand Prize target",
      explain: s.grandPrize ? "On the Grand Prize list, and therefore also a First Letters target."
                            : "Not on the Grand Prize list." },
    { key: "title_lane", on: s.titleLane, label: "Paris 4 title lane",
      explain: s.titleLane
        ? "In the single-scroll title prize route, with its own requirements. Being in a lane is not a result."
        : "Not in the Paris 4 title lane." },
    { key: "control", on: s.lane === "CONTROL_OR_DEV", label: "control / development",
      explain: s.lane === "CONTROL_OR_DEV"
        ? "Not a prize target. Used to measure the instrument, never to claim a reading."
        : "A prize target, not a control." },
    { key: "local", on: s.local.state !== "NOTHING_INDEXED", label: "material local",
      explain: s.local.line + (s.publishedUpstream && s.local.state === "NOTHING_INDEXED"
        ? " Published upstream -- which is not the same as held here." : "") },
    { key: "surface", on: surface, label: "surface available", explain: s.surface.line },
    { key: "result", on: s.work.results > 0, label: "result available",
      explain: s.work.results > 0
        ? `${s.work.results} result(s) recorded. A result is not a verified reading.`
        : "No result recorded for this scroll." },
    { key: "action", on: action, label: "action needed",
      explain: action
        ? (s.localDisagreement ?? `Blocked: ${s.blockedBy.slice(0, 3).join("; ")}`)
        : "Nothing is recorded as blocking this scroll." },
  ];
}

const ICON: Record<MarkKey, typeof Trophy> = {
  first_letters: BookOpen, grand_prize: Trophy, title_lane: Feather, control: Scan, local: Package,
  surface: Layers, result: FileCheck2, action: AlertTriangle,
};

function MarkIcon({ m, size = 18 }: { m: Mark; size?: number }) {
  const Icon = ICON[m.key];
  return (
    <span className="arc-mark" data-mark={m.key} data-on={m.on ? "true" : "false"}
          title={`${m.label}: ${m.explain}`} aria-hidden="true">
      <Icon size={size} strokeWidth={2} />
    </span>
  );
}

function describe(s: ScrollObject): string {
  const on = marksFor(s).filter((m) => m.on).map((m) => m.label);
  return `${s.display}. ${on.length ? on.join(", ") : "no recorded marks"}. Choose to open.`;
}

function WorkBand({ universe, onOpen }: { universe: Universe; onOpen: (s: ScrollObject) => void }) {
  const effortFailed = universe.failures.find((f) => f.startsWith("/api/scroll-effort"));
  const held = universe.held;
  const shown = held.slice(0, 3);
  const more = held.slice(3);
  const totalBytes = held.reduce((n, h) => n + h.bytes, 0);
  const asOf = universe.holdingsAsOf ? universe.holdingsAsOf.slice(0, 10) : null;
  return (
    <div className="arc-band" data-archive-ui data-control="home.workband">
      {effortFailed ? (
        <p className="arc-band-note" role="status">
          Could not read what is on this machine or where the work has gone ({effortFailed}). That is
          the page failing to read, not an empty machine.
        </p>
      ) : !universe.sources.find((x) => x.route === "/api/scroll-effort")?.ok ? (
        <p className="arc-band-note">Reading what is on this machine…</p>
      ) : (
        <Private kind="acquisition_order">
          <div className="arc-held" role="group" aria-label="On this machine">
            <span className="arc-held-label" title={asOf ? `dataset catalogue built ${asOf}` : undefined}>
              <HardDrive size={18} aria-hidden="true" /> On this machine
              <span className="arc-held-asof">
                {" "}· {held.length} scroll{held.length === 1 ? "" : "s"}, {fmtBytes(totalBytes)}
              </span>
            </span>
            {held.length === 0 ? (
              <span className="arc-band-note">no scroll has bytes catalogued here</span>
            ) : (
              shown.map((h) => {
                const s = universe.byId.get(h.scroll);
                return s ? (
                  <button
                    key={h.scroll}
                    type="button"
                    className="arc-held-chip"
                    data-indexed={h.indexed ? "true" : "false"}
                    data-control={`home.held.${h.scroll}`}
                    onClick={() => onOpen(s)}
                    title={s.local.line}
                  >
                    <span>{s.display}</span>
                    <b>{h.bytes ? fmtBytes(h.bytes) : "indexed"}</b>
                  </button>
                ) : null;
              })
            )}
            {more.length ? (
              <details className="arc-held-more">
                <summary data-control="home.held.more">+{more.length} more</summary>
                {asOf ? <p className="arc-held-note">Catalogued {asOf}. Dashed means bytes are here but nothing indexed them.</p> : null}
                <ul>
                  {more.map((h) => {
                    const s = universe.byId.get(h.scroll);
                    return s ? (
                      <li key={h.scroll}>
                        <button type="button" onClick={() => onOpen(s)} data-control={`home.held.${h.scroll}`}>
                          {s.display} <b>{h.bytes ? fmtBytes(h.bytes) : "indexed"}</b>
                        </button>
                      </li>
                    ) : null;
                  })}
                </ul>
              </details>
            ) : null}
          </div>
        </Private>
      )}
    </div>
  );
}

const SHORT_TITLE: Record<Lane, string> = {
  FIRST_LETTERS: "First Letters",
  GRAND_PRIZE: "Grand Prize",
  PARIS4_TITLE: "Paris 4 title",
  CONTROL_OR_DEV: "Controls",
  UNCLASSIFIED: "Eligibility unknown",
};

export interface ScrollArchiveProps {
  universe: Universe;
  deviceClass: string;
  selected: string | null;
  collection: Lane;
  onCollection: (lane: Lane) => void;
  onSelect: (id: string | null) => void;
  renderDetail: (s: ScrollObject, close: () => void) => React.ReactNode;
}

export function ScrollArchive({
  universe, deviceClass, selected, collection, onCollection, onSelect, renderDetail,
}: ScrollArchiveProps) {
  const current: Collection | undefined = universe.collections.find((c) => c.lane === collection);
  const scrolls = current?.scrolls ?? [];
  const shelves = Math.max(1, Math.ceil(scrolls.length / PER_SHELF));

  const selectedIx = selected ? scrolls.findIndex((s) => s.id === selected) : -1;
  const [shelf, setShelf] = useState(0);
  useEffect(() => {
    if (selectedIx >= 0) setShelf(Math.floor(selectedIx / PER_SHELF));
  }, [selectedIx]);
  useEffect(() => {
    if (shelf > shelves - 1) setShelf(0);
  }, [shelf, shelves]);

  const onShelf = scrolls.slice(shelf * PER_SHELF, shelf * PER_SHELF + PER_SHELF);
  const sel = selected ? universe.byId.get(selected) ?? null : null;

  useEffect(() => {
    if (!selected) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      const t = e.target as HTMLElement | null;
      const editable = !!t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
      if (editable && !t.closest(".arc-inspect")) return;
      e.preventDefault();
      const q = CSS.escape(selected);
      const back = document.querySelector<HTMLElement>(`[data-control="home.scroll.${q}"]`)
        ?? document.querySelector<HTMLElement>(`[data-control="home.list.${q}"]`)
        ?? stageRef.current;
      back?.focus({ preventScroll: true });
      onSelect(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selected, onSelect]);

  const [turn, setTurn] = useState<{ dir: 1 | -1; phase: "out" | "in" } | null>(null);
  const turning = useRef(false);
  const reduced = typeof window !== "undefined"
    && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  const HALF_TURN_MS = 230;

  const animateTo = (dir: 1 | -1, apply: () => void) => {
    if (turning.current) return;
    if (reduced) { apply(); return; }
    turning.current = true;
    setTurn({ dir, phase: "out" });
    window.setTimeout(() => {
      apply();
      setTurn({ dir, phase: "in" });
      window.setTimeout(() => { setTurn(null); turning.current = false; }, HALF_TURN_MS);
    }, HALF_TURN_MS);
  };

  const turnWall = (dir: 1 | -1) => {
    if (shelves <= 1) return;
    animateTo(dir, () => setShelf((n) => (n + dir + shelves) % shelves));
  };

  const [listOpen, setListOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [hover, setHover] = useState<string | null>(null);
  const v = useArchiveView(deviceClass, turnWall);
  const stageRef = useRef<HTMLDivElement | null>(null);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return [];
    return universe.scrolls
      .filter((s) => s.id.toLowerCase().includes(q) || s.display.toLowerCase().includes(q)
                     || s.aliases.some((a) => a.toLowerCase().includes(q)))
      .slice(0, 8);
  }, [query, universe.scrolls]);

  type Box = readonly [number, number, number, number];
  const toScreen = (b: Box) => {
    const view = v.view;
    if (!view) return null;
    return {
      left: view.tx + b[0] * view.scale,
      top: view.ty + b[1] * view.scale,
      width: (b[2] - b[0]) * view.scale,
      height: (b[3] - b[1]) * view.scale,
    };
  };

  const chooseFromSearch = (s: ScrollObject) => {
    onCollection(s.lane === "GRAND_PRIZE" && collection === "FIRST_LETTERS" ? collection : s.lane);
    onSelect(s.id);
    setQuery("");
  };

  return (
    <section className="arc" aria-label="Scroll archive" data-control="home.archive">

      {
}
      <div className="arc-room" data-inspecting={sel ? "true" : "false"}
           data-tip={hover && hover !== selected ? "true" : undefined}>
      <div className="arc-top" data-archive-ui>
      <header className="arc-head" data-archive-ui>
        <div className="arc-title">
          <h1>Scroll archive</h1>
          <p className="arc-lede">These are the scrolls. Choose one.</p>
        </div>

        <div className="arc-collections" role="tablist" aria-label="Collections">
          {universe.collections.map((c) => (
            <button
              key={c.lane}
              role="tab"
              aria-selected={c.lane === current?.lane}
              className="arc-collection"
              data-control={`home.collection.${c.lane}`}
              onClick={() => {
                if (c.lane === current?.lane) return;
                animateTo(1, () => { onCollection(c.lane); setShelf(0); });
              }}
              title={`${c.title}: ${c.why}${c.note ? " " + c.note : ""} (count from ${c.count.route})`}
            >
              <span className="arc-collection-name" aria-label={c.title}>{SHORT_TITLE[c.lane]}</span>
              <span className="arc-collection-count">{c.count.value ?? "unknown"}</span>
              {c.lane === "GRAND_PRIZE" && (
                <span className="arc-subset" aria-label="overlaps First Letters">
                  overlaps First Letters
                </span>
              )}
            </button>
          ))}
        </div>

        <div className="arc-search">
          <Search size={18} aria-hidden="true" />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Find a scroll"
            aria-label="Find a scroll by id, name or alias"
            data-control="home.search"
          />
          {matches.length > 0 && (
            <ul className="arc-search-results" role="listbox">
              {matches.map((s) => (
                <li key={s.id}>
                  <button role="option" aria-selected={s.id === selected}
                          data-control={`home.search.pick.${s.id}`}
                          onClick={() => chooseFromSearch(s)}>
                    {s.display}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </header>
        {!sel && <WorkBand universe={universe} onOpen={chooseFromSearch} />}
      </div>
      <div
        className="arc-stage"
        data-inspecting={sel ? "true" : "false"}
        ref={(el) => { v.hostRef.current = el; stageRef.current = el; }}
        tabIndex={0}
        role="region"
        aria-label="Archive shelf. Left and right arrows turn the wall. Ctrl or Cmd with the wheel zooms; once zoomed, drag pans. Plus and minus zoom, 0 fits the room, 1 is 100%."
        data-control="home.archive.stage"
        data-view={deviceClass === "phone" ? "whole" : "standing"}
        {...v.handlers}
        onKeyDown={(e) => {
          if (!e.shiftKey && (e.key === "ArrowRight" || e.key === "ArrowLeft")) {
            e.preventDefault();
            turnWall(e.key === "ArrowRight" ? 1 : -1);
            return;
          }
          v.handlers.onKeyDown(e);
        }}
      >
        {
}
        <div className="arc-face"
             data-turn={turn?.phase ?? "rest"}
             data-dir={turn?.dir === -1 ? "back" : "forward"}
             aria-live="polite"
             aria-atomic="false">
        {v.view && (
          <div
            className="arc-world"
            style={{
              width: NATURAL_W, height: NATURAL_H,
              transform: `translate(${v.view.tx}px, ${v.view.ty}px) scale(${v.view.scale})`,
            }}
          >
            <picture>
              <source type="image/avif"
                srcSet={`${BRAND}-390w.avif 390w, ${BRAND}-780w.avif 780w, ${BRAND}-1366w.avif 1366w, ${BRAND}-1600w.avif 1600w`}
                sizes="100vw" />
              <source type="image/webp"
                srcSet={`${BRAND}-390w.webp 390w, ${BRAND}-780w.webp 780w, ${BRAND}-1366w.webp 1366w, ${BRAND}-1600w.webp 1600w`}
                sizes="100vw" />
              <img src={`${BRAND}-master.png`} alt="" width={NATURAL_W} height={NATURAL_H}
                   draggable={false} decoding="async" />
            </picture>
          </div>
        )}
        {v.view && <div className="arc-vignette" aria-hidden="true" />}

        {v.view && onShelf.map((s, i) => {
          const bay = BAYS[i];
          if (!bay) return null;
          const plaque = toScreen(bay.plaque);
          const parch = toScreen(bay.parch);
          if (!plaque || !parch) return null;
          const named = plaque.width >= MIN_NAMED_PLAQUE_PX;
          const marked = parch.height >= MIN_MARKED_PARCH_PX;
          const isSel = s.id === selected;
          const ms = marksFor(s);
          return (
            <button
              key={s.id}
              data-archive-object
              data-scroll={s.id}
              data-control={`home.scroll.${s.id}`}
              data-bay={i}
              className="arc-object"
              aria-pressed={isSel}
              aria-label={describe(s)}
              onClick={() => onSelect(isSel ? null : s.id)}
              onMouseEnter={() => setHover(s.id)}
              onMouseLeave={() => setHover((h) => (h === s.id ? null : h))}
              onFocus={() => setHover(s.id)}
              onBlur={() => setHover((h) => (h === s.id ? null : h))}
              style={{ left: parch.left, top: parch.top, width: parch.width,
                       height: plaque.top + plaque.height - parch.top }}
            >
              {s.fixture.fixture && <span className="arc-fixture">fixture</span>}
              {marked && (
                <span className="arc-marks" aria-hidden="true">
                  {ms.filter((m) => m.on).map((m) => <MarkIcon key={m.key} m={m} />)}
                </span>
              )}
              <span
                className="arc-plaque"
                data-named={named ? "true" : "false"}
                style={{ top: plaque.top - parch.top, left: plaque.left - parch.left,
                         width: plaque.width, height: plaque.height }}
              >
                {named ? s.display : shelf * PER_SHELF + i + 1}
              </span>
              {hover === s.id && !isSel && (
                <span className="arc-tip" role="tooltip">
                  <strong>{s.display}</strong>
                  {ms.filter((m) => m.on).map((m) => (
                    <span key={m.key} className="arc-tip-row">
                      <MarkIcon m={m} size={16} /> {m.label}
                    </span>
                  ))}
                  <span className="arc-tip-next">{s.next.label}</span>
                </span>
              )}
            </button>
          );
        })}
        </div>

        {
}
        {shelves > 1 && !sel && (
          <>
            <button className="arc-edge" data-side="back" onClick={() => turnWall(-1)}
                    data-control="home.wall.edge.back" aria-label="Turn the wall back">
              <ChevronLeft size={30} aria-hidden="true" />
            </button>
            <button className="arc-edge" data-side="forward" onClick={() => turnWall(1)}
                    data-control="home.wall.edge.forward" aria-label="Turn the wall forward">
              <ChevronRight size={30} aria-hidden="true" />
            </button>
          </>
        )}

        <div className="arc-zoom" role="toolbar" aria-label="Archive view">
          <button onClick={v.fit} data-control="home.view.fit" title="Fit the room">
            <Maximize2 size={18} aria-hidden="true" /> <span>Fit room</span>
          </button>
          <button onClick={v.zoomOut} data-control="home.view.out" aria-label="Zoom out">
            <Minus size={18} aria-hidden="true" />
          </button>
          <button onClick={v.actual} data-control="home.view.actual" title="One image pixel to one screen pixel">
            100%
          </button>
          <button onClick={v.zoomIn} data-control="home.view.in" aria-label="Zoom in">
            <Plus size={18} aria-hidden="true" />
          </button>
          <button onClick={v.reset} data-control="home.view.reset" title="Reset the view">
            <RotateCcw size={18} aria-hidden="true" /> <span>Reset</span>
          </button>
          <output className="arc-percent" aria-live="polite">{v.percent}%</output>
        </div>

        {!sel && (
        <nav className="arc-turn" data-archive-ui aria-label={`Turn the ${current?.title ?? "collection"} wall`}>
        <button className="arc-turn-btn" onClick={() => turnWall(-1)} disabled={shelves <= 1}
                data-control="home.wall.turn.back" aria-label="Turn the wall back">
          <ChevronLeft size={22} aria-hidden="true" /> <span>Turn</span>
        </button>

        <div className="arc-faces" role="group" aria-label="Faces of this wall">
          {Array.from({ length: shelves }, (_, f) => {
            const from = f * PER_SHELF + 1;
            const to = Math.min(scrolls.length, (f + 1) * PER_SHELF);
            const holdsSelected = selectedIx >= 0 && Math.floor(selectedIx / PER_SHELF) === f;
            return (
              <button
                key={f}
                className="arc-face-dot"
                aria-current={f === shelf ? "true" : undefined}
                data-selected-here={holdsSelected ? "true" : "false"}
                data-control={`home.wall.face.${f + 1}`}
                aria-label={`Face ${f + 1} of ${shelves}: scrolls ${from} to ${to}`
                            + (holdsSelected ? ", holds the selected scroll" : "")}
                onClick={() => {
                  if (f === shelf) return;
                  animateTo(f > shelf ? 1 : -1, () => setShelf(f));
                }}
              />
            );
          })}
          <span className="arc-faces-where" aria-live="polite">
            Face {shelf + 1} of {shelves}
            <span className="arc-faces-range">
              {" "}· {scrolls.length ? shelf * PER_SHELF + 1 : 0}–
              {Math.min(scrolls.length, (shelf + 1) * PER_SHELF)} of {scrolls.length}
            </span>
          </span>
        </div>

        <button className="arc-turn-btn" onClick={() => turnWall(1)} disabled={shelves <= 1}
                data-control="home.wall.turn.forward" aria-label="Turn the wall forward">
          <span>Turn</span> <ChevronRight size={22} aria-hidden="true" />
        </button>
        </nav>
        )}
        {sel && (
          <div className="arc-inspect" role="dialog" aria-label={`Inspecting ${sel.display}`}>
            <div className="arc-inspect-scroll" data-control={`home.inspect.${sel.id}`}>
              <span className="arc-inspect-roller" aria-hidden="true" />
              <div className="arc-inspect-sheet">
                <div className="arc-inspect-marks">
                  {marksFor(sel).map((m) => (
                    <span key={m.key} className="arc-inspect-mark" data-on={m.on ? "true" : "false"}>
                      <MarkIcon m={m} size={18} />
                      <span className="arc-inspect-mark-text">
                        <strong>{m.label}</strong> {m.on ? "yes" : "no"}
                      </span>
                    </span>
                  ))}
                </div>
                <div className="arc-inspect-plaque">{sel.display}</div>
              </div>
              <span className="arc-inspect-roller" aria-hidden="true" />
            </div>
            <div className="arc-inspect-detail">
              <button className="arc-close" onClick={() => onSelect(null)}
                      data-control="home.inspect.close" aria-label="Return the scroll to the shelf">
                <X size={18} aria-hidden="true" /> <span>Back to shelf</span>
              </button>
              {renderDetail(sel, () => onSelect(null))}
            </div>
          </div>
        )}
      </div>
      </div>

      {
}
      <details className="arc-list" open={listOpen}
               onToggle={(e) => setListOpen((e.currentTarget as HTMLDetailsElement).open)}>
        <summary data-control="home.wall.list">
          List all {scrolls.length} in {current?.title ?? "this collection"}
        </summary>
        <ol className="arc-list-items">
          {scrolls.map((s, i) => (
            <li key={s.id}>
              <button
                className="arc-list-item"
                data-control={`home.list.${s.id}`}
                aria-pressed={s.id === selected}
                aria-label={describe(s)}
                onClick={() => { setShelf(Math.floor(i / PER_SHELF)); onSelect(s.id); }}
              >
                <span className="arc-chip-n">{i + 1}</span>
                <span className="arc-chip-name">{s.display}</span>
                <span className="arc-chip-marks" aria-hidden="true">
                  {marksFor(s).filter((m) => m.on && m.key !== "first_letters").map((m) => (
                    <MarkIcon key={m.key} m={m} size={15} />
                  ))}
                </span>
              </button>
            </li>
          ))}
        </ol>
      </details>
    </section>
  );
}
