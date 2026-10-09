"""API воркера. Приложение на ПК общается только с ним."""
import hmac
import os
import time
from datetime import datetime, timedelta

from flask import Flask, jsonify, request

from core import accounts, backup, chat, db, notify, plugins, raiser, subscription, sync
from core.crypto import encrypt
from core.funpay import FunPayAccount, FunPayError

VERSION = "0.1.0"
TOKEN = os.environ.get("KASSA_TOKEN", "")
if len(TOKEN) < 16:
    raise SystemExit("Задайте KASSA_TOKEN (минимум 16 символов)")

STARTED = time.time()
app = Flask(__name__)
app.json.ensure_ascii = False


@app.before_request
def auth():
    given = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(given, TOKEN):
        return jsonify(error="Неверный токен подключения"), 401


def err(msg, code=400):
    return jsonify(error=msg), code


# ---------- статус ----------
@app.get("/api/status")
def status():
    accs = db.query("SELECT status FROM accounts")
    return jsonify(version=VERSION, uptime=int(time.time() - STARTED),
                   accounts=len(accs), accounts_ok=sum(a["status"] == "ok" for a in accs),
                   plugins_enabled=sum(p["enabled"] for p in plugins.describe() if p["ready"]),
                   attention=db.query("SELECT COUNT(*) c FROM plugin_tasks WHERE status='attention'")[0]["c"],
                   unread=db.query("SELECT COUNT(*) c FROM chats WHERE unread=1")[0]["c"],
                   subscription=subscription.status())


# ---------- аккаунты ----------
def public_account(a):
    a = dict(a)
    a.pop("key_enc", None)
    a["orders_total"] = db.query("SELECT COUNT(*) c FROM orders WHERE account_id=?", (a["id"],))[0]["c"]
    return a


@app.get("/api/accounts")
def accounts_list():
    return jsonify([public_account(a) for a in db.query("SELECT * FROM accounts ORDER BY id")])


@app.post("/api/accounts")
def accounts_add():
    data = request.get_json(force=True)
    key = (data.get("golden_key") or "").strip()
    if not key:
        return err("Вставьте golden_key")
    try:
        me = FunPayAccount(key).get_me()
    except FunPayError as e:
        return err(str(e))
    except Exception as e:
        return err(f"Не удалось связаться с FunPay: {e}", 502)
    if db.query("SELECT id FROM accounts WHERE user_id=?", (me["user_id"],)):
        return err(f"Аккаунт {me['username']} уже добавлен")
    name = (data.get("name") or "").strip() or me["username"]
    aid = db.execute(
        "INSERT INTO accounts(name, key_enc, user_id, username, balance, currency, status, created) VALUES(?,?,?,?,?,?, 'ok', ?)",
        (name, encrypt(key), me["user_id"], me["username"], me["balance"], me["currency"], time.time()))
    db.log(f"Добавлен аккаунт {me['username']}")
    sync.wake()
    return jsonify(public_account(db.query("SELECT * FROM accounts WHERE id=?", (aid,))[0]))


@app.patch("/api/accounts/<int:aid>")
def accounts_rename(aid):
    name = (request.get_json(force=True).get("name") or "").strip()
    if not name:
        return err("Имя не может быть пустым")
    db.execute("UPDATE accounts SET name=? WHERE id=?", (name, aid))
    return jsonify(ok=True)


@app.delete("/api/accounts/<int:aid>")
def accounts_delete(aid):
    for table in ("orders", "chats", "messages"):
        db.execute(f"DELETE FROM {table} WHERE account_id=?", (aid,))
    db.execute("DELETE FROM accounts WHERE id=?", (aid,))
    db.execute("DELETE FROM plugin_kv WHERE plugin_id='core' AND key=?", (f"chat_init_max_{aid}",))
    accounts.forget(aid)
    db.log(f"Аккаунт #{aid} удалён")
    return jsonify(ok=True)


