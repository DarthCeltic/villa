"""Static guards: one per-scroll status, read by every screen; no screen derives its own."""
from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "ui" / "src"


def _read(rel: str) -> str:
    return (SRC / rel).read_text(encoding="utf-8")


def _ui_files():
    for pattern in ("screens/*.tsx", "components/**/*.tsx", "components/**/*.ts", "lib/*.ts", "lib/*.tsx"):
        for path in sorted(SRC.glob(pattern)):
            if ".test." in path.name or path.name.endswith("Fixture.ts"):
                continue
            yield path


def test_no_ui_file_computes_a_next_action_locally():
    offenders = []
    for path in _ui_files():
        text = path.read_text(encoding="utf-8")
        if re.search(r"\bfunction\s+nextAction\b|\bconst\s+nextAction\s*=|[^.\w]nextAction\s*\(", text):
            offenders.append(str(path.relative_to(SRC)))
    assert offenders == []


def test_the_universe_reads_the_servers_next_action_and_holds_none_of_the_old_rules():
    text = _read("components/ShelfUniverse.ts")
    assert "nextFromStatus" in text and "/api/scroll_status" in text
    for old_rule in ("Open the bench on this material",
                     "Continue the latest work", "Open its governed acquisition status"):
        assert old_rule not in text


def test_next_action_links_do_not_reopen_a_generic_scroll_overview():
    shelf = _read("components/ShelfCard.tsx")
    explore = _read("screens/Explore.tsx")
    assert "to={s.next.to}" in shelf
    assert "to={s.next.to}" in explore
    next_start = explore.index('className="ag-card-next-action ex-next"')
    next_end = explore.index("</Link>", next_start)
    assert "onClick" not in explore[next_start:next_end]


def test_process_screen_explains_the_selected_scroll_handoff():
    text = _read("screens/SystemScreen.tsx")
    assert "SelectedScrollHandoff" in text
    assert "/api/scroll_status" in text
    assert "What to do next for" in text
    assert "Inspect available sources" in text


def test_screens_show_the_same_status_component_for_the_selected_scroll():
    for rel in ("screens/Jobs.tsx", "screens/Evidence.tsx", "screens/SystemScreen.tsx",
                "components/GrailDiary.tsx"):
        text = _read(rel)
        assert "<ScrollStatusBar" in text, rel
        assert 'from "../components/ScrollStatusBar"' in text or 'from "./ScrollStatusBar"' in text, rel


def test_context_strip_facts_and_route_read_the_status_not_the_universe_rules():
    strip = _read("components/ContextStrip.tsx")
    assert "scrollBlocker" not in strip and "stageWord" not in strip
    assert "useScrollStatus" in strip and "stripAnswers" in strip
    facts = _read("components/ScrollFacts.tsx")
    assert "useProductState" not in facts and "scrollGate" not in facts
    assert "status.questions" in facts or "status?.questions" in facts
    route = _read("components/RouteStrip.tsx")
    assert "STAGE_ORDER" not in route and "s.local.state" not in route
    assert "scroll.status" in route


def test_review_matches_a_target_to_a_scroll_by_equality_not_substring():
    text = _read("screens/Review.tsx")
    assert "scrollOfTarget(t) === ctx.scroll" in text
    assert not re.search(r"\.includes\(\s*\(?ctx\.scroll", text)


def test_the_mode_is_in_the_context_hook_persisted_guarded_and_toggled_in_the_strip():
    ctx = _read("lib/context.ts")
    assert "export type ArgusMode" in ctx and "localStorage" in ctx
    assert re.search(r"try\s*\{[^}]*localStorage", ctx)
    assert "return { ctx, set, href, mode, setMode }" in ctx
    strip = _read("components/ContextStrip.tsx")
    assert 'data-control="context.mode"' in strip and "aria-pressed={mode === m}" in strip


def test_guided_hides_raw_receipts_behind_the_disclosure_component():
    bar = _read("components/ScrollStatusBar.tsx")
    assert 'from "./Disclosure"' in bar and "<Disclosure" in bar
    assert 'mode === "expert"' in bar


def test_the_status_stylesheet_uses_only_tokens():
    css = _read("theme/status.css")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(", css)
