import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
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
    build_holiday_calendar_service,
    router as line_webhook_router,
    run_nightly_digest_check,
)
from app.settings import settings  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Layer 3 of the 23:00-boot-interrupt fix: the first proactive (non-webhook-
# triggered) component in this codebase. Polls every 5 minutes so a tenant's
# auto_on_start_time boundary is noticed promptly without needing per-tenant
# cron scheduling; run_nightly_digest_check itself is idempotent per
# tenant-local day (see tenant_operation_state.last_digest_sent_date), so a
# 5-minute poll granularity just bounds how late the digest can be, never how
# often it fires.
_DIGEST_CHECK_INTERVAL_SECONDS = 300

# Pull this year's and next year's holiday calendars into the cache at boot so
# the first customer of the year does not wait on an HTTP fetch mid-reply, and
# so a quote near new year is not blocked just because nobody had asked about
# the coming year yet. Failure here is not fatal: the per-stay gate refetches
# on demand, and blocks the quote rather than mispricing it if that also fails.
_HOLIDAY_PREWARM_YEARS_AHEAD = 1


def _prewarm_holiday_calendar() -> None:
    service = build_holiday_calendar_service(settings.database_path)
    this_year = datetime.now(timezone.utc).year
    years = [this_year + offset for offset in range(_HOLIDAY_PREWARM_YEARS_AHEAD + 1)]
    missing = service.prewarm(years)
    if missing:
        logger.warning("Holiday calendar prewarm incomplete for %s", missing)
    else:
        logger.info("Holiday calendar ready for %s", years)


async def _nightly_digest_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(run_nightly_digest_check, settings.database_path)
        except Exception:  # noqa: BLE001 -- the loop must survive any single failure
            logger.warning("Nightly digest loop iteration failed", exc_info=True)
        await asyncio.sleep(_DIGEST_CHECK_INTERVAL_SECONDS)


async def _prewarm_holiday_calendar_async() -> None:
    try:
        await asyncio.to_thread(_prewarm_holiday_calendar)
    except Exception:  # noqa: BLE001 -- startup must not fail on a cold calendar
        logger.warning("Holiday calendar prewarm failed", exc_info=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(_nightly_digest_loop())
    prewarm = asyncio.create_task(_prewarm_holiday_calendar_async())
    try:
        yield
    finally:
        task.cancel()
        prewarm.cancel()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(health_router)
app.include_router(line_webhook_router)

