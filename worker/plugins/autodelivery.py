"""Автовыдача: отправляет покупателю товар сразу после оплаты.

Лоты и товары настраиваются в разделе «Автовыдача» приложения.
Режим «Текст» — одинаковый текст каждому покупателю (инструкция, ссылка, общий ключ).
Режим «Из списка» — каждому покупателю своя строка из загруженного списка (ключи, аккаунты).

Защита: строка списка закрепляется за заказом ДО отправки. Если отправка сорвалась,
при повторе покупатель получит ту же самую строку, а не следующую — товар не расходуется дважды.
"""
from core import db
from core.notify import notify

NAME = "Автовыдача"
DESCRIPTION = "Отправляет покупателю товар сразу после оплаты: текст или уникальную строку из списка."
CATEGORY = "Основное"
VERSION = "1.0"
TIMEOUT = 60
DEFAULT_TEMPLATE = "Спасибо за покупку, {buyer}! Ваш товар:\n{item}"


def _fill(text, **kw):
    for k, v in kw.items():
        text = text.replace("{" + k + "}", str(v))
    return text


def find_lot(description):
    desc = (description or "").lower()
    best = None
    for lot in db.query("SELECT * FROM delivery_lots WHERE enabled=1"):
        p = lot["phrase"].strip().lower()
        if p and p in desc and (not best or len(p) > len(best["phrase"])):
            best = lot
    return best


def stock_left(lot_id):
    return db.query("SELECT COUNT(*) c FROM delivery_stock WHERE lot_id=? AND order_id IS NULL", (lot_id,))[0]["c"]


def _reserve(lot, order_id):
    got = db.query("SELECT line FROM delivery_stock WHERE order_id=? AND lot_id=?", (order_id, lot["id"]))
    if got:
        return got[0]["line"]
    db.execute("UPDATE delivery_stock SET order_id=? WHERE id=(SELECT id FROM delivery_stock "
               "WHERE lot_id=? AND order_id IS NULL ORDER BY id LIMIT 1)", (order_id, lot["id"]))
    got = db.query("SELECT line FROM delivery_stock WHERE order_id=? AND lot_id=?", (order_id, lot["id"]))
    return got[0]["line"] if got else None


def on_new_order(order, ctx):
    lot = find_lot(order["description"])
    if not lot:
        return ctx.SKIP
    item = ""
    if lot["mode"] == "fifo":
        if ctx.dry_run:
            left = stock_left(lot["id"])
            item = "<строка из списка>" if left else None
            ctx.log(f"Лот «{lot['phrase']}»: в наличии {left} шт.")
        else:
            item = _reserve(lot, order["id"])
        if item is None:
            if not ctx.dry_run and not lot["notified_empty"]:
                db.execute("UPDATE delivery_lots SET notified_empty=1 WHERE id=?", (lot["id"],))
                notify("stock", f"📦 Закончился товар для лота «{lot['phrase']}». Заказ #{order['id']} не выдан.")
            raise Exception(f"Товар для лота «{lot['phrase']}» закончился. Загрузите товар и нажмите «Повторить».")
        template = lot["text"].strip() or DEFAULT_TEMPLATE
        if "{item}" not in template:
            template += "\n{item}"
    else:
        template = lot["text"]
        if not template.strip():
            raise Exception(f"У лота «{lot['phrase']}» пустой текст выдачи")

    message = _fill(template, buyer=order.get("buyer", ""), order=order["id"], lot=order.get("description", ""), item=item)
    try:
        ctx.send_message(order, message)
    except Exception as e:
        # товар закреплён за заказом, поэтому повтор безопасен: покупатель получит тот же товар
        raise ctx.Retry(f"не удалось отправить сообщение: {e}", delay=30)
    if lot["cost"]:
        ctx.add_cost(order, lot["cost"])
    if lot["mode"] == "fifo" and not ctx.dry_run:
        left = stock_left(lot["id"])
        if left == 0 and not lot["notified_empty"]:
            db.execute("UPDATE delivery_lots SET notified_empty=1 WHERE id=?", (lot["id"],))
            notify("stock", f"📦 Лот «{lot['phrase']}»: выдан последний товар. Загрузите новый.")
            ctx.log(f"Лот «{lot['phrase']}»: товар закончился", "warn")
    return f"Выдано по лоту «{lot['phrase']}»"
