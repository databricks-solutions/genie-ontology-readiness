"""Genie Ontology Readiness — FastAPI entry point.

A customer-deployable Databricks App that assesses a workspace's maturity for
Genie Ontology / Unity Catalog Business Semantics, explains each capability,
and generates tailored enablement + adoption plans via the customer's own
Foundation Model API.
"""

import os
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# index.html must never be cached, so a new deploy's HTML (which references the
# new content-hashed asset bundle) is always fetched. Otherwise a stale cached
# page can keep calling endpoints that no longer exist. Hashed /assets stay cacheable.
_NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}

from server.routes import router
from server.routes._shared import _ai_model, AI_MODELS, DEFAULT_LLM_MODEL
from server.config import USE_LAKEBASE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

logger.info(f"DATABRICKS_APP_NAME: {os.environ.get('DATABRICKS_APP_NAME', 'not set')}")
logger.info(f"DATABRICKS_HOST: {os.environ.get('DATABRICKS_HOST', 'not set')}")
logger.info(f"DATABRICKS_WAREHOUSE_ID: {os.environ.get('DATABRICKS_WAREHOUSE_ID', 'not set')}")
logger.info(f"USE_LAKEBASE: {USE_LAKEBASE}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Warm up the SQL warehouse and (optionally) the Lakebase snapshot pool."""
    import asyncio
    from server.sql_client import execute_sql

    async def init_lakebase():
        if USE_LAKEBASE:
            from server.lakebase_client import init_pool, is_available
            await init_pool()
            if is_available():
                from server.snapshots import ensure_table as ensure_snapshots
                from server.plans import ensure_table as ensure_plans
                await ensure_snapshots()
                await ensure_plans()
                logger.info("Lakebase pool ready — assessment history + plans enabled")
            else:
                logger.warning("Lakebase pool unavailable — history/plans disabled")

    async def warmup_warehouse():
        try:
            await execute_sql("SELECT 1 AS warmup")
            logger.info("SQL warehouse warmup complete.")
        except Exception as e:
            logger.warning(f"SQL warehouse warmup failed (non-fatal): {e}")

    await asyncio.gather(init_lakebase(), warmup_warehouse())
    yield

    if USE_LAKEBASE:
        from server.lakebase_client import close_pool
        await close_pool()


app = FastAPI(title="Genie Ontology Readiness", version="1.0.0", lifespan=lifespan)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error on {request.url}: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"error": str(exc)})


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Set per-request AI model from the X-AI-Model header."""

    async def dispatch(self, request: Request, call_next):
        model = request.headers.get("X-AI-Model", DEFAULT_LLM_MODEL)
        if model not in AI_MODELS:
            model = DEFAULT_LLM_MODEL
        token = _ai_model.set(model)
        try:
            response = await call_next(request)
            # Never cache API responses (e.g. assessment/plan history) so the UI
            # always reflects the latest per-user state.
            if request.url.path.startswith("/api"):
                response.headers["Cache-Control"] = "no-store"
            return response
        finally:
            _ai_model.reset(token)


app.add_middleware(RequestContextMiddleware)
app.include_router(router)

# ---------------------------------------------------------------------------
# Serve the built React frontend
# ---------------------------------------------------------------------------
frontend_dist = Path(__file__).parent / "frontend" / "dist"

if frontend_dist.exists():
    logger.info(f"Serving frontend from: {frontend_dist}")
    assets_dir = frontend_dist / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/vite.svg")
    async def vite_svg():
        f = frontend_dist / "vite.svg"
        if f.exists():
            return FileResponse(str(f))

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"error": "Not found"})
        return FileResponse(str(frontend_dist / "index.html"), headers=_NO_CACHE)
else:
    logger.warning(f"Frontend dist not found at: {frontend_dist}")

    @app.get("/")
    async def root():
        return {"message": "Genie Ontology Readiness API. Frontend not built yet."}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("DATABRICKS_APP_PORT", os.environ.get("PORT", 8000)))
    uvicorn.run(app, host="0.0.0.0", port=port)
