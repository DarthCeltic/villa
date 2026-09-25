import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Disclosure } from "./Disclosure";
import "../theme/product.css";

export interface BriefItem {
  text: ReactNode;
  to?: string;
  tone?: "info" | "warn" | "bad" | "unknown" | "idle";
  title?: string;
  control?: string;
}

export function ScreenBrief({
  route,
  looking,
  ready,
  missing,
  about,
  children,
}: {
  route: string;
  looking: ReactNode;
  ready: BriefItem[];
  missing: BriefItem[];
  about?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <section className="sb" data-control={`brief.${route}`} aria-label="At a glance">
      <p className="sb-looking" data-novice="looking">
        <span className="sb-k">Looking at</span> <span className="sb-v">{looking}</span>
      </p>
      <div className="sb-col sb-ready" data-novice="ready">
        <span className="sb-k">Ready</span>
        <Items items={ready} empty="nothing is ready here yet" tone="info" />
      </div>
      <div className="sb-col sb-missing" data-novice="missing">
        <span className="sb-k">Missing or unsafe</span>
        <Items items={missing} empty="nothing recorded as missing" tone="warn" />
      </div>
      {children ? <div className="sb-mount" data-mount={`brief.${route}`}>{children}</div> : null}
      {about ? (
        <Disclosure className="sb-about" summaryClassName="sb-about-sum" summary="About this page" data-control={`brief.${route}.about`}>
          <div className="sb-about-body">{about}</div>
        </Disclosure>
      ) : null}
    </section>
  );
}

function Items({ items, empty, tone }: { items: BriefItem[]; empty: string; tone: BriefItem["tone"] }) {
  if (!items.length) return <span className="sb-empty">{empty}</span>;
  return (
    <ul className="sb-chips">
      {items.map((it, i) => {
        const body = <span className="sb-chip-text">{it.text}</span>;
        return (
          <li key={i} className="sb-chip" data-tone={it.tone ?? tone} title={it.title} data-control={it.control}>
            {it.to ? (
              <Link to={it.to} className="sb-chip-link interactive">
                {body}
              </Link>
            ) : (
              body
            )}
          </li>
        );
      })}
    </ul>
  );
}
