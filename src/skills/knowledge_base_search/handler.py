#!/usr/bin/env python3
"""
knowledge_base_search skill handler — v2.0.0

Full-featured KB search with domain, account, time, language, source, and
sort-by filtering. Implements G3, G10, G14, G16, G19, G24, G27 from the
KnowledgeBase design document.

Input:  {"parameters": {"query": "...", "domain": "macroeconomics", ...}}
Output: {"success": true, "result": {"results": [...], "query_meta": {...}}}

Design reference: KnowledgeBase_design.md §5.3
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_CHROMA_PATH = "./data/chroma"
_EMBEDDING_MODEL = "BAAI/bge-m3"
_EMBEDDING_DIMS = 1024
_MIN_RESULTS_BEFORE_RETRY = 3    # G10: auto-retry threshold
_DATE_SORT_OVERFETCH_FACTOR = 2.5  # G14: over-fetch for date sorting
_FRESHNESS_BOOST_ENABLED = True   # KB-16: apply freshness score multiplier to relevance results


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _parse_relative_date(value: str) -> Optional[str]:
    """
    Parse a relative date shorthand to ISO 8601 UTC string.

    Accepts: '24h', '7d', '30d', '6m', '1y', or any ISO 8601 date.
    Returns None if parsing fails.
    """
    v = value.strip().lower()
    now = datetime.now(timezone.utc)
    try:
        if v.endswith("h"):
            return (now - timedelta(hours=int(v[:-1]))).isoformat()
        if v.endswith("d"):
            return (now - timedelta(days=int(v[:-1]))).isoformat()
        if v.endswith("m"):
            return (now - timedelta(days=int(v[:-1]) * 30)).isoformat()
        if v.endswith("y"):
            return (now - timedelta(days=int(v[:-1]) * 365)).isoformat()
    except (ValueError, IndexError):
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(value.strip(), fmt)
            return dt.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    return None


def _human_age(iso_date: str) -> str:
    """Return a human-readable age string: '3 days ago', '6 months ago', etc."""
    if not iso_date:
        return "unknown date"
    try:
        published = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - published
        days = delta.days
        if days < 0:
            return "future"
        if days == 0:
            return "today"
        if days == 1:
            return "yesterday"
        if days < 7:
            return f"{days} days ago"
        if days < 14:
            return "1 week ago"
        if days < 30:
            return f"{days // 7} weeks ago"
        if days < 60:
            return "1 month ago"
        if days < 365:
            return f"{days // 30} months ago"
        years = days // 365
        return f"{years} year{'s' if years > 1 else ''} ago"
    except Exception:
        return iso_date


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def _embed_query(query: str, api_key: str) -> list:
    """
    Embed a query using BAAI/bge-m3 via nano-gpt embeddings endpoint.
    Falls back to zero vector on error (logs warning).
    """
    import httpx
    base = os.environ.get("NANO_GPT_BASE_URL", "https://nano-gpt.com/api/v1")
    try:
        resp = httpx.post(
            f"{base}/embeddings",
            json={"model": _EMBEDDING_MODEL, "input": [query]},
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=20.0,
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]
    except Exception as exc:
        sys.stderr.write(f"Warning: embedding API failed: {exc}\n")
        return [0.0] * _EMBEDDING_DIMS


# ---------------------------------------------------------------------------
# KB-21: SQL-derived visible-account whitelist (team scope enforcement)
# ---------------------------------------------------------------------------

def _get_visible_account_ids(user_id: str, db_path: str) -> Optional[list]:
    """
    Return the list of kb_accounts.id values visible to *user_id*.

    Visible = personal accounts (user_id match) OR team-scoped accounts
    where the user is a team member.

    Returns None if the lookup fails (caller falls back to user_id filter).
    Returns an empty list if the user has no accounts at all.
    """
    if user_id == "default":
        return None
    try:
        import sqlite3 as _sq3
        conn = _sq3.connect(db_path)
        conn.row_factory = _sq3.Row
        rows = conn.execute(
            """
            SELECT id FROM kb_accounts
            WHERE user_id = ?
               OR (scope = 'team' AND scope_id IN (
                       SELECT team_id FROM team_members WHERE user_id = ?
                   ))
            """,
            (user_id, user_id),
        ).fetchall()
        conn.close()
        return [r["id"] for r in rows]
    except Exception:
        return None


# ---------------------------------------------------------------------------
# ChromaDB filter builder (G27: user_id always required)
# ---------------------------------------------------------------------------

def _build_where(user_id, domain, account, source, since_iso, until_iso, language,
                 visible_account_ids=None):
    """
    Build ChromaDB where filter dict.

    KB-21: when *visible_account_ids* is provided (SQL-derived whitelist that
    includes personal + team-scoped accounts) the user_id condition is replaced
    by an account_id $in check.  ChromaDB 0.6.3 supports $in; $or is not
    supported, so we cannot combine user_id and team account_id conditions.

    Without visible_account_ids (legacy / team_members table absent):
      user_id is ALWAYS a required condition (G27: user isolation).
    """
    if visible_account_ids is not None:
        # Use account_id whitelist as the isolation condition
        if visible_account_ids:
            conditions = [{"account_id": {"$in": visible_account_ids}}]
        else:
            # User has no accounts → guarantee empty results safely
            conditions = [{"account_id": {"$eq": "__no_accounts__"}}]
    else:
        conditions = [{"user_id": {"$eq": user_id}}]

    # Domain filter — ChromaDB 0.6.x does not support $contains; use $eq.
    # Accounts with a single domain store it as a plain string ("geopolitics"),
    # so $eq is correct and sufficient for the current schema.
    if domain:
        domains_list = [domain] if isinstance(domain, str) else list(domain)
        if len(domains_list) == 1:
            conditions.append({"domains": {"$eq": domains_list[0]}})
        else:
            conditions.append({
                "$or": [{"domains": {"$eq": d}} for d in domains_list]
            })

    # Account filter
    if account:
        accounts_list = [account] if isinstance(account, str) else list(account)
        if len(accounts_list) == 1:
            conditions.append({"account_id": {"$eq": accounts_list[0]}})
        else:
            conditions.append({
                "$or": [{"account_id": {"$eq": a}} for a in accounts_list]
            })

    # Source filter
    if source:
        conditions.append({"source": {"$eq": source}})

    # NOTE: Time filters ($gte/$lte) are NOT applied here — ChromaDB 0.6.x only
    # supports $gt/$gte/$lt/$lte for numeric fields, not strings.  published_at
    # is an ISO 8601 string so ChromaDB rejects those operators at query time.
    # Time filtering is done in Python after retrieval (see _apply_time_filter).

    # Language filter (G16)
    if language:
        conditions.append({"language": {"$eq": language}})

    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}


# ---------------------------------------------------------------------------
# Account existence check (G24)
# ---------------------------------------------------------------------------

def _account_exists_in_kb(account_id_or_ids, chroma_path, user_id):
    """
    Return True if at least one account ID has any chunks in kb_content for this user.
    Used to distinguish "account unknown" from "account has no recent content" (G24).
    """
    import chromadb
    accounts = [account_id_or_ids] if isinstance(account_id_or_ids, str) else list(account_id_or_ids)
    try:
        client = chromadb.PersistentClient(path=chroma_path)
        col = client.get_collection("kb_content")
        for acc_id in accounts:
            result = col.get(
                where={"$and": [
                    {"user_id": {"$eq": user_id}},
                    {"account_id": {"$eq": acc_id}},
                ]},
                limit=1,
                include=[],
            )
            if result.get("ids"):
                return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Core execute
# ---------------------------------------------------------------------------

def _resolve_account_param(account_raw, db_path: str, user_id: str):
    """
    Resolve account param (string or list) to (resolved_ids, resolved_display, was_resolved).

    For each element that doesn't match an exact account_id in ChromaDB,
    the account_resolver is tried. Returns the best-match IDs and a
    human-readable string describing what was resolved (for query_meta).
    """
    if not account_raw:
        return None, None, False

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    try:
        from kb.account_resolver import resolve_one, MIN_CONFIDENCE_AUTO
    except ImportError:
        # Resolver not available — fall back to raw value
        return account_raw, None, False

    items = [account_raw] if isinstance(account_raw, str) else list(account_raw)
    resolved_ids = []
    resolved_labels = []
    was_resolved = False

    for item in items:
        candidate = resolve_one(item, db_path=db_path, user_id=user_id if user_id != "default" else None)
        if candidate and candidate.account_id != item:
            resolved_ids.append(candidate.account_id)
            resolved_labels.append(
                f"{item!r}→{candidate.account_id}({candidate.confidence:.2f})"
            )
            was_resolved = True
        else:
            resolved_ids.append(item)

    if len(resolved_ids) == 1:
        resolved_ids = resolved_ids[0]

    resolved_str = "; ".join(resolved_labels) if resolved_labels else None
    return resolved_ids, resolved_str, was_resolved


def execute(params):
    query = params.get("query", "").strip()
    if not query:
        raise ValueError("'query' parameter is required.")

    domain = params.get("domain") or None
    account_raw = params.get("account") or None
    source = params.get("source") or None
    since_raw = params.get("since") or None
    until_raw = params.get("until") or None
    language = params.get("language") or None
    collection_name = params.get("collection", "kb_content") or "kb_content"
    n_results = max(1, min(50, int(params.get("n_results", 20))))
    sort_by = params.get("sort_by", "date_desc")
    user_id = params.get("user_id", "default") or "default"

    api_key = os.environ.get("NANO_GPT_API_KEY", "")
    chroma_path = os.environ.get("CHROMA_PATH", _DEFAULT_CHROMA_PATH)
    # Derive SQLite path from DATABASE_URL (sqlite:////app/data/agent.db) or DB_PATH
    _db_url = os.environ.get("DATABASE_URL", "")
    db_path = (
        os.environ.get("DB_PATH")
        or (_db_url.replace("sqlite:///", "").replace("sqlite://", "") if _db_url.startswith("sqlite") else "")
        or os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "data", "agent.db")
    )

    since_iso = _parse_relative_date(since_raw) if since_raw else None
    until_iso = _parse_relative_date(until_raw) if until_raw else None

    # KB-21: build SQL-derived visible-account whitelist (personal + team scope)
    visible_account_ids = _get_visible_account_ids(user_id, db_path)

    # Account resolution: fuzzy-match natural-language names → exact account_id
    account, resolved_account_str, account_was_resolved = _resolve_account_param(
        account_raw, db_path=db_path, user_id=user_id
    )

    # G24: check if account filter refers to known accounts
    account_found = True
    if account:
        account_found = _account_exists_in_kb(account, chroma_path, user_id)

    # Build query embedding
    embedding = _embed_query(query, api_key)

    def _apply_time_filter(results, since, until):
        """Filter results list by published_at in Python (ISO string comparison)."""
        if not since and not until:
            return results
        filtered = []
        for r in results:
            pub = r.get("published_at", "")
            if not pub:
                filtered.append(r)
                continue
            if since and pub < since:
                continue
            if until and pub > until:
                continue
            filtered.append(r)
        return filtered

    def _make_result(doc, meta, dist=None):
        meta = meta or {}
        pub_at = meta.get("published_at", "")
        domains_str = meta.get("domains", "")
        raw_score = round(1.0 - float(dist), 4) if dist is not None else 0.0

        # KB-16: freshness boost — adjust relevance score by staleness factor
        boosted_score = raw_score
        if _FRESHNESS_BOOST_ENABLED and dist is not None:
            ttl_days_raw = meta.get("freshness_ttl_days", -1)
            ttl_days = None if ttl_days_raw == -1 else ttl_days_raw
            try:
                sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
                from kb.freshness_classifier import freshness_boost
                boost = freshness_boost(ttl_days, pub_at)
                boosted_score = round(raw_score * boost, 4)
            except Exception:
                pass

        return {
            "text": doc,
            "source_url": meta.get("source_url", ""),
            "author": meta.get("author", ""),
            "account_id": meta.get("account_id", ""),
            "domain": domains_str.split("|")[0] if domains_str else "",
            "platform": meta.get("platform", ""),
            "published_at": pub_at,
            "published_age": _human_age(pub_at),
            "score": boosted_score,
            "source": meta.get("source", ""),
            "contradicts_chunk_id": meta.get("contradicts_chunk_id", ""),
            "freshness_category": meta.get("freshness_category", ""),
        }

    def _run_by_date():
        """
        Retrieve ALL chunks matching the metadata filter via col.get(), then
        sort/filter in Python.  Used for date_asc / date_desc — avoids the
        semantic over-fetch problem where recent items with low similarity
        scores are excluded from the candidate pool before date sorting.
        """
        where = _build_where(user_id, domain, account, source, None, None, language,
                             visible_account_ids=visible_account_ids)
        import chromadb
        try:
            client = chromadb.PersistentClient(path=chroma_path)
            col = client.get_collection(collection_name)
        except Exception:
            return []
        try:
            kwargs = {"include": ["documents", "metadatas"], "limit": 2000}
            if where:
                kwargs["where"] = where
            raw = col.get(**kwargs)
        except Exception as exc:
            sys.stderr.write(f"ChromaDB get error: {exc}\n")
            return []
        docs = raw.get("documents") or []
        metas = raw.get("metadatas") or []
        return [_make_result(d, m) for d, m in zip(docs, metas)]

    def _run_by_relevance():
        """
        Retrieve top-N chunks by semantic similarity via col.query().
        Used for relevance sort; also used as fallback pool for time-filtered
        relevance queries (over-fetch then post-filter in Python).
        """
        where = _build_where(user_id, domain, account, source, None, None, language,
                             visible_account_ids=visible_account_ids)
        import chromadb
        try:
            client = chromadb.PersistentClient(path=chroma_path)
            col = client.get_collection(collection_name)
        except Exception:
            return []
        count = col.count()
        if count == 0:
            return []
        # Over-fetch when time filter is active so post-filter has enough candidates
        fetch_count = n_results
        if since_iso or until_iso:
            fetch_count = max(50, int(n_results * _DATE_SORT_OVERFETCH_FACTOR))
        kwargs = {
            "query_embeddings": [embedding],
            "n_results": min(fetch_count, count),
            "include": ["documents", "distances", "metadatas"],
        }
        if where:
            kwargs["where"] = where
        try:
            raw = col.query(**kwargs)
        except Exception as exc:
            sys.stderr.write(f"ChromaDB query error: {exc}\n")
            return []
        docs = raw.get("documents", [[]])[0]
        distances = raw.get("distances", [[]])[0]
        metas = raw.get("metadatas", [[]])[0] or [{}] * len(docs)
        return [_make_result(d, m, dist) for d, m, dist in zip(docs, metas, distances)]

    # Choose retrieval strategy based on sort mode
    if sort_by in ("date_asc", "date_desc"):
        # Date queries: fetch all matching chunks (no semantic ranking bias),
        # apply time filter, sort chronologically, trim to n_results.
        all_results = _run_by_date()
        results = _apply_time_filter(all_results, since_iso, until_iso)

        # G10: auto-retry without time constraint if too few results
        extended_window = False
        if len(results) < _MIN_RESULTS_BEFORE_RETRY and (since_iso or until_iso):
            results = all_results
            extended_window = True

        results.sort(
            key=lambda r: r["published_at"] or "",
            reverse=(sort_by == "date_desc"),
        )
    else:
        # Relevance queries: semantic search, post-filter by time, no re-sort.
        all_results = _run_by_relevance()
        results = _apply_time_filter(all_results, since_iso, until_iso)

        extended_window = False
        if len(results) < _MIN_RESULTS_BEFORE_RETRY and (since_iso or until_iso):
            results = all_results
            extended_window = True

    results = results[:n_results]

    domain_filter_str = ""
    if domain:
        domain_filter_str = "|".join(domain) if isinstance(domain, list) else domain

    # KB-16: log citations for all accounts returned in results
    cited_accounts = list({r["account_id"] for r in results if r.get("account_id")})
    if cited_accounts:
        try:
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
            from kb.source_reliability import log_citations
            log_citations(cited_accounts, user_id, query, db_path=db_path)
        except Exception:
            pass  # citation log is non-blocking

    return {
        "results": results,
        "query_meta": {
            "account_found": account_found,
            "extended_window": extended_window,
            "result_count": len(results),
            "collection_searched": collection_name,
            "domain_filter_applied": domain_filter_str,
            "time_filter_applied": bool(since_iso or until_iso) and not extended_window,
            "resolved_account": resolved_account_str,
        },
    }


def main():
    if len(sys.argv) < 3:
        print("Usage: handler.py input.json output.json", file=sys.stderr)
        sys.exit(1)
    input_path, output_path = sys.argv[1], sys.argv[2]
    try:
        data = json.loads(open(input_path).read())
        params = data.get("parameters", {})
        result = execute(params)
        output = {"success": True, "result": result, "error": None, "logs": []}
    except Exception as exc:
        output = {"success": False, "result": None, "error": str(exc), "logs": []}
    with open(output_path, "w") as f:
        json.dump(output, f)


if __name__ == "__main__":
    main()
