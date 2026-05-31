from fastapi import FastAPI

from app.api.health_routes import router as health_router
from app.api.line_webhook_routes import router as line_webhook_router
from app.settings import settings

app = FastAPI(title=settings.app_name)
app.include_router(health_router)
app.include_router(line_webhook_router)

