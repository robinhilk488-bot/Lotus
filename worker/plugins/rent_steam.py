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
import re
import time

from core import accounts, db
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
     "default": "Аккаунт готов, {buyer}!\nЛогин: {login}\nПароль: {password}\nАренда до {until} ({hours} ч с момента оплаты).\n\n⚠️ Не меняйте пароль и не включайте Steam Guard на телефон — аккаунт станет недоступен, аренда не вернётся.\n\nКоманды в этом чате:\n!code — код Steam Guard для входа\n!time — сколько осталось\n!продлить — добавить время"},
    {"key": "type_map", "label": "Типы аккаунтов по лотам", "type": "textarea", "default": "",
     "hint": "Если сдаёте разные игры: по строке «фраза из названия лота = тип». Пример: Аренда CS2 = cs2. Тип должен совпадать с типом аккаунта. Пусто — все аккаунты в общей куче."},
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
    {"key": "review_bonus_min_hours", "label": "Час за отзыв только если куплено от, часов", "type": "number", "default": 2,
     "hint": "Чтобы не дарить час за отзыв тем, кто взял аренду всего на час. 0 — давать всегда."},
    {"key": "hide_lot_on_rent", "label": "Скрывать лот аккаунта на время аренды", "type": "bool", "default": True,
     "hint": "Если указан ID лота у аккаунта, он прячется на время аренды, чтобы не купили занятый. Выключите, если лот один на несколько аккаунтов."},
    {"key": "onlypc_check", "label": "Проверка OnlyPC (фото из клуба)", "type": "bool", "default": False,
     "hint": "Если включено, после оплаты бот просит фото из компьютерного клуба и ждёт вашего решения. Постоянников добавьте в белый список — им выдаётся сразу."},
    {"key": "onlypc_ask", "label": "Запрос фото", "type": "textarea",
     "default": "Спасибо за заказ! Пришлите, пожалуйста, фото, что вы находитесь в компьютерном клубе. После проверки выдам аккаунт."},
    {"key": "onlypc_wait", "label": "Ответ после получения фото", "type": "text",
     "default": "Фото получено, проверяю. Аккаунт выдам в течение нескольких минут."},
    {"key": "onlypc_whitelist", "label": "Белый список (ссылки на профили FunPay)", "type": "textarea", "default": "",
     "hint": "По одной ссылке на строку, например https://funpay.com/users/123456/. Этим покупателям аккаунт выдаётся сразу, без фото."},
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


def _type_for_order(order, cfg):
    """По названию лота определяет нужный тип аккаунта. None — тип не задан (общая куча)."""
    desc = (order.get("description") or "").lower()
    best = None
    for line in (cfg.get("type_map") or "").splitlines():
        if "=" not in line:
            continue
        phrase, typ = (x.strip() for x in line.split("=", 1))
        if phrase and typ and phrase.lower() in desc and (not best or len(phrase) > best[0]):
            best = (len(phrase), typ.lower())
    return best[1] if best else None


def _free_account(acc_type=None):
    busy = {r["steam_login"] for r in rentals().values() if r["status"] in ("active", "changing")}
    for a in steam_accounts():
        if not a.get("enabled", True) or a["login"] in busy or a.get("state", "free") != "free":
            continue
        if acc_type and (a.get("type", "") or "").lower() != acc_type:
            continue
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

    acc_type = _type_for_order(order, cfg)
    if acc_type and not ctx.dry_run and not _free_account(acc_type):
        raise Exception(f"Нет свободных аккаунтов типа «{acc_type}». Добавьте аккаунт этого типа и нажмите «Повторить».")

    if ctx.dry_run:
        ctx.log(f"Пробный запуск: выдал бы свободный аккаунт{(' типа ' + acc_type) if acc_type else ''} на {hours} ч"
                + (" (с проверкой OnlyPC)" if cfg.get("onlypc_check") and not _in_whitelist(order.get("buyer_id"), cfg) else ""))
        return "dry-run"

    # OnlyPC: если включено и покупатель НЕ в белом списке — не выдаём, просим фото
    if cfg.get("onlypc_check") and not _in_whitelist(order.get("buyer_id"), cfg):
        p = _kv("pending_photo", {})
        p[order["id"]] = {"buyer": order["buyer"], "buyer_id": order.get("buyer_id"),
                          "account_id": order["account_id"], "hours": hours, "acc_type": acc_type,
                          "chat_id": None, "created": time.time(), "reminded": False, "got_photo": False}
        _kv_set("pending_photo", p)
        try:
            ctx.send_message(order, cfg["onlypc_ask"])
        except Exception as e:
            raise ctx.Retry(f"не удалось запросить фото: {e}", delay=30)
        db.log(f"Аренда #{order['id']}: запрошено фото OnlyPC у {order['buyer']}", "order")
        return "Ожидает фото OnlyPC"

    return _issue_account(order["id"], order["buyer"], order.get("buyer_id"),
                          order["account_id"], hours, ctx, acc_type)


def _in_whitelist(buyer_id, cfg):
    if not buyer_id:
        return False
    ids = set()
    for line in (cfg.get("onlypc_whitelist") or "").splitlines():
        m = re.search(r"/users/(\d+)", line) or re.search(r"\b(\d{3,})\b", line)
        if m:
            ids.add(m.group(1))
    return str(buyer_id) in ids


