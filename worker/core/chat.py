"""Чаты FunPay: хранение переписки, приветствие, вызов продавца (!help), отзывы.

Сервер раз в N секунд смотрит список чатов. Если в чате появилось новое сообщение,
он загружает историю, сохраняет новые сообщения и реагирует на них.
Сообщения, которые были до подключения аккаунта, никогда не обрабатываются,
поэтому старые покупатели не получат внезапное приветствие.
"""
import json
import threading
import time

from . import accounts, db, plugins
from .funpay import CONFIRM_RE, REVIEW_RE, FunPayError
from .notify import notify

_wake = threading.Event()


def _kv_get(key, default=None):
    r = db.query("SELECT value FROM plugin_kv WHERE plugin_id='core' AND key=?", (key,))
    return json.loads(r[0]["value"]) if r else default


def _kv_set(key, value):
    db.execute("INSERT OR REPLACE INTO plugin_kv(plugin_id, key, value) VALUES('core', ?, ?)", (key, json.dumps(value)))


def _fill(text, **kw):
    for k, v in kw.items():
        text = text.replace("{" + k + "}", str(v))
    return text


def _store(acc_id, chat_id, msgs):
    for m in msgs:
        db.execute("INSERT OR IGNORE INTO messages(account_id, chat_id, id, author_id, author, text, ts, mine) "
                   "VALUES(?,?,?,?,?,?,?,?)",
                   (acc_id, chat_id, m["id"], m["author_id"], m["author"], m["text"], m["ts"], int(m["mine"])))


def _send(fp, acc_id, chat_id, text):
    fp.send_message(chat_id, text)
    time.sleep(0.4)


def _handle_review(fp, acc, chat, order_id):
    key = f"review_seen_{order_id}"
    try:
        review = fp.get_review(order_id)
    except FunPayError as e:
        db.log(f"Отзыв к заказу #{order_id}: не удалось прочитать ({e})", "warn")
        review = None
    rating = review["rating"] if review else None
    stars = "⭐" * rating if rating else "без оценки"
    db.log(f"Отзыв к заказу #{order_id} от {chat['name']}: {stars}", "order")
    notify("review", f"📝 Отзыв к заказу #{order_id} ({acc['name']})\n{chat['name']}: {stars}\n{(review or {}).get('text', '')}".strip())
    plugins.emit_review(acc, chat, order_id, rating)
    s = db.get_settings()
    if (s["review_thanks_enabled"] and rating and rating >= s["review_thanks_min"]
            and not _kv_get(key) and not db.in_blacklist(chat["name"])):
        _send(fp, acc["id"], chat["id"], _fill(s["review_thanks_text"], buyer=chat["name"], order=order_id))
        _kv_set(key, True)


def _process_new(fp, acc, chat, row, new_msgs):
    s = db.get_settings()
    greeted = bool(row and row["greeted"])
    for m in new_msgs:
        if m["mine"]:
            continue
        if m["author_id"] == 0:  # системное сообщение FunPay
            if found := REVIEW_RE.search(m["text"] or ""):
                _handle_review(fp, acc, chat, found.group(1))
            elif found := CONFIRM_RE.search(m["text"] or ""):
                plugins.emit_order_confirmed(acc, chat, found.group(1))
            continue
        buyer = chat["name"] or m["author"]
        if db.in_blacklist(buyer):
            greeted = True
            continue
        text = (m["text"] or "").strip()
        if plugins.emit_message(acc, chat, m):  # команда плагина (например, !code у аренды)
            greeted = True
            continue
        cmd = s["help_command"].strip().lower()
        if s["help_enabled"] and cmd and text.lower().startswith(cmd):
            notify("help", f"🙋 Вас зовут в чат FunPay ({acc['name']})\nПокупатель: {buyer}\nСообщение: {text}")
            db.log(f"{buyer} вызвал продавца командой {s['help_command']}", "order")
            if s["help_reply"].strip():
                _send(fp, acc["id"], chat["id"], _fill(s["help_reply"], buyer=buyer))
            greeted = True
            continue
        if not greeted:
            greeted = True
            if s["greeting_enabled"] and s["greeting_text"].strip():
                _send(fp, acc["id"], chat["id"], _fill(s["greeting_text"], buyer=buyer))
                db.log(f"Приветствие отправлено: {buyer}")
    return greeted


