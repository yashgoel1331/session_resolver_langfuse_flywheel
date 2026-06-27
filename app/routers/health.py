import time

from fastapi import APIRouter

from app.config import settings

router = APIRouter(prefix="/health", tags=["health"])

START_TIME = time.time()


@router.get("/live")
async def liveness():
    return {"status": "alive"}


@router.get("/")
async def health_check():
    return {
        "app": {
            "name": settings.app_name,
            "environment": settings.environment,
            "uptime_seconds": int(time.time() - START_TIME),
        },
        "status": "healthy",
    }
