"""AutoSMM — продажа SMM-услуг через стандартный SMM API v2 (TwiBoost и любые совместимые панели).

Схема:
1. В конце описания лота — код услуги с номером поставщика: #1-1234 или #2-1234.
2. Покупатель оплатил (например, 500 шт.). Бот просит ссылку.
3. Покупатель прислал ссылку → бот спрашивает «+ (верно) / − (неверно)».
   + → отправляет заказ в панель, показывает статус.
   − → снова просит ссылку.
4. !status — сколько накручено и сколько осталось.
5. Накрутка завершена → текст об окончании (меняется в настройках).

Защита денег:
- Заказ в панель (add) уходит ровно один раз на каждый оплаченный заказ FunPay.
- Количество не читается, код не найден, цена выше лимита → на ручную проверку, без отправки.
- Если покупатель не прислал ссылку: через 30 мин напоминание, ещё через 30 мин бот прекращает
  ожидание и уведомляет продавца (деньги на FunPay возвращаются вручную).
"""
import re
import time

from core import db
from core.crypto import decrypt
from core.notify import notify

NAME = "AutoSMM"
DESCRIPTION = "Продажа SMM-услуг через SMM-панели (TwiBoost и совместимые). Код услуги #1-1234 в конце описания лота."
CATEGORY = "Выдача через поставщиков"
VERSION = "1.0"
TIMEOUT = 60

SETTINGS = [
    # поставщик 1
    {"key": "url1", "label": "Поставщик 1 · адрес API", "type": "text", "default": "https://twiboost.com/api/v2",
     "hint": "Например: https://twiboost.com/api/v2"},
    {"key": "key1", "label": "Поставщик 1 · API-ключ", "type": "secret",
     "hint": "В панели: раздел API. Хранится на сервере в зашифрованном виде."},
    # поставщик 2 (необязательный)
    {"key": "url2", "label": "Поставщик 2 · адрес API", "type": "text", "default": "",
     "hint": "Необязательно. Второй поставщик для кодов вида #2-1234."},
    {"key": "key2", "label": "Поставщик 2 · API-ключ", "type": "secret", "hint": "Необязательно."},
    # защита и тексты
    {"key": "max_price", "label": "Не заказывать дороже, $ за заказ", "type": "number", "default": 10,
     "hint": "Защита от скачка цены у поставщика. 0 — без лимита."},
    {"key": "ask_link", "label": "Запрос ссылки", "type": "textarea",
     "default": "Спасибо за заказ! Пришлите ссылку (на профиль, пост или канал), куда выполнить накрутку {quantity} шт."},
    {"key": "confirm", "label": "Подтверждение ссылки", "type": "textarea",
     "default": "Проверьте ссылку: {link}\nВсё верно? Ответьте + если да, − если нужно изменить."},
    {"key": "started", "label": "Накрутка началась", "type": "textarea",
     "default": "Готово! Накрутка запущена. Статус в любой момент: !status"},
    {"key": "again", "label": "Переспросить ссылку (после −)", "type": "text",
     "default": "Хорошо, пришлите правильную ссылку."},
    {"key": "status_text", "label": "Ответ на !status", "type": "text",
     "default": "Выполнено {done} из {quantity}, осталось {remains}. Статус: {status}"},
    {"key": "done_text", "label": "Накрутка завершена", "type": "textarea",
     "default": "Накрутка выполнена полностью, спасибо за покупку! Будем рады видеть снова."},
    {"key": "remind_text", "label": "Напоминание про ссылку", "type": "text",
     "default": "Напоминаем: пришлите ссылку для выполнения заказа, иначе он не может быть выполнен."},
    {"key": "cancel_text", "label": "Прекращение ожидания", "type": "text",
     "default": "Заказ не может быть выполнен без ссылки. Обратитесь к продавцу для возврата."},
]

CODE_RE = re.compile(r"#\s*([12])\s*-\s*(\d{3,6})")
STATUS_RU = {"pending": "в очереди", "in progress": "в работе", "processing": "в работе",
             "completed": "выполнено", "partial": "частично", "canceled": "отменён",
             "cancelled": "отменён", "fail": "ошибка", "error": "ошибка"}


# ---------------- хранилище активных заказов ----------------
def jobs():
    return db.query("SELECT value FROM plugin_kv WHERE plugin_id='autosmm' AND key='jobs'")


def _jobs():
    import json
    r = jobs()
    return json.loads(r[0]["value"]) if r else {}


def _save(d):
    import json
    db.execute("INSERT OR REPLACE INTO plugin_kv(plugin_id,key,value) VALUES('autosmm','jobs',?)",
               (json.dumps(d, ensure_ascii=False),))


def _fill(t, **kw):
    for k, v in kw.items():
        t = t.replace("{" + k + "}", str(v))
    return t


