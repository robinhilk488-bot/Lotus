"""Dota 2 Rental — аренда Dota-аккаунтов с рангом/MMR в названии лота.

Это аренда Steam-аккаунтов (как RentSteam), заточенная под Dota 2:
- Выдаёт Steam-аккаунт после оплаты, считает время, закрывает по окончании.
- Steam Guard по команде !dota code, данные по !dota acc, время по !dota time.
- Продление через отдельный лот.
- Привязка лотов по Lot ID (лот = аккаунт/тип).
- Dota Info: бот заходит в аккаунт по maFile и читает ранг, MMR-оценку,
  behavior score, затем подставляет их в название лота по шаблону.

ВАЖНО: один Steam-логин и один Lot ID нельзя использовать одновременно
в «Аренда Steam» и в «Dota 2 Rental» — выберите один плагин для аккаунта.

Чтение ранга/MMR требует библиотек steam и dota2 (в requirements).
Точный MMR Valve скрывает, поэтому MMR — оценка по рангу (диапазон).
"""
import re
import time
import json

from core import db

# ===== Встроенный модуль чтения Dota-данных (ранг, MMR, behavior) =====
import time

# медали Dota по rank_tier (первая цифра — ранг, вторая — звёзды)
RANK_NAMES = {
    1: "Herald", 2: "Guardian", 3: "Crusader", 4: "Archon",
    5: "Legend", 6: "Ancient", 7: "Divine", 8: "Immortal",
}
# приблизительная оценка MMR по рангу (диапазоны, т.к. точный MMR Valve прячет)
RANK_MMR = {
    1: (0, 770), 2: (770, 1540), 3: (1540, 2310), 4: (2310, 3080),
    5: (3080, 3850), 6: (3850, 4620), 7: (4620, 5420), 8: (5620, 9000),
}


def _rank_label(rank_tier):
    """rank_tier 55 → 'Legend 5'. Возвращает (название, (mmr_min, mmr_max))."""
    if not rank_tier:
        return "Без ранга", (0, 0)
    tier = rank_tier // 10
    stars = rank_tier % 10
    name = RANK_NAMES.get(tier, "?")
    label = f"{name}{(' ' + str(stars)) if stars and tier < 8 else ''}"
    lo, hi = RANK_MMR.get(tier, (0, 0))
    # уточняем диапазон по звёздам внутри ранга
    if tier < 8 and stars:
        step = (hi - lo) / 5
        lo2 = int(lo + step * (stars - 1))
        hi2 = int(lo + step * stars)
        return label, (lo2, hi2)
    return label, (lo, hi)


def read_dota_profile(login, password, shared_secret, timeout=60):
    """Заходит в Steam и читает Dota-профиль аккаунта.
    Возвращает dict: {rank, mmr_min, mmr_max, behavior, steam_id} или {error}.
    """
    try:
        from steam.client import SteamClient
        from dota2.client import Dota2Client
    except ImportError:
        return {"error": "На сервере нет библиотек steam/dota2 — добавьте их в requirements"}

    result = {}
    client = SteamClient()
    dota = Dota2Client(client)

    @client.on("logged_on")
    def _on_login():
        dota.launch()

    @dota.on("ready")
    def _on_ready():
        try:
            # запрос профиля игрока в Dota
            dota.request_profile_card(client.steam_id.account_id)
        except Exception as e:
            result["error"] = f"Dota профиль недоступен: {e}"
            client.logout()

    @dota.on("profile_card")
    def _on_card(account_id, card):
        try:
            rank_tier = getattr(card, "rank_tier", 0)
            behavior = getattr(card, "behavior_score", None)
            label, (lo, hi) = _rank_label(rank_tier)
            result.update({
                "rank": label, "mmr_min": lo, "mmr_max": hi,
                "behavior": behavior, "steam_id": str(client.steam_id),
            })
        except Exception as e:
            result["error"] = f"не разобрал профиль: {e}"
        client.logout()

    try:
        # вход: если есть shared_secret — генерим Guard-код
        code = None
        if shared_secret:
            from steam.guard import generate_twofactor_code
            code = generate_twofactor_code(shared_secret)
        client.login(username=login, password=password, two_factor_code=code)
        # ждём результата до таймаута
        t0 = time.time()
        while not result and (time.time() - t0) < timeout:
            client.sleep(1)
        if not result:
            result["error"] = "Dota не ответила за отведённое время"
    except Exception as e:
        result["error"] = f"вход в Steam не удался: {e}"
    finally:
        try:
            client.logout()
        except Exception:
            pass
    return result


