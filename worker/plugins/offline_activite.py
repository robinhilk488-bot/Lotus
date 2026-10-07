"""Offline Activite — выдаёт покупателю код Steam Guard по команде.

Для аккаунтов, которые продаются с «оффлайн-активацией»: покупатель вошёл, Steam просит код
Guard, покупатель пишет в чат команду (по умолчанию !guard), и бот присылает актуальный код.

Аккаунты настраиваются на странице плагина: логин и maFile (файл из Steam Desktop
Authenticator). shared_secret хранится на сервере в зашифрованном виде.

Как бот понимает, чей код выдать:
- Если покупатель недавно покупал лот, в названии которого есть логин аккаунта — бот берёт этот аккаунт.
- Либо покупатель пишет команду с логином: !guard login123
- Если аккаунт один — бот отдаёт его код без уточнений.
Чтобы чужой не выманил код, по умолчанию бот отвечает только тем, у кого есть оплаченный
заказ с этим логином (можно отключить в настройках).
"""
import json
import time

from core import db
from core.crypto import decrypt, encrypt
from core.steam import SteamError, guard_code, parse_mafile

NAME = "Offline Activite"
DESCRIPTION = "Выдаёт покупателю код Steam Guard по команде !guard для настроенных аккаунтов."
CATEGORY = "Steam"
VERSION = "1.0"

SETTINGS = [
    {"key": "command", "label": "Команда для кода", "type": "text", "default": "!guard"},
    {"key": "only_buyers", "label": "Отвечать только покупателям этого аккаунта", "type": "bool", "default": True,
     "hint": "Код выдаётся, только если у написавшего есть оплаченный заказ с логином этого аккаунта. Защита от выманивания кода."},
    {"key": "reply", "label": "Текст с кодом", "type": "text", "default": "Код Steam Guard: {code} (действует ~30 секунд)"},
    {"key": "no_access", "label": "Если нет доступа", "type": "text",
     "default": "Код доступен только после покупки соответствующего аккаунта. Если вы купили — напишите продавцу."},
    {"key": "not_found", "label": "Если аккаунт не найден", "type": "text",
     "default": "Не нашёл аккаунт. Напишите команду с логином, например: !guard mylogin"},
    {"key": "limit", "label": "Лимит кодов на покупателя", "type": "number", "default": 0,
     "hint": "Сколько раз один покупатель может запросить код. 0 — без лимита."},
    {"key": "limit_hours", "label": "Период лимита, часов", "type": "number", "default": 24,
     "hint": "За сколько часов считается лимит. 0 — лимит навсегда (не сбрасывается)."},
    {"key": "limit_reached", "label": "Если лимит исчерпан", "type": "text",
     "default": "Вы исчерпали лимит запросов кода. Попробуйте позже или напишите продавцу."},
]


# ---------------- аккаунты (логин + shared_secret) ----------------
def accounts_raw():
    r = db.query("SELECT value FROM plugin_kv WHERE plugin_id='offline_activite' AND key='accounts'")
    return json.loads(r[0]["value"]) if r else []


def accounts_public():
    return [{"login": a["login"], "has_mafile": bool(a.get("shared_secret"))} for a in accounts_raw()]


def save_accounts(accs):
    db.execute("INSERT OR REPLACE INTO plugin_kv(plugin_id, key, value) VALUES('offline_activite','accounts',?)",
               (json.dumps(accs, ensure_ascii=False),))


def add_account(login, mafile_text):
    login = (login or "").strip()
    if not login:
        raise ValueError("Укажите логин")
    ss = parse_mafile(mafile_text)["shared_secret"]  # бросит SteamError при плохом файле
    accs = accounts_raw()
    if any(a["login"].lower() == login.lower() for a in accs):
        raise ValueError(f"Аккаунт {login} уже добавлен")
    accs.append({"login": login, "shared_secret": encrypt(ss)})
    save_accounts(accs)
    db.log(f"Offline Activite: добавлен аккаунт {login}")


def remove_account(login):
    save_accounts([a for a in accounts_raw() if a["login"] != login])


def _secret(login):
    for a in accounts_raw():
        if a["login"].lower() == login.lower():
            try:
                return decrypt(a["shared_secret"])
            except Exception:
                return None
    return None


