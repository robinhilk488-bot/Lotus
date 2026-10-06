"""Плагины и надёжная очередь заказов.

Все плагины встроены: лежат в папке plugins/ и обновляются вместе с воркером.
В приложении их можно только включать, выключать и настраивать.
Каталог (включая ещё не готовые) — core/catalog.py.

Плагин — один .py файл с id из каталога. Полный пример: plugins/_template_supplier.py.

    NAME = "Мой плагин"
    DESCRIPTION = "Что он делает"
    VERSION = "1.0"
    TIMEOUT = 120            # сколько секунд максимум может идти обработка одного заказа
    SETTINGS = [
        {"key": "api_key", "label": "API-ключ", "type": "secret", "required": True,
         "hint": "Где взять ключ"},
        # типы: text, textarea, number, bool, select (+ "options"), secret
    ]

    def test(ctx):                 # кнопка «Проверить подключение»
        return "Ключ работает, баланс 100 $"

    def on_new_order(order, ctx):  # вызывается ровно один раз на каждый заказ
        ...

Как устроена защита денег:
- Каждый заказ для каждого плагина записывается в очередь (таблица plugin_tasks) ДО обработки.
  Если сервер перезагрузится, заказ не потеряется.
- Один и тот же заказ плагин получает только один раз (уникальная пара плагин + заказ).
- Автоматический повтор только если плагин сам сказал, что это безопасно:
  raise ctx.Retry("причина") — значит, он ничего не успел сделать (например, API поставщика не ответил).
- Любая другая ошибка, зависание или перезапуск сервера посреди обработки → статус «Нужна проверка».
  Такой заказ не повторяется сам: вы смотрите, выдан ли товар, и жмёте «Повторить» или «Выполнено вручную».
  Так исключается двойная выдача за ваш счёт.
"""
import importlib.util
import json
import threading
import time
import traceback
from pathlib import Path

import requests

from . import accounts, db
from .catalog import CATALOG, CATEGORIES
from .crypto import decrypt, encrypt
from .notify import notify

PLUGINS_DIR = Path(__file__).resolve().parent.parent / "plugins"

SETTING_TYPES = {"text", "textarea", "number", "bool", "select", "secret"}
DEFAULT_TIMEOUT = 120
MAX_ATTEMPTS = 5
SKIP = object()

_loaded: dict[str, object] = {}
_lock = threading.RLock()
_wake = threading.Event()


class Retry(Exception):
    """Плагин ничего не сделал, заказ можно безопасно обработать ещё раз позже."""

    def __init__(self, reason="", delay=60):
        super().__init__(reason)
        self.delay = delay


class HttpSession(requests.Session):
    """requests.Session, у которого всегда есть таймаут, чтобы плагин не завис навсегда."""

    def request(self, *a, **kw):
        kw.setdefault("timeout", 20)
        return super().request(*a, **kw)


class Storage:
    """Постоянное хранилище плагина: ctx.storage.get(key) / set(key, value)."""

    def __init__(self, pid):
        self.pid = pid

    def get(self, key, default=None):
        r = db.query("SELECT value FROM plugin_kv WHERE plugin_id=? AND key=?", (self.pid, key))
        return json.loads(r[0]["value"]) if r else default

    def set(self, key, value):
        db.execute("INSERT OR REPLACE INTO plugin_kv(plugin_id, key, value) VALUES(?,?,?)",
                   (self.pid, key, json.dumps(value, ensure_ascii=False)))

    def delete(self, key):
        db.execute("DELETE FROM plugin_kv WHERE plugin_id=? AND key=?", (self.pid, key))


