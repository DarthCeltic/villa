import type { ReactNode } from "react";
import { Chip, type Tone } from "./Status";
import { Disclosure } from "./Disclosure";
import { usePoll } from "../lib/poll";
import { failureLine } from "../lib/http";

type Surface = {
  state: string;
  headline: string;
  [k: string]: unknown;
};

type Surfaces = {
  contract: string;
  generated_utc: string;
  worst_state: string;
  surfaces: Record<string, Surface>;
};

const TONE: Record<string, Tone> = {
  CLEAR: "certified",
  IN_SYNC: "certified",
  HELD: "certified",
  EMPTY: "active",
  READY_BUT_UNPUBLISHED: "active",
  DRIFTED: "blocked",
  NOT_MEASURED: "blocked",
  NOT_CHECKED: "blocked",
  UNREADABLE: "refused",
  BELOW_FLOOR: "refused",
  BLOCKED: "refused",
  ORPHANED_WORK: "refused",
  ERROR: "refused",
};

const TITLES: Record<string, { title: string; asks: string }> = {
  resources: { title: "Resources", asks: "is there room to run, and did the last run let go" },
  updates: { title: "Updates", asks: "what upstream offers versus what is running here" },
  downloads: { title: "Downloads", asks: "bytes held once, and what can never be evicted" },
  public_release: { title: "Public release", asks: "what would leave this machine" },
};

const ORDER = ["resources", "updates", "downloads", "public_release"];

function displayOrder(surfaces: Record<string, Surface>): string[] {
  return ORDER.filter((id) => id in surfaces);
}

function titleFor(id: string): { title: string; asks: string } {
  return TITLES[id] ?? { title: id, asks: "a surface this screen has not been taught about" };
}

function tone(state: string): Tone {
  return TONE[state] ?? "blocked";
}

function n(v: unknown): string {
  return typeof v === "number" ? v.toLocaleString() : "—";
}

function gb(v: unknown): string {
  return typeof v === "number" ? `${(v / 1024 ** 3).toFixed(2)} GB` : "—";
}

