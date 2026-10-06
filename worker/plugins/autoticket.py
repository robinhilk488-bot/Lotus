"""AutoTicket — напоминает покупателям подтвердить просроченные заказы.

Заказ на FunPay держит деньги «в заморозке», пока покупатель не подтвердит выполнение.
Если он забыл, деньги висят. Плагин находит заказы, которые оплачены, но не закрыты
дольше заданного времени, один раз вежливо напоминает покупателю в чате и уведомляет вас.

Создавать тикеты в поддержку FunPay автоматически плагин НЕ пытается: массовая
автоматическая отправка в поддержку нарушает правила площадки и рискует аккаунтом.
Безопасная и работающая часть — напоминание покупателю, что и делается.
"""
import time

from core import accounts, db
from core.notify import notify

NAME = "AutoTicket"
DESCRIPTION = "Напоминает покупателям подтвердить просроченные заказы и уведомляет вас о зависших деньгах."
CATEGORY = "Покупатели"
VERSION = "1.0"

SETTINGS = [
    {"key": "after_hours", "label": "Напоминать, если заказ не подтверждён, часов", "type": "number", "default": 24,
     "hint": "Через сколько часов после оплаты напомнить покупателю подтвердить заказ."},
    {"key": "reminder", "label": "Текст напоминания покупателю", "type": "textarea",
     "default": "Здравствуйте! Вы оформляли заказ, но ещё не подтвердили его выполнение. "
                "Если всё в порядке — пожалуйста, нажмите «Подтвердить выполнение заказа». Если возникли проблемы — напишите мне."},
    {"key": "notify_me", "label": "Уведомлять меня о зависших заказах", "type": "bool", "default": True},
]


def _reminded():
    r = db.query("SELECT value FROM plugin_kv WHERE plugin_id='autoticket' AND key='reminded'")
    import json
    return set(json.loads(r[0]["value"])) if r else set()


def _mark(ids):
    import json
    db.execute("INSERT OR REPLACE INTO plugin_kv(plugin_id, key, value) VALUES('autoticket','reminded',?)",
               (json.dumps(sorted(ids)[-2000:]),))  # не копим бесконечно


def on_tick(ctx):
    after = float(ctx.config.get("after_hours") or 24) * 3600
    if after <= 0:
        return
    cutoff = time.time() - after
    # оплаченные, но не закрытые заказы старше cutoff, которым ещё не напоминали
    rows = db.query("SELECT id, account_id, buyer, buyer_id, description FROM orders "
                    "WHERE status='paid' AND ts < ? ORDER BY ts", (cutoff,))
    done = _reminded()
    new_reminders = 0
    for o in rows:
        if o["id"] in done:
            continue
        if o.get("buyer_id"):
            try:
                with accounts.use(o["account_id"]) as fp:
                    fp.send_message(fp.chat_with(o["buyer_id"]), ctx.config["reminder"])
                new_reminders += 1
            except Exception as e:
                ctx.log(f"AutoTicket: не удалось напомнить по заказу #{o['id']} — {e}", "warn")
                continue
        done.add(o["id"])
        ctx.log(f"AutoTicket: напоминание покупателю {o['buyer']} по заказу #{o['id']}", "order")
    if new_reminders:
        _mark(done)
        if ctx.config.get("notify_me", True):
            notify("order", f"🎫 AutoTicket: напомнил {new_reminders} покупателям подтвердить заказы. "
                            f"Проверьте зависшие заказы в разделе «Продажи».")
