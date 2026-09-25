import { Link } from "react-router-dom";
import type { ScrollObject, TargetRegistryState } from "./ShelfUniverse";
import { SurfaceStatusDetail, useSurfaceStatus } from "./SurfaceStatusFacts";
import { PrepareExactEligibleSurface } from "./PrepareExactEligibleSurface";
import "../theme/workbench.css";

export function ShelfDetail({
  s,
  onClose,
  domain,
  targetRegistry,
}: {
  s: ScrollObject;
  onClose: () => void;
  domain: string;
  targetRegistry: TargetRegistryState;
}) {
  const { status: surfaceStatus } = useSurfaceStatus(s.id);
  return (
    <section
      className="ag-drawer"
      aria-label={`Recorded detail for ${s.id}`}
      data-control={`${domain}.detail.${s.id}`}
    >
      <header className="ag-collection-head">
        <h2 className="ag-collection-title">{s.display}</h2>
        <span className="ag-source">{s.id}</span>
        {
}
        <Link
          className="ag-btn ag-btn-primary interactive"
          to={`/workbench?scroll=${encodeURIComponent(s.id)}`}
          data-control={`${domain}.detail.workbench`}
          style={{ marginLeft: "auto" }}
        >
          Open in Workbench
        </Link>
        <button
          type="button"
          className="ag-btn"
          data-control={`${domain}.detail.close`}
          onClick={onClose}
        >
          Close detail
        </button>
      </header>

      {s.fixture.fixture ? (
        <p className="ag-collection-note">
          <span className="ag-fixture">fixture</span> {s.fixture.why}. This is developer data
          and is not a physical scroll.
        </p>
      ) : null}

      {surfaceStatus ? (
        <section aria-label="Status, as independent facts">
          <h3 className="ag-collection-title" style={{ fontSize: "var(--t-body)" }}>
            Status — every fact on its own, sourced
          </h3>
          <SurfaceStatusDetail status={surfaceStatus} />
        </section>
      ) : null}

      <PrepareExactEligibleSurface scroll={s.id} />

      <dl className="ag-kv">
        <div>
          <dt>Prize eligibility</dt>
          <dd>
            {targetRegistry.state === "UNAVAILABLE"
              ? `unknown — ${targetRegistry.why ?? "the official target registry is not installed"}`
              : [s.firstLetters ? "FIRST_LETTERS" : null, s.grandPrize ? "GRAND_PRIZE_2027" : null]
                  .filter(Boolean)
                  .join(" + ") || "none declared by /api/targets"}
          </dd>
        </div>
        <div>
          <dt>In canonical identity list</dt>
          <dd>
            {s.inCanonicalIds
              ? "yes — /api/scroll-ids → canonical"
              : "NO. This identity is declared prize-eligible by /api/targets and is absent from /api/scroll-ids → canonical. The identity list is the thing that should be corrected."}
          </dd>
        </div>
        <div>
          <dt>Aliases</dt>
          <dd>{s.aliases.length ? s.aliases.join(", ") : "none served"}</dd>
        </div>
        <div>
          <dt>Confusable with</dt>
          <dd>
            {s.confusableWith
              ? `${s.confusableWith} — may be misread for ${s.confusableWith}; the pair comes from argus.core.scroll_ids`
              : "no confusable pair served for this id"}
          </dd>
        </div>
      </dl>

      <h3 className="ag-studio-group-name">Declared acquisitions</h3>
      {targetRegistry.state === "UNAVAILABLE" ? (
        <p className="ag-prose">
          Acquisition identity is unknown because the official target registry is not installed.
          No scan, volume, or publication status is inferred from an empty response.
        </p>
      ) : s.acquisitions.length ? (
        <dl className="ag-kv">
          {s.acquisitions.map((a, i) => (
            <div key={`${a.scanId ?? "noscan"}-${i}`}>
              <dt>{a.scanId ?? "scan id undeclared"}</dt>
              <dd>
                {a.pitchUm === null ? "pitch undeclared" : `${a.pitchUm} µm`} ·{" "}
                {a.energyKev === null ? "energy undeclared" : `${a.energyKev} keV`} ·{" "}
                store_state {a.storeState ?? "undeclared"} ·{" "}
                {a.volumeStore ?? "no volume store named"}
              </dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="ag-prose">
          The prize registry declares no acquisition for this scroll, and an acquisition is
          never inferred from a file name.
        </p>
      )}

      <h3 className="ag-studio-group-name">What is on this machine</h3>
      <dl className="ag-kv">
        <div>
          <dt>Indexed material</dt>
          <dd>
            {s.local.line} ({s.local.route})
          </dd>
        </div>
        <div>
          <dt>Published upstream</dt>
          <dd>
            {targetRegistry.state === "UNAVAILABLE"
              ? "unknown — the official target registry is not installed"
              : s.publishedUpstream
              ? "at least one volume is listed in the publisher's bucket (/api/targets → store_state FOUND). This is not the same as holding it."
              : "no volume is listed upstream for this scroll"}
          </dd>
        </div>
        <div>
          <dt>Surface</dt>
          <dd>
            {s.surface.stage} — {s.surface.line}
          </dd>
        </div>
        <div>
          <dt>Ink labels</dt>
          <dd>
            {s.labels.line}
            {s.labels.authority ? ` · authority ${s.labels.authority}` : ""}
          </dd>
        </div>
        {s.shelfRow ? (
          <div>
            <dt>/api/shelf verdict</dt>
            <dd>
              shelf {s.shelfRow.shelf} · local_data {s.shelfRow.local_data} · furthest_stage{" "}
              {s.shelfRow.furthest_stage}
              {s.localDisagreement ? ` — ${s.localDisagreement}` : ""}
            </dd>
          </div>
        ) : (
          <div>
            <dt>/api/shelf verdict</dt>
            <dd>
              this route served no row for this scroll, so its verdict is absent rather than
              negative
            </dd>
          </div>
        )}
      </dl>

      {s.blockedBy.length ? (
        <>
          <h3 className="ag-studio-group-name">What is blocking a reading</h3>
          <ul className="ag-list">
            {s.blockedBy.map((b) => (
              <li key={b} className="ag-item">
                <span className="ag-item-sub">{b}</span>
                <span className="ag-item-id">/api/scrolls → blocked_by</span>
              </li>
            ))}
          </ul>
        </>
      ) : null}

      <h3 className="ag-studio-group-name">Runs that declare this scroll</h3>
      {s.work.runIds.length ? (
        <ul className="ag-list">
          {s.work.runIds.map((id) => (
            <li key={id}>
              <Link
                className="ag-item"
                to={`/workbench/${encodeURIComponent(id)}?scroll=${encodeURIComponent(s.id)}`}
                data-control={`${domain}.detail.run.${id}`}
              >
                <span className="ag-item-title">Open in the Workbench</span>
                <span className="ag-item-id">{id}</span>
              </Link>
            </li>
          ))}
        </ul>
      ) : (
        <p className="ag-prose">
          No run in the feed declares a target naming this scroll. That is an absence of work,
          not a negative result about the scroll.
        </p>
      )}

      <div className="ag-actions">
        <Link className="ag-btn-primary" to={s.next.to} data-control={`${domain}.detail.next`}>
          {s.next.label}
        </Link>
        <span className="ag-prose">{s.next.why}</span>
      </div>
    </section>
  );
}
