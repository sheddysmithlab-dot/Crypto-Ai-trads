"""Mon-Fri IST session windows for auto AI automation on/off.

Windows (Asia/Kolkata):
  - Morning Momentum:     05:30 - 08:30
  - Peak Overlap Window:  18:30 - 23:30
  - US Core Session:      19:30 - 01:30 (next day)

Automation is ON when the switch is enabled AND now falls in any window.
Midnight-crossing windows count against the weekday they started on
(e.g. Fri 19:30 -> Sat 01:30 is ON; Sun 19:30 -> Mon 01:30 is OFF).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

_DEFAULT_DATA = Path(__file__).resolve().parent / "data"
DATA_DIR = Path(os.environ.get("SESSION_SCHEDULE_DATA_DIR", str(_DEFAULT_DATA)))
STATE_PATH = DATA_DIR / "session_schedule.json"


@dataclass(frozen=True)
class SessionWindow:
    key: str
    label: str
    start: time  # inclusive
    end: time  # exclusive; if end <= start, window crosses midnight


WINDOWS: tuple[SessionWindow, ...] = (
    SessionWindow("morning_momentum", "Morning Momentum", time(5, 30), time(8, 30)),
    SessionWindow("peak_overlap", "Peak Overlap Window", time(18, 30), time(23, 30)),
    SessionWindow("us_core", "US Core Session", time(19, 30), time(1, 30)),
)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _window_active_at(now: datetime, start: time, end: time) -> bool:
    """True if `now` (IST-aware) is inside the window on an allowed session day."""
    now = now.astimezone(IST)
    t = now.time().replace(second=0, microsecond=0)

    if start < end:
        if not (start <= t < end):
            return False
        return now.weekday() < 5  # Mon-Fri

    # Crosses midnight (e.g. 19:30 -> 01:30)
    if t >= start:
        # Evening leg — must be Mon-Fri today
        return now.weekday() < 5
    if t < end:
        # Morning leg — session started yesterday; yesterday must be Mon-Fri
        yesterday = now - timedelta(days=1)
        return yesterday.weekday() < 5
    return False


def active_windows(dt: datetime | None = None) -> list[SessionWindow]:
    now = dt.astimezone(IST) if dt else datetime.now(IST)
    return [w for w in WINDOWS if _window_active_at(now, w.start, w.end)]


def should_automation_run(dt: datetime | None = None) -> bool:
    return bool(active_windows(dt))


def current_window_labels(dt: datetime | None = None) -> list[str]:
    return [w.label for w in active_windows(dt)]


def next_transition(dt: datetime | None = None) -> dict:
    """Next IST boundary when in_window flips (within ~7 days)."""
    now = (dt.astimezone(IST) if dt else datetime.now(IST)).replace(second=0, microsecond=0)
    want = should_automation_run(now)
    cursor = now
    for _ in range(60 * 24 * 8):  # minute steps, up to 8 days
        cursor += timedelta(minutes=1)
        if should_automation_run(cursor) != want:
            return {
                "at_ist": cursor.strftime("%Y-%m-%d %H:%M IST"),
                "will_be_active": should_automation_run(cursor),
                "windows": current_window_labels(cursor),
            }
    return {"at_ist": None, "will_be_active": want, "windows": []}


class SessionScheduleStore:
    """Persisted schedule switch — survives restarts via data/ JSON (+ env default)."""

    def __init__(self):
        self.enabled = _env_bool("SESSION_SCHEDULE_ENABLED", False)
        self._load()

    def _load(self) -> None:
        try:
            if STATE_PATH.is_file():
                data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "enabled" in data:
                    self.enabled = bool(data["enabled"])
        except Exception as exc:
            print(f"[SESSION SCHEDULE] load note: {exc}")

    def _save(self) -> None:
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            STATE_PATH.write_text(
                json.dumps({"enabled": self.enabled}, indent=2) + "\n",
                encoding="utf-8",
            )
        except Exception as exc:
            print(f"[SESSION SCHEDULE] save note: {exc}")

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        self._save()

    def status_dict(self) -> dict:
        now = datetime.now(IST)
        in_win = should_automation_run(now)
        labels = current_window_labels(now)
        return {
            "enabled": self.enabled,
            "timezone": "Asia/Kolkata",
            "days": "Mon-Fri",
            "in_window": in_win,
            "active_windows": labels,
            "want_active": self.enabled and in_win,
            "now_ist": now.strftime("%Y-%m-%d %H:%M:%S IST"),
            "weekday": now.strftime("%A"),
            "windows": [
                {
                    "key": w.key,
                    "label": w.label,
                    "start": w.start.strftime("%H:%M"),
                    "end": w.end.strftime("%H:%M"),
                }
                for w in WINDOWS
            ],
            "next": next_transition(now),
        }


schedule_store = SessionScheduleStore()
