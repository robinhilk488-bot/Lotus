"""ИИ-ответы — отвечает на лёгкие вопросы покупателей по лоту с помощью ИИ.

Берёт вопрос покупателя, описание купленного им лота и вашу памятку (FAQ) и отправляет
их ИИ. Отвечает строго по этой информации; про оплату, возврат, жалобы и всё важное —
молчит и передаёт продавцу.

Подключается к любому OpenAI-совместимому API (OpenAI и многие другие): укажите адрес,
ключ и модель. Ключ хранится на сервере в зашифрованном виде.

Защита:
- Отвечает только на вопросы, ничего не обещает, не называет цены, не решает споры.
- Не выдумывает: если ответа нет в описании/памятке — просит уточнить у продавца.
- Лимит ответов одному покупателю в день — защита от слива баланса на болтовню.
- Стоп-слова и денежные темы → ИИ не вмешивается, пишет продавцу через журнал.
"""
import time

from core import db

NAME = "ИИ-ответы"
DESCRIPTION = "ИИ отвечает на лёгкие вопросы покупателей по лоту. Про оплату и возвраты — передаёт продавцу."
CATEGORY = "Покупатели"
VERSION = "1.0"
TIMEOUT = 40

SETTINGS = [
    {"key": "base_url", "label": "Адрес API", "type": "text", "default": "https://api.openai.com/v1",
     "hint": "OpenAI-совместимый API. Для OpenAI оставьте как есть; для другого провайдера — его адрес."},
    {"key": "api_key", "label": "API-ключ", "type": "secret",
     "hint": "Ключ провайдера ИИ. Платится по использованию. Хранится в зашифрованном виде."},
    {"key": "model", "label": "Модель", "type": "text", "default": "gpt-4o-mini",
     "hint": "Название модели у провайдера. Берите недорогую — ответы короткие."},
    {"key": "faq", "label": "Памятка для ИИ (FAQ)", "type": "textarea",
     "default": "Выдача товара автоматическая, сразу после оплаты.\nЕсли товар не пришёл — напишите !help, позову продавца.\nГарантия 24 часа.",
     "hint": "На основе этого ИИ отвечает. Пишите факты о ваших товарах: сроки, совместимость, как активировать."},
    {"key": "daily_limit", "label": "Ответов одному покупателю в день", "type": "number", "default": 5,
     "hint": "Защита от слива баланса. 0 — без лимита."},
    {"key": "stop_words", "label": "Стоп-слова (ИИ молчит)", "type": "text",
     "default": "возврат, чарджбэк, верну, обман, жалоба",
     "hint": "Через запятую. При этих словах ИИ не отвечает, а зовёт продавца."},
    {"key": "signature", "label": "Пометка в ответе (необязательно)", "type": "text", "default": "",
     "hint": "Добавляется в конце ответа ИИ, например: — автоответ, при сомнениях напишите !help"},
]

# денежные/спорные темы, при которых ИИ никогда не отвечает (жёстко, помимо стоп-слов)
MONEY_WORDS = ["оплат", "деньги", "возврат", "refund", "chargeback", "charge back", "верну", "вернуть деньги",
               "жалоб", "спор", "обман", "развод", "scam", "скидк", "дешевл", "торг"]

SYSTEM_PROMPT = (
    "Ты — вежливый помощник продавца на торговой площадке. Отвечай покупателю КОРОТКО (1–3 предложения), "
    "ТОЛЬКО на основе информации ниже (описание товара и памятка продавца). "
    "Строгие правила: никогда не обещай возвраты, скидки или сроки, которых нет в памятке; "
    "не называй и не меняй цены; не решай споры и не признавай вину; "
    "если в предоставленной информации нет ответа — вежливо попроси написать команду !help, чтобы позвать продавца. "
    "Не выдумывай факты. Отвечай на языке покупателя."
)