def poll_account(acc):
    init_key = f"chat_init_max_{acc['id']}"
    with accounts.use(acc["id"]) as fp:
        chats = fp.get_chats()
        init_max = _kv_get(init_key)
        if init_max is None:  # первый запуск: запоминаем текущее состояние, ничего не обрабатываем
            init_max = max([c["last_msg_id"] for c in chats] or [0])
            _kv_set(init_key, init_max)
            # FunPay отдаёт чаты в порядке свежести (новые первыми). Сохраняем этот порядок,
            # проставляя убывающее время, чтобы в приложении новые были сверху.
            now = time.time()
            for i, c in enumerate(chats):
                db.execute("INSERT OR REPLACE INTO chats(account_id, chat_id, name, last_msg_id, last_text, last_ts, unread, greeted, avatar) "
                           "VALUES(?,?,?,?,?,?,?,1,?)", (acc["id"], c["id"], c["name"], c["last_msg_id"], c["last_text"], now - i, int(c["unread"]), c.get("avatar", "")))
            return
        for c in chats:
            row = (db.query("SELECT * FROM chats WHERE account_id=? AND chat_id=?", (acc["id"], c["id"])) or [None])[0]
            if row and row["last_msg_id"] >= c["last_msg_id"]:
                if row["unread"] != int(c["unread"]):
                    db.execute("UPDATE chats SET unread=? WHERE account_id=? AND chat_id=?", (int(c["unread"]), acc["id"], c["id"]))
                continue
            history = fp.get_history(c["id"])
            known = row["last_msg_id"] if row else 0
            new_msgs = [m for m in history if m["id"] > max(known, init_max)]
            _store(acc["id"], c["id"], history)
            greeted = _process_new(fp, acc, c, row, new_msgs)
            last = history[-1] if history else None
            db.execute("INSERT OR REPLACE INTO chats(account_id, chat_id, name, last_msg_id, last_text, last_ts, unread, greeted, avatar) "
                       "VALUES(?,?,?,?,?,?,?,?,?)",
                       (acc["id"], c["id"], c["name"], c["last_msg_id"], c["last_text"],
                        last["ts"] if last else time.time(), int(c["unread"]), int(greeted or bool(row and row["greeted"])), c.get("avatar", row["avatar"] if row else "")))


def refresh_chat(acc_id, chat_id):
    """Загрузить свежую историю одного чата (когда вы открыли его в приложении)."""
    with accounts.use(acc_id) as fp:
        history = fp.get_history(chat_id)
    _store(acc_id, chat_id, history)
    return history


def send_from_app(acc_id, chat_id, text):
    text = text.strip()
    if not text:
        raise ValueError("Пустое сообщение")
    with accounts.use(acc_id) as fp:
        fp.send_message(chat_id, text)
        history = fp.get_history(chat_id)
    _store(acc_id, chat_id, history)
    if history:
        db.execute("UPDATE chats SET last_msg_id=MAX(last_msg_id, ?), last_text=?, last_ts=?, unread=0 WHERE account_id=? AND chat_id=?",
                   (history[-1]["id"], history[-1]["text"], history[-1]["ts"], acc_id, chat_id))


def _loop():
    errors = {}
    while True:
        for acc in db.query("SELECT * FROM accounts WHERE status='ok'"):
            try:
                poll_account(acc)
                errors.pop(acc["id"], None)
            except Exception as e:
                if errors.get(acc["id"]) != str(e):  # одну и ту же ошибку пишем в журнал один раз
                    db.log(f"{acc['name']}: чаты недоступны ({e})", "error")
                errors[acc["id"]] = str(e)
        _wake.wait(db.get_settings()["chat_interval_sec"])
        _wake.clear()


def wake():
    _wake.set()


def start():
    threading.Thread(target=_loop, daemon=True, name="chats").start()
