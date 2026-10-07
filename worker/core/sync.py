"""Фоновая синхронизация: баланс и продажи всех аккаунтов."""
import threading
import time

from . import accounts, db, plugins
from .funpay import FunPayError
from .notify import notify

_wake = threading.Event()


def _set_error(acc, text):
    was_ok = acc["status"] != "error"
    db.execute("UPDATE accounts SET status='error', error=?, last_sync=? WHERE id=?", (text, time.time(), acc["id"]))
    db.log(f"{acc['name']}: {text}", "error")
    if was_ok:  # в Telegram — только при переходе из «работает» в «ошибка», без спама
        notify("account", f"⚠️ Аккаунт «{acc['name']}»: {text}")
    accounts.forget(acc["id"])


def _on_refund(order_id):
    n = db.query("SELECT COUNT(*) c FROM plugin_tasks WHERE order_id=? AND status='pending'", (order_id,))[0]["c"]
    db.execute("UPDATE plugin_tasks SET status='cancelled', error='Заказ возвращён покупателю', updated=? "
               "WHERE order_id=? AND status='pending'", (time.time(), order_id))
    if n:
        db.log(f"Заказ #{order_id} возвращён — обработка плагинами отменена", "warn")


def sync_account(acc: dict):
    try:
        with accounts.use(acc["id"]) as fp:
            me = fp.get_me()
            sales = fp.get_sales()
    except FunPayError as e:
        return _set_error(acc, str(e))
    except Exception as e:
        return _set_error(acc, f"Ошибка синхронизации: {e}")

    if acc["status"] == "error":
        db.log(f"{acc['name']}: снова работает")
        notify("account", f"✅ Аккаунт «{acc['name']}» снова работает")
    db.execute("UPDATE accounts SET user_id=?, username=?, balance=?, currency=?, status='ok', error=NULL, last_sync=? WHERE id=?",
               (me["user_id"], me["username"], me["balance"], me["currency"], time.time(), acc["id"]))
    first_sync = acc["last_sync"] is None

    for o in sales:
        exists = db.query("SELECT status FROM orders WHERE id=?", (o["id"],))
        if exists:
            if exists[0]["status"] != o["status"]:
                db.execute("UPDATE orders SET status=? WHERE id=?", (o["status"], o["id"]))
                if o["status"] == "refunded":
                    _on_refund(o["id"])
            continue
        db.execute("INSERT INTO orders(id, account_id, ts, buyer, buyer_id, description, amount, currency, status) "
                   "VALUES(?,?,?,?,?,?,?,?,?)",
                   (o["id"], acc["id"], o["ts"], o["buyer"], o["buyer_id"], o["description"], o["amount"], o["currency"], o["status"]))
        o["account"], o["account_id"] = acc["name"], acc["id"]
        if first_sync:
            # старые заказы плагинам не отдаём, чтобы не выдать товар повторно
            if o["status"] == "paid":
                db.log(f"Заказ #{o['id']} оплачен до подключения аккаунта — проверьте его вручную", "warn")
            continue
        db.log(f"Новый заказ #{o['id']} от {o['buyer']}: {o['amount']:g} {o['currency']}", "order")
        notify("order", f"💰 Новый заказ #{o['id']} ({acc['name']})\n{o['description']}\n{o['buyer']} · {o['amount']:g} {o['currency']}")
        # Выдаём по впервые увиденным заказам со статусом paid ИЛИ closed.
        # closed = покупатель быстро подтвердил выполнение (между синхронизациями) — товар
        # ещё не выдавался, его нужно выдать. От повторной выдачи защищает очередь (UNIQUE order_id).
        # refunded/прочее — не выдаём.
        if o["status"] not in ("paid", "closed"):
            continue
        if db.in_blacklist(o["buyer"]):
            db.log(f"Заказ #{o['id']} от {o['buyer']} из чёрного списка — автоматическая выдача не выполнялась", "warn")
            notify("blacklist", f"⛔ Заказ #{o['id']} от {o['buyer']} (в чёрном списке). Автовыдача не выполнялась, решите вручную.")
            continue
        plugins.enqueue_order(o)


def sync_all():
    for acc in db.query("SELECT * FROM accounts"):
        sync_account(acc)


def wake():
    _wake.set()


def _loop():
    while True:
        try:
            sync_all()
        except Exception as e:
            db.log(f"Сбой синхронизации: {e}", "error")
        _wake.wait(db.get_settings()["sync_interval_min"] * 60)
        _wake.clear()


def start():
    threading.Thread(target=_loop, daemon=True, name="sync").start()
