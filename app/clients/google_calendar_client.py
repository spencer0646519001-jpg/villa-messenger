"""
GoogleCalendarClient: fetches calendar events via service account and
normalizes them into domain CalendarEvent objects.

This is the I/O boundary for calendar access. It does not interpret events
(no keyword logic) — it only fetches and normalizes. The pure-function
check_availability() consumes its output.

Only this module imports googleapiclient / google.auth. Domain and service
layers stay Google-free (they use CalendarEvent only).
"""

from datetime import date, datetime, time, timezone
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.domain.availability_models import CalendarEvent


_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


class GoogleCalendarError(Exception):
    """Raised when the calendar API or auth fails. Wraps the underlying
    googleapiclient / google.auth exception so upstream code does not
    depend on Google's exception classes."""


class GoogleCalendarClient:
    def __init__(self, *, credentials_path: str, calendar_id: str) -> None:
        # Lazy: service is built on first fetch_events() call so constructing
        # the client never touches the network — important for tests and for
        # fast startup. The same service instance is reused after first build.
        self._credentials_path = credentials_path
        self._calendar_id = calendar_id
        self._service: Any | None = None

    def fetch_events(self, *, range_start: date, range_end: date) -> list[CalendarEvent]:
        """Fetch events overlapping [range_start, range_end]. Raises GoogleCalendarError on failure."""
        try:
            response = self._list_events(range_start, range_end)
        except (HttpError, OSError, ValueError) as exc:
            raise GoogleCalendarError(f"calendar fetch failed: {exc}") from exc
        return [self._to_calendar_event(item) for item in response.get("items", [])]

    def _list_events(self, range_start: date, range_end: date) -> dict:
        time_min = datetime.combine(range_start, time.min, tzinfo=timezone.utc).isoformat()
        time_max = datetime.combine(range_end, time.max, tzinfo=timezone.utc).isoformat()
        return self._get_service().events().list(
            calendarId=self._calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

    def _get_service(self) -> Any:
        if self._service is None:
            self._service = self._build_service()
        return self._service

    def _build_service(self) -> Any:
        credentials = service_account.Credentials.from_service_account_file(
            self._credentials_path, scopes=_SCOPES
        )
        return build("calendar", "v3", credentials=credentials, cache_discovery=False)

    def _to_calendar_event(self, raw: dict) -> CalendarEvent:
        return CalendarEvent(
            summary=raw.get("summary", "") or "",
            start_date=self._extract_date(raw.get("start", {})),
            end_date=self._extract_date(raw.get("end", {})),
        )

    def _extract_date(self, endpoint: dict) -> date:
        if "date" in endpoint:
            return date.fromisoformat(endpoint["date"])
        if "dateTime" in endpoint:
            return datetime.fromisoformat(endpoint["dateTime"]).date()
        raise GoogleCalendarError(f"event endpoint missing date/dateTime: {endpoint!r}")
