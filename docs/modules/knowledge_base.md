# Knowledge Base

**Status**: Implemented (phases KB-1 through KB-7)
**Last updated**: 2026-03-08

The Knowledge Base (KB) transforms 4S1T from a reactive chat assistant into a proactive intelligence platform. It continuously ingests content from a curated social graph across multiple platforms, builds a vector knowledge store, and exposes that knowledge through domain-scoped agent skills.

---

## Architecture

```
USER REQUEST (chat / NIP-17)
        │
        ▼
 OrchestratorAgent  ─── routes KB-domain questions to research_agent / ba_agent
        │
        ├──► research_agent   ──► knowledge_base_search(domain=..., query=...)
        ├──► ba_agent         ──► knowledge_base_search(domain="ba", source="babok", ...)
        └──► synthesis_agent  ──► knowledge_base_search(domain=..., ...)

SCHEDULED PIPELINE (background)
        │
        ▼
 KBScheduler (asyncio task, started at app startup)
        │
        ├── 1. Ingestion:    ingest_all_accounts() via thread pool
        ├── 2. Discovery:    enrich_discovery_candidates() — web research for L2 handles
        ├── 3. Briefs:       kb_monitor_agent via orchestrator → writes data/briefs/*.md
        └── 4. Delivery:     brief_dispatcher → NIP-17 DM to user

INGESTION PIPELINE (per content item)
        │
        ▼
 Platform Adapter (fetch raw content)
        │
        ▼
 KBPreprocessor:
    1. Language detection
    2. Text cleaning
    3. Chunking (512 tokens, 50-token overlap)
    4. Summarization via DeepSeek V3 (long content)
    5. Embedding via bge-m3 (1024-dim, nano-gpt)
    6. Exact deduplication (SHA-256 against kb_ingestion_log)
    7. Semantic near-dedup (cosine > 0.97 within 7-day window, same account)
    8. Alert evaluation (cosine check against kb_alerts)
    9. Entity extraction → L2 discovery queue
   10. Contradiction detection (cross-account, 0.65–0.95 similarity band)
   11. Store to ChromaDB
```

---

## Source Files

### Core subsystem (`src/kb/`)

| File | Purpose |
|------|---------|
| [src/kb/vector_store.py](../../src/kb/vector_store.py) | ChromaDB wrapper — 3 collections, upsert and query |
| [src/kb/social_graph.py](../../src/kb/social_graph.py) | Account registry + NetworkX graph, L1/L2 layers |
| [src/kb/preprocessor.py](../../src/kb/preprocessor.py) | Full ingest pipeline (11 steps, see above) |
| [src/kb/scheduler.py](../../src/kb/scheduler.py) | Asyncio background scheduler (5-min check loop) |
| [src/kb/entity_extractor.py](../../src/kb/entity_extractor.py) | DeepSeek V3 LLM-based entity extraction for L2 discovery |
| [src/kb/discovery.py](../../src/kb/discovery.py) | L2 discovery queue CRUD (`DiscoveryManager`) |
| [src/kb/alert_engine.py](../../src/kb/alert_engine.py) | Semantic alert matching (`AlertEngine`) |
| [src/kb/brief_dispatcher.py](../../src/kb/brief_dispatcher.py) | Scan `data/briefs/*.md` → record in `kb_briefs` → NIP-17 |
| [src/kb/brief_config.py](../../src/kb/brief_config.py) | `BriefConfigService` CRUD for `kb_user_config` |
| [src/kb/snapshot_service.py](../../src/kb/snapshot_service.py) | Topic snapshots with LLM longitudinal diff |
| [src/kb/web_research_discovery.py](../../src/kb/web_research_discovery.py) | LLM-suggested platform handles for pending L2 candidates |
| [src/kb/account_resolver.py](../../src/kb/account_resolver.py) | Natural-language → `account_id` resolver (fuzzy match, search_terms) |

### Ingestion adapters (`src/kb/ingestion/`)

