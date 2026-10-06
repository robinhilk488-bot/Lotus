"""RobloxVIP — автовыдача Robux / VIP Roblox после оплаты.

Покупатель оплачивает лот, бот просит его ник в Roblox и заказывает выдачу через ваш
сервис-поставщик по API. Количество Robux берётся из количества в заказе FunPay.
Тип выдачи (robux / vip / gamepass) задаётся кодом в описании лота #rbx-ТИП или в настройках.
Ключ хранится в зашифрованном виде.
"""
import re
import time

from core import db

NAME = "RobloxVIP"
DESCRIPTION = "Автовыдача Robux / VIP Roblox через сервис-поставщик. Ник покупателя + количество из заказа."
CATEGORY = "Выдача через поставщиков"
VERSION = "1.0"
TIMEOUT = 60

SETTINGS = [
    {"key": "api_url", "label": "Адрес API поставщика", "type": "text", "default": ""},
    {"key": "api_key", "label": "API-ключ поставщика", "type": "secret", "hint": "Хранится в зашифрованном виде."},
    {"key": "default_type", "label": "Тип выдачи по умолчанию", "type": "select", "default": "robux",
     "options": ["robux", "vip", "gamepass"], "hint": "Если в описании лота нет #rbx-ТИП."},
    {"key": "ask_nick", "label": "Запрос ника", "type": "textarea",
     "default": "Спасибо за заказ! Пришлите ваш ник в Roblox, куда зачислить {quantity}."},
    {"key": "done_text", "label": "После выдачи", "type": "text", "default": "Готово! {quantity} зачислены на {nick}. Спасибо за покупку!"},
    {"key": "max_price", "label": "Не заказывать дороже, за заказ", "type": "number", "default": 0, "hint": "0 — без лимита."},
    {"key": "remind_text", "label": "Напоминание", "type": "text", "default": "Напоминаем: пришлите ваш ник Roblox для выдачи."},
    {"key": "cancel_text", "label": "Прекращение ожидания", "type": "text", "default": "Без ника заказ не выполнить. Напишите продавцу."},
]

TYPE_RE = re.compile(r"#rbx-([A-Za-z0-9_]+)")


def _jobs():
    import json
    r = db.query("SELECT value FROM plugin_kv WHERE plugin_id='roblox_vip' AND key='jobs'")
    return json.loads(r[0]["value"]) if r else {}


def _save(d):
    import json
    db.execute("INSERT OR REPLACE INTO plugin_kv(plugin_id,key,value) VALUES('roblox_vip','jobs',?)", (json.dumps(d, ensure_ascii=False),))


def _fill(t, **kw):
    for k, v in kw.items():
        t = t.replace("{" + k + "}", str(v))
    return t


def _api(ctx, action, **params):
    url = ctx.config.get("api_url", "").strip().rstrip("/")
    key = ctx.config.get("api_key", "").strip()
    if not url or not key:
        raise Exception("Не заполнен адрес API или ключ поставщика Roblox")
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
    qty = ctx.order_quantity(order) or 1
    m = TYPE_RE.search(order["description"] or "")
    rbx_type = m.group(1) if m else ctx.config.get("default_type", "robux")
    if ctx.dry_run:
        ctx.log(f"Пробный запуск: запросил бы ник для {qty} ({rbx_type})")
        return "dry-run"
    d = _jobs()
    d[order["id"]] = {"stage": "await_nick", "quantity": qty, "type": rbx_type, "account_id": order["account_id"],
                      "buyer": order.get("buyer"), "buyer_id": order.get("buyer_id"),
                      "created": time.time(), "reminded": False, "ordered": False}
    _save(d)
    ctx.send_message(order, _fill(ctx.config["ask_nick"], quantity=qty))
    ctx.log(f"RobloxVIP #{order['id']}: запросил ник для {qty} ({rbx_type})", "order")
    return f"Ожидает ник · {qty} {rbx_type}"


def on_message(msg, chat, ctx):
    text = (msg.get("text") or "").strip()
    d = _jobs()
    for oid, job in list(d.items()):
        if job["account_id"] != chat["account_id"] or (job["buyer"] or "").lower() != (chat["name"] or "").lower():
            continue
        if job["stage"] != "await_nick" or job.get("ordered"):
            return True
        m = re.search(r"[A-Za-z0-9_]{3,20}", text)
        if not m:
            ctx.reply(chat, "Это не похоже на ник Roblox. Пришлите ваш игровой ник.")
            return True
        nick = m.group(0)
        limit = float(ctx.config.get("max_price") or 0)
        if limit:
            try:
                info = _api(ctx, "price", quantity=job["quantity"], type=job["type"])
                cost = float(info.get("price", 0)) if isinstance(info, dict) else 0
                if cost and cost > limit:
                    ctx.notify(f"🔴 RobloxVIP: заказ #{oid} дороже лимита ({cost} > {limit}). Проверьте вручную.")
                    return True
            except Exception:
                pass
        job["ordered"] = True
        _save(d)
        try:
            res = _api(ctx, "buy", nick=nick, quantity=job["quantity"], type=job["type"])
        except Exception as e:
            job["ordered"] = False
            _save(d)
            ctx.reply(chat, "Не удалось выдать, попробуем ещё раз через минуту.")
            ctx.notify(f"🔴 RobloxVIP: заказ #{oid} не выполнен ({e}). Проверьте вручную.")
            return True
        if isinstance(res, dict) and res.get("error"):
            job["ordered"] = False
            _save(d)
            ctx.notify(f"🔴 RobloxVIP: поставщик отказал по заказу #{oid}: {res['error']}")
            ctx.reply(chat, "Не удалось выдать — продавец уведомлён.")
            return True
        del d[oid]
        _save(d)
        ctx.reply(chat, _fill(ctx.config["done_text"], quantity=job["quantity"], nick=nick))
        ctx.log(f"RobloxVIP #{oid}: {job['quantity']} ({job['type']}) выдано на {nick}", "order")
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
                ctx.send_to_buyer(job["account_id"], job["buyer_id"], ctx.config["remind_text"]); job["reminded"] = True; changed = True
            except Exception:
                pass
        elif job["reminded"] and age > 60 * 60:
            if job.get("buyer_id"):
                try:
                    ctx.send_to_buyer(job["account_id"], job["buyer_id"], ctx.config["cancel_text"])
                except Exception:
                    pass
            ctx.notify(f"⏳ RobloxVIP: покупатель {job['buyer']} не прислал ник по заказу #{oid}.")
            del d[oid]; changed = True
    if changed:
        _save(d)