class Ctx:
    Retry = Retry
    SKIP = SKIP

    def __init__(self, pid, dry_run=False):
        mod = _loaded[pid]
        self.plugin_id = pid
        self.name = getattr(mod, "NAME", pid)
        self.config = get_config(pid)
        self.dry_run = dry_run      # True при пробном запуске: плагин не должен ничего покупать и отправлять
        self.storage = Storage(pid)
        self.http = HttpSession()
        self.lines = []

    def log(self, text, level="info"):
        self.lines.append(str(text))
        if not self.dry_run:
            db.log(f"[{self.name}] {text}", level)

    def send_message(self, order, text):
        """Написать покупателю этого заказа в чат FunPay. При пробном запуске только пишет в журнал."""
        if self.dry_run:
            self.log(f"Пробный запуск: отправил бы покупателю:\n{text}")
            return
        if not order.get("buyer_id"):
            raise RuntimeError("Не удалось определить покупателя заказа — сообщение не отправлено")
        with accounts.use(order["account_id"]) as fp:
            fp.send_message(fp.chat_with(order["buyer_id"]), text)

    def send_to_buyer(self, account_id, buyer_id, text):
        """Написать покупателю по его id (для напоминаний и сообщений вне заказа)."""
        if self.dry_run:
            self.log(f"Пробный запуск: отправил бы покупателю:\n{text}")
            return
        with accounts.use(account_id) as fp:
            fp.send_message(fp.chat_with(buyer_id), text)

    def reply(self, chat, text):
        """Ответить в чат, из которого пришло сообщение (для команд покупателя)."""
        with accounts.use(chat["account_id"]) as fp:
            fp.send_message(chat["id"], text)

    def order_quantity(self, order):
        if self.dry_run:
            return order.get("quantity", 1)
        with accounts.use(order["account_id"]) as fp:
            return fp.get_order_quantity(order["id"])

    def set_lot_active(self, account_id, offer_id, active):
        if self.dry_run:
            self.log(f"Пробный запуск: {'включил' if active else 'выключил'} бы лот {offer_id}")
            return
        with accounts.use(account_id) as fp:
            fp.set_lot_active(offer_id, active)

    def reply_to_review(self, account_id, order_id, text):
        """Публикует ответ продавца под отзывом покупателя."""
        if self.dry_run:
            self.log(f"Пробный запуск: ответил бы под отзывом #{order_id}: {text[:40]}")
            return
        with accounts.use(account_id) as fp:
            fp.reply_to_review(order_id, text)

    def lot_info(self, account_id, offer_id):
        """Название, описание и цена лота (для ИИ-ответов по товару)."""
        with accounts.use(account_id) as fp:
            return fp.get_lot_info(offer_id)

    def add_cost(self, order, amount):
        """Записать себестоимость заказа (в валюте FunPay-аккаунта) — для расчёта чистой прибыли."""
        if not self.dry_run:
            db.execute("INSERT OR REPLACE INTO order_costs(order_id, source, amount) VALUES(?,?,?)",
                       (order["id"], self.plugin_id, float(amount)))

    def notify(self, text, kind="attention"):
        if not self.dry_run:
            notify(kind, f"[{self.name}] {text}")


# ---------------- состояние и настройки ----------------
def _state(pid):
    rows = db.query("SELECT enabled, config FROM plugin_state WHERE id=?", (pid,))
    return rows[0] if rows else {"enabled": 0, "config": "{}"}


def _settings(pid):
    return [s for s in getattr(_loaded[pid], "SETTINGS", []) if s.get("type") in SETTING_TYPES]


def get_config(pid):
    """Настройки с расшифрованными секретами — только для самого плагина."""
    cfg = {s["key"]: s.get("default", "") for s in _settings(pid)}
    stored = json.loads(_state(pid)["config"])
    for s in _settings(pid):
        k = s["key"]
        if k not in stored:
            continue
        if s["type"] == "secret":
            try:
                cfg[k] = decrypt(stored[k]) if stored[k] else ""
            except Exception:
                cfg[k] = ""
        else:
            cfg[k] = stored[k]
    return cfg


def _public_config(pid):
    """Настройки для приложения: вместо секретов только отметка «сохранён»."""
    cfg = get_config(pid)
    secrets_set = {}
    for s in _settings(pid):
        if s["type"] == "secret":
            secrets_set[s["key"]] = bool(cfg[s["key"]])
            cfg[s["key"]] = ""
    return cfg, secrets_set


