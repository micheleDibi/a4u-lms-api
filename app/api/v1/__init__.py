"""Router delle API v1: organizzazioni e corsi (docs/contracts/api-v1.md §3)."""

from fastapi import APIRouter

from app.api.v1 import courses, organizations

router = APIRouter(prefix="/v1")
router.include_router(organizations.router)
router.include_router(courses.router)
