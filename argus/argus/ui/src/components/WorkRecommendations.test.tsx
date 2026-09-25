import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { ScrollRecommendation } from "../lib/scrollStatus";
import { WorkRecommendations } from "./WorkRecommendations";

function rec(scroll: string, over: Partial<ScrollRecommendation> = {}): ScrollRecommendation {
  return {
    scroll, display: scroll, done: 2, total: 16, verified_outputs: 0, ready: 0, decisions: 0, blocked: 1,
    next: { label: `Next for ${scroll}`, to: `/workbench?scroll=${scroll}`, why: "", kind: "step", operator_only: false, step: "acquisition" },
    ...over,
  };
}

const render = (best: ScrollRecommendation[], complete: ScrollRecommendation[], compact = false) =>
  renderToStaticMarkup(
    <MemoryRouter>
      <WorkRecommendations best={best} complete={complete} method="m" compact={compact} />
    </MemoryRouter>,
  );

describe("WorkRecommendations (UI gate P1-4)", () => {
  it("never names a scroll as most complete only because it sorts first", () => {
    const tied = [rec("PHercFixture4"), rec("PHercFixture1"), rec("PHercFixture5")];
    const html = render([], tied);
    expect(html).not.toContain("work-recommendation.complete.PHercFixture4");
    expect(html).toContain("No scroll is further along than the others yet: each has 2 of 16 route receipts");
  });

  it("recommends a scroll that is genuinely further along, with its denominator", () => {
    const html = render([], [rec("PHercFixture2", { done: 7 }), rec("PHercFixture1"), rec("PHercFixture5")]);
    expect(html).toContain('data-control="work-recommendation.complete.PHercFixture2.open"');
    expect(html).toContain("7 of 16 route receipts complete");
    expect(html).not.toContain("work-recommendation.complete.PHercFixture1");
  });

  it("best next work needs a ready step or a waiting decision, and says which", () => {
    const html = render([rec("PHercFixture3", { ready: 1 }), rec("PHercFixture1")], []);
    expect(html).toContain("1 step ready now");
    expect(html).not.toContain("work-recommendation.best.PHercFixture1");
  });

  it("opens the scroll's identity detail, and its next step separately", () => {
    const html = render([rec("PHercFixture3", { ready: 1 })], []);
    expect(html).toContain("/explore?tab=scrolls&amp;detail=PHercFixture3&amp;scroll=PHercFixture3");
    expect(html).toContain('data-control="work-recommendation.best.PHercFixture3.next"');
  });

  it("shows one combined row when one scroll leads both lanes in the compact view", () => {
    const lead = rec("PHercFixture2", { done: 7, ready: 3, decisions: 3 });
    const html = render([lead, rec("PHercFixture1", { ready: 1 })], [lead, rec("PHercFixture1")], true);
    expect(html).toContain("Most complete, and the best next work");
    expect((html.match(/work-recommendation\.both\.PHercFixture2\.open/g) ?? []).length).toBe(1);
  });
});
