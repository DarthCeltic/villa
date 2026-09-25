import { useCallback, useEffect, useRef, useState } from "react";
import { type Failure, type Fetched, freshness, getJson } from "./http";

export interface Polled<T> {
  data: T | null;
  at: number | null;
  ageS: number | null;
  stale: boolean | null;
  failure: Failure | null;
  loading: boolean;
  consecutiveFailures: number;
  refresh: () => void;
}

export function usePoll<T>(
  url: string,
  opts: { intervalMs?: number; staleAfterS?: number; timeoutMs?: number } = {},
): Polled<T> {
  const intervalMs = opts.intervalMs ?? 15000;
  const staleAfterS = opts.staleAfterS ?? Math.max(30, Math.round((intervalMs * 3) / 1000));

  const [data, setData] = useState<T | null>(null);
  const [at, setAt] = useState<number | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [loading, setLoading] = useState(true);
  const [fails, setFails] = useState(0);
  const [, setTick] = useState(0);

  const live = useRef(true);
  const inFlight = useRef<AbortController | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const run = useCallback(async () => {
    inFlight.current?.abort();
    const ctl = new AbortController();
    inFlight.current = ctl;
    const res: Fetched<T> = await getJson<T>(url, {
      signal: ctl.signal,
      timeoutMs: opts.timeoutMs,
    });
    if (!live.current || ctl.signal.aborted) return res;
    setLoading(false);
    if (res.ok) {
      setData(res.data);
      setAt(res.at);
      setFailure(null);
      setFails(0);
    } else if (res.kind !== "ABORTED") {
      setFailure(res);
      setFails((n) => n + 1);
    }
    return res;
  }, [url, opts.timeoutMs]);

  useEffect(() => {
    live.current = true;
    let cancelled = false;

    const schedule = (delay: number) => {
      if (cancelled) return;
      timer.current = setTimeout(cycle, delay);
    };

    const cycle = async () => {
      const res = await run();
      if (cancelled) return;
      const backoff =
        res.ok || res.kind === "ABORTED"
          ? intervalMs
          : res.retryable
            ? Math.min(intervalMs * 4, intervalMs * Math.max(1, fails + 1))
            : Math.max(intervalMs * 4, 60000);
      schedule(backoff);
    };

    cycle();
    const ticker = setInterval(() => live.current && setTick((n) => n + 1), 1000);
    return () => {
      cancelled = true;
      live.current = false;
      clearInterval(ticker);
      if (timer.current) clearTimeout(timer.current);
      inFlight.current?.abort();
    };
  }, [run, intervalMs]);

  const f = freshness(at, staleAfterS);
  return {
    data,
    at,
    ageS: f.ageS,
    stale: f.stale,
    failure,
    loading,
    consecutiveFailures: fails,
    refresh: () => void run(),
  };
}
