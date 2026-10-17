"""AutoSteam — пополнение баланса Steam покупателю через ns.gifts.

Схема (по рабочему образцу, через официальный API ns.gifts):
1. Покупатель купил лот на пополнение Steam (сумма в названии/заказе).
2. Бот спрашивает логин Steam покупателя.
3. Покупатель присылает логин, подтверждает.
4. Бот пополняет баланс Steam через ns.gifts (оплата с баланса вашего ns.gifts-аккаунта).

Нужно (в настройках, хранится зашифрованно):
- Email и пароль от аккаунта ns.gifts (для получения токена API)
- Баланс на ns.gifts — им оплачиваются пополнения

Безопасность: пополняется ИМЕННО тот логин Steam, который указал покупатель.
Всё через официальный API ns.gifts. Никаких отправок данных на сторону,
никаких зашитых получателей. Каждое пополнение логируется.
"""
import re
import json
import time
import uuid

from core import db

NAME = "AutoSteam"
DESCRIPTION = "Пополняет баланс Steam покупателю по логину через ns.gifts. Сумма — из заказа."
CATEGORY = "Выдача через поставщиков"
VERSION = "1.0"
TIMEOUT = 600

NS_URL = "https://api.ns.gifts/api/v1"
STEAM_SERVICE_ID = 1
MIN_AMOUNTS = {"RUB": 25, "UAH": 10, "KZT": 70}

SETTINGS = [
    {"key": "api_login", "label": "Email аккаунта ns.gifts", "type": "secret",
     "hint": "Почта, на которую зарегистрирован ns.gifts. Хранится в зашифрованном виде."},
    {"key": "api_password", "label": "Пароль ns.gifts", "type": "secret"},
    {"key": "currency", "label": "Валюта пополнения", "type": "select", "options": ["RUB", "UAH", "KZT"], "default": "RUB",
     "hint": "Валюта, в которой указана сумма в лоте."},
    {"key": "ask_login", "label": "Запрос логина Steam", "type": "textarea",
     "default": "Спасибо за заказ на пополнение Steam на {amount}!\nПришлите ваш логин Steam (никнейм для входа, не email)."},
    {"key": "confirm_text", "label": "Подтверждение", "type": "textarea",
     "default": "Проверьте данные:\nЛогин Steam: {login}\nСумма: {amount}\n\nЕсли всё верно — напишите «+» или «да». Если логин неверный — пришлите правильный."},
    {"key": "done_text", "label": "После пополнения", "type": "textarea",
     "default": "Готово! Баланс Steam «{login}» пополнен на {amount}. Спасибо за покупку! 💙\nНе забудьте подтвердить заказ и оставить отзыв."},
    {"key": "bad_login_text", "label": "Логин не найден", "type": "text",
     "default": "Steam не нашёл такой логин. Проверьте (нужен логин для входа, не email) и пришлите ещё раз."},
    {"key": "remind_text", "label": "Напоминание", "type": "text",
     "default": "Напоминаем: пришлите логин Steam, чтобы мы пополнили ваш баланс."},
    {"key": "fail_text", "label": "Ошибка пополнения", "type": "text",
     "default": "Не удалось пополнить баланс. Напишите продавцу — он поможет."},
]

_TOKEN = {"token": "", "expiry": 0}


def _jobs():
    r = db.query("SELECT value FROM plugin_kv WHERE plugin_id='auto_steam' AND key='jobs'")
    return json.loads(r[0]["value"]) if r else {}


def _save(d):
    db.execute("INSERT OR REPLACE INTO plugin_kv(plugin_id,key,value) VALUES('auto_steam','jobs',?)",
               (json.dumps(d, ensure_ascii=False),))


def _fill(t, **kw):
    for k, v in kw.items():
        t = t.replace("{" + k + "}", str(v))
    return t


def _amount_from_order(order, ctx):
    desc = order.get("description") or ""
    m = re.search(r"(\d+(?:[.,]\d+)?)", desc)
    per = float(m.group(1).replace(",", ".")) if m else 0
    qty = 1
    try:
        qty = ctx.order_quantity(order) or 1
    except Exception:
        pass
    return round(per * qty, 2) if per else 0


def _token(cfg):
    import requests
    if time.time() < _TOKEN["expiry"] and _TOKEN["token"]:
        return _TOKEN["token"]
    r = requests.post(f"{NS_URL}/get_token",
                      json={"email": cfg.get("api_login") or "", "password": cfg.get("api_password") or ""}, timeout=30)
    if r.status_code != 200:
        raise Exception(f"ns.gifts авторизация: {r.status_code}")
    data = r.json()
    tok = data.get("token") or data.get("access_token") or (data.get("data") or {}).get("token")
    _TOKEN["token"] = tok
    _TOKEN["expiry"] = data.get("valid_thru", time.time() + 7200)
    return tok


def _hdr(cfg):
    return {"Authorization": f"Bearer {_token(cfg)}", "Content-Type": "application/json"}


def _steam_usd(cfg, amount, currency):
    import requests
    r = requests.post(f"{NS_URL}/steam/get_amount",
                      json={"amount": round(amount, 2), "currency": currency}, headers=_hdr(cfg), timeout=30)
    if r.status_code == 200:
        return float(r.json().get("usd_price", 0))
    raise Exception(f"ns.gifts курс: {r.status_code}")


