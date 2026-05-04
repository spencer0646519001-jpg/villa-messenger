from datetime import datetime, timezone
import sqlite3


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return dict(row)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
