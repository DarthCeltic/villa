import {
  Compass,
  Cpu,
  Database,
  FileSearch,
  FlaskConical,
  Home as HomeIcon,
  ListChecks,
  ScanEye,
} from "lucide-react";
import { NavLink, useSearchParams } from "react-router-dom";

export interface NavItem {
  to: string;
  label: string;
  hint: string;
  id: string;
  Icon: typeof Compass;
  disabled?: string;
}

export const NAV_ITEMS: NavItem[] = [
  { to: "/", id: "home", label: "Home", hint: "the target and the next step", Icon: HomeIcon },
  { to: "/explore", id: "explore", label: "Explore", hint: "what we hold", Icon: Compass },
  { to: "/workbench", id: "work", label: "Work", hint: "operate, review and export", Icon: FlaskConical },
  { to: "/explore?tab=holdings", id: "library", label: "Library", hint: "the archive and holdings", Icon: Database },
  { to: "/system", id: "system", label: "System", hint: "capability and safety", Icon: Cpu },
];

const WORK_ITEMS: NavItem[] = [
  { to: "/workbench", id: "workbench", label: "Workbench", hint: "the selected material", Icon: FlaskConical },
  { to: "/jobs", id: "jobs", label: "Jobs", hint: "what is running now", Icon: ListChecks },
  { to: "/review", id: "review", label: "Review Lab", hint: "human decisions", Icon: ScanEye },
  { to: "/evidence", id: "evidence", label: "Evidence", hint: "reproduce the result", Icon: FileSearch },
];

export const RELOCATIONS: {
  was: string;
  now: string;
  to: string;
  legacy: string;
  redirects_to: string;
}[] = [
  {
    was: "Observatory",
    now: "Explore → Run archive",
    to: "/explore?tab=archive",
    legacy: "/observatory",
    redirects_to: "/explore",
  },
  {
    was: "Workspace",
    now: "Workbench",
    to: "/workbench",
    legacy: "/workspace",
    redirects_to: "/workbench",
  },
  { was: "Jobs", now: "Jobs", to: "/jobs", legacy: "/jobs", redirects_to: "/jobs" },
  {
    was: "Ingest",
    now: "Sources → Ingest",
    to: "/sources?tab=ingest",
    legacy: "/ingest",
    redirects_to: "/sources",
  },
  {
    was: "Collections",
    now: "Home → scroll archive collections",
    to: "/",
    legacy: "/collections",
    redirects_to: "/explore",
  },
  {
    was: "Library",
    now: "Explore → Run archive / Recovered texts; holdings in Sources",
    to: "/explore",
    legacy: "/library",
    redirects_to: "/explore",
  },
  { was: "Sources", now: "Sources", to: "/sources", legacy: "/sources", redirects_to: "/sources" },
  {
    was: "Models",
    now: "Sources → Model inventory",
    to: "/sources?tab=models",
    legacy: "/models",
    redirects_to: "/sources",
  },
  {
    was: "Workbench",
    now: "Workbench",
    to: "/workbench",
    legacy: "/workbench",
    redirects_to: "/workbench",
  },
  {
    was: "Evidence",
    now: "Evidence",
    to: "/evidence",
    legacy: "/evidence",
    redirects_to: "/evidence",
  },
  {
    was: "System",
    now: "System → Capability",
    to: "/system",
    legacy: "/system",
    redirects_to: "/system",
  },
];

export function NavRail({ mode = "full" }: { mode?: "full" | "collapsed" | "bar" }) {
  const showText = mode !== "collapsed";
  const [params] = useSearchParams();
  const scroll = params.get("scroll");

  const hrefFor = (n: NavItem) =>
    scroll ? `${n.to}${n.to.includes("?") ? "&" : "?"}scroll=${encodeURIComponent(scroll)}` : n.to;

  const linkFor = (n: NavItem, className: string, includeHint: boolean) => (
    <NavLink
      key={`${className}-${n.id}`}
      to={hrefFor(n)}
      end={n.id === "home" || n.id === "explore" || n.id === "library"}
      className={`${className} interactive`}
      data-control={`nav.${n.id}`}
      aria-label={`${n.label} — ${n.hint}`}
      title={includeHint ? undefined : `${n.label} — ${n.hint}`}
    >
      <n.Icon size={18} strokeWidth={1.6} aria-hidden />
      {includeHint ? (
        <span>
          <span className="rail-label">{n.label}</span>
          <span className="rail-hint">{n.hint}</span>
        </span>
      ) : null}
    </NavLink>
  );

  const mobileMenu = mode === "bar" ? (
    <details className="rail-mobile-menu">
      <summary className="rail-mobile-menu-toggle interactive">Menu</summary>
      <div className="rail-mobile-menu-panel" aria-label="All ARGUS destinations">
        {NAV_ITEMS.map((n) => linkFor(n, "rail-mobile-menu-item", true))}
        <div className="rail-mobile-menu-group">
          <span className="rail-mobile-menu-heading">Work</span>
          {WORK_ITEMS.map((n) => linkFor(n, "rail-mobile-menu-item rail-mobile-menu-child", false))}
        </div>
      </div>
    </details>
  ) : null;

  return (
    <nav aria-label="Primary" className="rail-nav argus-rail" data-mode={mode}>
      {mobileMenu}
      <div className="rail-list">
        {NAV_ITEMS.map((n) => (
          <div key={n.to} className="rail-group">
          {linkFor(n, "rail-item", showText)}
          {n.id === "work" && showText ? (
            <div className="rail-subnav" aria-label="Work">
              {WORK_ITEMS.map((child) => (
                <NavLink
                  key={child.to}
                  to={scroll ? `${child.to}?scroll=${encodeURIComponent(scroll)}` : child.to}
                  className="rail-subitem interactive"
                  data-control={`nav.${child.id}`}
                  aria-label={`${child.label} — ${child.hint}`}
                >
                  <span>{child.label}</span>
                </NavLink>
              ))}
            </div>
          ) : null}
          </div>
        ))}
      </div>
    </nav>
  );
}
