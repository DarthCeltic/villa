import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useLocation } from "react-router-dom";
import {
  BookOpen,
  ChevronDown,
  ChevronUp,
  LayoutGrid,
  MessageSquarePlus,
  Paperclip,
  RotateCcw,
  X,
} from "lucide-react";

import {
  ALLOWED_ATTACHMENT_MIME,
  addAnnotation,
  hideAnnotation,
  readAnnotations,
  uploadAttachment,
} from "../lib/governed";
import type { AttachmentMeta, OperatorNote } from "../lib/governed";
import { SHEET_STOPS, ZONES, useDockedPanel } from "../lib/useDockedPanel";
import type { SheetStop, Zone } from "../lib/useDockedPanel";
import {
  BUILD_COMMAND,
  kb,
  loadEvidence,
  shortHash,
  thumbUrl,
} from "../lib/diaryEvidence";
import type { EvidenceRow, EvidenceState } from "../lib/diaryEvidence";
import "../theme/diary.css";
import type { RunRecord } from "../api";
import { useArgusContext } from "../lib/context";
import { useSharedUniverse } from "../lib/sharedUniverse";
import { GrailNavigator, type GrailTab } from "./grail/GrailNavigator";
import { ScrollStatusBar } from "./ScrollStatusBar";
import { TOKEN_FALLBACK } from "../theme/tokenFallbacks";


const POSITION_KEY = "argus.grail.position";
const NAV_KEY = "argus.grail.navigator";

function readNav(): { tab: GrailTab; pinned: boolean } {
  try {
    const v = JSON.parse(window.localStorage.getItem(NAV_KEY) || "{}");
    const tabs: GrailTab[] = ["brief", "evidence", "findings", "notes", "diagnostics"];
    return { tab: tabs.includes(v.tab) ? v.tab : "brief", pinned: v.pinned === true };
  } catch {
    return { tab: "brief", pinned: false };
  }
}
function writeNav(v: { tab: GrailTab; pinned: boolean }) {
  try { window.localStorage.setItem(NAV_KEY, JSON.stringify(v)); } catch {  }
}

interface GrailSource {
  path?: string;
  state?: string;
  finding_lag?: number | null;
}

interface Grail {
  present: boolean;
  why?: string;
  how?: string;
  built_utc?: string;
  age_s?: number;
  snapshot_is_stale?: boolean;
  overall_state?: string;
  failures?: GrailSource[];
  warnings?: GrailSource[];
  counts?: {
    failures?: number;
    warnings?: number;
    findings?: number;
    with_ledger_entry?: number;
  };
  rebuild_with?: string;
}

function age(seconds: number | undefined): string {
  if (seconds === undefined) return "unknown age";
  if (seconds < 90) return "built just now";
  if (seconds < 5400) return `built ${Math.round(seconds / 60)} min ago`;
  if (seconds < 172800) return `built ${Math.round(seconds / 3600)} h ago`;
  return `built ${Math.round(seconds / 86400)} d ago`;
}

