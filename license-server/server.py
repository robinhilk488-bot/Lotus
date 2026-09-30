"""Сайт лицензий Kassa — твой главный сервер.

Живёт отдельно от воркеров клиентов. Делает две вещи:
  1. Выдаёт коды подписки (ты генерируешь их пачкой и загружаешь в автовыдачу FunPay).
  2. Отвечает воркеру клиента на вопрос «этот код годен?» и привязывает код к серверу клиента.

Код активируется один раз и навсегда привязывается к server_id клиента (это хэш его ключа
подключения, сам ключ сюда не попадает). Продление того же сервера другим кодом суммирует дни.

Запуск: см. README в этой папке. Управление — через X-Admin-Token (переменная KASSA_ADMIN_TOKEN).
"""
import hmac
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request

DATA = Path(os.environ.get("KASSA_LICENSE_DATA", Path(__file__).resolve().parent / "data"))
DATA.mkdir(parents=True, exist_ok=True)
ADMIN = os.environ.get("KASSA_ADMIN_TOKEN", "")
if len(ADMIN) < 16:
    raise SystemExit("Задайте KASSA_ADMIN_TOKEN (минимум 16 символов) — им вы управляете кодами")

PLANS = {1: "1 месяц", 3: "3 месяца", 6: "6 месяцев", 12: "12 месяцев"}
DAYS_PER_MONTH = 30
TRIAL_DAYS = 3

conn = sqlite3.connect(DATA / "license.db", check_same_thread=False)
conn.row_factory = sqlite3.Row
conn.executescript("""
CREATE TABLE IF NOT EXISTS codes (
    code TEXT PRIMARY KEY,
    months INTEGER NOT NULL,
    created REAL NOT NULL,
    activated_by TEXT,          -- server_id, к которому привязан код
    activated_at REAL,
    note TEXT
);
CREATE TABLE IF NOT EXISTS servers (
    server_id TEXT PRIMARY KEY,
    paid_until REAL NOT NULL DEFAULT 0,   -- срок по оплаченным кодам
    trial_until REAL NOT NULL DEFAULT 0,  -- срок пробного периода
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL
);
""")
conn.commit()

app = Flask(__name__)
app.json.ensure_ascii = False


def now():
    return time.time()


def make_code():
    """Код формата KSA-XXXX-XXXX-XXXX, буквы и цифры без похожих символов."""
    alphabet = "ABCDEFGHJKLMNPQRTUVWXYZ2346789"
    groups = ["".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3)]
    return "KSA-" + "-".join(groups)


def server_state(server_id):
    r = conn.execute("SELECT * FROM servers WHERE server_id=?", (server_id,)).fetchone()
    if not r:
        return None
    paid, trial = r["paid_until"], r["trial_until"]
    until = max(paid, trial)
    active = until > now()
    return {
        "active": active,
        "until": until,
        "days_left": max(0, int((until - now()) / 86400)) if active else 0,
        "source": "paid" if paid > now() else "trial" if trial > now() else "none",
        "trial_until": trial,
    }


def require_admin():
    given = request.headers.get("X-Admin-Token", "")
    return hmac.compare_digest(given, ADMIN)


# ---------------- для воркера клиента ----------------
@app.post("/api/check")
def check():
    """Воркер спрашивает статус подписки своего server_id. Здесь же выдаётся пробный период."""
    data = request.get_json(force=True, silent=True) or {}
    sid = (data.get("server_id") or "").strip()
    if len(sid) < 16:
        return jsonify(error="server_id не передан"), 400
    row = conn.execute("SELECT * FROM servers WHERE server_id=?", (sid,)).fetchone()
    if not row:
        # первый визит сервера — заводим пробный период
        trial_until = now() + TRIAL_DAYS * 86400
        conn.execute("INSERT INTO servers(server_id, trial_until, first_seen, last_seen) VALUES(?,?,?,?)",
                     (sid, trial_until, now(), now()))
        conn.commit()
    else:
        conn.execute("UPDATE servers SET last_seen=? WHERE server_id=?", (now(), sid))
        conn.commit()
    return jsonify(server_state(sid))


