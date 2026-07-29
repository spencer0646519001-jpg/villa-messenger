import asyncio
import logging
from contextlib import asynccontextmanager
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
from app.api.line_webhook_routes import (  # noqa: E402
    router as line_webhook_router,
    run_nightly_digest_check,
)
from app.settings import settings  # noqa: E402

logger = logging.getLogger(__name__)

# Layer 3 of the 23:00-boot-interrupt fix: the first proactive (non-webhook-
# triggered) component in this codebase. Polls every 5 minutes so a tenant's
# auto_on_start_time boundary is noticed promptly without needing per-tenant
# cron scheduling; run_nightly_digest_check itself is idempotent per
# tenant-local day (see tenant_operation_state.last_digest_sent_date), so a
# 5-minute poll granularity just bounds how late the digest can be, never how
# often it fires.
_DIGEST_CHECK_INTERVAL_SECONDS = 300


async def _nightly_digest_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(run_nightly_digest_check, settings.database_path)
        except Exception:  # noqa: BLE001 -- the loop must survive any single failure
            logger.warning("Nightly digest loop iteration failed", exc_info=True)
        await asyncio.sleep(_DIGEST_CHECK_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(_nightly_digest_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(health_router)
app.include_router(line_webhook_router)

