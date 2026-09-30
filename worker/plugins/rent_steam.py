"""RentSteam — аренда Steam-аккаунтов.

Аренда идёт от момента оплаты: 1 купленная штука = 1 час.
Команды покупателя: !code — код Steam Guard, !time — сколько осталось, !продлить — купить продление.

Продление: покупатель пишет !продлить → плагин включает заранее заготовленный лот продления и
даёт ссылку. Оплатил в течение 10 минут → часы добавляются. Не оплатил → лот выключается.
Если аренда истекла, продление не выдаётся (кроме оплаты, сделанной ещё до окончания).

Отзыв: 5 звёзд во время аренды → +1 час, один раз за всю аренду (продления входят в ту же аренду),
повторные и изменённые отзывы бонус не дают.

Когда время вышло: покупателю уходит сообщение, плагин меняет пароль аккаунта и проверяет,
что новый пароль работает. Пока пароль не сменён (или сброс не подтверждён вручную),
аккаунт НЕ выдаётся следующему покупателю.

Все аккаунты, пароли и maFile настраиваются в разделе «Аренда Steam» приложения и хранятся
на сервере в зашифрованном виде.
"""
import json
import time

from core import db
from core.crypto import decrypt, encrypt
from core.notify import notify
from core.steam import SteamError, change_password, guard_code, new_password

NAME = "Аренда Steam"
DESCRIPTION = "Сдаёт Steam-аккаунты в аренду: выдача, коды Guard, продление отдельным лотом, смена пароля после аренды."
CATEGORY = "Аренда аккаунтов"
VERSION = "1.0"
TIMEOUT = 120

SETTINGS = [
    {"key": "issue_text", "label": "Текст выдачи", "type": "textarea",
     "default": "Аккаунт готов, {buyer}!\nЛогин: {login}\nПароль: {password}\nАренда до {until} ({hours} ч с момента оплаты).\n\nКоманды в этом чате:\n!code — код Steam Guard для входа\n!time — сколько осталось\n!продлить — добавить время"},
    {"key": "code_text", "label": "Ответ на !code", "type": "text", "default": "Код Steam Guard: {code} (действует ~30 секунд)"},
    {"key": "extend_prompt", "label": "Ответ на !продлить", "type": "textarea",
     "default": "Оплатите продление по ссылке в течение 10 минут: {link}\nОдна штука = 1 час, время добавится к текущему."},
    {"key": "extend_paid_text", "label": "После оплаты продления", "type": "text", "default": "Готово! Аренда продлена до {until}."},
    {"key": "extend_expired_text", "label": "Если не оплатил за 10 минут", "type": "text", "default": "Время на оплату продления вышло, ссылка больше не активна. Напишите !продлить снова, если нужно."},
    {"key": "no_rent_text", "label": "Если аренды нет", "type": "text", "default": "У вас нет активной аренды."},
    {"key": "remind_text", "label": "Напоминание перед концом", "type": "text", "default": "До конца аренды 15 минут. Продлить: !продлить"},
    {"key": "end_text", "label": "Конец аренды", "type": "text", "default": "Аренда окончена, спасибо! Доступ к аккаунту закрыт."},
    {"key": "review_bonus_text", "label": "Спасибо за отзыв", "type": "text", "default": "Спасибо за отзыв! +1 час, аренда до {until}."},
    {"key": "extend_offer_id", "label": "ID лота продления на FunPay", "type": "text", "required": True,
     "hint": "Создайте отдельный лот «Продление аренды», держите его выключенным. ID — число из ссылки offer?id=... на лот."},
    {"key": "extend_command", "label": "Команда продления", "type": "text", "default": "!продлить"},
    {"key": "remind_before_min", "label": "Напоминать за, минут", "type": "number", "default": 15},
]


# ---------------- служебное хранилище ----------------
def _kv(key, default=None):
    r = db.query("SELECT value FROM plugin_kv WHERE plugin_id='rent_steam' AND key=?", (key,))
    return json.loads(r[0]["value"]) if r else default