# ---------------- работа с SMM API v2 ----------------
def _supplier(ctx, n):
    url = ctx.config.get(f"url{n}", "").strip().rstrip("/")
    key = ctx.config.get(f"key{n}", "").strip()
    return url, key


def _call(ctx, n, action, **params):
    url, key = _supplier(ctx, n)
    if not url or not key:
        raise Exception(f"Поставщик {n} не настроен")
    r = ctx.http.post(url, data={"key": key, "action": action, **params})
    try:
        data = r.json()
    except ValueError:
        raise Exception(f"Поставщик {n} вернул не JSON (проверьте адрес API)")
    if isinstance(data, dict) and data.get("error"):
        raise Exception(f"Поставщик {n}: {data['error']}")
    return data


def test_supplier(ctx, n):
    data = _call(ctx, n, "balance")
    return f"Баланс: {data.get('balance')} {data.get('currency', '')}".strip()


def _service_price(ctx, n, service_id):
    """Цена за 1000 у услуги. None, если не нашли."""
    for s in _call(ctx, n, "services"):
        if str(s.get("service")) == str(service_id):
            return float(s.get("rate", 0)), s
    return None, None


# ---------------- оплата заказа: просим ссылку ----------------
def on_new_order(order, ctx):
    m = CODE_RE.search(order["description"] or "")
    if not m:
        return ctx.SKIP  # не наш лот
    supplier = int(m.group(1))
    service_id = m.group(2)

    qty = ctx.order_quantity(order)
    if not qty:
        raise Exception("Не удалось прочитать количество из заказа — не отправляем в панель")

    # проверяем, что услуга существует и цена в пределах лимита — до всякой накрутки
    if not ctx.dry_run:
        price_per_k, _ = _service_price(ctx, supplier, service_id)
        if price_per_k is None:
            raise Exception(f"Услуга {service_id} не найдена у поставщика {supplier}")
        cost = price_per_k * qty / 1000
        limit = float(ctx.config.get("max_price") or 0)
        if limit and cost > limit:
            raise Exception(f"Расчётная цена {cost:.2f} выше лимита {limit}. Заказ не отправлен.")

    if ctx.dry_run:
        ctx.log(f"Пробный запуск: поставщик {supplier}, услуга {service_id}, {qty} шт. Запросил бы ссылку.")
        return "dry-run"

    # сохраняем активный заказ и просим ссылку
    d = _jobs()
    d[order["id"]] = {
        "stage": "await_link", "supplier": supplier, "service": service_id, "quantity": qty,
        "account_id": order["account_id"], "buyer": order.get("buyer"), "buyer_id": order.get("buyer_id"),
        "chat_id": None, "link": None, "smm_order": None, "created": time.time(),
        "reminded": False, "notified_done": False,
    }
    _save(d)
    ctx.send_message(order, _fill(ctx.config["ask_link"], quantity=qty, buyer=order.get("buyer", "")))
    ctx.log(f"Заказ #{order['id']}: запросил ссылку (поставщик {supplier}, услуга {service_id}, {qty} шт.)", "order")
    return f"Ожидает ссылку · поставщик {supplier}, {qty} шт."


# ---------------- сообщения покупателя ----------------
def _job_for(chat):
    """Активный заказ этого покупателя в этом чате."""
    d = _jobs()
    for oid, j in d.items():
        if j["account_id"] == chat["account_id"] and (j["buyer"] or "").lower() == (chat["name"] or "").lower() \
                and j["stage"] in ("await_link", "await_confirm", "working"):
            return oid, j, d
    return None, None, d


def on_message(msg, chat, ctx):
    text = (msg.get("text") or "").strip()
    low = text.lower()
    oid, job, d = _job_for(chat)

    # !status работает, пока заказ в работе
    if low in ("!status", "!статус"):
        if not job or not job.get("smm_order"):
            ctx.reply(chat, "Активного заказа на накрутку нет." if not job else "Накрутка ещё не запущена.")
            return True
        try:
            st = _call(ctx, job["supplier"], "status", order=job["smm_order"])
            remains = int(float(st.get("remains", 0)))
            done = job["quantity"] - remains
            status = STATUS_RU.get(str(st.get("status", "")).lower(), st.get("status", ""))
            ctx.reply(chat, _fill(ctx.config["status_text"], done=done, quantity=job["quantity"], remains=remains, status=status))
        except Exception:
            ctx.reply(chat, "Не удалось получить статус, попробуйте позже.")
        return True

    if not job:
        return False

    if job["stage"] == "await_link":
        link = _extract_link(text)
        if not link:
            return False  # не ссылка — пусть обрабатывают другие (приветствие и т.п.)
        job["link"] = link
        job["stage"] = "await_confirm"
        job["chat_id"] = chat["id"]
        _save(d)
        ctx.reply(chat, _fill(ctx.config["confirm"], link=link))
        return True

    if job["stage"] == "await_confirm":
        if text in ("+", "➕") or low in ("да", "yes", "верно", "ok", "ок"):
            job["chat_id"] = chat["id"]
            _save(d)
            _place(oid, job, d, ctx, chat)
            return True
        if text in ("-", "−", "–") or low in ("нет", "no", "неверно"):
            job["stage"] = "await_link"
            job["link"] = None
            _save(d)
            ctx.reply(chat, ctx.config["again"])
            return True
        # другой текст в ожидании подтверждения — считаем новой ссылкой
        link = _extract_link(text)
        if link:
            job["link"] = link
            _save(d)
            ctx.reply(chat, _fill(ctx.config["confirm"], link=link))
            return True
    return False


