#!/usr/bin/env python3
"""Render the Genie Code skill's reference docs from the App's canonical spec (#108).

The skill (``skills/genie-ontology-readiness/``) stays instructions-only and
publishable on its own, but the parts that must match the App are generated
from ``app/server/pillars.py`` (weights, levels, stages, roll-up) and
``app/server/signal_spec.py`` (per-pillar SQL, score formula, gaps):

* regions between ``<!-- BEGIN GENERATED: <name> -->`` and
  ``<!-- END GENERATED: <name> -->`` are replaced wholesale;
* every pillar heading ``## N. <name> — weight <w>`` is rewritten from PILLARS.

Hand-written prose outside those regions is left alone.

Usage:
    python3 scripts/render_skill.py           # rewrite the skill docs in place
    python3 scripts/render_skill.py --check   # exit 1 if they're stale (CI gate)
"""

from __future__ import annotations

import argparse
import difflib
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "app"))

from server import pillars as P  # noqa: E402  (import-free module)
from server import signal_spec as S  # noqa: E402  (import-free module)

SKILL_DIR = REPO / "skills" / "genie-ontology-readiness"
SKILL_MD = SKILL_DIR / "SKILL.md"
SKILL_REFS = SKILL_DIR / "references"
SIGNALS_MD = SKILL_REFS / "pillar-signals.md"
SCORING_MD = SKILL_REFS / "scoring-and-output.md"

_REGION = re.compile(
    r"(<!-- BEGIN GENERATED: (?P<name>[\w-]+) -->\n)(?P<body>.*?)(<!-- END GENERATED: (?P=name) -->)",
    re.S,
)
_HEADING = re.compile(r"^## (?P<n>\d+)\. .+ — weight \d+$", re.M)

_NOTICE = "<!-- Generated from app/server by scripts/render_skill.py — edit the spec, not this block. -->\n"


def _join(items: list[str]) -> str:
    """Natural-language list: "a", "a and b", "a, b, and c"."""
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + f", and {items[-1]}"


# Markdown inline-code span. Everything this script emits is Markdown prose for
# the skill docs, never SQL, so wrapping text in backticks is presentation only.
_MD_TICK = "`"


def _code(text: str) -> str:
    """Render ``text`` as a Markdown inline-code span."""
    return _MD_TICK + text + _MD_TICK


def _ticks(template: str) -> str:
    """Show ``{placeholder}`` values as inline code, as the skill's gap lists do."""
    return re.sub(r"\{(\w+)\}", lambda m: _code(m.group(0)), template)


# --- scoring-and-output.md -------------------------------------------------

def _weights() -> str:
    rows = [f"| {p['name']} | {_code(p['key'])} | {p['weight']} |" for p in P.PILLARS]
    total = sum(p["weight"] for p in P.PILLARS)
    return "\n".join(["| Pillar | key | weight |", "|---|---|---|", *rows, "", f"Weights sum to **{total}**."]) + "\n"


def _levels() -> str:
    rows = [f"| {_code('>= ' + str(b['min_score']))} | {b['level']} | {P.LEVEL_LABELS[b['level']]} |"
            for b in P.LEVEL_BANDS]
    lowest = P.LEVEL_BANDS[-1]["min_score"]
    rows.append(f"| `> 0` and `< {lowest}` | 1 | {P.LEVEL_LABELS[1]} |")
    rows.append(f"| `0` | 0 | {P.LEVEL_LABELS[0]} |")
    return "\n".join(["| score | level | label |", "|---|---|---|", *rows,
                      "", "Take the first row that matches, top to bottom."]) + "\n"


def _stages() -> str:
    rows = [f"| {s['min_score']} | {s['label']} | {s['detail']} |" for s in P.READINESS_STAGES]
    return "\n".join(["| min_score | stage | generic detail (fallback only) |", "|---|---|---|", *rows]) + "\n"