| File | Platform | Method |
|------|----------|--------|
| [src/kb/ingestion/website_adapter.py](../../src/kb/ingestion/website_adapter.py) | Websites / blogs | RSS feed + HTML fallback (readability) |
| [src/kb/ingestion/nitter_adapter.py](../../src/kb/ingestion/nitter_adapter.py) | Twitter (via Nitter) | Nitter RSS |
| [src/kb/ingestion/youtube_adapter.py](../../src/kb/ingestion/youtube_adapter.py) | YouTube | Channel Atom feed; `@handle` resolution |
| [src/kb/ingestion/podcast_adapter.py](../../src/kb/ingestion/podcast_adapter.py) | Podcasts (RSS 2.0 + iTunes) | RSS; audio via Whisper STT |
| [src/kb/ingestion/nostr_adapter.py](../../src/kb/ingestion/nostr_adapter.py) | Nostr | nostr-sdk relay subscription, kind:1 |
| [src/kb/ingestion/rumble_adapter.py](../../src/kb/ingestion/rumble_adapter.py) | Rumble | Public channel RSS feed |
| [src/kb/ingestion/ingestion_runner.py](../../src/kb/ingestion/ingestion_runner.py) | — | Adapter registry + cursor dispatch; `ingest_all_accounts()` |

### Bootstrap scripts (`src/kb/bootstrap/`)

| File | Purpose | Run |
|------|---------|-----|
| [src/kb/bootstrap/babok_loader.py](../../src/kb/bootstrap/babok_loader.py) | Load BABOK v3 PDF → ChromaDB (`source="babok"`, `domain="ba"`) | Once after initial deploy |
| [src/kb/bootstrap/seed_accounts.py](../../src/kb/bootstrap/seed_accounts.py) | Seed L1 accounts from `kb_domains.yaml` into `kb_accounts` | Idempotent; re-run after adding new accounts |
| [src/kb/bootstrap/document_loader.py](../../src/kb/bootstrap/document_loader.py) | Bootstrap document ingestion from `bootstrap_sources` in config | Per domain or `--all` |

### Configuration

| File | Purpose |
|------|---------|
| [src/config/kb_domains.yaml](../../src/config/kb_domains.yaml) | Domain definitions, L1 accounts, routing keywords (gitignored — private) |
| [src/config/kb_domains.yaml.example](../../src/config/kb_domains.yaml.example) | Schema reference and example |
| [src/config/kb_config.py](../../src/config/kb_config.py) | YAML loader — `get_domain_ids()`, `get_routing_rules_text()`, `get_bootstrap_sources()` |

### Skill

| File | Purpose |
|------|---------|
| [src/skills/knowledge_base_search/](../../src/skills/knowledge_base_search/) | Agent-callable KB search skill (v2.0) |
| [src/skills/vision_analyze/](../../src/skills/vision_analyze/) | Vision model skill for `ba_agent` (KB-5) |

### API

| File | Purpose |
|------|---------|
| [src/api/kb_routes.py](../../src/api/kb_routes.py) | REST API at `/api/v1/kb/` (all require 2FA) |

---

## ChromaDB Collections

Located at `data/chroma/` inside the project data directory.

| Collection | Content | Typical size |
|------------|---------|-------------|
| `kb_content` | Full text chunks — main corpus | 100K–1M chunks |
| `kb_summaries` | 3-sentence summaries per article/episode | 10K–100K |
| `kb_graph_ctx` | Account bios, cross-platform descriptions | 1K–10K |

Embedding: BAAI/bge-m3 (1024-dim, multilingual, via nano-gpt). Similarity: cosine.

Multi-domain accounts store domain tags as a pipe-separated string in ChromaDB metadata (e.g. `"macroeconomics|geopolitics"`). Domain filtering uses `$contains`. Never use `$in` on string fields — ChromaDB does not support it.

---

## Database Schema

Migrations in `src/database/migrations/`:

