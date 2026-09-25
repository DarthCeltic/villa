import { afterEach, describe, expect, it, vi } from "vitest";

import { closeSession, openSession, subscribeGovernedSession } from "./governed";

afterEach(() => {
  closeSession();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("governed session bootstrap", () => {
  it("prompts once and sends the operator key only to the session endpoint", async () => {
    const changed = vi.fn();
    const unsubscribe = subscribeGovernedSession(changed);
    const prompt = vi.fn(() => "  local-operator-key  ");
    const fetch = vi.fn(async () => ({
      ok: true,
      json: async () => ({ csrf: "csrf-value", expires_in_s: 900 }),
    }));
    vi.stubGlobal("window", { prompt });
    vi.stubGlobal("fetch", fetch);

    await openSession();

    expect(prompt).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledWith("/ui/session", {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-Argus-Access-Key": "local-operator-key" },
    });
    expect(changed).toHaveBeenCalledTimes(1);
    unsubscribe();
  });

  it("does not make a request when the operator declines the key prompt", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("window", { prompt: vi.fn(() => null) });
    vi.stubGlobal("fetch", fetch);

    await expect(openSession()).rejects.toThrow("operator access key is required");
    expect(fetch).not.toHaveBeenCalled();
  });
});
