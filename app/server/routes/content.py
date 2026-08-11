"""Content endpoints — capability explainers (Learn tab) and pillar accelerators."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from server.content.library import list_capabilities, get_capability
from server.content.accelerators import (
    accelerators_for,
    get_accelerator,
    list_accelerators,
)

router = APIRouter()

# Bundled accelerator artifacts live under app/accelerators/<dir>/. This file is
# app/server/routes/content.py, so parents[2] is the app/ directory.
ACCEL_ROOT = (Path(__file__).resolve().parents[2] / "accelerators").resolve()


def _with_accelerators(cap: dict) -> dict:
    """Attach the accelerators that improve this capability."""
    return {**cap, "accelerators": accelerators_for(cap["key"])}


@router.get("/content")
async def all_content():
    return {"capabilities": [_with_accelerators(c) for c in list_capabilities()]}


@router.get("/content/{key}")
async def one_capability(key: str):
    cap = get_capability(key)
    if cap is None:
        return JSONResponse(status_code=404, content={"error": f"Unknown capability: {key}"})
    return _with_accelerators(cap)


@router.get("/accelerators")
async def all_accelerators():
    return {"accelerators": list_accelerators()}


@router.get("/accelerators/{key}/artifact")
async def accelerator_artifact(key: str):
    """Download a bundled accelerator artifact (e.g. the notebook source)."""
    acc = get_accelerator(key)
    if acc is None or not acc.get("artifact_file"):
        return JSONResponse(status_code=404, content={"error": "No downloadable artifact for this accelerator."})
    path = (ACCEL_ROOT / acc.get("artifact_dir", "") / acc["artifact_file"]).resolve()
    # Path-traversal guard: the resolved path must stay under the accelerators root.
    if ACCEL_ROOT not in path.parents or not path.is_file():
        return JSONResponse(status_code=404, content={"error": "Artifact not found."})
    # Artifacts can be notebooks, SQL, or (e.g. workshop) docs — serve the right type.
    media_type = {
        ".py": "text/x-python",
        ".sql": "application/sql",
        ".md": "text/markdown",
        ".ipynb": "application/x-ipynb+json",
    }.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type, filename=acc["artifact_file"])
