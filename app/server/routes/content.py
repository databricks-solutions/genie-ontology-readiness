"""Content endpoints — capability explainers (Learn tab) and pillar accelerators."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse, Response

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
    # The on-disk name may differ from what the user should download. A Databricks
    # notebook (`.py` with a `# Databricks notebook source` header) is imported by
    # `bundle deploy` as a workspace NOTEBOOK — the `.py` is stripped and the app can
    # no longer find it — so such artifacts are stored with a neutral `.txt` extension
    # and expose the real download name via `download_as`. The served content-type and
    # filename come from the download name, not the storage name.
    download_name = acc.get("download_as") or acc["artifact_file"]
    # Artifacts can be notebooks, SQL, or (e.g. workshop) docs — serve the right type.
    media_type = {
        ".py": "text/x-python",
        ".sql": "application/sql",
        ".md": "text/markdown",
        ".ipynb": "application/x-ipynb+json",
    }.get(Path(download_name).suffix.lower(), "application/octet-stream")
    # Text artifacts (markdown handbooks, .py notebooks, .sql) embed
    # docs.databricks.com links authored for AWS — route them to the deployment's
    # cloud before serving (see server.doc_links) so an Azure/GCP viewer never gets
    # an AWS-only link. Binary/opaque types (.ipynb JSON, octet-stream) are served
    # verbatim.
    _REWRITE_MEDIA = {"text/markdown", "text/x-python", "application/sql"}
    if media_type in _REWRITE_MEDIA:
        from server.config import get_cloud_provider
        from server.doc_links import rewrite_doc_links_in_text
        text = rewrite_doc_links_in_text(path.read_text(encoding="utf-8"), get_cloud_provider())
        return Response(
            content=text,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{download_name}"'},
        )
    return FileResponse(path, media_type=media_type, filename=download_name)
