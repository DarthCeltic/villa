import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle, BookMarked, Copy, ExternalLink, FileSearch, FlaskConical, Image as ImageIcon,
  Layers, Pin, PinOff, Search, ShieldAlert, StickyNote, Wrench,
} from "lucide-react";
import type { RunRecord } from "../../api";
import { api } from "../../api";
import { PrizeRouteBoard } from "../PrizeRouteBoard";
import { scrollBlocker, scrollGate, STAGE_ORDER, STAGE_WORD, type ScrollObject, type Universe } from "../ShelfUniverse";
import { usePublicDemo } from "../../lib/publicDemo";
import { useArgusContext } from "../../lib/context";
import { ScrollContextPicker } from "../ScrollContextPicker";
import { ScrollFactsList, scrollContradictions } from "../ScrollFacts";
import { ScrollPipelineNarrative } from "../ScrollPipelineNarrative";
import { tierOf, TIER_WORD, type EvidenceTier } from "../../lib/unrollScroll";
import {
  useCommunity, useFindings, useProjectSources, useScrollMetadata, useStacks,
  useScrollStatus,
  type ScrollMetadataEvidence, type SourceState, type Stack,
} from "./useGrailData";
import type { ScrollStatus } from "../../lib/scrollStatus";
import { SurfaceStatusChips, SurfaceStatusDetail, useSurfaceStatus } from "../SurfaceStatusFacts";
import "../../theme/grail.css";

export type GrailTab = "brief" | "evidence" | "findings" | "notes" | "diagnostics";

const TABS: { key: GrailTab; label: string; icon: typeof Layers }[] = [
  { key: "brief", label: "Brief", icon: BookMarked },
  { key: "evidence", label: "Evidence", icon: ImageIcon },
  { key: "findings", label: "Findings", icon: FileSearch },
  { key: "notes", label: "Notes", icon: StickyNote },
  { key: "diagnostics", label: "Diagnostics", icon: Wrench },
];

function Pending<T>({ s, children, what }: {
  s: SourceState<T>; what: string; children: (d: T) => React.ReactNode;
}) {
  if (s.state === "LOADING") return <p className="gn-muted">Reading {what}…</p>;
  if (s.state === "FAILED") {
    return (
      <p className="gn-fail" role="status">
        <AlertTriangle size={16} aria-hidden="true" /> Could not read {what}: {s.why}. This is the
        panel failing to read, not a statement that there is nothing.
      </p>
    );
  }
  return <>{children(s.data)}</>;
}

function copy(text: string) {
  try {
    void navigator.clipboard?.writeText(text);
  } catch {
  }
}

function TierChip({ tier }: { tier: EvidenceTier }) {
  return (
    <span className="gn-tier" data-tier={tier}>
      <span className="gn-tier-glyph" aria-hidden="true">
        {tier === "CONTROL" ? "◎" : tier === "EXPLORATORY" ? "◇" : tier === "DEVELOPMENT" ? "△"
          : tier === "ADMISSIBLE" ? "✓" : "?"}
      </span>
      {TIER_WORD[tier]}
    </span>
  );
}