def _recent_orders_text(buyer):
    rows = db.query("SELECT description FROM orders WHERE buyer=? ORDER BY ts DESC LIMIT 3", (buyer,))
    descs = [r["description"] for r in rows if r["description"]]
    return "\n".join(f"- {d}" for d in descs) if descs else "(покупатель пока без заказов)"


def _count_key(chat):
    return f"count:{chat['account_id']}:{chat['name']}:{time.strftime('%Y-%m-%d')}"


def _today_count(key):
    r = db.query("SELECT value FROM plugin_kv WHERE plugin_id='ai_assistant' AND key=?", (key,))
    return int(r[0]["value"]) if r else 0


def _inc(key):
    db.execute("INSERT OR REPLACE INTO plugin_kv(plugin_id, key, value) VALUES('ai_assistant', ?, ?)",
               (key, str(_today_count(key) + 1)))


def _ask_ai(ctx, user_text, context):
    base = ctx.config["base_url"].strip().rstrip("/")
    key = ctx.config["api_key"].strip()
    model = ctx.config["model"].strip() or "gpt-4o-mini"
    if not base or not key:
        raise Exception("Не заполнен адрес API или ключ")
    r = ctx.http.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "max_tokens": 250,
            "temperature": 0.3,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"{context}\n\nВопрос покупателя: {user_text}"},
            ],
        },
    )
    try:
        data = r.json()
    except ValueError:
        raise Exception(f"ИИ-провайдер вернул не JSON (код {r.status_code})")
    if r.status_code >= 400:
        msg = (data.get("error") or {}).get("message") if isinstance(data, dict) else None
        raise Exception(msg or f"ИИ-провайдер ответил {r.status_code}")
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError):
        raise Exception("ИИ-провайдер вернул неожиданный ответ")


def test(ctx):
    ans = _ask_ai(ctx, "Скажи одним словом: тест", "Это проверка соединения.")
    return f"Работает. Модель ответила: {ans[:60]}"


def on_message(msg, chat, ctx):
    text = (msg.get("text") or "").strip()
    low = text.lower()
    if not text or text.startswith("!") or len(text) < 3:
        return False
    # вопросов обычно есть «?» или вопросительные слова; на простые реплики не тратим ИИ
    if "?" not in text and not any(w in low for w in ("как", "можно", "подойд", "сколько", "когда", "где", "что", "почему")):
        return False
    # денежные/спорные темы и стоп-слова — ИИ молчит, зовём продавца
    stop = [w.strip().lower() for w in (ctx.config.get("stop_words") or "").split(",") if w.strip()]
    if any(w in low for w in MONEY_WORDS) or any(w and w in low for w in stop):
        ctx.log(f"ИИ не отвечает {chat['name']} (денежная/стоп-тема): {text[:50]}", "warn")
        return False
    # лимит в день
    limit = int(ctx.config.get("daily_limit") or 0)
    ckey = _count_key(chat)
    if limit and _today_count(ckey) >= limit:
        ctx.log(f"ИИ: лимит ответов для {chat['name']} исчерпан", "warn")
        return False

    context = f"Товары, которые покупал этот покупатель:\n{_recent_orders_text(chat['name'])}\n\nПамятка продавца:\n{ctx.config.get('faq', '')}"
    if ctx.dry_run:
        ctx.log(f"Пробный вопрос: {text}\nКонтекст собран, отправил бы ИИ.")
        try:
            ans = _ask_ai(ctx, text, context)
            ctx.log(f"Ответ ИИ: {ans}")
        except Exception as e:
            ctx.log(f"Ошибка ИИ: {e}", "error")
        return True
    try:
        answer = _ask_ai(ctx, text, context)
    except Exception as e:
        ctx.log(f"ИИ: ошибка ответа — {e}", "error")
        return False  # при сбое молчим, пусть сработает обычный сценарий
    sig = ctx.config.get("signature", "").strip()
    if sig:
        answer = f"{answer}\n{sig}"
    ctx.reply(chat, answer)
    _inc(ckey)
    ctx.log(f"ИИ ответил {chat['name']}: {answer[:50]}")
    return True
