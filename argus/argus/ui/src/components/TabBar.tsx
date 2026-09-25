import { Link, useLocation, useSearchParams } from "react-router-dom";

export interface TabDef {
  id: string;
  label: string;
  hint: string;
  extra?: Record<string, string>;
}

export function TabBar({
  tabs,
  current,
  label,
}: {
  tabs: TabDef[];
  current: string;
  label: string;
}) {
  const [params] = useSearchParams();
  const loc = useLocation();

  const href = (t: TabDef) => {
    const next = new URLSearchParams(params);
    next.set("tab", t.id);
    for (const key of ["mode"]) next.delete(key);
    for (const [k, v] of Object.entries(t.extra ?? {})) next.set(k, v);
    return `${loc.pathname}?${next.toString()}`;
  };

  return (
    <nav className="tabbar" aria-label={label}>
      {tabs.map((t) => (
        <Link
          key={t.id}
          to={href(t)}
          className="tabbar-item interactive"
          aria-current={t.id === current ? "page" : undefined}
          data-control={"tab." + t.id}
          title={t.hint}
        >
          <span className="tabbar-label">{t.label}</span>
          <span className="tabbar-hint">{t.hint}</span>
        </Link>
      ))}
    </nav>
  );
}

export function activeTab(params: URLSearchParams, tabs: TabDef[], fallback?: string): string {
  const asked = params.get("tab");
  if (asked && tabs.some((t) => t.id === asked)) return asked;
  return fallback ?? tabs[0]?.id ?? "";
}
