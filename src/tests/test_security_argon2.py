"""
Tests for argon2id password hashing and transparent legacy SHA-256 migration.
Covers task 1.1 — Phase 1 security hardening.
"""
import hashlib
import os
import secrets
import sys

import pytest

# Set required env vars before any application modules are imported.
# These mirror what conftest.py does for the broader test suite.
os.environ.setdefault("SECRET_KEY", "x" * 64)
os.environ.setdefault("DATABASE_URL", "sqlite:///test.db")
os.environ.setdefault("ALLOWED_ORIGINS", '["http://localhost:3000"]')
os.environ.setdefault("DEBUG", "true")

# Ensure src/ is on the path when run directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.security import (
    get_password_hash,
    verify_password,
    verify_and_rehash,
    _is_legacy_sha256,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_legacy_hash(password: str) -> str:
    """Reproduce the old salt$sha256hex format."""
    salt = secrets.token_hex(16)
    digest = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}${digest}"


# ---------------------------------------------------------------------------
# get_password_hash — produces argon2id output
# ---------------------------------------------------------------------------

class TestGetPasswordHash:
    def test_returns_argon2id_prefix(self):
        h = get_password_hash("S3cur3P@ssw0rd!")
        assert h.startswith("$argon2id$"), f"Expected argon2id hash, got: {h[:20]}"

    def test_different_hashes_for_same_password(self):
        h1 = get_password_hash("S3cur3P@ssw0rd!")
        h2 = get_password_hash("S3cur3P@ssw0rd!")
        assert h1 != h2, "Hashes should differ due to random salt"

    def test_hash_is_string(self):
        assert isinstance(get_password_hash("abc"), str)


# ---------------------------------------------------------------------------
# verify_password — backward-compatible bool interface
# ---------------------------------------------------------------------------

class TestVerifyPassword:
    def test_correct_argon2id_password(self):
        h = get_password_hash("CorrectH0rse#")
        assert verify_password("CorrectH0rse#", h) is True

    def test_wrong_argon2id_password(self):
        h = get_password_hash("CorrectH0rse#")
        assert verify_password("WrongPassword!", h) is False

    def test_correct_legacy_sha256_password(self):
        pw = "Leg@cy1234Pass!"
        legacy = _make_legacy_hash(pw)
        assert verify_password(pw, legacy) is True

    def test_wrong_legacy_sha256_password(self):
        legacy = _make_legacy_hash("OriginalP@ss1")
        assert verify_password("WrongP@ss1234!", legacy) is False

    def test_empty_password_returns_false(self):
        h = get_password_hash("RealPassword1!")
        assert verify_password("", h) is False


# ---------------------------------------------------------------------------
# verify_and_rehash — migration behaviour
# ---------------------------------------------------------------------------

class TestVerifyAndRehash:
    def test_argon2_correct_returns_true_no_new_hash(self):
        h = get_password_hash("Argon2P@ssword1")
        is_valid, new_hash = verify_and_rehash("Argon2P@ssword1", h)
        assert is_valid is True
        assert new_hash is None

    def test_argon2_wrong_returns_false_no_new_hash(self):
        h = get_password_hash("Argon2P@ssword1")
        is_valid, new_hash = verify_and_rehash("IncorrectP@ss1", h)
        assert is_valid is False
        assert new_hash is None

    def test_legacy_correct_returns_true_with_new_hash(self):
        pw = "Migr@tion1234!"
        legacy = _make_legacy_hash(pw)
        is_valid, new_hash = verify_and_rehash(pw, legacy)
        assert is_valid is True
        assert new_hash is not None
        assert new_hash.startswith("$argon2id$"), "Migrated hash must be argon2id"

    def test_legacy_wrong_returns_false_no_new_hash(self):
        legacy = _make_legacy_hash("OrigP@ssword12")
        is_valid, new_hash = verify_and_rehash("WrongP@ss1234!", legacy)
        assert is_valid is False
        assert new_hash is None

    def test_migrated_hash_is_valid_argon2(self):
        """After migration the returned hash must verify correctly with argon2id."""
        pw = "MigratedP@ss1!"
        legacy = _make_legacy_hash(pw)
        _, new_hash = verify_and_rehash(pw, legacy)
        assert new_hash is not None
        # Verify the new hash works
        is_valid2, _ = verify_and_rehash(pw, new_hash)
        assert is_valid2 is True


# ---------------------------------------------------------------------------
# _is_legacy_sha256 — format detection
# ---------------------------------------------------------------------------

class TestIsLegacySha256:
    def test_detects_legacy_format(self):
        legacy = _make_legacy_hash("AnyP@ssword1")
        assert _is_legacy_sha256(legacy) is True

    def test_rejects_argon2_format(self):
        argon2 = get_password_hash("AnyP@ssword1")
        assert _is_legacy_sha256(argon2) is False

    def test_rejects_random_string(self):
        assert _is_legacy_sha256("notahash") is False

    def test_rejects_partial_legacy(self):
        # Only 32 chars in hash part instead of 64
        assert _is_legacy_sha256("a" * 32 + "$" + "b" * 32) is False
