"""SQLite-хранилище воркера."""
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

DATA_DIR = Path(os.environ.get("KASSA_DATA", Path(__file__).resolve().parent.parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "kassa.db"

_lock = threading.RLock()
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    key_enc TEXT NOT NULL,
    user_id INTEGER,
    username TEXT,
    balance REAL DEFAULT 0,
    currency TEXT DEFAULT '₽',
    status TEXT DEFAULT 'new',
    error TEXT,
    last_sync REAL,
    created REAL
);
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL,
    ts REAL NOT NULL,
    buyer TEXT,
    description TEXT,
    amount REAL,
    currency TEXT,
    status TEXT,
    buyer_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_orders_ts ON orders(ts);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS plugin_state (id TEXT PRIMARY KEY, enabled INTEGER DEFAULT 0, config TEXT DEFAULT '{}');
CREATE TABLE IF NOT EXISTS plugin_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plugin_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_try REAL NOT NULL DEFAULT 0,
    result TEXT,
    error TEXT,
    created REAL NOT NULL,
    updated REAL NOT NULL,
    UNIQUE(plugin_id, order_id)
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON plugin_tasks(status, next_try);
CREATE TABLE IF NOT EXISTS plugin_kv (
    plugin_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT,
    PRIMARY KEY(plugin_id, key)
);
CREATE TABLE IF NOT EXISTS chats (
    account_id INTEGER NOT NULL,
    chat_id TEXT NOT NULL,
    name TEXT,
    last_msg_id INTEGER DEFAULT 0,
    last_text TEXT,
    last_ts REAL,
    unread INTEGER DEFAULT 0,
    greeted INTEGER DEFAULT 0,
    PRIMARY KEY(account_id, chat_id)
);
CREATE TABLE IF NOT EXISTS messages (
    account_id INTEGER NOT NULL,
    chat_id TEXT NOT NULL,
    id INTEGER NOT NULL,
    author_id INTEGER,
    author TEXT,
    text TEXT,
    ts REAL,
    mine INTEGER DEFAULT 0,
    PRIMARY KEY(account_id, chat_id, id)
);
CREATE TABLE IF NOT EXISTS delivery_lots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phrase TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'text',
    text TEXT DEFAULT '',
    cost REAL DEFAULT 0,
    enabled INTEGER DEFAULT 1,
    notified_empty INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS delivery_stock (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lot_id INTEGER NOT NULL,
    line TEXT NOT NULL,
    order_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_stock ON delivery_stock(lot_id, order_id);
CREATE TABLE IF NOT EXISTS order_costs (
    order_id TEXT NOT NULL,
    source TEXT NOT NULL,
    amount REAL NOT NULL,
    PRIMARY KEY(order_id, source)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    level TEXT NOT NULL,
    text TEXT NOT NULL
);
"""

with _lock:
    _conn.executescript(SCHEMA)
    _conn.commit()
    # миграции: добавляем новые столбцы, если их ещё нет (безопасно для старых баз)
    _cols = {r[1] for r in _conn.execute("PRAGMA table_info(accounts)").fetchall()}
    if "proxy" not in _cols:
        _conn.execute("ALTER TABLE accounts ADD COLUMN proxy TEXT DEFAULT ''")
        _conn.commit()
    _ccols = {r[1] for r in _conn.execute("PRAGMA table_info(chats)").fetchall()}
    if "avatar" not in _ccols:
        _conn.execute("ALTER TABLE chats ADD COLUMN avatar TEXT DEFAULT ''")
        _conn.commit()
    if "avatar" not in _cols:
        _conn.execute("ALTER TABLE accounts ADD COLUMN avatar TEXT DEFAULT ''")
        _conn.commit()


def query(sql, params=()):
    with _lock:
        return [dict(r) for r in _conn.execute(sql, params).fetchall()]


def execute(sql, params=()):
    with _lock:
        cur = _conn.execute(sql, params)
        _conn.commit()
        return cur.lastrowid


# ---- настройки ----
# ключ: (тип, значение по умолчанию, минимум, максимум)
SETTINGS_SPEC = {
    "sync_interval_min": ("int", 5, 1, 60),
    "chat_interval_sec": ("int", 10, 5, 120),
    "notify_new_orders": ("bool", True),
    # приветствие и вызов продавца
    "greeting_enabled": ("bool", False),
    "greeting_text": ("str", "Здравствуйте! Спасибо, что написали. Отвечу в ближайшее время. "
                             "Если нужна срочная помощь, напишите !help"),
    "help_enabled": ("bool", True),
    "help_command": ("str", "!help"),
    "help_reply": ("str", "Продавец получил уведомление и скоро ответит."),
    # отзывы
    "review_thanks_enabled": ("bool", False),
    "review_thanks_min": ("int", 5, 1, 5),
    "review_thanks_text": ("str", "Спасибо за отзыв, {buyer}! Будем рады видеть вас снова."),
    # автоподнятие
    "raise_enabled": ("bool", False),
    "raise_interval_min": ("int", 120, 30, 1440),
    # прибыль и чёрный список
    "commission_pct": ("float", 0.0, 0, 50),
    "blacklist": ("list", []),
    # Telegram-уведомления
    "tg_token": ("secret", ""),
    "tg_chat_id": ("str", ""),
    "tg_on_attention": ("bool", True),
    "tg_on_account": ("bool", True),
    "tg_on_help": ("bool", True),
    "tg_on_review": ("bool", True),
    "tg_on_stock": ("bool", True),
    "tg_on_blacklist": ("bool", True),
    "tg_on_order": ("bool", False),
}
SECRET_KEYS = {k for k, v in SETTINGS_SPEC.items() if v[0] == "secret"}


def _read_raw():
    return {r["key"]: json.loads(r["value"]) for r in query("SELECT key, value FROM settings")}


def get_settings(reveal_secrets=True):
    """Все настройки. С reveal_secrets=False секреты заменены на '' и добавлен флаг <ключ>_set."""
    from .crypto import decrypt
    raw = _read_raw()
    out = {}
    for k, spec in SETTINGS_SPEC.items():
        v = raw.get(k, spec[1])
        if spec[0] == "secret":
            try:
                v = decrypt(v) if v else ""
            except Exception:
                v = ""
            if not reveal_secrets:
                out[k + "_set"] = bool(v)
                v = ""
        out[k] = v
    return out


def set_settings(values: dict):
    from .crypto import encrypt
    for k, v in values.items():
        spec = SETTINGS_SPEC.get(k)
        if not spec:
            continue
        t = spec[0]
        try:
            if t == "int":
                v = max(spec[2], min(spec[3], int(v)))
            elif t == "float":
                v = max(spec[2], min(spec[3], float(v)))
            elif t == "bool":
                v = bool(v)
            elif t == "list":
                items = v if isinstance(v, list) else str(v).splitlines()
                uniq = {}
                for x in items:  # без повторов, регистр не важен
                    x = str(x).strip()
                    if x and x.lower() not in uniq:
                        uniq[x.lower()] = x
                v = sorted(uniq.values(), key=str.lower)
            elif t == "secret":
                if v is None:
                    continue
                v = encrypt(str(v).strip()) if str(v).strip() else ""
            else:
                v = str(v)
        except (TypeError, ValueError):
            raise ValueError(f"Неверное значение для {k}")
        execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)", (k, json.dumps(v, ensure_ascii=False)))


def in_blacklist(username):
    return (username or "").strip().lower() in {x.lower() for x in get_settings()["blacklist"]}


# ---- журнал событий (его показывает приложение) ----
def log(text, level="info"):
    execute("INSERT INTO events(ts, level, text) VALUES(?, ?, ?)", (time.time(), level, text))
    execute("DELETE FROM events WHERE id < (SELECT MAX(id) - 500 FROM events)")
