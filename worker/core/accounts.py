"""Один постоянный клиент FunPay на аккаунт (сессия и csrf живут между запросами).

Все модули (синхронизация, чаты, поднятие, плагины) работают с FunPay через use(),
чтобы запросы одного аккаунта не шли параллельно.
"""
import threading
from contextlib import contextmanager

from . import db
from .crypto import decrypt
from .funpay import FunPayAccount, FunPayError

_clients: dict[int, FunPayAccount] = {}
_locks: dict[int, threading.RLock] = {}
_guard = threading.Lock()


@contextmanager
def use(account_id: int):
    with _guard:
        lock = _locks.setdefault(account_id, threading.RLock())
    with lock:
        fp = _clients.get(account_id)
        if fp is None:
            rows = db.query("SELECT key_enc, proxy FROM accounts WHERE id=?", (account_id,))
            if not rows:
                raise FunPayError(f"Аккаунт #{account_id} не найден")
            fp = _clients[account_id] = FunPayAccount(decrypt(rows[0]["key_enc"]), rows[0].get("proxy") or "")
        yield fp


def forget(account_id: int):
    _clients.pop(account_id, None)


def active_ids():
    return [r["id"] for r in db.query("SELECT id FROM accounts WHERE status != 'error' OR status IS NULL")]


def all_ids():
    return [r["id"] for r in db.query("SELECT id FROM accounts")]
