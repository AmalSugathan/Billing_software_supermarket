"""Password and session primitives. No plaintext password or session token is persisted."""

import base64
import hashlib
import hmac
import secrets

SCRYPT_N = 2**17
SCRYPT_R = 8
SCRYPT_P = 1


def _derive(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        maxmem=256 * 1024 * 1024,
        dklen=64,
    )


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _derive(password, salt)
    return (
        "scrypt$"
        + base64.b64encode(salt).decode("ascii")
        + "$"
        + base64.b64encode(digest).decode("ascii")
    )


def verify_password(password: str, encoded: str | None) -> bool:
    if encoded is None:
        _derive(password, bytes(16))  # Comparable work for nonexistent accounts.
        return False
    try:
        algorithm, salt_encoded, digest_encoded = encoded.split("$")
        salt = base64.b64decode(salt_encoded, validate=True)
        digest = base64.b64decode(digest_encoded, validate=True)
        if algorithm != "scrypt" or len(salt) != 16 or len(digest) != 64:
            return False
        return hmac.compare_digest(_derive(password, salt), digest)
    except ValueError, TypeError:
        return False


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def csrf_token(token: str) -> str:
    return hmac.new(token.encode("utf-8"), b"supermarket-csrf-v1", hashlib.sha256).hexdigest()


def new_session_token() -> str:
    return secrets.token_urlsafe(32)