| Migration | Tables / columns added |
|-----------|----------------------|
| `010_knowledge_base.py` | `kb_accounts`, `kb_account_aliases`, `kb_relations`, `kb_ingestion_log`, `kb_discovery_queue`, `kb_alerts`, `kb_user_config`, `kb_briefs`, `kb_snapshots` |
| `011_kb_ingestion_cursors.py` | `kb_ingestion_cursors` — cursor-based incremental fetch per account + platform |
| `012_kb_phase3.py` | `kb_alert_matches` — per-chunk alert trigger log + NIP-17 delivery tracking |
| `013_kb_schedule_days.py` | `kb_user_config.brief_days` (TEXT, JSON array of day abbrevs e.g. `["mon","wed","fri"]`; NULL = all days) |
| `014_kb_account_search_terms.py` | `kb_accounts.search_terms` (TEXT, JSON array of alternative names, brand names, nicknames for fuzzy account resolution) |

All KB tables include `user_id` for multi-user isolation. The `knowledge_base_search` skill always filters by the calling user's `user_id`.

### Key tables

**`kb_accounts`** — account registry
Columns: `id` (UUID), `user_id`, `display_name`, `layer` (1=manual, 2=approved, 3=pending), `domains` (pipe-sep), `active`, `added_at`, `added_by`, `search_terms` (JSON array of alternative names/nicknames for fuzzy resolution)

**`kb_account_aliases`** — cross-platform identity
Columns: `account_id`, `platform` (`nostr`/`twitter`/`youtube`/`podcast`/`website`/`rumble`), `platform_id`, `confidence`, `verified`

**`kb_ingestion_cursors`** — incremental fetch state
Columns: `user_id`, `account_id`, `platform`, `last_ingested_at`, `last_cursor`

**`kb_alerts`** — semantic alert subscriptions
Columns: `id`, `user_id`, `query`, `query_embedding` (BLOB), `domain_filter`, `account_filter`, `similarity_threshold` (default 0.85), `active`, `last_triggered_at`

**`kb_user_config`** — per-user brief schedule
Columns: `user_id`, `domain`, `brief_enabled`, `brief_frequency` (`daily`/`weekly`/`disabled`), `brief_time` (HH:MM UTC), `brief_min_items`, `brief_extend_factor`

**`kb_briefs`** — brief history
Columns: `id`, `user_id`, `domain`, `frequency`, `content`, `window_start`, `window_end`, `generated_at`, `delivered`, `extended_window`

**`kb_snapshots`** — topic snapshots for longitudinal comparison
Columns: `id`, `user_id`, `topic_query`, `summary`, `source_ids` (JSON), `snapshot_at`, `session_id`

---

## Knowledge Domains

Defined in `src/config/kb_domains.yaml` (gitignored). The example file at [src/config/kb_domains.yaml.example](../../src/config/kb_domains.yaml.example) documents the full schema.

Configured domains as of 2026-03-08:

| Domain ID | Focus area |
|-----------|-----------|
| `macroeconomics` | Monetary systems, global liquidity, macro |
| `geopolitics` | International relations, conflict |
| `ai` | Artificial intelligence — tools, research, business |
| `ba` | Business analysis — frameworks, standards, industry bodies |
| *(user-defined)* | Additional domains configured in `kb_domains.yaml` |

The orchestrator decompose prompt is injected with domain routing rules from `get_routing_rules_text()` at runtime — no hardcoded domain names in code.

---

## `knowledge_base_search` Skill

**Version**: 2.0.0
**Agent scope**: `ba_agent`, `research_agent`, `synthesis_agent`, `kb_monitor_agent`

