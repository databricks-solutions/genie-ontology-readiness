"""API routes package — aggregates all routers under /api.

Each router is included under the CUJ phase it serves, so outbound Databricks
API calls made while handling its requests are tagged with the matching product
(``genie-ontology-readiness-<phase>``). The tagging itself happens in the
aiohttp/SDK layers; here we only pin the phase into the request context via a
router-level dependency. ``config`` carries no phase (app bootstrap → base
product).
"""

from fastapi import APIRouter, Depends

from server.user_agent import set_product_phase

from .config import router as config_router
from .assess import router as assess_router
from .content import router as content_router
from .plan import router as plan_router
from .genie import router as genie_router
from .workspaces import router as workspaces_router
from .catalogs import router as catalogs_router


def _phase_dep(phase: str):
    """Build a dependency that marks the request context with ``phase``.

    ``_mark`` MUST be ``async``: FastAPI runs a *sync* dependency in a threadpool
    (``run_in_threadpool``), which copies the context into a worker thread, so a
    ``contextvars.ContextVar.set()`` there mutates a throwaway copy and never
    reaches the request's own context — the endpoint and its outbound aiohttp
    calls would still read the default ``None`` (base product, no phase suffix).
    An ``async`` dependency is awaited in the request task itself, so the set is
    visible to everything that runs while serving the request.
    """

    async def _mark() -> None:
        set_product_phase(phase)

    return _mark


# Router -> CUJ phase. Catalog/workspace pickers and the Genie answer-quality
# test all belong to the Assess tab; Plan and Learn are their own tabs.
_PHASE_BY_ROUTER = [
    (assess_router, "assess"),
    (catalogs_router, "assess"),
    (workspaces_router, "assess"),
    (genie_router, "assess"),
    (plan_router, "plan"),
    (content_router, "learn"),
]

router = APIRouter(prefix="/api")

# config: no phase (base product).
router.include_router(config_router)

for sub, phase in _PHASE_BY_ROUTER:
    router.include_router(sub, dependencies=[Depends(_phase_dep(phase))])