def _kv_set(key, value):
    db.execute("INSERT OR REPLACE INTO plugin_kv(plugin_id, key, value) VALUES('rent_steam', ?, ?)", (key, json.dumps(value)))


def rentals():
    return _kv("rentals", {})  # order_id -> аренда


def save_rentals(r):
    _kv_set("rentals", r)


def steam_accounts():
    """Список аккаунтов с расшифрованными паролями — только внутри плагина и API аренды."""
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


def _fill(t, **kw):
    for k, v in kw.items():
        t = t.replace("{" + k + "}", str(v))
    return t


def _hm(ts):
    return time.strftime("%H:%M", time.localtime(ts))


def _active_rental_for(buyer, account_id):
    for oid, r in rentals().items():
        if r["status"] == "active" and r["buyer"].lower() == (buyer or "").lower() and r["account_id"] == account_id:
            return oid, r
    return None, None


def _free_account():
    busy = {r["steam_login"] for r in rentals().values() if r["status"] in ("active", "changing")}
    for a in steam_accounts():
        if a.get("enabled", True) and a["login"] not in busy and a.get("state", "free") == "free":
            return a
    return None


# ---------------- выдача аренды ----------------
def on_new_order(order, ctx):
    cfg = ctx.config
    # это оплата продления?
    pend = _kv("pending_extend", {})
    key = f"{order['account_id']}:{order['buyer'].lower()}"
    if key in pend:
        return _apply_extension(order, ctx, pend, key)

    hours = ctx.order_quantity(order)
    if not hours:
        raise Exception("Не удалось прочитать количество часов из заказа — аренда не выдана")

    acc = None if ctx.dry_run else _free_account()
    if ctx.dry_run:
        ctx.log(f"Пробный запуск: выдал бы свободный аккаунт на {hours} ч")
        return "dry-run"
    if not acc:
        notify("stock", f"🎮 Нет свободных Steam-аккаунтов для заказа #{order['id']}. Аренда не выдана.")
        raise Exception("Нет свободных аккаунтов. Добавьте аккаунт и нажмите «Повторить».")

    until = time.time() + hours * 3600
    text = _fill(cfg["issue_text"], buyer=order["buyer"], login=acc["login"], password=acc["password"],
                 until=_hm(until), hours=hours)
    try:
        ctx.send_message(order, text)
    except Exception as e:
        raise ctx.Retry(f"не удалось отправить данные покупателю: {e}", delay=30)

    r = rentals()
    r[order["id"]] = {
        "status": "active", "buyer": order["buyer"], "buyer_id": order.get("buyer_id"),
        "account_id": order["account_id"], "chat_id": None, "steam_login": acc["login"],
        "until": until, "hours": hours, "reminded": False, "review_bonus": False, "started": time.time(),
    }
    save_rentals(r)
    db.log(f"Аренда #{order['id']}: выдан {acc['login']} на {hours} ч (до {_hm(until)})", "order")
    return f"Аренда до {_hm(until)}"


def _apply_extension(order, ctx, pend, key):
    info = pend.pop(key)
    _kv_set("pending_extend", pend)
    hours = ctx.order_quantity(order) or 1
    r = rentals()
    rent = r.get(info["order_id"])
    if not rent or rent["status"] not in ("active",):
        raise Exception("Продление оплачено, но исходная аренда уже завершена — решите вручную")
    rent["until"] += hours * 3600
    rent["reminded"] = False
    save_rentals(r)
    try:
        ctx.set_lot_active(order["account_id"], ctx.config["extend_offer_id"], False)
    except Exception as e:
        notify("attention", f"🎮 Продление #{order['id']}: не удалось выключить лот продления ({e}). Выключите вручную.")
    ctx.reply({"id": _chat(rent), "account_id": order["account_id"]}, _fill(ctx.config["extend_paid_text"], until=_hm(rent["until"])))
    db.log(f"Аренда #{info['order_id']}: продлена на {hours} ч (до {_hm(rent['until'])})", "order")
    return f"Продлено до {_hm(rent['until'])}"


