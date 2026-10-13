"""AutoStars — автовыдача Telegram Stars через Fragment + TON.

Как работает (как у рабочего плагина Kaderix):
1. Покупатель оплачивает Stars-лот на FunPay.
2. Бот спрашивает Telegram-юзернейм покупателя.
3. Покупатель присылает @username, бот проверяет получателя через Fragment.com
   и готовит покупку (узнаёт адрес и сумму в TON).
4. Покупатель подтверждает (да / нет / отмена).
5. Бот отправляет TON-транзакцию со своего кошелька → Stars приходят покупателю.

Нужно (в настройках, всё хранится зашифрованно):
- Мнемоника TON-кошелька (12/24 слова) — с него оплачиваются Stars
- TON API Key (tonapi.io) — для проверки баланса и работы с сетью
- Fragment hash и Cookie (со страницы покупки Stars на fragment.com)

Количество Stars: число из названия лота × количество в заказе; если в названии нет —
берётся количество из заказа FunPay. Минимум — 50.
"""
import re
import time
import json
import base64

from core import db

NAME = "AutoStars"
DESCRIPTION = "Автовыдача Telegram Stars напрямую через Fragment и TON-кошелёк. Получатель — юзернейм покупателя."
CATEGORY = "Telegram"
VERSION = "3.0"
TIMEOUT = 600  # сколько ждём юзернейм/подтверждение, сек

FRAGMENT_URL = "https://fragment.com/api"
MIN_STARS = 50

SETTINGS = [
    {"key": "mnemonic", "label": "Мнемоника TON-кошелька (12/24 слова)", "type": "secret",
     "hint": "Слова кошелька, с которого оплачиваются Stars. Хранятся в зашифрованном виде. Держите на кошельке запас TON на комиссию."},
    {"key": "ton_api_key", "label": "TON API Key (tonapi.io)", "type": "secret",
     "hint": "Ключ с tonapi.io — для проверки баланса и отправки транзакций."},
    {"key": "fragment_hash", "label": "Fragment hash", "type": "secret",
     "hint": "Значение hash из запроса fragment.com/api?hash=... (DevTools → Network на странице покупки Stars). Можно вставить весь URL."},
    {"key": "fragment_cookie", "label": "Fragment Cookie", "type": "secret",
     "hint": "Весь Request Headers → Cookie с fragment.com (обязательны stel_ssid, stel_token, stel_ton_token)."},
    {"key": "allowed_qty", "label": "Допустимые количества Stars", "type": "text", "default": "",
     "hint": "Через запятую, напр. 50, 75, 100, 250. Пусто — любое целое от 50."},
    {"key": "ask_username", "label": "Запрос юзернейма", "type": "textarea",
     "default": "Спасибо за заказ на {quantity} ⭐!\nПришлите ваш Telegram-юзернейм (@username или t.me/username), куда зачислить звёзды."},
    {"key": "confirm_text", "label": "Запрос подтверждения", "type": "textarea",
     "default": "Проверил: {username} — всё верно. Отправить {quantity} ⭐?\nНапишите «да» для подтверждения или «нет», чтобы указать другой юзернейм."},
    {"key": "done_text", "label": "После выдачи", "type": "textarea",
     "default": "Готово! {quantity} ⭐ отправлены на {username}. Спасибо за покупку! 💜\nПроверить транзакцию: {tx}"},
    {"key": "notfound_text", "label": "Юзернейм не найден", "type": "text",
     "default": "Не нашёл такого Telegram-юзернейма. Проверьте и пришлите ещё раз (например, @username)."},
    {"key": "remind_text", "label": "Напоминание", "type": "text",
     "default": "Напоминаем: пришлите Telegram-юзернейм, чтобы мы отправили ваши Stars."},
    {"key": "cancel_text", "label": "Отмена/ошибка", "type": "text",
     "default": "Не получилось оформить выдачу. Обратитесь к продавцу — он поможет."},
]

_FRAG_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Host": "fragment.com",
    "Origin": "https://fragment.com",
    "Referer": "https://fragment.com/stars/buy",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
    "X-Requested-With": "XMLHttpRequest",
}


# ---------- хранилище «ждём от покупателя» ----------
def _jobs():
    r = db.query("SELECT value FROM plugin_kv WHERE plugin_id='auto_stars' AND key='jobs'")
    return json.loads(r[0]["value"]) if r else {}