@app.post("/api/sync")
def sync_now():
    sync.wake()
    return jsonify(ok=True)


# ---------- статистика ----------
@app.get("/api/stats")
def stats():
    days = max(1, min(365, int(request.args.get("days", 30))))
    account = request.args.get("account", type=int)
    start = (datetime.now() - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    where, params = "ts >= ?", [start.timestamp()]
    if account:
        where += " AND account_id = ?"
        params.append(account)
    rows = db.query(f"""SELECT o.*, a.name AS account FROM orders o
                        LEFT JOIN accounts a ON a.id = o.account_id
                        WHERE {where} ORDER BY ts DESC""", params)

    costs = {r["order_id"]: r["c"] for r in db.query(
        "SELECT order_id, SUM(amount) c FROM order_costs GROUP BY order_id")}
    commission_pct = db.get_settings()["commission_pct"]

    daily = {(start + timedelta(days=i)).strftime("%Y-%m-%d"): {"revenue": 0.0, "orders": 0, "profit": 0.0} for i in range(days)}
    lots = {}
    revenue = refunds = cost_total = 0.0
    count = 0
    for r in rows:
        if r["status"] == "refunded":
            refunds += r["amount"] or 0
            continue
        amount = r["amount"] or 0
        cost = costs.get(r["id"], 0.0)
        profit = amount * (1 - commission_pct / 100) - cost
        r["cost"], r["profit"] = cost, round(profit, 2)
        day = datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d")
        if day in daily:
            daily[day]["revenue"] += amount
            daily[day]["profit"] += profit
            daily[day]["orders"] += 1
        lot = lots.setdefault(r["description"] or "—", {"lot": r["description"] or "—", "orders": 0, "revenue": 0.0, "profit": 0.0})
        lot["orders"] += 1
        lot["revenue"] += amount
        lot["profit"] += profit
        revenue += amount
        cost_total += cost
        count += 1
    commission = revenue * commission_pct / 100

    return jsonify(
        days=days, revenue=round(revenue, 2), orders=count, refunds=round(refunds, 2),
        avg_check=round(revenue / count, 2) if count else 0,
        costs=round(cost_total, 2), commission=round(commission, 2), commission_pct=commission_pct,
        profit=round(revenue - commission - cost_total, 2),
        daily=[{"date": d, **v} for d, v in daily.items()],
        top_lots=sorted(lots.values(), key=lambda x: -x["profit"])[:10],
        recent=rows[:200],
    )


# ---------- плагины ----------
@app.get("/api/plugins")
def plugins_list():
    return jsonify(plugins.describe())


@app.post("/api/plugins/<pid>/enabled")
def plugins_toggle(pid):
    try:
        on = bool(request.get_json(force=True).get("enabled"))
        if on and not subscription.is_active():
            return err("Плагины доступны по подписке. Оформите её в разделе «Подписка».", 402)
        plugins.set_enabled(pid, on)
        db.log(f"Плагин {pid} {'включён' if on else 'выключен'}")
    except KeyError:
        return err("Плагин не найден", 404)
    except ValueError as e:
        return err(str(e))
    return jsonify(ok=True)


@app.put("/api/plugins/<pid>/config")
def plugins_config(pid):
    try:
        plugins.set_config(pid, request.get_json(force=True))
    except KeyError:
        return err("Плагин не найден", 404)
    except ValueError as e:
        return err(str(e))
    return jsonify(ok=True)


import os as _os

def _plugin_img_dir():
    d = _os.path.join(_os.environ.get("KASSA_DATA", "data"), "plugin_images")
    _os.makedirs(d, exist_ok=True)
    return d


@app.post("/api/plugins/<pid>/image/<key>")
def plugin_image_upload(pid, key):
    import base64
    body = request.get_json(force=True, silent=True) or {}
    b64 = body.get("data_b64", "")
    if not b64:
        return err("Файл не получен")
    try:
        data = base64.b64decode(b64)
    except Exception:
        return err("Не удалось прочитать картинку")
    if len(data) > 5 * 1024 * 1024:
        return err("Картинка больше 5 МБ")
    with open(_os.path.join(_plugin_img_dir(), f"{pid}_{key}"), "wb") as out:
        out.write(data)
    return jsonify(ok=True, set=True)


@app.delete("/api/plugins/<pid>/image/<key>")
def plugin_image_delete(pid, key):
    path = _os.path.join(_plugin_img_dir(), f"{pid}_{key}")
    if _os.path.exists(path):
        _os.remove(path)
    return jsonify(ok=True, set=False)


@app.get("/api/plugins/<pid>/image/<key>")
def plugin_image_get(pid, key):
    path = _os.path.join(_plugin_img_dir(), f"{pid}_{key}")
    if not _os.path.exists(path):
        return err("Нет картинки", 404)
    from flask import send_file
    return send_file(path, mimetype="image/png")


@app.post("/api/plugins/<pid>/test")
def plugins_test(pid):
    try:
        return jsonify(plugins.test(pid))
    except KeyError:
        return err("У плагина нет проверки подключения", 404)


@app.post("/api/plugins/<pid>/dry-run")
def plugins_dry_run(pid):
    try:
        return jsonify(plugins.dry_run(pid, request.get_json(silent=True) or {}))
    except KeyError:
        return err("Плагин не обрабатывает заказы", 404)


# ---------- заказы в работе у плагинов ----------
@app.get("/api/tasks")
def tasks_list():
    return jsonify(plugins.list_tasks(request.args.get("status"), request.args.get("limit", 100, type=int)))


@app.post("/api/tasks/<int:tid>/retry")
def tasks_retry(tid):
    try:
        plugins.retry_task(tid)
    except ValueError as e:
        return err(str(e))
    return jsonify(ok=True)


@app.post("/api/tasks/<int:tid>/resolve")
def tasks_resolve(tid):
    try:
        plugins.resolve_task(tid)
    except ValueError as e:
        return err(str(e))
    return jsonify(ok=True)


# ---------- настройки ----------
@app.get("/api/settings")
def settings_get():
    return jsonify(db.get_settings(reveal_secrets=False))


@app.put("/api/settings")
def settings_put():
    data = request.get_json(force=True)
    clear = set(data.pop("__clear__", []))
    for k in db.SECRET_KEYS:  # пустой секрет = «не менять», стирание — только явно
        if k in clear:
            data[k] = ""
        elif not data.get(k):
            data.pop(k, None)
    try:
        db.set_settings(data)
    except ValueError as e:
        return err(str(e))
    sync.wake()
    chat.wake()
    return jsonify(db.get_settings(reveal_secrets=False))


@app.post("/api/telegram/test")
def telegram_test():
    try:
        notify.test()
    except Exception as e:
        return jsonify(ok=False, message=str(e))
    return jsonify(ok=True, message="Тестовое сообщение отправлено — проверьте Telegram")


# ---------- чаты ----------
@app.get("/api/chats")
def chats_list():
    return jsonify(db.query("""SELECT c.*, a.name AS account FROM chats c JOIN accounts a ON a.id = c.account_id
                               ORDER BY c.last_ts DESC LIMIT 300"""))


def _messages(aid, cid):
    return db.query("SELECT * FROM messages WHERE account_id=? AND chat_id=? ORDER BY id DESC LIMIT 150", (aid, cid))[::-1]


def _buyer_lot(aid, cid):
    """Последний заказ покупателя из этого чата — его название лота и номер заказа."""
    row = db.query("SELECT name FROM chats WHERE account_id=? AND chat_id=?", (aid, cid))
    if not row or not row[0]["name"]:
        return None
    buyer = row[0]["name"]
    o = db.query("SELECT id, description, ts FROM orders WHERE account_id=? AND buyer=? ORDER BY ts DESC LIMIT 1",
                 (aid, buyer))
    if not o:
        return None
    return {"order_id": o[0]["id"], "lot": (o[0]["description"] or "").strip()}


@app.get("/api/chats/<int:aid>/<cid>")
def chat_messages(aid, cid):
    warning = None
    try:
        chat.refresh_chat(aid, cid)
    except Exception as e:
        warning = f"Показаны сохранённые сообщения: {e}"
    db.execute("UPDATE chats SET unread=0 WHERE account_id=? AND chat_id=?", (aid, cid))
    return jsonify(messages=_messages(aid, cid), warning=warning, order=_buyer_lot(aid, cid))


@app.post("/api/chats/<int:aid>/<cid>")
def chat_send(aid, cid):
    try:
        chat.send_from_app(aid, cid, request.get_json(force=True).get("text", ""))
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"Сообщение не отправлено: {e}", 502)
    return jsonify(messages=_messages(aid, cid))