def _chat(rent):
    return rent.get("chat_id")


# ---------------- команды покупателя ----------------
def on_message(msg, chat, ctx):
    text = (msg.get("text") or "").strip()
    low = text.lower()
    cfg = ctx.config
    # запомним chat_id для активной аренды этого покупателя
    oid, rent = _active_rental_for(chat["name"], chat["account_id"])
    if rent and rent.get("chat_id") != chat["id"]:
        r = rentals(); r[oid]["chat_id"] = chat["id"]; save_rentals(r); rent = r[oid]

    if low in ("!code", "!код"):
        if not rent:
            ctx.reply(chat, cfg["no_rent_text"]); return True
        acc = next((a for a in steam_accounts() if a["login"] == rent["steam_login"]), None)
        if not acc or not acc.get("shared_secret"):
            ctx.reply(chat, "Для этого аккаунта не загружен maFile — код недоступен, напишите продавцу."); return True
        try:
            code = guard_code(acc["shared_secret"])
        except SteamError:
            ctx.reply(chat, "Не удалось получить код — напишите продавцу."); return True
        ctx.reply(chat, _fill(cfg["code_text"], code=code)); return True

    if low in ("!time", "!время"):
        if not rent:
            ctx.reply(chat, cfg["no_rent_text"]); return True
        left = int(rent["until"] - time.time())
        ctx.reply(chat, f"Осталось {left // 3600} ч {left % 3600 // 60} мин (до {_hm(rent['until'])})." if left > 0 else "Аренда завершена."); return True

    if low.startswith(cfg["extend_command"].strip().lower()):
        if not rent or rent["until"] <= time.time():
            ctx.reply(chat, cfg["no_rent_text"]); return True
        try:
            ctx.set_lot_active(chat["account_id"], cfg["extend_offer_id"], True)
        except Exception as e:
            ctx.reply(chat, "Не получилось открыть продление, попробуйте через минуту.")
            notify("attention", f"🎮 !продлить от {chat['name']}: не удалось включить лот продления ({e}).")
            return True
        from core.funpay import FunPayAccount
        link = FunPayAccount.offer_link(cfg["extend_offer_id"])
        pend = _kv("pending_extend", {})
        pend[f"{chat['account_id']}:{chat['name'].lower()}"] = {"order_id": oid, "deadline": time.time() + 600, "chat_id": chat["id"]}
        _kv_set("pending_extend", pend)
        ctx.reply(chat, _fill(cfg["extend_prompt"], link=link))
        db.log(f"Аренда #{oid}: {chat['name']} запросил продление, лот открыт на 10 мин", "order")
        return True
    return False


# ---------------- отзыв (5★ во время аренды, один раз) ----------------
def on_review(review, ctx):
    if review["rating"] != 5:
        return
    r = rentals()
    rent = r.get(review["order_id"])
    # отзыв может быть к продлению — ищем исходную аренду того же покупателя
    if not rent:
        oid, rent = _active_rental_for(review["buyer"], review["account_id"])
    else:
        oid = review["order_id"]
    if not rent or rent["status"] != "active" or rent["until"] <= time.time():
        return
    if rent.get("review_bonus"):
        return  # бонус за эту аренду уже был
    rent["review_bonus"] = True
    rent["until"] += 3600
    rent["reminded"] = False
    r[oid] = rent
    save_rentals(r)
    if rent.get("chat_id"):
        ctx.reply({"id": rent["chat_id"], "account_id": rent["account_id"]},
                  _fill(ctx.config["review_bonus_text"], until=_hm(rent["until"])))
    db.log(f"Аренда #{oid}: +1 час за отзыв (до {_hm(rent['until'])})", "order")


