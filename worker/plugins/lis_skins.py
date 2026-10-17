"""LIS-SKINS — продажа скинов CS2 через api.lis-skins.com.

Схема (по официальному API LIS-SKINS):
1. Покупатель купил лот скина на FunPay (к лоту привязан ID скина из lis-skins).
2. Бот спрашивает Trade URL покупателя (ссылка обмена Steam).
3. Бот покупает привязанный скин на LIS-SKINS и сразу отправляет его
   на Trade URL покупателя — скин приходит ему в Steam (нужно принять обмен).

Лот = скин: к каждому лоту привязывается ID скина (из lis-skins.com).

Нужно (в настройках, хранится зашифрованно):
- API-ключ LIS-SKINS (с lis-skins.com)
- Баланс на LIS-SKINS — им оплачиваются скины

Безопасность: скин покупается на Trade URL покупателя (не на чужой аккаунт).
ID скина берётся из настройки лота. Никаких зашитых получателей.
Каждая покупка логируется.
"""
import re
import json
import time
import uuid

from core import db

NAME = "LIS-SKINS"
DESCRIPTION = "Продажа скинов CS2: к лоту привязан скин, бот покупает его на LIS-SKINS и отправляет покупателю."
CATEGORY = "Выдача через поставщиков"
VERSION = "1.0"
TIMEOUT = 1800  # ждём Trade URL от покупателя

NS_URL = "https://api.lis-skins.com/v1"

SETTINGS = [
    {"key": "api_key", "label": "API-ключ LIS-SKINS", "type": "secret",
     "hint": "Ключ с lis-skins.com (раздел API). Хранится в зашифрованном виде."},
    {"key": "lot_skins", "label": "Лоты и скины (фраза=ID скина)", "type": "textarea", "default": "",
     "hint": "По строке: фраза из названия лота = ID скина из lis-skins. Напр. «AK-47 Redline=125345». Можно несколько ID через + для набора: «Нож=1001+1002»."},
    {"key": "max_overpay", "label": "Допустимая переплата, %", "type": "number", "default": 10,
     "hint": "Защита: бот не купит скин дороже, чем цена лота + этот процент. 0 — без лимита."},
    {"key": "ask_trade", "label": "Запрос Trade URL", "type": "textarea",
     "default": "Спасибо за покупку! 🎮\nПришлите вашу Trade-ссылку Steam, куда отправить скин.\nГде взять: steamcommunity.com/id/me/tradeoffers/privacy — скопируйте «Trade URL»."},
    {"key": "done_text", "label": "После отправки", "type": "textarea",
     "default": "Готово! Скин отправлен вам в Steam — примите обмен в течение 12 часов.\nЕсли обмена нет — проверьте, что Trade-ссылка верная, и напишите продавцу."},
    {"key": "bad_trade_text", "label": "Неверная Trade-ссылка", "type": "text",
     "default": "Это не похоже на Trade URL. Пришлите ссылку вида steamcommunity.com/tradeoffer/new/?partner=...&token=..."},
    {"key": "fail_text", "label": "Ошибка покупки", "type": "text",
     "default": "Не удалось купить скин (возможно, его уже купили или нет на складе). Напишите продавцу — он поможет."},
    {"key": "remind_text", "label": "Напоминание", "type": "text",
     "default": "Напоминаем: пришлите вашу Trade-ссылку Steam, чтобы получить скин."},
]


def _jobs():
    r = db.query("SELECT value FROM plugin_kv WHERE plugin_id='lis_skins' AND key='jobs'")
    return json.loads(r[0]["value"]) if r else {}


def _save(d):
    db.execute("INSERT OR REPLACE INTO plugin_kv(plugin_id,key,value) VALUES('lis_skins','jobs',?)",
               (json.dumps(d, ensure_ascii=False),))


def _fill(t, **kw):
    for k, v in kw.items():
        t = t.replace("{" + k + "}", str(v))
    return t


def _skin_ids(order, cfg):
    """ID скина(ов), привязанных к лоту заказа. Список int или None."""
    desc = (order.get("description") or "").lower()
    for line in (cfg.get("lot_skins") or "").splitlines():
        if "=" not in line:
            continue
        phrase, ids = line.split("=", 1)
        phrase = phrase.strip().lower()
        if phrase and phrase in desc:
            nums = [int(x) for x in re.findall(r"\d+", ids)]
            if nums:
                return nums
    return None


def parse_trade_url(url):
    """Из Trade URL достаём partner и token. Возвращает (partner, token) или (None, None)."""
    url = (url or "").strip()
    p = re.search(r"partner=(\d+)", url)
    t = re.search(r"token=([A-Za-z0-9_-]+)", url)
    if p and t:
        return p.group(1), t.group(1)
    return None, None


# ---------- LIS-SKINS API ----------
def _hdr(cfg):
    return {"Authorization": f"Bearer {cfg.get('api_key') or ''}",
            "Accept": "application/json", "Content-Type": "application/json"}


def _balance(cfg):
    import requests
    r = requests.get(f"{NS_URL}/user/balance", headers=_hdr(cfg), timeout=20)
    if r.status_code == 200:
        d = r.json()
        return d.get("data", {}).get("balance", d.get("balance"))
    raise Exception(f"баланс: {r.status_code}")


