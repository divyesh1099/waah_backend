"""
Lightweight background scheduler to keep report snapshots up to date.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from app.db import SessionLocal
from app.services.reporting import refresh_all_reports_for_day

_started = False


def _run_for_day(day):
    db = SessionLocal()
    try:
        stats = refresh_all_reports_for_day(db, day)
        db.commit()
        logging.info("refreshed reports for %s: %s", day.isoformat(), stats)
    except Exception:
        db.rollback()
        logging.exception("report scheduler failed for %s", day)
    finally:
        db.close()


def _loop():
    last_full_day = None
    while True:
        today = datetime.now(timezone.utc).date()
        _run_for_day(today)

        if last_full_day != today:
            # Re-run yesterday once per day to capture any late changes/uploads
            _run_for_day(today - timedelta(days=1))
            last_full_day = today

        time.sleep(60 * 60)  # hourly


def start_report_scheduler():
    global _started
    if _started:
        return
    _started = True
    t = threading.Thread(target=_loop, name="report-scheduler", daemon=True)
    t.start()
