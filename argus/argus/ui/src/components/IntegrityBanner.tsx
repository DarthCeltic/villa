import { certificationIsSuppressed, type Integrity } from "../lib/systemTruth";
import type { Polled } from "../lib/poll";

export function IntegrityBanner({ integrity }: { integrity: Polled<Integrity> }) {
  const i = integrity.data;
  const gate = certificationIsSuppressed(i);

  if (gate.suppressed && i?.chain) {
    return (
      <div
        role="alert"
        data-integrity="BROKEN"
        style={{
          padding: "10px 20px",
          display: "flex",
          gap: 14,
          alignItems: "baseline",
          flexWrap: "wrap",
          background: "var(--status-refused-dim)",
          borderBottom: "1px solid var(--status-refused-edge)",
          color: "var(--ink)",
        }}
      >
        <strong
          style={{
            color: "var(--status-refused)",
            letterSpacing: ".08em",
            textTransform: "uppercase",
            fontSize: "var(--t-meta)",
          }}
        >
          ✕ System integrity — audit chain BROKEN
        </strong>
        <span className="small">
          {i.ledger} fails verification at record{" "}
          <span className="mono">{i.chain.at ?? "?"}</span>
          {i.chain.why ? ` — ${i.chain.why}` : ""}. From that record onward the log cannot
          attest to what ran, so scientific certification is displayed as QUALIFIED and not
          as certified anywhere in this interface.
        </span>
        <span className="meta faint">
          verified by {i.verified_by} · read {integrity.ageS === null ? "—" : `${integrity.ageS}s ago`}
          {integrity.stale ? " · this reading is stale" : ""}
        </span>
      </div>
    );
  }

  if (integrity.failure || (i && !i.readable)) {
    return (
      <div
        role="status"
        data-integrity="UNKNOWN"
        style={{
          padding: "7px 20px",
          background: "var(--status-blocked-dim)",
          borderBottom: "1px solid var(--status-blocked-edge)",
          color: "var(--ink-dim)",
          fontSize: "var(--t-meta)",
        }}
      >
        ! System integrity unknown — the audit chain could not be verified
        {integrity.failure ? ` (${integrity.failure.message})` : ""}. This is the interface
        failing to check, not a statement that the ledger is sound.
      </div>
    );
  }

  return null;
}

export function IntegrityLine({ integrity }: { integrity: Polled<Integrity> }) {
  const i = integrity.data;
  if (integrity.loading) return <span className="meta faint">checking the audit chain…</span>;
  if (!i || !i.readable || integrity.failure) {
    return (
      <span className="meta" style={{ color: "var(--status-blocked)" }}>
        audit chain: could not be verified
        {integrity.failure ? ` — ${integrity.failure.message}` : ""}
      </span>
    );
  }
  const st = i.chain?.status ?? "UNKNOWN";
  const tone =
    st === "INTACT" ? "var(--status-certified)" : st === "EMPTY" ? "var(--ink-faint)"
      : "var(--status-refused)";
  return (
    <span className="meta" style={{ color: tone }} title={i.means[st] ?? ""}>
      audit chain: {st}
      {st === "INTACT" && i.chain?.n ? ` · ${i.chain.n} records` : ""}
      {st === "BROKEN" ? ` at record ${i.chain?.at ?? "?"}` : ""}
    </span>
  );
}
