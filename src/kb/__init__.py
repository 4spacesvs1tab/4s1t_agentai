"""
Knowledge Base subsystem for 4S1T Agent AI.

Provides:
  - KBVectorStore       — ChromaDB service for three KB collections
  - SocialGraph         — L1/L2 account graph management
  - KBPreprocessor      — chunk, embed, dedup, and store content
  - Ingestion adapters  — per-platform content fetchers (website, nitter,
                          youtube, podcast, nostr)
  - IngestionRunner     — unified dispatch with cursor-based incremental fetch
  - KBScheduler         — asyncio background ingestion scheduler (G7, G22)
  - BriefConfigService  — per-user brief preference CRUD (G22, G26)

Design reference: KnowledgeBase_design.md §5.6
"""
