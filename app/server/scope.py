"""Assessment scope: which catalogs, schemas and workspaces a run covered (#89).

Saved with each snapshot so the "Readiness over time" trend and Compare only line up
runs that measured the same thing. A run on catalog A and a run on catalogs A+B are
different populations; plotting them on one line reads as a trend that doesn't exist.

Pure and free of I/O so it unit-tests without Lakebase.
"""

import hashlib
import json


def normalize_scope(
    catalogs: list[str] | None,
    schemas: list[str] | None,
    workspace_filter: dict | None,
    env_catalogs: list[str] | None = None,
) -> dict:
    """A canonical, order-independent scope for one assessment request.

    Mirrors how the assessment resolves its scope (``probes._scoped_catalogs`` and
    ``workspace_filter.set_workspace_filter``), so two runs get the same scope only
    when they assessed the same thing:

    * catalogs: the requested catalogs; else, when no workspace include-filter picks
      catalogs through bindings, the deployment's ``ASSESS_CATALOGS`` (``env_catalogs``);
      else empty, meaning "all catalogs in scope". With an include-filter the
      catalogs come from the selected workspaces, which the workspace ids capture.
    * names are lower-cased (Unity Catalog names are case-insensitive) and sorted;
    * schemas only apply when exactly one catalog is selected, so they are dropped
      otherwise;
    * the workspace mode is "exclude" only for exactly "exclude", else "include";
      an empty id list means "all workspaces" (no mode).
    """
    wf = workspace_filter or {}
    ids = sorted({str(i).strip() for i in (wf.get("workspace_ids") or []) if str(i).strip()})
    mode = ("exclude" if wf.get("mode") == "exclude" else "include") if ids else None
    requested = [c for c in (catalogs or []) if c and c.strip()]
    if not requested and not (mode == "include" and ids):
        requested = [c for c in (env_catalogs or []) if c and c.strip()]
    cats = sorted({c.strip().lower() for c in requested})
    schs = sorted({s.strip().lower() for s in (schemas or []) if s and s.strip()}) if len(cats) == 1 else []
    return {"catalogs": cats, "schemas": schs, "workspace_mode": mode, "workspace_ids": ids}


def scope_hash(scope: dict | None) -> str | None:
    """A short, stable id for a normalized scope (``None`` for an unknown scope)."""
    if scope is None:
        return None
    blob = json.dumps(scope, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def same_scope(a: str | None, b: str | None) -> bool | None:
    """Whether two snapshots share a scope; ``None`` when either scope is unknown
    (a snapshot saved before scopes were recorded)."""
    if a is None or b is None:
        return None
    return a == b
