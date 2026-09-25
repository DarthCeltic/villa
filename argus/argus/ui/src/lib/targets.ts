import type { Certified, Operational, RunRecord } from "../api";
import { rankCertified } from "../api";

export const UNCATALOGUED = "__uncatalogued__";

export interface TargetGroup {
  id: string;
  scroll: string | null;
  collection: string | null;
  shelf: string;
  acquisitionFamily: string | null;
  volumeId: string | null;
  runs: RunRecord[];
  highestCertified: Certified;
  state: Operational;
  sealed: boolean;
}

const UNKNOWN_SHELF = "unknown acquisition";

export function scrollOf(run: RunRecord): string | null {
  const t = run.target;
  if (!t) return null;
  const head = t.split("/")[0];
  return head && head.trim() ? head.trim() : null;
}

export function groupTargets(runs: RunRecord[]): TargetGroup[] {
  const by = new Map<string, RunRecord[]>();
  for (const r of runs) {
    const key = scrollOf(r) ?? UNCATALOGUED;
    const list = by.get(key);
    if (list) list.push(r);
    else by.set(key, [r]);
  }

  const out: TargetGroup[] = [];
  for (const [id, list] of by) {
    const sorted = [...list].sort((a, b) => a.run_id.localeCompare(b.run_id));
    const withAcq = sorted.find((r) => r.acquisition_family);
    const newest = sorted[sorted.length - 1];
    out.push({
      id,
      scroll: id === UNCATALOGUED ? null : id,
      collection: sorted.find((r) => r.collection)?.collection ?? null,
      shelf: withAcq?.acquisition_family ?? UNKNOWN_SHELF,
      acquisitionFamily: withAcq?.acquisition_family ?? null,
      volumeId: withAcq?.acquisition?.volume_id ?? null,
      runs: sorted,
      highestCertified: highestOf(sorted),
      state: newest ? newest.operational_state : "UNKNOWN",
      sealed: sorted.some((r) => r.blinding.sealed),
    });
  }
  return out.sort((a, b) => (a.scroll ?? "￿").localeCompare(b.scroll ?? "￿"));
}

function highestOf(runs: RunRecord[]): Certified {
  let best: Certified = null;
  for (const r of runs) {
    if (rankCertified(r.highest_certified_stage) > rankCertified(best))
      best = r.highest_certified_stage;
  }
  return best;
}

export function groupBookcases(targets: TargetGroup[]) {
  const by = new Map<string, TargetGroup[]>();
  for (const t of targets) {
    const key = t.collection ?? UNCATALOGUED;
    const list = by.get(key);
    if (list) list.push(t);
    else by.set(key, [t]);
  }
  return [...by.entries()]
    .map(([collection, items]) => ({
      collection: collection === UNCATALOGUED ? null : collection,
      label: collection === UNCATALOGUED ? "Uncatalogued" : collection,
      shelves: shelvesOf(items),
      targets: items,
    }))
    .sort((a, b) =>
      a.collection === null ? 1 : b.collection === null ? -1 : a.label.localeCompare(b.label),
    );
}

function shelvesOf(targets: TargetGroup[]) {
  const by = new Map<string, TargetGroup[]>();
  for (const t of targets) {
    const list = by.get(t.shelf);
    if (list) list.push(t);
    else by.set(t.shelf, [t]);
  }
  return [...by.entries()]
    .map(([shelf, items]) => ({ shelf, targets: items }))
    .sort((a, b) => a.shelf.localeCompare(b.shelf));
}

export function recoveredTexts(runs: RunRecord[]): RunRecord[] {
  return runs.filter((r) => r.highest_certified_stage === "CERTIFIED_INK_CANDIDATE");
}
