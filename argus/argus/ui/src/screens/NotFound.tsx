import { Link, useLocation } from "react-router-dom";
import { routes } from "../lib/nav";

const ROOMS: [string, string][] = [
  [routes.observatory(), "Observatory"],
  ["/library", "Library"],
  ["/workbench", "Workbench"],
  ["/evidence", "Evidence"],
  ["/models", "Models"],
  ["/sources", "Sources"],
  ["/collections", "Collections"],
  ["/ingest", "Ingest"],
  ["/workspace", "Workspace"],
  ["/jobs", "Jobs"],
  ["/system", "System"],
  ["/m", "Monitor (phone)"],
];

export function NotFound() {
  const loc = useLocation();
  return (
    <div style={{ padding: "26px 22px", display: "grid", gap: 16, maxWidth: 720 }}>
      <div className="panel" style={{ padding: 22, display: "grid", gap: 10 }}>
        <h2 style={{ fontSize: "var(--t-h2)", margin: 0 }}>No such room</h2>
        <p className="muted" style={{ margin: 0 }}>
          Nothing in ARGUS answers{" "}
          <span className="mono" style={{ wordBreak: "break-all" }}>
            {loc.pathname}
          </span>
          .
        </p>
        <p className="small faint" style={{ margin: 0 }}>
          The address is left as you typed it rather than rewritten to another room, so a
          broken link stays visible and reportable instead of quietly succeeding.
        </p>
      </div>
      <div style={{ display: "grid", gap: 8 }}>
        <h3 className="eyebrow">Rooms that do exist</h3>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {ROOMS.map(([to, label]) => (
            <Link
              key={to}
              to={to}
              className="panel interactive"
              style={{
                padding: "7px 13px",
                textDecoration: "none",
                color: "inherit",
                fontSize: "var(--t-small)",
              }}
            >
              {label}
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}

export default NotFound;