# ---------- автовыдача ----------
def _lot_out(lot):
    lot = dict(lot)
    lot["stock"] = db.query("SELECT COUNT(*) c FROM delivery_stock WHERE lot_id=? AND order_id IS NULL", (lot["id"],))[0]["c"]
    lot["sold"] = db.query("SELECT COUNT(*) c FROM delivery_stock WHERE lot_id=? AND order_id IS NOT NULL", (lot["id"],))[0]["c"]
    return lot


def _lot_input(data):
    phrase = (data.get("phrase") or "").strip()
    mode = data.get("mode") if data.get("mode") in ("text", "fifo") else "text"
    text = data.get("text") or ""
    if len(phrase) < 3:
        raise ValueError("Укажите часть названия лота — минимум 3 символа")
    if mode == "text" and not text.strip():
        raise ValueError("Напишите текст, который получит покупатель")
    try:
        cost = max(0.0, float(data.get("cost") or 0))
    except ValueError:
        raise ValueError("Себестоимость должна быть числом")
    return phrase, mode, text, cost, int(bool(data.get("enabled", True)))


@app.get("/api/delivery")
def delivery_list():
    return jsonify([_lot_out(l) for l in db.query("SELECT * FROM delivery_lots ORDER BY id")])


@app.post("/api/delivery")
def delivery_add():
    try:
        vals = _lot_input(request.get_json(force=True))
    except ValueError as e:
        return err(str(e))
    lid = db.execute("INSERT INTO delivery_lots(phrase, mode, text, cost, enabled) VALUES(?,?,?,?,?)", vals)
    return jsonify(_lot_out(db.query("SELECT * FROM delivery_lots WHERE id=?", (lid,))[0]))