function Facts({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="ops-facts">
      {rows.map(([k, v]) => (
        <div key={k} className="ops-fact">
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function summaryFacts(id: string, s: Surface): [string, ReactNode][] {
  const free = (s.free ?? {}) as Record<string, number | null>;
  switch (id) {
    case "resources":
      return [
        ["RAM free", free.ram_free_mb != null ? `${n(free.ram_free_mb)} MB` : "—"],
        ["VRAM free", free.vram_free_mb != null ? `${n(free.vram_free_mb)} MB` : "—"],
        ["Disk free", free.disk_free_mb != null ? `${n(free.disk_free_mb)} MB` : "—"],
        ["Orphaned", `${n(s.orphan_working_set_mb)} MB`],
      ];
    case "updates": {
      const sources = (s.sources ?? []) as { state?: string }[];
      return [
        ["Tracked", n(sources.length)],
        ["Drifted", n((s.drifted as unknown[] | undefined)?.length ?? 0)],
        ["Checked", (s.checked_utc as string) ?? "never"],
      ];
    }
    case "downloads":
      return [
        ["Held", n(s.objects_held)],
        ["Size", gb(s.bytes_held)],
        ["Produced here", n(s.derived_here)],
        ["Re-fetchable", n(s.upstream_refetchable)],
      ];
    case "public_release":
      return [
        ["Would publish", n(s.publishable)],
        ["Withheld", n(s.withheld)],
        ["Unclassified", n(s.unclassified)],
        ["Undeclared licences", n((s.undeclared_licences as unknown[] | undefined)?.length ?? 0)],
      ];
    default:
      return [];
  }
}

function Details({ id, s }: { id: string; s: Surface }) {
  if (id === "updates") {
    const sources = (s.sources ?? []) as {
      key: string;
      kind: string;
      state: string;
      stable: string;
      candidate: string | null;
      behind: string | null;
    }[];
    return (
      <>
        <table className="ops-table">
          <thead>
            <tr>
              <th>Source</th>
              <th>Running</th>
              <th>Available</th>
              <th>State</th>
            </tr>
          </thead>
          <tbody>
            {sources.map((r) => (
              <tr key={r.key}>
                <td>{r.key}</td>
                <td className="mono">{(r.stable ?? "").slice(0, 12)}</td>
                <td className="mono">
                  {(r.candidate ?? "—").slice(0, 12)}
                  {r.behind && r.behind !== "0" ? ` (+${r.behind})` : ""}
                </td>
                <td>
                  <Chip tone={tone(r.state)} size="sm">
                    {r.state}
                  </Chip>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="ops-why">{s.nothing_was_promoted as string}</p>
      </>
    );
  }

  if (id === "public_release") {
    const byClass = (s.by_class ?? {}) as Record<string, number>;
    return (
      <>
        <Facts rows={Object.entries(byClass).map(([k, v]) => [k, n(v)])} />
        <p className="ops-why">{s.fail_closed as string}</p>
        <p className="ops-why">{s.publishing_is_not_automatic as string}</p>
      </>
    );
  }

  if (id === "resources") {
    const floors = (s.floors ?? {}) as Record<string, unknown>;
    return (
      <>
        {s.floors_measured ? (
          <Facts
            rows={[
              ["RAM floor", `${n(floors.ram_floor_mb)} MB`],
              ["VRAM floor", `${n(floors.vram_floor_mb)} MB`],
              ["Disk floor", `${n(floors.disk_floor_mb)} MB`],
              ["Measured from", (floors.measured_from as string) ?? "—"],
            ]}
          />
        ) : (
          <p className="ops-why">{(floors.why as string) ?? "no floors recorded"}</p>
        )}
        <p className="ops-why">{s.why_floors_are_measured as string}</p>
        {(s.orphan_jobs as string[] | undefined)?.length ? (
          <p className="ops-why">Jobs still holding memory: {(s.orphan_jobs as string[]).join(", ")}</p>
        ) : null}
      </>
    );
  }

  return <p className="ops-why">{s.derived_is_never_evicted as string}</p>;
}

export function OperationsPanel() {
  const poll = usePoll<Surfaces>("/api/surfaces", { intervalMs: 20000 });
  const d = poll.data;

  if (!d) {
    return (
      <section className="ops">
        <p className="ops-why">
          {poll.loading
            ? "Reading the operational surfaces…"
            : poll.failure
              ? failureLine(poll.failure)
              : "No operational surfaces were returned."}
        </p>
      </section>
    );
  }

  return (
    <section className="ops">
      <header className="ops-head">
        <Chip tone={tone(d.worst_state)}>{d.worst_state}</Chip>
        <span className="ops-why">
          The overall state is the worst surface, never an average: an average of states
          hides the one that mattered.
        </span>
      </header>

      <div className="ops-grid">
        {displayOrder(d.surfaces).map((id) => {
          const s = d.surfaces[id];
          if (!s) return null;
          const meta = titleFor(id);
          return (
            <article key={id} className="ops-card">
              <h3>
                {meta.title}
                <Chip tone={tone(s.state)} size="sm">
                  {s.state}
                </Chip>
              </h3>
              <p className="ops-asks">{meta.asks}</p>
              <p className="ops-headline">{s.headline}</p>
              <Facts rows={summaryFacts(id, s)} />
              <Disclosure summary="Details" className="ops-more">
                <Details id={id} s={s} />
              </Disclosure>
            </article>
          );
        })}
      </div>

      <footer className="ops-why">
        Read at {new Date(d.generated_utc).toLocaleTimeString()}
        {poll.stale ? " — stale" : ""}. Nothing on this screen fetches, promotes or publishes;
        it reports what those subsystems last recorded.
      </footer>
    </section>
  );
}
