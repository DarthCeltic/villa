import { createElement, Fragment, useEffect, useSyncExternalStore, type ReactNode } from "react";

export type PrivateKind = string;

const KEY = "argus.publicDemo";
const listeners = new Set<() => void>();

function readUrl(): boolean | null {
  try {
    const v = new URLSearchParams(window.location.search).get("demo");
    if (v === null) return null;
    return v === "1" || v === "true" || v === "public";
  } catch {
    return null;
  }
}

function readStore(): boolean {
  try {
    return window.sessionStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

export function isPublicDemo(): boolean {
  const u = readUrl();
  return u === null ? readStore() : u;
}

export function setPublicDemo(on: boolean): void {
  try {
    if (on) window.sessionStorage.setItem(KEY, "1");
    else window.sessionStorage.removeItem(KEY);
  } catch {
  }
  try {
    const url = new URL(window.location.href);
    if (url.searchParams.has("demo")) {
      if (on) url.searchParams.set("demo", "1");
      else url.searchParams.delete("demo");
      window.history.replaceState(window.history.state, "", url.toString());
    }
  } catch {
  }
  publish();
}

function publish() {
  const on = isPublicDemo();
  try {
    if (on) document.documentElement.setAttribute("data-public-demo", "true");
    else document.documentElement.removeAttribute("data-public-demo");
  } catch {
  }
  listeners.forEach((l) => l());
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  const onPop = () => publish();
  window.addEventListener("popstate", onPop);
  return () => {
    listeners.delete(cb);
    window.removeEventListener("popstate", onPop);
  };
}

export function usePublicDemo(search?: string): boolean {
  const on = useSyncExternalStore(subscribe, isPublicDemo, () => false);
  useEffect(() => {
    const u = readUrl();
    if (u !== null) {
      try {
        if (u) window.sessionStorage.setItem(KEY, "1");
        else window.sessionStorage.removeItem(KEY);
      } catch {
      }
    }
    publish();
  }, [search]);
  return on;
}

const DRIVE_PATH = /\b[A-Za-z]:[\\/][^\s,;)"'<>]*/g;
const UNC_PATH = /\\\\[^\s\\]+\\[^\s,;)"'<>]*/g;
const HOME_PATH = /(^|[\s("'=])\/(?:home|Users)\/[^\s,;)"'<>]*/g;
export const LOCAL_PATH_MARK = "‹local path›";

export function redactLocalPaths(text: string): string {
  return text
    .replace(UNC_PATH, LOCAL_PATH_MARK)
    .replace(DRIVE_PATH, LOCAL_PATH_MARK)
    .replace(HOME_PATH, (_m, lead: string) => `${lead}${LOCAL_PATH_MARK}`);
}

export function demoText(text: string): string {
  return isPublicDemo() ? redactLocalPaths(text) : text;
}

export function Private({ kind, children }: { kind: PrivateKind; children: ReactNode }) {
  const on = usePublicDemo();
  if (on) return null;
  return createElement(Fragment, { key: kind }, children);
}
