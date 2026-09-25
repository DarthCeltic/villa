import { Fragment, useMemo, useState } from "react";
import { Chip } from "./Status";

export interface TargetRow {
  scroll: string;
  scan_id: string;
  prizes: string[];
  volume_store: string | null;
  store_state: "FOUND" | "NOT_FOUND";
  pitch_um: number | null;
  energy_kev: number | null;
  v1_scan_id: string | null;
  v1_scan_id_still_listed: boolean | null;
}

export interface TargetSets {
  sets: Record<string, { count: number; scrolls: string[]; rule?: string }>;
  targets: TargetRow[];
  acquisition_families: Record<string, string[]>;
  v1_ids_that_no_longer_resolve: string[];
  why_v2: string;
}

function family(t: TargetRow) {
  return t.pitch_um && t.energy_kev ? `${t.pitch_um} µm / ${t.energy_kev} keV` : "unknown";
}

export function TargetCatalog({ data }: { data: TargetSets }) {
  const prizes = Object.keys(data.sets).filter(
    (k) => typeof (data.sets[k] as { count?: number })?.count === "number",
  );
  const [prize, setPrize] = useState(prizes[0] ?? "FIRST_LETTERS");
  const [openScroll, setOpenScroll] = useState<string | null>(null);

  const rows = useMemo(
    () => data.targets.filter((t) => t.prizes.includes(prize)),
    [data, prize],
  );

  return (
    <section
      style={{
        background: "var(--bg-raised)",
        border: "1px solid var(--line)",
        borderRadius: "var(--radius-sm)",
        padding: "14px 16px",
        display: "grid",
        gap: 12,
      }}
    >
      <header style={{ display: "flex", gap: 12, alignItems: "baseline", flexWrap: "wrap" }}>
        <h2 style={{ margin: 0, fontSize: "var(--t-h2)" }}>Eligible scroll volumes</h2>
        <span className="small muted">
          two prizes, two lists — never merged
        </span>
      </header>

      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {prizes.map((p) => (
          <button
            key={p}
            onClick={() => setPrize(p)}
            aria-pressed={p === prize}
            className="interactive"
            style={{
              padding: "7px 12px",
              borderRadius: "var(--radius-sm)",
              border: "1px solid " + (p === prize ? "var(--accent)" : "var(--line-strong)"),
              background: p === prize ? "var(--bg-raised-2)" : "transparent",
              color: p === prize ? "var(--ink)" : "var(--ink-dim)",
              fontSize: "var(--t-small)",
              cursor: "pointer",
            }}
          >
            {p.replace(/_/g, " ")}
            <span
              style={{
                display: "block",
                fontFamily: "var(--mono)",
                fontSize: "var(--t-meta)",
                color: "var(--ink-faint)",
              }}
            >
              {data.sets[p]?.count ?? 0} volumes
            </span>
          </button>
        ))}
      </div>

      {data.v1_ids_that_no_longer_resolve?.length > 0 && (
        <p
          style={{
            margin: 0,
            fontSize: "var(--t-small)",
            color: "var(--warn)",
            borderLeft: "2px solid var(--warn)",
            paddingLeft: 8,
          }}
        >
          {data.v1_ids_that_no_longer_resolve.length} scan ids from the previous frozen registry
          no longer resolve on S3. A scroll name is not a volume; the scan id is.
        </p>
      )}

      <div style={{ overflowX: "auto" }}>
        <table style={{ borderCollapse: "collapse", width: "100%", fontSize: "var(--t-small)" }}>
          <thead>
            <tr style={{ textAlign: "left", color: "var(--ink-dim)" }}>
              {["Scroll", "Scan id", "Acquisition", "Store", "Prizes"].map((h) => (
                <th
                  key={h}
                  style={{
                    padding: "6px 10px 6px 0",
                    borderBottom: "1px solid var(--line)",
                    fontWeight: 600,
                    fontFamily: "var(--mono)",
                    fontSize: "var(--t-meta)",
                    letterSpacing: ".08em",
                    textTransform: "uppercase",
                  }}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => {
              const open = openScroll === t.scroll;
              return (
                <Fragment key={t.scroll}>
                  <tr
                    onClick={() => setOpenScroll(open ? null : t.scroll)}
                    style={{
                      cursor: "pointer",
                      background: open ? "var(--bg-raised-2)" : undefined,
                    }}
                  >
                    <td style={{ padding: "7px 10px 7px 0", borderBottom: "1px solid var(--line)" }}>
                      {t.scroll}
                    </td>
                    <td
                      style={{
                        padding: "7px 10px 7px 0",
                        borderBottom: "1px solid var(--line)",
                        fontFamily: "var(--mono)",
                        fontSize: "var(--t-meta)",
                        color: "var(--ink-dim)",
                      }}
                    >
                      {t.scan_id}
                    </td>
                    <td style={{ padding: "7px 10px 7px 0", borderBottom: "1px solid var(--line)" }}>
                      {family(t)}
                    </td>
                    <td style={{ padding: "7px 10px 7px 0", borderBottom: "1px solid var(--line)" }}>
                      <Chip tone={t.store_state === "FOUND" ? "active" : "refused"} size="sm">
                        {t.store_state === "FOUND" ? "on S3" : "not found"}
                      </Chip>
                    </td>
                    <td
                      style={{
                        padding: "7px 10px 7px 0",
                        borderBottom: "1px solid var(--line)",
                        color: "var(--ink-faint)",
                        fontSize: "var(--t-small)",
                      }}
                    >
                      {t.prizes.map((p) => p.split("_")[0]).join(" · ")}
                    </td>
                  </tr>
                  {open && (
                    <tr key={t.scroll + "-d"}>
                      <td colSpan={5} style={{ padding: "4px 0 12px", borderBottom: "1px solid var(--line)" }}>
                        <div style={{ display: "grid", gap: 5, fontSize: "var(--t-small)", color: "var(--ink-dim)", paddingLeft: 2 }}>
                          <div>
                            <b style={{ color: "var(--ink)" }}>store</b>{" "}
                            <code style={{ fontFamily: "var(--mono)", fontSize: "var(--t-meta)" }}>
                              {t.volume_store ?? "—"}
                            </code>
                          </div>
                          {t.v1_scan_id && t.v1_scan_id_still_listed === false && (
                            <div style={{ color: "var(--warn)" }}>
                              previously frozen as {t.v1_scan_id}, which no longer resolves
                            </div>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      <p style={{ margin: 0, fontSize: "var(--t-small)", color: "var(--ink-faint)" }}>
        Acquisition families across this set:{" "}
        {Object.entries(data.acquisition_families)
          .map(([k, v]) => `${k} — ${v.length}`)
          .join(" · ")}
        . Nothing on this screen downloads anything.
      </p>
    </section>
  );
}
