"""
PII scrubber for 4S1T Agent AI.

Provides:
  PIIMatch  — dataclass representing a single detected PII instance
  PIIScrubber — detect(), scrub(), restore()

Detection runs the full PATTERNS list against the input text. Each regex
match is optionally validated by the pattern's checksum/format validator
before being reported. Overlapping matches are de-duplicated by span.

Scrubbing replaces matched text with typed placeholders ([PESEL_1], [EMAIL_2],
etc.) and returns a reverse map so the caller can optionally restore original
values in the LLM's response.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from privacy.pii_patterns import PATTERNS, PIIPattern


@dataclass
class PIIMatch:
    """One detected PII instance in a piece of text."""
    name: str           # e.g. "PESEL"
    tier: int           # 1 or 2
    value: str          # original matched text
    placeholder: str    # replacement token, e.g. "[PESEL_1]"
    start: int          # character offset in source text
    end: int            # character offset (exclusive)


class PIIScrubber:
    """
    Detects and optionally scrubs PII from text strings.

    Usage::

        scrubber = PIIScrubber()

        # Detection only
        matches = scrubber.detect(text)

        # Detection + scrub
        scrubbed, reverse_map = scrubber.scrub(text, matches)

        # Restore (apply to LLM response if needed)
        restored = scrubber.restore(llm_response, reverse_map)
    """

    def detect(self, text: str) -> list[PIIMatch]:
        """
        Scan *text* for all PII patterns.

        Returns a list of PIIMatch instances sorted by start offset.
        Overlapping matches are resolved by keeping the longest span.
        Regex matches failing their validator are silently discarded.
        """
        raw_matches: list[PIIMatch] = []
        counters: dict[str, int] = {}

        for pattern in PATTERNS:
            for m in pattern.regex.finditer(text):
                value = m.group(0)
                # Run checksum / format validator if present
                if pattern.validator is not None and not pattern.validator(value):
                    continue
                counters[pattern.name] = counters.get(pattern.name, 0) + 1
                placeholder = f"[{pattern.name}_{counters[pattern.name]}]"
                raw_matches.append(
                    PIIMatch(
                        name=pattern.name,
                        tier=pattern.tier,
                        value=value,
                        placeholder=placeholder,
                        start=m.start(),
                        end=m.end(),
                    )
                )

        return _deduplicate(sorted(raw_matches, key=lambda x: x.start))

    def scrub(
        self,
        text: str,
        matches: list[PIIMatch],
    ) -> tuple[str, dict[str, str]]:
        """
        Replace each match in *text* with its placeholder.

        Returns:
            (scrubbed_text, reverse_map)  where reverse_map maps
            placeholder → original value for later restoration.

        Note: *matches* should be sorted by start offset (as returned by
        detect()). Overlapping spans are handled by processing right-to-left.
        """
        reverse_map: dict[str, str] = {}
        # Process in reverse order so offsets remain valid after replacement
        parts = list(text)
        for match in reversed(matches):
            parts[match.start:match.end] = list(match.placeholder)
            reverse_map[match.placeholder] = match.value

        return "".join(parts), reverse_map

    def restore(self, text: str, reverse_map: dict[str, str]) -> str:
        """
        Substitute placeholders back with their original values.

        Used to restore PII in the LLM's response so the user sees
        the original data while the provider only ever saw placeholders.
        """
        for placeholder, original in reverse_map.items():
            text = text.replace(placeholder, original)
        return text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _deduplicate(matches: list[PIIMatch]) -> list[PIIMatch]:
    """
    Remove overlapping matches, keeping the longest span.

    When two matches overlap, the one with the greater span length wins.
    If lengths are equal, the earlier start wins.
    """
    if not matches:
        return []

    result: list[PIIMatch] = []
    current = matches[0]

    for nxt in matches[1:]:
        if nxt.start < current.end:
            # Overlap — keep the longer span
            if (nxt.end - nxt.start) > (current.end - current.start):
                current = nxt
        else:
            result.append(current)
            current = nxt

    result.append(current)
    return result


def format_pii_summary(matches: list[PIIMatch]) -> str:
    """
    Format a human-readable summary of detected PII for NIP-17 messages.

    Groups matches by name and counts instances:
      "  • 1 × PESEL\n  • 2 × email address\n  • 1 × IBAN (PL)"
    """
    counts: dict[str, int] = {}
    for m in matches:
        counts[m.name] = counts.get(m.name, 0) + 1

    _LABELS: dict[str, str] = {
        "PESEL": "PESEL",
        "NIP": "NIP (tax ID)",
        "IBAN_PL": "IBAN (PL)",
        "IBAN_EU": "IBAN (EU)",
        "CREDIT_CARD": "credit card number",
        "DOWOD": "dowód osobisty number",
        "EMAIL": "email address",
        "PHONE_PL": "Polish phone number",
        "PHONE_EU": "EU phone number",
        "POSTAL_PL": "Polish postal code",
        "IPV4": "IPv4 address",
        "PASSPORT": "passport number",
    }

    lines = []
    for name, count in counts.items():
        label = _LABELS.get(name, name)
        plural = "es" if label.endswith("address") else "s" if count > 1 else ""
        lines.append(f"  • {count} × {label}{'' if count == 1 else plural}")

    return "\n".join(lines) if lines else "  (none)"