def _check_login(cfg, login, amount, currency):
    import requests
    try:
        usd = _steam_usd(cfg, amount, currency)
        custom_id = str(uuid.uuid4())
        r = requests.post(f"{NS_URL}/create_order",
                          json={"service_id": STEAM_SERVICE_ID, "quantity": f"{usd:.2f}",
                                "custom_id": custom_id, "data": login}, headers=_hdr(cfg), timeout=30)
        if r.status_code == 200:
            return True, r.json().get("custom_id", custom_id)
        if r.status_code == 400 and "no such login" in r.text.lower():
            return False, "bad_login"
        return False, f"ns.gifts: {r.status_code} {r.text[:80]}"
    except Exception as e:
        return False, str(e)


def _pay(cfg, custom_id):
    import requests
    try:
        r = requests.post(f"{NS_URL}/pay_order", json={"custom_id": custom_id}, headers=_hdr(cfg), timeout=60)
        if r.status_code == 200:
            return True, None
        msg = r.json().get("detail", r.text[:80])
        return False, msg
    except Exception as e:
        return False, str(e)


def on_new_order(order, ctx):
    cfg = ctx.config
    if not (cfg.get("api_login") or "").strip() or not (cfg.get("api_password") or "").strip():
        ctx.log(f"AutoSteam не настроен (нет данных ns.gifts) — заказ #{order['id']} пропущен", "warn")
        return ctx.SKIP
    amount = _amount_from_order(order, ctx)
    currency = cfg.get("currency") or "RUB"
    if amount < MIN_AMOUNTS.get(currency, 0):
        ctx.log(f"AutoSteam: сумма {amount} {currency} ниже минимума — заказ #{order['id']} пропущен", "warn")
        return ctx.SKIP
    buyer_id = order.get("buyer_id")
    if not buyer_id:
        return ctx.SKIP
    jobs = _jobs()
    jobs[str(order["id"])] = {
        "order_id": order["id"], "account_id": order["account_id"],
        "buyer": order["buyer"], "buyer_id": buyer_id,
        "amount": amount, "currency": currency, "login": "", "custom_id": "",
        "stage": "await_login", "ts": time.time(), "reminded": False,
    }
    _save(jobs)
    ctx.send_to_buyer(order["account_id"], buyer_id, _fill(cfg["ask_login"], amount=f"{amount} {currency}"))
    ctx.log(f"AutoSteam: заказ #{order['id']} на {amount} {currency} — жду логин Steam", "order")
    return "Жду логин Steam"


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
    amount_txt = f"{job['amount']} {job['currency']}"

    if job["stage"] == "await_login":
        login = text.split()[0]
        ok, info = _check_login(cfg, login, job["amount"], job["currency"])
        if not ok and info == "bad_login":
            ctx.reply(chat, cfg["bad_login_text"])
            return True
        if not ok:
            ctx.log(f"AutoSteam: ns.gifts ошибка по заказу #{job['order_id']}: {info}", "warn")
            ctx.reply(chat, cfg["fail_text"])
            return True
        job["login"] = login
        job["custom_id"] = info
        job["stage"] = "await_confirm"
        job["ts"] = time.time()
        jobs[str(job["order_id"])] = job
        _save(jobs)
        ctx.reply(chat, _fill(cfg["confirm_text"], login=login, amount=amount_txt))
        return True

    if job["stage"] == "await_confirm":
        if low in ("+", "да", "yes", "ок", "ok", "подтверждаю", "верно"):
            ctx.log(f"AutoSteam #{job['order_id']}: пополняю Steam «{job['login']}» на {amount_txt} (через ns.gifts)", "order")
            ok, err = _pay(cfg, job["custom_id"])
            if ok:
                jobs.pop(str(job["order_id"]), None)
                _save(jobs)
                ctx.reply(chat, _fill(cfg["done_text"], login=job["login"], amount=amount_txt))
                ctx.log(f"AutoSteam: пополнен Steam «{job['login']}» на {amount_txt} (заказ #{job['order_id']})", "order")
            else:
                ctx.reply(chat, cfg["fail_text"])
                ctx.log(f"AutoSteam: ПРОВЕРЬТЕ заказ #{job['order_id']} — не пополнено: {err}", "error")
            return True
        job["stage"] = "await_login"
        jobs[str(job["order_id"])] = job
        _save(jobs)
        return on_message(msg, chat, ctx)
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
                ctx.send_to_buyer(job["account_id"], job["buyer_id"], cfg["fail_text"])
            except Exception:
                pass
            jobs.pop(oid, None)
            changed = True
            ctx.log(f"AutoSteam: заказ #{job['order_id']} — покупатель не завершил, снято", "warn")
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
    import requests
    cfg = ctx.config
    if not (cfg.get("api_login") or "").strip():
        return {"ok": False, "message": "Не заданы данные ns.gifts"}
    try:
        tok = _token(cfg)
        r = requests.post(f"{NS_URL}/check_balance", headers={"Authorization": f"Bearer {tok}"}, timeout=20)
        if r.status_code == 200:
            bal = r.json()
            return {"ok": True, "message": f"ns.gifts на связи, баланс: {bal.get('balance', bal)}"}
        return {"ok": True, "message": "ns.gifts: авторизация успешна"}
    except Exception as e:
        return {"ok": False, "message": f"ns.gifts: {e}"}
