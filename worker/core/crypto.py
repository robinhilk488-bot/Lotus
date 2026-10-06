"""Шифрование golden_key. Ключ шифрования лежит в data/secret.key и не покидает сервер."""
from cryptography.fernet import Fernet

from .db import DATA_DIR

_KEY_FILE = DATA_DIR / "secret.key"
if not _KEY_FILE.exists():
    _KEY_FILE.write_bytes(Fernet.generate_key())
    _KEY_FILE.chmod(0o600)
_fernet = Fernet(_KEY_FILE.read_bytes())


def encrypt(text: str) -> str:
    return _fernet.encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet.decrypt(token.encode()).decode()