@app.put("/api/delivery/<int:lid>")
def delivery_update(lid):
    try:
        vals = _lot_input(request.get_json(force=True))
    except ValueError as e:
        return err(str(e))
    db.execute("UPDATE delivery_lots SET phrase=?, mode=?, text=?, cost=?, enabled=? WHERE id=?", (*vals, lid))
    return jsonify(ok=True)


@app.delete("/api/delivery/<int:lid>")
def delivery_delete(lid):
    db.execute("DELETE FROM delivery_stock WHERE lot_id=? AND order_id IS NULL", (lid,))
    db.execute("DELETE FROM delivery_lots WHERE id=?", (lid,))
    return jsonify(ok=True)


@app.post("/api/delivery/<int:lid>/stock")
def delivery_stock_add(lid):
    lines = [l.strip() for l in (request.get_json(force=True).get("lines") or "").splitlines() if l.strip()]
    if not lines:
        return err("Список пуст: вставьте товары, по одному на строку")
    for line in lines:
        db.execute("INSERT INTO delivery_stock(lot_id, line) VALUES(?, ?)", (lid, line))
    db.execute("UPDATE delivery_lots SET notified_empty=0 WHERE id=?", (lid,))
    db.log(f"Автовыдача: добавлено {len(lines)} шт. товара в лот #{lid}")
    return jsonify(added=len(lines))


