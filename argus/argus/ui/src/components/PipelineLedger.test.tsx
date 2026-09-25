import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { PIPELINE, PipelineLedger } from "./PipelineStrip";
import { Chip } from "./Status";

const html = (node: React.ReactElement) => renderToStaticMarkup(<MemoryRouter>{node}</MemoryRouter>);

describe("the inspector's stage ledger", () => {
  it("is one ordered column with a row for every stage, even with no run at all", () => {
    const out = html(<PipelineLedger run={null} />);
    expect(out).toContain('class="wb-ledger"');
    const rows = out.match(/class="wb-ledger-row"/g) ?? [];
    expect(rows.length).toBe(PIPELINE.length);
    expect(PIPELINE.length).toBe(8);
    for (const stage of PIPELINE) {
      expect(out).toContain(`data-stage="${stage.key}"`);
      expect(out).toContain(stage.label);
    }
  });

  it("never draws a horizontal card grid or scroller (the strip's cells are not reused)", () => {
    const out = html(<PipelineLedger run={null} />);
    expect(out).not.toContain("pipeline-cell");
    expect(out).not.toContain("overflow-x");
  });

  it("offers no Evidence link for a stage nothing stands behind", () => {
    const out = html(<PipelineLedger run={null} />);
    expect(out).not.toContain("Evidence →");
  });
});

describe("a status chip", () => {
  it("lets a long word wrap at word boundaries and never letter by letter", () => {
    const out = html(<Chip tone="active">labels present but projection unavailable</Chip>);
    expect(out).toContain("overflow-wrap:break-word");
    expect(out).toContain("word-break:normal");
    expect(out).not.toContain("word-break:break-all");
    expect(out).not.toContain("word-break:break-word");
  });
});
