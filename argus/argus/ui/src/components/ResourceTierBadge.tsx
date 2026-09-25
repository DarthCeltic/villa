import { resourceTier } from "../lib/resourceTiers";

const COLORS: Record<string, string> = {
  LOCAL_CPU: "var(--ok, #2a7)",
  LOCAL_6GB_VERIFIED: "var(--accent, #2a7)",
  REMOTE_GPU_REQUIRED: "var(--warn, #c73)",
  NOT_YET_MEASURED: "var(--ink-dim, #888)",
};

const SHORT_LABEL: Record<string, string> = {
  LOCAL_CPU: "LOCAL CPU",
  LOCAL_6GB_VERIFIED: "LOCAL GPU · VERIFIED",
  REMOTE_GPU_REQUIRED: "REMOTE GPU REQUIRED",
  NOT_YET_MEASURED: "NOT YET MEASURED",
};

export function ResourceTierBadge({ operationId }: { operationId: string }) {
  const entry = resourceTier(operationId);
  const color = COLORS[entry.tier] ?? "var(--ink-dim, #888)";
  return (
    <span
      title={entry.evidence}
      className="meta"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        border: `1px solid ${color}`,
        color,
        borderRadius: "var(--radius-sm, 4px)",
        padding: "1px 6px",
        fontSize: "0.75em",
        fontWeight: 600,
        letterSpacing: "0.02em",
        whiteSpace: "nowrap",
      }}
    >
      {SHORT_LABEL[entry.tier] ?? entry.tier.replace(/_/g, " ")}
    </span>
  );
}
