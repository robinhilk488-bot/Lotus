"""Работа с FunPay через golden_key.

У FunPay нет официального API, поэтому используются те же запросы, что делает сайт в браузере.
Если FunPay поменяет сайт, чинить нужно только этот файл. Любая ошибка разбора
превращается в FunPayError с понятным текстом и попадает в журнал и в Telegram.
"""
import json
import re
import time
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

BASE = "https://funpay.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
XHR = {"accept": "*/*", "x-requested-with": "XMLHttpRequest",
       "content-type": "application/x-www-form-urlencoded; charset=UTF-8"}

MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
REVIEW_RE = re.compile(r"(?:отзыв к заказу|feedback to the order|відгук до замовлення)\s*#([A-Z0-9]{6,12})", re.I)
CONFIRM_RE = re.compile(r"(?:подтвердил выполнение заказа|closed the order|підтвердив виконання замовлення)\s*#([A-Z0-9]{6,12})", re.I)


class FunPayError(Exception):
    pass


def _money(text: str):
    """'1 234,50 ₽' -> (1234.5, '₽')"""
    text = (text or "").replace("\xa0", " ").strip()
    num = re.sub(r"[^\d.,]", "", text).replace(",", ".")
    cur = re.sub(r"[\d\s.,]", "", text) or "₽"
    try:
        return float(num), cur
    except ValueError:
        return 0.0, cur


def _parse_date(text: str) -> datetime:
    """Понимает 'сегодня, 14:05', 'вчера, 09:10', '12 сентября, 14:00', '12 сентября 2025, 14:00'."""
    text = (text or "").strip().lower()
    now = datetime.now()
    m = re.search(r"(\d{1,2}):(\d{2})", text)
    hh, mm = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    if text.startswith(("сегодня", "today")):
        d = now
    elif text.startswith(("вчера", "yesterday")):
        d = now - timedelta(days=1)
    else:
        m = re.match(r"(\d{1,2})\s+([a-zа-яё]+)(?:\s+(\d{4}))?", text)
        if not m or m.group(2) not in MONTHS:
            return now
        year = int(m.group(3)) if m.group(3) else now.year
        d = datetime(year, MONTHS[m.group(2)], int(m.group(1)))
    return d.replace(hour=hh, minute=mm, second=0, microsecond=0)


def _text(el):
    return el.get_text(" ", strip=True) if el else ""


def parse_proxy(raw: str):
    """Разбирает строку прокси в формат для requests.
    Поддержка: host:port:user:pass, host:port, protocol://user:pass@host:port.
    Возвращает dict {"http": url, "https": url} или None."""
    raw = (raw or "").strip()
    if not raw:
        return None
    # уже со схемой: socks5://user:pass@host:port или http://host:port
    if "://" in raw:
        url = raw
    else:
        parts = raw.split(":")
        if len(parts) == 2:           # host:port
            url = f"http://{parts[0]}:{parts[1]}"
        elif len(parts) == 4:         # host:port:user:pass
            host, port, user, pwd = parts
            url = f"http://{user}:{pwd}@{host}:{port}"
        else:
            return None
    return {"http": url, "https": url}


