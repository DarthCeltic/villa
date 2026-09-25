import { describe, expect, it, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { ScrollStatusView } from "./ScrollStatusBar";
import { stripAnswers } from "./ContextStrip";
import { scrollFacts } from "./ScrollFacts";
import { mineAt } from "./RouteStrip";
import { resetModeForTests, setMode, useArgusContext } from "../lib/context";
import { QUESTION_ORDER } from "../lib/scrollStatus";
import { fixtureStatus, refusedStatus } from "../lib/scrollStatusFixture";

const RECEIPT = "artifacts/receipt_surface.json";

function view(node: JSX.Element): string {
  return renderToStaticMarkup(<MemoryRouter>{node}</MemoryRouter>);
}

function ModeProbe() {
  const { mode } = useArgusContext();
  return <span data-mode={mode}>{mode}</span>;
}

describe("the shared status component", () => {
  beforeEach(() => resetModeForTests());

  it("guided leads with the one next action and keeps raw receipts behind a disclosure", () => {
    const html = view(<ScrollStatusView status={fixtureStatus()} mode="guided" />);
    expect(html).toContain("Trace and segment the surface");
    expect(html).toContain("geometry can be produced");
    expect(html).toContain("step 6 of 16");
    expect(html).toContain("The whole route, its receipts and sources");
    expect(html).not.toContain(RECEIPT);
    expect(html).not.toContain("argus.core.basis_");
    expect(html).not.toContain("stage_lineage/PHerc0001.jsonl");
  });

  it("expert shows the receipts, sources and lineage inline", () => {
    const html = view(<ScrollStatusView status={fixtureStatus()} mode="expert" />);
    expect(html).toContain(RECEIPT);
    expect(html).toContain("argus.core.basis_surface_prediction");
    expect(html).toContain("stage_lineage/PHerc0001.jsonl");
    expect(html).toContain("no fiber volume registered");
    expect(html).not.toContain("The whole route, its receipts and sources");
  });

  it("shows all sixteen steps as a route in either mode", () => {
    for (const mode of ["guided", "expert"] as const) {
      const html = view(<ScrollStatusView status={fixtureStatus()} mode={mode} />);
      expect(html.match(/class="ssb-dot"/g)?.length).toBe(16);
    }
  });

  it("names a refusal and its reason rather than drawing a route", () => {
    const html = view(<ScrollStatusView status={refusedStatus()} mode="guided" />);
    expect(html).toContain("IDENTITY_UNKNOWN");
    expect(html).toContain("unknown scroll id");
    expect(html).not.toContain("ssb-route");
  });

  it("says when there is no status instead of showing an all-clear", () => {
    const html = view(<ScrollStatusView status={null} mode="guided" note="Reading this scroll's status." />);
    expect(html).toContain("Reading this scroll&#x27;s status.");
    expect(html).not.toContain("ssb-route");
  });

  it("a shared mode reaches every consumer of the context hook", () => {
    expect(view(<ModeProbe />)).toContain('data-mode="guided"');
    setMode("expert");
    expect(view(<ModeProbe />)).toContain('data-mode="expert"');
  });
});

describe("the strip, the facts and the route read the status and derive nothing", () => {
  it("the context strip words the server's stage, blocker and next action", () => {
    const a = stripAnswers("PHerc0001", fixtureStatus(), false, false, "/");
    expect(a.stage.text).toBe("step 6 of 16: step label 6");
    expect(a.blocker.text).toContain("data unavailable");
    expect(a.next).toEqual({
      label: "Trace and segment the surface", to: "/workbench?scroll=PHerc0001", why: "geometry can be produced",
    });
  });

  it("names required human decisions when there is no blocking failure", () => {
    const status = fixtureStatus({
      blocker: null,
      progress_summary: {
        done: 6, ready: 3, decisions: 2, blocked: 0, later: 4,
        optional_missing: 1, total: 16, furthest_complete: null,
        scope: "fixture scope",
      },
    });
    expect(stripAnswers("PHercFixture1", status, false, false, "/").blocker.text)
      .toBe("2 human decisions");
  });

  it("keeps route receipts separate from later evidence in the context summary", () => {
    const status = fixtureStatus({
      progress_summary: {
        done: 2, ready: 0, decisions: 0, blocked: 1, later: 13,
        optional_missing: 2, total: 16, furthest_complete: null,
        input_state: "CATALOGUED_UNSEALED", evidence_attempted: 6,
        evidence_chain_state: "VERIFIED", verified_outputs: 6,
        scope: "Route receipts and separate evidence are both visible.",
      },
    });
    expect(stripAnswers("PHercFixture1", status, false, false, "/").stage.text)
      .toBe("canonical route 2/16 receipts · 6 downstream outputs verified and viewable · 1 blocked");
  });

  it("with no status the strip says so and offers only the way back", () => {
    const a = stripAnswers("PHerc0001", null, false, false, "/shelf");
    expect(a.stage.known).toBe(false);
    expect(a.next?.to).toBe("/shelf");
    expect(stripAnswers(null, null, false, false, "/").next).toBeNull();
  });

  it("scrollFacts maps the eight answers and shows a refusal as UNKNOWN with its reason", () => {
    const facts = scrollFacts(fixtureStatus());
    expect(facts.map((f) => f.key)).toEqual(QUESTION_ORDER);
    const acquisition = facts.find((f) => f.key === "acquisition")!;
    expect(acquisition.unknown).toBe(true);
    expect(acquisition.value).toBe("UNKNOWN");
    expect(acquisition.why).toBe("declared nowhere");
    expect(facts.find((f) => f.key === "next")!.to).toBe("/workbench?scroll=PHerc0001");
  });

  it("scrollFacts asserts nothing before the status is read, and can withhold local holdings", () => {
    const unread = scrollFacts(null, { statusRead: false });
    expect(unread.every((f) => f.unknown)).toBe(true);
    const redacted = scrollFacts(fixtureStatus(), { redactLocal: true }).find((f) => f.key === "local")!;
    expect(redacted.value).toBe("withheld in public demo");
  });

  it("the route strip takes each cell's state from its journey step", () => {
    const vocab = { CT: "acquisition", Surface: "tracing", Detect: "ink_inference" };
    expect(mineAt("CT", fixtureStatus(), vocab)).toEqual({ has: true, word: "done" });
    expect(mineAt("Surface", fixtureStatus(), vocab)).toEqual({ has: null, word: "can run" });
    expect(mineAt("Detect", fixtureStatus(), vocab).has).toBe(false);
    expect(mineAt("Review", fixtureStatus(), vocab).word).toBe("not mapped to a journey step");
    expect(mineAt("CT", null, vocab).word).toBe("status not read");
    expect(mineAt("CT", refusedStatus(), vocab).word).toBe("refused: IDENTITY_UNKNOWN");
  });
});