def _buy(cfg, ids, partner, token, max_price, order_id):
    """Покупает скин(ы) на Trade URL покупателя. Возвращает (ok, info)."""
    import requests
    payload = {
        "ids": ids,
        "partner": partner,
        "token": token,
        "custom_id": f"fp-{order_id}",   # защита от двойной покупки
        "skip_unavailable": True,
    }
    if max_price and max_price > 0:
        payload["max_price"] = round(max_price, 2)
    try:
        r = requests.post(f"{NS_URL}/market/buy", json=payload, headers=_hdr(cfg), timeout=60)
        if r.status_code == 200:
            data = r.json().get("data", {})
            return True, data
        # частые ошибки
        txt = r.text[:150]
        if r.status_code == 400 and "balance" in txt.lower():
            return False, "недостаточно средств на LIS-SKINS"
        return False, f"{r.status_code}: {txt}"
    except Exception as e:
        return False, str(e)


# ---------- обработка заказа ----------
def on_new_order(order, ctx):
    cfg = ctx.config
    if not (cfg.get("api_key") or "").strip():
        ctx.log(f"LIS-SKINS не настроен (нет API-ключа) — заказ #{order['id']} пропущен", "warn")
        return ctx.SKIP
    ids = _skin_ids(order, cfg)
    if not ids:
        ctx.log(f"LIS-SKINS: к заказу #{order['id']} не привязан скин — пропущен", "warn")
        return ctx.SKIP
    buyer_id = order.get("buyer_id")
    if not buyer_id:
        return ctx.SKIP
    # лимит цены: цена заказа + допустимая переплата
    overpay = float(cfg.get("max_overpay") or 0)
    max_price = 0
    if overpay and order.get("amount"):
        max_price = float(order["amount"]) * (1 + overpay / 100)
    jobs = _jobs()
    jobs[str(order["id"])] = {
        "order_id": order["id"], "account_id": order["account_id"],
        "buyer": order["buyer"], "buyer_id": buyer_id,
        "ids": ids, "max_price": max_price,
        "stage": "await_trade", "ts": time.time(), "reminded": False,
    }
    _save(jobs)
    ctx.send_to_buyer(order["account_id"], buyer_id, cfg["ask_trade"])
    ctx.log(f"LIS-SKINS: заказ #{order['id']} (скин {ids}) — жду Trade URL", "order")
    return "Жду Trade URL покупателя"


def on_message(msg, chat, ctx):
    text = (msg.get("text") or "").strip()
    if not text:
        return False
    cfg = ctx.config
    jobs = _jobs()
    job = None
    for j in jobs.values():
        if j["account_id"] == chat["account_id"] and (j["buyer"] or "").lower() == (chat["name"] or "").lower():
            job = j
            break
    if not job:
        return False

    partner, token = parse_trade_url(text)
    if not partner or not token:
        ctx.reply(chat, cfg["bad_trade_text"])
        return True

    # покупаем скин на Trade URL покупателя
    ctx.log(f"LIS-SKINS #{job['order_id']}: покупаю скин {job['ids']} на Trade URL покупателя "
            f"(partner={partner})", "order")
    ok, info = _buy(cfg, job["ids"], partner, token, job.get("max_price", 0), job["order_id"])
    if ok:
        jobs.pop(str(job["order_id"]), None)
        _save(jobs)
        ctx.reply(chat, cfg["done_text"])
        skins = info.get("skins", []) if isinstance(info, dict) else []
        names = ", ".join(s.get("name", "") for s in skins) or "скин"
        ctx.log(f"LIS-SKINS: выдан {names} (заказ #{job['order_id']}, purchase {info.get('purchase_id','?')})", "order")
    else:
        ctx.reply(chat, cfg["fail_text"])
        ctx.log(f"LIS-SKINS: ПРОВЕРЬТЕ заказ #{job['order_id']} — скин не куплен: {info}", "error")
    return True


def tick(ctx):
    cfg = ctx.config
    jobs = _jobs()
    now = time.time()
    changed = False
    for oid, job in list(jobs.items()):
        age = now - job.get("ts", now)
        if age > TIMEOUT:
            try:
                ctx.send_to_buyer(job["account_id"], job["buyer_id"], cfg["fail_text"])
            except Exception:
                pass
            jobs.pop(oid, None)
            changed = True
            ctx.log(f"LIS-SKINS: заказ #{job['order_id']} — покупатель не прислал Trade URL, снято", "warn")
        elif age > TIMEOUT / 2 and not job.get("reminded"):
            try:
                ctx.send_to_buyer(job["account_id"], job["buyer_id"], cfg["remind_text"])
            except Exception:
                pass
            job["reminded"] = True
            jobs[oid] = job
            changed = True
    if changed:
        _save(jobs)


def test(ctx):
    """Проверка: ключ и баланс LIS-SKINS."""
    cfg = ctx.config
    if not (cfg.get("api_key") or "").strip():
        return {"ok": False, "message": "Не задан API-ключ LIS-SKINS"}
    try:
        bal = _balance(cfg)
        return {"ok": True, "message": f"LIS-SKINS на связи, баланс: {bal}"}
    except Exception as e:
        return {"ok": False, "message": f"LIS-SKINS: {e} (проверьте API-ключ)"}
