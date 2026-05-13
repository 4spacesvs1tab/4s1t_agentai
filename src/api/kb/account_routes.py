"""KB account management endpoints — /accounts."""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from api.kb._deps import get_account_service, require_2fa
from application.account_service import AccountService
from core.db_path import get_db_path
from utils.logger import setup_logger

import os

logger = setup_logger(__name__)

router = APIRouter()


# ===========================================================================
# Pydantic models
# ===========================================================================

class AccountCreateBody(BaseModel):
    display_name: str
    layer: int = 1
    domains: str = Field(..., description="Pipe-separated domain IDs")
    aliases: Optional[Dict[str, str]] = Field(
        default=None,
        description="Platform → handle/URL map, e.g. {\"twitter\": \"@handle\"}"
    )


class BackfillBody(BaseModel):
    platforms: Optional[List[str]] = Field(
        None,
        description="Platforms to backfill (e.g. ['podcast']). Omit to backfill all aliases.",
    )
    max_items: int = Field(200, ge=1, le=2000, description="Max items per platform alias.")


class AccountUpdateBody(BaseModel):
    display_name: Optional[str] = None
    domains: Optional[str] = None
    layer: Optional[int] = None


class AliasUpsertBody(BaseModel):
    platform_id: str = Field(..., description="Handle/URL for the platform")


class ConsolidatePreviewBody(BaseModel):
    main_id: str = Field(..., description="Account ID that will be kept")
    secondary_ids: List[str] = Field(..., min_length=1, description="Account IDs to merge into main")


class ConsolidateConfirmBody(BaseModel):
    main_id: str = Field(..., description="Account ID that will be kept")
    secondary_ids: List[str] = Field(..., min_length=1, description="Account IDs to merge into main")


# ===========================================================================
# Helpers
# ===========================================================================

