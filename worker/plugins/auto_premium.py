"""AutoPremium — выдача Telegram Premium через fragment-api.com.

Схема (по рабочему образцу tinechelovec, через официальный Fragment API):
1. Покупатель купил Premium-лот (3/6/12 месяцев).
2. Бот спрашивает Telegram-юзернейм покупателя.
3. Проверяет через Fragment, что username существует и Premium ещё нет.
4. Покупатель подтверждает.
5. Бот выдаёт Premium через fragment-api.com (оплата TON с привязанного кошелька).

Нужно (в настройках, хранится зашифрованно):
- Fragment API Key (с fragment-api.com)
- Телефон номера Fragment-аккаунта
- Мнемоника TON-кошелька (12/24 слова) — с него оплачивается Premium
- Версия кошелька (v4r2 или w5)

Безопасность: Premium выдаётся ИМЕННО тому юзернейму, которого указал покупатель.
Всё через официальный API fragment-api.com. Никаких зашитых получателей и закладок.
Каждая выдача логируется.
"""
import re
import json
import time

from core import db

NAME = "AutoPremium"
DESCRIPTION = "Выдаёт Telegram Premium покупателю через Fragment API. Получатель — юзернейм покупателя."
CATEGORY = "Telegram"
VERSION = "1.0"
TIMEOUT = 600

FRAGMENT_API_URL = "https://api.fragment-api.com/v1"
MONTHS_ALLOWED = (3, 6, 12)

SETTINGS = [
    {"key": "api_key", "label": "Fragment API Key", "type": "secret",
     "hint": "Ключ с fragment-api.com. Хранится в зашифрованном виде."},
    {"key": "phone", "label": "Телефон Fragment-аккаунта", "type": "secret",
     "hint": "Номер телефона аккаунта Fragment (в международном формате)."},
    {"key": "mnemonic", "label": "Мнемоника TON-кошелька (12/24 слова)", "type": "secret",
     "hint": "Слова кошелька, с которого оплачивается Premium. Держите на нём запас TON."},
    {"key": "wallet_version", "label": "Версия кошелька", "type": "select", "options": ["v4r2", "w5"], "default": "v4r2",
     "hint": "Версия вашего TON-кошелька. Обычно v4r2."},
    {"key": "ask_username", "label": "Запрос юзернейма", "type": "textarea",
     "default": "Спасибо за заказ Telegram Premium на {months} мес!\nПришлите ваш Telegram-юзернейм (@username), куда подключить Premium."},
    {"key": "confirm_text", "label": "Подтверждение", "type": "textarea",
     "default": "Проверил: {username} — всё верно. Подключить Premium на {months} мес?\nНапишите «да» или «нет», чтобы указать другой юзернейм."},
    {"key": "done_text", "label": "После выдачи", "type": "textarea",
     "default": "Готово! Telegram Premium на {months} мес подключён для {username}. Спасибо за покупку! 💜"},
    {"key": "notfound_text", "label": "Юзернейм не найден", "type": "text",
     "default": "Не нашёл такого Telegram-юзернейма. Проверьте и пришлите ещё раз (например, @username)."},
    {"key": "has_premium_text", "label": "Premium уже есть", "type": "text",
     "default": "У этого аккаунта уже есть активный Premium. Укажите другой юзернейм или обратитесь к продавцу."},
    {"key": "remind_text", "label": "Напоминание", "type": "text",
     "default": "Напоминаем: пришлите Telegram-юзернейм, чтобы мы подключили Premium."},
    {"key": "cancel_text", "label": "Отмена/ошибка", "type": "text",
     "default": "Не получилось оформить выдачу. Обратитесь к продавцу — он поможет."},
]


def _jobs():
    r = db.query("SELECT value FROM plugin_kv WHERE plugin_id='auto_premium' AND key='jobs'")
    return json.loads(r[0]["value"]) if r else {}


def _save(d):
    db.execute("INSERT OR REPLACE INTO plugin_kv(plugin_id,key,value) VALUES('auto_premium','jobs',?)",
               (json.dumps(d, ensure_ascii=False),))


def _tok():
    r = db.query("SELECT value FROM plugin_kv WHERE plugin_id='auto_premium' AND key='token'")
    return r[0]["value"] if r else ""


