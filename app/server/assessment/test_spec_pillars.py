"""Migrated pillars: the App's scores and gap strings stay pinned (#118, slice of #108).

Each pillar moved into ``server.signal_spec`` has:

* golden tests: a fixed set of warehouse answers and the exact score, gap and
  signal output the App produced before the move; and
* parity tests: every gap the spec's ``when`` rule selects is the gap the App emits,
  across a grid of inputs on each side of every threshold.

The fake warehouse answers each probe query by a substring of its SQL, so the same
answers drive the pre-move and post-move SQL.
"""
import asyncio
import datetime
import unittest
from unittest.mock import AsyncMock, patch

from server import signal_spec as S
from server.assessment import probes


class _Warehouse:
    """Stands in for ``execute_sql``. The first route whose predicate matches the SQL
    answers: rows, or an exception to raise. A query no route matches returns no rows
    (the probes' drill-downs)."""

    def __init__(self, *routes):
        self._routes = routes

    async def __call__(self, query, parameters=None, force_sp=False, record=True, **_client_options):
        for match, answer in self._routes:
            if match(query):
                if isinstance(answer, Exception):
                    raise answer
                return answer
        return []


def _run(probe, sources, *routes):
    with patch.object(probes, "_resolve_sources", AsyncMock(return_value=sources)), \
         patch.object(probes, "execute_sql", _Warehouse(*routes)):
        return asyncio.run(probe())


def _sources(catalogs, system_ok=True, schemas=None):
    return {"system_ok": system_ok, "catalogs": catalogs, "schemas": schemas}


def _uc_routes(schemas, total, managed, hive):
    """``hive`` is a non-UC table count, or an exception when the legacy read is refused."""
    if isinstance(hive, Exception):
        hive_answer = hive
    else:
        hive_answer = [{"non_uc": str(hive)}]
    return (
        (lambda q: "schemata" in q, [{"n": str(schemas)}]),
        (lambda q: "AS total" in q, [{"total": str(total), "managed": str(managed)}]),
        (lambda q: "hive_metastore" in q and "GROUP BY" not in q, hive_answer),
    )


def _spec_gaps(spec, vals):
    """(gap id, text) for each gap whose spec ``when`` rule holds for ``vals``. A rule over
    a value that is None (the read did not succeed) is silent, as the App treats it."""
    out = []
    for g in spec["gaps"]:
        try:
            fires = eval(g["when"], {}, dict(vals))  # trusted, repo-owned spec
        except TypeError:
            fires = False
        if fires:
            out.append((g["id"], S.gap_text(spec, g["id"], **vals)))
    return out


class UcFoundationGoldenTest(unittest.TestCase):
    """probe_uc_foundation output, pinned to the pre-move App text."""

    def test_healthy_footprint_with_legacy_tables(self):
        out = _run(probes.probe_uc_foundation, _sources(["a", "b", "c"]),
                   *_uc_routes(schemas=6, total=120, managed=60, hive=10))
        self.assertTrue(out["available"])
        self.assertEqual(out["score"], 100.0)
        self.assertEqual(out["gaps"], [
            "10 table(s) (7.7%) are still in the legacy hive_metastore (not in Unity Catalog) "
            "— migrate them into UC (see the UCX accelerator).",
        ])
        self.assertEqual([(s["label"], s["value"]) for s in out["signals"]], [
            ("Catalogs", 3), ("Schemas", 6), ("Managed", 50.0),
            ("In Unity Catalog", 92.3), ("Not in Unity Catalog", 10),
        ])

    def test_small_footprint_and_mostly_legacy(self):
        out = _run(probes.probe_uc_foundation, _sources(["a"]),
                   *_uc_routes(schemas=2, total=49, managed=0, hive=300))
        self.assertEqual(out["score"], 70.0)
        self.assertEqual(out["gaps"], [
            "Limited table footprint; broaden UC adoption beyond an initial workload.",
            "300 table(s) (86.0%) are still in the legacy hive_metastore (not in Unity Catalog) "
            "— migrate them into UC (see the UCX accelerator).",
        ])

    def test_no_catalogs_and_legacy_read_refused(self):
        out = _run(probes.probe_uc_foundation, _sources([]),
                   *_uc_routes(schemas=0, total=0, managed=0,
                               hive=RuntimeError("UC_HIVE_METASTORE_DISABLED_EXCEPTION")))
        self.assertTrue(out["available"])
        self.assertEqual(out["score"], 0.0)
        self.assertEqual(out["gaps"], [
            "No user catalogs found — Unity Catalog may not be in active use.",
            "Limited table footprint; broaden UC adoption beyond an initial workload.",
        ])
        self.assertEqual([s["label"] for s in out["signals"]], ["Catalogs", "Schemas", "Managed"])

    def test_large_footprint_with_no_legacy_tables(self):
        out = _run(probes.probe_uc_foundation, _sources(["a", "b"]),
                   *_uc_routes(schemas=5, total=60, managed=0, hive=0))
        self.assertEqual(out["score"], 100.0)
        self.assertEqual(out["gaps"], [])
        self.assertEqual([(s["label"], s["value"]) for s in out["signals"]][3:], [
            ("In Unity Catalog", 100.0), ("Not in Unity Catalog", 0),
        ])


