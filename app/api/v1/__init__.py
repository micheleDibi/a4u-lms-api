"""Router delle API v1: organizzazioni e corsi (docs/contracts/api-v1.md §3)."""

from fastapi import APIRouter

from app.api.v1 import courses, organizations
from app.api.v1.responses import V1_ERRORS

router = APIRouter(prefix="/v1")
# 401, 429 e 500 valgono per ogni route /v1 (solo documentazione OpenAPI).
router.include_router(organizations.router, responses=V1_ERRORS)
router.include_router(courses.router, responses=V1_ERRORS)
