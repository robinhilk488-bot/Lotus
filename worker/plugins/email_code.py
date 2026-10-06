"""EmailCode — присылает покупателю код подтверждения из почты по команде.

Для продажи аккаунтов, куда коды входа приходят на почту (Steam, Epic, соцсети и т.п.).
Покупатель пишет в чат команду (по умолчанию !code), бот заходит в почтовый ящик по IMAP,
находит самое свежее письмо с кодом и присылает код покупателю.

Настройки на странице плагина: IMAP-сервер, почта, пароль (пароль приложения), команда,
сколько минут письмо считается свежим, шаблон поиска кода. Пароль хранится в зашифрованном виде.

Защита: по умолчанию бот отвечает только покупателю, у которого есть оплаченный заказ
(можно отключить). Код ищется только в письмах за последние N минут, чтобы не отдать старый.
"""
import email
import imaplib
import re
import time
from email.header import decode_header

from core import db

NAME = "EmailCode"
DESCRIPTION = "Присылает покупателю код подтверждения из почты (IMAP) по команде !code."
CATEGORY = "Steam"
VERSION = "1.0"
TIMEOUT = 40

SETTINGS = [
    {"key": "imap_host", "label": "IMAP-сервер", "type": "text", "default": "imap.gmail.com",
     "hint": "Например: imap.gmail.com, imap.mail.ru, outlook.office365.com"},
    {"key": "email", "label": "Почта", "type": "text", "default": ""},
    {"key": "password", "label": "Пароль (пароль приложения)", "type": "secret",
     "hint": "Для Gmail/Mail.ru создайте «пароль приложения» в настройках почты. Хранится в зашифрованном виде."},
    {"key": "command", "label": "Команда для кода", "type": "text", "default": "!code"},
    {"key": "fresh_min", "label": "Искать код в письмах за, минут", "type": "number", "default": 10,
     "hint": "Старые письма игнорируются, чтобы не отдать устаревший код."},
    {"key": "from_filter", "label": "Только от отправителя (необязательно)", "type": "text", "default": "",
     "hint": "Часть адреса отправителя, например steampowered.com. Пусто — любые письма."},
    {"key": "code_pattern", "label": "Шаблон кода", "type": "text", "default": r"\b[A-Z0-9]{5}\b",
     "hint": "Регулярное выражение. По умолчанию — 5 заглавных букв/цифр (Steam Guard)."},
    {"key": "only_buyers", "label": "Отвечать только покупателям", "type": "bool", "default": True},
    {"key": "reply", "label": "Текст с кодом", "type": "text", "default": "Ваш код: {code}"},
    {"key": "not_found", "label": "Если код не найден", "type": "text",
     "default": "Свежего кода в почте нет. Попробуйте запросить код заново и напишите команду ещё раз."},
    {"key": "no_access", "label": "Если нет доступа", "type": "text",
     "default": "Код доступен только после покупки. Если вы купили — напишите продавцу."},
]


def _decode(s):
    out = ""
    for part, enc in decode_header(s or ""):
        out += part.decode(enc or "utf-8", "ignore") if isinstance(part, bytes) else part
    return out


def _body_text(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "ignore")
                except Exception:
                    continue
        # нет plain — берём html как текст
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    html = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "ignore")
                    return re.sub(r"<[^>]+>", " ", html)
                except Exception:
                    continue
        return ""
    try:
        return msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8", "ignore")
    except Exception:
        return ""


def fetch_code(cfg):
    """Возвращает код из свежего письма или None. Бросает Exception при проблеме с подключением."""
    host = cfg["imap_host"].strip()
    user = cfg["email"].strip()
    pwd = cfg["password"]
    if not (host and user and pwd):
        raise Exception("Не заполнены IMAP-сервер, почта или пароль")
    pattern = re.compile(cfg.get("code_pattern") or r"\b[A-Z0-9]{5}\b")
    from_filter = (cfg.get("from_filter") or "").strip().lower()
    fresh = float(cfg.get("fresh_min") or 10) * 60
    now = time.time()

    M = imaplib.IMAP4_SSL(host)
    try:
        M.login(user, pwd)
        M.select("INBOX")
        typ, data = M.search(None, "ALL")
        ids = data[0].split()[-20:]  # последние 20 писем
        for num in reversed(ids):
            typ, msg_data = M.fetch(num, "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            # свежесть
            try:
                sent = time.mktime(email.utils.parsedate(msg.get("Date")))
                if now - sent > fresh:
                    continue
            except Exception:
                pass
            if from_filter and from_filter not in (_decode(msg.get("From")) or "").lower():
                continue
            text = _decode(msg.get("Subject")) + "\n" + _body_text(msg)
            m = pattern.search(text)
            if m:
                return m.group(0)
        return None
    finally:
        try:
            M.logout()
        except Exception:
            pass


def _buyer_has_order(buyer):
    return bool(db.query("SELECT 1 FROM orders WHERE buyer=? AND status IN ('paid','closed') LIMIT 1", (buyer,)))


def test(ctx):
    code = fetch_code(ctx.config)
    return f"Подключение работает. Свежий код: {code}" if code else "Подключение работает. Свежих писем с кодом нет."


def on_message(msg, chat, ctx):
    text = (msg.get("text") or "").strip().lower()
    cmd = ctx.config["command"].strip().lower()
    if not text.startswith(cmd):
        return False
    if ctx.config.get("only_buyers", True) and not _buyer_has_order(chat["name"]):
        ctx.reply(chat, ctx.config["no_access"])
        return True
    try:
        code = fetch_code(ctx.config)
    except Exception as e:
        ctx.reply(chat, "Не удалось получить код, попробуйте позже.")
        ctx.log(f"EmailCode: ошибка почты — {e}", "error")
        return True
    if not code:
        ctx.reply(chat, ctx.config["not_found"])
        return True
    ctx.reply(chat, ctx.config["reply"].replace("{code}", code))
    ctx.log(f"EmailCode: выдан код покупателю {chat['name']}")
    return True
