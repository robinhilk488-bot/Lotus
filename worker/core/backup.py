"""Резервные копии базы.

Раз в сутки делает копию базы (аккаунты, продажи, аренда, настройки, подписка) в data/backups.
Копия создаётся штатным механизмом SQLite (целостная, даже если в этот момент идёт запись).
Файл-ключ шифрования (secret.key) в копии НЕ включается намеренно: копию можно хранить где угодно,
и без ключа с сервера из неё нельзя достать golden_key. Храним последние N копий, старые удаляются.

Восстановление (вручную на сервере):
  docker compose down
  cp data/backups/ИМЯ_КОПИИ.db data/kassa.db
  docker compose up -d
"""
import sqlite3
import threading
import time
from pathlib import Path

from . import db

BACKUP_DIR = db.DATA_DIR / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
KEEP = 14                      # сколько копий хранить
EVERY = 24 * 3600              # раз в сутки


def make_backup() -> str:
    """Создаёт целостную копию базы. Возвращает путь к файлу."""
    name = time.strftime("kassa-%Y%m%d-%H%M%S.db")
    dest = BACKUP_DIR / name
    n = 1
    while dest.exists():  # на случай нескольких копий в одну секунду
        dest = BACKUP_DIR / name.replace(".db", f"-{n}.db")
        n += 1
    name = dest.name
    src = sqlite3.connect(db.DB_PATH)
    try:
        out = sqlite3.connect(dest)
        with out:
            src.backup(out)  # атомарная копия средствами SQLite
        out.close()
    finally:
        src.close()
    _rotate()
    db.log(f"Создана резервная копия базы: {name}")
    return str(dest)


def _rotate():
    backups = sorted(BACKUP_DIR.glob("kassa-*.db"))
    for old in backups[:-KEEP]:
        try:
            old.unlink()
        except OSError:
            pass


def list_backups():
    out = []
    for f in sorted(BACKUP_DIR.glob("kassa-*.db"), reverse=True):
        st = f.stat()
        out.append({"name": f.name, "size": st.st_size, "ts": st.st_mtime})
    return out


def _last_backup_ts():
    files = list(BACKUP_DIR.glob("kassa-*.db"))
    return max((f.stat().st_mtime for f in files), default=0)


def _loop():
    # первая копия вскоре после старта, затем раз в сутки
    time.sleep(120)
    while True:
        try:
            if time.time() - _last_backup_ts() >= EVERY:
                make_backup()
        except Exception as e:
            db.log(f"Не удалось создать резервную копию: {e}", "error")
        time.sleep(3600)


def start():
    threading.Thread(target=_loop, daemon=True, name="backup").start()
