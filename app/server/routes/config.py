"""Config endpoint — describes pillars, questions, models, and app capabilities to the UI."""

import os
from fastapi import APIRouter

from server.pillars import PILLARS, LEVEL_LABELS
from server.routes._shared import list_available_models, resolve_default_model
from server.config import USE_LAKEBASE, GENIE_SPACE_ID, WORKSPACE_ID, ASSESS_CATALOGS, get_cloud_provider

router = APIRouter()


@router.get("/config")
async def get_config():
    """Everything the frontend needs to render the app shell."""
    # Fetch live available models from workspace serving endpoints
    ai_models = await list_available_models()
    default_model = await resolve_default_model(ai_models)

    return {
        "app_name": "Genie Ontology Readiness",
        "brand_name": os.environ.get("BRAND_NAME", "Databricks"),
        "workspace_id": WORKSPACE_ID,
        "level_labels": LEVEL_LABELS,
        "pillars": [
            {
                "key": p["key"],
                "name": p["name"],
                "short": p["short"],
                "weight": p["weight"],
                "capability": p["capability"],
            }
            for p in PILLARS
        ],
        "ai_models": ai_models,
        "default_model": default_model,
        "lakebase_enabled": USE_LAKEBASE,
        "genie_space_configured": bool(GENIE_SPACE_ID),
        "assess_catalogs": ASSESS_CATALOGS,
        # Cloud the app is deployed on (aws|azure|gcp); documentation links are
        # routed to this cloud's docs server-side.
        "cloud": get_cloud_provider(),
    }


_SYSTEM_CATALOGS = {"system", "__databricks_internal", "samples", "information_schema"}


@router.get("/setup/grants")
async def get_setup_grants():
    """The GRANT statements an admin runs once so the app can read its assessment signals.

    Filled in with this app's own service principal id (DATABRICKS_CLIENT_ID). When no
    catalogs are set, the catalogs the app can list are used, if it can list them.
    """
    from server.config import _get_workspace_client
    from server.grants import grant_statements

    sp_id = os.environ.get("DATABRICKS_CLIENT_ID", "")
    catalogs = list(ASSESS_CATALOGS)
    note = ""
    if not catalogs:
        try:
            catalogs = [c.name for c in _get_workspace_client().catalogs.list()
                        if c.name and c.name not in _SYSTEM_CATALOGS and not c.name.startswith("__")]
        except Exception:
            note = "Add one catalog grant per catalog you assess. The app could not list catalogs."
    return {
        "service_principal_id": sp_id,
        "statements": grant_statements(sp_id, catalogs),
        "note": note,
    }