class UcFoundationParityTest(unittest.TestCase):
    """The App emits exactly the gaps the spec's rules select, with their ids, in order."""

    def test_gap_ids_are_the_contract_ids(self):
        self.assertEqual([g["id"] for g in S.UC_FOUNDATION["gaps"]],
                         ["no_user_catalogs", "small_table_footprint", "hive_metastore_tables"])

    def test_app_gaps_match_the_spec_rules(self):
        for n_cat in (0, 1):
            for total in (0, 49, 50):
                for hive in (None, 0, 5):
                    for scoped in (False, True):
                        with self.subTest(n_cat=n_cat, total=total, hive=hive, scoped=scoped):
                            sources = _sources(["a"] * n_cat, schemas=["x"] if scoped else None)
                            routes = _uc_routes(schemas=5, total=total, managed=0,
                                                hive=hive if hive is not None else RuntimeError("refused"))
                            out = _run(probes.probe_uc_foundation, sources, *routes)
                            # A scoped run never reads the workspace's Hive footprint.
                            non_uc = None if scoped or hive is None else hive
                            vals = {"n_catalogs": n_cat, "total": total, "non_uc": non_uc,
                                    "not_uc_pct": round(100 - probes._pct(total, total + (non_uc or 0)), 1)
                                    if non_uc is not None else None}
                            expected = _spec_gaps(S.UC_FOUNDATION, vals)
                            self.assertEqual(out["gap_ids"], [gid for gid, _ in expected])
                            self.assertEqual(out["gaps"], [text for _, text in expected])


def _rel_routes(gold, constraints):
    """``constraints`` is a (pk, fk) pair, or an exception when table_constraints is refused."""
    if isinstance(constraints, Exception):
        constraint_answer = constraints
    else:
        pk, fk = constraints
        constraint_answer = [{"constraint_type": "PRIMARY KEY", "n": str(pk)},
                             {"constraint_type": "FOREIGN KEY", "n": str(fk)}]
    return (
        (lambda q: "constraint_type, COUNT(*) AS n" in q, constraint_answer),
        (lambda q: "RLIKE" in q and "GROUP BY" not in q, [{"gold": str(gold)}]),
    )


class RelationshipsGoldenTest(unittest.TestCase):
    """probe_relationships output, pinned to the pre-move App text."""

    _NO_GOLD = "No clearly-named gold/mart layer detected; Genie performs best on curated, pre-joined tables."
    _NO_FK = "No foreign-key constraints declared; PK/FK relationships let Genie infer joins reliably."

    def test_gold_layer_with_pks_and_fks(self):
        out = _run(probes.probe_relationships, _sources(["main"]), *_rel_routes(4, (3, 2)))
        self.assertEqual(out["score"], 100.0)
        self.assertEqual(out["gaps"], [])
        self.assertEqual([(s["label"], s["value"]) for s in out["signals"]],
                         [("Gold-layer tables", 4), ("Primary keys", 3), ("Foreign keys", 2)])

    def test_no_gold_layer_and_no_constraints(self):
        out = _run(probes.probe_relationships, _sources(["main"]), *_rel_routes(0, (0, 0)))
        self.assertEqual(out["score"], 0.0)
        self.assertEqual(out["gaps"], [self._NO_GOLD, self._NO_FK])

    def test_constraints_unreadable_scores_the_gold_layer_only(self):
        out = _run(probes.probe_relationships, _sources(["main"]),
                   *_rel_routes(2, RuntimeError("table_constraints not granted")))
        self.assertEqual(out["score"], 50.0)
        self.assertEqual(out["gaps"], [])
        self.assertEqual(out["note"],
                         "Constraint metadata not available; relationship score is based on the gold layer only.")
        self.assertEqual([s["label"] for s in out["signals"]], ["Gold-layer tables"])

    def test_primary_keys_without_foreign_keys(self):
        out = _run(probes.probe_relationships, _sources(["main"]), *_rel_routes(2, (2, 0)))
        self.assertEqual(out["score"], 65.0)
        self.assertEqual(out["gaps"], [self._NO_FK])


class RelationshipsParityTest(unittest.TestCase):
    """The App emits exactly the gaps the spec's rules select, with their ids, in order."""

    def test_gap_ids_are_the_contract_ids(self):
        self.assertEqual([g["id"] for g in S.REL_MODELING["gaps"]], ["no_gold_layer", "no_fk_constraints"])

    def test_app_gaps_match_the_spec_rules(self):
        for gold in (0, 2):
            for constraints in ((0, 0), (3, 0), (0, 2), (3, 2), None):  # None: unreadable
                with self.subTest(gold=gold, constraints=constraints):
                    out = _run(probes.probe_relationships, _sources(["main"]),
                               *_rel_routes(gold, constraints if constraints is not None
                                            else RuntimeError("refused")))
                    available = constraints is not None
                    pk, fk = constraints or (0, 0)
                    vals = {"gold_tables": gold, "constraints_available": available, "pk": pk, "fk": fk}
                    expected = _spec_gaps(S.REL_MODELING, vals)
                    self.assertEqual(out["gap_ids"], [gid for gid, _ in expected])
                    self.assertEqual(out["gaps"], [text for _, text in expected])


