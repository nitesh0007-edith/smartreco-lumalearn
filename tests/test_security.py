from app.security import hash_password, verify_password


def test_passwords_are_argon2_hashed():
    encoded = hash_password("a-long-test-password")
    assert encoded != "a-long-test-password"
    assert encoded.startswith("$argon2")
    assert verify_password("a-long-test-password", encoded)
    assert not verify_password("wrong-password", encoded)