def _save_tok(t):
    db.execute("INSERT OR REPLACE INTO plugin_kv(plugin_id,key,value) VALUES('auto_premium','token',?)", (t,))


def _fill(t, **kw):
    for k, v in kw.items():
        t = t.replace("{" + k + "}", str(v))
    return t


def _clean_username(u):
    u = (u or "").strip()
    for p in ("https://t.me/", "http://t.me/", "t.me/", "@"):
        if u.lower().startswith(p):
            u = u[len(p):]
    return u.strip().split()[0] if u.strip() else ""


def _months(order, ctx):
    desc = (order.get("description") or "").lower()
    for pat in (r"\b(3|6|12)\s*(?:m|mo|months|мес|месяц(?:ев|а)?|м)\b", r"\bна\s*(3|6|12)\s*(?:мес|месяц(?:ев|а)?|м)\b", r"\b(3|6|12)\b"):
        m = re.search(pat, desc)
        if m and int(m.group(1)) in MONTHS_ALLOWED:
            return int(m.group(1))
    return 3


# ---------- Fragment API ----------
def _auth(cfg):
    """Авторизация в fragment-api.com → JWT токен."""
    import requests
    try:
        payload = {
            "api_key": cfg.get("api_key") or "",
            "phone_number": cfg.get("phone") or "",
            "version": "V4R2" if (cfg.get("wallet_version") or "v4r2") == "v4r2" else "W5",
            "mnemonics": (cfg.get("mnemonic") or "").strip().split(),
        }
        r = requests.post(f"{FRAGMENT_API_URL}/auth/authenticate/", json=payload, timeout=30)
        if r.status_code == 200:
            t = r.json().get("token")
            if t:
                _save_tok(t)
                return t
    except Exception:
        pass
    return ""


def _headers(token):
    return {"Accept": "application/json", "Authorization": f"JWT {token}", "Content-Type": "application/json"}


def _check_user(cfg, username, token):
    """Возвращает (существует, есть_premium)."""
    import requests
    clean = _clean_username(username)
    try:
        r = requests.get(f"{FRAGMENT_API_URL}/misc/user/{clean}/", headers=_headers(token), timeout=20)
        if r.status_code != 200:
            return False, False
        data = r.json()
        has = False
        if isinstance(data, dict):
            for k in ("is_premium", "has_premium", "premium", "premium_until", "premium_expires"):
                if data.get(k):
                    has = True
                    break
        exists = bool(data)
        return exists, has
    except Exception:
        return False, False


def _send_premium(cfg, username, months, token):
    """Выдаёт Premium. Возвращает (ok, info)."""
    import requests
    payload = {"username": _clean_username(username), "months": months,
               "wallet_version": (cfg.get("wallet_version") or "v4r2")}
    try:
        r = requests.post(f"{FRAGMENT_API_URL}/order/premium/", json=payload, headers=_headers(token), timeout=60)
        if r.status_code == 200:
            return True, r.text
        return False, r.text
    except Exception as e:
        return False, str(e)


def _ensure_token(cfg):
    t = _tok()
    return t or _auth(cfg)


# ---------- обработка заказа ----------
def on_new_order(order, ctx):
    cfg = ctx.config
    for k in ("api_key", "phone", "mnemonic"):
        if not (cfg.get(k) or "").strip():
            ctx.log(f"AutoPremium не настроен (нет {k}) — заказ #{order['id']} пропущен", "warn")
            return ctx.SKIP
    months = _months(order, ctx)
    buyer_id = order.get("buyer_id")
    if not buyer_id:
        return ctx.SKIP
    jobs = _jobs()
    jobs[str(order["id"])] = {
        "order_id": order["id"], "account_id": order["account_id"],
        "buyer": order["buyer"], "buyer_id": buyer_id,
        "months": months, "stage": "await_username", "username": "",
        "ts": time.time(), "reminded": False,
    }
    _save(jobs)
    ctx.send_to_buyer(order["account_id"], buyer_id, _fill(cfg["ask_username"], months=months))
    ctx.log(f"AutoPremium: заказ #{order['id']} на Premium {months} мес — жду юзернейм", "order")
    return "Жду юзернейм покупателя"


