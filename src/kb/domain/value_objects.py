"""
KB Domain Value Objects — typed primitives that enforce invariants at construction.

DDD Issues addressed:
  Issue 6: Anemic value objects — layer, platform, similarity were bare primitives
            with no validation, no domain methods, and silent failure on bad values.
  Issue 10: Inconsistent ubiquitous language — "twitter2" and "nitter" appeared as
            distinct platform names; canonical Platform enum provides a single name.

Pure domain code — no imports from infrastructure, config, or external libraries.
"""
from __future__ import annotations

from enum import IntEnum, Enum


class Layer(IntEnum):
    """Content-source trust layer. IntEnum so existing int comparisons still work."""
    MANUAL   = 1   # user-created, always trusted
    APPROVED = 2   # auto-promoted from discovery
    PENDING  = 3   # awaiting review

    def is_auto_approvable(self) -> bool:
        """Return True if this layer represents an auto-approved discovery account."""
        return self == Layer.APPROVED


class Platform(str, Enum):
    """Canonical platform identifier. str-Enum so .value is always a plain string."""
    WEBSITE   = "website"
    BLOG      = "blog"
    SUBSTACK  = "substack"
    WORDPRESS = "wordpress"
    TWITTER   = "twitter"
    YOUTUBE   = "youtube"
    NOSTR     = "nostr"
    PODCAST   = "podcast"
    RUMBLE    = "rumble"

    @classmethod
    def from_alias(cls, alias: str) -> "Platform":
        """
        Map adapter aliases and alternate spellings to the canonical Platform.

        Aliases handled (from ingestion_runner._ADAPTERS):
          twitter2, nitter → Platform.TWITTER

        All other keys must be valid Platform values directly.
        """
        _ALIASES: dict[str, str] = {
            "twitter2": "twitter",
            "nitter": "twitter",
        }
        canonical = _ALIASES.get(alias.lower(), alias.lower())
        return cls(canonical)


class SimilarityScore(float):
    """
    Cosine similarity in [0.0, 1.0].

    Enforces the valid range at construction time so callers cannot accidentally
    create scores > 1 from raw ChromaDB distances.

    Class constants document the default thresholds from agent_config.yaml.
    The YAML values are the operator-tunable source of truth — these constants
    must stay in sync with the YAML defaults.
    """
    # Mirrors kb.preprocessor.dedup_similarity_threshold: 0.97
    DEDUP_THRESHOLD: float = 0.97
    # Mirrors kb.preprocessor.contradiction_sim_range_low: 0.65
    CONTRADICTION_LOW: float = 0.65
    # Mirrors kb.preprocessor.contradiction_sim_range_high: 0.95
    CONTRADICTION_HIGH: float = 0.95

    def __new__(cls, v: float) -> "SimilarityScore":
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"SimilarityScore must be in [0, 1], got {v}")
        return super().__new__(cls, v)

    def is_duplicate(self) -> bool:
        """Return True if this score meets the near-duplicate threshold."""
        return self >= self.DEDUP_THRESHOLD

    def is_contradiction_candidate(self) -> bool:
        """Return True if this score falls in the contradiction detection band."""
        return self.CONTRADICTION_LOW <= self < self.CONTRADICTION_HIGH


class DomainLabel(str):
    """
    Validated KB domain label.

    Validation is deferred to call time (not import time) to avoid a YAML
    dependency in this pure domain module.  Use DomainLabel.validate() at
    ingestion boundaries where the set of valid labels is known.
    """

    @classmethod
    def validate(cls, label: str, valid_labels: set[str]) -> "DomainLabel":
        """Return a validated DomainLabel, or raise ValueError for unknown labels."""
        if label not in valid_labels:
            raise ValueError(f"Unknown domain label: {label!r}")
        return cls(label)
