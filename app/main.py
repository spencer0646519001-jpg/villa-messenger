from pathlib import Path

from dotenv import load_dotenv

# Load local .env into os.environ before anything reads env vars (e.g. the LINE
# webhook resolves channel_secret_ref via os.environ at request time). Optional:
# a no-op if .env is absent (prod/CI), and override=False so test monkeypatch
# and real shell env always win over .env.
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH)

from fastapi import FastAPI  # noqa: E402

from app.api.health_routes import router as health_router  # noqa: E402
from app.api.line_webhook_routes import router as line_webhook_router  # noqa: E402
from app.settings import settings  # noqa: E402

app = FastAPI(title=settings.app_name)
app.include_router(health_router)
app.include_router(line_webhook_router)

