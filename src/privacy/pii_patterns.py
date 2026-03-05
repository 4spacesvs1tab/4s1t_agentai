"""
PII detection patterns for Poland and EU.

Each pattern is a PIIPattern namedtuple:
  name      — short label used in placeholder tokens, e.g. "PESEL"
  tier      — 1 (always alert) or 2 (alert above threshold when scrubbing off)
  regex     — compiled regular expression
  validator — optional callable(str) -> bool for checksum / format validation;
              if provided, a regex match is only accepted when validator returns True

Tier 1 — Critical identifiers (always alert regardless of scrubbing setting):
  PESEL, NIP, IBAN_PL, IBAN_EU, CREDIT_CARD, DOWOD

Tier 2 — Common contact / location data (alert when scrubbing off, count >= threshold):
  EMAIL, PHONE_PL, PHONE_EU, POSTAL_PL, IPV4, DATE_PL, PASSPORT
"""
from __future__ import annotations

import re
from typing import Callable, NamedTuple, Optional


class PIIPattern(NamedTuple):
    name: str
    tier: int
    regex: re.Pattern
    validator: Optional[Callable[[str], bool]]


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

def _validate_pesel(value: str) -> bool:
    """Validate PESEL checksum (Polish national ID, 11 digits)."""
    digits = re.sub(r"\D", "", value)
    if len(digits) != 11:
        return False
    weights = [1, 3, 7, 9, 1, 3, 7, 9, 1, 3]
    total = sum(int(digits[i]) * weights[i] for i in range(10))
    checksum = (10 - (total % 10)) % 10
    return checksum == int(digits[10])


def _validate_nip(value: str) -> bool:
    """Validate NIP checksum (Polish tax ID, 10 digits)."""
    digits = re.sub(r"\D", "", value)
    if len(digits) != 10:
        return False
    weights = [6, 5, 7, 2, 3, 4, 5, 6, 7]
    total = sum(int(digits[i]) * weights[i] for i in range(9))
    checksum = total % 11
    if checksum == 10:
        return False
    return checksum == int(digits[9])


def _validate_luhn(value: str) -> bool:
    """Luhn algorithm for credit card numbers."""
    digits = re.sub(r"[\s\-]", "", value)
    if not digits.isdigit() or len(digits) < 13:
        return False
    total = 0
    reverse = digits[::-1]
    for i, ch in enumerate(reverse):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _validate_iban(value: str) -> bool:
    """Basic IBAN format validation (country code + check digits + BBAN)."""
    iban = re.sub(r"\s", "", value).upper()
    if len(iban) < 15 or len(iban) > 34:
        return False
    if not iban[:2].isalpha() or not iban[2:4].isdigit():
        return False
    # Move first 4 chars to end and replace letters with numbers
    rearranged = iban[4:] + iban[:4]
    numeric = ""
    for ch in rearranged:
        if ch.isalpha():
            numeric += str(ord(ch) - ord("A") + 10)
        else:
            numeric += ch
    return int(numeric) % 97 == 1


# ---------------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------------

PATTERNS: list[PIIPattern] = [

    # ------------------------------------------------------------------
    # Tier 1 — critical identifiers
    # ------------------------------------------------------------------

    PIIPattern(
        name="PESEL",
        tier=1,
        # 11 consecutive digits; require word boundaries to avoid matching
        # substrings of longer numbers
        regex=re.compile(r"\b\d{11}\b"),
        validator=_validate_pesel,
    ),

    PIIPattern(
        name="NIP",
        tier=1,
        # NNN-NNN-NN-NN or NNNNNNNNNN (10 digits)
        regex=re.compile(r"\b\d{3}[- ]?\d{3}[- ]?\d{2}[- ]?\d{2}\b"),
        validator=_validate_nip,
    ),

    PIIPattern(
        name="IBAN_PL",
        tier=1,
        # PL + 26 digits (2 check + 24 BBAN), optional spaces every 4 digits
        # Format: PL61 1090 1014 0000 0712 1981 2874
        regex=re.compile(
            r"\bPL\d{2}(?:[\s]?\d{4}){6}\b",
            re.IGNORECASE,
        ),
        validator=_validate_iban,
    ),

    PIIPattern(
        name="IBAN_EU",
        tier=1,
        # Generic EU IBAN: 2-letter country code (not PL, handled above) + 2 digits + BBAN
        regex=re.compile(
            r"\b(?!PL)[A-Z]{2}\d{2}[\s]?[A-Z0-9]{4}(?:[\s]?[A-Z0-9]{4}){2,7}\b",
            re.IGNORECASE,
        ),
        validator=_validate_iban,
    ),

    PIIPattern(
        name="CREDIT_CARD",
        tier=1,
        # 13-19 digits, optionally space- or dash-separated in groups of 4
        regex=re.compile(
            r"\b(?:\d{4}[\s\-]?){3}\d{1,7}\b"
        ),
        validator=_validate_luhn,
    ),

    PIIPattern(
        name="DOWOD",
        tier=1,
        # Polish ID card: 3 uppercase letters + 6 digits (e.g. ABC123456)
        regex=re.compile(r"\b[A-Z]{3}\d{6}\b"),
        validator=None,
    ),

    # ------------------------------------------------------------------
    # Tier 2 — contact / location data
    # ------------------------------------------------------------------

    PIIPattern(
        name="EMAIL",
        tier=2,
        regex=re.compile(
            r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
        ),
        validator=None,
    ),

    PIIPattern(
        name="PHONE_PL",
        tier=2,
        # +48 XXX XXX XXX, 48XXXXXXXXX, or bare 9-digit Polish number
        regex=re.compile(
            r"(?:\+48[\s\-]?|48[\s\-]?)?\b[4-9]\d{2}[\s\-]?\d{3}[\s\-]?\d{3}\b"
        ),
        validator=None,
    ),

    PIIPattern(
        name="PHONE_EU",
        tier=2,
        # International format: +XX(X) followed by 7-12 digits
        regex=re.compile(
            r"\+(?!48\b)[1-9]\d{1,2}[\s\-]?\(?\d{1,4}\)?[\s\-]?\d{3,5}[\s\-]?\d{3,5}\b"
        ),
        validator=None,
    ),

    PIIPattern(
        name="POSTAL_PL",
        tier=2,
        # Polish postal code: NN-NNN
        regex=re.compile(r"\b\d{2}-\d{3}\b"),
        validator=None,
    ),

    PIIPattern(
        name="IPV4",
        tier=2,
        regex=re.compile(
            r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}"
            r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
        ),
        validator=None,
    ),

    PIIPattern(
        name="DATE_PL",
        tier=2,
        # DD.MM.YYYY, DD/MM/YYYY, YYYY-MM-DD
        regex=re.compile(
            r"\b(?:\d{2}[./]\d{2}[./]\d{4}|\d{4}-\d{2}-\d{2})\b"
        ),
        validator=None,
    ),

    PIIPattern(
        name="PASSPORT",
        tier=2,
        # Polish passport: 2 letters + 7 digits
        regex=re.compile(r"\b[A-Z]{2}\d{7}\b"),
        validator=None,
    ),
]

# Convenience lookup by name
PATTERNS_BY_NAME: dict[str, PIIPattern] = {p.name: p for p in PATTERNS}
