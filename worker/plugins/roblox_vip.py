"""AutoRobux — выдача Robux покупателю через Game Pass.

Схема (стандартная для продажи Robux, по рабочему образцу):
1. Покупатель купил лот на N Robux.
2. Бот просит создать Game Pass стоимостью N / 0.7 Robux (Roblox берёт ~30% комиссии,
   покупатель получит чистыми ровно N).
3. Покупатель присылает ID или ссылку на свой Game Pass.
4. Бот проверяет, что цена Game Pass именно та, что нужна (защита от обмана),
   и покупает его через ваши Roblox-аккаунты (cookie) — Robux уходят покупателю.

Нужно (в настройках, хранится зашифрованно):
- Roblox cookie (.ROBLOSECURITY) одного или нескольких аккаунтов, с которых покупаются Game Pass.
  С этих аккаунтов тратятся Robux. Держите на них запас.

Безопасность: плагин покупает ИМЕННО тот Game Pass, который прислал покупатель,
проверяет его цену и продавца по данным самого Roblox. Никаких сторонних проверок
и зависимостей. Перед каждой покупкой пишет в журнал, что и за сколько покупает.
"""
import re
import json
import time
import math

from core import db
from core.crypto import decrypt

NAME = "AutoRobux"
DESCRIPTION = "Выдаёт Robux покупателю: он создаёт Game Pass на нужную сумму, бот его покупает через ваши Roblox-аккаунты."
CATEGORY = "Выдача через поставщиков"
VERSION = "2.0"
TIMEOUT = 1800  # ждём Game Pass от покупателя, сек
COMMISSION = 0.7  # Roblox отдаёт продавцу 70% цены Game Pass

SETTINGS = [
    {"key": "cookies", "label": "Roblox cookie аккаунтов", "type": "secret",
     "hint": "По одному .ROBLOSECURITY на строку. С этих аккаунтов покупаются Game Pass (тратятся Robux). Хранятся в зашифрованном виде."},
    {"key": "ask_text", "label": "Текст после оплаты", "type": "textarea",
     "default": "Заказ на {robux} Robux принят!\nСоздайте Game Pass стоимостью {price} Robux и пришлите его ID или ссылку.\nИнструкция: https://telegra.ph/Kak-sozdat-gamepass-06-11\nПочему {price}, а не {robux}: Roblox берёт ~30% комиссии, вы получите ровно {robux} чистыми."},
    {"key": "wrong_price_text", "label": "Неверная цена Game Pass", "type": "text",
     "default": "Game Pass должен стоить ровно {price} Robux. Поправьте цену и пришлите ID снова."},
    {"key": "done_text", "label": "После выдачи", "type": "text",
     "default": "Готово! Game Pass куплен, {robux} Robux у вас. Не забудьте подтвердить заказ! 💜"},
    {"key": "bad_id_text", "label": "Не распознан ID", "type": "text",
     "default": "Не понял ID Game Pass. Пришлите число или ссылку вида roblox.com/game-pass/123456."},
    {"key": "fail_text", "label": "Не удалось купить", "type": "text",
     "default": "Не получилось купить Game Pass. Проверьте, что он публичный и за Robux, или напишите продавцу."},
    {"key": "remind_text", "label": "Напоминание", "type": "text",
     "default": "Напоминаем: создайте Game Pass на нужную сумму и пришлите его ID, чтобы получить Robux."},
]


def _jobs():
    r = db.query("SELECT value FROM plugin_kv WHERE plugin_id='roblox_vip' AND key='jobs'")
    return json.loads(r[0]["value"]) if r else {}


def _save(d):
    db.execute("INSERT OR REPLACE INTO plugin_kv(plugin_id,key,value) VALUES('roblox_vip','jobs',?)",
               (json.dumps(d, ensure_ascii=False),))


def _fill(t, **kw):
    for k, v in kw.items():
        t = t.replace("{" + k + "}", str(v))
    return t


def _cookies(cfg):
    raw = cfg.get("cookies") or ""
    try:
        raw = decrypt(raw) if raw and not raw.startswith(".ROBLOSECURITY") and "\n" not in raw else raw
    except Exception:
        pass
    out = []
    for line in raw.splitlines():
        c = line.strip()
        if not c:
            continue
        out.append(c if c.startswith(".ROBLOSECURITY=") else f".ROBLOSECURITY={c}")
    return out


def _robux_from_order(order, ctx):
    """Сколько Robux купил покупатель: число из названия × количество, иначе из заказа."""
    desc = order.get("description") or ""
    m = re.search(r"(\d+)\s*(?:robux|робукс|r\$)", desc, re.I)
    per = int(m.group(1)) if m else 0
    qty = 1
    try:
        qty = ctx.order_quantity(order) or 1
    except Exception:
        pass
    if per:
        return per * qty
    nums = re.findall(r"\d+", desc)
    return (int(nums[-1]) * qty) if nums else 0


# ---------- Roblox API ----------
def _gamepass_info(gamepass_id):
    """Цена и продавец Game Pass по данным Roblox. (price, product_id, seller_id) или None."""
    import httpx
    url = f"https://apis.roblox.com/game-passes/v1/game-passes/{gamepass_id}/product-info"
    try:
        r = httpx.get(url, timeout=12)
        if r.status_code != 200:
            return None
        info = r.json()
        return (info.get("PriceInRobux"), info.get("ProductId"), info.get("Creator", {}).get("Id"))
    except Exception:
        return None