def format_title(template, rank="", mmr_min=0, mmr_max=0, behavior=""):
    """Подставляет Dota-данные в шаблон названия лота.
    Плейсхолдеры: {rank} {mmr} {mmr_exact} {mmr_range} {mmr_estimate_min} {mmr_estimate_max} {behavior_score} {lp_status}
    """
    mmr_range = f"{mmr_min}–{mmr_max}" if (mmr_min or mmr_max) else ""
    mmr_mid = (mmr_min + mmr_max) // 2 if (mmr_min or mmr_max) else ""
    repl = {
        "rank": rank or "",
        "mmr": mmr_range or (str(mmr_mid) if mmr_mid else ""),
        "mmr_exact": str(mmr_mid) if mmr_mid else "",
        "mmr_range": mmr_range,
        "mmr_estimate_min": str(mmr_min) if mmr_min else "",
        "mmr_estimate_max": str(mmr_max) if mmr_max else "",
        "behavior_score": str(behavior) if behavior else "",
        "lp_status": "",  # low priority — пока не читаем
    }
    out = template
    for k, v in repl.items():
        out = out.replace("{" + k + "}", v)
    return out

# ===== Конец модуля Dota-данных =====

NAME = "Dota 2 Rental"
DESCRIPTION = "Аренда Dota-аккаунтов: выдача по времени, Steam Guard по команде, ранг и MMR в названии лота."
CATEGORY = "Аренда аккаунтов"
VERSION = "1.0"

SETTINGS = [
    {"key": "issue_text", "label": "Текст выдачи", "type": "textarea",
     "default": "Аккаунт готов, {buyer}!\nЛогин: {login}\nПароль: {password}\nРанг: {rank}\nАренда до {until} ({hours} ч с момента оплаты).\n\n⚠️ Не меняйте пароль и не включайте Guard на телефон — аккаунт станет недоступен.\n\nКоманды:\n!dota code — код Steam Guard\n!dota acc — данные аккаунта снова\n!dota time — сколько осталось\n!продлить — добавить время"},
    {"key": "title_template", "label": "Шаблон названия (RU)", "type": "text",
     "default": "Аренда Dota 2 | {rank} | {mmr} MMR | {behavior_score} behavior",
     "hint": "Плейсхолдеры: {rank} {mmr} {mmr_range} {mmr_exact} {mmr_estimate_min} {mmr_estimate_max} {behavior_score}"},
    {"key": "title_template_en", "label": "Шаблон названия (EN)", "type": "text",
     "default": "Dota 2 rent | {rank} | {mmr} MMR | {behavior_score} behavior",
     "hint": "Английский заголовок — произвольный текст пишите на английском, плейсхолдеры те же."},
    {"key": "update_title", "label": "Обновлять название лота по Dota Info", "type": "bool", "default": True,
     "hint": "После чтения ранга бот сам обновит название лота аккаунта на FunPay."},
    {"key": "code_text", "label": "Ответ на !dota code", "type": "text",
     "default": "Код Steam Guard: {code} (действует ~30 секунд)"},
    {"key": "time_text", "label": "Ответ на !dota time", "type": "text",
     "default": "До конца аренды: {left}. Продлить: !продлить"},
    {"key": "no_rent_text", "label": "Если аренды нет", "type": "text",
     "default": "У вас нет активной аренды Dota-аккаунта."},
    {"key": "extend_offer_id", "label": "ID лота продления", "type": "text", "required": True,
     "hint": "Отдельный лот «Продление», держите выключенным. ID — число из ссылки offer?id=..."},
    {"key": "extend_prompt", "label": "Ответ на !продлить", "type": "textarea",
     "default": "Оплатите продление по ссылке в течение 10 минут: {link}\nОдна штука = 1 час."},
    {"key": "remind_text", "label": "Напоминание перед концом", "type": "text",
     "default": "До конца аренды 15 минут. Продлить: !продлить"},
    {"key": "end_text", "label": "Конец аренды", "type": "text",
     "default": "Аренда окончена, спасибо! Доступ к аккаунту закрыт."},
    {"key": "remind_before_min", "label": "Напоминать за, минут", "type": "number", "default": 15},
    {"key": "hide_lot_on_rent", "label": "Скрывать лот на время аренды", "type": "bool", "default": True},
]


# ---------- хранилище ----------
def _kv(key, default=None):
    r = db.query("SELECT value FROM plugin_kv WHERE plugin_id='dota_rent' AND key=?", (key,))
    return json.loads(r[0]["value"]) if r else (default if default is not None else {})


def _kv_set(key, value):
    db.execute("INSERT OR REPLACE INTO plugin_kv(plugin_id,key,value) VALUES('dota_rent',?,?)",
               (key, json.dumps(value, ensure_ascii=False)))