function shortAge(seconds: number | undefined): string {
  if (seconds === undefined) return "?";
  if (seconds < 90) return "now";
  if (seconds < 5400) return `${Math.round(seconds / 60)}m`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86400)}d`;
}

function tone(g: Grail): { colour: string; label: string } {
  if (!g.present) return { colour: "var(--ink-dim)", label: "not built" };
  if (g.snapshot_is_stale) return { colour: "var(--warn)", label: "snapshot stale" };
  if ((g.counts?.failures ?? 0) > 0) return { colour: "var(--warn)", label: g.overall_state ?? "" };
  return { colour: "var(--accent)", label: g.overall_state ?? "" };
}


function Attachment({ a }: { a: AttachmentMeta }) {
  const [broken, setBroken] = useState(false);
  const src = `/api/grail/attachments/${encodeURIComponent(a.id)}`;
  if (broken)
    return (
      <span className="small faint" style={{ display: "inline-block", padding: "2px 6px" }}>
        {a.filename} could not be loaded
      </span>
    );
  if (a.mime.startsWith("video/"))
    return (
      <video
        src={src}
        controls
        style={{ maxWidth: 220, maxHeight: 160, display: "block", borderRadius: 4 }}
        onError={() => setBroken(true)}
      />
    );
  return (
    <a href={src} target="_blank" rel="noreferrer" title={a.filename}>
      <img
        src={src}
        alt={a.filename}
        style={{
          maxWidth: 120,
          maxHeight: 120,
          display: "block",
          borderRadius: 4,
          border: "1px solid var(--line-strong)",
        }}
        onError={() => setBroken(true)}
      />
    </a>
  );
}

const ACCEPT_ATTACHMENT = Object.keys(ALLOWED_ATTACHMENT_MIME).join(",");

function Notes({
  target,
  notes,
  writing,
  draft,
  busy,
  pending,
  attaching,
  attachErr,
  onOpen,
  onCancel,
  onDraft,
  onSave,
  onHide,
  onAttach,
  onRemovePending,
}: {
  target: string;
  notes: OperatorNote[];
  writing: string | null;
  draft: string;
  busy: boolean;
  pending: AttachmentMeta[];
  attaching: boolean;
  attachErr: string | null;
  onOpen: (t: string) => void;
  onCancel: () => void;
  onDraft: (v: string) => void;
  onSave: (t: string) => void;
  onHide: (id: string) => void;
  onAttach: (files: FileList | File[]) => void;
  onRemovePending: (id: string) => void;
}) {
  const open = writing === target;
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  return (
    <div style={{ marginTop: 4 }}>
      {notes.map((n) => (
        <div
          key={n.id}
          className="meta"
          style={{
            display: "flex",
            gap: 6,
            alignItems: "flex-start",
            borderLeft: "2px solid var(--line-strong)",
            paddingLeft: 8,
            marginTop: 4,
            color: "var(--ink)",
          }}
        >
          <span style={{ flex: 1, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
            <span style={{ color: "var(--ink-dim)" }}>note · {n.author} · {n.utc} — </span>
            {n.text}
            {n.attachments && n.attachments.length ? (
              <span
                style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6 }}
                data-control="annotation.attachments"
              >
                {n.attachments.map((a) => (
                  <Attachment key={a.id} a={a} />
                ))}
              </span>
            ) : null}
          </span>
          <button
            type="button"
            aria-label={`hide this note on ${target}`}
            data-control="annotation.hide"
            onClick={() => onHide(n.id)}
            className="interactive"
            style={{
              background: "transparent",
              border: "none",
              cursor: "pointer",
              color: "var(--ink-dim)",
              padding: 2,
              flex: "0 0 auto",
            }}
          >
            <X size={12} aria-hidden />
          </button>
        </div>
      ))}

      {open ? (
        <div style={{ marginTop: 6, display: "grid", gap: 6 }}>
          {
}
          <textarea
            value={draft}
            autoFocus
            onChange={(e) => onDraft(e.target.value)}
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragOver(false);
              if (e.dataTransfer.files.length) onAttach(e.dataTransfer.files);
            }}
            placeholder={`What do you know about ${target} that the diary cannot see? Drop a photo or clip in too.`}
            rows={3}
            style={{
              width: "100%",
              boxSizing: "border-box",
              font: "inherit",
              padding: 8,
              borderRadius: "var(--radius-sm)",
              border: dragOver ? `1px dashed var(--identity-copper, ${TOKEN_FALLBACK.identityCopper})` : "1px solid var(--line-strong)",
              background: dragOver ? "var(--bg-raised)" : "var(--bg)",
              color: "var(--ink)",
              resize: "vertical",
            }}
          />
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept={ACCEPT_ATTACHMENT}
            style={{ display: "none" }}
            onChange={(e) => {
              if (e.target.files?.length) onAttach(e.target.files);
              e.target.value = "";
            }}
          />
          {pending.length || attaching ? (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {pending.map((a) => (
                <span
                  key={a.id}
                  className="small"
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 4,
                    padding: "3px 6px",
                    borderRadius: "var(--radius-sm)",
                    border: "1px solid var(--line-strong)",
                    background: "var(--bg-raised)",
                  }}
                >
                  {a.filename}
                  <button
                    type="button"
                    aria-label={`remove ${a.filename}`}
                    onClick={() => onRemovePending(a.id)}
                    style={{ background: "transparent", border: "none", cursor: "pointer", color: "var(--ink-dim)" }}
                  >
                    <X size={11} aria-hidden />
                  </button>
                </span>
              ))}
              {attaching ? <span className="small faint">uploading…</span> : null}
            </div>
          ) : null}
          {attachErr ? <p className="small" style={{ color: "var(--status-refused)" }}>{attachErr}</p> : null}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button
              type="button"
              className="interactive"
              disabled={busy || !draft.trim()}
              data-control="annotation.save"
              onClick={() => onSave(target)}
              style={{ minHeight: "var(--control-h)", padding: "0 12px" }}
            >
              {busy ? "Saving…" : "Save note"}
            </button>
            <button
              type="button"
              className="interactive"
              data-control="annotation.attach"
              onClick={() => fileInputRef.current?.click()}
              title="attach a photo or clip"
              style={{
                minHeight: "var(--control-h)",
                padding: "0 12px",
                display: "inline-flex",
                alignItems: "center",
                gap: 5,
              }}
            >
              <Paperclip size={13} aria-hidden /> Attach
            </button>
            <button
              type="button"
              className="interactive"
              data-control="annotation.cancel"
              onClick={onCancel}
              style={{ minHeight: "var(--control-h)", padding: "0 12px" }}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          className="interactive"
          onClick={() => onOpen(target)}
          aria-label={`annotate ${target}`}
          data-control="annotation.open"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 5,
            marginTop: 4,
            background: "transparent",
            border: "none",
            cursor: "pointer",
            color: "var(--ink-dim)",
            padding: "2px 0",
            font: "inherit",
          }}
        >
          <MessageSquarePlus size={12} aria-hidden />
          <span className="meta">{notes.length ? "add another note" : "annotate"}</span>
        </button>
      )}
    </div>
  );
}

function Evidence({ ev }: { ev: EvidenceState }) {
  const [broken, setBroken] = useState<Record<string, string>>({});
  const m = ev.manifest;
  const rows: EvidenceRow[] = m?.rows ?? [];

  const textRow = (r: EvidenceRow, reason: string) => (
    <div className="grail-diary-evidence-row" key={r.path} data-evidence-row={r.path}>
      <code className="mono">{r.path}</code>
      <span className="grail-diary-shot-dims">
        {r.pixels.w}×{r.pixels.h} · {kb(r.bytes)}
      </span>
      <code className="mono grail-diary-shot-hash">sha256 {shortHash(r.sha256)}</code>
      <span className="grail-diary-evidence-why" data-evidence-reason>
        no image endpoint
      </span>
      <span className="meta" style={{ gridColumn: "1 / -1" }}>
        {reason}
      </span>
    </div>
  );

  return (
    <div className="grail-diary-evidence" data-evidence-section>
      <div style={{ fontWeight: 600 }}>Evidence on disk</div>

      {!m ? (
        <div className="meta" data-evidence-empty>
          {ev.reason ?? "the evidence manifest could not be read"}
          {ev.reason && ev.reason.includes(BUILD_COMMAND) ? null : (
            <>
              {" "}
              The manifest comes from {BUILD_COMMAND}; without it the diary shows no screenshots and claims none.
            </>
          )}
        </div>
      ) : (
        <>
          <div className="meta">
            {m.kept} of {m.scanned} PNG(s) enumerated{" "}
            <span data-evidence-generated>{m.generated_utc}</span>, hashed and sized from each
            file's own header. A selection, not a gallery — the manifest lists what it left out
            and why.
          </div>

          {ev.serving ? (
            <div className="grail-diary-evidence-grid">
              {rows.map((r) => {
                const failed = broken[r.path];
                return failed ? (
                  textRow(r, failed)
                ) : (
                  <a
                    className="grail-diary-shot"
                    key={r.path}
                    href={thumbUrl(r)}
                    target="_blank"
                    rel="noreferrer"
                    data-evidence-row={r.path}
                    title={`${r.path}\nsha256 ${r.sha256}\n${r.pixels.w}×${r.pixels.h}, ${kb(
                      r.bytes,
                    )}, ${r.mtime_utc}${r.dir_why ? `\n${r.dir_why}` : ""}`}
                  >
                    <img
                      src={thumbUrl(r)}
                      alt={`${r.path}, ${r.pixels.w} by ${r.pixels.h} pixels, sha256 ${shortHash(
                        r.sha256,
                      )}`}
                      loading="eager"
                      decoding="sync"
                      width={r.pixels.w}
                      height={r.pixels.h}
                      data-evidence-img={r.path}
                      onError={() =>
                        setBroken((b) => ({
                          ...b,
                          [r.path]:
                            "the service stopped serving these bytes after the manifest was " +
                            "written. The path and hash below are what was hashed; rebuild " +
                            "the manifest with " + BUILD_COMMAND,
                        }))
                      }
                    />
                    <span className="grail-diary-shot-dims">
                      {r.pixels.w}×{r.pixels.h} · {kb(r.bytes)}
                    </span>
                    <span className="grail-diary-shot-hash">{shortHash(r.sha256)}</span>
                  </a>
                );
              })}
            </div>
          ) : (
            <div style={{ display: "grid", gap: 6 }}>
              {rows.map((r) =>
                textRow(
                  r,
                  ev.reason ?? "the service did not serve these bytes; no reason was reported",
                ),
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

export function GrailDiary({ runs }: { runs: RunRecord[] }) {
  const { ctx } = useArgusContext();
  const location = useLocation();
  const universe = useSharedUniverse();
  const [nav, setNav] = useState(readNav);
  const setTab = useCallback((tab: GrailTab) => setNav((n) => { const v = { ...n, tab }; writeNav(v); return v; }), []);
  const setPinned = useCallback((pinned: boolean) => setNav((n) => { const v = { ...n, pinned }; writeNav(v); return v; }), []);
  const selected = ctx.scroll ? universe.byId.get(ctx.scroll) ?? null : null;
  const activeRuns = runs.filter((r) => r.operational_state === "RUNNING");
  const pageRef = useRef<HTMLDivElement>(null);

  const lastScroll = useRef(ctx.scroll);
  useEffect(() => {
    if (lastScroll.current !== ctx.scroll) {
      lastScroll.current = ctx.scroll;
      setTab("brief");
      pageRef.current?.scrollTo({ top: 0, behavior: "auto" });
    }
  }, [ctx.scroll, setTab]);

  const [g, setG] = useState<Grail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, OperatorNote[]>>({});
  const [notesErr, setNotesErr] = useState<string | null>(null);
  const [writing, setWriting] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [writeErr, setWriteErr] = useState<string | null>(null);
  const [pendingAttachments, setPendingAttachments] = useState<AttachmentMeta[]>([]);
  const [attaching, setAttaching] = useState(false);
  const [attachErr, setAttachErr] = useState<string | null>(null);

  const loadNotes = useCallback(async () => {
    try {
      const d = await readAnnotations();
      if (!d.ok) throw new Error(d.why ?? "the notes could not be read");
      setNotes(d.by_target ?? {});
      setNotesErr(null);
    } catch (e) {
      setNotesErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/grail", { credentials: "same-origin" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setG((await r.json()) as Grail);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    load();
    loadNotes();
    const t = window.setInterval(() => {
      load();
      loadNotes();
    }, 120000);
    return () => window.clearInterval(t);
  }, [load, loadNotes]);

  const save = useCallback(
    async (target: string) => {
      const text = draft.trim();
      if (!text) return;
      setBusy(true);
      setWriteErr(null);
      try {
        await addAnnotation(target, text, undefined, pendingAttachments.map((a) => a.id));
        setDraft("");
        setWriting(null);
        setPendingAttachments([]);
        await loadNotes();
      } catch (e) {
        setWriteErr(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [draft, pendingAttachments, loadNotes],
  );

  const attachFiles = useCallback(async (files: FileList | File[]) => {
    setAttaching(true);
    setAttachErr(null);
    try {
      for (const f of Array.from(files)) {
        const rec = await uploadAttachment(f);
        setPendingAttachments((cur) => [...cur, rec]);
      }
    } catch (e) {
      setAttachErr(e instanceof Error ? e.message : String(e));
    } finally {
      setAttaching(false);
    }
  }, []);

  const removePendingAttachment = useCallback((id: string) => {
    setPendingAttachments((cur) => cur.filter((a) => a.id !== id));
  }, []);

  const drop = useCallback(
    async (id: string) => {
      try {
        await hideAnnotation(id);
        await loadNotes();
      } catch (e) {
        setWriteErr(e instanceof Error ? e.message : String(e));
      }
    },
    [loadNotes],
  );

  const [ev, setEv] = useState<EvidenceState>({
    manifest: null,
    serving: false,
    reason: null,
    manifestUrl: "",
  });
  useEffect(() => {
    let live = true;
    loadEvidence().then((s) => {
      if (live) setEv(s);
    });
    return () => {
      live = false;
    };
  }, []);

  const dock = useDockedPanel(POSITION_KEY);
  const compactedInitialState = useRef(false);
  useEffect(() => {
    if (compactedInitialState.current) return;
    compactedInitialState.current = true;
    if (
      !nav.pinned &&
      !dock.narrow &&
      dock.layout.zone === "BOTTOM" &&
      !dock.layout.bottomChosen
    ) dock.dockTo("RIGHT");
    if (!nav.pinned && !dock.layout.collapsed) dock.setCollapsed(true);
  }, [dock.dockTo, dock.layout.bottomChosen, dock.layout.collapsed, dock.layout.zone, dock.narrow, dock.setCollapsed, nav.pinned]);

  const routeKey = `${location.pathname}${location.search}`;
  const previousRoute = useRef(routeKey);
  useEffect(() => {
    if (previousRoute.current === routeKey) return;
    previousRoute.current = routeKey;
    if (!nav.pinned) dock.setCollapsed(true);
  }, [dock.setCollapsed, nav.pinned, routeKey]);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const chipRef = useRef<HTMLButtonElement | null>(null);

  const [flip, setFlip] = useState<{ up: boolean; left: boolean }>({ up: false, left: false });

  useEffect(() => {
    if (!menuOpen) {
      setFlip({ up: false, left: false });
      return;
    }
    const el = menuRef.current;
    if (el) {
      const r = el.getBoundingClientRect();
      setFlip({
        up: r.bottom > window.innerHeight - 4,
        left: r.right > window.innerWidth - 4,
      });
    }
    const items = el ? Array.from(el.querySelectorAll<HTMLButtonElement>("button")) : [];
    (items.find((b) => b.getAttribute("aria-checked") === "true") ?? items[0])?.focus({ preventScroll: true });

    const onDocKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      setMenuOpen(false);
      chipRef.current?.focus();
    };
    const onDocPointer = (e: PointerEvent) => {
      const t = e.target as Node | null;
      if (el?.contains(t as Node) || chipRef.current?.contains(t as Node)) return;
      setMenuOpen(false);
    };
    document.addEventListener("keydown", onDocKey);
    document.addEventListener("pointerdown", onDocPointer, true);
    return () => {
      document.removeEventListener("keydown", onDocKey);
      document.removeEventListener("pointerdown", onDocPointer, true);
    };
  }, [menuOpen]);

  const onMenuKeyDown = useCallback((e: React.KeyboardEvent) => {
    const el = menuRef.current;
    if (!el) return;
    const items = Array.from(el.querySelectorAll<HTMLButtonElement>("button"));
    const i = items.indexOf(document.activeElement as HTMLButtonElement);
    const move =
      e.key === "ArrowDown" || e.key === "ArrowRight"
        ? 1
        : e.key === "ArrowUp" || e.key === "ArrowLeft"
          ? -1
          : 0;
    if (move) {
      e.preventDefault();
      items[(i + move + items.length) % items.length]?.focus({ preventScroll: true });
      return;
    }
    if (e.key === "Home") {
      e.preventDefault();
      items[0]?.focus({ preventScroll: true });
    } else if (e.key === "End") {
      e.preventDefault();
      items[items.length - 1]?.focus({ preventScroll: true });
    }
  }, []);

  const collapsed = dock.layout.collapsed;

  const toggle = useCallback(() => {
    if (dock.consumedClick()) return;
    if (nav.pinned && !collapsed) return;
    if (dock.narrow) {
      dock.setSheet(collapsed ? "FULL" : "COLLAPSED");
      dock.setCollapsed(!collapsed);
      return;
    }
    dock.setCollapsed(!collapsed);
  }, [dock, collapsed, nav.pinned]);

  useEffect(() => {
    if (nav.pinned && collapsed) dock.setCollapsed(false);
  }, [nav.pinned, collapsed, dock]);

  const gs: Grail = g ?? { present: false };
  const t = tone(gs);
  const fails = gs.counts?.failures ?? 0;
  const warns = gs.counts?.warnings ?? 0;
  const contextLine = selected
    ? `Brief · ${selected.display}`
    : ctx.scroll
      ? `Brief · ${ctx.scroll}`
      : "Project brief";

  const noteSlot = (target: string) => (
    <>
      <Notes
        target={target}
        notes={notes[target] ?? []}
        writing={writing}
        draft={draft}
        busy={busy}
        pending={pendingAttachments}
        attaching={attaching}
        attachErr={attachErr}
        onOpen={(x) => {
          setWriting(x);
          setDraft("");
          setWriteErr(null);
          setPendingAttachments([]);
          setAttachErr(null);
        }}
        onCancel={() => {
          setWriting(null);
          setDraft("");
          setPendingAttachments([]);
          setAttachErr(null);
        }}
        onDraft={setDraft}
        onSave={save}
        onHide={drop}
        onAttach={attachFiles}
        onRemovePending={removePendingAttachment}
      />
      {notesErr ? (
        <div className="meta" style={{ color: "var(--warn)" }}>
          Notes could not be read: {notesErr}. This is the panel failing to read, not a statement
          that you have written none.
        </div>
      ) : null}
      {writeErr ? <div className="meta" style={{ color: "var(--warn)" }}>Not saved: {writeErr}</div> : null}
    </>
  );

  const rail = collapsed && (dock.sideDocked || dock.narrow);
  const launcherOnly = collapsed && !nav.pinned && !(dock.layout.zone === "BOTTOM" && dock.layout.bottomChosen && !dock.narrow);
  const strip = `Grail: ${contextLine}. Project snapshot ${t.label || "state unknown"}, ${fails} failing, ${warns} stale, ${age(
    gs.age_s,
  )}`;

  return (
    <aside
      ref={dock.setPanel}
      className="grail-diary"
      data-zone={dock.narrow ? "SHEET" : dock.layout.zone}
      data-sheet={dock.narrow ? dock.layout.sheet : undefined}
      data-floating={dock.floating ? "true" : undefined}
      data-dragging={dock.dragging ? "true" : undefined}
      data-collapsed={collapsed ? "true" : "false"}
      data-rail={rail ? "true" : undefined}
      data-launcher-only={launcherOnly ? "true" : undefined}
      hidden={launcherOnly}
      aria-label="Grail Diary: project state at a glance"
      style={dock.panelStyle}
    >
      <button
        type="button"
        onClick={toggle}
        data-control="diary.toggle"
        aria-expanded={!collapsed}
        className="interactive grail-diary-handle"
        title={
          rail
            ? `${strip}. Click to open. Arrow keys move it between zones; Alt+Arrow docks.`
            : "Drag to move or dock to an edge. Arrow keys move it between zones, or nudge it " +
              "when floating. Alt+Arrow docks from anywhere. Click to open."
        }
        aria-label={rail ? `${strip}. Open the diary.` : undefined}
        {...dock.handleProps}
      >
        <BookOpen size={16} aria-hidden style={{ flex: "0 0 auto", color: t.colour }} />
        {rail ? (
          <>
            <span className="grail-diary-rail-label" aria-hidden>
              Grail
            </span>
            <span className="grail-diary-rail-num" style={{ color: t.colour }} aria-hidden title={`${fails} failing project checks`}>
              {fails} fail
            </span>
            <span className="grail-diary-rail-num" aria-hidden title={`${warns} stale project checks`}>
              {warns} stale
            </span>
            <span
              className="grail-diary-rail-age"
              aria-hidden
              style={gs.snapshot_is_stale ? { color: "var(--warn)" } : undefined}
            >
              {shortAge(gs.age_s)}
            </span>
            <ChevronDown size={14} aria-hidden />
          </>
        ) : (
          <>
            <span className="grail-diary-strip-name" style={{ fontWeight: 600, whiteSpace: "nowrap" }}>
              Grail
            </span>
            {
}
            <span className="grail-diary-context" data-grail-context={ctx.scroll ?? "project"}>
              {contextLine}
            </span>
            {
}
            <span
              className="grail-diary-health"
              data-stale={gs.snapshot_is_stale ? "true" : undefined}
              title={err ? `Project snapshot unreadable: ${err}` : `Project snapshot ${t.label}: ${fails} failing, ${warns} stale. This is repository diagnostics, not the selected scroll route. See Diagnostics.`}
            >
              {ctx.scroll
                ? "selected scroll brief · project health is under Diagnostics"
                : err ? "project snapshot unreadable" : `project snapshot · ${fails} failing · ${warns} stale · ${age(gs.age_s)}`}
            </span>
            <span className="grail-diary-strip-spacer" style={{ flex: 1 }} />
            {collapsed ? (
              <ChevronDown size={16} aria-hidden />
            ) : (
              <ChevronUp size={16} aria-hidden />
            )}
          </>
        )}
      </button>

      {
}
      {dock.preview ? (
        <div className="grail-snap-preview" data-preview={dock.preview} aria-hidden />
      ) : null}

      {
}
      <div className="grail-diary-dockbar">
        <button
          type="button"
          ref={chipRef}
          data-control="diary.dockmenu"
          className="interactive grail-diary-chip"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((v) => !v)}
          title="Choose where the diary sits"
          aria-label={`Where the diary sits: ${
            dock.narrow ? dock.layout.sheet : dock.layout.zone
          }. Choose another.`}
        >
          <LayoutGrid size={13} aria-hidden />
          {
}
          <span className="grail-diary-chip-label">
            {dock.narrow ? dock.layout.sheet : dock.layout.zone}
          </span>
        </button>
        {menuOpen && chipRef.current
          ? createPortal(
              <div
                className="grail-diary-menu"
                role="menu"
                ref={menuRef}
                style={{
                  position: "fixed",
                  zIndex: 2147483000,
                  ...(flip.up
                    ? { bottom: window.innerHeight - chipRef.current.getBoundingClientRect().top + 4 }
                    : { top: chipRef.current.getBoundingClientRect().bottom + 4 }),
                  ...(flip.left
                    ? { left: chipRef.current.getBoundingClientRect().left }
                    : { right: window.innerWidth - chipRef.current.getBoundingClientRect().right }),
                }}
                data-flip-up={flip.up ? "true" : undefined}
                data-flip-left={flip.left ? "true" : undefined}
                aria-label="Where the diary sits"
                onKeyDown={onMenuKeyDown}
              >
                {(dock.narrow ? SHEET_STOPS : ZONES).map((z) => (
                  <button
                    key={z}
                    type="button"
                    role="menuitemradio"
                    aria-checked={
                      dock.narrow ? dock.layout.sheet === z : dock.layout.zone === z
                    }
                    data-control={`diary.zone.${z}`}
                    className="interactive grail-diary-menuitem"
                    onClick={() => {
                      if (dock.narrow) {
                        dock.setSheet(z as SheetStop);
                        dock.setCollapsed(z === "COLLAPSED");
                      } else {
                        dock.dockTo(z as Zone);
                      }
                      setMenuOpen(false);
                      chipRef.current?.focus();
                    }}
                  >
                    {z}
                  </button>
                ))}
                <button
                  type="button"
                  role="menuitem"
                  data-control="diary.reset"
                  className="interactive grail-diary-menuitem"
                  onClick={() => {
                    dock.resetLayout();
                    setMenuOpen(false);
                    chipRef.current?.focus();
                  }}
                >
                  <RotateCcw size={12} aria-hidden /> Reset layout
                </button>
              </div>,
              document.body,
            )
          : null}
      </div>

      {
}
      {(dock.sideDocked || dock.bottomDocked) && !collapsed ? (
        <div
          className="grail-diary-resize"
          data-control="diary.resize"
          data-axis={dock.bottomDocked ? "y" : "x"}
          role="separator"
          aria-orientation={dock.bottomDocked ? "horizontal" : "vertical"}
          aria-label="Resize the diary. Arrow keys resize; Shift for larger steps; Home and End for the limits."
          aria-valuenow={dock.layout.size}
          aria-valuemin={dock.sizeMin}
          aria-valuemax={dock.sizeMax}
          {...dock.resizeProps}
        />
      ) : null}

      {
}
      {collapsed ? null : (
        <div ref={pageRef} className="grail-diary-page">
          <ScrollStatusBar control="diary.scroll.status" />
          <GrailNavigator
            universe={universe}
            scroll={ctx.scroll}
            runs={runs}
            activeRuns={activeRuns}
            tab={nav.tab}
            onTab={setTab}
            pinned={nav.pinned}
            onPin={setPinned}
            notesSlot={noteSlot}
            diagnostics={
              <div style={{ display: "grid", gap: 10 }}>
                {err ? (
                  <div className="meta" style={{ color: "var(--warn)" }}>
                    Grail snapshot unreadable: {err}. This is the panel failing to read, not a
                    statement about the project.
                  </div>
                ) : !g ? (
                  <div className="meta">Reading the Grail snapshot…</div>
                ) : !g.present ? (
                  <div className="meta">
                    {g.why}. Build it with <code className="mono">{g.how}</code>.
                  </div>
                ) : (
                  <>
                    <div className="meta" style={{ color: "var(--ink-dim)" }}>
                      Snapshot {t.label}, {age(g.age_s)}. {g.counts?.with_ledger_entry} of{" "}
                      {g.counts?.findings} findings carry a ledger entry. The rest exist in the
                      notebook only and are not independently checkable.
                    </div>
                    {fails > 0 ? (
                      <div>
                        <div style={{ fontWeight: 600, color: "var(--warn)" }}>Failing surfaces ({fails})</div>
                        <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                          {(g.failures ?? []).map((f) => (
                            <li key={f.path} className="meta">
                              <code className="mono">{f.path}</code> — {f.state}
                              {noteSlot(f.path ?? "")}
                            </li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                    {warns > 0 ? (
                      <div>
                        <div style={{ fontWeight: 600 }}>Stale surfaces ({warns})</div>
                        <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                          {(g.warnings ?? []).map((w) => (
                            <li key={w.path} className="meta">
                              <code className="mono">{w.path}</code> — {w.state}
                              {typeof w.finding_lag === "number" ? `, ${w.finding_lag} findings behind` : ""}
                              {noteSlot(w.path ?? "")}
                            </li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                    <Evidence ev={ev} />
                    {
}
                    <div className="meta" style={{ color: "var(--ink-dim)" }}>
                      This panel reads the project. To refresh it:{" "}
                      <code className="mono">{g.rebuild_with}</code>
                    </div>
                  </>
                )}
              </div>
            }
          />
        </div>
      )}
    </aside>
  );
}