# ---------------- таймер: напоминания, окончание, чистка продлений ----------------
def on_tick(ctx):
    now = time.time()
    cfg = ctx.config
    r = rentals()
    changed = False

    # просроченные окна оплаты продления — выключаем лот
    pend = _kv("pending_extend", {})
    expired = [k for k, v in pend.items() if v["deadline"] <= now]
    for k in expired:
        info = pend.pop(k)
        acc_id = int(k.split(":")[0])
        try:
            ctx.set_lot_active(acc_id, cfg["extend_offer_id"], False)
        except Exception as e:
            notify("attention", f"🎮 Просрочено продление: не удалось выключить лот ({e}). Выключите вручную.")
        rent = r.get(info["order_id"])
        if rent and rent.get("chat_id"):
            try:
                ctx.reply({"id": rent["chat_id"], "account_id": acc_id}, cfg["extend_expired_text"])
            except Exception:
                pass
        db.log(f"Продление по аренде #{info['order_id']}: не оплачено за 10 мин, лот выключен")
    if expired:
        _kv_set("pending_extend", pend)

    for oid, rent in list(r.items()):
        if rent["status"] != "active":
            continue
        # напоминание
        remind_at = rent["until"] - cfg["remind_before_min"] * 60
        if not rent["reminded"] and remind_at <= now < rent["until"] and rent.get("buyer_id"):
            try:
                ctx.send_to_buyer(rent["account_id"], rent["buyer_id"], cfg["remind_text"])
                rent["reminded"] = True; changed = True
            except Exception:
                pass
        # окончание
        if rent["until"] <= now:
            if rent.get("buyer_id"):
                try:
                    ctx.send_to_buyer(rent["account_id"], rent["buyer_id"], cfg["end_text"])
                except Exception:
                    pass
            rent["status"] = "changing"; changed = True
            db.log(f"Аренда #{oid}: время вышло, меняю пароль {rent['steam_login']}")
    if changed:
        save_rentals(r)
    # смену пароля запускаем после сохранения, чтобы её итог не был перезаписан
    for oid, rent in list(rentals().items()):
        if rent["status"] == "changing":
            _reset_account(oid, rent, ctx)


def _reset_account(oid, rent, ctx):
    """Смена пароля после аренды. Аккаунт освобождается только при успехе."""
    acc = next((a for a in steam_accounts() if a["login"] == rent["steam_login"]), None)
    if not acc:
        return
    if not acc.get("shared_secret"):  # без maFile сменить пароль нельзя — просим сбросить вручную
        _mark_account(acc["login"], "needs_reset")
        _set_status(oid, "needs_reset")
        notify("attention", f"🎮 Аренда #{oid} окончена. У аккаунта {acc['login']} нет maFile — "
                            f"выйдите на всех устройствах и при желании смените пароль, затем нажмите «Сброс выполнен».")
        return
    old = acc["password"]
    new = new_password()
    try:
        change_password(acc["login"], old, new, acc["shared_secret"])
    except SteamError as e:
        _mark_account(acc["login"], "needs_reset")
        _set_status(oid, "needs_reset")
        notify("attention", f"🔴 Аккаунт {acc['login']}: смена пароля не удалась ({e}). "
                            f"Смените вручную и нажмите «Сброс выполнен». Аккаунт не выдаётся, пока не проверите.")
        db.log(f"Аренда #{oid}: смена пароля {acc['login']} не удалась — {e}", "error")
        return
    _update_password(acc["login"], new)
    _mark_account(acc["login"], "free")
    _set_status(oid, "done")
    db.log(f"Аренда #{oid}: пароль {acc['login']} сменён, аккаунт свободен")


# ---------------- операции с аккаунтами (шифрование) ----------------
def _raw_accounts():
    return _kv("accounts", [])


def _update_password(login, new_plain):
    accs = _raw_accounts()
    for a in accs:
        if a["login"] == login:
            a["password"] = encrypt(new_plain)
    _kv_set("accounts", accs)


def _mark_account(login, state):
    accs = _raw_accounts()
    for a in accs:
        if a["login"] == login:
            a["state"] = state
    _kv_set("accounts", accs)


def _set_status(oid, status):
    r = rentals()
    if oid in r:
        r[oid]["status"] = status
        save_rentals(r)