def _top_gaps() -> str:
    return (
        "1. Rank pillars **lowest score first, then highest weight** (i.e. sort key\n"
        "   `(score ascending, weight descending)`).\n"
        "2. Walk the ranked pillars and collect each pillar's gaps in order, tagging each\n"
        f"   with its pillar name: {_code('{pillar, gap}')}.\n"
        f"3. Keep the **first {P.TOP_GAPS_LIMIT}**.\n"
    )


def _guidance() -> str:
    sentence = P.GUIDANCE_TEMPLATE
    return (
        f"- From the ranked pillars (same order as above), take the **names of up to {P.GUIDANCE_MAX_PILLARS}\n"
        "  pillars that have at least one gap** (dedup, preserve order).\n"
        f'- If any: `"{sentence}"`\n'
        f"  where {_code('{names}')} is a natural-language join — `\"A\"`, `\"A and B\"`, or\n"
        "  `\"A, B, and C\"`.\n"
        "- If **no** pillar has gaps: fall back to the selected stage's generic `detail`.\n"
    )


# Fixed illustrative pillar scores; every number below is computed with the App's
# own functions, so the example can't disagree with the scoring it illustrates.
_EXAMPLE_SCORES = {"uc_foundation": 85, "metadata": 40, "relationships": 50, "metrics": 30,
                   "genie_agents": 40, "domains": 20, "adoption": 50}
_EXAMPLE_NO_GAPS = {"adoption"}


def _example() -> str:
    pil = [{**p, "score": _EXAMPLE_SCORES[p["key"]],
            "gaps": [] if p["key"] in _EXAMPLE_NO_GAPS else ["…"]} for p in P.PILLARS]
    listing = ", ".join(f"{p['key']} {p['score']}" for p in pil)
    terms = " + ".join(f"{p['score']}*{p['weight']}" for p in pil)
    products = " + ".join(str(p["score"] * p["weight"]) for p in pil)
    weighted = sum(p["score"] * p["weight"] for p in pil)
    total_w = sum(p["weight"] for p in pil)
    overall = round(weighted / total_w, 1)
    level = P.level_from_score(overall)
    stage = P.readiness_stage(overall)
    nxt = [s["min_score"] for s in P.READINESS_STAGES if s["min_score"] > stage["min_score"]]
    bound = f">={stage['min_score']}" + (f", <{nxt[0]}" if nxt else "")
    ranked = sorted(pil, key=lambda x: (x["score"], -x["weight"]))
    order = ", ".join(f"{p['key']} ({p['score']}/{p['weight']})" for p in ranked)
    guidance = P.readiness_guidance(ranked, stage["detail"])
    with_gaps = [p["name"] for p in ranked if p["gaps"]]
    named = min(len(with_gaps), P.GUIDANCE_MAX_PILLARS)
    if not with_gaps:
        why = "No pillar has a gap here, so the guidance falls back to the stage's detail:"
    else:
        no_gap = [p["key"] for p in ranked if not p["gaps"]]
        which = f"Every pillar except {_join(no_gap)} has a gap" if no_gap else "Every pillar has a gap"
        why = f"{which} here, so the first {named} with gaps give the guidance:"
    return (
        f"Say the pillar scores come out:\n{_code(listing)}.\n\n"
        "```\n"
        f"weighted = {terms}\n"
        f"         = {products} = {weighted}\n"
        f"overall  = {weighted} / {total_w} = {overall}  -> level {level} ({P.LEVEL_LABELS[level]})\n"
        f"stage    = {overall} -> \"{stage['label']}\" ({bound})\n"
        "```\n\n"
        f"Ranked lowest→highest (score asc, weight desc): {order}.\n"
        f"{why}\n"
        f"`\"{guidance}\"`\n"
    )


# --- SKILL.md --------------------------------------------------------------

def _at_a_glance() -> str:
    weights = " · ".join(f"{p['name']} {p['weight']}" for p in P.PILLARS)
    total = sum(p["weight"] for p in P.PILLARS)
    return (
        f"- {len(P.PILLARS)} pillars, each scored 0-100. Pillar **weights sum to {total}**:\n"
        f"  {weights}.\n"
        "- **Overall** = weighted average of the pillar scores (an unavailable pillar\n"
        "  contributes 0). Map it to a maturity **level** (0-4) and a **readiness stage**.\n"
        "- **Prioritized gaps** = each pillar's gaps, ordered lowest-score / highest-weight\n"
        f"  first, top {P.TOP_GAPS_LIMIT}.\n"
    )