@app.delete("/api/delivery/<int:lid>/stock")
def delivery_stock_clear(lid):
    db.execute("DELETE FROM delivery_stock WHERE lot_id=? AND order_id IS NULL", (lid,))
    return jsonify(ok=True)


# ---------- аренда Steam ----------
from core import steam as _steam  # noqa: E402
from plugins import rent_steam as _rent  # noqa: E402


@app.get("/api/rent/top")
def rent_top():
    """Топ продаж аренды за последние 30 дней: по аккаунтам, по играм, заработок."""
    import time as _t
    import json as _j
    row = db.query("SELECT value FROM plugin_kv WHERE plugin_id='rent_steam' AND key='rentals'")
    rentals = _j.loads(row[0]["value"]) if row else {}
    since = _t.time() - 30 * 86400
    by_acc, by_game = {}, {}
    total = 0.0
    cur = "₽"
    count = 0
    for oid, r in rentals.items():
        if r.get("started", 0) < since:
            continue
        amt = float(r.get("amount") or 0)
        total += amt
        count += 1
        cur = r.get("currency", cur)
        acc = r.get("steam_login", "—")
        game = (r.get("game") or "—").strip() or "—"
        a = by_acc.setdefault(acc, {"account": acc, "count": 0, "sum": 0.0})
        a["count"] += 1; a["sum"] += amt
        g = by_game.setdefault(game, {"game": game, "count": 0, "sum": 0.0})
        g["count"] += 1; g["sum"] += amt
    top_acc = sorted(by_acc.values(), key=lambda x: x["count"], reverse=True)[:10]
    top_game = sorted(by_game.values(), key=lambda x: x["count"], reverse=True)[:10]
    return jsonify(total=round(total, 2), currency=cur, count=count,
                   accounts=top_acc, games=top_game)


@app.get("/api/rent/accounts")
def rent_accounts():
    busy = {r["steam_login"]: (oid, r) for oid, r in _rent.rentals().items() if r["status"] in ("active", "changing", "needs_reset")}
    out = []
    for a in _rent.steam_accounts():
        rent = busy.get(a["login"])
        out.append({"login": a["login"], "title": a.get("title", ""), "password": a["password"], "has_mafile": bool(a.get("shared_secret")),
                    "enabled": a.get("enabled", True), "state": a.get("state", "free"),
                    "rented_until": rent[1]["until"] if rent else None,
                    "rented_by": rent[1]["buyer"] if rent else None,
                    "order_id": rent[0] if rent else None,
                    "offer_id": a.get("offer_id", ""),
                    "extend_offer_id": a.get("extend_offer_id", ""),
                    "onlypc_check": a.get("onlypc_check", None),
                    "hide_lot_on_rent": a.get("hide_lot_on_rent", None),
                    "bonus_enabled": a.get("bonus_enabled", False),
                    "bonus_minutes": a.get("bonus_minutes", ""),
                    "bonus_min_stars": a.get("bonus_min_stars", 5),
                    "bonus_min_hours": a.get("bonus_min_hours", "")})
    return jsonify(out)


def _apply_rent_opts(acc, d):
    """Индивидуальные настройки аккаунта аренды. Пусто/нет = наследует общие из настроек плагина."""
    if "extend_offer_id" in d:
        acc["extend_offer_id"] = (d.get("extend_offer_id") or "").strip()
    # бонус за отзыв — индивидуально на аккаунт
    if "bonus_enabled" in d:
        acc["bonus_enabled"] = bool(d["bonus_enabled"])
    if "bonus_minutes" in d:
        v = d.get("bonus_minutes")
        acc["bonus_minutes"] = "" if v in (None, "") else int(float(v))
    if "bonus_min_stars" in d:
        acc["bonus_min_stars"] = int(d.get("bonus_min_stars") or 5)
    if "bonus_min_hours" in d:
        v = d.get("bonus_min_hours")
        acc["bonus_min_hours"] = "" if v in (None, "") else float(v)
    # bool-настройки: None = наследовать, True/False = своё значение
    for k in ("onlypc_check", "hide_lot_on_rent"):
        if k in d:
            acc[k] = None if d[k] is None else bool(d[k])


