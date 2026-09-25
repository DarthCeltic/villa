import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import { StructuredIntentConsole } from "./StructuredIntentConsole";

describe("StructuredIntentConsole", () => {
  it("states the closed-vocabulary boundary and cannot run before a deliberate session", () => {
    const html = renderToStaticMarkup(<StructuredIntentConsole />);
    expect(html).toContain("Tell ARGUS what to do");
    expect(html).toContain("closed, inspectable sentence grammar");
    expect(html).toContain("Open a governed session");
    expect(html).toContain("Preview exact plan");
    expect(html).toContain("disabled");
    expect(html).toContain("deliberately unreachable through text");
  });

  it("starts with a real supported read-only phrase, not an open-ended prompt", () => {
    const html = renderToStaticMarkup(<StructuredIntentConsole />);
    expect(html).toContain("run preflight");
    expect(html).not.toContain("Ask me anything");
  });
});
