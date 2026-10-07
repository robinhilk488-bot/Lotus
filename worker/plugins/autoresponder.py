"""Автоответчик — ответы покупателям и работа с отзывами.

Умеет:
1. Ответы по ключевым словам: «слово = ответ», по строке на правило.
2. Приветствие на ПЕРВОЕ сообщение покупателя (любое, не только «привет»).
3. Просьбу оставить отзыв — после того как покупатель подтвердил выполнение заказа.
4. Ответы ПОД ОТЗЫВОМ (не в чате): свой текст на 5, 4, 3, 2, 1 звезду.
   Пустое поле для оценки — на неё бот ничего не отвечает (например, на 1-2 звезды).

{buyer} в текстах подставляет имя покупателя, {order} — номер заказа.
Команды бота (!code, !status и т.п.) автоответчик не трогает.
"""
import time

from core import db

NAME = "Автоответчик"
DESCRIPTION = "Приветствие, ответы по ключевым словам, просьба об отзыве и ответы под отзывами по звёздам."
CATEGORY = "Покупатели"
VERSION = "2.0"

SETTINGS = [
    {"key": "greet_enabled", "label": "Приветствие на первое сообщение", "type": "bool", "default": True},
    {"key": "greet_text", "label": "Текст приветствия", "type": "textarea",
     "default": "Здравствуйте, {buyer}! Товар выдаётся автоматически сразу после оплаты. Если возникнут вопросы — пишите."},
    {"key": "greet_photo", "label": "Фото к приветствию (необязательно)", "type": "image",
     "hint": "Картинка отправится вместе с приветствием. Например, инструкция или баннер магазина."},

    {"key": "rules", "label": "Ответы по ключевым словам", "type": "textarea",
     "default": "гарантия | возврат = Гарантия 24 часа. Если возникнут проблемы — вернём деньги или заменим товар.",
     "hint": "По строке: ключевые слова = ответ. Несколько слов через | . Регистр не важен. Можно оставить пустым."},
    {"key": "cooldown_min", "label": "Не повторять один ответ чаще, минут", "type": "number", "default": 10,
     "hint": "Защита от спама: один и тот же ответ одному покупателю не чаще этого времени."},

    {"key": "ask_review_enabled", "label": "Просить отзыв после подтверждения заказа", "type": "bool", "default": True},
    {"key": "ask_review_text", "label": "Текст просьбы об отзыве", "type": "textarea",
     "default": "Спасибо за покупку, {buyer}! Будем благодарны за отзыв — это очень помогает магазину."},
    {"key": "ask_review_photo", "label": "Фото к просьбе об отзыве (необязательно)", "type": "image",
     "hint": "Например, картинка-инструкция, как оставить отзыв."},

    {"key": "review_reply_enabled", "label": "Отвечать под отзывами", "type": "bool", "default": False,
     "hint": "Бот публикует ответ ПОД отзывом покупателя (не в чате). Для каждой оценки свой текст; пустое поле — не отвечать."},
    {"key": "review_5", "label": "Ответ на 5 звёзд", "type": "textarea",
     "default": "Спасибо за пятёрку, {buyer}! Рады, что всё понравилось. Ждём снова."},
    {"key": "review_4", "label": "Ответ на 4 звезды", "type": "textarea",
     "default": "Спасибо за отзыв! Подскажите, что можно улучшить — будем рады стать лучше."},
    {"key": "review_3", "label": "Ответ на 3 звезды", "type": "textarea", "default": ""},
    {"key": "review_2", "label": "Ответ на 2 звезды", "type": "textarea", "default": ""},
    {"key": "review_1", "label": "Ответ на 1 звезду", "type": "textarea", "default": ""},
]


def _fill(t, **kw):
    for k, v in kw.items():
        t = t.replace("{" + k + "}", str(v))
    return t


def _kv(key):
    r = db.query("SELECT value FROM plugin_kv WHERE plugin_id='autoresponder' AND key=?", (key,))
    return r[0]["value"] if r else None


def _kv_set(key, value):
    db.execute("INSERT OR REPLACE INTO plugin_kv(plugin_id, key, value) VALUES('autoresponder', ?, ?)",
               (key, str(value)))


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


def on_message(msg, chat, ctx):
    text = (msg.get("text") or "").strip()
    low = text.lower()
    if not text or text.startswith("!"):
        return False
    cfg = ctx.config
    handled = False

    if cfg.get("greet_enabled", True) and cfg.get("greet_text", "").strip():
        gkey = f"greeted:{chat['account_id']}:{(chat['name'] or '').lower()}"
        if not _kv(gkey):
            if ctx.dry_run:
                ctx.log(f"Поприветствовал бы {chat['name']}")
            else:
                ctx.reply(chat, _fill(cfg["greet_text"], buyer=chat["name"]))
                try:
                    ctx.reply_image(chat, ctx.plugin_image("greet_photo"))
                except Exception as e:
                    ctx.log(f"не удалось отправить фото приветствия: {e}", "warn")
                _kv_set(gkey, 1)
                ctx.log(f"Приветствие: {chat['name']}")
            handled = True

    cooldown = float(cfg.get("cooldown_min") or 0) * 60
    for keys, answer in _rules(cfg):
        if any(k in low for k in keys):
            key = f"sent:{chat['account_id']}:{(chat['name'] or '').lower()}:{hash(answer) & 0xffffff}"
            last = float(_kv(key) or 0)
            if cooldown and time.time() - last < cooldown:
                handled = True
                continue
            if ctx.dry_run:
                ctx.log(f"Ответил бы: {answer}")
            else:
                ctx.reply(chat, answer)
                _kv_set(key, time.time())
                ctx.log(f"Автоответ «{chat['name']}»: {answer[:40]}")
            handled = True
            break
    return handled


def on_order_confirmed(order, ctx):
    cfg = ctx.config
    if not cfg.get("ask_review_enabled", True) or not cfg.get("ask_review_text", "").strip():
        return
    key = f"asked_review:{order['order_id']}"
    if _kv(key):
        return
    chat = {"id": order["chat_id"], "account_id": order["account_id"], "name": order["buyer"]}
    if ctx.dry_run:
        ctx.log(f"Попросил бы отзыв по заказу #{order['order_id']}")
    else:
        ctx.reply(chat, _fill(cfg["ask_review_text"], buyer=order["buyer"], order=order["order_id"]))
        try:
            ctx.reply_image(chat, ctx.plugin_image("ask_review_photo"))
        except Exception as e:
            ctx.log(f"не удалось отправить фото к просьбе об отзыве: {e}", "warn")
        _kv_set(key, 1)
        ctx.log(f"Просьба об отзыве: #{order['order_id']} ({order['buyer']})", "order")


def on_review(review, ctx):
    cfg = ctx.config
    if not cfg.get("review_reply_enabled", False):
        return
    rating = review.get("rating")
    if not rating:
        return
    text = (cfg.get(f"review_{rating}") or "").strip()
    if not text:
        return
    key = f"review_replied:{review['order_id']}"
    if _kv(key):
        return
    body = _fill(text, buyer=review["buyer"], order=review["order_id"])
    try:
        ctx.reply_to_review(review["account_id"], review["order_id"], body)
        _kv_set(key, 1)
        ctx.log(f"Ответ под отзывом #{review['order_id']} ({rating} звёзд)", "order")
    except Exception as e:
        ctx.log(f"не удалось ответить под отзывом #{review['order_id']}: {e}", "warn")
