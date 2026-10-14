"""AutoAppleCard — выдача подарочных карт Apple через ns.gifts.

Схема (по рабочему образцу triuq, через официальный API ns.gifts):
1. Покупатель купил лот Apple Gift Card (номинал + валюта в названии, напр. «50 TRY»).
2. Бот определяет номинал и страну (TR/US/RU), находит нужную услугу в ns.gifts.
3. Покупает карту через ns.gifts и сразу выдаёт код покупателю в чат.

Номинал и страна берутся из названия лота:
- Страна: TRY/try/лира → Турция, USD/dollar → США, RUB/₽/руб → Россия.
- Номинал: число в названии (напр. «Apple 50 TRY» → 50 лир).

Нужно (в настройках, хранится зашифрованно):
- Email и пароль от аккаунта ns.gifts
- Баланс на ns.gifts — им оплачиваются карты

Безопасность: карта покупается по номиналу из заказа, код выдаётся покупателю
в чат. Всё через официальный ns.gifts. Никаких отправок данных на сторону.
"""
import re
import json
import time
import uuid

from core import db

NAME = "AutoAppleCard"
DESCRIPTION = "Выдаёт коды подарочных карт Apple (TR/US/RU) по номиналу из заказа, через ns.gifts."
CATEGORY = "Выдача через поставщиков"
VERSION = "1.0"

NS_URL = "https://api.ns.gifts/api/v1"

# Соответствие номинал → service_id в ns.gifts (из публичного каталога услуг)
SERVICE_IDS_TR = {10: 33, 15: 450, 25: 34, 30: 451, 40: 377, 50: 35, 75: 452, 100: 36,
                  150: 453, 200: 454, 250: 37, 300: 455, 400: 456, 500: 38, 600: 457,
                  700: 458, 1000: 39, 1250: 459, 1500: 460}
SERVICE_IDS_US = {2: 20, 3: 21, 4: 22, 5: 23, 6: 24, 7: 25, 8: 26, 9: 27, 10: 28,
                  20: 29, 25: 30, 50: 31, 100: 32}
SERVICE_IDS_RU = {500: 40, 600: 378, 700: 379, 1000: 41, 1500: 42, 2000: 380, 2500: 381, 5000: 382}

SETTINGS = [
    {"key": "api_login", "label": "Email аккаунта ns.gifts", "type": "secret",
     "hint": "Почта аккаунта ns.gifts. Хранится в зашифрованном виде."},
    {"key": "api_password", "label": "Пароль ns.gifts", "type": "secret"},
    {"key": "done_text", "label": "Текст выдачи кода", "type": "textarea",
     "default": "✅ Готово! Ваша карта Apple:\n{code}\n\nНоминал: {nominal} {currency}\nСпасибо за покупку! 💙 Не забудьте подтвердить заказ и оставить отзыв."},
    {"key": "bad_country_text", "label": "Страна не определена", "type": "text",
     "default": "Не удалось определить страну карты из названия лота. Напишите продавцу."},
    {"key": "bad_nominal_text", "label": "Номинал не поддерживается", "type": "text",
     "default": "Этот номинал карты пока недоступен. Напишите продавцу."},
    {"key": "fail_text", "label": "Ошибка выдачи", "type": "text",
     "default": "Не удалось выдать карту. Напишите продавцу — он поможет."},
]

_TOKEN = {"token": "", "expiry": 0}


def _fill(t, **kw):
    for k, v in kw.items():
        t = t.replace("{" + k + "}", str(v))
    return t


def _detect(order):
    """Определяет (service_dict, currency, nominal) из названия лота."""
    title = (order.get("description") or "").lower()
    m = re.search(r"(\d+)", title)
    nominal = int(m.group(1)) if m else 0
    if "try" in title or "лир" in title or "турц" in title:
        return SERVICE_IDS_TR, "TRY", nominal
    if "usd" in title or "dollar" in title or "долл" in title or "сша" in title or "$" in title:
        return SERVICE_IDS_US, "USD", nominal
    if "rub" in title or "₽" in title or "руб" in title or "росс" in title:
        return SERVICE_IDS_RU, "RUB", nominal
    return None, None, nominal


# ---------- ns.gifts API ----------
def _token(cfg):
    import requests
    if time.time() < _TOKEN["expiry"] and _TOKEN["token"]:
        return _TOKEN["token"]
    r = requests.post(f"{NS_URL}/get_token",
                      json={"email": cfg.get("api_login") or "", "password": cfg.get("api_password") or ""}, timeout=30)
    if r.status_code != 200:
        raise Exception(f"ns.gifts авторизация: {r.status_code}")
    data = r.json()
    tok = data.get("token") or data.get("access_token") or (data.get("data") or {}).get("token")
    _TOKEN["token"] = tok
    _TOKEN["expiry"] = data.get("valid_thru", time.time() + 7200)
    return tok