def _missing_required(pid):
    cfg = get_config(pid)
    return [s["label"] for s in _settings(pid)
            if s.get("required") and (cfg.get(s["key"]) in ("", None))]


def _coerce(s, v):
    t = s["type"]
    if t == "bool":
        return bool(v)
    if t == "number":
        try:
            return float(v) if "." in str(v) else int(v)
        except (TypeError, ValueError):
            raise ValueError(f"«{s['label']}» должно быть числом")
    if t == "select" and s.get("options") and v not in s["options"]:
        raise ValueError(f"«{s['label']}»: недопустимое значение")
    return str(v if v is not None else "").strip() if t in ("text", "secret") else str(v or "")


# ---------------- загрузка ----------------
def load_all():
    with _lock:
        _loaded.clear()
        for path in sorted(PLUGINS_DIR.glob("*.py")):
            if path.name.startswith("_"):
                continue
            pid = path.stem
            try:
                spec = importlib.util.spec_from_file_location(f"kassa_plugin_{pid}", path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                _loaded[pid] = mod
            except Exception:
                db.log(f"Плагин {path.name} не загрузился:\n{traceback.format_exc(limit=3)}", "error")


def describe():
    out = []
    for pid, mod in _loaded.items():
        cfg, secrets_set = _public_config(pid)
        counts = {r["status"]: r["c"] for r in db.query(
            "SELECT status, COUNT(*) c FROM plugin_tasks WHERE plugin_id=? GROUP BY status", (pid,))}
        out.append({
            "id": pid,
            "ready": True,
            "name": getattr(mod, "NAME", pid),
            "description": getattr(mod, "DESCRIPTION", ""),
            "category": getattr(mod, "CATEGORY", "Уведомления"),
            "version": getattr(mod, "VERSION", "1.0"),
            "enabled": bool(_state(pid)["enabled"]),
            "settings": _settings(pid),
            "config": cfg,
            "secrets_set": secrets_set,
            "missing": _missing_required(pid),
            "can_test": callable(getattr(mod, "test", None)),
            "can_dry_run": callable(getattr(mod, "on_new_order", None)),
            "tasks": {"done": counts.get("done", 0), "attention": counts.get("attention", 0),
                      "pending": counts.get("pending", 0) + counts.get("running", 0)},
        })
    for pid, name, desc, category, needs in CATALOG:
        if pid in _loaded:
            continue
        out.append({"id": pid, "ready": False, "name": name, "description": desc, "category": category,
                    "needs": needs, "enabled": False, "settings": [], "config": {}, "secrets_set": {},
                    "missing": [], "can_test": False, "can_dry_run": False,
                    "tasks": {"done": 0, "attention": 0, "pending": 0}})
    order = {c: i for i, c in enumerate(CATEGORIES)}
    out.sort(key=lambda p: (order.get(p["category"], 99), not p["ready"], p["name"].lower()))
    return out


def resume_all():
    """После продления подписки — ничего дополнительно делать не нужно, очередь сама продолжит.
    Функция оставлена как явная точка для будущих действий при возобновлении."""
    db.log("Подписка активна — плагины снова принимают новые заказы")


def set_enabled(pid, enabled: bool):
    if pid not in _loaded:
        if any(c[0] == pid for c in CATALOG):
            raise ValueError("Этот плагин ещё в разработке и пока не может быть включён")
        raise KeyError(pid)
    if enabled and (missing := _missing_required(pid)):
        raise ValueError("Сначала заполните в настройках: " + ", ".join(missing))
    db.execute("INSERT OR REPLACE INTO plugin_state(id, enabled, config) VALUES(?, ?, ?)",
               (pid, int(enabled), _state(pid)["config"]))


def set_config(pid, config: dict):
    """Секрет, пришедший пустым, не меняется. Чтобы стереть секрет, передайте {"__clear__": ["key"]}."""
    if pid not in _loaded:
        raise KeyError(pid)
    stored = json.loads(_state(pid)["config"])
    clear = set(config.get("__clear__", []))
    for s in _settings(pid):
        k = s["key"]
        if s["type"] == "secret":
            if k in clear:
                stored[k] = ""
            elif config.get(k):
                stored[k] = encrypt(_coerce(s, config[k]))
        elif k in config:
            stored[k] = _coerce(s, config[k])
    db.execute("INSERT OR REPLACE INTO plugin_state(id, enabled, config) VALUES(?, ?, ?)",
               (pid, _state(pid)["enabled"], json.dumps(stored, ensure_ascii=False)))
    if _state(pid)["enabled"] and _missing_required(pid):
        set_enabled(pid, False)
        db.log(f"Плагин {pid} выключен: не заполнены обязательные настройки", "error")


# ---------------- проверка и пробный запуск ----------------
def _run_with_timeout(fn, timeout):
    box = {}

    def target():
        try:
            box["result"] = fn()
        except BaseException as e:  # noqa: BLE001
            box["error"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None, TimeoutError(f"не уложился в {timeout} сек"), t
    return box.get("result"), box.get("error"), None


def test(pid):
    mod = _loaded.get(pid)
    if not mod or not callable(getattr(mod, "test", None)):
        raise KeyError(pid)
    if missing := _missing_required(pid):
        return {"ok": False, "message": "Не заполнено: " + ", ".join(missing)}
    ctx = Ctx(pid)
    res, err, _ = _run_with_timeout(lambda: mod.test(ctx), 30)
    if err:
        return {"ok": False, "message": str(err) or type(err).__name__}
    return {"ok": True, "message": str(res or "Подключение работает")}


SAMPLE_ORDER = {"id": "TEST0001", "buyer": "test_buyer", "description": "Пробный заказ", "amount": 100.0,
                "currency": "₽", "status": "paid", "account": "Пробный", "ts": 0}


def dry_run(pid, order=None):
    """Запуск на пробном заказе с ctx.dry_run=True. Ничего не пишется в очередь."""
    mod = _loaded.get(pid)
    if not mod or not callable(getattr(mod, "on_new_order", None)):
        raise KeyError(pid)
    ctx = Ctx(pid, dry_run=True)
    o = {**SAMPLE_ORDER, **(order or {}), "ts": time.time()}
    res, err, _ = _run_with_timeout(lambda: mod.on_new_order(o, ctx), getattr(mod, "TIMEOUT", DEFAULT_TIMEOUT))
    if isinstance(err, Retry):
        return {"ok": False, "message": f"Плагин попросил повторить позже: {err}", "log": ctx.lines}
    if err:
        return {"ok": False, "message": f"{type(err).__name__}: {err}", "log": ctx.lines}
    return {"ok": True, "message": "Пропущен плагином" if res is SKIP else "Отработал без ошибок", "log": ctx.lines}


# ---------------- очередь заказов ----------------
def enqueue_order(order: dict):
    """Ставит заказ в очередь каждому включённому плагину с on_new_order. Повторная постановка игнорируется.
    Без активной подписки новые заказы не принимаются, но уже стоящие в очереди дорабатываются."""
    from . import subscription
    if not subscription.is_active():
        db.log(f"Заказ #{order['id']} не передан плагинам: подписка неактивна", "warn")
        return
    now = time.time()
    for pid, mod in list(_loaded.items()):
        if callable(getattr(mod, "on_new_order", None)) and _state(pid)["enabled"]:
            db.execute("INSERT OR IGNORE INTO plugin_tasks(plugin_id, order_id, payload, created, updated) VALUES(?,?,?,?,?)",
                       (pid, order["id"], json.dumps(order, ensure_ascii=False), now, now))
    _wake.set()


def _finish(task_id, status, result=None, error=None, next_try=0):
    db.execute("UPDATE plugin_tasks SET status=?, result=?, error=?, next_try=?, updated=? WHERE id=?",
               (status, result, error, next_try, time.time(), task_id))


def _alert(ctx, order, reason):
    notify("attention", f"🔴 Нужна проверка: заказ #{order['id']} ({order.get('account', '')})\n"
                        f"{ctx.name}: {reason}\nПокупатель: {order.get('buyer')}, {order.get('amount')} {order.get('currency')}\n"
                        f"Откройте Lotus → Плагины.")


def _process(task):
    pid = task["plugin_id"]
    mod = _loaded.get(pid)
    if not mod:
        _finish(task["id"], "attention", error="Плагин удалён или не загрузился")
        return
    if not _state(pid)["enabled"]:
        return  # выключен — ждём, пока включат снова
    order = json.loads(task["payload"])
    ctx = Ctx(pid)
    attempts = task["attempts"] + 1
    db.execute("UPDATE plugin_tasks SET status='running', attempts=?, updated=? WHERE id=?",
               (attempts, time.time(), task["id"]))
    timeout = getattr(mod, "TIMEOUT", DEFAULT_TIMEOUT)
    res, err, hung = _run_with_timeout(lambda: mod.on_new_order(order, ctx), timeout)

    if hung:
        _finish(task["id"], "attention", error=f"Обработка зависла ({timeout} сек). Проверьте, выдан ли товар.")
        _alert(ctx, order, "обработка зависла")
        db.log(f"[{ctx.name}] заказ #{order['id']}: обработка зависла — нужна проверка", "error")

        def late():  # если плагин всё-таки закончит, запишем итог
            hung.join()
            if db.query("SELECT status FROM plugin_tasks WHERE id=?", (task["id"],))[0]["status"] == "attention":
                db.execute("UPDATE plugin_tasks SET error=? WHERE id=?",
                           ("Плагин завершил работу с опозданием. Проверьте результат и закройте заказ вручную.", task["id"]))
        threading.Thread(target=late, daemon=True).start()
    elif isinstance(err, Retry):
        if attempts >= MAX_ATTEMPTS:
            _finish(task["id"], "attention", error=f"{err} (после {attempts} попыток)")
            _alert(ctx, order, f"не удалось за {attempts} попыток: {err}")
            db.log(f"[{ctx.name}] заказ #{order['id']}: не удалось за {attempts} попыток — нужна проверка", "error")
        else:
            _finish(task["id"], "pending", error=str(err), next_try=time.time() + max(10, err.delay) * attempts)
            db.log(f"[{ctx.name}] заказ #{order['id']}: повтор позже ({err})", "warn")
    elif err:
        text = str(err) if type(err) is Exception else f"{type(err).__name__}: {err}"
        _finish(task["id"], "attention", error=text)
        _alert(ctx, order, text)
        db.log(f"[{ctx.name}] заказ #{order['id']}: {text.rstrip('.')} — нужна проверка", "error")
    elif res is SKIP:
        _finish(task["id"], "skipped")
    else:
        _finish(task["id"], "done", result=str(res) if res else None)


def _runner():
    # Всё, что было «в работе» при прошлом выключении сервера, — под ручную проверку.
    stuck = db.query("SELECT id, plugin_id, order_id FROM plugin_tasks WHERE status='running'")
    for t in stuck:
        _finish(t["id"], "attention", error="Сервер перезапустился во время обработки. Проверьте, выдан ли товар.")
        notify("attention", f"🔴 Нужна проверка: заказ #{t['order_id']} — сервер перезапустился во время обработки ({t['plugin_id']}).")
        db.log(f"[{t['plugin_id']}] заказ #{t['order_id']}: прерван перезапуском — нужна проверка", "error")
    while True:
        try:
            due = db.query("SELECT * FROM plugin_tasks WHERE status='pending' AND next_try<=? ORDER BY id LIMIT 20",
                           (time.time(),))
            for task in due:
                _process(task)
        except Exception:
            db.log("Сбой очереди плагинов:\n" + traceback.format_exc(limit=3), "error")
        _wake.wait(5)
        _wake.clear()


def start_runner():
    threading.Thread(target=_runner, daemon=True, name="plugin-runner").start()
    threading.Thread(target=_ticker, daemon=True, name="plugin-ticker").start()


# ---------------- события чата и таймер ----------------
def _enabled_with(hook):
    return [(pid, mod) for pid, mod in list(_loaded.items())
            if callable(getattr(mod, hook, None)) and _state(pid)["enabled"]]


def emit_message(acc, chat, msg) -> bool:
    """Сообщение покупателя. True — плагин обработал его как команду (приветствие не нужно)."""
    handled = False
    info = {"id": chat["id"], "name": chat["name"], "account_id": acc["id"], "account": acc["name"]}
    for pid, mod in _enabled_with("on_message"):
        ctx = Ctx(pid)
        try:
            handled = bool(mod.on_message(msg, info, ctx)) or handled
        except Exception as e:
            ctx.log(f"ошибка при обработке сообщения: {e}", "error")
    return handled


def emit_order_confirmed(acc, chat, order_id):
    data = {"order_id": order_id, "buyer": chat["name"], "chat_id": chat["id"], "account_id": acc["id"]}
    for pid, mod in _enabled_with("on_order_confirmed"):
        ctx = Ctx(pid)
        try:
            mod.on_order_confirmed(data, ctx)
        except Exception as e:
            ctx.log(f"ошибка при подтверждении заказа: {e}", "error")


def emit_review(acc, chat, order_id, rating):
    review = {"order_id": order_id, "rating": rating, "buyer": chat["name"], "chat_id": chat["id"], "account_id": acc["id"]}
    for pid, mod in _enabled_with("on_review"):
        ctx = Ctx(pid)
        try:
            mod.on_review(review, ctx)
        except Exception as e:
            ctx.log(f"ошибка при обработке отзыва: {e}", "error")


def _ticker():
    """Каждые 20 секунд вызывает on_tick у включённых плагинов (таймеры аренды и т.п.)."""
    while True:
        for pid, mod in _enabled_with("on_tick"):
            ctx = Ctx(pid)
            try:
                mod.on_tick(ctx)
            except Exception as e:
                ctx.log(f"ошибка таймера: {e}", "error")
        time.sleep(20)


# ---------------- ручное управление задачами ----------------
def list_tasks(status=None, limit=100):
    where, params = "", []
    if status == "attention":
        where = "WHERE status='attention'"
    elif status == "active":
        where = "WHERE status IN ('pending','running','attention')"
    rows = db.query(f"SELECT * FROM plugin_tasks {where} ORDER BY id DESC LIMIT ?", (*params, limit))
    for r in rows:
        o = json.loads(r.pop("payload"))
        r.update(buyer=o.get("buyer"), description=o.get("description"), amount=o.get("amount"),
                 currency=o.get("currency"), account=o.get("account"),
                 plugin_name=getattr(_loaded.get(r["plugin_id"]), "NAME", r["plugin_id"]))
    return rows


def retry_task(task_id):
    if not db.query("SELECT id FROM plugin_tasks WHERE id=? AND status='attention'", (task_id,)):
        raise ValueError("Повторить можно только заказ со статусом «Нужна проверка»")
    db.execute("UPDATE plugin_tasks SET status='pending', next_try=0, error=NULL, updated=? WHERE id=?",
               (time.time(), task_id))
    db.log(f"Задача #{task_id}: повтор запущен вручную")
    _wake.set()


def resolve_task(task_id):
    if not db.query("SELECT id FROM plugin_tasks WHERE id=? AND status IN ('attention','pending')", (task_id,)):
        raise ValueError("Задача уже закрыта или в работе")
    _finish(task_id, "done", result="Закрыто вручную")
    db.log(f"Задача #{task_id}: отмечена выполненной вручную")