def accounts():
    """Аккаунты Dota-аренды с расшифрованными секретами."""
    from core.crypto import decrypt
    out = []
    for a in _kv("accounts", []):
        a = dict(a)
        for f in ("password", "shared_secret"):
            try:
                a[f] = decrypt(a[f]) if a.get(f) else ""
            except Exception:
                a[f] = ""
        out.append(a)
    return out


def rentals():
    return _kv("rentals", {})


def save_rentals(r):
    _kv_set("rentals", r)


def _fill(t, **kw):
    for k, v in kw.items():
        t = t.replace("{" + k + "}", str(v))
    return t


def _hm(ts):
    return time.strftime("%H:%M", time.localtime(ts))


def _left(ts):
    sec = max(0, int(ts - time.time()))
    h, m = sec // 3600, (sec % 3600) // 60
    return (f"{h} ч {m} мин" if h else f"{m} мин")


def order_quantity(order, ctx):
    try:
        return ctx.order_quantity(order) or 1
    except Exception:
        return 1


# ---------- привязка лота к аккаунту (как в rent_steam) ----------
def _account_for_order(order, ctx):
    accs = accounts()
    bound = [a for a in accs if (a.get("offer_id") or "").strip()]
    if not bound:
        return None
    desc = (order.get("description") or "").strip().lower()
    for a in bound:
        lot = (a.get("title") or "").strip().lower()
        if lot and (lot in desc or desc in lot):
            return a
    return ctx.SKIP


def _free_account():
    busy = {r["steam_login"] for r in rentals().values() if r["status"] in ("active", "changing")}
    for a in accounts():
        if a.get("enabled", True) and a["login"] not in busy and a.get("state", "free") == "free":
            return a
    return None


# ---------- выдача ----------
def on_new_order(order, ctx):
    cfg = ctx.config
    matched = _account_for_order(order, ctx)
    if matched is ctx.SKIP:
        return ctx.SKIP
    hours = order_quantity(order, ctx)
    acc = matched or _free_account()
    if not acc:
        raise Exception("Нет свободных Dota-аккаунтов. Добавьте аккаунт и нажмите «Повторить».")

    until = time.time() + hours * 3600
    rentals_d = rentals()
    rentals_d[str(order["id"])] = {
        "order_id": order["id"], "account_id": order["account_id"],
        "buyer": order["buyer"], "buyer_id": order.get("buyer_id"),
        "steam_login": acc["login"], "until": until, "hours": hours,
        "status": "active", "started": time.time(), "reminded": False,
        "chat_id": None, "offer_id": acc.get("offer_id", ""),
    }
    save_rentals(rentals_d)
    _mark_account(acc["login"], "rented")

    if acc.get("offer_id") and cfg.get("hide_lot_on_rent"):
        try:
            ctx.set_lot_active(order["account_id"], acc["offer_id"], False)
        except Exception:
            pass

    text = _fill(cfg["issue_text"], buyer=order["buyer"], login=acc["login"],
                 password=acc["password"], rank=acc.get("rank", "—"),
                 until=_hm(until), hours=hours)
    if order.get("buyer_id"):
        ctx.send_to_buyer(order["account_id"], order["buyer_id"], text)
    ctx.log(f"Dota аренда #{order['id']}: выдан {acc['login']} на {hours} ч (до {_hm(until)})", "order")
    return f"Аренда до {_hm(until)}"


# ---------- команды покупателя ----------
def _rent_for_chat(chat):
    for oid, r in rentals().items():
        if r["status"] == "active" and (r["buyer"] or "").lower() == (chat["name"] or "").lower() \
           and r["account_id"] == chat["account_id"]:
            return oid, r
    return None, None


