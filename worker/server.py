"""Точка входа для собранного бинарника воркера (PyInstaller).

Запускает то же Flask-приложение (app:app) через gunicorn, вызванный программно,
с теми же HTTPS-сертификатами, что и раньше. Это сохраняет проверенную схему запуска,
но внутри одного исполняемого файла.
"""
import os
import multiprocessing


def main():
    port = os.environ.get("KASSA_PORT", "8765")
    data = os.environ.get("KASSA_DATA", "/data")
    cert = os.path.join(data, "certs", "cert.pem")
    key = os.path.join(data, "certs", "key.pem")

    from gunicorn.app.base import BaseApplication
    from app import app as flask_app

    class Worker(BaseApplication):
        def load_config(self):
            self.cfg.set("bind", f"0.0.0.0:{port}")
            self.cfg.set("workers", 1)
            self.cfg.set("threads", 8)
            self.cfg.set("certfile", cert)
            self.cfg.set("keyfile", key)
            self.cfg.set("timeout", 120)

        def load(self):
            return flask_app

    Worker().run()


if __name__ == "__main__":
    multiprocessing.freeze_support()  # нужно для PyInstaller
    main()
