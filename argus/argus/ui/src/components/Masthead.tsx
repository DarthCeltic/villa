import { Link } from "react-router-dom";
import type { FeedState } from "../api";

export type FeedCondition = { tone: "active" | "blocked" | "refused"; label: string };

export const FEED_STALE_AFTER_S = 8;

export function feedCondition(feed: FeedState): FeedCondition {
  if (!feed.connected) return { tone: "refused", label: "Feed unavailable" };
  if (feed.ageS > FEED_STALE_AFTER_S) return { tone: "blocked", label: "Feed stale" };
  return { tone: "active", label: "Feed live" };
}

export function Masthead({
  collectionName,
  rail = "full",
}: {
  collectionName: string;
  rail?: "full" | "collapsed" | "bar";
}) {
  const wide = rail === "full";
  const phone = rail === "bar";
  return (
    <header className="argus-masthead">
      {

}
      <Link
        to="/"
        data-control="shell.home"
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--s-4)",
          textDecoration: "none",
          color: "inherit",
          borderRadius: "var(--radius-sm)",
          minWidth: 0,
          minHeight: "var(--control-h)",
        }}
        aria-label="ARGUS, Ancient Reconstruction and Glyph Unwrapping System. Go to the home screen"
      >
        {
}
        <picture className="argus-brand-lockup">
          <source srcSet="/brand/argus-app-icon.png" />
          <img
            className="argus-brand-image"
            src="/brand/argus-mark.png"
            alt=""
            aria-hidden
            height={phone ? 34 : 58}
            style={{ height: phone ? 34 : 58, width: "auto", display: "block" }}
          />
        </picture>
        <span style={{ minWidth: 0 }}>
          <span
            className="display"
            style={{
              display: "block",
              fontSize: phone ? 20 : 30,
              letterSpacing: "0.34em",
              color: "var(--identity-ivory)",
              lineHeight: 1,
            }}
          >
            ARGUS
          </span>
          {

}
          {phone ? null : (
            <span
              className="faint"
              style={{
                display: "block",
                fontSize: "max(12px, var(--t-small))",
                letterSpacing: wide ? "0.09em" : "0.05em",
                marginTop: 9,
                whiteSpace: "nowrap",
              }}
            >
              ANCIENT RECONSTRUCTION &amp; GLYPH UNWRAPPING SYSTEM
            </span>
          )}
        </span>
      </Link>

      <div style={{ flex: 1, minWidth: 8 }} />

      {
}
      {wide ? (
        <div
          className="faint"
          style={{
            textAlign: "right",
            fontSize: "max(12px, var(--t-small))",
            letterSpacing: "0.16em",
            lineHeight: 1.6,
            whiteSpace: "nowrap",
          }}
        >
          EVERY FIGURE FROM A RECEIPT
          <br />
          EVERY REFUSAL WITH A REASON
        </div>
      ) : null}

      <div
        className="masthead-collection"
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--s-4)",
          paddingLeft: phone ? 0 : "var(--s-5)",
          borderLeft: phone ? undefined : "1px solid var(--line)",
          flexWrap: "wrap",
          minWidth: 0,
        }}
      >
        {phone ? null : (
          <span className="eyebrow" style={{ color: "var(--accent)" }}>
            {collectionName}
          </span>
        )}
        {
}
      </div>
    </header>
  );
}
