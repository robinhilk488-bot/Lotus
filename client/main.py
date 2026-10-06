"""Lotus — приложение для ПК (на PySide6 / QtWebEngine).

Движок Chromium встроен в программу (QtWebEngine), поэтому ничего ставить не нужно
и ввод текста работает всегда. Интерфейс — HTML/CSS/JS из папки ui/.

Мостик к Python сделан через QWebChannel, но в страницу добавлен слой совместимости,
поэтому JS по-прежнему вызывает window.pywebview.api.* — файлы ui/ менять не нужно.
"""
import base64
import json
import os
import ssl
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
import urllib3
from requests.adapters import HTTPAdapter

from PySide6.QtCore import QFile, QIODevice, QObject, Qt, Slot
from PySide6.QtGui import QIcon
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMainWindow

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Флаги движка Chromium: лечат ввод с клавиатуры в собранном .exe на части машин.
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS",
                      "--disable-gpu --disable-gpu-compositing --no-sandbox")

APP_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
INSTALL_REPO = "robinhilk488-bot/Lotus"
CONFIG_DIR = Path.home() / ".kassa"
CONFIG_FILE = CONFIG_DIR / "config.json"


class PinnedAdapter(HTTPAdapter):
    """Принимает только сертификат с известным отпечатком SHA-256 (из ключа подключения)."""

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
    link = "".join(link.split())
    if link.startswith(("lotus_", "kassa_")):
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


class Api(QObject):
    """Методы доступны из JS как window.pywebview.api.* (через слой совместимости)."""

    def __init__(self):
        super().__init__()
        self.conn = None
        self.session = None

    @Slot(result=str)
    def install_command(self):
        raw = f"https://raw.githubusercontent.com/{INSTALL_REPO}/main/install.sh"
        return json.dumps(f"KASSA_REPO=https://github.com/{INSTALL_REPO}.git bash <(curl -s {raw})")

    @Slot(result=str)
    def load_saved(self):
        try:
            return json.dumps(json.loads(CONFIG_FILE.read_text("utf-8")))
        except Exception:
            return json.dumps(None)

    def _save(self, data):
        CONFIG_DIR.mkdir(exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False), "utf-8")

    @Slot(result=str)
    def forget(self):
        self.conn = self.session = None
        CONFIG_FILE.unlink(missing_ok=True)
        return json.dumps(True)

    @Slot(str, result=str)
    def connect(self, link):
        try:
            conn = parse_link(link)
        except ValueError as e:
            return json.dumps({"error": str(e)})
        s = requests.Session()
        s.verify = False
        s.mount("https://", PinnedAdapter(conn["fp"]))
        s.headers["Authorization"] = f"Bearer {conn['token']}"
        self.conn, self.session = conn, s
        res = self._request("GET", "/api/status")
        if "error" in res:
            self.conn = self.session = None
            return json.dumps(res)
        self._save({"link": conn["link"]})
        return json.dumps({"ok": True, "host": conn["host"], "port": conn["port"], "status": res})

    @Slot(str, str, str, result=str)
    def request(self, method, path, body_json):
        body = json.loads(body_json) if body_json else None
        return json.dumps(self._request(method, path, body))

    def _request(self, method, path, body=None):
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


SHIM = """
new QWebChannel(qt.webChannelTransport, function (channel) {
    var api = channel.objects.api;
    function call(name, args) {
        return new Promise(function (resolve) {
            var cb = function (res) { resolve(JSON.parse(res)); };
            api[name].apply(api, (args || []).concat(cb));
        });
    }
    window.pywebview = { api: {
        install_command: function () { return call("install_command", []); },
        load_saved:      function () { return call("load_saved", []); },
        forget:          function () { return call("forget", []); },
        connect:         function (link) { return call("connect", [link]); },
        request:         function (m, p, body) { return call("request", [m, p, body ? JSON.stringify(body) : ""]); }
    }};
    window.dispatchEvent(new Event("pywebviewready"));
});
"""


def _qwebchannel_js():
    # сначала локальная копия рядом с приложением (надёжнее всего), затем ресурс Qt
    local = APP_DIR / "ui" / "qwebchannel.js"
    if local.exists():
        return local.read_text("utf-8")
    f = QFile(":/qtwebchannel/qwebchannel.js")
    if f.open(QIODevice.ReadOnly):
        data = bytes(f.readAll().data()).decode("utf-8")
        f.close()
        return data
    return ""


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Lotus")
        self.resize(1280, 800)
        self.setMinimumSize(1040, 680)
        ico = APP_DIR / "kassa.ico"
        if ico.exists():
            self.setWindowIcon(QIcon(str(ico)))

        self.view = QWebEngineView()
        self.view.setFocusPolicy(Qt.StrongFocus)
        self.setCentralWidget(self.view)

        self.api = Api()
        self.channel = QWebChannel()
        self.channel.registerObject("api", self.api)
        self.view.page().setWebChannel(self.channel)

        boot = _qwebchannel_js() + SHIM

        def _on_loaded(ok):
            if ok:
                self.view.page().runJavaScript(boot)
                self.view.setFocus()  # отдаём фокус странице, иначе не принимается ввод

        self.view.loadFinished.connect(_on_loaded)
        self.view.setUrl("file:///" + str(APP_DIR / "ui" / "index.html").replace("\\", "/"))


if __name__ == "__main__":
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    QWebEngineProfile.defaultProfile()
    win = Window()
    win.show()
    win.raise_()
    win.activateWindow()
    sys.exit(app.exec())
