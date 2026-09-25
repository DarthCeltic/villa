import { usePoll } from "../lib/poll";

interface Item {
  id: string;
  label: string;
  root: string;
  path: string;
  state: "AVAILABLE_LOCALLY" | "NOT_MOUNTED" | "RECOVERABLE" | "MISSING";
  preservation_class: string;
  recoverable: boolean;
  recovery: { kind?: string; source?: string; receipt?: string; note?: string };
  inventory: { files?: number; bytes?: number; as_of_utc?: string } | null;
  read_only: boolean;
}

interface Doc {
  roots: { id: string; path: string; class: string; visible: boolean }[];
  items: Item[];
  rule: string;
}

const gib = (b?: number) => (typeof b === "number" ? `${(b / 1024 ** 3).toFixed(1)} GiB` : "size not measured");

function tone(state: string): string {
  if (state === "AVAILABLE_LOCALLY") return "var(--status-certified)";
  if (state === "MISSING") return "var(--bad)";
  return "var(--ink-dim)";
}

export function ExternalEvidencePanel() {
  const doc = usePoll<Doc>("/api/external_evidence", { intervalMs: 60000 });
  const d = doc.data;
  if (!d) {
    return (
      <div className="meta" data-control="external.loading">
        {doc.failure ? `Could not read /api/external_evidence: ${doc.failure.message}. This is not "nothing external".` : "Reading external evidence…"}
      </div>
    );
  }
  return (
    <section aria-label="External evidence" data-control="external.panel" style={{ display: "grid", gap: 8, marginTop: 16 }}>
      <div style={{ fontWeight: 600 }}>Evidence outside this repository</div>
      <div className="meta">{d.rule}</div>
      {d.roots.map((r) => (
        <div key={r.id} className="meta" data-control={`external.root.${r.id}`}>
          <b>{r.id}</b> · {r.class} · <span className="ops-mono">{r.path}</span> · {r.visible ? "visible from here" : "not visible from here"}
        </div>
      ))}
      {d.items.map((it) => (
        <div key={it.id} className="meta" data-control={`external.item.${it.id}`} style={{ borderTop: "1px solid var(--line-strong)", paddingTop: 4 }}>
          <b>{it.label}</b> — <span style={{ color: tone(it.state) }}>{it.state}</span> · {it.preservation_class}
          {it.inventory ? ` · ${it.inventory.files ?? "?"} files, ${gib(it.inventory.bytes)} as of ${it.inventory.as_of_utc ?? "?"}` : ""}
          <div className="ops-mono">{it.path}</div>
          {it.state !== "AVAILABLE_LOCALLY" && it.recoverable ? (
            <div>Recoverable from {it.recovery.source ?? "a declared source"}. {it.recovery.note ?? ""}</div>
          ) : null}
        </div>
      ))}
    </section>
  );
}
