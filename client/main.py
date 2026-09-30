"""Lotus — приложение для ПК.

Окно рисуется на HTML/CSS (папка ui/), а все запросы к воркеру идут через Python:
так проверяется отпечаток сертификата сервера и токен не светится в браузерном коде.
"""
import base64
import json
import ssl
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
import urllib3
import webview
from requests.adapters import HTTPAdapter

# Проверку сертификата делаем сами — по отпечатку из ссылки (см. PinnedAdapter).
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

APP_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
# Команда установки сервера. При сборке через GitHub Actions подставляется автоматически.
INSTALL_REPO = "robinhilk488-bot/Lotus"
CONFIG_DIR = Path.home() / ".kassa"
CONFIG_FILE = CONFIG_DIR / "config.json"


class PinnedAdapter(HTTPAdapter):
    """Принимает только сертификат с заранее известным отпечатком SHA-256 (из ссылки подключения)."""

    def __init__(self, fingerprint, **kw):
        self.fingerprint = fingerprint
        super().__init__(**kw)

    def init_poolmanager(self, *args, **kw):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kw["ssl_context"] = ctx
        kw["assert_fingerprint"] = self.fingerprint
        super().init_poolmanager(*args, **kw)

    def cert_verify(self, conn, url, verify, cert):
        conn.cert_reqs = "CERT_NONE"
        conn.ca_certs = conn.ca_cert_dir = None

    def build_connection_pool_key_attributes(self, request, verify, cert=None):
        host_params, pool_kwargs = super().build_connection_pool_key_attributes(request, False, cert)
        pool_kwargs["cert_reqs"] = "CERT_NONE"
        return host_params, pool_kwargs


BAD_KEY = "Это не ключ подключения. Скопируйте целиком строку lotus_… из конца установки на сервере."


def parse_link(link: str) -> dict:
    """Принимает ключ подключения lotus_… (его выдаёт установщик) или старую ссылку kassa://…"""
    link = "".join(link.split())
    if link.startswith(("lotus_", "kassa_")):  # kassa_ — старые ключи, тоже принимаем
        try:
            raw = link.split("_", 1)[1]
            raw += "=" * (-len(raw) % 4)
            host, port, token, fp = base64.urlsafe_b64decode(raw).decode().split("|")
            port = int(port)
        except Exception:
            raise ValueError(BAD_KEY)
        if not (host and token and len(fp) == 64):
            raise ValueError(BAD_KEY)
        return {"host": host, "port": port, "token": token, "fp": fp.lower(), "link": link}
    u = urlparse(link)
    q = parse_qs(u.query)
    if u.scheme != "kassa" or not u.hostname or "token" not in q or "fp" not in q:
        raise ValueError(BAD_KEY)
    return {"host": u.hostname, "port": u.port or 8765, "token": q["token"][0],
            "fp": q["fp"][0].replace(":", "").lower(), "link": link}


class Api:
    """Эти методы доступны из JS как window.pywebview.api.*"""

    def __init__(self):
        self.conn = None
        self.session = None

    def install_command(self):
        raw = f"https://raw.githubusercontent.com/{INSTALL_REPO}/main/install.sh"
        return f"KASSA_REPO=https://github.com/{INSTALL_REPO}.git bash <(curl -s {raw})"

    # --- сохранённое подключение ---
    def load_saved(self):
        try:
            return json.loads(CONFIG_FILE.read_text("utf-8"))
        except Exception:
            return None

    def _save(self, data):
        CONFIG_DIR.mkdir(exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False), "utf-8")

    def forget(self):
        self.conn = self.session = None
        CONFIG_FILE.unlink(missing_ok=True)
        return True

    # --- подключение ---
    def connect(self, link: str):
        try:
            conn = parse_link(link)
        except ValueError as e:
            return {"error": str(e)}
        s = requests.Session()
        s.verify = False  # цепочку CA не проверяем: вместо неё сверяется отпечаток сертификата
        s.mount("https://", PinnedAdapter(conn["fp"]))
        s.headers["Authorization"] = f"Bearer {conn['token']}"
        self.conn, self.session = conn, s
        res = self.request("GET", "/api/status")
        if "error" in res:
            self.conn = self.session = None
            return res
        self._save({"link": conn["link"]})
        return {"ok": True, "host": conn["host"], "port": conn["port"], "status": res}

    def request(self, method, path, body=None):
        if not self.session:
            return {"error": "Нет подключения к серверу"}
        url = f"https://{self.conn['host']}:{self.conn['port']}{path}"
        try:
            r = self.session.request(method, url, json=body, timeout=30)
        except requests.exceptions.SSLError:
            return {"error": "Сервер не совпадает с ключом подключения. Возьмите новый ключ: запустите установщик на сервере ещё раз."}
        except requests.exceptions.ConnectionError:
            return {"error": "Сервер недоступен. Проверьте IP, порт и что воркер запущен."}
        except requests.exceptions.Timeout:
            return {"error": "Сервер не ответил за 30 секунд."}
        try:
            data = r.json()
        except ValueError:
            return {"error": f"Сервер вернул неожиданный ответ ({r.status_code})"}
        if r.status_code >= 400 and isinstance(data, dict) and "error" not in data:
            data = {"error": f"Ошибка сервера ({r.status_code})"}
        return data if isinstance(data, dict) else {"data": data}


if __name__ == "__main__":
    webview.create_window(
        "Lotus", str(APP_DIR / "ui" / "index.html"), js_api=Api(),
        width=1280, height=800, min_size=(1040, 680), background_color="#101A2E",
    )
    webview.start()
