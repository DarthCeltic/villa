export const routes = {
  observatory: (runId?: string | null) =>
    runId ? `/explore?run=${encodeURIComponent(runId)}` : "/explore",
  workbench: (runId?: string | null) =>
    runId ? `/workbench/${encodeURIComponent(runId)}` : "/workbench",
  evidence: (runId?: string | null) =>
    runId ? `/evidence/${encodeURIComponent(runId)}` : "/evidence",
  library: (targetId?: string | null, runId?: string | null) => {
    const q = new URLSearchParams();
    if (targetId) q.set("target", targetId);
    if (runId) q.set("run", runId);
    const s = q.toString();
    return s ? `/explore?${s}` : "/explore";
  },
  sources: () => "/sources",
  collections: () => "/explore",
  system: () => "/system",
};

export function runDestinations(runId: string, targetId?: string | null) {
  return [
    { key: "workbench", label: "Open in Workbench", to: routes.workbench(runId) },
    { key: "observatory", label: "Locate in Explore", to: routes.observatory(runId) },
    { key: "evidence", label: "View Evidence", to: routes.evidence(runId) },
    ...(targetId
      ? [{ key: "library", label: "Show the scroll in Explore", to: routes.library(targetId, runId) }]
      : []),
  ];
}
