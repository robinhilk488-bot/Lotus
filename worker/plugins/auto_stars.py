"""AutoStars — автоматическая выдача Telegram Stars после оплаты.

Покупатель оплачивает лот, бот просит его Telegram-юзернейм, затем заказывает Stars
через ваш сервис-поставщик (по API) и подтверждает покупателю.

Подключается к сервису-поставщику Stars по API: вы указываете адрес API и ключ.
Количество Stars берётся из количества в заказе FunPay. Ключ хранится в зашифрованном виде.

Защита: количество не читается → на проверку; заказ в сервис уходит один раз;
нет юзернейма за заданное время → напоминание, затем прекращение ожидания и уведомление.
"""
import re
import time

from core import db

NAME = "AutoStars"
DESCRIPTION = "Автовыдача Telegram Stars через сервис-поставщик. Количество — из заказа, получатель — юзернейм покупателя."
CATEGORY = "Telegram"
VERSION = "1.0"
TIMEOUT = 60

SETTINGS = [
    {"key": "api_url", "label": "Адрес API поставщика", "type": "text", "default": "",
     "hint": "URL сервиса, где вы покупаете Stars (из его документации)."},
    {"key": "api_key", "label": "API-ключ поставщика", "type": "secret",
     "hint": "Ключ из личного кабинета сервиса. Хранится в зашифрованном виде."},
    {"key": "ask_username", "label": "Запрос юзернейма", "type": "textarea",
     "default": "Спасибо за заказ! Пришлите ваш Telegram-юзернейм (например, @username), куда зачислить {quantity} ⭐."},
    {"key": "done_text", "label": "После выдачи", "type": "text",
     "default": "Готово! {quantity} ⭐ отправлены на {username}. Спасибо за покупку!"},
    {"key": "max_price", "label": "Не заказывать дороже, за заказ", "type": "number", "default": 0,
     "hint": "Защита от скачка цены у поставщика. 0 — без лимита."},
    {"key": "remind_text", "label": "Напоминание про юзернейм", "type": "text",
     "default": "Напоминаем: пришлите Telegram-юзернейм, чтобы мы отправили ваши Stars."},
    {"key": "cancel_text", "label": "Прекращение ожидания", "type": "text",
     "default": "Без юзернейма заказ не может быть выполнен. Обратитесь к продавцу."},
]


def _jobs():
    import json
    r = db.query("SELECT value FROM plugin_kv WHERE plugin_id='auto_stars' AND key='jobs'")
    return json.loads(r[0]["value"]) if r else {}


def _save(d):
    import json
    db.execute("INSERT OR REPLACE INTO plugin_kv(plugin_id,key,value) VALUES('auto_stars','jobs',?)",
               (json.dumps(d, ensure_ascii=False),))


def _fill(t, **kw):
    for k, v in kw.items():
        t = t.replace("{" + k + "}", str(v))
    return t


def _api(ctx, action, **params):
    url = ctx.config.get("api_url", "").strip().rstrip("/")
    key = ctx.config.get("api_key", "").strip()
    if not url or not key:
        raise Exception("Не заполнен адрес API или ключ поставщика Stars")
    r = ctx.http.post(url, data={"key": key, "action": action, **params})
    try:
        return r.json()
    except ValueError:
        raise Exception(f"Поставщик вернул не JSON (код {r.status_code})")


def test(ctx):
    data = _api(ctx, "balance")
    if isinstance(data, dict) and data.get("error"):
        raise Exception(str(data["error"]))
    return f"Подключение работает. Баланс: {data.get('balance', data)}"


def on_new_order(order, ctx):
    qty = ctx.order_quantity(order)
    if not qty:
        raise Exception("Не удалось прочитать количество Stars из заказа")
    if ctx.dry_run:
        ctx.log(f"Пробный запуск: запросил бы юзернейм для {qty} ⭐")
        return "dry-run"
    d = _jobs()
    d[order["id"]] = {"stage": "await_username", "quantity": qty, "account_id": order["account_id"],
                      "buyer": order.get("buyer"), "buyer_id": order.get("buyer_id"),
                      "created": time.time(), "reminded": False, "ordered": False}
    _save(d)
    ctx.send_message(order, _fill(ctx.config["ask_username"], quantity=qty))
    ctx.log(f"AutoStars #{order['id']}: запросил юзернейм для {qty} ⭐", "order")
    return f"Ожидает юзернейм · {qty} ⭐"


def on_message(msg, chat, ctx):
    text = (msg.get("text") or "").strip()
    d = _jobs()
    for oid, job in list(d.items()):
        if job["account_id"] != chat["account_id"] or (job["buyer"] or "").lower() != (chat["name"] or "").lower():
            continue
        if job["stage"] != "await_username" or job.get("ordered"):
            return True
        m = re.search(r"@?[A-Za-z0-9_]{4,32}", text)
        if not m:
            ctx.reply(chat, "Это не похоже на юзернейм. Пришлите в формате @username.")
            return True
        username = m.group(0)
        if not username.startswith("@"):
            username = "@" + username
        # защита цены
        limit = float(ctx.config.get("max_price") or 0)
        if limit:
            try:
                info = _api(ctx, "price", quantity=job["quantity"])
                cost = float(info.get("price", 0)) if isinstance(info, dict) else 0
                if cost and cost > limit:
                    ctx.notify(f"🔴 AutoStars: заказ #{oid} дороже лимита ({cost} > {limit}). Проверьте вручную.")
                    ctx.log(f"AutoStars #{oid}: цена выше лимита", "error")
                    return True
            except Exception:
                pass
        job["ordered"] = True
        _save(d)
        try:
            res = _api(ctx, "buy", username=username, quantity=job["quantity"])
        except Exception as e:
            job["ordered"] = False
            _save(d)
            ctx.reply(chat, "Не удалось отправить Stars, попробуем ещё раз через минуту.")
            ctx.notify(f"🔴 AutoStars: заказ #{oid} не отправлен ({e}). Проверьте вручную.")
            return True
        if isinstance(res, dict) and res.get("error"):
            job["ordered"] = False
            _save(d)
            ctx.notify(f"🔴 AutoStars: поставщик отказал по заказу #{oid}: {res['error']}")
            ctx.reply(chat, "Не удалось отправить Stars — продавец уведомлён.")
            return True
        del d[oid]
        _save(d)
        ctx.reply(chat, _fill(ctx.config["done_text"], quantity=job["quantity"], username=username))
        ctx.log(f"AutoStars #{oid}: отправлено {job['quantity']} ⭐ на {username}", "order")
        return True
    return False


def on_tick(ctx):
    now = time.time()
    d = _jobs()
    changed = False
    for oid, job in list(d.items()):
        if job.get("ordered"):
            continue
        age = now - job["created"]
        if not job["reminded"] and age > 30 * 60 and job.get("buyer_id"):
            try:
                ctx.send_to_buyer(job["account_id"], job["buyer_id"], ctx.config["remind_text"])
                job["reminded"] = True
                changed = True
            except Exception:
                pass
        elif job["reminded"] and age > 60 * 60:
            if job.get("buyer_id"):
                try:
                    ctx.send_to_buyer(job["account_id"], job["buyer_id"], ctx.config["cancel_text"])
                except Exception:
                    pass
            ctx.notify(f"⏳ AutoStars: покупатель {job['buyer']} не прислал юзернейм по заказу #{oid}. Ожидание прекращено.")
            del d[oid]
            changed = True
    if changed:
        _save(d)
