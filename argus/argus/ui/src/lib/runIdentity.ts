import type { RunRecord } from "../api";

function norm(p: string | null | undefined): string {
  return String(p ?? "")
    .replace(/\\/g, "/")
    .replace(/\/+$/, "")
    .toLowerCase();
}

function isAncestorOf(a: string, b: string): boolean {
  return a !== "" && b !== a && b.startsWith(a + "/");
}

export interface RunPartition {
  runs: RunRecord[];
  containers: { record: RunRecord; contains: string[] }[];
}

export function partitionRuns(all: RunRecord[]): RunPartition {
  const dirs = all.map((r) => ({ r, d: norm(r.run_dir) }));
  const runs: RunRecord[] = [];
  const containers: { record: RunRecord; contains: string[] }[] = [];
  for (const { r, d } of dirs) {
    const inside = dirs.filter((o) => o.r !== r && isAncestorOf(d, o.d)).map((o) => o.r.run_id);
    if (inside.length) containers.push({ record: r, contains: inside });
    else runs.push(r);
  }
  return { runs, containers };
}

export interface ResultGroup {
  target: string | null;
  records: RunRecord[];
  primary: RunRecord;
}

export function groupByDeclaredTarget(runs: RunRecord[]): ResultGroup[] {
  const by = new Map<string, RunRecord[]>();
  for (const r of runs) {
    const key = r.target ? `t:${r.target}` : `u:${r.run_id}`;
    const list = by.get(key);
    if (list) list.push(r);
    else by.set(key, [r]);
  }
  const groups: ResultGroup[] = [];
  for (const records of by.values()) {
    const first = records[0];
    if (!first) continue;
    const primary = records.find((r) => r.collection) ?? records.find((r) => r.stage) ?? first;
    groups.push({ target: primary.target ?? null, records, primary });
  }
  return groups;
}

export function resultCount(runs: RunRecord[]): number {
  return groupByDeclaredTarget(runs).length;
}

export function primaryRunForScroll(runs: RunRecord[], scroll: string | null): RunRecord | null {
  if (!scroll) return null;
  const rank: Record<string, number> = {
    CERTIFIED_INK_CANDIDATE: 3,
    CERTIFIED_2D: 2,
    CERTIFIED_SURFACE: 1,
  };
  const { runs: actual } = partitionRuns(runs.filter((r) => !r.foreign));
  const groups = groupByDeclaredTarget(actual).filter(
    (g) => typeof g.target === "string" && g.target.includes(scroll),
  );
  groups.sort(
    (a, b) =>
      (rank[b.primary.highest_certified_stage ?? ""] ?? 0) -
      (rank[a.primary.highest_certified_stage ?? ""] ?? 0),
  );
  return groups[0]?.primary ?? null;
}

const STAMP = /^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(\d{3})$/;

export interface RunLabel {
  primary: string;
  id: string;
  when: string | null;
}

export function runLabel(run: Pick<RunRecord, "run_id" | "target" | "stage">): RunLabel {
  const id = run.run_id;
  const m = STAMP.exec(id);
  if (!m) {
    return { primary: id, id, when: null };
  }
  const months = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
  ];
  const mi = Number(m[2]) - 1;
  const when = `${Number(m[3])} ${months[mi] ?? m[2]} ${m[1]}, ${m[4]}:${m[5]}:${m[6]}`;
  return { primary: when, id, when };
}
