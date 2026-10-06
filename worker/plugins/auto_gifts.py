"""AutoGifts — автоматическая выдача подарков Telegram после оплаты.

Покупатель оплачивает лот, бот просит его Telegram-юзернейм и заказывает подарок через
ваш сервис-поставщик по API. Какой подарок — определяется кодом в описании лота (#gift-КОД),
либо одним подарком по умолчанию из настроек. Ключ хранится в зашифрованном виде.
"""
import re
import time

from core import db

NAME = "AutoGifts"
DESCRIPTION = "Автовыдача подарков Telegram через сервис-поставщик. Код подарка #gift-КОД в описании лота."
CATEGORY = "Telegram"
VERSION = "1.0"
TIMEOUT = 60

SETTINGS = [
    {"key": "api_url", "label": "Адрес API поставщика", "type": "text", "default": ""},
    {"key": "api_key", "label": "API-ключ поставщика", "type": "secret", "hint": "Хранится в зашифрованном виде."},
    {"key": "default_gift", "label": "Код подарка по умолчанию", "type": "text", "default": "",
     "hint": "Если в описании лота нет #gift-КОД — отправится этот подарок. Пусто — требовать код в лоте."},
    {"key": "ask_username", "label": "Запрос юзернейма", "type": "textarea",
     "default": "Спасибо за заказ! Пришлите ваш Telegram-юзернейм (@username), куда отправить подарок."},
    {"key": "done_text", "label": "После выдачи", "type": "text", "default": "Готово! Подарок отправлен на {username}. Спасибо!"},
    {"key": "remind_text", "label": "Напоминание", "type": "text", "default": "Напоминаем: пришлите @username для отправки подарка."},
    {"key": "cancel_text", "label": "Прекращение ожидания", "type": "text", "default": "Без юзернейма заказ не выполнить. Напишите продавцу."},
]

GIFT_RE = re.compile(r"#gift-([A-Za-z0-9_]+)")


def _jobs():
    import json
    r = db.query("SELECT value FROM plugin_kv WHERE plugin_id='auto_gifts' AND key='jobs'")
    return json.loads(r[0]["value"]) if r else {}


def _save(d):
    import json
    db.execute("INSERT OR REPLACE INTO plugin_kv(plugin_id,key,value) VALUES('auto_gifts','jobs',?)", (json.dumps(d, ensure_ascii=False),))


def _fill(t, **kw):
    for k, v in kw.items():
        t = t.replace("{" + k + "}", str(v))
    return t


def _api(ctx, action, **params):
    url = ctx.config.get("api_url", "").strip().rstrip("/")
    key = ctx.config.get("api_key", "").strip()
    if not url or not key:
        raise Exception("Не заполнен адрес API или ключ поставщика подарков")
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
    m = GIFT_RE.search(order["description"] or "")
    gift = m.group(1) if m else (ctx.config.get("default_gift") or "").strip()
    if not gift:
        return ctx.SKIP  # не наш лот (нет кода и нет подарка по умолчанию)
    if ctx.dry_run:
        ctx.log(f"Пробный запуск: запросил бы юзернейм для подарка {gift}")
        return "dry-run"
    d = _jobs()
    d[order["id"]] = {"stage": "await_username", "gift": gift, "account_id": order["account_id"],
                      "buyer": order.get("buyer"), "buyer_id": order.get("buyer_id"),
                      "created": time.time(), "reminded": False, "ordered": False}
    _save(d)
    ctx.send_message(order, ctx.config["ask_username"])
    ctx.log(f"AutoGifts #{order['id']}: запросил юзернейм для подарка {gift}", "order")
    return f"Ожидает юзернейм · подарок {gift}"


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
        username = m.group(0) if m.group(0).startswith("@") else "@" + m.group(0)
        job["ordered"] = True
        _save(d)
        try:
            res = _api(ctx, "send_gift", username=username, gift=job["gift"])
        except Exception as e:
            job["ordered"] = False
            _save(d)
            ctx.reply(chat, "Не удалось отправить подарок, попробуем ещё раз.")
            ctx.notify(f"🔴 AutoGifts: заказ #{oid} не отправлен ({e}). Проверьте вручную.")
            return True
        if isinstance(res, dict) and res.get("error"):
            job["ordered"] = False
            _save(d)
            ctx.notify(f"🔴 AutoGifts: поставщик отказал по заказу #{oid}: {res['error']}")
            ctx.reply(chat, "Не удалось отправить подарок — продавец уведомлён.")
            return True
        del d[oid]
        _save(d)
        ctx.reply(chat, _fill(ctx.config["done_text"], username=username))
        ctx.log(f"AutoGifts #{oid}: подарок {job['gift']} отправлен на {username}", "order")
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
            ctx.notify(f"⏳ AutoGifts: покупатель {job['buyer']} не прислал юзернейм по заказу #{oid}.")
            del d[oid]; changed = True
    if changed:
        _save(d)