def _extract_link(text):
    m = re.search(r"(https?://\S+|t\.me/\S+|@[\w\d_]+)", text)
    return m.group(1) if m else None


def _place(oid, job, d, ctx, chat):
    """Отправка заказа в панель — ровно один раз."""
    if job.get("smm_order"):
        ctx.reply(chat, "Заказ уже запущен. Статус: !status")
        return
    try:
        res = _call(ctx, job["supplier"], "add", service=job["service"], link=job["link"], quantity=job["quantity"])
    except Exception as e:
        job["stage"] = "await_confirm"
        _save(d)
        ctx.reply(chat, "Не удалось запустить накрутку, попробуем ещё раз через минуту.")
        notify("attention", f"🔴 AutoSMM: заказ #{oid} не отправлен в панель ({e}). Проверьте вручную.")
        ctx.log(f"Заказ #{oid}: ошибка отправки в панель — {e}", "error")
        return
    smm_order = res.get("order") if isinstance(res, dict) else None
    if not smm_order:
        job["stage"] = "await_confirm"
        _save(d)
        notify("attention", f"🔴 AutoSMM: заказ #{oid} — панель не вернула номер заказа. Проверьте вручную.")
        ctx.log(f"Заказ #{oid}: панель не вернула номер заказа — нужна проверка", "error")
        return
    job["stage"] = "working"
    job["smm_order"] = smm_order
    _save(d)
    # записываем себестоимость для расчёта прибыли
    try:
        price_per_k, _ = _service_price(ctx, job["supplier"], job["service"])
        if price_per_k:
            db.execute("INSERT OR REPLACE INTO order_costs(order_id, source, amount) VALUES(?,?,?)",
                       (oid, "autosmm", round(price_per_k * job["quantity"] / 1000, 2)))
    except Exception:
        pass
    ctx.reply(chat, ctx.config["started"])
    ctx.log(f"Заказ #{oid}: накрутка запущена, заказ панели {smm_order}", "order")


# ---------------- таймер: напоминания, прекращение, завершение ----------------
def on_tick(ctx):
    now = time.time()
    d = _jobs()
    changed = False
    for oid, job in list(d.items()):
        # ожидание ссылки: напоминание и прекращение
        if job["stage"] in ("await_link", "await_confirm"):
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
                notify("attention", f"⏳ AutoSMM: покупатель {job['buyer']} не прислал ссылку по заказу #{oid}. "
                                    f"Ожидание прекращено, верните деньги вручную при необходимости.")
                ctx.log(f"Заказ #{oid}: ссылка не получена за час — ожидание прекращено", "warn")
                del d[oid]
                changed = True
            continue
        # в работе: проверяем завершение
        if job["stage"] == "working" and not job["notified_done"] and job.get("smm_order"):
            try:
                st = _call(ctx, job["supplier"], "status", order=job["smm_order"])
            except Exception:
                continue
            status = str(st.get("status", "")).lower()
            if status in ("completed", "complete"):
                if job.get("buyer_id"):
                    try:
                        ctx.send_to_buyer(job["account_id"], job["buyer_id"], ctx.config["done_text"])
                    except Exception:
                        pass
                ctx.log(f"Заказ #{oid}: накрутка завершена", "order")
                del d[oid]
                changed = True
            elif status in ("canceled", "cancelled", "fail", "error", "partial"):
                notify("attention", f"🔴 AutoSMM: заказ #{oid} у поставщика в статусе «{status}». Проверьте вручную.")
                job["notified_done"] = True
                changed = True
    if changed:
        _save(d)


# ---------------- для кнопки «Проверить» на странице ----------------
def test(ctx):
    out = []
    for n in (1, 2):
        url, key = _supplier(ctx, n)
        if url and key:
            try:
                out.append(f"Поставщик {n}: {test_supplier(ctx, n)}")
            except Exception as e:
                out.append(f"Поставщик {n}: {e}")
    return "; ".join(out) if out else "Ни один поставщик не настроен"
