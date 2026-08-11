"""Config endpoint — describes pillars, questions, models, and app capabilities to the UI."""

import os
from fastapi import APIRouter

from server.pillars import PILLARS, LEVEL_LABELS
from server.routes._shared import list_available_models, resolve_default_model
from server.config import USE_LAKEBASE, GENIE_SPACE_ID, WORKSPACE_ID, ASSESS_CATALOGS

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
    }