function StackTiles({ stacks, filter }: { stacks: Stack[]; filter: EvidenceTier | "ALL" }) {
  const demo = usePublicDemo();
  const tiles = stacks.flatMap((st) => {
    const tier = tierOf(st.detail.result_class);
    if (filter !== "ALL" && tier !== filter) return [];
    return Object.entries(st.detail.levels ?? {}).flatMap(([level, lv]) =>
      Object.entries(lv.images ?? {})
        .filter(([layer]) => !(demo && /ink|prob|pred|detector|candidate/i.test(layer)))
        .map(([layer, img]) => ({ st, tier, level, lv, layer, img })));
  });
  if (!tiles.length) {
    return <p className="gn-muted">No receipt-backed image matches this filter.</p>;
  }
  return (
    <ul className="gn-tiles">
      {tiles.map(({ st, tier, level, lv, layer, img }) => {
        const rc = st.detail.result_class ?? {};
        const isDetector = /ink|prob|pred|detector/i.test(layer);
        return (
          <li key={`${st.key}:${level}:${layer}`} className="gn-tile" data-tier={tier}>
            <a href={api.fileUrl(img.path)} target="_blank" rel="noreferrer"
               className="gn-tile-img" data-control={`grail.evidence.open.${st.key}.${layer}`}
               aria-label={`Open source image ${layer} from ${st.name}`}>
              <img src={api.fileUrl(img.path)} alt={`${st.name}: ${layer} at ${level}. ${lv.readable}.`}
                   loading="lazy" decoding="async" />
            </a>
            <div className="gn-tile-meta">
              <div className="gn-tile-row">
                <TierChip tier={tier} />
                <span className="gn-tile-layer">{layer}</span>
              </div>
              {isDetector && (
                <p className="gn-tile-warn">
                  Detector output. {rc.detector_cross_scroll_qualified ? "" : "The detector is not cross-scroll qualified, so this is not a reading."}
                </p>
              )}
              <p className="gn-tile-banner">{rc.banner ?? "No result-class banner was exported with this stack."}</p>
              <dl className="gn-kv">
                <dt>Source</dt><dd>{st.name} · {level} · {lv.effective_pitch_um} µm</dd>
                <dt>Class</dt><dd>{rc.target_class ?? "not declared"} · {rc.presentation ?? "not declared"}</dd>
                <dt>Hash</dt><dd className="gn-mono">{img.sha256 ? img.sha256.slice(0, 16) : "not recorded"}</dd>
                <dt>Observed</dt><dd>not recorded in the exported stack</dd>
              </dl>
              <button type="button" className="gn-link-btn"
                      data-control={`grail.evidence.copy.${st.key}.${layer}`}
                      onClick={() => copy(`${img.path}  sha256:${img.sha256 ?? "not-recorded"}`)}>
                <Copy size={15} aria-hidden="true" /> Copy path and hash
              </button>
            </div>
          </li>
        );
      })}
    </ul>
  );
}


function formatScrollMetadataEvidence(e?: ScrollMetadataEvidence): string {
  if (!e) return "no evidence recorded";
  if (e.authority) return `authority: ${e.authority}`;
  const parts: string[] = [];
  if (e.source_authority) parts.push(`source: ${e.source_authority}`);
  if (e.operational_verification) parts.push(`verification: ${e.operational_verification}`);
  return parts.length ? parts.join(", ") : "no evidence recorded";
}