@app.post("/api/rent/accounts")
def rent_add():
    d = request.get_json(force=True)
    login = (d.get("login") or "").strip()
    password = (d.get("password") or "").strip()
    if not login or not password:
        return err("Укажите логин и пароль")
    ss = ""
    if d.get("mafile"):
        try:
            ss = _steam.parse_mafile(d["mafile"])["shared_secret"]
        except _steam.SteamError as e:
            return err(str(e))
    accs = _rent._raw_accounts()
    if any(x["login"].lower() == login.lower() for x in accs):
        return err(f"Аккаунт {login} уже добавлен")
    new_acc = {"login": login, "title": (d.get("title") or "").strip(), "password": encrypt(password),
                 "shared_secret": encrypt(ss) if ss else "", "enabled": True, "state": "free",
                 "offer_id": (d.get("offer_id") or "").strip()}
    _apply_rent_opts(new_acc, d)
    accs.append(new_acc)
    _rent._kv_set("accounts", accs)
    db.log(f"Аренда: добавлен аккаунт {login}")
    return jsonify(ok=True)


@app.put("/api/rent/accounts/<login>")
def rent_edit(login):
    d = request.get_json(force=True)
    accs = _rent._raw_accounts()
    acc = next((a for a in accs if a["login"] == login), None)
    if not acc:
        return err("Аккаунт не найден", 404)
    if "enabled" in d:
        acc["enabled"] = bool(d["enabled"])
    if "title" in d:
        acc["title"] = (d.get("title") or "").strip()
    if "offer_id" in d:
        acc["offer_id"] = (d.get("offer_id") or "").strip()
    _apply_rent_opts(acc, d)
    if d.get("password"):
        acc["password"] = encrypt(d["password"].strip())
    if d.get("mafile"):
        try:
            acc["shared_secret"] = encrypt(_steam.parse_mafile(d["mafile"])["shared_secret"])
        except _steam.SteamError as e:
            return err(str(e))
    _rent._kv_set("accounts", accs)
    return jsonify(ok=True)


@app.delete("/api/rent/accounts/<login>")
def rent_delete(login):
    if any(r["steam_login"] == login and r["status"] in ("active", "changing")
           for r in _rent.rentals().values()):
        return err("Аккаунт сейчас в аренде — дождитесь окончания")
    _rent._kv_set("accounts", [a for a in _rent._raw_accounts() if a["login"] != login])
    return jsonify(ok=True)


@app.post("/api/rent/accounts/<login>/test")
def rent_test(login):
    acc = next((a for a in _rent.steam_accounts() if a["login"] == login), None)
    if not acc:
        return err("Аккаунт не найден", 404)
    if not acc.get("shared_secret"):
        return jsonify(ok=False, message="Нет maFile — проверить вход автоматически нельзя")
    try:
        _steam.check_login(acc["login"], acc["password"], acc["shared_secret"])
    except Exception as e:
        return jsonify(ok=False, message=str(e))
    return jsonify(ok=True, message="Вход работает, пароль верный")


@app.get("/api/rent/onlypc")
def onlypc_list():
    return jsonify(_rent.onlypc_pending())


@app.post("/api/rent/onlypc/<order_id>/<decision>")
def onlypc_decide(order_id, decision):
    from core import plugins as _pl
    try:
        res = _rent.onlypc_decide(order_id, decision == "approve", _pl.Ctx("rent_steam"))
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(str(e), 502)
    return jsonify(ok=True, message=res)


