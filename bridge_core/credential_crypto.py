from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class CredentialEncryptionError(ValueError):
    """Raised when an encrypted integration credential is invalid."""


def _get_cipher() -> Fernet:
    try:
        key = settings.ODOO_CREDENTIAL_ENCRYPTION_KEY.encode("ascii")
        return Fernet(key)
    except (AttributeError, UnicodeEncodeError, ValueError) as exc:
        raise CredentialEncryptionError(
            "Odoo credential encryption key is invalid."
        ) from exc


def encrypt_credential(plaintext: str) -> str:
    if not isinstance(plaintext, str):
        raise TypeError("Credential plaintext must be a string.")

    if not plaintext:
        return ""

    ciphertext = _get_cipher().encrypt(
        plaintext.encode("utf-8")
    )

    return ciphertext.decode("ascii")


def decrypt_credential(ciphertext: str) -> str:
    if not isinstance(ciphertext, str):
        raise TypeError("Credential ciphertext must be a string.")

    if not ciphertext:
        return ""

    try:
        plaintext = _get_cipher().decrypt(
            ciphertext.encode("ascii")
        )
    except (
        InvalidToken,
        UnicodeEncodeError,
        UnicodeDecodeError,
        ValueError,
    ) as exc:
        raise CredentialEncryptionError(
            "Stored Odoo credential could not be decrypted."
        ) from exc

    return plaintext.decode("utf-8")