@app.post("/api/activate")
def activate():
    """Воркер присылает код и свой server_id. Код привязывается к серверу, дни суммируются."""
    data = request.get_json(force=True, silent=True) or {}
    sid = (data.get("server_id") or "").strip()
    code = (data.get("code") or "").strip().upper()
    if len(sid) < 16:
        return jsonify(error="server_id не передан"), 400
    row = conn.execute("SELECT * FROM codes WHERE code=?", (code,)).fetchone()
    if not row:
        return jsonify(error="Код не найден. Проверьте, правильно ли он введён."), 404
    if row["activated_by"]:
        if row["activated_by"] == sid:
            return jsonify(error="Этот код уже активирован на этом сервере."), 409
        return jsonify(error="Этот код уже использован на другом сервере."), 409

    srv = conn.execute("SELECT * FROM servers WHERE server_id=?", (sid,)).fetchone()
    base = max(srv["paid_until"] if srv else 0, now())  # продлеваем от остатка или от сегодня
    paid_until = base + row["months"] * DAYS_PER_MONTH * 86400
    if srv:
        conn.execute("UPDATE servers SET paid_until=?, last_seen=? WHERE server_id=?", (paid_until, now(), sid))
    else:
        conn.execute("INSERT INTO servers(server_id, paid_until, first_seen, last_seen) VALUES(?,?,?,?)",
                     (sid, paid_until, now(), now()))
    conn.execute("UPDATE codes SET activated_by=?, activated_at=? WHERE code=?", (sid, now(), code))
    conn.commit()
    state = server_state(sid)
    state["added_months"] = row["months"]
    return jsonify(state)


# ---------------- для тебя (админ) ----------------
@app.post("/admin/generate")
def generate():
    """Создать пачку кодов на N месяцев. Тело: {"months": 1, "count": 50, "note": "..."}."""
    if not require_admin():
        return jsonify(error="Нужен админ-токен"), 401
    data = request.get_json(force=True, silent=True) or {}
    months = int(data.get("months", 0))
    count = max(1, min(1000, int(data.get("count", 1))))
    if months not in PLANS:
        return jsonify(error=f"months должен быть одним из {list(PLANS)}"), 400
    codes = []
    for _ in range(count):
        code = make_code()
        while conn.execute("SELECT 1 FROM codes WHERE code=?", (code,)).fetchone():
            code = make_code()
        conn.execute("INSERT INTO codes(code, months, created, note) VALUES(?,?,?,?)",
                     (code, months, now(), data.get("note", "")))
        codes.append(code)
    conn.commit()
    return jsonify(months=months, count=len(codes), codes=codes)


@app.get("/admin/codes")
def list_codes():
    if not require_admin():
        return jsonify(error="Нужен админ-токен"), 401
    status = request.args.get("status")  # free | used
    q = "SELECT * FROM codes"
    if status == "free":
        q += " WHERE activated_by IS NULL"
    elif status == "used":
        q += " WHERE activated_by IS NOT NULL"
    q += " ORDER BY created DESC LIMIT 2000"
    rows = [dict(r) for r in conn.execute(q).fetchall()]
    free = conn.execute("SELECT months, COUNT(*) c FROM codes WHERE activated_by IS NULL GROUP BY months").fetchall()
    return jsonify(codes=rows, free_by_plan={r["months"]: r["c"] for r in free})


@app.get("/admin/servers")
def list_servers():
    if not require_admin():
        return jsonify(error="Нужен админ-токен"), 401
    rows = []
    for r in conn.execute("SELECT * FROM servers ORDER BY last_seen DESC LIMIT 2000").fetchall():
        d = dict(r)
        d["active"] = max(r["paid_until"], r["trial_until"]) > now()
        rows.append(d)
    return jsonify(servers=rows)


@app.get("/")
def health():
    return jsonify(service="kassa-license", ok=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8900)))
