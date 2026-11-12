"""The Genie Code skill's generated doc regions match the App's spec (#108).

``scripts/render_skill.py`` renders weights, levels, stages, roll-up rules and
migrated pillars' signals from ``server.pillars`` / ``server.signal_spec``. If
this fails, run ``python3 scripts/render_skill.py`` and commit the result.
"""
import importlib.util
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("render_skill", REPO / "scripts" / "render_skill.py")
render_skill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(render_skill)


def test_skill_docs_are_up_to_date():
    stale = [str(p.relative_to(REPO)) for p, new in render_skill.render().items() if p.read_text(encoding="utf-8") != new]
    assert not stale, f"stale skill docs (run python3 scripts/render_skill.py): {stale}"


def test_a_spec_change_makes_the_docs_stale(monkeypatch):
    from server import pillars

    monkeypatch.setattr(pillars, "TOP_GAPS_LIMIT", 7)
    rendered = render_skill.render()
    assert rendered[render_skill.SCORING_MD] != render_skill.SCORING_MD.read_text(encoding="utf-8")


def test_every_pillar_heading_matches_pillars_py():
    from server.pillars import PILLARS

    text = render_skill.SIGNALS_MD.read_text(encoding="utf-8")
    for n, p in enumerate(PILLARS, 1):
        assert f"## {n}. {p['name']} — weight {p['weight']}\n" in text