```python
knowledge_base_search(
    query: str,                         # required — natural language question or topic
    domain: str | list[str] | None,     # None = all domains; list = OR logic
    account: str | list[str] | None,    # account name or ID — fuzzy-resolved automatically
    source: str | None,                 # 'babok' | 'website' | 'twitter' | 'youtube' | ...
    since: str | None,                  # ISO date or relative: '7d', '30d', '24h', '6m', '1y'
    until: str | None,                  # ISO date or relative
    language: str | None,               # 'en' | 'pl' | None = all (content language, not query)
    collection: str,                    # 'kb_content' (default) | 'kb_summaries' | 'kb_graph_ctx'
    n_results: int,                     # 1–50, default 20
    sort_by: str,                       # 'date_desc' (default) | 'relevance' | 'date_asc'
    user_id: str,                       # injected from session; default 'default'
)
```

**Returns**:
```json
{
  "results": [
    {
      "text": "...",
      "source_url": "...",
      "author": "...",
      "account_id": "...",
      "domain": "...",
      "platform": "...",
      "published_at": "2026-01-15T12:00:00Z",
      "published_age": "7 weeks ago",
      "score": 0.91,
      "source": "website",
      "contradicts_chunk_id": ""
    }
  ],
  "query_meta": {
    "account_found": true,
    "extended_window": false,
    "result_count": 20,
    "collection_searched": "kb_content",
    "domain_filter_applied": "macroeconomics",
    "time_filter_applied": true,
    "resolved_account": "'John Doe'→john_doe_account(0.98)"
  }
}
```

**Behaviour notes**:
- `sort_by="date_desc"` (default): fetches **all** matching chunks via `col.get(limit=2000)` then sorts in Python — no semantic bias. Newest content always surfaces first regardless of query similarity score.
- `sort_by="relevance"`: uses `col.query()` (cosine similarity). Use for deep-dive "what does X say about Y" queries where freshness matters less than topical match.
- `sort_by="date_asc"/"date_desc"` with `since`/`until`: time filter applied in Python (ChromaDB does not support `$gte`/`$lte` on string fields).
- Account resolver: the `account` parameter accepts natural-language names, nicknames, brand names, or typos. The skill resolves these to exact `account_id` values using `src/kb/account_resolver.py` (8-strategy pipeline: exact match → fuzzy difflib → substring). `query_meta.resolved_account` is non-null when resolution occurred, e.g. `"'John Doe'→john_doe_account(0.98)"`.
- `account_found=False`: When an `account` filter is specified but even after fuzzy resolution no matching account exists in ChromaDB, the agent must not report "no results" — inform the user the account is not tracked and offer to add it (G24).
- Auto-retry on sparse results (G10): If fewer than 3 results pass the time filter, the skill automatically retries without `since`/`until` and sets `query_meta.extended_window=True`.
- `language`: Restricts result content language, not query handling. bge-m3 handles cross-lingual matching natively.
- `contradicts_chunk_id` non-empty: A different account's chunk was flagged as contradicting this one at ingest time. Surface as "Sources disagree on this" in the response (UC8).

---

## Scheduler

**Source**: [src/kb/scheduler.py](../../src/kb/scheduler.py)

Started as a background asyncio task in `src/main.py` at app startup:

```python
app.state.kb_scheduler = KBScheduler(agent_infra=app.state.agent_infra)
app.state.kb_scheduler_task = asyncio.create_task(app.state.kb_scheduler.run_forever())
```

**Tick cycle** (every 1 hour, `_CHECK_INTERVAL_S=3600`):
1. Load `kb_user_config` from DB (refreshed every 30 min, or immediately if empty)
2. For each `(user_id, domain)` where `brief_frequency != 'disabled'` and last ingestion ≥ 1 hour ago (`_INGEST_INTERVAL_S=3600`):
   - Run `ingest_all_accounts()` sequentially (awaited in thread pool, one domain at a time) — prevents concurrent ChromaDB loads from spiking memory above the container limit
3. For each dispatched user: run `enrich_discovery_candidates()` (web research for pending L2 handles)
4. For each dispatched user: **only if current UTC hour ≥ 7** (`_BRIEF_SEND_HOUR_UTC=7`): run `kb_monitor_agent` as a `BaseAgent` (not via orchestrator) to write per-domain briefs — one agent session per domain to cap context size. Skips domains whose brief file for today already exists.
5. For **all** active users (not just dispatched): run `brief_dispatcher` — record new brief files in `kb_briefs` and deliver pending briefs + alert matches via NIP-17

