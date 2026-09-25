
import { demoText } from "./publicDemo";

export type FailureKind =
  | "TIMEOUT"
  | "ABORTED"
  | "NETWORK"
  | "NOT_FOUND"
  | "UNAUTHORIZED"
  | "REFUSED_BY_SERVICE"
  | "SERVER_ERROR"
  | "HTTP_ERROR"
  | "NOT_JSON";

export interface Ok<T> {
  ok: true;
  data: T;
  status: number;
  at: number;
}

export interface Failure {
  ok: false;
  status: number | null;
  kind: FailureKind;
  message: string;
  bodyPreview: string | null;
  retryable: boolean;
  at: number;
}

export type Fetched<T> = Ok<T> | Failure;

export const DEFAULT_TIMEOUT_MS = 12000;

const PREVIEW = 180;

function classify(status: number): { kind: FailureKind; retryable: boolean } {
  if (status === 401 || status === 403) return { kind: "UNAUTHORIZED", retryable: false };
  if (status === 404) return { kind: "NOT_FOUND", retryable: false };
  if (status === 405 || status === 423) return { kind: "REFUSED_BY_SERVICE", retryable: false };
  if (status >= 500) return { kind: "SERVER_ERROR", retryable: true };
  return { kind: "HTTP_ERROR", retryable: false };
}

export async function getJson<T>(
  url: string,
  opts: { timeoutMs?: number; signal?: AbortSignal } = {},
): Promise<Fetched<T>> {
  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const ctl = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    ctl.abort();
  }, timeoutMs);
  const onOuterAbort = () => ctl.abort();
  opts.signal?.addEventListener("abort", onOuterAbort);

  try {
    const r = await fetch(url, {
      signal: ctl.signal,
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    const text = await r.text();
    if (!r.ok) {
      const c = classify(r.status);
      let detail: string | null = null;
      try {
        const parsed = JSON.parse(text);
        detail =
          typeof parsed?.detail === "string"
            ? parsed.detail
            : typeof parsed?.error === "string"
              ? parsed.error
              : null;
      } catch {
      }
      return {
        ok: false,
        status: r.status,
        kind: c.kind,
        retryable: c.retryable,
        message: demoText(detail ?? `the service answered ${r.status} ${r.statusText || ""}`.trim()),
        bodyPreview: detail ? null : demoText(text.slice(0, PREVIEW)) || null,
        at: Date.now(),
      };
    }
    try {
      return { ok: true, data: JSON.parse(text) as T, status: r.status, at: Date.now() };
    } catch {
      return {
        ok: false,
        status: r.status,
        kind: "NOT_JSON",
        retryable: false,
        message:
          "the service answered successfully with something that is not JSON, so this " +
          "reading cannot be trusted and is not being guessed at",
        bodyPreview: demoText(text.slice(0, PREVIEW)) || null,
        at: Date.now(),
      };
    }
  } catch (e) {
    const aborted = (e as { name?: string })?.name === "AbortError";
    if (aborted && timedOut) {
      return {
        ok: false,
        status: null,
        kind: "TIMEOUT",
        retryable: true,
        message: `no answer within ${Math.round(timeoutMs / 1000)}s`,
        bodyPreview: null,
        at: Date.now(),
      };
    }
    if (aborted) {
      return {
        ok: false,
        status: null,
        kind: "ABORTED",
        retryable: false,
        message: "this read was cancelled",
        bodyPreview: null,
        at: Date.now(),
      };
    }
    return {
      ok: false,
      status: null,
      kind: "NETWORK",
      retryable: true,
      message: "the ARGUS service did not answer on this origin",
      bodyPreview: null,
      at: Date.now(),
    };
  } finally {
    clearTimeout(timer);
    opts.signal?.removeEventListener("abort", onOuterAbort);
  }
}

export function ageLabel(ageS: number | null, staleAfterS: number): string {
  if (ageS === null) return "never read";
  if (ageS <= staleAfterS) return "live";
  if (ageS < 3600) return `${Math.max(1, Math.round(ageS / 60))} min behind`;
  if (ageS < 86400) return `${Math.round(ageS / 3600)} h behind`;
  return `${Math.round(ageS / 86400)} d behind`;
}

export function freshness(at: number | null, staleAfterS: number) {
  if (!at) return { ageS: null as number | null, stale: null as boolean | null, label: "never read" };
  const ageS = Math.max(0, Math.round((Date.now() - at) / 1000));
  return { ageS, stale: ageS > staleAfterS, label: ageLabel(ageS, staleAfterS) };
}

export function failureLine(f: Failure): string {
  switch (f.kind) {
    case "TIMEOUT":
      return `${f.message}. This is the interface failing to read, not a statement about the work.`;
    case "NETWORK":
      return `${f.message}. This is the interface failing to read, not a statement about the work.`;
    case "UNAUTHORIZED":
      return `${f.message}. The door is shut, which is not the same as nothing being behind it.`;
    case "NOT_FOUND":
      return `${f.message}. Nothing is being substituted for the missing value.`;
    case "NOT_JSON":
      return f.message;
    default:
      return f.message;
  }
}
