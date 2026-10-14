"""Счётчики продаж — дописывает в описание лотов статистику продаж.

Что делает: автоматически добавляет в конец описания выбранных лотов на FunPay
счётчики «продано за день / за неделю / всего». Обновляется при каждой продаже.
Повышает доверие покупателей («продано 1500 раз») и продажи.

Как работает:
- Плагин считает заказы по каждому лоту (из истории продаж бота).
- В описание лота добавляется блок между метками, который обновляется, не затирая
  ваш основной текст описания.
- Вы выбираете, у каких лотов показывать счётчики (по ID лота).

Безопасно: не трогает деньги, только редактирует текст описания ваших лотов.
"""
import re
import time
import json

from core import db

NAME = "Счётчики продаж"
DESCRIPTION = "Дописывает в описание лотов статистику: продано за день, за неделю и всего. Обновляется автоматически."
CATEGORY = "Лоты"
VERSION = "1.0"

MARK_START = "─────────────"
MARK_END = "─────────────"

SETTINGS = [
    {"key": "lot_ids", "label": "ID лотов со счётчиками", "type": "textarea", "default": "",
     "hint": "ID лотов (из ссылки offer?id=...), по одному на строку или через запятую. Только у них будут счётчики. Пусто — выключено."},
    {"key": "template", "label": "Формат счётчика", "type": "textarea",
     "default": "📊 Продаж за день: {day} · за неделю: {week} · всего: {total}",
     "hint": "{day} — за сегодня, {week} — за 7 дней, {total} — всего. Можно несколько строк."},
    {"key": "update_min", "label": "Обновлять каждые, минут", "type": "number", "default": 30,
     "hint": "Как часто обновлять описания на FunPay. Не слишком часто, чтобы не нагружать аккаунт."},
]


def _lot_ids(cfg):
    return [int(x) for x in re.findall(r"\d+", cfg.get("lot_ids") or "")]


def _last_run():
    r = db.query("SELECT value FROM plugin_kv WHERE plugin_id='auto_bonus' AND key='last_run'")
    return float(r[0]["value"]) if r else 0


def _set_last_run(ts):
    db.execute("INSERT OR REPLACE INTO plugin_kv(plugin_id,key,value) VALUES('auto_bonus','last_run',?)", (str(ts),))


def _lot_cache():
    """Кэш названий лотов по ID (чтобы сопоставлять заказы с лотами)."""
    r = db.query("SELECT value FROM plugin_kv WHERE plugin_id='auto_bonus' AND key='lot_names'")
    return json.loads(r[0]["value"]) if r else {}


def _set_lot_cache(c):
    db.execute("INSERT OR REPLACE INTO plugin_kv(plugin_id,key,value) VALUES('auto_bonus','lot_names',?)",
               (json.dumps(c, ensure_ascii=False),))


def _sales_for_lot(lot_name, account_id):
    """Считает продажи по названию лота: за день, неделю, всего."""
    now = time.time()
    day0 = now - 86400
    week0 = now - 7 * 86400
    # сравниваем по совпадению названия лота в описании заказа
    rows = db.query(
        "SELECT ts FROM orders WHERE account_id=? AND status!='refunded' AND description LIKE ?",
        (account_id, f"%{lot_name}%"))
    day = sum(1 for r in rows if r["ts"] >= day0)
    week = sum(1 for r in rows if r["ts"] >= week0)
    total = len(rows)
    return {"day": day, "week": week, "total": total}


def _fill(t, **kw):
    for k, v in kw.items():
        t = t.replace("{" + k + "}", str(v))
    return t


def _apply_counter(old_desc, counter_block):
    """Вставляет/обновляет блок счётчика в описании, не затирая основной текст."""
    block = f"{MARK_START}\n{counter_block}\n{MARK_END}"
    # если блок уже есть — заменяем его
    pattern = re.escape(MARK_START) + r".*?" + re.escape(MARK_END)
    if re.search(pattern, old_desc, re.S):
        return re.sub(pattern, block, old_desc, flags=re.S).strip()
    # иначе — дописываем в конец
    return (old_desc.rstrip() + "\n\n" + block).strip()


def _run(ctx, force=False):
    cfg = ctx.config
    ids = _lot_ids(cfg)
    if not ids:
        return
    interval = max(5, int(cfg.get("update_min") or 30)) * 60
    if not force and (time.time() - _last_run()) < interval:
        return
    _set_last_run(time.time())

    from core import db as _db
    accs = _db.query("SELECT id FROM accounts WHERE status='ok'")
    cache = _lot_cache()
    for offer_id in ids:
        done = False
        for acc in accs:
            try:
                info = ctx.lot_info(acc["id"], offer_id)
            except Exception:
                continue
            name = (info.get("title") or "").strip()
            if not name:
                continue
            cache[str(offer_id)] = name
            stats = _sales_for_lot(name, acc["id"])
            counter = _fill(cfg["template"], day=stats["day"], week=stats["week"], total=stats["total"])
            new_desc = _apply_counter(info.get("description") or "", counter)
            if new_desc != (info.get("description") or ""):
                try:
                    ctx.save_lot_description(acc["id"], offer_id, new_desc)
                    ctx.log(f"Счётчики: обновил лот {offer_id} ({name[:30]}) — "
                            f"день {stats['day']}, неделя {stats['week']}, всего {stats['total']}")
                except Exception as e:
                    ctx.log(f"Счётчики: не удалось обновить лот {offer_id}: {e}", "warn")
            done = True
            break
        if not done:
            ctx.log(f"Счётчики: лот {offer_id} не найден ни на одном аккаунте", "warn")
    _set_lot_cache(cache)


def tick(ctx):
    """Периодически обновляет описания."""
    try:
        _run(ctx)
    except Exception as e:
        ctx.log(f"Счётчики: ошибка обновления: {e}", "warn")


def on_order_confirmed(order, ctx):
    """После подтверждённой продажи — обновим счётчики (с учётом интервала)."""
    try:
        _run(ctx)
    except Exception:
        pass


def test(ctx):
    """Проверка: обновить описания прямо сейчас."""
    ids = _lot_ids(ctx.config)
    if not ids:
        return {"ok": False, "message": "Не заданы ID лотов"}
    try:
        _run(ctx, force=True)
        return {"ok": True, "message": f"Обновлено лотов: {len(ids)} (смотрите описания на FunPay)"}
    except Exception as e:
        return {"ok": False, "message": f"Ошибка: {e}"}
