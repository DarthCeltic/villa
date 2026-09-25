import { AlertTriangle, Database, HardDrive, Layers, Package } from "lucide-react";
import { Chip, type Tone } from "../components/Status";
import {
  PROJECTION_MEANING,
  PROJECTION_TONE,
  gib,
  pitch,
  type AssetRow,
  type HoldingsView,
  type ProjectionState,
  type ScrollHoldings,
} from "../lib/holdings";
import { isKnown, unknownReason, type Triple } from "../lib/models";
import { useSharedUniverse } from "../lib/sharedUniverse";
import "../theme/workbench.css";

export function Holdings({ view, settled }: { view: HoldingsView; settled: boolean }) {
  const universe = useSharedUniverse();
  if (!settled)
    return (
      <div style={{ padding: 22 }}>
        <span className="muted">Asking the service for the asset catalogue…</span>
      </div>
    );
  if (!view.present) return <CatalogueMissing view={view} />;

  const usable = view.scrolls.filter((s) => s.constructible > 0);
  const total = view.scrolls.reduce((n, s) => n + s.constructible, 0);

  return (
    <div style={{ overflow: "auto", padding: "22px 22px 48px", display: "grid", gap: 18 }}>
      <section className="panel" style={{ padding: 18, display: "grid", gap: 12 }}>
        <div style={{ display: "flex", gap: 12, alignItems: "baseline", flexWrap: "wrap" }}>
          <h2 className="eyebrow">
            <Package size={13} aria-hidden /> Holdings
          </h2>
          <span className="meta">
            {view.assetCount} stores · {view.totalFiles.toLocaleString()} files ·{" "}
            {gib(view.totalBytes)}
            {view.utc ? ` · catalogued ${view.utc}` : ""}
          </span>
        </div>

        {
}
        <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
          <Chip tone={total ? "active" : "refused"}>
            {total} segment{total === 1 ? "" : "s"} constructible
          </Chip>
          <span className="small muted" style={{ maxWidth: 720 }}>
            A segment counts here only when ink labels, raw CT and a coordinate map exist for
            that <em>same</em> segment. Labels alone are not a pair, and a scroll with plenty
            of both in different segments still yields nothing to learn from.
          </span>
        </div>
        {usable.length ? (
          <div className="small">
            constructible on:{" "}
            {usable.map((s) => `${s.scroll} (${s.constructible})`).join(", ")}
          </div>
        ) : null}

        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {Object.entries(view.kindCounts)
            .sort((a, b) => b[1] - a[1])
            .map(([kind, n]) => (
              <span
                key={kind}
                className="small"
                style={{
                  padding: "2px 9px",
                  borderRadius: 999,
                  border: "1px solid var(--line-strong)",
                  color: "var(--ink-dim)",
                }}
              >
                {kind.replace(/_/g, " ")} <span className="mono">{n}</span>
              </span>
            ))}
        </div>
      </section>

      <ProjectionLegend />
      <ChunkCache view={view} />

      <section style={{ display: "grid", gap: 14 }}>
        <div style={{ display: "flex", gap: 12, alignItems: "baseline", flexWrap: "wrap" }}>
          <h2 className="eyebrow" data-control="holdings.shelfCount">
            <Layers size={13} aria-hidden /> {view.scrolls.length} scroll identities in the
            catalogue shelf
          </h2>
          <span className="meta">
            {view.scrolls.filter((x) => !x.nothingLocal).length} with material here ·{" "}
            {view.scrolls.filter((x) => x.nothingLocal).length} with nothing on this machine
            {view.canonicalKnown
              ? ` · canonical set ${view.canonicalCount}` +
                (view.identityFromEngine
                  ? " from argus.core.scroll_ids"
                  : " from the dataset registry — the identity route is not answering, so aliases and confusable pairs are unavailable")
              : " · the canonical set is not being served, so only scrolls with local material are listed"}
          </span>
        </div>
        {}
        <p className="small" style={{ margin: 0, maxWidth: 860 }} data-control="holdings.universeNote">
          This is the identity set this catalogue groups by, not the prize cohort.{" "}
          {universe.counts.firstLetters.value === null
            ? <>Prize eligibility is unknown here: the official target registry is not installed, so no First Letters or Grand Prize count is claimed, across {universe.counts.registered.value} known identities</>
            : <>The prize registry declares {universe.counts.firstLetters.value} First Letters targets and{" "}
              {universe.counts.grandPrize.value} Grand Prize targets — the second overlapping the
              first — across {universe.counts.registered.value} registered identities</>}
          {universe.missingFromCanonicalIds.length
            ? `, ${universe.missingFromCanonicalIds.length} of which are absent from the canonical identity list (${universe.missingFromCanonicalIds.join(", ")})`
            : ""}
          . Counts from {universe.counts.firstLetters.route} → {universe.counts.firstLetters.field}{" "}
          and {universe.counts.grandPrize.field}.
        </p>
        {
}
        {view.scrolls.map((s) => (
          <ScrollCard key={s.scroll} s={s} />
        ))}
      </section>

      {view.ungrouped.length ? (
        <section className="panel" style={{ padding: 18, display: "grid", gap: 10 }}>
          <h2 className="eyebrow">
            {view.ungrouped.length} stores the catalogue could not attach to a scroll
          </h2>
          <p className="small faint" style={{ margin: 0, maxWidth: 760 }}>
            Nothing in these stores declares a scroll and no canonical name appears in their
            path. They are listed rather than dropped: unattributed material is a
            fact about this machine, and a catalogue that quietly omits what it cannot
            classify reports a tidier world than the one on the disk.
          </p>
          <AssetTable assets={view.ungrouped} />
        </section>
      ) : null}

      <Coverage view={view} />
    </div>
  );
}

