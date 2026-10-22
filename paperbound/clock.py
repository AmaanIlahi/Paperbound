"""The single source of "today". Everything that depends on the date (extraction prompt, rent-table row selection,
decision brief, reminder dates) calls this, so a run can be replayed as of another day.

Override with the AS_OF environment variable (YYYY-MM-DD), or pass a date explicitly to `today()`.
"""

import os
from datetime import date


def today(override: date | None = None) -> date:
    """`override` wins, then AS_OF, then the system date."""
    if override is not None:
        return override
    raw = os.environ.get("AS_OF", "").strip()
    if raw:
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(f"AS_OF must be YYYY-MM-DD, got {raw!r}") from exc
    return date.today()