def _buyer_has_account(buyer, login):
    """Есть ли у покупателя оплаченный заказ, в названии которого встречается логин аккаунта."""
    rows = db.query("SELECT description FROM orders WHERE buyer=? AND status IN ('paid','closed')", (buyer,))
    return any(login.lower() in (r["description"] or "").lower() for r in rows)


def _pick_account(text, buyer):
    accs = accounts_raw()
    # 1) логин прямо в команде
    for a in accs:
        if a["login"].lower() in text.lower():
            return a["login"]
    # 2) по заказам покупателя
    for a in accs:
        if _buyer_has_account(buyer, a["login"]):
            return a["login"]
    # 3) единственный аккаунт
    if len(accs) == 1:
        return accs[0]["login"]
    return None


def _usage_key(buyer, login):
    return f"usage:{login}:{(buyer or '').lower()}"


def _limit_ok(buyer, login, limit, period_hours):
    """True — лимит ещё не исчерпан (только проверка, счётчик НЕ трогает)."""
    if not limit or limit <= 0:
        return True
    key = _usage_key(buyer, login)
    r = db.query("SELECT value FROM plugin_kv WHERE plugin_id='offline_activite' AND key=?", (key,))
    rec = json.loads(r[0]["value"]) if r else {"count": 0, "reset": 0}
    now = time.time()
    # истёкший период = счётчик сброшен
    if period_hours and period_hours > 0 and rec.get("reset", 0) and now > rec["reset"]:
        return True
    return rec["count"] < limit


def _count_use(buyer, login, period_hours):
    """Засчитать выданный код. Вызывать ТОЛЬКО после успешной выдачи."""
    key = _usage_key(buyer, login)
    r = db.query("SELECT value FROM plugin_kv WHERE plugin_id='offline_activite' AND key=?", (key,))
    rec = json.loads(r[0]["value"]) if r else {"count": 0, "reset": 0}
    now = time.time()
    if period_hours and period_hours > 0:
        if rec.get("reset", 0) and now > rec["reset"]:
            rec = {"count": 0, "reset": now + period_hours * 3600}
        elif not rec.get("reset"):
            rec["reset"] = now + period_hours * 3600
    rec["count"] += 1
    db.execute("INSERT OR REPLACE INTO plugin_kv(plugin_id, key, value) VALUES('offline_activite', ?, ?)",
               (key, json.dumps(rec)))


def on_message(msg, chat, ctx):
    text = (msg.get("text") or "").strip()
    cmd = ctx.config["command"].strip().lower()
    if not text.lower().startswith(cmd):
        return False
    login = _pick_account(text, chat["name"])
    if not login:
        ctx.reply(chat, ctx.config["not_found"])
        return True
    if ctx.config.get("only_buyers", True) and not _buyer_has_account(chat["name"], login):
        ctx.reply(chat, ctx.config["no_access"])
        ctx.log(f"Offline Activite: отказано {chat['name']} в коде для {login} (нет заказа)", "warn")
        return True
    limit = int(ctx.config.get("limit") or 0)
    period = int(ctx.config.get("limit_hours") or 0)
    # только ПРОВЕРЯЕМ лимит; засчитаем после успешной выдачи, чтобы ошибка не тратила попытку
    if limit and not _limit_ok(chat["name"], login, limit, period):
        ctx.reply(chat, ctx.config["limit_reached"])
        ctx.log(f"Offline Activite: лимит кодов исчерпан у {chat['name']} для {login}", "warn")
        return True
    ss = _secret(login)
    if not ss:
        ctx.reply(chat, "Для этого аккаунта не загружен maFile — напишите продавцу.")
        return True
    try:
        code = guard_code(ss)
    except SteamError:
        ctx.reply(chat, "Не удалось получить код — напишите продавцу.")
        return True
    ctx.reply(chat, ctx.config["reply"].replace("{code}", code).replace("{login}", login))
    if limit:
        _count_use(chat["name"], login, period)  # засчитываем ТОЛЬКО успешную выдачу
    ctx.log(f"Offline Activite: выдан код для {login} ({chat['name']})")
    return True
