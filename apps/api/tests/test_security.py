import pytest

from supermarket import dev_database
from supermarket.security import (
    csrf_token,
    hash_password,
    new_session_token,
    token_digest,
    verify_password,
)


def test_password_hashes_are_salted_and_verify_without_plaintext() -> None:
    password = "a sufficiently long password"
    first, second = hash_password(password), hash_password(password)
    assert first != second
    assert password not in first
    assert verify_password(password, first)
    assert not verify_password(password + "x", first)
    assert not verify_password(password, None)


@pytest.mark.parametrize("encoded", ["invalid", "scrypt$!$!", "other$YQ==$YQ=="])
def test_malformed_hash_fails_closed(encoded: str) -> None:
    assert not verify_password("a-long-password", encoded)


def test_session_and_csrf_tokens_are_distinct_and_unpredictable() -> None:
    first, second = new_session_token(), new_session_token()
    assert first != second
    assert len(token_digest(first)) == 64
    assert csrf_token(first) != token_digest(first)
    assert csrf_token(first) != csrf_token(second)
    assert csrf_token(first) == csrf_token(first)


def test_development_role_helper_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError, match="development-only"):
        dev_database.main()
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("MIGRATION_DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="MIGRATION_DATABASE_URL"):
        dev_database.main()