def _hdr(cfg):
    return {"Authorization": f"Bearer {_token(cfg)}"}


def _buy_card(cfg, service_id):
    """Покупает карту: create_order → pay_order → order_info. Возвращает (список кодов, error)."""
    import requests
    try:
        custom_id = str(uuid.uuid4())
        r = requests.post(f"{NS_URL}/create_order",
                          json={"service_id": service_id, "quantity": 1.0, "custom_id": custom_id, "data": ""},
                          headers=_hdr(cfg), timeout=30)
        if r.status_code != 200:
            return None, f"create_order: {r.text[:80]}"
        # оплата
        p = requests.post(f"{NS_URL}/pay_order", json={"custom_id": custom_id}, headers=_hdr(cfg), timeout=60)
        if p.status_code != 200:
            msg = p.json().get("detail", p.text[:80]) if p.text else p.status_code
            return None, f"pay_order: {msg}"
        # получаем код карты (может быть не сразу — несколько попыток)
        for _ in range(6):
            info = requests.post(f"{NS_URL}/order_info", json={"custom_id": custom_id}, headers=_hdr(cfg), timeout=30)
            if info.status_code == 200:
                data = info.json()
                codes = data.get("pins") or data.get("codes") or []
                if codes:
                    return codes, None
            time.sleep(2)
        return None, "код карты не пришёл от ns.gifts (проверьте заказ в ns.gifts)"
    except Exception as e:
        return None, str(e)


# ---------- обработка заказа ----------
def on_new_order(order, ctx):
    cfg = ctx.config
    if not (cfg.get("api_login") or "").strip() or not (cfg.get("api_password") or "").strip():
        ctx.log(f"AutoAppleCard не настроен (нет данных ns.gifts) — заказ #{order['id']} пропущен", "warn")
        return ctx.SKIP

    service_dict, currency, nominal = _detect(order)
    buyer_id = order.get("buyer_id")
    acc = order["account_id"]

    if not service_dict:
        ctx.log(f"AutoAppleCard: не определена страна карты в заказе #{order['id']}", "warn")
        if buyer_id:
            ctx.send_to_buyer(acc, buyer_id, cfg["bad_country_text"])
        return "Не определена страна карты"

    service_id = service_dict.get(nominal)
    if not service_id:
        ctx.log(f"AutoAppleCard: номинал {nominal} {currency} не поддерживается (заказ #{order['id']})", "warn")
        if buyer_id:
            ctx.send_to_buyer(acc, buyer_id, cfg["bad_nominal_text"])
        return f"Номинал {nominal} {currency} не поддерживается"

    # лог: что покупаем (прозрачность)
    ctx.log(f"AutoAppleCard #{order['id']}: покупаю карту Apple {nominal} {currency} (service_id={service_id}, ns.gifts)", "order")
    codes, err = _buy_card(cfg, service_id)
    if codes:
        code_text = "\n".join(str(c) for c in codes)
        if buyer_id:
            ctx.send_to_buyer(acc, buyer_id, _fill(cfg["done_text"], code=code_text, nominal=nominal, currency=currency))
        ctx.log(f"AutoAppleCard: выдана карта {nominal} {currency} (заказ #{order['id']})", "order")
        return f"Выдана карта {nominal} {currency}"
    else:
        if buyer_id:
            ctx.send_to_buyer(acc, buyer_id, cfg["fail_text"])
        ctx.log(f"AutoAppleCard: ПРОВЕРЬТЕ заказ #{order['id']} — карта не выдана: {err}", "error")
        return None  # в «нужна проверка»


def test(ctx):
    """Проверка: авторизация и баланс ns.gifts."""
    import requests
    cfg = ctx.config
    if not (cfg.get("api_login") or "").strip():
        return {"ok": False, "message": "Не заданы данные ns.gifts"}
    try:
        tok = _token(cfg)
        r = requests.post(f"{NS_URL}/check_balance", headers={"Authorization": f"Bearer {tok}"}, timeout=20)
        if r.status_code == 200:
            return {"ok": True, "message": f"ns.gifts на связи, баланс: {r.json().get('balance', r.json())}"}
        return {"ok": True, "message": "ns.gifts: авторизация успешна"}
    except Exception as e:
        return {"ok": False, "message": f"ns.gifts: {e}"}