def _load_account_full(conn: sqlite3.Connection, account_id: str, user_id: str) -> Optional[dict]:
    """Load a single account with its aliases as a dict."""
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT a.*, GROUP_CONCAT(al.platform || ':' || al.platform_id) AS aliases_raw
        FROM kb_accounts a
        LEFT JOIN kb_account_aliases al ON a.id = al.account_id
        WHERE a.id = ? AND a.user_id = ? AND a.active = 1
        GROUP BY a.id
        """,
        (account_id, user_id),
    ).fetchone()
    if row is None:
        return None
    data = dict(row)
    raw = data.pop("aliases_raw", None) or ""
    aliases: Dict[str, str] = {}
    for pair in raw.split(","):
        if ":" in pair:
            plat, pid = pair.split(":", 1)
            aliases[plat] = pid
    data["aliases"] = aliases
    data["domains_list"] = [d for d in (data.get("domains") or "").split("|") if d]
    return data


def _merge_preview(main: dict, secondaries: list[dict]) -> dict:
    """Compute the merged account state without modifying the DB."""
    merged_aliases = dict(main["aliases"])
    for sec in secondaries:
        for plat, pid in sec["aliases"].items():
            if plat not in merged_aliases:
                merged_aliases[plat] = pid

    all_domains: list[str] = list(main["domains_list"])
    for sec in secondaries:
        for d in sec["domains_list"]:
            if d not in all_domains:
                all_domains.append(d)

    return {
        "id": main["id"],
        "display_name": main["display_name"],
        "layer": main["layer"],
        "domains": "|".join(all_domains),
        "domains_list": all_domains,
        "aliases": merged_aliases,
        "added_by": main.get("added_by", "user"),
    }


# ===========================================================================
# Accounts CRUD
# ===========================================================================

@router.get("/accounts")
async def list_kb_accounts(
    layer: Optional[int] = None,
    domain: Optional[str] = None,
    current_user: dict = Depends(require_2fa),
    svc: AccountService = Depends(get_account_service),
):
    """List KB accounts for the authenticated user, optionally filtered by layer or domain."""
    try:
        rows = await svc.list_accounts(
            user_id=current_user["id"],
            layer=layer,
            domain=domain,
        )
        return {"accounts": rows}
    except Exception as exc:
        logger.error("list_kb_accounts failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to load accounts")


@router.post("/accounts", status_code=status.HTTP_201_CREATED)
async def create_kb_account(
    body: AccountCreateBody,
    current_user: dict = Depends(require_2fa),
    svc: AccountService = Depends(get_account_service),
):
    """Add a new KB account (L1 or L2) with optional platform aliases."""
    try:
        account_id = await svc.add_account(
            user_id=current_user["id"],
            display_name=body.display_name,
            layer=body.layer,
            domains=body.domains,
            aliases=body.aliases,
        )
        return {"account_id": account_id}
    except Exception as exc:
        logger.error("create_kb_account failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to create account")


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_kb_account(
    account_id: str,
    current_user: dict = Depends(require_2fa),
    svc: AccountService = Depends(get_account_service),
):
    """Deactivate a KB account (soft delete — sets active=0)."""
    try:
        found = await svc.remove_account(
            account_id=account_id,
            user_id=current_user["id"],
        )
        if not found:
            raise HTTPException(status_code=404, detail="Account not found")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("deactivate_kb_account failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to deactivate account")


# ===========================================================================
# Account backfill  (E3-continuation: move to BackfillService)
# ===========================================================================

@router.post("/accounts/{account_id}/backfill", status_code=status.HTTP_202_ACCEPTED)
async def backfill_kb_account(
    account_id: str,
    body: BackfillBody,
    current_user: dict = Depends(require_2fa),
):
    """
    Load ALL available historical content for a single account, ignoring cursors.

    Runs in the background (fire-and-forget). Returns 202 immediately.
    Does NOT modify the scheduled ingestion cursor — normal incremental
    ingestion continues unaffected after backfill completes.

    Use case: new account added; user wants full history loaded now.
    """
    import asyncio

    user_id = current_user["id"]
    db = str(get_db_path())
    api_key = os.environ.get("NANO_GPT_API_KEY", "")

    # Run backfill in a thread pool executor (in-process).
    # _ingestion_lock inside backfill_account() serialises it with the scheduler:
    # backfill waits until the current scheduler domain finishes, then runs while
    # the scheduler is sleeping.  Peak memory = scheduler's residual RSS + ~355 MB
    # for ChromaDB/embeddings — well within the 6 GB cgroup limit.
    # (Subprocess approach was abandoned because the subprocess has its own copy
    # of _ingestion_lock, so scheduler and backfill ran concurrently → OOM.)
    def _run_backfill():
        from kb.ingestion.ingestion_runner import backfill_account
        try:
            results = backfill_account(
                account_id=account_id,
                user_id=user_id,
                platforms=body.platforms,
                max_items=body.max_items,
                db_path=db,
                api_key=api_key,
            )
            total_chunks = sum(r.chunks_stored for r in results)
            total_items = sum(r.items_fetched for r in results)
            logger.info(
                "Backfill finished account=%s user=%s items=%d chunks=%d",
                account_id, user_id, total_items, total_chunks,
            )
        except Exception as exc:
            logger.error("Backfill failed account=%s user=%s: %s", account_id, user_id, exc)

    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _run_backfill)
    return {"status": "accepted", "account_id": account_id}


# ===========================================================================
# Account edit / alias management / blacklist  (E3-continuation: move to AccountService)
# ===========================================================================

@router.patch("/accounts/{account_id}")
async def update_kb_account(
    account_id: str,
    body: AccountUpdateBody,
    current_user: dict = Depends(require_2fa),
    svc: AccountService = Depends(get_account_service),
):
    """Update display_name, domains, or layer of a KB account."""
    if body.display_name is None and body.domains is None and body.layer is None:
        return {"status": "no_change"}
    try:
        found = await svc.update_account(
            account_id=account_id,
            user_id=current_user["id"],
            display_name=body.display_name,
            domains=body.domains,
            layer=body.layer,
        )
        if not found:
            raise HTTPException(status_code=404, detail="Account not found")
        return {"status": "updated"}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("update_kb_account failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to update account")


@router.put("/accounts/{account_id}/aliases/{platform}")
async def upsert_kb_alias(
    account_id: str,
    platform: str,
    body: AliasUpsertBody,
    current_user: dict = Depends(require_2fa),
):
    """Add or update a single platform alias on a KB account."""
    user_id = current_user["id"]
    try:
        conn = sqlite3.connect(str(get_db_path()))
        # Verify ownership
        cur = conn.execute(
            "SELECT id FROM kb_accounts WHERE id = ? AND user_id = ? AND active = 1",
            (account_id, user_id),
        )
        if not cur.fetchone():
            conn.close()
            raise HTTPException(status_code=404, detail="Account not found")
        conn.execute(
            """
            INSERT OR REPLACE INTO kb_account_aliases
                (account_id, platform, platform_id, confidence, verified)
            VALUES (?, ?, ?, 1.0, 1)
            """,
            (account_id, platform, body.platform_id),
        )
        conn.commit()
        conn.close()
        return {"status": "ok", "platform": platform, "platform_id": body.platform_id}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("upsert_kb_alias failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to upsert alias")


@router.delete("/accounts/{account_id}/aliases/{platform}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_kb_alias(
    account_id: str,
    platform: str,
    current_user: dict = Depends(require_2fa),
):
    """Remove a platform alias from a KB account."""
    user_id = current_user["id"]
    try:
        conn = sqlite3.connect(str(get_db_path()))
        cur = conn.execute(
            "SELECT id FROM kb_accounts WHERE id = ? AND user_id = ? AND active = 1",
            (account_id, user_id),
        )
        if not cur.fetchone():
            conn.close()
            raise HTTPException(status_code=404, detail="Account not found")
        conn.execute(
            "DELETE FROM kb_account_aliases WHERE account_id = ? AND platform = ?",
            (account_id, platform),
        )
        conn.commit()
        conn.close()
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("delete_kb_alias failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to delete alias")


@router.post("/accounts/{account_id}/blacklist", status_code=status.HTTP_204_NO_CONTENT)
async def blacklist_kb_account(
    account_id: str,
    current_user: dict = Depends(require_2fa),
):
    """
    Permanently blacklist a KB account from future L2 discovery.

    Sets active=0 and added_by='blacklisted' so the entity extractor
    and discovery manager skip it permanently.
    """
    user_id = current_user["id"]
    try:
        conn = sqlite3.connect(str(get_db_path()))
        cur = conn.execute(
            "UPDATE kb_accounts SET active = 0, added_by = 'blacklisted' WHERE id = ? AND user_id = ?",
            (account_id, user_id),
        )
        conn.commit()
        changed = cur.rowcount
        conn.close()
        if changed == 0:
            raise HTTPException(status_code=404, detail="Account not found")
        # Also blacklist any pending discovery candidates with this name
        # (best-effort, ignore errors)
        try:
            conn2 = sqlite3.connect(str(get_db_path()))
            conn2.execute(
                "UPDATE kb_discovery_queue SET status = 'rejected' WHERE discovered_via = ? AND user_id = ?",
                (account_id, user_id),
            )
            conn2.commit()
            conn2.close()
        except Exception:
            pass
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("blacklist_kb_account failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to blacklist account")


# ===========================================================================
# Account consolidation  (E3-continuation: move to AccountService)
# ===========================================================================

@router.post("/accounts/consolidate/preview")
async def consolidate_preview(
    body: ConsolidatePreviewBody,
    current_user: dict = Depends(require_2fa),
):
    """
    Preview what the consolidated account will look like without modifying any data.

    Returns:
      - details of each account being consolidated
      - the merged result that would be created
      - relation counts that will be repointed
    """
    user_id = current_user["id"]
    all_ids = [body.main_id] + body.secondary_ids

    try:
        conn = sqlite3.connect(str(get_db_path()))
        conn.row_factory = sqlite3.Row

        accounts = []
        for aid in all_ids:
            acc = _load_account_full(conn, aid, user_id)
            if acc is None:
                conn.close()
                raise HTTPException(status_code=404, detail=f"Account {aid!r} not found")
            accounts.append(acc)

        main = accounts[0]
        secondaries = accounts[1:]

        # Count relations involving secondary accounts
        placeholders = ",".join("?" * len(body.secondary_ids))
        relation_rows = conn.execute(
            f"""
            SELECT COUNT(*) AS cnt FROM kb_relations
            WHERE from_account_id IN ({placeholders}) OR to_account_id IN ({placeholders})
            """,
            body.secondary_ids + body.secondary_ids,
        ).fetchone()
        relation_count = relation_rows["cnt"] if relation_rows else 0

        # Count ingestion cursors
        cursor_rows = conn.execute(
            f"""
            SELECT COUNT(*) AS cnt FROM kb_ingestion_cursors
            WHERE account_id IN ({placeholders}) AND user_id = ?
            """,
            body.secondary_ids + [user_id],
        ).fetchone()
        cursor_count = cursor_rows["cnt"] if cursor_rows else 0

        conn.close()

        merged = _merge_preview(main, secondaries)

        return {
            "main": main,
            "secondaries": secondaries,
            "merged": merged,
            "relations_to_repoint": relation_count,
            "cursors_to_repoint": cursor_count,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("consolidate_preview failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to build consolidation preview")


@router.post("/accounts/consolidate/confirm", status_code=status.HTTP_200_OK)
async def consolidate_confirm(
    body: ConsolidateConfirmBody,
    current_user: dict = Depends(require_2fa),
):
    """
    Execute account consolidation:
      1. Copy secondary aliases into main (main wins on conflict)
      2. Union domains
      3. Repoint kb_relations edges
      4. Repoint kb_ingestion_cursors
      5. Mark secondaries inactive with consolidated_into=main_id
      6. Reload social graph singleton
    """
    user_id = current_user["id"]

    try:
        conn = sqlite3.connect(str(get_db_path()))
        conn.row_factory = sqlite3.Row

        # Validate all accounts exist and belong to this user
        all_ids = [body.main_id] + body.secondary_ids
        accounts: dict[str, dict] = {}
        for aid in all_ids:
            acc = _load_account_full(conn, aid, user_id)
            if acc is None:
                conn.close()
                raise HTTPException(status_code=404, detail=f"Account {aid!r} not found")
            accounts[aid] = acc

        main = accounts[body.main_id]
        secondaries = [accounts[sid] for sid in body.secondary_ids]
        merged = _merge_preview(main, secondaries)

        # 1. Copy secondary aliases into main (skip conflicts)
        for sec in secondaries:
            for plat, pid in sec["aliases"].items():
                if plat not in main["aliases"]:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO kb_account_aliases
                            (account_id, platform, platform_id, confidence, verified)
                        VALUES (?, ?, ?, 1.0, 1)
                        """,
                        (body.main_id, plat, pid),
                    )

        # 2. Update main account domains
        conn.execute(
            "UPDATE kb_accounts SET domains = ? WHERE id = ? AND user_id = ?",
            (merged["domains"], body.main_id, user_id),
        )

        # 3. Repoint kb_relations edges (secondary → main), remove self-loops and duplicates
        for sec_id in body.secondary_ids:
            # Repoint from_account_id
            conn.execute(
                """
                UPDATE OR IGNORE kb_relations
                SET from_account_id = ?
                WHERE from_account_id = ? AND to_account_id != ?
                """,
                (body.main_id, sec_id, body.main_id),
            )
            # Remove self-loops that arose from the remap
            conn.execute(
                "DELETE FROM kb_relations WHERE from_account_id = ? AND to_account_id = ?",
                (body.main_id, body.main_id),
            )
            # Repoint to_account_id
            conn.execute(
                """
                UPDATE OR IGNORE kb_relations
                SET to_account_id = ?
                WHERE to_account_id = ? AND from_account_id != ?
                """,
                (body.main_id, sec_id, body.main_id),
            )
            conn.execute(
                "DELETE FROM kb_relations WHERE from_account_id = ? AND to_account_id = ?",
                (body.main_id, body.main_id),
            )

        # 4. Repoint kb_ingestion_cursors (skip if main already has a cursor for that platform)
        for sec_id in body.secondary_ids:
            cursors = conn.execute(
                "SELECT platform FROM kb_ingestion_cursors WHERE account_id = ? AND user_id = ?",
                (sec_id, user_id),
            ).fetchall()
            for cur_row in cursors:
                plat = cur_row["platform"]
                exists = conn.execute(
                    "SELECT 1 FROM kb_ingestion_cursors WHERE account_id = ? AND user_id = ? AND platform = ?",
                    (body.main_id, user_id, plat),
                ).fetchone()
                if not exists:
                    conn.execute(
                        """
                        UPDATE kb_ingestion_cursors
                        SET account_id = ?
                        WHERE account_id = ? AND user_id = ? AND platform = ?
                        """,
                        (body.main_id, sec_id, user_id, plat),
                    )
                else:
                    conn.execute(
                        "DELETE FROM kb_ingestion_cursors WHERE account_id = ? AND user_id = ? AND platform = ?",
                        (sec_id, user_id, plat),
                    )

        # 5. Soft-delete secondary accounts and record consolidated_into
        placeholders = ",".join("?" * len(body.secondary_ids))
        conn.execute(
            f"""
            UPDATE kb_accounts
            SET active = 0, consolidated_into = ?
            WHERE id IN ({placeholders}) AND user_id = ?
            """,
            [body.main_id] + body.secondary_ids + [user_id],
        )

        conn.commit()
        conn.close()

        # 6. Reload social graph singleton
        try:
            from kb.social_graph import get_social_graph
            get_social_graph().reload(user_id)
        except Exception as exc:
            logger.warning("Failed to reload social graph after consolidation: %s", exc)

        logger.info(
            "Consolidated %d accounts into %s for user=%s",
            len(body.secondary_ids), body.main_id, user_id,
        )
        return {
            "status": "ok",
            "main_id": body.main_id,
            "deactivated": body.secondary_ids,
            "merged_domains": merged["domains"],
            "merged_aliases": merged["aliases"],
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("consolidate_confirm failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to consolidate accounts")


# ===========================================================================
# Account scope (team sharing)  (E3-continuation: move to AccountService)
# ===========================================================================

@router.patch("/accounts/{account_id}/scope")
async def set_account_scope(
    account_id: str,
    scope: str,
    scope_id: Optional[str] = None,
    current_user: dict = Depends(require_2fa),
):
    """
    Set the scope of a KB account ('personal' | 'family' | 'team').
    For 'team' scope, scope_id must be the team_id the caller belongs to.
    """
    user_id = current_user["id"]
    if scope not in ("personal", "family", "team"):
        raise HTTPException(status_code=422, detail="scope must be 'personal', 'family', or 'team'")
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    # Verify account ownership
    row = conn.execute(
        "SELECT id FROM kb_accounts WHERE id=? AND user_id=?", (account_id, user_id)
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Account not found or access denied.")
    if scope == "team":
        if not scope_id:
            conn.close()
            raise HTTPException(status_code=422, detail="scope_id (team_id) required for scope='team'")
        team_row = conn.execute(
            "SELECT role FROM team_members WHERE team_id = ? AND user_id = ?",
            (scope_id, user_id),
        ).fetchone()
        if not team_row:
            conn.close()
            raise HTTPException(status_code=403, detail="Not a member of that team.")
    conn.execute(
        "UPDATE kb_accounts SET scope=?, scope_id=? WHERE id=?",
        (scope, scope_id, account_id),
    )
    conn.commit()
    conn.close()
    return {"account_id": account_id, "scope": scope, "scope_id": scope_id}
