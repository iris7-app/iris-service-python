"""Password hashing tests."""

from __future__ import annotations

from iris_service.auth.passwords import hash_password, verify_password


def test_hash_then_verify_succeeds() -> None:
    hashed = hash_password("super-secret-password")
    assert verify_password("super-secret-password", hashed)


def test_verify_rejects_wrong_password() -> None:
    hashed = hash_password("secret")
    assert not verify_password("wrong", hashed)


def test_hash_is_not_plaintext() -> None:
    plain = "my-password"
    hashed = hash_password(plain)
    assert plain not in hashed
    # bcrypt format starts with $2b$
    assert hashed.startswith("$2b$")


def test_two_hashes_of_same_password_differ() -> None:
    """bcrypt uses random salt — same input → different outputs (both verify)."""
    h1 = hash_password("same")
    h2 = hash_password("same")
    assert h1 != h2
    assert verify_password("same", h1)
    assert verify_password("same", h2)
