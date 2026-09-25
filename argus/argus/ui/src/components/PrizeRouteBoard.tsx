type PrizeRoute = "FIRST_LETTERS" | "GRAND_PRIZE" | "PARIS4_TITLE";

export function PrizeRouteBoard({
  scroll, compact = false,
}: {
  scroll?: string | null;
  compact?: boolean;
  lanes?: PrizeRoute[];
}) {
  return (
    <p className="gn-muted" data-control="prizeroutes.public" data-compact={compact ? "true" : undefined}>
      Prize-route boards{scroll ? ` for ${scroll}` : ""} are not part of the public release. The
      published prize rules on scrollprize.org are the authority for every prize route. A rendered
      candidate is not a reading.
    </p>
  );
}

export function PrizeRouteTargets() {
  return null;
}
