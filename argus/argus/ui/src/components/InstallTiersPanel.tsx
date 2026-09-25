import { usePoll } from "../lib/poll";

interface Row {
  id: string;
  tier: string;
  label: string;
  state: "READY" | "MISSING" | "DEGRADED" | "UNKNOWN";
  detail: string;
  pin_status: string;
  scientific_use_allowed: boolean;
  required: boolean;
  group: string | null;
  observed_digest: string | null;
}

interface Tier {
  label: string;
  plain: string;
  state: string;
  ready: boolean;
  blocking: string[];
  scientific_use_allowed: boolean;
  components: string[];
}

interface Step {
  n: number;
  component: string | null;
  tier: string | null;
  reason: string | null;
  text: string;
  command: string | null;
  downloads: boolean;
  installs: boolean;
  needs_approval: boolean;
  needs_admin: boolean;
}

interface Doc {
  read_only: boolean;
  check: { components: Row[]; tiers: Record<string, Tier> };
  repair: {
    steps: Step[];
    needs_approval: boolean;
    approx_disk_gib: number;
    unknown_size_components: string[];
    planning_gib_per_unknown: number;
    refuses_if_free_disk_below_gib: number;
  };
}

const PIN_TEXT: Record<string, string> = {
  PINNED_BY_DIGEST: "pinned by digest",
  VERSION_PINNED: "version pinned",
  TAG_ONLY_DIGEST_NOT_RESOLVED: "mutable tag only, no digest: not usable for scientific runs",
};

function stateColor(state: string): string {
  if (state === "READY") return "var(--status-certified)";
  if (state === "UNKNOWN") return "var(--ink-dim)";
  return "var(--bad)";
}

export function InstallTiersPanel() {
  const doc = usePoll<Doc>("/api/install_tiers", { intervalMs: 30000 });
  const d = doc.data;
  if (!d) {
    return (
      <div className="meta" data-control="install.loading">
        {doc.failure
          ? `Could not read /api/install_tiers: ${doc.failure.message}. This is not "everything is installed".`
          : "Checking the installation…"}
      </div>
    );
  }
  const byId = new Map(d.check.components.map((c) => [c.id, c]));
  return (
    <div data-control="install.panel" style={{ display: "grid", gap: 16 }}>
      <div className="meta" style={{ color: "var(--ink-dim)" }}>
        Read-only check. Core setup belongs to the packaged Docker/launcher flow. This screen
        explains optional repairs without asking a standard user to copy commands.
      </div>
      {Object.entries(d.check.tiers).map(([tid, t]) => (
        <section key={tid} aria-label={t.label} data-control={`install.tier.${tid}`} style={{ display: "grid", gap: 6 }}>
          <div style={{ fontWeight: 600 }}>
            {t.label}: <span style={{ color: stateColor(t.state) }}>{t.state}</span>
            {t.ready && !t.scientific_use_allowed ? " (ready, but not for scientific use: see pins below)" : ""}
          </div>
          <div className="meta">{t.plain}</div>
          {t.blocking.length ? <div className="meta">Blocking: {t.blocking.join(", ")}</div> : null}
          {t.components.map((cid) => {
            const c = byId.get(cid);
            if (!c) return null;
            return (
              <div key={cid} className="meta" data-control={`install.component.${cid}`} style={{ borderTop: "1px solid var(--line-strong)", paddingTop: 4 }}>
                <b>{c.label}</b> — <span style={{ color: stateColor(c.state) }}>{c.state}</span>
                {c.required ? "" : " (optional)"} · {PIN_TEXT[c.pin_status] ?? c.pin_status}
                {c.detail ? ` · ${c.detail}` : ""}
                {c.observed_digest ? ` · ${c.observed_digest.slice(0, 19)}` : ""}
              </div>
            );
          })}
        </section>
      ))}
      <section aria-label="Repair plan" data-control="install.repair" style={{ display: "grid", gap: 6 }}>
        <div style={{ fontWeight: 600 }}>
          {d.repair.steps.length ? "Optional repair plan" : "Nothing needs repair"}
        </div>
        {d.repair.steps.length ? (
          <div className="meta">
            About {d.repair.approx_disk_gib} GiB of disk
            {d.repair.unknown_size_components.length
              ? `, counting ${d.repair.planning_gib_per_unknown} GiB for each component whose size is unknown (${d.repair.unknown_size_components.join(", ")})`
              : ""}
            ; keep at least {d.repair.refuses_if_free_disk_below_gib} GiB free.
          </div>
        ) : null}
        {d.repair.steps.map((s) => (
          <div key={s.n} className="meta" data-control={`install.step.${s.n}`}>
            {s.n}. {s.text}
            {s.command ? <div style={{ color: "var(--ink-dim)" }}>Exact operator instructions are retained in the signed plan receipt, not shown as a command to copy.</div> : null}
            {s.downloads || s.installs || s.needs_admin ? (
              <div style={{ color: "var(--ink-dim)" }}>
                {[s.downloads ? "downloads" : "", s.installs ? "installs" : "", s.needs_admin ? "needs administrator rights" : ""].filter(Boolean).join(" · ")}
              </div>
            ) : null}
          </div>
        ))}
      </section>
    </div>
  );
}
