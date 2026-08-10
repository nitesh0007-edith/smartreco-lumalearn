import hashlib

from app.config import Settings
from app.security import hash_password, verify_password


def test_passwords_are_argon2_hashed():
    encoded = hash_password("a-long-test-password")
    assert encoded != "a-long-test-password"
    assert encoded.startswith("$argon2")
    assert verify_password("a-long-test-password", encoded)
    assert not verify_password("wrong-password", encoded)


def test_admin_email_hash_allowlist_is_normalized():
    email = "owner@example.com"
    digest = hashlib.sha256(email.encode()).hexdigest()
    settings = Settings(admin_email_hashes=f"invalid, {digest.upper()}")

    assert settings.is_admin_email(" Owner@Example.com ")
    assert not settings.is_admin_email("someone-else@example.com")