function LiveTruthCard({ status, scroll }: {
  status: SourceState<ScrollStatus>; scroll: string;
}) {
  return (
    <section className="gn-sec" aria-label="Live ARGUS truth">
      <h3><ShieldAlert size={17} aria-hidden="true" /> Live ARGUS truth</h3>
      {status.state === "LOADING" ? <p className="gn-muted">Reading the selected scroll's canonical route…</p> : null}
      {status.state === "FAILED" ? <p className="gn-fail">Could not read the canonical route: {status.why}</p> : null}
      {status.state === "OK" ? (
        <>
          <p className="gn-muted">This is the same 16-step status used by Workbench and System. The project snapshot below is a different clock.</p>
          <ol className="gn-steps" data-control={`grail.live-route.${scroll}`}>
            {status.data.steps.map((step) => {
              const current = status.data.next_action.step === step.id;
              const state = step.state === "DONE" ? "have" : step.state === "BLOCKED" || step.state === "HUMAN_GATED" ? "blocked" : current ? "next" : "missing";
              return <li key={step.id} data-step={state} title={step.why}>
                <span className="gn-step-mark" aria-hidden="true">{state === "have" ? "●" : state === "blocked" ? "■" : state === "next" ? "◐" : "○"}</span>
                <span className="gn-step-word">{step.label}</span>
                <span className="gn-step-state">{step.state.toLowerCase().replaceAll("_", " ")}</span>
                {step.to && step.action_label ? <Link className="gn-step-action" to={step.to} data-control={`grail.step.${step.id}.action`}>{step.action_label}</Link> : null}
              </li>;
            })}
          </ol>
          {status.data.contradictions.length ? (
            <ul className="gn-contradictions">
              {status.data.contradictions.map((text, i) => <li key={i}>{text}</li>)}
            </ul>
          ) : null}
        </>
      ) : null}
    </section>
  );
}

function ScrollBrief({ s, universe, stacks, runs, notesSlot }: {
  s: ScrollObject; universe: Universe; stacks: SourceState<Stack[]>; runs: RunRecord[];
  notesSlot: (target: string) => React.ReactNode;
}) {
  const findings = useFindings(s.id, s.aliases, "", null);
  const liveStatus = useScrollStatus(s.id);
  const metadata = useScrollMetadata(s.id);
  const ix = STAGE_ORDER.indexOf(s.surface.stage);
  const declaredAcquisitions = s.acquisitions.filter((item) => item.scanId);
  const acq = declaredAcquisitions.length === 1 ? declaredAcquisitions[0] : undefined;
  const demo = usePublicDemo();
  const g = scrollGate(s);
  const gate = { operatorOnly: g.operatorOnly, why: demo ? g.publicWhy : g.why };
  const blocker = scrollBlocker(s, demo);
  const mine = stacks.state === "OK" ? stacks.data.filter((x) => x.scroll === s.id) : [];
  const exportedLevels = mine.flatMap((stack) =>
    Object.entries(stack.detail.levels ?? {}),
  );
  const level = exportedLevels.length === 1 ? exportedLevels[0] : undefined;
  const contradictions = scrollContradictions(s);
  const { status: surfaceStatus } = useSurfaceStatus(s.id);

  return (
    <div className="gn-brief">
      <section className="gn-sec" aria-label="Identity">
        <h3><BookMarked size={17} aria-hidden="true" /> Identity</h3>
        <p className="gn-ident">{s.display}</p>
        <div className="gn-chips">
          {s.firstLetters && <span className="gn-chip">First Letters target</span>}
          {s.grandPrize && <span className="gn-chip">Grand Prize target</span>}
          {s.titleLane && <span className="gn-chip">Paris 4 title lane</span>}
          {s.lane === "CONTROL_OR_DEV" && <span className="gn-chip" data-kind="control">control / development — not prize-eligible</span>}
          {s.lane === "UNCLASSIFIED" && <span className="gn-chip">prize eligibility unknown</span>}
          {s.fixture.fixture && <span className="gn-chip" data-kind="fixture">fixture</span>}
        </div>
        {surfaceStatus ? (
          <div style={{ marginTop: 8 }}>
            <SurfaceStatusChips status={surfaceStatus} size="sm" />
          </div>
        ) : null}
        <dl className="gn-kv">
          <dt>Aliases</dt><dd>{s.aliases.length ? s.aliases.join(", ") : "none declared"}</dd>
          <dt>Acquisition</dt><dd className="gn-mono">{acq?.scanId ?? "not declared"}{acq?.volumeStore ? ` · ${acq.volumeStore}` : ""}</dd>
          <dt>Pitch · energy</dt><dd>{s.familyLine}</dd>
          <dt>Coordinate level</dt>
          <dd>{level ? `${level[0]} · ${level[1].effective_pitch_um} µm effective · ${level[1].readable}` : "no exported layer stack, so no coordinate level is asserted"}</dd>
        </dl>
      </section>

      <ScrollPipelineNarrative scroll={s} universe={universe} runs={runs} />

      <LiveTruthCard status={liveStatus} scroll={s.id} />

      <section className="gn-sec" aria-label="Evidence for this scroll">
        <h3><ImageIcon size={17} aria-hidden="true" /> Evidence</h3>
        <Pending s={stacks} what="the exported layer stacks">
          {() => mine.length
            ? <StackTiles stacks={mine} filter="ALL" />
            : <p className="gn-muted">No receipt-backed image exists for {s.display}. Nothing is borrowed from another scroll to fill this space.</p>}
        </Pending>
      </section>

      {
}
      <section className="gn-sec" aria-label="Known facts">
        <h3><BookMarked size={17} aria-hidden="true" /> Known facts</h3>
        <ScrollFactsList s={s} universe={universe} />
      </section>

      {surfaceStatus ? (
        <section className="gn-sec" aria-label="Status, as independent facts">
          <h3><BookMarked size={17} aria-hidden="true" /> Status — every fact on its own, sourced</h3>
          <SurfaceStatusDetail status={surfaceStatus} />
        </section>
      ) : null}

      {
}
      <section className="gn-sec" aria-label="Physical facts">
        <h3><BookMarked size={17} aria-hidden="true" /> Physical facts</h3>
        {metadata.state === "LOADING" && (
          <p className="gn-muted">Reading the scroll's physical-fact record…</p>
        )}
        {metadata.state === "NO_RECORD" && (
          <p className="gn-muted">
            No ScrollDatasetMetadata record has been saved for {s.display}. This is not the same
            as zero -- nothing here substitutes a default.
          </p>
        )}
        {metadata.state === "FAILED" && (
          <p className="gn-muted">Could not read the physical-fact record: {metadata.why}</p>
        )}
        {metadata.state === "OK" && (
          <dl className="gn-kv">
            <dt>Winding count</dt>
            <dd>
              {metadata.data.winding_count
                ? `${metadata.data.winding_count.count} (${metadata.data.winding_count.derivation_method || "method not recorded"})`
                : "not recorded"}
            </dd>
            <dt>Winding-count evidence</dt>
            <dd>{formatScrollMetadataEvidence(metadata.data.winding_count?.evidence)}</dd>
            <dt>Record integrity</dt>
            <dd>
              {metadata.data.validation_problems.length
                ? `refuses to serve -- ${metadata.data.validation_problems.join("; ")}`
                : `clean (sha256 ${metadata.data.record_sha256.slice(0, 12)}…)`}
            </dd>
          </dl>
        )}
      </section>

      <section className="gn-sec" aria-label="Contradictions">
        <h3><AlertTriangle size={17} aria-hidden="true" /> Contradictions</h3>
        {contradictions.length ? (
          <ul className="gn-contradictions" data-control={`grail.brief.contradictions.${s.id}`}>
            {contradictions.map((c, i) => <li key={i}>{c}</li>)}
          </ul>
        ) : (
          <p className="gn-muted" data-control={`grail.brief.contradictions.${s.id}`}>
            None recorded: the routes that were read agree about this scroll. That is not a
            certificate that no contradiction exists.
          </p>
        )}
      </section>

      <section className="gn-sec" aria-label="What ARGUS has">
        <h3><Layers size={17} aria-hidden="true" /> What ARGUS has</h3>
        <ul className="gn-have">
          <li data-have={s.local.state === "MATERIAL_INDEXED"}><span>Local CT</span><span>{s.local.line}</span></li>
          <li data-have={s.publishedUpstream ? "upstream" : "false"}><span>Published upstream CT</span>
            <span>{s.publishedUpstream ? "published upstream — which is not the same as held here" : "not published upstream"}</span></li>
          <li data-have={ix >= STAGE_ORDER.indexOf("GEOMETRY")}><span>Surface geometry</span><span>{s.surface.line}</span></li>
          <li data-have={ix >= STAGE_ORDER.indexOf("RENDERED")}><span>Rendered surface</span>
            <span>{ix >= STAGE_ORDER.indexOf("RENDERED") ? "rendered" : "not rendered"}</span></li>
          <li data-have={s.labels.present}><span>Labels</span>
            <span>{s.labels.line}{s.labels.authority ? ` · authority ${s.labels.authority}` : ""}</span></li>
          <li data-have={s.work.results > 0}><span>Model results</span>
            <span>{s.work.results > 0 ? `${s.work.results} result(s); highest certified tier: ${s.work.highest ?? "none"}` : "none recorded"}</span></li>
          <li data-have={s.work.runIds.length > 0}><span>Receipts</span>
            <span>{s.work.runIds.length ? `${s.work.runIds.length} run record(s) declare this scroll` : "no run record declares this scroll"}</span></li>
        </ul>
      </section>

      <section className={`gn-sec ${liveStatus.state === "OK" && liveStatus.data.status === "OK" ? "gn-legacy-path" : ""}`} aria-label="Shelf snapshot (legacy)">
        <h3><FlaskConical size={17} aria-hidden="true" /> Shelf snapshot (legacy)</h3>
        <p className="gn-muted">The canonical live route is above. This older shelf summary is retained for diagnostics only and is not the current step count.</p>
        <ol className="gn-steps">
          {STAGE_ORDER.filter((st) => st !== "NOTHING").map((st) => {
            const six = STAGE_ORDER.indexOf(st);
            const state = six <= ix ? "have" : six === ix + 1 ? (s.blockedBy.length ? "blocked" : "next") : "missing";
            return (
              <li key={st} data-step={state}>
                <span className="gn-step-mark" aria-hidden="true">
                  {state === "have" ? "●" : state === "blocked" ? "■" : state === "next" ? "◐" : "○"}
                </span>
                <span className="gn-step-word">{STAGE_WORD[st]}</span>
                <span className="gn-step-state">{state}</span>
              </li>
            );
          })}
        </ol>
        <p className="gn-blocked" data-control={`grail.brief.blocker.${s.id}`}>
          <ShieldAlert size={16} aria-hidden="true" /> Blocked by: {blocker.full}
        </p>
        <div className="gn-next" data-operator={gate.operatorOnly}>
          <p className="gn-next-label">Smallest safe next action</p>
          <Link to={s.next.to} className="gn-next-action" data-control={`grail.brief.next.${s.id}`}>{s.next.label}</Link>
          <p className="gn-next-why">{s.next.why}</p>
          <p className="gn-next-gate">
            <strong>{gate.operatorOnly ? "Operator only." : "Automatic, through the governed runner."}</strong> {gate.why}
          </p>
          <p className="gn-muted">Measured cost: not measured for this action.</p>
        </div>
      </section>

      {(s.firstLetters || s.grandPrize || s.titleLane) ? (
        <section className="gn-sec" aria-label="Prize routes for this scroll">
          <h3>Prize routes</h3>
          <PrizeRouteBoard
            scroll={s.id}
            compact
            lanes={[
              ...(s.firstLetters ? ["FIRST_LETTERS" as const] : []),
              ...(s.grandPrize ? ["GRAND_PRIZE" as const] : []),
              ...(s.titleLane ? ["PARIS4_TITLE" as const] : []),
            ]}
          />
        </section>
      ) : null}

      <section className="gn-sec" aria-label="Findings for this scroll">
        <h3><FileSearch size={17} aria-hidden="true" /> Findings</h3>
        <Pending s={findings} what="the findings corpus">
          {(f) => !f.present
            ? <p className="gn-fail">{f.why}</p>
            : (
              <>
                <p className="gn-muted">{f.total_matching_kinds ?? 0} finding(s) mention {s.display}. {f.relation_means}</p>
                <div className="gn-chips">
                  {Object.entries(f.counts_by_kind ?? {}).map(([k, n]) => (
                    <span key={k} className="gn-chip" data-kind={k.toLowerCase()}>{k.toLowerCase()} {n}</span>
                  ))}
                </div>
                <ul className="gn-findings">
                  {(f.rows ?? []).slice(0, 4).map((r) => (
                    <li key={r.id}><span className="gn-mono">{r.id}</span> <span className="gn-kind" data-kind={r.kind.toLowerCase()}>{r.kind.toLowerCase()}</span> {r.title}</li>
                  ))}
                </ul>
              </>
            )}
        </Pending>
      </section>

      {demo ? null : (
        <section className="gn-sec gn-notes" aria-label="Operator notes for this scroll">
          <h3><StickyNote size={17} aria-hidden="true" /> Operator notes</h3>
          <p className="gn-muted">Your notes. Stored outside every evidence tree, never read by any derivation, and not findings.</p>
          {notesSlot(s.id)}
        </section>
      )}
    </div>
  );
}

function ProjectBrief({ universe, activeRuns, stacks }: {
  universe: Universe; activeRuns: RunRecord[]; stacks: SourceState<Stack[]>;
}) {
  const { established, blockers } = useProjectSources();
  const c = universe.counts;
  return (
    <div className="gn-brief">
      <section className="gn-sec gn-headline" aria-label="Project headline">
        <h3>ARGUS today</h3>
        <p className="gn-head">What the project records as open.</p>
        <Pending s={established} what="what is established">
          {(e) => (
            <p className="gn-muted">
              Still open: {Array.isArray(e.what_is_actually_open) ? e.what_is_actually_open.join("; ") : e.what_is_actually_open ?? "not recorded"}.
            </p>
          )}
        </Pending>
      </section>

      <section className="gn-sec" aria-label="Active work">
        <h3>Today's active work</h3>
        {activeRuns.length
          ? <ul className="gn-list">{activeRuns.slice(0, 5).map((r) => (
              <li key={r.run_id}><span className="gn-live" aria-hidden="true">◆</span> <span className="gn-mono">{r.run_id}</span>{r.target ? ` · ${r.target}` : ""}</li>))}</ul>
          : <p className="gn-muted">Nothing is running now.</p>}
      </section>

      <section className="gn-sec" aria-label="Current blockers">
        <h3>Current blockers</h3>
        <Pending s={blockers} what="the blockers">
          {(b) => (b.items?.length
            ? <ul className="gn-list">{b.items.map((it) => (
                <li key={it.id}><span className="gn-kind" data-kind="blocked">{it.state.toLowerCase()}</span> {it.summary ?? it.label}</li>))}</ul>
            : <p className="gn-muted">No blocker receipt is recorded.</p>)}
        </Pending>
      </section>

      <section className="gn-sec" aria-label="Established">
        <h3>Recently verified, with caveats</h3>
        <Pending s={established} what="what is established">
          {(e) => (
            <ul className="gn-list">
              {(e.evidence ?? []).slice(0, 4).map((x, i) => (
                <li key={i}><strong>{x.claim}</strong> — {x.value}. <span className="gn-caveat">Caveat: {x.caveat}</span> <span className="gn-mono">{x.receipt}</span></li>
              ))}
            </ul>
          )}
        </Pending>
      </section>

      <section className="gn-sec" aria-label="Prize routes">
        <h3>Prize routes</h3>
        <dl className="gn-kv">
          <dt>First Letters</dt><dd>{c.firstLetters.value} targets · {c.firstLettersWithLocalMaterial.value} with material indexed here</dd>
          <dt>Grand Prize</dt><dd>{c.grandPrize.value} targets, overlapping First Letters</dd>
          <dt>Controls</dt><dd>{c.controlsAndDev.value} development and control scrolls, never prize-eligible</dd>
        </dl>
        <PrizeRouteBoard compact />
      </section>

      <section className="gn-sec" aria-label="Latest receipt-backed images">
        <h3>Latest receipt-backed images</h3>
        <Pending s={stacks} what="the exported layer stacks">
          {(st) => st.length ? <StackTiles stacks={st} filter="ALL" /> : <p className="gn-muted">No exported layer stack is on this machine.</p>}
        </Pending>
      </section>

      <section className="gn-sec" aria-label="Decisions owed">
        <h3>Unresolved operator decisions</h3>
        <p className="gn-muted">Decisions that only the operator can make are held in Review, with their evidence and consequences.</p>
        <Link to="/review" className="gn-next-action" data-control="grail.project.review">Open Review</Link>
      </section>
    </div>
  );
}


function FindingsTab({ scroll }: { scroll: ScrollObject | null }) {
  const [q, setQ] = useState("");
  const [kind, setKind] = useState<string | null>(null);
  const f = useFindings(scroll?.id ?? null, scroll?.aliases ?? [], q, kind);
  const community = useCommunity(scroll?.id ?? null, scroll?.aliases ?? []);
  return (
    <div className="gn-brief">
      <div className="gn-search">
        <Search size={17} aria-hidden="true" />
        <input type="search" value={q} onChange={(e) => setQ(e.target.value)}
               placeholder={scroll ? `Search findings that mention ${scroll.display}` : "Search every finding"}
               aria-label="Search findings" data-control="grail.findings.search" />
      </div>
      <div className="gn-filters" role="group" aria-label="Finding kind">
        {[null, "CURRENT", "ERRATUM", "AMENDED", "SUPERSEDED"].map((k) => (
          <button key={k ?? "all"} type="button" aria-pressed={kind === k} className="gn-filter"
                  data-control={`grail.findings.kind.${(k ?? "all").toLowerCase()}`}
                  onClick={() => setKind(k)}>
            {k ? k.toLowerCase() : "all"}
          </button>
        ))}
      </div>
      <Pending s={f} what="the findings corpus">
        {(d) => !d.present ? <p className="gn-fail">{d.why}</p> : (
          <>
            <p className="gn-muted">{d.total_matching_kinds ?? 0} match. {d.relation_means}</p>
            <ul className="gn-findings gn-findings-full">
              {(d.rows ?? []).map((r) => (
                <li key={r.id}>
                  <div className="gn-finding-head">
                    <span className="gn-mono">{r.id}</span>
                    <span className="gn-kind" data-kind={r.kind.toLowerCase()}>{r.kind.toLowerCase()}</span>
                    <strong>{r.title}</strong>
                  </div>
                  <p className="gn-excerpt">{r.excerpt}</p>
                  {(r.retracts.length > 0 || r.amends.length > 0) && (
                    <p className="gn-muted">{r.retracts.length ? `Retracts ${r.retracts.join(", ")}. ` : ""}{r.amends.length ? `Amends ${r.amends.join(", ")}.` : ""}</p>
                  )}
                  <button type="button" className="gn-link-btn" data-control={`grail.findings.copy.${r.id}`}
                          onClick={() => copy(r.id)}>
                    <Copy size={15} aria-hidden="true" /> Copy finding id
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
      </Pending>

      <section className="gn-sec gn-community" aria-label="Community reports">
        <h3>Community reports — pending reproduction</h3>
        <p className="gn-muted">Reported by others and not reproduced here. A separate list: never findings.</p>
        <Pending s={community} what="the community register">
          {(c) => c.count ? (
            <ul className="gn-list">{c.claims.map((x) => (
              <li key={x.key}>
                <span className="gn-kind" data-kind="community">pending reproduction</span>{" "}
                {x.claim}{" "}
                <Link
                  className="gn-link-btn"
                  data-control={`grail.community.open.${x.key}`}
                  to={`/workbench?${new URLSearchParams({
                    ...(scroll ? { scroll: scroll.id } : {}),
                    intel: x.key,
                  }).toString()}`}
                >
                  Open this intelligence in the Bench
                </Link>
              </li>))}</ul>
          ) : <p className="gn-muted">No community report {scroll ? `mentions ${scroll.display}` : "is registered"}.</p>}
        </Pending>
      </section>
    </div>
  );
}


export interface GrailNavigatorProps {
  universe: Universe;
  scroll: string | null;
  runs: RunRecord[];
  activeRuns: RunRecord[];
  tab: GrailTab;
  onTab: (t: GrailTab) => void;
  pinned: boolean;
  onPin: (p: boolean) => void;
  notesSlot: (target: string) => React.ReactNode;
  diagnostics: React.ReactNode;
}

export function GrailNavigator({
  universe, scroll, runs, activeRuns, tab, onTab, pinned, onPin, notesSlot, diagnostics,
}: GrailNavigatorProps) {
  const demo = usePublicDemo();
  const { set } = useArgusContext();
  const stacks = useStacks();
  const s = scroll ? universe.byId.get(scroll) ?? null : null;
  const [tierFilter, setTierFilter] = useState<EvidenceTier | "ALL">("ALL");
  const scoped = useMemo<SourceState<Stack[]>>(() => {
    if (stacks.state !== "OK" || !s) return stacks;
    return { state: "OK", data: stacks.data.filter((x) => x.scroll === s.id) };
  }, [stacks, s]);

  return (
    <div className="gn">
      <div className="gn-top">
        <p className="gn-context" aria-live="polite">
          {s ? <>Brief for <strong>{s.display}</strong></> : <>Project brief — no scroll chosen</>}
        </p>
        <ScrollContextPicker
          control="grail.scrollpicker"
          buttonClassName="gn-link-btn"
          buttonLabel={s ? "Change scroll" : "Choose a scroll"}
        />
        {s ? (
          <button
            type="button"
            className="gn-link-btn"
            data-control="grail.scrollpicker.clear"
            onClick={() => set({ scroll: null })}
            title="Clear the selected scroll and show the project-wide brief"
          >
            Project brief
          </button>
        ) : null}
        <button type="button" className="gn-pin" aria-pressed={pinned}
                data-control="grail.pin" onClick={() => onPin(!pinned)}
                title={pinned ? "Unpin: the diary may be collapsed again" : "Pin open: keep the diary open on every page"}>
          {pinned ? <Pin size={16} aria-hidden="true" /> : <PinOff size={16} aria-hidden="true" />}
          {pinned ? "Pinned open" : "Pin open"}
        </button>
      </div>

      <div className="gn-tabs" role="tablist" aria-label="Grail sections">
        {TABS.filter((t) => !(demo && t.key === "notes")).map((t) => (
          <button key={t.key} type="button" role="tab" aria-selected={tab === t.key}
                  className="gn-tab" data-control={`grail.tab.${t.key}`} onClick={() => onTab(t.key)}>
            <t.icon size={16} aria-hidden="true" /> {t.label}
          </button>
        ))}
      </div>

      <div className="gn-body" role="tabpanel">
        {tab === "brief" && (s
          ? <ScrollBrief s={s} universe={universe} stacks={stacks} runs={runs} notesSlot={notesSlot} />
          : <ProjectBrief universe={universe} activeRuns={activeRuns} stacks={stacks} />)}

        {tab === "evidence" && (
          <div className="gn-brief">
            <div className="gn-filters" role="group" aria-label="Evidence tier">
              {(["ALL", "CONTROL", "DEVELOPMENT", "EXPLORATORY", "ADMISSIBLE"] as const).map((t) => (
                <button key={t} type="button" aria-pressed={tierFilter === t} className="gn-filter"
                        data-control={`grail.evidence.tier.${t.toLowerCase()}`} onClick={() => setTierFilter(t)}>
                  {t === "ALL" ? "all" : TIER_WORD[t]}
                </button>
              ))}
            </div>
            <p className="gn-muted">
              {s ? `Images whose layer stack belongs to ${s.display}. Nothing is borrowed from another scroll.`
                 : "Every receipt-backed image on this machine."} Each carries its result class; a render is never a pass by being shown.
            </p>
            <Pending s={scoped} what="the exported layer stacks">
              {(st) => st.length ? <StackTiles stacks={st} filter={tierFilter} />
                : <p className="gn-muted">No receipt-backed image exists{s ? ` for ${s.display}` : ""}.</p>}
            </Pending>
          </div>
        )}

        {tab === "findings" && <FindingsTab scroll={s} />}

        {tab === "notes" && !demo && (
          <div className="gn-brief gn-notes">
            <p className="gn-muted">Operator notes are yours. They are stored outside the repository and every evidence tree, are never read by any derivation, and are never findings.</p>
            {s && (<section className="gn-sec"><h3>{s.display}</h3>{notesSlot(s.id)}</section>)}
            <section className="gn-sec"><h3>The project</h3>{notesSlot("project")}</section>
          </div>
        )}

        {tab === "diagnostics" && (
          <div className="gn-brief">
            <p className="gn-muted">
              Whether this diary's snapshot is current. For maintaining ARGUS, not for reading a scroll —
              kept inspectable here rather than leading the panel.
            </p>
            {diagnostics}
          </div>
        )}
      </div>

      <p className="gn-foot">
        <ExternalLink size={14} aria-hidden="true" /> Dock placement is a view preference and never changes a scientific status.
      </p>
    </div>
  );
}
