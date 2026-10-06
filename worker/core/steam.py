"""Steam: коды Steam Guard, вход в аккаунт и смена пароля.

Коды Steam Guard считаются по shared_secret из maFile (тот же алгоритм, что в мобильном приложении).
Вход и смена пароля повторяют запросы сайта Steam. Если Steam поменяет сайт, чинить нужно этот файл.
Смена пароля всегда перепроверяется входом с новым паролем.
"""
import base64
import hashlib
import hmac
import re
import secrets
import string
import struct
import time
from urllib.parse import parse_qs, urlparse

import requests
from cryptography.hazmat.primitives.asymmetric import padding, rsa

API = "https://api.steampowered.com/IAuthenticationService"
HELP = "https://help.steampowered.com/en/wizard"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
CODE_CHARS = "23456789BCDFGHJKMNPQRTVWXY"
ERESULT = {5: "неверный пароль", 63: "Steam требует подтверждение входа", 65: "неверный код Steam Guard",
           84: "слишком много попыток входа, Steam временно ограничил вход — подождите 30–60 минут",
           88: "неверный код Steam Guard (проверьте maFile)", 18: "аккаунт не найден"}


class SteamError(Exception):
    pass


# ---------------- Steam Guard ----------------
def guard_code(shared_secret: str, at: float | None = None) -> str:
    try:
        key = base64.b64decode(shared_secret, validate=True)
    except Exception:
        raise SteamError("shared_secret в maFile повреждён")
    if len(key) < 10:
        raise SteamError("shared_secret в maFile повреждён")
    msg = struct.pack(">Q", int((at or time.time()) // 30))
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    start = digest[19] & 0x0F
    value = struct.unpack(">I", digest[start:start + 4])[0] & 0x7FFFFFFF
    code = ""
    for _ in range(5):
        code += CODE_CHARS[value % len(CODE_CHARS)]
        value //= len(CODE_CHARS)
    return code


def parse_mafile(text: str) -> dict:
    """Достаёт shared_secret и имя аккаунта из maFile (JSON)."""
    import json
    try:
        data = json.loads(text)
    except ValueError:
        raise SteamError("Это не maFile: файл должен быть в формате JSON из Steam Desktop Authenticator")
    ss = data.get("shared_secret")
    if not ss:
        raise SteamError("В maFile нет shared_secret")
    try:
        guard_code(ss)
    except Exception:
        raise SteamError("shared_secret в maFile повреждён")
    return {"shared_secret": ss, "account_name": data.get("account_name", "")}


def new_password(length=14) -> str:
    alphabet = string.ascii_letters + string.digits
    while True:
        p = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(c.isdigit() for c in p) and any(c.isupper() for c in p) and any(c.islower() for c in p):
            return p


def _rsa_encrypt(text, mod_hex, exp_hex):
    key = rsa.RSAPublicNumbers(int(exp_hex, 16), int(mod_hex, 16)).public_key()
    return base64.b64encode(key.encrypt(text.encode(), padding.PKCS1v15())).decode()


# ---------------- вход ----------------
class SteamSession:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Origin": "https://steamcommunity.com",
                               "Referer": "https://steamcommunity.com/"})
        self.sessionid = secrets.token_hex(12)
        self.steamid = None

    def _api(self, method, data, get=False):
        url = f"{API}/{method}/v1"
        try:
            r = self.s.get(url, params=data, timeout=20) if get else self.s.post(url, data=data, timeout=20)
        except requests.RequestException as e:
            raise SteamError(f"Steam недоступен: {e}")
        er = int(r.headers.get("x-eresult", "1") or 1)
        if er != 1:
            raise SteamError(ERESULT.get(er, f"Steam отказал (код {er})"))
        try:
            return r.json().get("response", {})
        except ValueError:
            raise SteamError(f"Steam вернул неожиданный ответ на {method}")

    def login(self, login, password, shared_secret):
        key = self._api("GetPasswordRSAPublicKey", {"account_name": login}, get=True)
        if not key.get("publickey_mod"):
            raise SteamError("Steam не выдал ключ шифрования — проверьте логин")
        begin = self._api("BeginAuthSessionViaCredentials", {
            "account_name": login, "persistence": "1", "website_id": "Community",
            "encrypted_password": _rsa_encrypt(password, key["publickey_mod"], key["publickey_exp"]),
            "encryption_timestamp": key["timestamp"],
        })
        if not begin.get("client_id"):
            raise SteamError("Steam не начал вход — проверьте логин и пароль")
        self.steamid = begin["steamid"]
        confirmations = [c.get("confirmation_type") for c in begin.get("allowed_confirmations", [])]
        if 3 in confirmations:  # нужен код из мобильного приложения
            if not shared_secret:
                raise SteamError("Steam требует код Steam Guard, а maFile не загружен")
            self._api("UpdateAuthSessionWithSteamGuardCode", {
                "client_id": begin["client_id"], "steamid": self.steamid, "code_type": 3, "code": guard_code(shared_secret)})
        elif 2 in confirmations:
            raise SteamError("Steam требует код с почты — такой аккаунт нельзя обслуживать автоматически")
        for _ in range(8):
            poll = self._api("PollAuthSessionStatus", {"client_id": begin["client_id"], "request_id": begin["request_id"]})
            if poll.get("refresh_token"):
                break
            time.sleep(1.5)
        else:
            raise SteamError("Steam не подтвердил вход")
        fin = self.s.post("https://login.steampowered.com/jwt/finalizelogin", timeout=20, data={
            "nonce": poll["refresh_token"], "sessionid": self.sessionid, "redir": "https://steamcommunity.com/login/home/?goto="})
        try:
            transfers = fin.json().get("transfer_info", [])
        except ValueError:
            raise SteamError("Steam не завершил вход")
        for t in transfers:
            self.s.post(t["url"], data={**t.get("params", {}), "steamID": self.steamid}, timeout=20)
        for domain in ("steamcommunity.com", "store.steampowered.com", "help.steampowered.com"):
            self.s.cookies.set("sessionid", self.sessionid, domain=domain)
        return self.steamid

    def logout_everywhere(self):
        """Завершает все сессии аккаунта (как «выйти на всех устройствах»). Пароль не меняется."""
        # нужен access_token для веб-API; у community-сессии он в cookie steamLoginSecure,
        # поэтому используем страницу управления устройствами.
        r = self.s.post("https://store.steampowered.com/twofactor/manage_action", timeout=20,
                        data={"action": "deauthorize", "sessionid": self.sessionid})
        if r.status_code != 200:
            raise SteamError(f"Steam не завершил сессии (код {r.status_code})")

    # ---------------- смена пароля (мастер help.steampowered.com) ----------------
    def _help(self, path, data, get=False):
        data = {**data, "sessionid": self.sessionid, "wizard_ajax": 1, "gamepad": 0}
        url = f"{HELP}/{path}"
        try:
            r = self.s.get(url, params=data, timeout=20) if get else self.s.post(url, data=data, timeout=20)
            js = r.json()
        except (requests.RequestException, ValueError):
            raise SteamError(f"Steam не ответил на шаге смены пароля ({path})")
        if js.get("errorMsg"):
            raise SteamError(f"Steam: {js['errorMsg']}")
        return js

    def _help_rsa(self, login):
        r = self.s.post("https://help.steampowered.com/en/login/getrsakey/",
                        data={"sessionid": self.sessionid, "username": login}, timeout=20)
        try:
            js = r.json()
            return js["publickey_mod"], js["publickey_exp"], js["timestamp"]
        except (ValueError, KeyError):
            raise SteamError("Steam не выдал ключ для смены пароля")

    def change_password(self, login, old, new, shared_secret):
        r = self.s.get(f"{HELP}/HelpChangePassword?redir=store/account/", timeout=20)
        q = parse_qs(urlparse(r.url).query)
        s_param = (q.get("s") or [None])[0]
        if not s_param and (m := re.search(r"[?&]s=(\d+)", r.text)):
            s_param = m.group(1)
        account = (q.get("account") or [""])[0]
        if not account and (m := re.search(r"[?&]account=(\d+)", r.text)):
            account = m.group(1)
        if not s_param:
            raise SteamError("Steam не открыл смену пароля (нет входа на help.steampowered.com)")
        common = {"s": s_param, "reset": 1, "lost": 0, "issueid": 406}
        self._help("AjaxSendAccountRecoveryCode", {**common, "method": 8, "link": ""})
        self._help("AjaxVerifyAccountRecoveryCode", {**common, "method": 8, "code": guard_code(shared_secret)}, get=True)
        self._help("AjaxAccountRecoveryGetNextStep", {**common, "account": account}, get=True)
        mod, exp, ts = self._help_rsa(login)
        self._help("AjaxAccountRecoveryVerifyPassword", {"s": s_param, "lost": 2, "reset": 1,
                                                         "password": _rsa_encrypt(old, mod, exp), "rsatimestamp": ts})
        avail = self._help("AjaxCheckPasswordAvailable", {"password": new})
        if avail.get("available") is False:
            raise SteamError("Steam не принял новый пароль")
        mod, exp, ts = self._help_rsa(login)
        self._help("AjaxAccountRecoveryChangePassword", {"s": s_param, "account": account,
                                                         "password": _rsa_encrypt(new, mod, exp), "rsatimestamp": ts})


