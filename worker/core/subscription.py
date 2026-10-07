"""Подписка: воркер спрашивает главный сервер лицензий, активна ли подписка.

server_id — это хэш от секретов воркера (токен подключения). Сам токен на сервер лицензий
не уходит, только необратимый хэш, поэтому по нему нельзя подключиться к воркеру.

Статус кэшируется и перепроверяется раз в сутки. Если сервер лицензий недоступен,
последний известный статус действует ещё 3 дня (grace), чтобы разовый сбой сети
не отключал плагины у оплативших клиентов.
"""
import hashlib
import json
import os
import threading
import time

import requests

from . import db

# Адрес сервера лицензий зашит в код (клиент не может его стереть, чтобы включить бесплатный режим).
# Переменная окружения может переопределить только для разработки/своего сервера.
_DEFAULT_LICENSE_URL = "http://158.220.95.203:8900"
LICENSE_URL = (os.environ.get("KASSA_LICENSE_URL") or _DEFAULT_LICENSE_URL).rstrip("/")
CHECK_EVERY = 24 * 3600
GRACE_SECONDS = 3 * 86400

_lock = threading.Lock()


def server_id():
    """Стабильный id этого воркера: хэш токена подключения. Токен наружу не уходит."""
    token = os.environ.get("KASSA_TOKEN", "")
    return hashlib.sha256(("kassa-server-id:" + token).encode()).hexdigest()


def _cache():
    r = db.query("SELECT value FROM plugin_kv WHERE plugin_id='core' AND key='subscription'")
    return json.loads(r[0]["value"]) if r else {}


def _save(data):
    db.execute("INSERT OR REPLACE INTO plugin_kv(plugin_id, key, value) VALUES('core','subscription',?)",
               (json.dumps(data),))


def _post(path, body):
    if not LICENSE_URL:
        raise RuntimeError("Сервер лицензий не настроен (KASSA_LICENSE_URL)")
    r = requests.post(f"{LICENSE_URL}{path}", json={**body, "server_id": server_id()}, timeout=15)
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code >= 400:
        raise RuntimeError(data.get("error", f"Сервер лицензий ответил {r.status_code}"))
    return data


def status(force=False):
    """Возвращает статус подписки. Ходит на сервер не чаще раза в сутки, если не force."""
    with _lock:
        c = _cache()
        fresh = c.get("checked_at", 0) + CHECK_EVERY > time.time()
        if c and fresh and not force:
            return _public(c)
        try:
            remote = _post("/api/check", {})
            c = {"active": remote["active"], "until": remote["until"], "source": remote["source"],
                 "days_left": remote["days_left"], "trial_until": remote.get("trial_until", 0),
                 "checked_at": time.time(), "offline": False}
            _save(c)
        except Exception as e:
            if c:  # grace: держим последний статус ещё 3 дня
                c["offline"] = True
                c["offline_error"] = str(e)
                if c.get("until", 0) < time.time() and c.get("checked_at", 0) + GRACE_SECONDS < time.time():
                    c["active"] = False
                _save(c)
            else:
                c = {"active": False, "until": 0, "source": "none", "days_left": 0,
                     "offline": True, "offline_error": str(e), "checked_at": 0}
        return _public(c)


def _public(c):
    return {
        "active": bool(c.get("active")),
        "until": c.get("until", 0),
        "days_left": c.get("days_left", 0),
        "source": c.get("source", "none"),
        "trial_until": c.get("trial_until", 0),
        "offline": c.get("offline", False),
        "offline_error": c.get("offline_error"),
        "configured": bool(LICENSE_URL),
    }


def is_active():
    # Подписка обязательна всегда. Нет связи с сервером лицензий → работает только grace-период
    # (последний успешный статус держится 3 дня), потом отключается.
    return status().get("active", False)


def activate(code):
    code = (code or "").strip().upper()
    if not code:
        raise ValueError("Введите код подписки")
    remote = _post("/api/activate", {"code": code})
    _save({"active": remote["active"], "until": remote["until"], "source": remote["source"],
           "days_left": remote["days_left"], "trial_until": remote.get("trial_until", 0),
           "checked_at": time.time(), "offline": False})
    db.log(f"Подписка активирована: +{remote.get('added_months', 0)} мес, активна до "
           f"{time.strftime('%d.%m.%Y', time.localtime(remote['until']))}")
    return status(force=True)


def _loop():
    while True:
        time.sleep(3600)
        try:
            status(force=True)
        except Exception:
            pass


def start():
    if LICENSE_URL:
        threading.Thread(target=_loop, daemon=True, name="subscription").start()
