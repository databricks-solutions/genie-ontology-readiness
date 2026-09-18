"""Tests for the per-CUJ ``User-Agent`` on the app's Databricks API calls.

Two jobs:
  1. The contextvar-driven ``user_agent()`` / ``with_ua()`` produce the right
     phase-qualified client token.
  2. A drift guard: every outbound Databricks API call carries the token. Because
     the token rides a per-request contextvar, an untagged ``aiohttp.ClientSession``
     would fall back to aiohttp's generic default ``User-Agent`` — so we assert
     every session sets ours at the source.
"""

import pathlib
import re
import unittest

from server.user_agent import (
    PRODUCT_NAME,
    PRODUCT_VERSION,
    VALID_PHASES,
    set_product_phase,
    user_agent,
    with_ua,
)

_SERVER_DIR = pathlib.Path(__file__).resolve().parent.parent / "app" / "server"


class UserAgentTest(unittest.TestCase):
    def tearDown(self):
        set_product_phase(None)  # never leak a phase across tests

    def test_base_when_no_phase(self):
        set_product_phase(None)
        self.assertEqual(user_agent(), f"{PRODUCT_NAME}/{PRODUCT_VERSION}")

    def test_phase_qualified(self):
        for phase in ("assess", "plan", "learn"):
            set_product_phase(phase)
            self.assertEqual(user_agent(), f"{PRODUCT_NAME}-{phase}/{PRODUCT_VERSION}")

    def test_unknown_phase_degrades_to_base(self):
        set_product_phase("bogus")
        self.assertEqual(user_agent(), f"{PRODUCT_NAME}/{PRODUCT_VERSION}")


class WithUaTest(unittest.TestCase):
    def tearDown(self):
        set_product_phase(None)

    def test_sets_user_agent(self):
        set_product_phase("plan")
        self.assertEqual(with_ua()["User-Agent"], f"{PRODUCT_NAME}-plan/{PRODUCT_VERSION}")

    def test_preserves_other_headers(self):
        merged = with_ua({"Authorization": "Bearer x", "Content-Type": "application/json"})
        self.assertEqual(merged["Authorization"], "Bearer x")
        self.assertEqual(merged["Content-Type"], "application/json")
        self.assertIn("User-Agent", merged)

    def test_does_not_mutate_input(self):
        original = {"Authorization": "Bearer x"}
        with_ua(original)
        self.assertNotIn("User-Agent", original)


class SessionTaggingDriftGuard(unittest.TestCase):
    """Every aiohttp session must set our ``User-Agent``, or it uses aiohttp's default.

    The one long-lived, cross-request session (``_llm_session`` in
    routes/_shared.py) can't carry a phase as a session default because it
    outlives any single request's phase, so it is tagged per-request at its call
    sites and skipped here (detected by its ``_llm_session`` assignment).
    """

    def _server_sources(self):
        for path in _SERVER_DIR.rglob("*.py"):
            if path.name.startswith("test_") or "__pycache__" in path.parts:
                continue
            yield path

    @staticmethod
    def _call_span(text: str, open_paren_idx: int) -> str:
        """Return the text of the (...) call starting at open_paren_idx, matching
        parens so a multi-line constructor of any length is covered in full —
        rather than a fixed-size window that could truncate a valid `headers=`."""
        depth = 0
        for i in range(open_paren_idx, len(text)):
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
                if depth == 0:
                    return text[open_paren_idx : i + 1]
        return text[open_paren_idx:]

    def test_every_ephemeral_session_is_tagged(self):
        untagged = []
        for path in self._server_sources():
            text = path.read_text()
            for m in re.finditer(r"aiohttp\.ClientSession\(", text):
                line_start = text.rfind("\n", 0, m.start()) + 1
                # The assignment/preamble on the same line (e.g. `_llm_session =`).
                preamble = text[line_start : m.start()]
                call = self._call_span(text, m.end() - 1)
                if "_llm_session" in preamble:
                    continue  # persistent session: tagged at its call sites, below
                if "with_ua" not in call:
                    untagged.append(f"{path.relative_to(_SERVER_DIR)}: {preamble.strip()}aiohttp.ClientSession(...)")
        self.assertEqual(untagged, [], f"untagged aiohttp.ClientSession(s): {untagged}")

    def test_persistent_llm_session_tagged_at_call_sites(self):
        shared = (_SERVER_DIR / "routes" / "_shared.py").read_text()
        # Both the streaming POST and the serving-endpoints GET must wrap headers.
        self.assertGreaterEqual(
            shared.count("with_ua("), 2, "persistent LLM session call sites must use with_ua()"
        )


class PhaseWiringTest(unittest.TestCase):
    """The three CUJ tabs must each be wired to a router."""

    def test_all_phases_are_routed(self):
        from server.routes import _PHASE_BY_ROUTER

        phases = {phase for _router, phase in _PHASE_BY_ROUTER}
        self.assertEqual(phases, VALID_PHASES)


class PhaseDependencyPropagationTest(unittest.TestCase):
    """Regression test for the phase not reaching the endpoint's context.

    The router-level dependency sets the CUJ phase via a contextvar. FastAPI runs
    a *sync* dependency in a threadpool, so the ``set()`` mutates a copied context
    and never reaches the request task — the endpoint (and its outbound aiohttp
    calls) then read the default (base product, no phase suffix). This is the bug
    that shipped in the first UAT build: every request used the base product token
    regardless of tab. An *async* dependency runs in the request task itself and
    propagates. The unit
    tests above set the phase directly, so they could not catch this; here we
    drive a real request through a ``TestClient`` and pin the real ``_mark`` async.
    """

    def tearDown(self):
        set_product_phase(None)

    def _ua_via_dep(self, dep):
        from fastapi import APIRouter, Depends, FastAPI
        from fastapi.testclient import TestClient

        sub = APIRouter()

        @sub.get("/probe")
        async def probe():
            return {"ua": user_agent()}

        app = FastAPI()
        app.include_router(sub, dependencies=[Depends(dep)])
        with TestClient(app) as client:
            return client.get("/probe").json()["ua"]

    def test_async_dependency_propagates_phase_to_endpoint(self):
        async def mark_async() -> None:
            set_product_phase("assess")

        self.assertEqual(
            self._ua_via_dep(mark_async), f"{PRODUCT_NAME}-assess/{PRODUCT_VERSION}"
        )

    def test_real_phase_dep_is_async(self):
        """The shipped dependency must be `async def _mark` — a sync def loses the
        phase in a threadpool (the original bug)."""
        src = (_SERVER_DIR / "routes" / "__init__.py").read_text()
        self.assertRegex(
            src, r"async def _mark\b",
            "routes._phase_dep._mark must be `async def` or the phase is lost in a threadpool",
        )
        self.assertNotRegex(
            src, r"(?m)^\s*def _mark\b",
            "found a sync `def _mark` — it must be `async def _mark`",
        )


if __name__ == "__main__":
    unittest.main()
