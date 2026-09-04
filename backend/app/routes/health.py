"""
Health check route.

Kept dead simple on purpose — used by uptime checks, load balancers,
and for smoke-testing that the app boots correctly.
"""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    return {"status": "ok"}