Fallback: if `kb_user_config` has no rows, the scheduler falls back to `kb_accounts` to find active users and runs daily ingestion for all domains.

**Env vars** the scheduler reads:
- `NANO_GPT_API_KEY` — for ingestion embedding calls
- `NITTER_INSTANCES` — comma-separated Nitter base URLs (e.g. `http://nitter:8080`)
- `NOSTR_RELAYS` — comma-separated wss:// relay URLs

---

## API Endpoints

Router: `/api/v1/kb/` — all routes require a 2FA-verified session.
Source: [src/api/kb_routes.py](../../src/api/kb_routes.py)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/discovery` | List L2 discovery candidates (filter by `status_filter`) |
| `POST` | `/discovery` | Manually add a discovery candidate |
| `POST` | `/discovery/{id}/approve` | Promote candidate to L2 account |
| `POST` | `/discovery/{id}/reject` | Mark candidate rejected |
| `GET` | `/accounts` | List KB accounts for the user |
| `POST` | `/accounts` | Add an L1 account with aliases |
| `DELETE` | `/accounts/{id}` | Remove an account (and its aliases) |
| `GET` | `/alerts` | List semantic alert subscriptions |
| `POST` | `/alerts` | Create alert (embeds query via bge-m3 at creation) |
| `DELETE` | `/alerts/{id}` | Delete an alert |
| `GET` | `/snapshots` | List topic snapshots |
| `POST` | `/snapshots` | Save a new snapshot |
| `GET` | `/snapshots/compare?topic=` | LLM longitudinal diff of snapshots for a topic |
| `DELETE` | `/snapshots/{id}` | Delete a snapshot |
| `POST` | `/ingest` | Trigger manual ingestion (fire-and-forget, 202 Accepted) |
| `POST` | `/generate-briefs` | Trigger on-demand brief generation for the user |

---

## KB Web UI

Full-featured browser interface for managing the Knowledge Base. Templates in `src/web/templates/kb_*.html`, routes in `src/api/web_routes.py`.

| Page | URL | Purpose |
|------|-----|---------|
| Dashboard | `/kb` | Platform breakdown, ingestion status, last-run times |
| Accounts | `/kb/accounts` | Add / remove L1 accounts and aliases |
| Discovery | `/kb/discovery` | Review and approve L2 discovery candidates |
| Alerts | `/kb/alerts` | Create and manage semantic alert subscriptions |
| Briefs | `/kb/briefs` | View generated domain briefs, trigger regeneration |
| Documents | `/kb/documents` | Browse ingested chunks per account |
| Schedule | `/kb/schedule` | Configure per-domain `brief_frequency`, `brief_days`, `brief_time` |
| Graph | `/kb/graph` | Social graph visualisation (account relationships) |

`brief_days` (from migration 013): optional JSON array of day abbreviations (`["mon","wed","fri"]`). NULL means every day. Used by the schedule UI to restrict brief generation to selected weekdays.

`generate-briefs` endpoint (updated): accepts optional `{"domain": "macroeconomics"}` body to regenerate a single domain. Deletes the existing brief file for that domain before re-running, allowing on-demand refresh.

`GET /api/v1/kb/stats`: returns counts of accounts, chunks, briefs, alerts, discovery candidates per domain.

---

## Agent Personas

### `kb_monitor_agent`

Added to [src/agents/personas.py](../../src/agents/personas.py).

- **Invoked by**: Scheduler only (not by user requests)
- **Skills**: `knowledge_base_search`, `get_current_datetime`, `file_write`
- **Output**: writes briefs to `data/briefs/{domain}_{date}.md`; scheduler handles NIP-17 delivery
- **Brief format**: Top Stories → Key Signals → Expert Predictions → [Discoveries awaiting approval]
- **Empty-state policy**: if fewer than `brief_min_items` items found, auto-extends window by `brief_extend_factor`; if extended window also sparse, writes a one-line skip-notice instead of a full brief

### `ba_agent` changes (KB-1, KB-5)

- `babok_lookup` skill removed; replaced with `knowledge_base_search(domain='ba', source='babok', ...)`
- `vision_analyze` skill added (KB-5) — calls vision model (`Qwen2-VL-72B` on nano-gpt) for image inputs

---

## Brief Delivery Pipeline

1. `kb_monitor_agent` writes `data/briefs/{domain}_{date}.md`
2. On each scheduler tick, `brief_dispatcher.run_dispatch(user_id)`:
   a. Scans `data/briefs/` for new files not yet in `kb_briefs`
   b. Records each new file as a `kb_briefs` row (`delivered=False`)
   c. Reads pending (undelivered) briefs → sends each as a NIP-17 DM to the user's npub
   d. Marks `kb_briefs.delivered=True`
   e. Scans `kb_alert_matches` for pending alert matches → sends digest DM → marks delivered

**Required env vars for delivery**:
- `FILE_READ_BASE_DIR=/app/data` — must be set in `.env`; otherwise `file_write` resolves paths relative to the read-only volume mount

---

## Contradiction Detection (KB-5)

Implemented in [src/kb/preprocessor.py](../../src/kb/preprocessor.py) as step 10 of the ingestion pipeline.

For each new chunk:
1. Query ChromaDB for similar chunks from **different** accounts with cosine similarity 0.65–0.95 (too-high = same content/repost, not contradiction)
2. If candidates found, run a DeepSeek V3 YES/NO prompt: "Do these two passages express contradictory claims?"
3. If YES: re-upsert the new chunk with `contradicts_chunk_id` set to the existing chunk's ID
4. The `knowledge_base_search` result items include `contradicts_chunk_id` so agents can surface disagreements

No new migration needed — `contradicts_chunk_id` is stored in ChromaDB metadata only.

---

## Account Resolver (KB-7)

**Source**: [src/kb/account_resolver.py](../../src/kb/account_resolver.py)

Resolves natural-language account references to exact `account_id` strings. Called transparently inside `knowledge_base_search` — no agent changes required.

**Resolution pipeline** (first match wins):

| Priority | Strategy | Example |
|----------|----------|---------|
| 1 | Exact `account_id` match | `"john_doe"` → `john_doe` |
| 2 | Exact `display_name` match (case-insensitive) | `"John Doe"` → `john_doe` |
| 3 | Exact `search_terms` match | `"Brand Name"` → `john_doe` |
| 4 | Exact platform handle match | `"@account_handle"` → `john_doe` |
| 5 | Fuzzy `display_name` (difflib ≥ 0.72) | `"Jon Doe"` → `john_doe` (0.92) |
| 6 | Fuzzy `account_id` (difflib ≥ 0.72) | `"jon_doe"` → `john_doe` (0.91) |
| 7 | Fuzzy `search_terms` (difflib ≥ 0.72) | `"Brand Nam"` → `john_doe` |
| 8 | Substring in `display_name` / `search_terms` | `"Doe"` → `john_doe` |

`search_terms` are seeded per account in migration 014 and can be extended by editing the migration and re-running it (idempotent `UPDATE`).

**Cost**: one SQLite SELECT on ~25 rows + difflib string comparisons. Adds <1 ms to skill execution. Eliminates token-expensive retry loops from wrong account ID guesses.

---

## Setup and Deployment

### First-time setup

1. Copy `src/config/kb_domains.yaml.example` → `src/config/kb_domains.yaml` and fill in your domain accounts
2. Run migrations on the host DB:
   ```bash
   python3 src/database/migrations/010_knowledge_base.py /path/to/data/agent.db
   python3 src/database/migrations/011_kb_ingestion_cursors.py /path/to/data/agent.db
   python3 src/database/migrations/012_kb_phase3.py /path/to/data/agent.db
   python3 src/database/migrations/013_kb_schedule_days.py /path/to/data/agent.db
   python3 src/database/migrations/014_kb_account_search_terms.py /path/to/data/agent.db
   ```
3. Seed L1 accounts from `kb_domains.yaml`:
   ```bash
   python3 src/kb/bootstrap/seed_accounts.py
   ```
   This auto-detects the real user UUID from the `users` table. It is idempotent — safe to re-run after adding new accounts to the YAML.
4. Bootstrap BABOK (one-time):
   ```bash
   python3 src/kb/bootstrap/babok_loader.py
   ```
5. Set env vars in `.env`:
   ```
   FILE_READ_BASE_DIR=/app/data
   NITTER_INSTANCES=http://nitter:8080
   NOSTR_RELAYS=wss://relay1.example.com,wss://relay2.example.com
   TOR_CONTROL_HOST=<docker-bridge-gateway>
   TOR_CONTROL_PORT=9051
   TOR_CONTROL_PASSWORD=<password>
   ```
6. Restart the container (`docker compose up -d agent` — not `docker restart` if `.env` changed)

### Ongoing deployment (rsync)

No new migrations are needed unless new migration files are present. See [INSTALL.md](../../INSTALL.md) for the full rsync procedure.

After rsync:
```bash
docker restart 4s1t-agent
curl -sf http://<server-ip>:8000/health
```

The scheduler starts automatically on app startup. Confirm in logs:
```
KB scheduler started (check_interval=3600s)
```

---

## Known Issues and Operational Notes

### Scheduler
- The `asyncio.create_task()` return value **must** be stored (e.g. `app.state.kb_scheduler_task`) to prevent the task from being garbage-collected silently.
- `_DEFAULT_DB_PATH` uses `Path(__file__).resolve()` to avoid incorrect path resolution when the module is imported through cross-package paths. If you move `scheduler.py`, verify the path logic.
- Config is only cached once a non-empty `kb_user_config` result is obtained. On startup DB-lock failures the scheduler retries on the very next tick (up to 1 hour later) rather than waiting the full 30-minute refresh interval.

### Ingestion
- YouTube `@handle` format requires handle-to-channel-ID resolution (`_resolve_handle_to_channel_id()` in `youtube_adapter.py`). Tor may block YouTube — expect 0 items if relay is blocked.
- Rumble adapter uses public RSS feeds and requires no credentials, but RSS may lag video uploads.
- Ingestion cursor stores `max(published_at)` of fetched items, not wall-clock time. This prevents episodes published before ingestion runs (e.g. 07:00 UTC when ingestion runs at 12:00 UTC) from being permanently skipped on the next scheduled run. Cursors are also written for 0-item runs to prevent re-dispatch on every tick.
- `kb_accounts.user_id` must match the real user UUID in the `users` table, not the string `'default'`. Run `seed_accounts.py` (not a manual INSERT) to get the correct UUID.

### Brief delivery
- `kb_user_config` must have rows for the user, and at least one `kb_accounts` row must be active for the scheduler to dispatch.
- The `file_write` skill requires `FILE_READ_BASE_DIR=/app/data` in the container environment. Without this, briefs are written to the read-only volume mount and silently fail.
- `kb_monitor_agent` must be listed in the `agent_scope` of `src/skills/file_write/meta.json`.

### Tor circuit rotation
When nano-gpt's Vercel edge returns 403 (rate limit via Tor exit), `src/core/api_client.py` sends `NEWNYM` to the Tor control port and waits 3s before retrying. Requires `/etc/tor/torrc` to have `ControlPort 9051` and a hashed password configured.

---

## Dependencies

Added to `requirements.txt` for the KB feature:

```
chromadb
feedparser
langdetect
networkx
readability-lxml
pdfplumber
PyPDF2
nostr-sdk
```
