"""Автоподнятие лотов: раз в заданный интервал поднимает все активные лоты каждого аккаунта."""
import threading
import time

from . import accounts, db

_wake = threading.Event()
last_run = {}  # account_id -> {"ts": ..., "report": [...]}


def raise_account(acc):
    try:
        with accounts.use(acc["id"]) as fp:
            report = fp.raise_all()
    except Exception as e:
        report = [f"Ошибка: {e}"]
    last_run[acc["id"]] = {"ts": time.time(), "report": report}
    level = "error" if any(r.startswith("Ошибка") for r in report) else "info"
    db.log(f"Автоподнятие, {acc['name']}: " + "; ".join(report), level)
    return report


def raise_now():
    return {a["name"]: raise_account(a) for a in db.query("SELECT * FROM accounts WHERE status='ok'")}


def _loop():
    next_at = 0
    while True:
        s = db.get_settings()
        if s["raise_enabled"] and time.time() >= next_at:
            raise_now()
            next_at = time.time() + s["raise_interval_min"] * 60
        _wake.wait(60)
        _wake.clear()


def start():
    threading.Thread(target=_loop, daemon=True, name="raiser").start()
