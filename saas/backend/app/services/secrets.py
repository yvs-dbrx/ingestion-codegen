"""Encryption of stored credentials — the one place that knows how secrets are protected.

Stand-in for the secrets vault in PRD Section 8: everything else calls encrypt()/decrypt(),
so swapping in AWS Secrets Manager / Vault later means changing this file only.
"""
from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class CannotDecrypt(Exception):
    """Stored ciphertext isn't readable with the current key (the key was changed or lost)."""


def _fernet() -> Fernet:
    return Fernet(get_settings().credential_encryption_key.encode("utf-8"))


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise CannotDecrypt("Stored credentials can't be decrypted with the current key") from exc
