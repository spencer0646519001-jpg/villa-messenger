from fastapi import FastAPI

from app.api.health_routes import router as health_router
from app.settings import settings

app = FastAPI(title=settings.app_name)
app.include_router(health_router)

