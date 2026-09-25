import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { humanize, OpsFailure, OpsUnknown } from "./OpsKit";

const outsideDetails = (html: string) => html.replace(/<details[\s\S]*?<\/details>/g, "").replace(/<[^>]+>/g, "");

describe("plain words first, the route under Technical details", () => {
  it("replaces a service route with its plain name and keeps the original for the details", () => {
    const h = humanize("/api/ingest/plan has not answered yet.");
    expect(h.human).toBe("The staged-holdings plan has not answered yet.");
    expect(h.technical).toBe("/api/ingest/plan has not answered yet.");
    expect(humanize("Could not read /api/scrolls (the holdings index)").human).toBe("Could not read the scroll index (the holdings index)");
  });

  it("uses a generic phrase for a route it does not know, and touches nothing else", () => {
    expect(humanize("/api/some_new_route answered 500").human).toBe("The service answered 500");
    expect(humanize("The holdings are loading.")).toEqual({ human: "The holdings are loading.", technical: null });
  });

  it("an unavailable item shows no route outside its Technical details", () => {
    const html = renderToStaticMarkup(<OpsUnknown what="The staged holdings" why="/api/ingest/plan and /api/storage have not answered yet." />);
    expect(outsideDetails(html)).not.toMatch(/\/api\//);
    expect(html).toContain("Technical details");
    expect(html).toContain("/api/ingest/plan and /api/storage have not answered yet.");
    expect(outsideDetails(html)).toContain("not available");
  });

  it("a failed read says so in words, without the route or the transport error, and offers the details", () => {
    const failure = { kind: "http", status: 500, detail: "boom", retryable: true, message: "/api/receipts answered 500: boom" } as never;
    const html = renderToStaticMarkup(<OpsFailure what="Could not read /api/receipts" failure={failure} control="x" />);
    const visible = outsideDetails(html);
    expect(visible).not.toMatch(/\/api\//);
    expect(visible).toContain("read failed");
    expect(html).toContain("Technical details");
  });
});
