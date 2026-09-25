import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { CompareControls, RendererStatusPanel, TaskConquest, TaskFlatteningPanel } from "./ConquestPanels";

const text = (html: string) => html.replace(/<[^>]+>/g, "").replace(/\s+/g, " ");

describe("public release stubs", () => {
  it("every panel says it is not part of the public release and shows no figure", () => {
    const outputs = [
      renderToStaticMarkup(<RendererStatusPanel status={null} />),
      renderToStaticMarkup(<TaskFlatteningPanel annotation={null} />),
      renderToStaticMarkup(<CompareControls status={null} annotation={null} coordinates="unchanged" />),
      renderToStaticMarkup(<TaskConquest taskSha={"a".repeat(64)} coordinates="unchanged" />),
    ];
    for (const html of outputs) {
      expect(text(html)).toContain("not part of the public release");
      expect(html).toContain('data-state="UNAVAILABLE"');
      expect(text(html)).not.toMatch(/\d\.\d{2,}/);
    }
  });

  it("keeps the coordinates statement visible and unchanged", () => {
    const html = renderToStaticMarkup(<CompareControls status={null} annotation={null} coordinates="mesh level-0 XYZ" />);
    expect(text(html)).toContain("mesh level-0 XYZ");
  });
});