def _issue_account(order_id, buyer, buyer_id, account_id, hours, ctx, acc_type=None):
    acc = _free_account(acc_type)
    if not acc:
        t = f" типа «{acc_type}»" if acc_type else ""
        notify("stock", f"🎮 Нет свободных Steam-аккаунтов{t} для заказа #{order_id}. Аренда не выдана.")
        raise Exception(f"Нет свободных аккаунтов{t}. Добавьте аккаунт и нажмите «Повторить».")
    until = time.time() + hours * 3600
    text = _fill(ctx.config["issue_text"], buyer=buyer, login=acc["login"], password=acc["password"],
                 until=_hm(until), hours=hours)
    with accounts.use(account_id) as fp:
        fp.send_message(fp.chat_with(buyer_id), text) if buyer_id else None
    r = rentals()
    r[order_id] = {
        "status": "active", "buyer": buyer, "buyer_id": buyer_id,
        "account_id": account_id, "chat_id": None, "steam_login": acc["login"],
        "until": until, "hours": hours, "reminded": False, "review_bonus": False, "started": time.time(),
        "offer_id": acc.get("offer_id", ""),
    }
    save_rentals(r)
    if ctx.config.get("hide_lot_on_rent", True) and acc.get("offer_id"):
        try:
            ctx.set_lot_active(account_id, acc["offer_id"], False)
        except Exception as e:
            ctx.log(f"не удалось скрыть лот {acc['offer_id']}: {e}", "warn")
    db.log(f"Аренда #{order_id}: выдан {acc['login']} на {hours} ч (до {_hm(until)})", "order")
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

    # OnlyPC: покупатель, у которого запрошено фото, прислал сообщение/картинку
    photos = _kv("pending_photo", {})
    for po_id, job in list(photos.items()):
        if job["account_id"] == chat["account_id"] and (job["buyer"] or "").lower() == (chat["name"] or "").lower():
            if job.get("got_photo"):
                return True  # уже ждём решения владельца — молчим
            job["got_photo"] = True
            job["chat_id"] = chat["id"]
            photos[po_id] = job
            _kv_set("pending_photo", photos)
            kind = "фото" if "[изображение]" in text else "сообщение"
            notify("attention", f"🖼 Нужна проверка OnlyPC: заказ #{po_id}\nПокупатель: {chat['name']}\nПрислал {kind}: {text[:80]}\nОткройте Lotus → Аренда Steam → Проверка OnlyPC.")
            db.log(f"Аренда #{po_id}: {chat['name']} прислал {kind} для проверки OnlyPC — нужна ваша проверка", "order")
            ctx.reply(chat, cfg["onlypc_wait"])
            return True

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
    min_hours = float(ctx.config.get("review_bonus_min_hours") or 0)
    if min_hours and rent.get("hours", 0) < min_hours:
        return  # куплено слишком мало часов — бонус не положен
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

    # OnlyPC: ждём фото — напоминание через 30 мин, прекращение через 60
    photos = _kv("pending_photo", {})
    pchanged = False
    for po_id, job in list(photos.items()):
        if job.get("got_photo"):
            continue  # фото пришло, ждём решения владельца — не торопим
        age = now - job["created"]
        if not job["reminded"] and age > 30 * 60 and job.get("buyer_id"):
            try:
                ctx.send_to_buyer(job["account_id"], job["buyer_id"], cfg["onlypc_ask"])
                job["reminded"] = True; pchanged = True
            except Exception:
                pass
        elif job["reminded"] and age > 60 * 60:
            if job.get("buyer_id"):
                try:
                    ctx.send_to_buyer(job["account_id"], job["buyer_id"],
                                      "Фото не получено, заказ не может быть выполнен. Обратитесь к продавцу.")
                except Exception:
                    pass
            notify("attention", f"⏳ OnlyPC: по заказу #{po_id} покупатель {job['buyer']} не прислал фото. Ожидание прекращено, решите вручную.")
            db.log(f"Аренда #{po_id}: фото OnlyPC не получено за час — ожидание прекращено", "warn")
            del photos[po_id]; pchanged = True
    if pchanged:
        _kv_set("pending_photo", photos)

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
    _show_lot_back(oid, rent, ctx)
    db.log(f"Аренда #{oid}: пароль {acc['login']} сменён, аккаунт свободен")


def onlypc_pending():
    """Заказы, ожидающие вашего решения по фото OnlyPC."""
    out = []
    for po_id, job in _kv("pending_photo", {}).items():
        out.append({"order_id": po_id, "buyer": job["buyer"], "account_id": job["account_id"],
                    "got_photo": job.get("got_photo", False), "created": job["created"]})
    return out


def onlypc_decide(order_id, approve, ctx):
    photos = _kv("pending_photo", {})
    job = photos.get(order_id)
    if not job:
        raise ValueError("Заказ не найден среди ожидающих проверки")
    del photos[order_id]
    _kv_set("pending_photo", photos)
    if approve:
        return _issue_account(order_id, job["buyer"], job.get("buyer_id"),
                              job["account_id"], job["hours"], ctx, job.get("acc_type"))
    if job.get("buyer_id"):
        try:
            with accounts.use(job["account_id"]) as fp:
                fp.send_message(fp.chat_with(job["buyer_id"]),
                                "К сожалению, проверка не пройдена, аккаунт не выдан. Обратитесь к продавцу.")
        except Exception:
            pass
    db.log(f"Аренда #{order_id}: проверка OnlyPC отклонена владельцем", "warn")
    return "Отклонено"


def _show_lot_back(oid, rent, ctx):
    if not ctx.config.get("hide_lot_on_rent", True):
        return
    offer = rent.get("offer_id")
    if offer:
        try:
            ctx.set_lot_active(rent["account_id"], offer, True)
        except Exception as e:
            ctx.log(f"не удалось вернуть лот {offer}: {e}", "warn")


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
