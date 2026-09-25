import { Fragment } from "react";
import "../../theme/status.css";
import { Disclosure } from "../Disclosure";
import { OpsBadge } from "../OpsKit";
import { scoreLine, usePublicRuns, type PublicRunRow } from "../../lib/publicRuns";

const short = (h: string | null | undefined) => (h ? `${h.slice(0, 12)}…` : "—");

export function PublicRunCard({ row, control = "public-run", expanded = false }: { row: PublicRunRow; control?: string; expanded?: boolean }) {
  const r = row.public_run;
  if (!r) return null;
  const rc = r.result_class;
  const score = scoreLine(rc);
  return (
    <article className="pub-run" data-control={control} data-run-id={row.run_id} data-presentation={rc.presentation ?? undefined}>
      <p className="pub-run-banner" data-control={`${control}.banner`} role="note">
        {rc.banner ?? "This receipt states no result class. Read nothing into its score."}
      </p>
      <p className="pub-run-head">
        <span className="ops-mono" data-control={`${control}.target`}>{r.target ?? row.run_id}</span>{" "}
        <OpsBadge tone={r.outcome === "COMPLETED" ? "ok" : "warn"} control={`${control}.outcome`}>{r.outcome ?? "outcome not recorded"}</OpsBadge>{" "}
        {score ? <span data-control={`${control}.score`}>{score}</span> : null}
        {r.scroll ? <span className="pub-run-dim"> · {r.scroll}</span> : null}
      </p>
      <p className="pub-run-dim" data-control={`${control}.claims`}>
        Discovery claim: {rc.may_claim_discovery === false ? "not permitted" : "not stated"} · Ink-found claim:{" "}
        {rc.may_claim_ink_found === false ? "not permitted" : "not stated"}
        {rc.detector_cross_scroll_qualified === false ? " · detector not qualified across scrolls" : ""}
      </p>
      <Disclosure
        className="pub-run-more"
        summaryClassName="pub-run-sum"
        defaultOpen={expanded}
        summary={`Stages, hashes and data terms (${r.stages.length} stages)`}
        data-control={`${control}.more`}
      >
        <dl className="pub-run-dl">
          {rc.public_name ? (<><dt>Target</dt><dd>{rc.public_name}</dd></>) : null}
          {rc.detector ? (<><dt>Detector</dt><dd>{rc.detector}</dd></>) : null}
          {rc.acquisition ? (<><dt>Acquisition</dt><dd>{rc.acquisition}</dd></>) : null}
          {rc.exposure_basis ? (<><dt>Exposure</dt><dd>{rc.exposure_basis}{rc.target_class ? ` · ${rc.target_class}` : ""}</dd></>) : null}
          <dt>Run</dt>
          <dd>{r.run_id ?? row.run_id} · {r.started_utc ?? "?"} to {r.finished_utc ?? "?"}{r.launch_ceiling ? ` · ceiling ${r.launch_ceiling}` : ""}</dd>
        </dl>
        <ol className="pub-run-stages" data-control={`${control}.stages`}>
          {r.stages.map((s, i) => (
            <li key={`${s.stage}-${i}`} data-state={s.state ?? undefined}>
              <span className="ops-mono">{s.stage}</span> {s.state}
              {s.seconds !== null ? ` · ${s.seconds}s` : ""}
            </li>
          ))}
        </ol>
        <dl className="pub-run-dl" data-control={`${control}.hashes`}>
          <dt>Manifest sha256</dt><dd className="ops-mono" title={r.manifest_sha256 ?? undefined}>{r.manifest_sha256 ?? "—"}</dd>
          {r.crop_manifest_sha256 ? (<><dt>Crop manifest sha256</dt><dd className="ops-mono" title={r.crop_manifest_sha256}>{r.crop_manifest_sha256}</dd></>) : null}
          {Object.entries(r.outputs).map(([name, o]) => (
            <Fragment key={name}>
              <dt>{name}</dt><dd className="ops-mono" title={o.sha256}>{o.sha256}{o.bytes !== null ? ` · ${o.bytes} bytes` : ""}</dd>
            </Fragment>
          ))}
          {r.source_commit ? (<><dt>Source commit</dt><dd className="ops-mono">{short(r.source_commit)}</dd></>) : null}
          {row.receipt_sha256 ? (<><dt>Receipt sha256</dt><dd className="ops-mono">{row.receipt_sha256}</dd></>) : null}
        </dl>
        <div data-control={`${control}.terms`}>
          <p className="pub-run-dim">Data terms</p>
          <ul className="pub-run-terms">
            {Object.entries(r.terms).map(([k, v]) => (<li key={k}><strong>{k}</strong>: {v}</li>))}
          </ul>
        </div>
        {rc.not_established.length || r.limits.length ? (
          <div data-control={`${control}.limits`}>
            <p className="pub-run-dim">Not established / limits</p>
            <ul className="pub-run-terms">
              {[...rc.not_established, ...r.limits].map((t) => (<li key={t}>{t}</li>))}
            </ul>
          </div>
        ) : null}
      </Disclosure>
    </article>
  );
}

export function PublicRunsView({
  runs, settled, failure, control = "public-runs", quiet = false, expanded = false,
}: { runs: PublicRunRow[]; settled: boolean; failure: string | null; control?: string; quiet?: boolean; expanded?: boolean }) {
  if (quiet && (!settled || (!runs.length && !failure))) return null;
  return (
    <section className="pub-runs" data-control={control} aria-label="Scored public runs">
      <p className="pub-run-dim">
        <strong>Scored public runs</strong> · {settled ? `${runs.length} listed` : "reading…"} · from the evidence index,
        receipts written by <span className="ops-mono">argus run</span>
      </p>
      {failure ? (
        <p className="pub-run-dim" data-control={`${control}.failed`}>
          The run index could not be read ({failure}). That is a failed read, not an empty archive.
        </p>
      ) : settled && !runs.length ? (
        <p className="pub-run-dim" data-control={`${control}.empty`}>
          No scored public run is indexed here. Nothing in this line says that none has happened elsewhere.
        </p>
      ) : null}
      {runs.map((row) => (<PublicRunCard key={`${row.collection}/${row.run_id}`} row={row} control={`${control}.run`} expanded={expanded} />))}
    </section>
  );
}

export function PublicRuns({ scroll, control, quiet = false, expanded = false }: { scroll?: string | null; control?: string; quiet?: boolean; expanded?: boolean }) {
  const s = usePublicRuns(scroll);
  return <PublicRunsView {...s} control={control} quiet={quiet} expanded={expanded} />;
}