def _save(d):
    db.execute("INSERT OR REPLACE INTO plugin_kv(plugin_id,key,value) VALUES('auto_stars','jobs',?)",
               (json.dumps(d, ensure_ascii=False),))


def _fill(t, **kw):
    for k, v in kw.items():
        t = t.replace("{" + k + "}", str(v))
    return t


def _clean_username(u):
    u = (u or "").strip()
    for p in ("https://t.me/", "http://t.me/", "t.me/", "@"):
        if u.lower().startswith(p):
            u = u[len(p):]
    return u.strip().split()[0] if u.strip() else ""


def _quantity(order, cfg):
    """Количество Stars: число из названия лота × количество в заказе, иначе количество из заказа."""
    import core.plugins as _p
    desc = order.get("description") or ""
    m = re.search(r"(\d+)\s*(?:stars|star|зв[её]зд|⭐|★)", desc, re.I)
    per = int(m.group(1)) if m else 0
    qty_order = None
    try:
        qty_order = _p.Ctx("auto_stars").order_quantity(order)
    except Exception:
        pass
    if per:
        return per * (qty_order or 1)
    return qty_order or per or 0


def _allowed(qty, cfg):
    lst = [int(x) for x in re.findall(r"\d+", cfg.get("allowed_qty") or "")]
    if qty < MIN_STARS:
        return False
    return (qty in lst) if lst else True


# ---------- Fragment: проверка получателя и подготовка покупки ----------
def _frag_hash(cfg):
    h = (cfg.get("fragment_hash") or "").strip()
    if "hash=" in h:
        h = h.split("hash=")[-1].split("&")[0]
    return h


