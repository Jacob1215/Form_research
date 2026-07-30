"""API 密钥加解密：基于 cryptography Fernet 对称加密。"""
from cryptography.fernet import Fernet
from .config import get_encrypt_key

_fernet = Fernet(get_encrypt_key())


def encrypt(plaintext: str) -> str:
    """加密明文，返回可存储的字符串。"""
    if not plaintext:
        return ""
    return _fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(ciphertext: str) -> str:
    """解密密文，失败返回空串。"""
    if not ciphertext:
        return ""
    try:
        return _fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except Exception:
        return ""


def mask(api_key: str) -> str:
    """脱敏显示：保留前3位与后4位，中间用 **** 替换。"""
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "****"
    return f"{api_key[:3]}****{api_key[-4:]}"
