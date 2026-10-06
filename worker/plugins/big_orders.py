"""Пример плагина: отмечает в журнале крупные заказы."""
CATEGORY = "Основное"

NAME = "Крупные заказы"
DESCRIPTION = "Пишет в журнал, когда приходит заказ дороже заданной суммы."
VERSION = "1.0"
SETTINGS = [
    {"key": "threshold", "label": "Сумма от, ₽", "type": "number", "default": 1000},
    {"key": "note", "label": "Пометка в журнале", "type": "text", "default": "Крупный заказ"},
]


def on_new_order(order, ctx):
    if order["amount"] >= float(ctx.config["threshold"]):
        ctx.log(f"{ctx.config['note']}: #{order['id']} на {order['amount']:g} {order['currency']} "
                f"({order['account']})", "order")
