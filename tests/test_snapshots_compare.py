"""Pytest entry for snapshot compare (suite lives next to snapshots.py)."""

from server.test_snapshots_compare import (  # noqa: F401
    BuildCompareTest,
    CompareSnapshotsOwnershipTest,
)
