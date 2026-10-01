"""Автоответчик — автоматические ответы на сообщения покупателей по ключевым словам.

Правила задаются построчно в настройках:
    привет = Здравствуйте! Чем помочь?
    гаранти | возврат = Гарантия 24 часа, при проблеме вернём деньги.

Слева — ключевые слова (через | можно несколько), справа — ответ. Регистр не важен.
Если сообщение покупателя содержит ключевое слово, бот отправляет ответ.

Чтобы не спамить, каждый ответ уходит покупателю не чаще, чем раз в N минут (настраивается).
Команды самого бота (!code, !status и т.п.) автоответчик не трогает.
"""
import time

from core import db

NAME = "Автоответчик"
DESCRIPTION = "Отвечает на сообщения покупателей по ключевым словам. Правила: «слово = ответ», по строке на правило."
CATEGORY = "Покупатели"
VERSION = "1.0"

SETTINGS = [
    {"key": "rules", "label": "Правила", "type": "textarea",
     "default": "привет | здравствуй = Здравствуйте! Товар выдаётся автоматически после оплаты.\n"
                "гарантия | возврат = Гарантия 24 часа. Если возникнут проблемы — вернём деньги или заменим товар.",
     "hint": "По строке: ключевые слова = ответ. Несколько слов через | . Регистр не важен."},
    {"key": "cooldown_min", "label": "Не повторять ответ чаще, минут", "type": "number", "default": 10,
     "hint": "Защита от спама: один и тот же ответ одному покупателю не чаще этого времени."},
    {"key": "first_only", "label": "Отвечать только на первое совпадение в сообщении", "type": "bool", "default": True},
]


def _rules(cfg):
    out = []
    for line in (cfg.get("rules") or "").splitlines():
        if "=" not in line:
            continue
        left, answer = line.split("=", 1)
        answer = answer.strip()
        keys = [k.strip().lower() for k in left.split("|") if k.strip()]
        if keys and answer:
            out.append((keys, answer))
    return out


def _recently_sent(key):
    r = db.query("SELECT value FROM plugin_kv WHERE plugin_id='autoresponder' AND key=?", (key,))
    return float(r[0]["value"]) if r else 0


def _mark(key):
    db.execute("INSERT OR REPLACE INTO plugin_kv(plugin_id, key, value) VALUES('autoresponder', ?, ?)",
               (key, str(time.time())))


def on_message(msg, chat, ctx):
    text = (msg.get("text") or "").strip().lower()
    if not text or text.startswith("!"):  # команды не трогаем
        return False
    cooldown = float(ctx.config.get("cooldown_min") or 0) * 60
    matched = False
    for keys, answer in _rules(ctx.config):
        if any(k in text for k in keys):
            key = f"sent:{chat['account_id']}:{chat['name']}:{hash(answer) & 0xffffff}"
            if cooldown and time.time() - _recently_sent(key) < cooldown:
                matched = True  # недавно отвечали — считаем обработанным, но молчим
                continue
            if ctx.dry_run:
                ctx.log(f"Ответил бы: {answer}")
            else:
                ctx.reply(chat, answer)
                _mark(key)
                ctx.log(f"Автоответ «{chat['name']}»: {answer[:40]}")
            matched = True
            if ctx.config.get("first_only", True):
                break
    return matched