SKILL_REGIONS = {"at-a-glance": _at_a_glance}


# --- pillar-signals.md -----------------------------------------------------

def _pillar_block(spec: dict) -> str:
    out = ["**Signals**", "", "```sql"]
    for i, q in enumerate(spec["queries"]):
        if i:
            out.append("")
        out += [f"-- {line}" for line in q["comment"].split("\n")]
        out.append(S.render_sql(q["sql"], schema_column=q.get("schema_column", "table_schema")))
    out += ["```", ""]
    out += spec["derived"] + [""]
    out += [f"**Score:** {_code(spec['score'])}", ""]
    shown = [f"{s['label']} ({s['value']})" + (f" {s['when']}" if s.get("when") else "") for s in spec["signals"]]
    if shown:
        out += ["**Signals to display:** " + _join(shown) + ".", ""]
    out.append("**Gaps:**")
    for g in spec["gaps"]:
        cond = _code(g["when"]) + (f" ({g['when_note']})" if g.get("when_note") else "")
        out.append(f'- {cond} → "{_ticks(g["text"])}"')
    return "\n".join(out) + "\n"


SCORING_REGIONS = {
    "weights": _weights,
    "levels": _levels,
    "stages": _stages,
    "top-gaps": _top_gaps,
    "guidance": _guidance,
    "example": _example,
}
SIGNAL_REGIONS = {f"pillar-{key}": (lambda spec=spec: _pillar_block(spec)) for key, spec in S.PILLAR_SPECS.items()}


def _render(text: str, regions: dict, path: pathlib.Path) -> str:
    seen: set[str] = set()

    def sub(m: re.Match) -> str:
        name = m.group("name")
        if name not in regions:
            raise SystemExit(f"{path.name}: unknown generated region {name!r}")
        seen.add(name)
        return m.group(1) + _NOTICE + regions[name]() + m.group(4)

    text = _REGION.sub(sub, text)
    missing = set(regions) - seen
    if missing:
        raise SystemExit(f"{path.name}: missing generated region(s): {', '.join(sorted(missing))}")
    return text


def _headings(text: str) -> str:
    def sub(m: re.Match) -> str:
        n = int(m.group("n"))
        if not 1 <= n <= len(P.PILLARS):
            raise SystemExit(f"{SIGNALS_MD.name}: heading '{m.group(0)}' has no pillar #{n} "
                             f"(PILLARS has {len(P.PILLARS)})")
        p = P.PILLARS[n - 1]
        return f"## {n}. {p['name']} — weight {p['weight']}"

    return _HEADING.sub(sub, text)


def render() -> dict[pathlib.Path, str]:
    return {
        SKILL_MD: _render(SKILL_MD.read_text(encoding="utf-8"), SKILL_REGIONS, SKILL_MD),
        SCORING_MD: _render(SCORING_MD.read_text(encoding="utf-8"), SCORING_REGIONS, SCORING_MD),
        SIGNALS_MD: _headings(_render(SIGNALS_MD.read_text(encoding="utf-8"), SIGNAL_REGIONS, SIGNALS_MD)),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--check", action="store_true", help="fail if the skill docs are out of date")
    args = ap.parse_args()
    stale = []
    for path, new in render().items():
        old = path.read_text(encoding="utf-8")
        if old == new:
            continue
        stale.append(path)
        if args.check:
            sys.stdout.writelines(difflib.unified_diff(
                old.splitlines(True), new.splitlines(True),
                f"{path.relative_to(REPO)} (committed)", f"{path.relative_to(REPO)} (rendered)"))
        else:
            path.write_text(new, encoding="utf-8")
    if args.check and stale:
        print("\nSkill docs are stale — run: python3 scripts/render_skill.py", file=sys.stderr)
        return 1
    for path in stale:
        print(f"rendered {path.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