def _frag_post(cfg, payload):
    import requests
    url = f"{FRAGMENT_URL}?hash={_frag_hash(cfg)}"
    headers = dict(_FRAG_HEADERS)
    headers["Cookie"] = cfg.get("fragment_cookie") or ""
    r = requests.post(url, headers=headers, data=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def _prepare_purchase(cfg, username, quantity):
    """Возвращает (ok, amount_ton, comment, error). Как в рабочем коде: search → init → link."""
    user = _clean_username(username)
    # 1) поиск получателя
    try:
        res = _frag_post(cfg, {"query": user, "quantity": quantity, "method": "searchStarsRecipient"})
    except Exception as e:
        return False, 0, "", f"Fragment не ответил при поиске получателя: {e}"
    found = (res or {}).get("found") or {}
    recipient = found.get("recipient")
    if not recipient:
        return False, 0, "", "notfound"
    # 2) инициализация покупки — узнаём сумму TON
    try:
        init = _frag_post(cfg, {"recipient": recipient, "quantity": quantity, "method": "initBuyStarsRequest"})
    except Exception as e:
        return False, 0, "", f"Fragment не ответил при подготовке покупки: {e}"
    if "req_id" not in init or "amount" not in init:
        return False, 0, "", f"Fragment не подготовил покупку: {init.get('error', init)}"
    req_id = init["req_id"]
    try:
        amount = float(init["amount"])
    except (TypeError, ValueError):
        return False, 0, "", "Fragment вернул некорректную сумму"
    # 3) получаем payload транзакции → из него ref_id для комментария
    payload_link = {
        "account": '{"address":"0:0000000000000000000000000000000000000000000000000000000000000000","chain":"-239"}',
        "device": '{"platform":"android","appName":"Tonkeeper","appVersion":"5.0.18","maxProtocolVersion":2,"features":["SendTransaction",{"name":"SendTransaction","maxMessages":4}]}',
        "transaction": "1", "id": req_id, "show_sender": "0", "method": "getBuyStarsLink",
    }
    try:
        link = _frag_post(cfg, payload_link)
    except Exception as e:
        return False, 0, "", f"Fragment не отдал данные транзакции: {e}"
    tx = (link or {}).get("transaction") or {}
    msgs = tx.get("messages") or []
    if not msgs:
        return False, 0, "", f"Fragment не вернул транзакцию: {link.get('error', link)}"
    payload_b64 = msgs[0].get("payload")
    address = msgs[0].get("address")
    ref_id = ""
    try:
        raw = payload_b64 + "=" * (-len(payload_b64) % 4)
        decoded = base64.b64decode(raw).decode("latin1")
        ref_id = decoded.split("Ref#")[-1].strip()
    except Exception:
        pass
    comment = f"{quantity} Telegram Stars \n\nRef#{ref_id}"
    return True, amount, comment, (address or "")


# ---------- TON: отправка транзакции (через tonutils, как в рабочем коде) ----------
def _send_ton(cfg, destination, amount, comment):
    """Отправляет amount TON на destination с комментарием. Возвращает (tx_hash, error)."""
    try:
        from tonutils.client import TonapiClient
        from tonutils.wallet import WalletV4R2
    except ImportError:
        return None, "Не установлен модуль tonutils на сервере"
    import asyncio

    async def _run():
        client = TonapiClient(api_key=cfg.get("ton_api_key") or "", is_testnet=False)
        wallet, _pub, _priv, _mn = WalletV4R2.from_mnemonic(client, (cfg.get("mnemonic") or "").split())
        # проверка баланса
        try:
            bal = await wallet.balance()
            bal_ton = bal / 1e9 if bal > 1000 else float(bal)
        except Exception:
            bal_ton = None
        if bal_ton is not None and bal_ton < amount:
            return None, f"Недостаточно TON: нужно {amount}, на кошельке {bal_ton}"
        tx_hash = await wallet.transfer(destination=destination, amount=amount, body=comment)
        return tx_hash, None

    try:
        return asyncio.run(_run())
    except Exception as e:
        return None, f"Ошибка отправки TON: {e}"


# ---------- обработка заказа ----------
def on_new_order(order, ctx):
    cfg = ctx.config
    # проверим, что всё настроено
    for k in ("mnemonic", "ton_api_key", "fragment_hash", "fragment_cookie"):
        if not (cfg.get(k) or "").strip():
            ctx.log(f"AutoStars не настроен (нет {k}) — заказ #{order['id']} не обработан", "warn")
            return ctx.SKIP

    qty = _quantity(order, cfg)
    if not _allowed(qty, cfg):
        ctx.log(f"AutoStars: количество {qty} вне допустимого — заказ #{order['id']} пропущен", "warn")
        return ctx.SKIP

    buyer_id = order.get("buyer_id")
    if not buyer_id:
        return ctx.SKIP

    jobs = _jobs()
    jobs[str(order["id"])] = {
        "order_id": order["id"], "account_id": order["account_id"],
        "buyer": order["buyer"], "buyer_id": buyer_id,
        "quantity": qty, "stage": "await_username",
        "username": "", "amount": 0, "ts": time.time(), "reminded": False,
    }
    _save(jobs)
    ctx.send_to_buyer(order["account_id"], buyer_id, _fill(cfg["ask_username"], quantity=qty))
    ctx.log(f"AutoStars: жду юзернейм для заказа #{order['id']} ({qty} ⭐)", "order")
    return "Жду юзернейм покупателя"


def on_message(msg, chat, ctx):
    text = (msg.get("text") or "").strip()
    if not text:
        return False
    cfg = ctx.config
    jobs = _jobs()
    # находим задание этого покупателя
    job = None
    for j in jobs.values():
        if j["account_id"] == chat["account_id"] and (j["buyer"] or "").lower() == (chat["name"] or "").lower():
            job = j
            break
    if not job:
        return False

    low = text.lower()

    if job["stage"] == "await_username":
        # покупатель прислал юзернейм — проверяем через Fragment
        ok, amount, comment, info = _prepare_purchase(cfg, text, job["quantity"])
        if not ok and info == "notfound":
            ctx.reply(chat, cfg["notfound_text"])
            return True
        if not ok:
            ctx.log(f"AutoStars: Fragment-ошибка по заказу #{job['order_id']}: {info}", "warn")
            ctx.reply(chat, cfg["cancel_text"])
            return True
        job["username"] = _clean_username(text)
        job["amount"] = amount
        job["comment"] = comment
        job["destination"] = info  # адрес ОТ FRAGMENT, куда уйдёт оплата
        job["stage"] = "await_confirm"
        job["ts"] = time.time()
        jobs[str(job["order_id"])] = job
        _save(jobs)
        # аудит: фиксируем, какой адрес вернул Fragment и сумму — для прозрачности
        ctx.log(f"AutoStars #{job['order_id']}: Fragment подготовил покупку — {amount} TON "
                f"на {info} для @{job['username']} ({job['quantity']} ⭐)")
        ctx.reply(chat, _fill(cfg["confirm_text"], username="@" + job["username"], quantity=job["quantity"]))
        return True

    if job["stage"] == "await_confirm":
        if low in ("да", "yes", "+", "да+", "подтверждаю", "ок", "ok"):
            dest = job.get("destination", "")
            amount = job.get("amount", 0)
            # ЗАЩИТА: адрес получателя обязан быть получен от Fragment в этой же сессии.
            # Если адреса нет или сумма нулевая — не отправляем (защита от потери денег).
            if not dest or not amount:
                ctx.log(f"AutoStars: ОТМЕНА заказа #{job['order_id']} — Fragment не вернул адрес/сумму, "
                        f"деньги НЕ отправлены (защита)", "error")
                ctx.reply(chat, cfg["cancel_text"])
                return True
            # Прозрачность: всегда пишем в журнал, СКОЛЬКО и КУДА уходит TON.
            # Адрес берётся только из ответа Fragment — так вы всегда видите, куда ушли деньги.
            ctx.log(f"AutoStars #{job['order_id']}: отправляю {amount} TON на адрес {dest} "
                    f"(адрес получен от Fragment) за {job['quantity']} ⭐ для @{job['username']}", "order")
            # отправляем TON
            tx, err = _send_ton(cfg, dest, amount, job.get("comment", ""))
            if err or not tx:
                ctx.log(f"AutoStars: не удалось отправить Stars по заказу #{job['order_id']}: {err}", "error")
                ctx.reply(chat, cfg["cancel_text"])
                ctx.log(f"AutoStars: ПРОВЕРЬТЕ заказ #{job['order_id']} — Stars не отправлены, решите с покупателем (возврат вручную)", "error")
                return True
            jobs.pop(str(job["order_id"]), None)
            _save(jobs)
            ctx.reply(chat, _fill(cfg["done_text"], username="@" + job["username"],
                                  quantity=job["quantity"], tx=f"https://tonviewer.com/transaction/{tx}"))
            ctx.log(f"AutoStars: выдано {job['quantity']} ⭐ на @{job['username']} (заказ #{job['order_id']})", "order")
            return True
        if low in ("нет", "no", "-", "другой", "сменить"):
            job["stage"] = "await_username"
            job["ts"] = time.time()
            jobs[str(job["order_id"])] = job
            _save(jobs)
            ctx.reply(chat, _fill(cfg["ask_username"], quantity=job["quantity"]))
            return True
        if low in ("!бэк", "!back", "отмена", "cancel"):
            jobs.pop(str(job["order_id"]), None)
            _save(jobs)
            ctx.reply(chat, "Заказ отменён. Обратитесь к продавцу, если нужна помощь.")
            return True
    return False


def tick(ctx):
    """Напоминания и снятие просроченных ожиданий."""
    cfg = ctx.config
    jobs = _jobs()
    now = time.time()
    changed = False
    for oid, job in list(jobs.items()):
        age = now - job.get("ts", now)
        if age > TIMEOUT:
            try:
                ctx.send_to_buyer(job["account_id"], job["buyer_id"], cfg["cancel_text"])
            except Exception:
                pass
            jobs.pop(oid, None)
            changed = True
            ctx.log(f"AutoStars: заказ #{job['order_id']} — покупатель не завершил, ожидание снято", "warn")
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
    """Проверка настроек: баланс кошелька и доступ к Fragment."""
    cfg = ctx.config
    out = []
    # баланс TON
    try:
        from tonutils.client import TonapiClient
        from tonutils.wallet import WalletV4R2
        import asyncio
        async def _bal():
            client = TonapiClient(api_key=cfg.get("ton_api_key") or "", is_testnet=False)
            wallet, *_ = WalletV4R2.from_mnemonic(client, (cfg.get("mnemonic") or "").split())
            return await wallet.balance()
        bal = asyncio.run(_bal())
        out.append(f"Кошелёк на связи, баланс ≈ {bal/1e9 if bal>1000 else bal} TON")
    except Exception as e:
        return {"ok": False, "message": f"Кошелёк/TON API: {e}"}
    # Fragment
    try:
        res = _frag_post(cfg, {"query": "durov", "quantity": 50, "method": "searchStarsRecipient"})
        if (res or {}).get("found"):
            out.append("Fragment отвечает, сессия активна")
        else:
            out.append("Fragment ответил, но получатель не найден — проверьте сессию")
    except Exception as e:
        return {"ok": False, "message": f"Fragment: {e} (обновите hash и Cookie)"}
    return {"ok": True, "message": " · ".join(out)}