def _metric_routes(count, commented):
    """``count`` / ``commented`` are counts, or an exception when that read is refused."""
    def answer(v):
        return v if isinstance(v, Exception) else [{"c": str(v)}]
    return (
        (lambda q: "table_type = '" in q and "comment" not in q, answer(count)),
        (lambda q: "comment IS NOT NULL" in q and "GROUP BY" not in q, answer(commented)),
    )


class MetricsGoldenTest(unittest.TestCase):
    """probe_metrics output, pinned to the pre-move App text."""

    _NO_MV = ("No metric views found. Metric views are the GA foundation that feeds Genie Ontology — "
              "define KPIs centrally here.")
    _FEW = "Few metric views; expand coverage so common KPIs are centrally defined and certified."

    @staticmethod
    def _uncommented(n):
        return (f"{n} metric view(s) lack a description — Genie reads metric-view, dimension, and measure "
                "comments to reason; add them.")

    def test_no_metric_views(self):
        out = _run(probes.probe_metrics, _sources(["main"]), *_metric_routes(0, 0))
        self.assertEqual(out["score"], 0.0)
        self.assertEqual(out["gaps"], [self._NO_MV])
        self.assertEqual([s["label"] for s in out["signals"]], ["Metric views"])

    def test_some_metric_views_one_described(self):
        out = _run(probes.probe_metrics, _sources(["main"]), *_metric_routes(4, 1))
        self.assertEqual(out["score"], 53.5)
        self.assertEqual(out["gaps"], [self._uncommented(3)])
        self.assertEqual([(s["label"], s["value"]) for s in out["signals"]],
                         [("Metric views", 4), ("Commented", 25.0)])

    def test_few_metric_views_all_described(self):
        out = _run(probes.probe_metrics, _sources(["main"]), *_metric_routes(2, 2))
        self.assertEqual(out["score"], 68.0)
        self.assertEqual(out["gaps"], [self._FEW])

    def test_mature_semantic_layer(self):
        out = _run(probes.probe_metrics, _sources(["main"]), *_metric_routes(12, 12))
        self.assertEqual(out["score"], 100.0)
        self.assertEqual(out["gaps"], [])

    def test_commented_read_refused_counts_as_undescribed(self):
        out = _run(probes.probe_metrics, _sources(["main"]), *_metric_routes(5, RuntimeError("refused")))
        self.assertEqual(out["score"], 50.0)
        self.assertEqual(out["gaps"], [self._uncommented(5)])

    def test_metric_view_type_is_unrecognised(self):
        out = _run(probes.probe_metrics, _sources(["main"]),
                   *_metric_routes(RuntimeError("no such type"), 0))
        self.assertFalse(out["available"])
        self.assertEqual(out["gaps"], [])


    def test_runtime_that_spells_the_type_with_a_space(self):
        """Some runtimes report the type as 'METRIC VIEW'. The App retries with that spelling,
        and the warehouse must receive that spelling in both the count and the commented read."""
        seen = []

        async def warehouse(query, parameters=None, force_sp=False, record=True):
            seen.append(query)
            if "table_type = 'METRIC_VIEW'" in query:
                raise RuntimeError("no such type")
            if "table_type = 'METRIC VIEW'" in query and "comment IS NOT NULL" in query and "GROUP BY" not in query:
                return [{"c": "1"}]
            if "table_type = 'METRIC VIEW'" in query and "GROUP BY" not in query:
                return [{"c": "4"}]
            return []

        with patch.object(probes, "_resolve_sources", AsyncMock(return_value=_sources(["main"]))), \
             patch.object(probes, "execute_sql", warehouse):
            out = asyncio.run(probes.probe_metrics())
        self.assertEqual(out["score"], 53.5)
        self.assertEqual(out["gaps"], [self._uncommented(3)])
        self.assertTrue(any("table_type = 'METRIC VIEW'" in q and "comment IS NOT NULL" in q and "GROUP BY" not in q
                            for q in seen))
        self.assertFalse(any("table_type = 'METRIC_VIEW'" in q and "comment IS NOT NULL" in q for q in seen))


