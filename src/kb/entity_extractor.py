"""
KB Entity Extractor — Phase KB-3 (L2 Discovery).

Extracts named entities (people, organizations, publications) from ingested
text using an LLM call. Extracted entities feed the L2 discovery pipeline:

  text → extract_entities() → list[ExtractedEntity]
    → discovery.py (upsert into kb_discovery_queue)

The extractor uses a lightweight structured prompt to DeepSeek V3 via nano-gpt.
It asks for a JSON array of {name, type, handle_hints} objects.

Design reference: KnowledgeBase_design.md §6.5 (L2 discovery)
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional

from utils.logger import setup_logger
logger = setup_logger(__name__)

# ---------------------------------------------------------------------------
# Platform name normalisation
# ---------------------------------------------------------------------------

# Canonical platform names used throughout the KB system.
# All variants map to the canonical form.
_PLATFORM_CANON: dict[str, str] = {
    # Twitter / X / Nitter — all the same underlying platform
    "x":        "twitter",
    "x.com":    "twitter",
    "nitter":   "twitter",
    # Case variants
    "Twitter":  "twitter",
    "X":        "twitter",
    "TWITTER":  "twitter",
    # LinkedIn
    "LinkedIn": "linkedin",
    "LINKEDIN": "linkedin",
    # Website / site
    "Site":     "website",
    "site":     "website",
    "WEBSITE":  "website",
    # Substack
    "Substack": "substack",
    "SUBSTACK": "substack",
    # YouTube
    "Youtube":  "youtube",
    "YouTube":  "youtube",
    "YOUTUBE":  "youtube",
    # Podcast
    "Podcast":  "podcast",
    "PODCAST":  "podcast",
    # Rumble
    "Rumble":   "rumble",
    "RUMBLE":   "rumble",
    # Nostr
    "Nostr":    "nostr",
    "NOSTR":    "nostr",
}

# Handle prefixes that encode the platform inside the value
# e.g. "x.com/ExampleUser" → platform=twitter, handle=@ExampleUser
_HANDLE_URL_PREFIXES: list[tuple[str, str]] = [
    ("x.com/",           "twitter"),
    ("twitter.com/",     "twitter"),
    ("nitter.net/",      "twitter"),
    ("linkedin.com/",    "linkedin"),
    ("substack.com/",    "substack"),
    ("youtube.com/",     "youtube"),
    ("youtu.be/",        "youtube"),
    ("rumble.com/c/",    "rumble"),
    ("rumble.com/user/", "rumble"),
]


def normalize_platform(platform: str) -> str:
    """Return the canonical platform name for *platform*."""
    return _PLATFORM_CANON.get(platform, platform.lower())


def normalize_handle(platform: str, handle: str) -> tuple[str, str]:
    """
    Given a (platform, handle) pair, return a normalised (canonical_platform, handle).

    Handles cases like:
      ("x.com", "x.com/ExampleUser") → ("twitter", "@ExampleUser")
      ("twitter", "x.com/ExampleUser") → ("twitter", "@ExampleUser")
      ("twitter", "@ExampleUser")     → ("twitter", "@ExampleUser")
    """
    canon_platform = normalize_platform(platform)
    h = handle.strip()

    # Strip any URL-based prefix that encodes the platform
    for prefix, _p in _HANDLE_URL_PREFIXES:
        if h.lower().startswith(prefix.lower()):
            h = h[len(prefix):]
            # Remove trailing slash or extra path parts
            h = h.split("/")[0].split("?")[0]
            # Ensure @ prefix for social handles
            if canon_platform in ("twitter", "nostr") and not h.startswith("@") and not h.startswith("npub"):
                h = "@" + h
            break

    return canon_platform, h


# Platforms for which an ingestion adapter exists.  Hints for any other
# platform are stripped before storing in the discovery queue / aliases.
INGESTABLE_PLATFORMS = {"twitter", "youtube", "nostr", "podcast", "website", "rumble"}


def normalize_handle_hints(hints: dict[str, str]) -> dict[str, str]:
    """
    Normalise all platform keys and handle values in a handle_hints dict.

    - Drops platforms that have no ingestion adapter (linkedin, telegram, etc.)
    - Deduplicates: if two keys map to the same canonical platform, keeps the
      more informative value (@-prefixed short handle beats full URL).
    """
    result: dict[str, str] = {}
    for platform, handle in hints.items():
        canon_plat, canon_handle = normalize_handle(platform, handle)
        if canon_plat not in INGESTABLE_PLATFORMS:
            continue
        if canon_plat not in result:
            result[canon_plat] = canon_handle
        else:
            # Prefer @-prefixed short handles over full URLs
            existing = result[canon_plat]
            if existing.startswith("http") and not canon_handle.startswith("http"):
                result[canon_plat] = canon_handle
    return result


# Extraction model — same as summarisation (cheap but capable)
_EXTRACT_MODEL = "deepseek-v3.2"

# Minimum text length to warrant entity extraction (short tweets not worth it)
_MIN_TEXT_LEN = 200

# Maximum entities we'll accept per document (guard against hallucination floods)
_MAX_ENTITIES_PER_DOC = 10

_EXTRACT_SYSTEM = (
    "You are a named-entity extractor for a knowledge-base of followable content sources.\n"
    "ONLY extract entities that a person could actually follow online: podcasters, YouTubers, "
    "newsletter authors, researchers with a public blog, Twitter/X accounts, Nostr npubs, etc.\n"
    "STRICT exclusion rules — skip the entity entirely if ANY of these apply:\n"
    "  • Single first name only (e.g. 'Krysia', 'Adam', 'Bill') with no surname or handle\n"
    "  • Case-study subjects, interview guests, or historical figures mentioned in passing\n"
    "  • Generic institutions and household names (Amazon, BBC, Fed, ECB, IMF, NATO, EU, "
    "government agencies, banks, stock exchanges)\n"
    "  • Methodology names or acronyms (Agile, BPMN, BABOK, SCRUM, ESG, BRICS, BLS)\n"
    "  • Companies not known as KB content sources\n"
    "The entity MUST have at least one of: a known social-media handle, a podcast feed URL, "
    "a YouTube channel, a Nostr npub, or a personal website with an RSS feed.\n"
    "For each qualifying entity return a JSON object with these keys:\n"
    '  "name" (string, required — full name or recognised handle),\n'
    '  "type" (string: "person" or "org"),\n'
    '  "handle_hints" (object: ONLY use these platform keys: '
    '"twitter" (use short @handle only, e.g. "@ExampleUser"), '
    '"youtube" (channel ID or @handle), '
    '"nostr" (npub only), '
    '"podcast" (RSS/Apple Podcasts URL), '
    '"website" (homepage or RSS URL), '
    '"rumble" (channel name). '
    'DO NOT include: linkedin, facebook, telegram, discord, instagram, tiktok, '
    'email, newsletter, substack, patreon, reddit, bloomberg, or any other platform.),\n'
    '  "snippet" (string: 1-2 sentence verbatim quote showing the mention context),\n'
    '  "relation" (string: "mentioned" | "cited" | "endorsed" | "criticized" | "replied"),\n'
    '  "sentiment" (string: "positive" | "neutral" | "negative").\n'
    "Return ONLY a JSON array (empty array [] if no qualifying entities). No markdown fences."
)


@dataclass
class ExtractedEntity:
    """A named entity extracted from ingested content."""
    name: str
    entity_type: str                         # "person" | "org"
    handle_hints: dict[str, str] = field(default_factory=dict)
    snippet: str = ""                        # verbatim quote showing mention context
    relation: str = "mentioned"              # mentioned | cited | endorsed | criticized | replied
    sentiment: str = "neutral"              # positive | neutral | negative
    # Set by the caller (not extracted from LLM)
    source_url: str = ""
    discovered_via_account_id: str = ""
    user_id: str = "default"


def extract_entities(
    text: str,
    source_url: str = "",
    discovered_via_account_id: str = "",
    user_id: str = "default",
    api_key: Optional[str] = None,
) -> list[ExtractedEntity]:
    """
    Extract named entities from *text* using an LLM call.

    Returns an empty list if the text is too short, if the API call fails,
    or if the LLM returns malformed JSON. Never raises.

    Args:
        text: The content to analyse.
        source_url: Source URL for evidence tracking.
        discovered_via_account_id: KB account ID of the content owner.
        user_id: For user isolation.
        api_key: nano-gpt API key (falls back to NANO_GPT_API_KEY env var).
    """
    if len(text) < _MIN_TEXT_LEN:
        return []

    key = api_key or os.environ.get("NANO_GPT_API_KEY", "")
    if not key:
        logger.debug("No API key — skipping entity extraction")
        return []

    import httpx

    nano_gpt_base = os.environ.get("NANO_GPT_BASE_URL", "https://nano-gpt.com/api/v1")

    prompt = (
        "Extract significant persons and organisations from the following text. "
        "Only include entities that could be valuable knowledge-base sources "
        "(analysts, researchers, journalists, notable public figures).\n\n"
        + text[:4000]
    )

    try:
        resp = httpx.post(
            f"{nano_gpt_base}/chat/completions",
            json={
                "model": _EXTRACT_MODEL,
                "messages": [
                    {"role": "system", "content": _EXTRACT_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 512,
                "temperature": 0.0,
            },
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            timeout=20.0,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        logger.debug("Entity extraction API call failed: %s", exc)
        return []

    # Parse JSON
    try:
        # Strip optional markdown fences
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        entities_raw = json.loads(raw)
        if not isinstance(entities_raw, list):
            return []
    except json.JSONDecodeError as exc:
        logger.debug("Entity extraction JSON parse failed: %s — raw=%r", exc, raw[:200])
        return []

    results: list[ExtractedEntity] = []
    for item in entities_raw[:_MAX_ENTITIES_PER_DOC]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name or len(name) < 2:
            continue
        entity_type = str(item.get("type", "person")).lower()
        if entity_type not in ("person", "org"):
            entity_type = "person"
        handle_hints = item.get("handle_hints") or {}
        if not isinstance(handle_hints, dict):
            handle_hints = {}
        handle_hints = normalize_handle_hints({str(k): str(v) for k, v in handle_hints.items()})
        relation = str(item.get("relation", "mentioned")).lower()
        if relation not in ("mentioned", "cited", "endorsed", "criticized", "replied"):
            relation = "mentioned"
        sentiment = str(item.get("sentiment", "neutral")).lower()
        if sentiment not in ("positive", "neutral", "negative"):
            sentiment = "neutral"
        snippet = str(item.get("snippet", "")).strip()
        results.append(
            ExtractedEntity(
                name=name,
                entity_type=entity_type,
                handle_hints=handle_hints,
                snippet=snippet,
                relation=relation,
                sentiment=sentiment,
                source_url=source_url,
                discovered_via_account_id=discovered_via_account_id,
                user_id=user_id,
            )
        )

    logger.debug(
        "Extracted %d entities from %s", len(results), source_url or "text"
    )
    return results


# ---------------------------------------------------------------------------
# EntityExtractor — stateful handler for ContentIngested events (E4)
# ---------------------------------------------------------------------------

class EntityExtractor:
    """
    ContentIngested subscriber: entity extraction + L2 discovery + knowledge graph.

    Owns the logic previously at preprocessor step 9:
      1. Call extract_entities() for qualifying content (L1, non-backfill, >= 200 chars).
      2. Upsert each entity into kb_discovery_queue via DiscoveryManager.
      3. Store entity nodes and source→entity relations in the knowledge graph.

    Note: DiscoveryManager is NOT a separate ContentIngested subscriber in Sprint 7.
    Full entity/discovery decoupling will be completed in Sprint 8 via an
    EntitiesExtracted domain event, avoiding the duplicate LLM call that would
    result from two independent handlers calling extract_entities().

    Usage (wired in initializer.py):
        extractor = EntityExtractor(api_key=api_key)
        event_bus.subscribe("content_ingested", _adapt(extractor.handle_content_ingested))
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("NANO_GPT_API_KEY", "")

    def handle_content_ingested(self, event: "ContentIngested") -> None:
        """
        Process ContentIngested event: extract entities, update discovery queue
        and knowledge graph.

        Mirrors the logic previously at preprocessor step 9.
        Skips for backfill, non-L1 accounts, and short texts.
        """
        if event.ingestion_type == "backfill":
            return
        if event.layer != 1:
            return
        if len(event.text) < _MIN_TEXT_LEN:
            return

        try:
            entities = extract_entities(
                text=event.text,
                source_url=event.source_url,
                discovered_via_account_id=event.account_id,
                user_id=event.user_id,
                api_key=self._api_key,
            )
            if not entities:
                return

            # Upsert into discovery queue
            from kb.discovery import get_discovery_manager
            dm = get_discovery_manager()
            for entity in entities:
                dm.upsert_candidate(entity)

            # KB-19: store in formal knowledge graph tables
            try:
                from kb.knowledge_graph import get_knowledge_graph_service
                kg = get_knowledge_graph_service()
                source_entity_id = (
                    kg.ensure_account_entity(event.account_id)
                    if event.account_id
                    else None
                )
                for entity in entities:
                    target_entity_id = kg.upsert_entity(
                        name=entity.name,
                        entity_type=entity.entity_type,
                        canonical_name=entity.name,
                    )
                    if source_entity_id:
                        kg.upsert_relation(
                            source_entity_id=source_entity_id,
                            target_entity_id=target_entity_id,
                            relation_type=entity.relation,
                        )
            except Exception as kg_exc:
                logger.debug("Knowledge graph upsert failed in event handler: %s", kg_exc)

        except Exception as exc:
            logger.debug("Entity extraction/discovery step failed in event handler: %s", exc)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_extractor: "EntityExtractor | None" = None


def get_entity_extractor(api_key: str | None = None) -> "EntityExtractor":
    """Return the shared EntityExtractor singleton.

    In production, wired in initializer.py. This fallback handles out-of-process
    callers (scripts and tests that bypass the initializer).
    """
    global _extractor
    if _extractor is None:
        _extractor = EntityExtractor(api_key=api_key)
    return _extractor
