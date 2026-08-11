"""API routes package — aggregates all routers under /api."""

from fastapi import APIRouter

from .config import router as config_router
from .assess import router as assess_router
from .content import router as content_router
from .plan import router as plan_router
from .genie import router as genie_router

router = APIRouter(prefix="/api")
for sub in [config_router, assess_router, content_router, plan_router, genie_router]:
    router.include_router(sub)