class MetricsParityTest(unittest.TestCase):
    """The App emits exactly the gaps the spec's rules select, with their ids, in order."""

    def test_app_gaps_match_the_spec_rules(self):
        for mv in (0, 1, 2, 3, 12):
            for commented in sorted({0, mv // 2, mv}):
                with self.subTest(mv=mv, commented=commented):
                    out = _run(probes.probe_metrics, _sources(["main"]), *_metric_routes(mv, commented))
                    vals = {"metric_views": mv, "commented": commented, "uncommented": mv - commented}
                    expected = _spec_gaps(S.METRICS, vals)
                    self.assertEqual(out["gap_ids"], [gid for gid, _ in expected])
                    self.assertEqual(out["gaps"], [text for _, text in expected])

    def test_gap_ids_are_the_contract_ids_plus_the_empty_case(self):
        # The contract names few_metric_views and metric_views_uncommented. The zero-view
        # case has its own App text, so it needs its own id (flagged in the PR).
        self.assertEqual([g["id"] for g in S.METRICS["gaps"]],
                         ["no_metric_views", "few_metric_views", "metric_views_uncommented"])


def _genie_counts(total, active, users=0, questions=0, recency_days=None, dashboard=0):
    """One audit_counts row. ``recency_days`` is turned into a ``last_active_date``
    relative to today so the probe's derived recency is deterministic."""
    last = (None if recency_days is None
            else (datetime.date.today() - datetime.timedelta(days=recency_days)).isoformat())
    return {"total": str(total), "active_30d": str(active), "distinct_users_30d": str(users),
            "questions_30d": str(questions), "last_active_date": last,
            "dashboard_embedded": str(dashboard)}


def _genie_routes(counts):
    """``counts`` is an audit_counts row dict (see ``_genie_counts``), or an exception
    when the audit read is refused. The per-agent drill-down and curation reads return
    nothing in these unit runs (no row matches, no standalone agents to inspect)."""
    answer = counts if isinstance(counts, Exception) else [counts]
    return ((lambda q: "AS total" in q and "FROM standalone" in q, answer),)


class GenieAgentsGoldenTest(unittest.TestCase):
    """probe_genie_agents output, pinned for the graded real-usage score (#24)."""

    _NONE = ("No standalone Genie Agents found in the audit log — create a curated Genie Agent "
             "as the entry point to natural-language analytics.")

    def test_curated_and_heavily_used(self):
        out = _run(probes.probe_genie_agents, _sources(["main"]),
                   *_genie_routes(_genie_counts(10, 8, users=30, questions=400, recency_days=2, dashboard=2)))
        self.assertEqual(out["score"], 100.0)
        self.assertEqual(out["gaps"], [])
        self.assertEqual([(s["label"], s["value"]) for s in out["signals"]],
                         [("Genie Agents (standalone)", 10), ("Active agents (30d)", 8),
                          ("Users (30d)", 30), ("Questions (30d)", 400), ("Dashboard-embedded", 2)])

    def test_no_standalone_agents(self):
        # Every agent is dashboard-embedded: no standalone population to score.
        out = _run(probes.probe_genie_agents, _sources(["main"]),
                   *_genie_routes(_genie_counts(0, 0, dashboard=3)))
        self.assertEqual(out["score"], 0.0)
        self.assertEqual(out["gaps"], [self._NONE])
        self.assertEqual(out["metrics"]["dashboard_embedded"], 3)

    def test_agents_exist_but_none_active(self):
        out = _run(probes.probe_genie_agents, _sources(["main"]),
                   *_genie_routes(_genie_counts(5, 0, recency_days=45)))
        self.assertEqual(out["score"], 15.0)  # existence only; no real-usage bands reached
        self.assertEqual(out["gaps"], [
            "5 standalone Genie Agent(s) exist but none had a conversation in the last 30 days "
            "— drive adoption or retire stale agents.",
        ])

    def test_moderate_usage(self):
        out = _run(probes.probe_genie_agents, _sources(["main"]),
                   *_genie_routes(_genie_counts(4, 2, users=6, questions=30, recency_days=10)))
        self.assertEqual(out["score"], 71.0)
        self.assertEqual(out["gaps"], [])

    def test_low_activity_share(self):
        out = _run(probes.probe_genie_agents, _sources(["main"]),
                   *_genie_routes(_genie_counts(10, 2, users=3, questions=15, recency_days=5)))
        self.assertEqual(out["score"], 53.0)
        self.assertEqual(out["gaps"], [])

    def test_audit_read_refused(self):
        out = _run(probes.probe_genie_agents, _sources(["main"]),
                   *_genie_routes(RuntimeError("PERMISSION_DENIED on system.access.audit")))
        self.assertFalse(out["available"])
        self.assertEqual(out["gaps"], [])
        self.assertEqual(out["unavailable_reason"], "insufficient_permission")

    def test_score_is_invariant_to_curation(self):
        # The non-scored-curation guarantee (#24): flipping readability and changing the
        # per-agent curation counts must leave the numeric score (and gaps) unchanged.
        counts = _genie_counts(4, 2, users=6, questions=30, recency_days=10)
        rich = [{"title": "A", "readable": True, "type": "standalone", "instructions": 99,
                 "sample_questions": 99, "example_sqls": 99, "functions": 99, "benchmarks": 99, "tables": 99}]
        degraded = [{"title": "A", "readable": False, "type": "standalone", "instructions": None,
                     "sample_questions": None, "example_sqls": None, "functions": None,
                     "benchmarks": None, "tables": None}]

        def run_with(curation):
            with patch.object(probes, "_genie_curation", AsyncMock(return_value=curation)):
                return _run(probes.probe_genie_agents, _sources(["main"]), *_genie_routes(counts))

        out_rich, out_degraded = run_with(rich), run_with(degraded)
        self.assertEqual(out_rich["score"], out_degraded["score"])
        self.assertEqual(out_rich["score"], 71.0)  # the usage-only score, unaffected by curation
        self.assertEqual(out_rich["gaps"], out_degraded["gaps"])
        # Curation still rides along in metrics for the UI, just never in the score.
        self.assertEqual(out_rich["metrics"]["spaces"], rich)
        self.assertEqual(out_degraded["metrics"]["spaces"], degraded)


class GenieUsageBandsTest(unittest.TestCase):
    """The graded score is a deterministic sum of existence + real-usage bands, capped 0..100."""

    def test_existence_floor_and_cap(self):
        # A standalone agent with no measurable usage scores exactly the existence floor.
        self.assertEqual(S.genie_usage_score(1, 0, 0, 0, None), S.GENIE_EXISTENCE_POINTS)
        # Nothing exceeds 100 even with every band maxed.
        self.assertEqual(S.genie_usage_score(10, 10, 999, 9999, 0), 100.0)
        # No standalone agents → 0, regardless of other inputs.
        self.assertEqual(S.genie_usage_score(0, 0, 50, 50, 0), 0.0)

    def test_each_band_adds_its_highest_reached_tier(self):
        base = S.GENIE_EXISTENCE_POINTS
        # active share: 1/10 = 0.1 reaches only the first share tier.
        self.assertEqual(S.genie_usage_score(10, 1, 0, 0, None), base + S.GENIE_SHARE_BANDS[0][1])
        # distinct users: 5 reaches the second user tier.
        self.assertEqual(S.genie_usage_score(1, 0, 5, 0, None), base + S.GENIE_USER_BANDS[1][1])
        # questions: 100 reaches the top question tier.
        self.assertEqual(S.genie_usage_score(1, 0, 0, 100, None), base + S.GENIE_QUESTION_BANDS[2][1])

    def test_recency_fresher_scores_higher_and_unknown_scores_zero(self):
        self.assertEqual(S.genie_recency_points(1), 15.0)   # within 7d
        self.assertEqual(S.genie_recency_points(10), 10.0)  # within 14d
        self.assertEqual(S.genie_recency_points(25), 5.0)   # within 30d
        self.assertEqual(S.genie_recency_points(45), 0.0)   # stale
        self.assertEqual(S.genie_recency_points(None), 0.0)


class GenieClassificationTest(unittest.TestCase):
    """The audit query classifies dashboard-embedded agents out of the scored standalone
    population and counts them separately (#24). Skipped when duckdb is not installed."""

    def _audit_table(self):
        try:
            import duckdb
        except ImportError:  # pragma: no cover - requirements-dev.txt installs it
            self.skipTest("duckdb not installed")
        con = duckdb.connect()
        # The spec SQL uses Databricks' get_json_object(json, '$.path'); map it to DuckDB's
        # json_extract_string (identical JSONPath) so the shared SQL runs unchanged here.
        con.execute("CREATE OR REPLACE MACRO get_json_object(j, p) AS json_extract_string(j, p)")
        con.execute("CREATE TABLE audit (event_date DATE, service_name VARCHAR, action_name VARCHAR, "
                    "request_params STRUCT(space_id VARCHAR, display_name VARCHAR), "
                    "user_identity STRUCT(email VARCHAR), response STRUCT(result VARCHAR), workspace_id BIGINT)")
        con.execute(
            "INSERT INTO audit VALUES "
            # standalone, active: two distinct users ask questions in the last 30 days
            "(current_date() - 3, 'aibiGenie', 'createConversationMessage', {'space_id': 'fresh', 'display_name': 'Fresh'}, {'email': 'alice@example.com'}, {'result': NULL}, 1), "
            "(current_date() - 3, 'aibiGenie', 'genieStartConversationMessage', {'space_id': 'fresh', 'display_name': 'Fresh'}, {'email': 'bob@example.com'}, {'result': NULL}, 1), "
            # standalone, stale: a conversation 60 days ago (counted, not active)
            "(current_date() - 60, 'aibiGenie', 'createConversationMessage', {'space_id': 'stale', 'display_name': 'Stale'}, {'email': 'alice@example.com'}, {'result': NULL}, 1), "
            # trashed: excluded entirely
            "(current_date() - 5, 'aibiGenie', 'trashSpace', {'space_id': 'gone', 'display_name': NULL}, {'email': 'alice@example.com'}, {'result': NULL}, 1), "
            # polling noise with the 'new' placeholder id: not an agent
            "(current_date() - 2, 'aibiGenie', 'genieGetSpace', {'space_id': 'new', 'display_name': NULL}, {'email': 'svc@example.com'}, {'result': NULL}, 1), "
            # dashboard-embedded: conversation activity AND space_id seen in a dashboard-genie response
            "(current_date() - 2, 'aibiGenie', 'createConversationMessage', {'space_id': 'dash', 'display_name': 'Dash'}, {'email': 'carol@example.com'}, {'result': NULL}, 1), "
            "(current_date() - 1, 'aibiGenie', 'getDashboardGenie', {'space_id': 'wrap', 'display_name': NULL}, {'email': 'carol@example.com'}, {'result': '{\"genie_space_id\":\"dash\"}'}, 1)")
        return con

    def test_dashboard_embedded_excluded_from_scored_population(self):
        con = self._audit_table()
        sql = S.render_sql(S.GENIE_AGENTS["queries_by_id"]["audit_counts"]["sql"], workspace_filter="")
        sql = sql.replace("system.access.audit", "audit").rstrip(";")
        cols = [d[0] for d in con.execute(sql).description]
        row = dict(zip(cols, con.execute(sql).fetchone()))
        # 'dash' is dashboard-embedded (space_id in a dashboard-genie response.result) so it
        # leaves the standalone population; 'wrap' is a standalone (non-conversation) agent.
        self.assertEqual(row["total"], 3)             # fresh + stale + wrap
        self.assertEqual(row["active_30d"], 1)        # only fresh had a conversation in 30d
        self.assertEqual(row["questions_30d"], 2)     # fresh's two conversation messages
        self.assertEqual(row["distinct_users_30d"], 2)  # alice@example.com + bob@example.com
        self.assertEqual(row["dashboard_embedded"], 1)  # 'dash', counted separately

    def test_classification_is_exact_match_not_substring(self):
        # Regression (#24 review): the classifier parses the space_id out of the dashboard
        # response JSON and matches on EXACT equality, so an id that is a substring of
        # another id, or that appears incidentally in unrelated result text, is NOT
        # mis-marked dashboard-embedded (which would silently drop it from the score).
        try:
            import duckdb
        except ImportError:  # pragma: no cover - requirements-dev.txt installs it
            self.skipTest("duckdb not installed")
        con = duckdb.connect()
        con.execute("CREATE OR REPLACE MACRO get_json_object(j, p) AS json_extract_string(j, p)")
        con.execute("CREATE TABLE audit (event_date DATE, service_name VARCHAR, action_name VARCHAR, "
                    "request_params STRUCT(space_id VARCHAR, display_name VARCHAR), "
                    "user_identity STRUCT(email VARCHAR), response STRUCT(result VARCHAR), workspace_id BIGINT)")
        con.execute(
            "INSERT INTO audit VALUES "
            # two standalone agents whose ids overlap: 'abc' is a substring of 'abcd'
            "(current_date() - 2, 'aibiGenie', 'createConversationMessage', {'space_id': 'abc', 'display_name': 'ABC'}, {'email': 'alice@example.com'}, {'result': NULL}, 1), "
            "(current_date() - 2, 'aibiGenie', 'createConversationMessage', {'space_id': 'abcd', 'display_name': 'ABCD'}, {'email': 'bob@example.com'}, {'result': NULL}, 1), "
            # a dashboard-genie row whose parsed space_id is EXACTLY 'abc'; its result text
            # also mentions 'abcd' as a substring, which must NOT classify 'abcd'.
            "(current_date() - 1, 'aibiGenie', 'getDashboardGenie', {'space_id': 'wrap', 'display_name': NULL}, {'email': 'alice@example.com'}, {'result': '{\"space_id\":\"abc\",\"note\":\"linked from abcd dashboard\"}'}, 1)")
        sql = S.render_sql(S.GENIE_AGENTS["queries_by_id"]["audit_counts"]["sql"], workspace_filter="")
        sql = sql.replace("system.access.audit", "audit").rstrip(";")
        cols = [d[0] for d in con.execute(sql).description]
        row = dict(zip(cols, con.execute(sql).fetchone()))
        # Only 'abc' is dashboard-embedded; 'abcd' (and the standalone 'wrap') stay scored.
        self.assertEqual(row["dashboard_embedded"], 1)
        self.assertEqual(row["total"], 2)  # abcd + wrap remain standalone; abc is excluded
        self.assertEqual(row["active_30d"], 1)  # only abcd had a conversation in 30d


class GenieAgentsParityTest(unittest.TestCase):
    """The App emits exactly the gaps the spec's rules select, with their ids, in order."""

    def test_gap_ids_are_the_contract_ids(self):
        self.assertEqual([g["id"] for g in S.GENIE_AGENTS["gaps"]],
                         ["no_genie_agents", "no_active_genie_agents"])

    def test_app_gaps_match_the_spec_rules(self):
        for total in (0, 1, 5, 10):
            for active in sorted({0, total // 2, total}):
                with self.subTest(total=total, active=active):
                    out = _run(probes.probe_genie_agents, _sources(["main"]),
                               *_genie_routes(_genie_counts(total, active)))
                    expected = _spec_gaps(S.GENIE_AGENTS, {"total": total, "active_30d": active})
                    self.assertEqual(out["gap_ids"], [gid for gid, _ in expected])
                    self.assertEqual(out["gaps"], [text for _, text in expected])


class GenieCurationTest(unittest.IsolatedAsyncioTestCase):
    """Best-effort per-agent curation (#24): populated where the serialized space is
    readable, and degraded to a clear 'not readable' row — NEVER 0 — otherwise. Curation
    never affects the numeric score."""

    async def test_readable_agent_gets_counts(self):
        with patch.object(probes, "get_workspace_host", lambda: "https://ws"), \
             patch.object(probes, "get_auth_headers", lambda force_sp=False: {"Authorization": "Bearer x"}), \
             patch.object(probes, "_inspect_space",
                          AsyncMock(return_value=("ok", {"title": "Sales", "instructions": 3,
                                                         "sample_questions": 4, "example_sqls": 2,
                                                         "functions": 0, "benchmarks": 1, "tables": 5}))):
            spaces = await probes._genie_curation([{"space_id": "s1", "agent": "Sales"}])
        self.assertEqual(spaces, [{"title": "Sales", "readable": True, "type": "standalone",
                                   "instructions": 3, "sample_questions": 4, "example_sqls": 2,
                                   "functions": 0, "benchmarks": 1, "tables": 5}])

    async def test_unreadable_agent_degrades_to_not_assessed_not_zero(self):
        with patch.object(probes, "get_workspace_host", lambda: "https://ws"), \
             patch.object(probes, "get_auth_headers", lambda force_sp=False: {"Authorization": "Bearer x"}), \
             patch.object(probes, "_inspect_space", AsyncMock(return_value=("forbidden", None))):
            spaces = await probes._genie_curation([{"space_id": "s1", "agent": "Gated"}])
        row = spaces[0]
        self.assertFalse(row["readable"])
        self.assertEqual(row["title"], "Gated")
        # The curation counts are None ("not assessed"), never 0 — a visibility limit must
        # not read as poor curation.
        for key in ("instructions", "sample_questions", "example_sqls", "functions", "benchmarks", "tables"):
            self.assertIsNone(row[key])

    async def test_no_host_degrades_without_network(self):
        with patch.object(probes, "get_workspace_host", lambda: ""), \
             patch.object(probes, "_inspect_space", AsyncMock(side_effect=AssertionError("must not fetch"))):
            spaces = await probes._genie_curation([{"space_id": "s1", "agent": "Nameless"}])
        self.assertEqual(len(spaces), 1)
        self.assertFalse(spaces[0]["readable"])

    async def test_transient_error_degrades_to_not_assessed(self):
        with patch.object(probes, "get_workspace_host", lambda: "https://ws"), \
             patch.object(probes, "get_auth_headers", lambda force_sp=False: {"Authorization": "Bearer x"}), \
             patch.object(probes, "_inspect_space", AsyncMock(return_value=("error", None))):
            spaces = await probes._genie_curation([{"space_id": "s1", "agent": "Flaky"}])
        self.assertFalse(spaces[0]["readable"])
        self.assertIsNone(spaces[0]["instructions"])


class GenieSerializedParseTest(unittest.TestCase):
    """The serialized-space parser degrades on a missing/malformed payload (the real
    graceful-degradation path, not a mocked happy-path _inspect_space)."""

    def test_missing_serialized_space_is_not_readable(self):
        status, data = probes._serialized_counts({"title": "No payload"}, "s1", "No payload")
        self.assertEqual((status, data), ("forbidden", None))

    def test_malformed_serialized_json_is_not_readable(self):
        status, data = probes._serialized_counts({"serialized_space": "{not valid json"}, "s1", "Bad")
        self.assertEqual((status, data), ("forbidden", None))

    def test_empty_body_is_not_readable(self):
        self.assertEqual(probes._serialized_counts({}, "s1", "x"), ("forbidden", None))

    def test_serialized_space_as_json_string_is_parsed(self):
        body = {"title": "Sales",
                "serialized_space": '{"instructions": {"text_instructions": ["a", "b"], '
                                    '"example_question_sqls": ["q"]}, "data_sources": {"tables": ["t1", "t2", "t3"]}}'}
        status, counts = probes._serialized_counts(body, "s1", "fallback")
        self.assertEqual(status, "ok")
        self.assertEqual(counts["title"], "Sales")
        self.assertEqual(counts["instructions"], 2)
        self.assertEqual(counts["example_sqls"], 1)
        self.assertEqual(counts["tables"], 3)
        self.assertEqual(counts["benchmarks"], 0)  # absent path counts 0, not an error

    def test_serialized_space_as_dict_is_parsed(self):
        body = {"serialized_space": {"config": {"sample_questions": ["q1", "q2"]}}}
        status, counts = probes._serialized_counts(body, "s1", "fallback")
        self.assertEqual(status, "ok")
        self.assertEqual(counts["title"], "fallback")  # no title in body → fall back to the passed title
        self.assertEqual(counts["sample_questions"], 2)


def _adoption_routes(users, queries):
    """``users`` / ``queries`` are the 30-day counts, or an exception when that read is refused."""
    def answer(v):
        return v if isinstance(v, Exception) else [{"c": str(v)}]
    return (
        (lambda q: "user_identity.email" in q and "GROUP BY" not in q, answer(users)),
        (lambda q: "system.query.history" in q and "GROUP BY" not in q, answer(queries)),
    )


class AdoptionGoldenTest(unittest.TestCase):
    """probe_adoption output, pinned to the pre-move App text. This pillar emits no gap text."""

    def test_busy_workspace(self):
        out = _run(probes.probe_adoption, _sources(["main"]), *_adoption_routes(60, 10))
        self.assertEqual(out["score"], 100.0)
        self.assertEqual(out["gaps"], [])
        self.assertEqual([(s["label"], s["value"]) for s in out["signals"]],
                         [("Active users (30d)", 60), ("Queries (30d)", 10)])

    def test_no_activity(self):
        out = _run(probes.probe_adoption, _sources(["main"]), *_adoption_routes(0, 0))
        self.assertEqual(out["score"], 0.0)
        self.assertEqual([(s["label"], s["value"]) for s in out["signals"]],
                         [("Active users (30d)", 0), ("Queries (30d)", 0)])

    def test_users_without_queries(self):
        out = _run(probes.probe_adoption, _sources(["main"]),
                   *_adoption_routes(7, RuntimeError("PERMISSION_DENIED on system.query.history")))
        self.assertEqual(out["score"], 35.0)
        self.assertEqual([s["label"] for s in out["signals"]], ["Active users (30d)"])

    def test_queries_without_user_read(self):
        out = _run(probes.probe_adoption, _sources(["main"]),
                   *_adoption_routes(RuntimeError("PERMISSION_DENIED"), 3))
        self.assertEqual(out["score"], 50.0)
        self.assertEqual([s["label"] for s in out["signals"]], ["Queries (30d)"])

    def test_user_band_edges(self):
        # Points by active users: 1-4 -> 20, 5-19 -> 35, 20-49 -> 45, 50+ -> 50 (queries add 50 when > 0).
        for users, band in ((1, 20), (4, 20), (5, 35), (19, 35), (20, 45), (49, 45), (50, 50)):
            with self.subTest(users=users):
                out = _run(probes.probe_adoption, _sources(["main"]), *_adoption_routes(users, 0))
                self.assertEqual(out["score"], float(band))

    def test_both_reads_refused_as_not_enabled(self):
        out = _run(probes.probe_adoption, _sources(["main"]),
                   *_adoption_routes(RuntimeError("no such table"), RuntimeError("no such table")))
        self.assertFalse(out["available"])
        self.assertEqual(out["unavailable_reason"], "not_enabled")

    def test_both_reads_not_found_read_as_a_missing_grant(self):
        # The App counts "not found" on a system table as a missing grant, not as not enabled.
        out = _run(probes.probe_adoption, _sources(["main"]),
                   *_adoption_routes(RuntimeError("table or view not found"), RuntimeError("table or view not found")))
        self.assertFalse(out["available"])
        self.assertEqual(out["unavailable_reason"], "insufficient_permission")


class AdoptionParityTest(unittest.TestCase):
    """Adoption emits no gap text, so the App's gap list and gap ids are empty."""

    def test_no_gap_ids_in_the_spec(self):
        self.assertEqual(S.ADOPTION["gaps"], [])

    def test_app_emits_no_gaps(self):
        for users in (None, 0, 3, 60):
            for queries in (None, 0, 5):
                with self.subTest(users=users, queries=queries):
                    routes = _adoption_routes(RuntimeError("refused") if users is None else users,
                                              RuntimeError("refused") if queries is None else queries)
                    out = _run(probes.probe_adoption, _sources(["main"]), *routes)
                    self.assertEqual(out["gaps"], [])
                    self.assertEqual(out["gap_ids"], [])


class GenieAuditBudgetTest(unittest.TestCase):
    """The 90-day audit scan gets a longer poll budget, and a timeout isn't reported
    as a missing grant (live finding while merging #119 after #128)."""

    def test_audit_queries_get_the_long_budget(self):
        execute = AsyncMock(return_value=[{"total": 3, "active_30d": 1}])
        with patch.object(probes, "execute_sql", execute):
            asyncio.run(probes._genie_audit_counts())
            asyncio.run(probes._genie_audit_rows())
        budgets = [c.kwargs.get("poll_seconds") for c in execute.await_args_list]
        self.assertEqual(budgets, [probes._GENIE_AUDIT_POLL_SECONDS] * 2)
        self.assertGreater(probes._GENIE_AUDIT_POLL_SECONDS, 180)

    def test_a_timeout_is_not_reported_as_a_missing_grant(self):
        with patch.object(probes, "execute_sql",
                          AsyncMock(side_effect=probes.StatementTimeout("timed out after 600 seconds"))):
            audit = asyncio.run(probes._genie_audit_counts())
            self.assertTrue(audit.get("timed_out"))
            with patch.object(probes, "_genie_audit_counts", AsyncMock(return_value=audit)):
                out = asyncio.run(probes.probe_genie_agents())
        self.assertFalse(out["available"])
        self.assertIn("timed out", out["note"])
        self.assertNotIn("needs SELECT", out["note"])


if __name__ == "__main__":
    import unittest

    unittest.main()