@app.post("/api/rent/accounts/<login>/logout")
def rent_logout(login):
    acc = next((a for a in _rent.steam_accounts() if a["login"] == login), None)
    if not acc:
        return err("Аккаунт не найден", 404)
    if not acc.get("shared_secret"):
        return err("Нужен maFile, чтобы войти и завершить сессии")
    try:
        _steam.logout_everywhere(acc["login"], acc["password"], acc["shared_secret"])
    except Exception as e:
        return err(f"Не удалось завершить сессии: {e}", 502)
    db.log(f"Аренда: завершены сессии аккаунта {login}")
    return jsonify(ok=True, message="Все сессии аккаунта завершены")


@app.post("/api/rent/accounts/<login>/reset-done")
def rent_reset_done(login):
    """Продавец вручную сбросил доступ — освобождаем аккаунт."""
    d = request.get_json(silent=True) or {}
    accs = _rent._raw_accounts()
    acc = next((a for a in accs if a["login"] == login), None)
    if not acc:
        return err("Аккаунт не найден", 404)
    if d.get("password"):
        acc["password"] = encrypt(d["password"].strip())
    acc["state"] = "free"
    _rent._kv_set("accounts", accs)
    r = _rent.rentals()
    for oid, rent in r.items():
        if rent["steam_login"] == login and rent["status"] == "needs_reset":
            rent["status"] = "done"
    _rent.save_rentals(r)
    db.log(f"Аренда: аккаунт {login} освобождён вручную")
    return jsonify(ok=True)


# ---------- Offline Activite: аккаунты ----------
from plugins import offline_activite as _oa  # noqa: E402
from core import steam as _steam2  # noqa: E402


@app.get("/api/offline/accounts")
def offline_accounts():
    return jsonify(_oa.accounts_public())


@app.post("/api/offline/accounts")
def offline_add():
    d = request.get_json(force=True)
    try:
        _oa.add_account(d.get("login", ""), d.get("mafile", ""))
    except (_steam2.SteamError, ValueError) as e:
        return err(str(e))
    return jsonify(ok=True)


@app.delete("/api/offline/accounts/<login>")
def offline_delete(login):
    _oa.remove_account(login)
    return jsonify(ok=True)


# ---------- автоподнятие ----------
@app.get("/api/raise")
def raise_status():
    names = {a["id"]: a["name"] for a in db.query("SELECT id, name FROM accounts")}
    return jsonify([{"account": names.get(k, k), **v} for k, v in raiser.last_run.items()])


@app.post("/api/raise")
def raise_now():
    return jsonify(raiser.raise_now())


# ---------- подписка ----------
@app.get("/api/subscription")
def subscription_get():
    return jsonify(subscription.status())


@app.post("/api/subscription/refresh")
def subscription_refresh():
    return jsonify(subscription.status(force=True))


@app.post("/api/subscription/activate")
def subscription_activate():
    code = request.get_json(force=True).get("code", "")
    try:
        st = subscription.activate(code)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(str(e), 502)
    plugins.resume_all()  # снять паузу с плагинов после оплаты
    return jsonify(st)


# ---------- резервные копии ----------
@app.get("/api/backups")
def backups_list():
    return jsonify(backup.list_backups())


@app.post("/api/backups")
def backups_make():
    try:
        path = backup.make_backup()
    except Exception as e:
        return err(f"Не удалось создать копию: {e}", 500)
    return jsonify(ok=True, name=path.split("/")[-1])


# ---------- журнал ----------
@app.get("/api/events")
def events():
    since = request.args.get("since", 0, type=int)
    return jsonify(db.query("SELECT * FROM events WHERE id > ? ORDER BY id DESC LIMIT 100", (since,)))


plugins.load_all()
plugins.start_runner()
subscription.start()
sync.start()
chat.start()
raiser.start()
backup.start()
db.log(f"Воркер запущен, версия {VERSION}")