def check_login(login, password, shared_secret):
    SteamSession().login(login, password, shared_secret)


def logout_everywhere(login, password, shared_secret):
    """Входит в аккаунт и завершает все его сессии, не меняя пароль."""
    sess = SteamSession()
    sess.login(login, password, shared_secret)
    sess.logout_everywhere()


def change_password(login, old, new, shared_secret):
    """Меняет пароль и перепроверяет. Возвращает 'new', если работает новый пароль.
    Бросает SteamError; в тексте сказано, какой пароль сейчас рабочий, если это удалось выяснить."""
    try:
        sess = SteamSession()
        sess.login(login, old, shared_secret)
        sess.change_password(login, old, new, shared_secret)
    except SteamError as first:
        error = first
    else:
        error = None
    time.sleep(3)
    try:  # проверка: какой пароль реально работает
        check_login(login, new, shared_secret)
        return "new"
    except SteamError:
        pass
    if error:
        try:
            check_login(login, old, shared_secret)
            raise SteamError(f"{error}. Пароль НЕ изменён, работает старый")
        except SteamError as e2:
            if "НЕ изменён" in str(e2):
                raise
            raise SteamError(f"{error}. Не удалось войти ни со старым, ни с новым паролем — проверьте аккаунт вручную")
    raise SteamError("Steam сообщил об успехе, но вход с новым паролем не проходит — проверьте аккаунт вручную")