class FunPayAccount:
    def __init__(self, golden_key: str, proxy: str = ""):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self.s.cookies.set("golden_key", golden_key.strip(), domain="funpay.com")
        p = parse_proxy(proxy)
        if p:
            self.s.proxies.update(p)
        self.user_id = None
        self.username = None
        self.csrf = None
        self._game_ids = {}  # подкатегория -> game_id для поднятия

    # ---------- базовые запросы ----------
    def _get(self, path, xhr=False):
        try:
            r = self.s.get(BASE + path, headers=XHR if xhr else None, timeout=20)
        except requests.RequestException as e:
            raise FunPayError(f"FunPay недоступен: {e}")
        if r.status_code != 200:
            raise FunPayError(f"FunPay ответил {r.status_code} на {path}")
        return r

    def _soup(self, path):
        return BeautifulSoup(self._get(path).text, "html.parser")

    def _post(self, path, data, retry=True):
        if not self.csrf:
            self.get_me()
        try:
            r = self.s.post(BASE + path, headers=XHR, data={**data, "csrf_token": self.csrf}, timeout=20)
        except requests.RequestException as e:
            raise FunPayError(f"FunPay недоступен: {e}")
        if r.status_code in (400, 403) and retry:  # истёк csrf — обновляем сессию один раз
            self.get_me()
            return self._post(path, data, retry=False)
        if r.status_code != 200:
            raise FunPayError(f"FunPay ответил {r.status_code} на {path}")
        try:
            return r.json()
        except ValueError:
            raise FunPayError(f"FunPay вернул не JSON на {path}")

    def _runner(self, objects, request=None):
        return self._post("/runner/", {
            "objects": json.dumps(objects) if objects else "",
            "request": json.dumps(request) if request else "false",
        })

    # ---------- аккаунт ----------
    def get_me(self) -> dict:
        """Проверяет ключ и возвращает имя, id и баланс."""
        soup = self._soup("/")
        body = soup.find("body")
        app_data = json.loads(body.get("data-app-data", "{}")) if body else {}
        self.user_id = app_data.get("userId") or None
        self.csrf = app_data.get("csrf-token")
        if not self.user_id:
            raise FunPayError("golden_key недействителен: FunPay не узнал аккаунт")
        self.username = _text(soup.select_one(".user-link-name")) or f"id{self.user_id}"
        bal_el = soup.select_one(".badge-balance")
        balance, currency = _money(bal_el.get_text()) if bal_el else (0.0, "₽")
        avatar = ""
        ava_el = soup.select_one(".user-link-photo, .avatar-photo")
        if ava_el:
            style = ava_el.get("style", "")
            if m := re.search(r"url\(['\"]?([^'\")]+)", style):
                avatar = m.group(1)
            elif ava_el.get("data-src"):
                avatar = ava_el["data-src"]
        return {"user_id": self.user_id, "username": self.username, "balance": balance, "currency": currency, "avatar": avatar}

    # ---------- продажи ----------
    def get_sales(self) -> list[dict]:
        """Первая страница продаж (/orders/trade)."""
        soup = self._soup("/orders/trade")
        out = []
        for row in soup.select("a.tc-item"):
            oid = row.select_one(".tc-order")
            if not oid:
                continue
            classes = row.get("class", [])
            status = "refunded" if "warning" in classes else "paid" if "info" in classes else "closed"
            amount, currency = _money(_text(row.select_one(".tc-price")))
            buyer_id = None
            ava = row.select_one(".avatar-photo[data-href]")
            if ava and (m := re.search(r"/users/(\d+)", ava["data-href"])):
                buyer_id = int(m.group(1))
            out.append({
                "id": _text(oid).lstrip("#"),
                "description": _text(row.select_one(".order-desc div")),
                "buyer": _text(row.select_one(".media-user-name")),
                "buyer_id": buyer_id,
                "amount": amount,
                "currency": currency,
                "status": status,
                "ts": _parse_date(_text(row.select_one(".tc-date-time"))).timestamp(),
            })
        return out

    def chat_with(self, buyer_id: int) -> str:
        """Имя чата с пользователем. FunPay называет личные чаты users-<меньший id>-<больший id>."""
        if not self.user_id:
            self.get_me()
        a, b = sorted((int(self.user_id), int(buyer_id)))
        return f"users-{a}-{b}"

    # ---------- чаты ----------
    def get_chats(self) -> list[dict]:
        if not self.user_id:
            self.get_me()
        res = self._runner([{"type": "chat_bookmarks", "id": self.user_id, "tag": "00000000", "data": False}])
        try:
            data = res["objects"][0]["data"]
            html = data["html"] if isinstance(data, dict) else data
        except (KeyError, IndexError, TypeError):
            raise FunPayError("Не удалось прочитать список чатов FunPay")
        out = []
        for a in BeautifulSoup(html, "html.parser").select("a.contact-item"):
            if not a.get("data-id"):
                continue
            avatar = ""
            ava_el = a.select_one(".avatar-photo")
            if ava_el:
                # аватар может быть в style="background-image:url(...)" или в data-src
                style = ava_el.get("style", "")
                if m := re.search(r"url\(['\"]?([^'\")]+)", style):
                    avatar = m.group(1)
                elif ava_el.get("data-src"):
                    avatar = ava_el["data-src"]
            out.append({
                "id": a["data-id"],
                "name": _text(a.select_one(".media-user-name")),
                "last_msg_id": int(a.get("data-node-msg") or 0),
                "last_text": _text(a.select_one(".contact-item-message")),
                "unread": "unread" in a.get("class", []),
                "avatar": avatar,
            })
        return out

    def get_history(self, chat_id) -> list[dict]:
        r = self._get(f"/chat/history?node={chat_id}&last_message=9999999999999", xhr=True)
        try:
            msgs = r.json()["chat"]["messages"]
        except (ValueError, KeyError, TypeError):
            raise FunPayError("Не удалось прочитать историю чата FunPay")
        out, last_author = [], None
        for m in msgs:
            soup = BeautifulSoup(m.get("html", ""), "html.parser")
            author = _text(soup.select_one(".chat-msg-author-link")) or last_author
            last_author = author
            text = _text(soup.select_one(".chat-msg-text")) or _text(soup.select_one(".message-text"))
            if not text and soup.select_one("img"):
                text = "[изображение]"
            date_el = soup.select_one(".chat-msg-date")
            ts = time.time()
            if date_el and date_el.get("title"):
                ts = _parse_date(date_el["title"]).timestamp()
            author_id = int(m.get("author") or 0)
            out.append({"id": int(m["id"]), "author_id": author_id, "author": "FunPay" if author_id == 0 else author,
                        "text": text, "ts": ts, "mine": author_id == self.user_id})
        return out

    def send_message(self, chat_id, text: str):
        res = self._runner([], {"action": "chat_message",
                                "data": {"node": chat_id, "last_message": -1, "content": text}})
        resp = res.get("response")
        if not resp:
            raise FunPayError("FunPay не принял сообщение")
        if resp.get("error"):
            raise FunPayError(f"FunPay не принял сообщение: {resp['error']}")

    def send_image(self, chat_id, image_bytes: bytes, filename: str = "image.png"):
        """Отправляет изображение в чат. Сначала загружает картинку, затем шлёт как сообщение."""
        if not self.csrf:
            self._runner([])  # обновит csrf и сессию
        # 1) загрузка картинки — FunPay возвращает её id
        r = self.s.post(f"{BASE}/file/addChatImage", timeout=40,
                        headers={**XHR}, data={"csrf_token": self.csrf},
                        files={"file": (filename, image_bytes, "image/png")})
        if r.status_code != 200:
            raise FunPayError(f"FunPay не принял изображение (код {r.status_code})")
        try:
            image_id = r.json().get("fileId") or r.json().get("imageId")
        except ValueError:
            raise FunPayError("FunPay вернул неожиданный ответ при загрузке изображения")
        if not image_id:
            raise FunPayError("FunPay не вернул id изображения")
        # 2) отправка сообщения с картинкой
        res = self._runner([], {"action": "chat_message",
                                "data": {"node": chat_id, "last_message": -1, "content": "", "image_id": image_id}})
        resp = res.get("response")
        if not resp or resp.get("error"):
            raise FunPayError(f"FunPay не принял изображение: {resp.get('error') if resp else 'нет ответа'}")

    # ---------- отзывы ----------
    def get_review(self, order_id: str) -> dict | None:
        soup = self._soup(f"/orders/{order_id}/")
        box = soup.select_one(".review-item")
        if not box:
            return None
        rating = None
        r_el = box.select_one("[class*='rating']")
        for el in box.select("[class*='rating']"):
            for c in el.get("class", []):
                if m := re.fullmatch(r"rating(\d)", c):
                    rating = int(m.group(1))
        return {"rating": rating, "text": _text(box.select_one(".review-item-text")) or _text(r_el)}

    def reply_to_review(self, order_id: str, text: str):
        """Публикует ответ продавца под отзывом покупателя к заказу."""
        # нужен csrf-токен со страницы заказа
        soup = self._soup(f"/orders/{order_id}/")
        token = ""
        for inp in soup.select("input[name='csrf_token']"):
            token = inp.get("value", "")
            if token:
                break
        if not token:
            m = re.search(r'"csrf-token"\s*content="([^"]+)"', str(soup)) or re.search(r'data-token="([^"]+)"', str(soup))
            token = m.group(1) if m else ""
        r = self.s.post(f"{BASE}/orders/review", timeout=20, data={
            "authorID": self.user_id, "text": text, "rating": "",
            "csrf_token": token, "orderId": order_id,
        }, headers={"X-Requested-With": "XMLHttpRequest"})
        if r.status_code != 200:
            raise FunPayError(f"FunPay не принял ответ на отзыв (код {r.status_code})")
        try:
            data = r.json()
            if data.get("error"):
                raise FunPayError(str(data.get("msg") or data["error"]))
        except ValueError:
            pass

    # ---------- заказ ----------
    def get_order_quantity(self, order_id: str):
        """Количество из страницы заказа. None, если прочитать не удалось — тогда ничего не выдаём наугад."""
        soup = self._soup(f"/orders/{order_id}/")
        for item in soup.select(".param-item"):
            title = _text(item.select_one("h5")).lower()
            if any(w in title for w in ("количество", "amount", "кількість")):
                value = _text(item).replace(_text(item.select_one("h5")), "", 1)
                m = re.search(r"\d+", value.replace(" ", ""))
                if m and int(m.group()) > 0:
                    return int(m.group())
        return None

    # ---------- включение и выключение лота ----------
    def _offer_form(self, offer_id) -> dict:
        soup = self._soup(f"/lots/offerEdit?offer={offer_id}")
        form = soup.select_one("form.form-offer-editor") or soup.find("form", action=re.compile("offerSave"))
        if not form:
            raise FunPayError(f"Не удалось открыть лот {offer_id}: проверьте ID и что лот принадлежит этому аккаунту")
        fields = {}
        for inp in form.find_all("input"):
            name, kind = inp.get("name"), (inp.get("type") or "text").lower()
            if not name or kind in ("submit", "button", "file"):
                continue
            if kind in ("checkbox", "radio"):
                if inp.has_attr("checked"):
                    fields[name] = inp.get("value", "on")
            else:
                fields[name] = inp.get("value", "")
        for ta in form.find_all("textarea"):
            if ta.get("name"):
                fields[ta["name"]] = ta.text
        for sel in form.find_all("select"):
            if sel.get("name"):
                opt = sel.find("option", selected=True) or sel.find("option")
                fields[sel["name"]] = opt.get("value", "") if opt else ""
        return fields

    def get_lot_info(self, offer_id) -> dict:
        """Название, описание и цена лота — для ИИ-ответов по конкретному товару."""
        f = self._offer_form(offer_id)
        def pick(*keys):
            for k in keys:
                for name, val in f.items():
                    if k in name.lower() and val:
                        return val
            return ""
        return {
            "title": pick("summary", "title", "short"),
            "description": pick("desc"),
            "price": pick("price"),
        }

    def lot_is_active(self, offer_id) -> bool:
        return "active" in self._offer_form(offer_id)

    def set_lot_active(self, offer_id, active: bool):
        """Включает или выключает лот и перепроверяет, что FunPay действительно применил изменение."""
        fields = self._offer_form(offer_id)
        if ("active" in fields) == active:
            return
        if active:
            fields["active"] = "on"
        else:
            fields.pop("active", None)
        fields.pop("csrf_token", None)
        res = self._post("/lots/offerSave", fields)
        if res.get("error") or res.get("errors"):
            raise FunPayError(f"FunPay не сохранил лот {offer_id}: {res.get('msg') or res.get('errors') or res.get('error')}")
        if self.lot_is_active(offer_id) != active:
            raise FunPayError(f"FunPay не {'включил' if active else 'выключил'} лот {offer_id}")

    @staticmethod
    def offer_link(offer_id):
        return f"{BASE}/lots/offer?id={offer_id}"

    # ---------- поднятие лотов ----------
    def my_subcategories(self) -> list[int]:
        if not self.user_id:
            self.get_me()
        soup = self._soup(f"/users/{self.user_id}/")
        ids = set()
        for a in soup.select(".offer a[href], .offer-list-title a[href]"):
            if m := re.search(r"/lots/(\d+)/", a["href"]):
                ids.add(int(m.group(1)))
        return sorted(ids)

    def _game_id(self, sub: int):
        if sub not in self._game_ids:
            btn = self._soup(f"/lots/{sub}/trade").select_one(".js-lot-raise")
            self._game_ids[sub] = int(btn["data-game"]) if btn and btn.get("data-game") else None
        return self._game_ids[sub]

    def raise_all(self) -> list[str]:
        """Поднимает все лоты. Возвращает строки с результатом по каждой игре."""
        games = {}
        for sub in self.my_subcategories():
            gid = self._game_id(sub)
            if gid:
                games.setdefault(gid, []).append(sub)
        if not games:
            return ["Лотов для поднятия не найдено"]
        report = []
        for gid, subs in games.items():
            data = {"game_id": gid, "node_id": subs[0]}
            res = self._post("/lots/raise", data)
            if res.get("modal"):  # FunPay просит выбрать подкатегории — выбираем все свои
                data["node_ids[]"] = subs
                res = self._post("/lots/raise", data)
            msg = re.sub(r"<[^>]+>", "", str(res.get("msg") or "")).strip()
            report.append(("Не поднято: " if res.get("error") else "") + (msg or "Лоты подняты"))
        return report