def on_message(msg, chat, ctx):
    text = (msg.get("text") or "").strip().lower()
    if not text.startswith("!dota") and text != "!продлить":
        return False
    cfg = ctx.config
    oid, rent = _rent_for_chat(chat)

    if text == "!продлить":
        if not rent:
            ctx.reply(chat, cfg["no_rent_text"])
            return True
        link = f"https://funpay.com/lots/offer?id={cfg.get('extend_offer_id','')}"
        pend = _kv("pending_extend", {})
        pend[f"{rent['account_id']}:{(rent['buyer'] or '').lower()}"] = {"order_id": oid, "ts": time.time()}
        _kv_set("pending_extend", pend)
        ctx.reply(chat, _fill(cfg["extend_prompt"], link=link))
        return True

    if not rent:
        ctx.reply(chat, cfg["no_rent_text"])
        return True
    rentals_d = rentals(); rentals_d[oid]["chat_id"] = chat["id"]; save_rentals(rentals_d)
    acc = next((a for a in accounts() if a["login"] == rent["steam_login"]), None)

    if "code" in text or "код" in text:
        if not acc or not acc.get("shared_secret"):
            ctx.reply(chat, "Для этого аккаунта код Guard недоступен (нет maFile).")
            return True
        try:
            from core.steam import guard_code
            ctx.reply(chat, _fill(cfg["code_text"], code=guard_code(acc["shared_secret"])))
        except Exception:
            ctx.reply(chat, "Не удалось сгенерировать код, попробуйте ещё раз.")
        return True

    if "acc" in text or "данн" in text:
        ctx.reply(chat, _fill(cfg["issue_text"], buyer=rent["buyer"], login=acc["login"] if acc else "?",
                              password=acc["password"] if acc else "?", rank=acc.get("rank", "—") if acc else "—",
                              until=_hm(rent["until"]), hours=rent["hours"]))
        return True

    if "time" in text or "врем" in text:
        ctx.reply(chat, _fill(cfg["time_text"], left=_left(rent["until"])))
        return True
    return False


# ---------- Dota Info: чтение ранга и обновление названия ----------
def dota_info(login, ctx):
    """Читает Dota-данные аккаунта по maFile и сохраняет их. Возвращает dict."""
    acc = next((a for a in accounts() if a["login"] == login), None)
    if not acc:
        return {"error": "Аккаунт не найден"}
    data = read_dota_profile(acc["login"], acc.get("password", ""), acc.get("shared_secret", ""))
    if "error" in data:
        return data
    # сохраняем в аккаунт
    raw = _kv("accounts", [])
    for a in raw:
        if a["login"] == login:
            a["rank"] = data.get("rank", "")
            a["mmr_min"] = data.get("mmr_min", 0)
            a["mmr_max"] = data.get("mmr_max", 0)
            a["behavior"] = data.get("behavior", "")
    _kv_set("accounts", raw)
    # обновляем название лота, если включено
    cfg = ctx.config
    if cfg.get("update_title") and acc.get("offer_id"):
        title_ru = format_title(cfg.get("title_template", ""), data.get("rank", ""),
                                         data.get("mmr_min", 0), data.get("mmr_max", 0), data.get("behavior", ""))
        try:
            # account_id первого FunPay-аккаунта (у аренды обычно один)
            fp = db.query("SELECT id FROM accounts WHERE status='ok' LIMIT 1")
            if fp:
                ctx.save_lot_description(fp[0]["id"], acc["offer_id"], title_ru)  # название в описание
                ctx.log(f"Dota Info: {login} → {data.get('rank')}, обновил название лота {acc['offer_id']}", "order")
        except Exception as e:
            ctx.log(f"Dota Info: не удалось обновить название: {e}", "warn")
    return data


# ---------- фоновое завершение аренд ----------
def on_tick(ctx):
    cfg = ctx.config
    now = time.time()
    rentals_d = rentals()
    changed = False
    remind_before = int(cfg.get("remind_before_min") or 15) * 60
    for oid, r in list(rentals_d.items()):
        if r["status"] != "active":
            continue
        if now >= r["until"]:
            if r.get("chat_id"):
                try:
                    ctx.reply({"id": r["chat_id"], "account_id": r["account_id"]}, cfg["end_text"])
                except Exception:
                    pass
            r["status"] = "done"
            _mark_account(r["steam_login"], "free")
            if r.get("offer_id") and cfg.get("hide_lot_on_rent"):
                try:
                    ctx.set_lot_active(r["account_id"], r["offer_id"], True)
                except Exception:
                    pass
            ctx.log(f"Dota аренда #{oid}: окончена, {r['steam_login']} свободен", "order")
            changed = True
        elif not r.get("reminded") and now >= r["until"] - remind_before:
            if r.get("chat_id"):
                try:
                    ctx.reply({"id": r["chat_id"], "account_id": r["account_id"]}, cfg["remind_text"])
                except Exception:
                    pass
            r["reminded"] = True
            changed = True
    if changed:
        save_rentals(rentals_d)


def _mark_account(login, state):
    raw = _kv("accounts", [])
    for a in raw:
        if a["login"] == login:
            a["state"] = state
    _kv_set("accounts", raw)


def test(ctx):
    accs = accounts()
    if not accs:
        return {"ok": False, "message": "Добавьте хотя бы один Dota-аккаунт"}
    free = sum(1 for a in accs if a.get("state", "free") == "free")
    return {"ok": True, "message": f"Аккаунтов: {len(accs)}, свободно: {free}. Для чтения ранга нажмите Dota Info у аккаунта."}