function CatalogueMissing({ view }: { view: HoldingsView }) {
  return (
    <div style={{ padding: 22 }}>
      <section
        className="panel"
        style={{
          padding: 20,
          display: "grid",
          gap: 10,
          borderColor: view.sealed
            ? "var(--status-active-edge)"
            : "var(--status-blocked-edge)",
        }}
      >
        <h2 className="eyebrow" style={{ color: "var(--ink)" }}>
          {view.sealed
            ? "the asset catalogue is under an active seal"
            : "the asset catalogue has not been written"}
        </h2>
        <p className="small muted" style={{ margin: 0, maxWidth: 760 }}>
          {view.sealed
            ? "The service is withholding the bytes because this receipt sits under an active blinding marker. That is the seal working, not a fault."
            : "The stage that scans the material roots and writes the catalogue has not run, so this room can say nothing about what is on this machine. It will not guess from directory names, which is the whole reason the catalogue exists."}
        </p>
        <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
          <Chip tone="blocked" size="sm">
            absent
          </Chip>
          <code className="mono">
            {view.relpath ?? "artifacts/registries/assets.json"}
          </code>
        </div>
      </section>
    </div>
  );
}

function ProjectionLegend() {
  const states: ProjectionState[] = [
    "RAW_CT_PAIR_CONSTRUCTIBLE",
    "LABELS_PRESENT_BUT_PROJECTION_UNAVAILABLE",
    "NO_LABELS",
  ];
  return (
    <section className="panel" style={{ padding: 18, display: "grid", gap: 10 }}>
      <h2 className="eyebrow">What a segment&rsquo;s state means</h2>
      <div
        style={{
          display: "grid",
          gap: 10,
          gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 320px), 1fr))",
        }}
      >
        {states.map((s) => (
          <div key={s} style={{ display: "grid", gap: 5 }}>
            <Chip tone={PROJECTION_TONE[s] as Tone} size="sm">
              {s.replace(/_/g, " ").toLowerCase()}
            </Chip>
            <span className="small faint">{PROJECTION_MEANING[s]}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

function ChunkCache({ view }: { view: HoldingsView }) {
  const c = view.chunkCache;
  if (!c) return null;
  const known = isKnown(c);
  const shape = known ? c.value.known_shape : null;
  return (
    <section className="panel" style={{ padding: 18, display: "grid", gap: 8 }}>
      <h2 className="eyebrow">
        <HardDrive size={13} aria-hidden /> Chunk cache
      </h2>
      {known ? (
        <>
          <div className="small">
            {shape
              ? `${shape.files.toLocaleString()} chunks · ${shape.total_gib} GiB`
              : "size not measured"}
          </div>
          {c.value.status ? (
            <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
              <Chip tone="active" size="sm">
                status
              </Chip>
              <span className="small muted">{c.value.status}</span>
            </div>
          ) : null}
          <span className="meta">
            {c.source}
            {c.evidence ? ` · ${c.evidence}` : ""}
          </span>
        </>
      ) : (
        <span className="small" style={{ color: "var(--status-blocked)" }}>
          not established — {unknownReason(c)}
        </span>
      )}
    </section>
  );
}

function ScrollCard({ s }: { s: ScrollHoldings }) {
  const tone: Tone = s.constructible
    ? "certified"
    : s.labelsWithoutProjection
      ? "blocked"
      : "refused";
  if (s.nothingLocal) return <AbsentScroll s={s} />;
  return (
    <article
      className="panel"
      style={{
        padding: 18,
        display: "grid",
        gap: 12,
        borderLeft: `3px solid var(--status-${tone})`,
      }}
    >
      <div style={{ display: "flex", gap: 12, alignItems: "baseline", flexWrap: "wrap" }}>
        <h3 className="display" style={{ fontSize: "var(--t-h2)" }}>
          {s.scroll}
        </h3>
        <Chip tone={tone} size="sm">
          {s.constructible
            ? `${s.constructible} constructible`
            : s.labelsWithoutProjection
              ? `${s.labelsWithoutProjection} labelled, none pairable`
              : "raw material only"}
        </Chip>
        <span className="meta">
          {s.declared.length + s.inferred.length} stores · {s.files.toLocaleString()} files ·{" "}
          {gib(s.bytes)}
        </span>
        {!s.inCanonicalSet ? (
          <Chip
            tone="blocked"
            size="sm"
            title="this grouping key is not in argus.core.scroll_ids.CANONICAL, so nothing routed through resolve() can address it"
          >
            id not canonical
          </Chip>
        ) : null}
        <Confusable s={s} />
      </div>

      {s.conflicts.length ? (
        <div style={{ display: "grid", gap: 4 }}>
          {s.conflicts.map((c, i) => (
            <div
              key={i}
              className="small"
              style={{
                color: "var(--status-blocked)",
                display: "flex",
                gap: 8,
                alignItems: "baseline",
              }}
            >
              <AlertTriangle size={13} aria-hidden />
              <span>
                pitch conflict on {c.kind.replace(/_/g, " ")}:{" "}
                {isKnown(c.t) ? renderValue(c.t.value) : unknownReason(c.t)}
              </span>
            </div>
          ))}
        </div>
      ) : null}

      {s.segments.length ? (
        <Segments s={s} />
      ) : (
        <span className="small faint">
          The catalogue records no per-segment breakdown for this scroll, so whether any
          example could be built from it is not established.
        </span>
      )}

      <Grouping s={s} />
    </article>
  );
}

function AbsentScroll({ s }: { s: ScrollHoldings }) {
  const up = s.upstream;
  const vols = Array.isArray(up?.volumes)
    ? up.volumes
    : isKnown(up?.volumes as Triple<{ name: string; voxel_um?: number }[]>)
      ? (up!.volumes as { value: { name: string; voxel_um?: number }[] }).value
      : [];
  const eligible =
    up && isKnown(up.eligibility) ? up.eligibility.value.first_letters_2027 : null;
  return (
    <article
      className="panel"
      style={{
        padding: "14px 18px",
        display: "flex",
        gap: 14,
        alignItems: "baseline",
        flexWrap: "wrap",
        borderLeft: "3px solid var(--line-strong)",
      }}
    >
      <h3 className="display" style={{ fontSize: "var(--t-h2)", opacity: 0.8 }}>
        {s.scroll}
      </h3>
      <Chip tone="blocked" size="sm">
        nothing on this machine
      </Chip>
      <Confusable s={s} />
      {eligible ? (
        <Chip tone="active" size="sm">
          First Letters eligible
        </Chip>
      ) : null}
      <span className="small muted">
        {vols.length
          ? `${vols.length} volume${vols.length === 1 ? "" : "s"} published upstream` +
            (vols[0]?.voxel_um ? `, best ${Math.min(...vols.map((v) => v.voxel_um ?? Infinity))} µm` : "")
          : up
            ? "the dataset registry lists no published volume for this scroll"
            : "not in the dataset registry, so what exists upstream is unknown here"}
      </span>
    </article>
  );
}

function Confusable({ s }: { s: ScrollHoldings }) {
  if (!s.confusableWith) return null;
  return (
    <Chip
      tone="blocked"
      size="sm"
      title="argus.core.scroll_ids.confusable_pairs() names these two as misreadable for one another; this is the engine's own list, not a rule reimplemented here"
    >
      easily misread for {s.confusableWith}
    </Chip>
  );
}

function Segments({ s }: { s: ScrollHoldings }) {
  return (
    <div style={{ display: "grid", gap: 6, paddingTop: 10, borderTop: "1px solid var(--line-soft)" }}>
      <span className="eyebrow">{s.segments.length} segments</span>
      <div style={{ overflowX: "auto" }}>
        <table style={{ borderCollapse: "collapse", minWidth: 660 }}>
          <thead>
            <tr>
              {
}
            {["segment", "labels", "raw CT", "coord map", "supervision", "validation",
              "label pitch, microns", "raw pitch, microns", "state"].map(
                (h) => (
                  <th
                    key={h}
                    className="eyebrow"
                    style={{
                      textAlign: "left",
                      padding: "4px 14px 6px 0",
                      borderBottom: "1px solid var(--line)",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {h}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {s.segments.map(({ id, row }) => (
              <tr key={id}>
                <td className="mono" style={{ padding: "5px 14px 5px 0" }}>
                  {id}
                </td>
                {(
                  [
                    row.labels,
                    row.raw_ct,
                    row.coordinate_map,
                    row.supervision_mask,
                    row.validation_mask,
                  ] as boolean[]
                ).map((v, i) => (
                  <td
                    key={i}
                    className="small"
                    style={{
                      padding: "5px 14px 5px 0",
                      color: v ? "var(--status-certified)" : "var(--ink-faint)",
                    }}
                  >
                    {
}
                    {v ? "yes" : "no"}
                  </td>
                ))}
                {
}
                <PitchCell v={row.label_pitch_um} />
                <PitchCell
                  v={row.raw_ct_pitch_um}
                  ratio={row.pitch_ratio_label_to_raw ?? null}
                />
                <td style={{ padding: "5px 14px 5px 0" }}>
                  <Chip tone={PROJECTION_TONE[row.projection_state] as Tone} size="sm">
                    {row.projection_state.replace(/_/g, " ").toLowerCase()}
                  </Chip>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {s.segments
        .filter((x) => x.row.raw_chunk_axis_caveat)
        .slice(0, 1)
        .map((x) => (
          <span key={x.id} className="small faint">
            axis caveat: {x.row.raw_chunk_axis_caveat}
          </span>
        ))}
      {s.segments
        .filter((x) => x.row.pitch_scale_caveat)
        .slice(0, 1)
        .map((x) => (
          <span key={x.id} className="small" style={{ color: "var(--status-blocked)" }}>
            pitch scale: {x.row.pitch_scale_caveat}
          </span>
        ))}
    </div>
  );
}

function PitchCell({
  v,
  ratio = null,
}: {
  v: number | number[] | null | undefined;
  ratio?: number | null;
}) {
  const p = pitch(v);
  const undeclared = p.text === "undeclared";
  return (
    <td
      className="small"
      style={{
        padding: "5px 14px 5px 0",
        whiteSpace: "nowrap",
        color: p.disagree
          ? "var(--status-blocked)"
          : undeclared
            ? "var(--ink-faint)"
            : "var(--ink)",
      }}
      title={p.disagree ? "stores disagree on this segment's pitch" : undefined}
    >
      {p.text}
      {p.disagree ? " (disagree)" : ""}
      {ratio && ratio !== 1 ? (
        <span style={{ color: "var(--status-blocked)" }}> &times;{ratio}</span>
      ) : null}
    </td>
  );
}

function Grouping({ s }: { s: ScrollHoldings }) {
  return (
    <div style={{ display: "grid", gap: 10, paddingTop: 10, borderTop: "1px solid var(--line-soft)" }}>
      <div style={{ display: "flex", gap: 14, flexWrap: "wrap" }}>
        <span className="small">
          <Chip tone="certified" size="sm">
            declared
          </Chip>{" "}
          <span className="muted">
            {s.declared.length} store{s.declared.length === 1 ? "" : "s"} whose own metadata
            names this scroll
          </span>
        </span>
        <span className="small">
          <Chip tone="blocked" size="sm">
            inferred from path
          </Chip>{" "}
          <span className="muted">
            {s.inferred.length} grouped by directory name only — a hint, not a declaration
          </span>
        </span>
      </div>
      <details data-control={`holdings.stores.${s.scroll}`}>
        <summary className="small muted">Stores</summary>
        <div style={{ paddingTop: 10, display: "grid", gap: 12 }}>
          {s.declared.length ? (
            <div style={{ display: "grid", gap: 5 }}>
              <span className="eyebrow">Declared</span>
              <AssetTable assets={s.declared} />
            </div>
          ) : null}
          {s.inferred.length ? (
            <div style={{ display: "grid", gap: 5 }}>
              <span className="eyebrow">Inferred from path</span>
              <AssetTable assets={s.inferred} />
            </div>
          ) : null}
        </div>
      </details>
    </div>
  );
}

function AssetTable({ assets }: { assets: AssetRow[] }) {
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ borderCollapse: "collapse", minWidth: 620 }}>
        <thead>
          <tr>
            {
}
            {["kind", "segment", "files", "size", "pitch, microns", "on disk"].map((h) => (
              <th
                key={h}
                className="eyebrow"
                style={{
                  textAlign: "left",
                  padding: "4px 14px 6px 0",
                  borderBottom: "1px solid var(--line)",
                  whiteSpace: "nowrap",
                }}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {assets.map((a, i) => (
            <tr key={i}>
              <td className="small" style={{ padding: "5px 14px 5px 0" }}>
                {a.asset_kind.replace(/_/g, " ")}
              </td>
              <td className="mono" style={{ padding: "5px 14px 5px 0" }}>
                {isKnown(a.segment_id) ? a.segment_id.value : ""}
              </td>
              <td className="mono" style={{ padding: "5px 14px 5px 0" }}>
                {a.files.toLocaleString()}
              </td>
              <td className="mono" style={{ padding: "5px 14px 5px 0" }}>
                {gib(a.bytes)}
                {a.size_truncated ? " +" : ""}
              </td>
              <td
                className="small"
                style={{
                  padding: "5px 14px 5px 0",
                  color: isKnown(a.declared_pitch_um) ? "var(--ink)" : "var(--ink-faint)",
                }}
              >
                {
}
                {isKnown(a.declared_pitch_um) ? a.declared_pitch_um.value : "not declared"}
              </td>
              <td className="small" style={{ padding: "5px 14px 5px 0" }}>
                {a.local_state ?? ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Coverage({ view }: { view: HoldingsView }) {
  return (
    <section className="panel" style={{ padding: 18, display: "grid", gap: 8 }}>
      <h2 className="eyebrow">
        <Database size={13} aria-hidden /> Identifier coverage
      </h2>
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        {
}
        <Chip
          tone={
            !view.coverageMeasured ? "blocked" : view.refusedIds.length ? "blocked" : "certified"
          }
          size="sm"
        >
          {view.coverageMeasured ? `${view.refusedIds.length} refused` : "not measured"}
        </Chip>
        <span className="small muted">
          {!view.coverageMeasured ? (
            "This catalogue carries no identifier-coverage section, so how many grouping keys resolved is unknown. It is not a claim that they all did."
          ) : (
            <>
              of a canonical set of {view.canonicalSetSize ?? "an unreported number"}.
              {view.refusedIds.length
                ? ` Refused: ${view.refusedIds.join(", ")}.`
                : " Every grouping key on this machine resolved."}
            </>
          )}{" "}
          A refusal is a finding, not an error state — an identifier that cannot be resolved
          is never fuzzy-matched into one that can.
        </span>
      </div>
      {view.coverageNote ? (
        <span className="small faint">{view.coverageNote}</span>
      ) : null}
      {view.unknownCount ? (
        <span className="small faint">
          {view.unknownCount} fields in this catalogue are recorded as unknown with a stated
          reason. Each is shown where it belongs rather than blanked.
        </span>
      ) : null}
    </section>
  );
}

function renderValue(v: unknown): string {
  if (typeof v === "string") return v;
  if (v && typeof v === "object")
    return Object.entries(v as Record<string, unknown>)
      .map(([k, x]) => `${k.replace(/_/g, " ")}: ${renderValue(x)}`)
      .join(" · ");
  return String(v);
}

export type { Triple };