def on_message(msg, chat, ctx):
    text = (msg.get("text") or "").strip()
    if not text:
        return False
    cfg = ctx.config
    jobs = _jobs()
    job = None
    for j in jobs.values():
        if j["account_id"] == chat["account_id"] and (j["buyer"] or "").lower() == (chat["name"] or "").lower():
            job = j
            break
    if not job:
        return False
    low = text.lower()

    if job["stage"] == "await_username":
        token = _ensure_token(cfg)
        if not token:
            ctx.log(f"AutoPremium: не удалось авторизоваться в Fragment API (заказ #{job['order_id']})", "error")
            ctx.reply(chat, cfg["cancel_text"])
            return True
        exists, has = _check_user(cfg, text, token)
        if not exists:
            ctx.reply(chat, cfg["notfound_text"])
            return True
        if has:
            ctx.reply(chat, cfg["has_premium_text"])
            return True
        job["username"] = _clean_username(text)
        job["stage"] = "await_confirm"
        job["ts"] = time.time()
        jobs[str(job["order_id"])] = job
        _save(jobs)
        ctx.reply(chat, _fill(cfg["confirm_text"], username="@" + job["username"], months=job["months"]))
        return True

    if job["stage"] == "await_confirm":
        if low in ("да", "yes", "+", "подтверждаю", "ок", "ok"):
            token = _ensure_token(cfg)
            # ЛОГ: кому и на сколько выдаём (прозрачность)
            ctx.log(f"AutoPremium #{job['order_id']}: выдаю Premium {job['months']} мес на @{job['username']} "
                    f"(через fragment-api.com)", "order")
            ok, info = _send_premium(cfg, job["username"], job["months"], token)
            if not ok and "401" in str(info):  # токен протух — обновим и повторим
                token = _auth(cfg)
                ok, info = _send_premium(cfg, job["username"], job["months"], token)
            if ok:
                jobs.pop(str(job["order_id"]), None)
                _save(jobs)
                ctx.reply(chat, _fill(cfg["done_text"], username="@" + job["username"], months=job["months"]))
                ctx.log(f"AutoPremium: выдан Premium {job['months']} мес на @{job['username']} (заказ #{job['order_id']})", "order")
            else:
                ctx.reply(chat, cfg["cancel_text"])
                ctx.log(f"AutoPremium: ПРОВЕРЬТЕ заказ #{job['order_id']} — Premium не выдан: {str(info)[:120]}", "error")
            return True
        if low in ("нет", "no", "-", "другой"):
            job["stage"] = "await_username"
            job["ts"] = time.time()
            jobs[str(job["order_id"])] = job
            _save(jobs)
            ctx.reply(chat, _fill(cfg["ask_username"], months=job["months"]))
            return True
    return False


def tick(ctx):
    cfg = ctx.config
    jobs = _jobs()
    now = time.time()
    changed = False
    for oid, job in list(jobs.items()):
        age = now - job.get("ts", now)
        if age > TIMEOUT:
            try:
                ctx.send_to_buyer(job["account_id"], job["buyer_id"], cfg["cancel_text"])
            except Exception:
                pass
            jobs.pop(oid, None)
            changed = True
            ctx.log(f"AutoPremium: заказ #{job['order_id']} — покупатель не завершил, ожидание снято", "warn")
        elif age > TIMEOUT / 2 and not job.get("reminded"):
            try:
                ctx.send_to_buyer(job["account_id"], job["buyer_id"], cfg["remind_text"])
            except Exception:
                pass
            job["reminded"] = True
            jobs[oid] = job
            changed = True
    if changed:
        _save(jobs)


def test(ctx):
    """Проверка: авторизация в Fragment API."""
    cfg = ctx.config
    for k in ("api_key", "phone", "mnemonic"):
        if not (cfg.get(k) or "").strip():
            return {"ok": False, "message": f"Не заполнено поле: {k}"}
    t = _auth(cfg)
    if t:
        return {"ok": True, "message": "Fragment API: авторизация успешна, кошелёк подключён"}
    return {"ok": False, "message": "Не удалось авторизоваться — проверьте API Key, телефон и мнемонику"}
