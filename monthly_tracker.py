"""Monthly activity goal snapshot and parsing of GoFluent's visible History UI."""
from __future__ import annotations

import json
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
SNAPSHOT_PATH = ROOT / "data" / "monthly_activity.json"
REFRESH_LIMIT_PATH = ROOT / "data" / "monthly_refresh_limit.json"
MONTHLY_GOAL = 12
REFRESH_COOLDOWN_SECONDS = 120


def refresh_cooldown_remaining(now: float | None = None) -> int:
    try:
        saved = json.loads(REFRESH_LIMIT_PATH.read_text(encoding="utf-8"))
        last_click = float(saved.get("last_click_epoch", 0))
        return max(0, int(REFRESH_COOLDOWN_SECONDS - ((now or time.time()) - last_click) + 0.99))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return 0


def record_manual_refresh(now: float | None = None) -> None:
    now = now or time.time()
    REFRESH_LIMIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = REFRESH_LIMIT_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps({
        "last_click_epoch": now,
        "last_click_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "cooldown_seconds": REFRESH_COOLDOWN_SECONDS,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(REFRESH_LIMIT_PATH)


def month_key(today: date | None = None) -> str:
    today = today or date.today()
    return today.strftime("%Y-%m")


def empty_snapshot(today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    return {
        "month": month_key(today),
        "count": 0,
        "goal": MONTHLY_GOAL,
        "activity_titles": [],
        "records": [],
        "synced_at": None,
        "source": "GoFluent My Training / History",
    }


def load_snapshot(today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    fallback = empty_snapshot(today)
    try:
        saved = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        if not isinstance(saved, dict) or saved.get("month") != fallback["month"]:
            return fallback
        count = saved.get("count")
        if not isinstance(count, int) or count < 0:
            return fallback
        return {**fallback, **saved, "goal": MONTHLY_GOAL}
    except (OSError, json.JSONDecodeError):
        return fallback


def save_snapshot(records: list[dict[str, Any]], today: date | None = None,
                  synced_at: str | None = None) -> dict[str, Any]:
    today = today or date.today()
    # Count each passed activity title once per month, even if the History
    # contains multiple attempts for the same activity.
    by_title: dict[str, dict[str, Any]] = {}
    for record in records:
        title = " ".join(str(record.get("title", "")).split())
        if title:
            by_title.setdefault(title.casefold(), {**record, "title": title})
    snapshot = {
        "month": month_key(today),
        "count": len(by_title),
        "goal": MONTHLY_GOAL,
        "activity_titles": [record["title"] for record in by_title.values()],
        "records": list(by_title.values()),
        "synced_at": synced_at or datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "GoFluent My Training / History",
    }
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = SNAPSHOT_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(SNAPSHOT_PATH)
    return snapshot


def visible_history_groups(page) -> list[dict[str, Any]]:
    """Read activity labels/status/date from rendered Training History cards."""
    return page.evaluate("""() => Array.from(document.querySelectorAll('section.training-card-block'))
      .map(section => {
        const heading = section.querySelector('h2.training-card-block__date');
        const rawDate = (heading?.innerText || '').trim();
        const parsed = Date.parse(rawDate);
        const localDate = Number.isFinite(parsed) ? new Date(parsed) : null;
        return {
          date: localDate ? [localDate.getFullYear(), String(localDate.getMonth() + 1).padStart(2, '0'), String(localDate.getDate()).padStart(2, '0')].join('-') : '',
          raw_date: rawDate,
          records: Array.from(section.querySelectorAll('.training-card[role="listitem"]')).map(card => ({
            type: (card.querySelector('.training-card__type')?.innerText || '').trim(),
            title: (card.querySelector('.training-card__link-content')?.innerText || '').trim(),
            status: (card.querySelector('.training-card__status')?.innerText || '').trim()
          }))
        };
      })""")