def _buy_gamepass(cookie, gamepass_id, expected_price, ctx):
    """Покупает Game Pass через аккаунт. True/False."""
    import httpx
    headers = {"Cookie": cookie, "User-Agent": "Mozilla/5.0"}
    info = _gamepass_info(gamepass_id)
    if not info:
        return False
    price, product_id, seller_id = info
    if price is None or product_id is None or seller_id is None:
        return False
    if expected_price and price != expected_price:
        ctx.log(f"AutoRobux: цена Game Pass {gamepass_id} = {price}, ожидалось {expected_price} — не покупаю", "warn")
        return False
    purchase_url = f"https://economy.roblox.com/v1/purchases/products/{product_id}"
    try:
        # получаем csrf-токен
        t = httpx.post(purchase_url, headers=headers, timeout=12)
        csrf = t.headers.get("x-csrf-token")
        if not csrf:
            return False
        # ЛОГ: покупаем именно этот Game Pass, за эту цену, у этого продавца (данные Roblox)
        ctx.log(f"AutoRobux: покупаю Game Pass {gamepass_id} за {price} Robux "
                f"(продавец Roblox id={seller_id}, данные от Roblox)", "order")
        r = httpx.post(purchase_url, headers={**headers, "X-CSRF-Token": csrf, "Content-Type": "application/json"},
                       json={"expectedCurrency": 1, "expectedPrice": price, "expectedSellerId": seller_id}, timeout=15)
        return bool(r.json().get("purchased"))
    except Exception as e:
        ctx.log(f"AutoRobux: ошибка покупки Game Pass {gamepass_id}: {e}", "warn")
        return False


# ---------- обработка заказа ----------
def on_new_order(order, ctx):
    cfg = ctx.config
    if not _cookies(cfg):
        ctx.log("AutoRobux не настроен (нет Roblox cookie) — заказ пропущен", "warn")
        return ctx.SKIP
    robux = _robux_from_order(order, ctx)
    if not robux:
        ctx.log(f"AutoRobux: не понял количество Robux в заказе #{order['id']} — пропущен", "warn")
        return ctx.SKIP
    price = math.ceil(robux / COMMISSION)  # цена Game Pass с учётом комиссии Roblox
    buyer_id = order.get("buyer_id")
    if not buyer_id:
        return ctx.SKIP
    jobs = _jobs()
    jobs[str(order["id"])] = {
        "order_id": order["id"], "account_id": order["account_id"],
        "buyer": order["buyer"], "buyer_id": buyer_id,
        "robux": robux, "price": price, "ts": time.time(), "reminded": False,
    }
    _save(jobs)
    ctx.send_to_buyer(order["account_id"], buyer_id, _fill(cfg["ask_text"], robux=robux, price=price))
    ctx.log(f"AutoRobux: заказ #{order['id']} на {robux} Robux — жду Game Pass за {price}", "order")
    return "Жду Game Pass от покупателя"


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

    # распознаём ID Game Pass (число или ссылка)
    m = re.search(r"game-pass(?:/|\?id=)(\d+)", text, re.I)
    gid = int(m.group(1)) if m else (int(text) if text.isdigit() else None)
    if not gid:
        ctx.reply(chat, cfg["bad_id_text"])
        return True

    # проверяем цену
    info = _gamepass_info(gid)
    if not info or info[0] is None:
        ctx.reply(chat, cfg["bad_id_text"])
        return True
    price = info[0]
    if price != job["price"]:
        ctx.reply(chat, _fill(cfg["wrong_price_text"], price=job["price"]))
        return True

    # покупаем через аккаунты по очереди
    bought = False
    for cookie in _cookies(cfg):
        if _buy_gamepass(cookie, gid, job["price"], ctx):
            bought = True
            break
    if bought:
        jobs.pop(str(job["order_id"]), None)
        _save(jobs)
        ctx.reply(chat, _fill(cfg["done_text"], robux=job["robux"]))
        ctx.log(f"AutoRobux: выдано {job['robux']} Robux (Game Pass {gid}, заказ #{job['order_id']})", "order")
    else:
        ctx.reply(chat, cfg["fail_text"])
        ctx.log(f"AutoRobux: ПРОВЕРЬТЕ заказ #{job['order_id']} — Game Pass {gid} не куплен", "error")
    return True


def tick(ctx):
    cfg = ctx.config
    jobs = _jobs()
    now = time.time()
    changed = False
    for oid, job in list(jobs.items()):
        age = now - job.get("ts", now)
        if age > TIMEOUT:
            jobs.pop(oid, None)
            changed = True
            ctx.log(f"AutoRobux: заказ #{job['order_id']} — покупатель не прислал Game Pass, ожидание снято", "warn")
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
    """Проверка: доступны ли Roblox-аккаунты и их баланс."""
    import httpx
    cfg = ctx.config
    cks = _cookies(cfg)
    if not cks:
        return {"ok": False, "message": "Не заданы Roblox cookie"}
    results = []
    for i, cookie in enumerate(cks, 1):
        try:
            r = httpx.get("https://users.roblox.com/v1/users/authenticated",
                          headers={"Cookie": cookie, "User-Agent": "Mozilla/5.0"}, timeout=10)
            if r.status_code != 200:
                results.append(f"#{i}: cookie недействителен")
                continue
            uid = r.json().get("id")
            b = httpx.get(f"https://economy.roblox.com/v1/users/{uid}/currency",
                          headers={"Cookie": cookie, "User-Agent": "Mozilla/5.0"}, timeout=10)
            bal = b.json().get("robux", 0) if b.status_code == 200 else "?"
            results.append(f"#{i}: ок, баланс {bal} Robux")
        except Exception as e:
            results.append(f"#{i}: ошибка {e}")
    ok = any("ок" in x for x in results)
    return {"ok": ok, "message": " · ".join(results)}
