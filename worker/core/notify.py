"""Уведомления в Telegram (только сигналы, без управления)."""
import threading

import requests

from . import db

KINDS = {
    "attention": "tg_on_attention", "account": "tg_on_account", "help": "tg_on_help",
    "review": "tg_on_review", "stock": "tg_on_stock", "blacklist": "tg_on_blacklist", "order": "tg_on_order",
}


def send_raw(token, chat_id, text):
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True}, timeout=15)
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if not data.get("ok"):
        desc = data.get("description", f"HTTP {r.status_code}")
        if "chat not found" in desc:
            desc = "чат не найден: проверьте Telegram ID и что вы написали боту /start"
        elif r.status_code == 401:
            desc = "неверный токен бота"
        raise RuntimeError(desc)


def notify(kind, text):
    """Отправляет уведомление в фоне, если этот тип включён. Ошибки — только в журнал."""
    s = db.get_settings()
    if not (s["tg_token"] and s["tg_chat_id"] and s.get(KINDS.get(kind, ""), False)):
        return

    def run():
        try:
            send_raw(s["tg_token"], s["tg_chat_id"], text)
        except Exception as e:
            db.log(f"Telegram: уведомление не отправлено ({e})", "warn")
    threading.Thread(target=run, daemon=True).start()


def test():
    s = db.get_settings()
    if not s["tg_token"] or not s["tg_chat_id"]:
        raise RuntimeError("Заполните токен бота и ваш Telegram ID")
    send_raw(s["tg_token"], s["tg_chat_id"], "✅ Lotus: уведомления работают.")
