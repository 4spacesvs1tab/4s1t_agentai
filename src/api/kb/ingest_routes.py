"""
KB ingestion, briefs, stats, schedule, documents, predictions, reliability,
and action-item inbox endpoints.
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field

from api.kb._deps import require_2fa, get_brief_service, get_ingestion_service
from application.brief_service import BriefService
from application.ingestion_service import IngestionService
from core.db_path import get_db_path
from utils.logger import setup_logger

logger = setup_logger(__name__)

router = APIRouter()

# ===========================================================================
# Module-level constants
# ===========================================================================

_DOCS_SUBDIR = "docs"
_ALLOWED_CONTENT_TYPES = {"application/pdf"}
_MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MB
_VALID_PREDICTION_STATUSES = {"pending", "verified", "failed", "inconclusive", "expired"}


# ===========================================================================
# Pydantic models
# ===========================================================================

class IngestBody(BaseModel):
    domain: Optional[str] = None
    max_items_per_platform: int = Field(50, ge=1, le=500)


class GenerateBriefsBody(BaseModel):
    domain: Optional[str] = Field(None, description="Single domain ID to regenerate, or null for all domains")


class ScheduleUpdateBody(BaseModel):
    brief_enabled: Optional[bool] = None
    brief_days: Optional[List[str]] = Field(
        None,
        description="Day abbreviations to generate briefs on, e.g. ['mon','wed','fri']. Empty list = disabled."
    )
    brief_time: Optional[str] = Field(None, description="UTC time HH:MM")
    brief_min_items: Optional[int] = None


class PredictionStatusBody(BaseModel):
    status: str = Field(
        ...,
        description="New status: 'pending' | 'verified' | 'failed' | 'inconclusive' | 'expired'",
    )
    verification_evidence: Optional[str] = None
    verification_url: Optional[str] = None


class ActionItemStatusBody(BaseModel):
    status: str = Field(..., description="New status: 'pending' | 'done' | 'dismissed'")


# ===========================================================================
# Helpers
# ===========================================================================

def _briefs_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent.parent / "data" / "briefs"


def _docs_dir() -> Path:
    data_root = Path(os.environ.get("FILE_READ_BASE_DIR", str(Path(str(get_db_path())).parent)))
    d = data_root / _DOCS_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run_document_load(
    pdf_path: str,
    source_tag: str,
    domain: str,
    author: str,
    published_at: str,
    user_id: str,
) -> None:
    """Background task: load a PDF into ChromaDB via document_loader."""
    try:
        from kb.bootstrap.document_loader import load_document
        n = load_document(
            pdf_path=pdf_path,
            source_tag=source_tag,
            domain=domain,
            author=author,
            published_at=published_at,
            user_id=user_id,
        )
        logger.info("Document indexed: %s → %d chunks (domain=%s)", pdf_path, n, domain)
    except Exception as exc:
        logger.error("Document indexing failed for %s: %s", pdf_path, exc)


# ===========================================================================
# Manual ingestion trigger
# ===========================================================================

@router.post("/ingest", status_code=status.HTTP_202_ACCEPTED)
async def trigger_ingestion(
    body: IngestBody,
    current_user: dict = Depends(require_2fa),
    svc: "IngestionService" = Depends(get_ingestion_service),
):
    """
    Trigger a manual KB ingestion run for the authenticated user.

    Runs in the background (fire-and-forget). Returns immediately with a
    202 Accepted. Results are stored in ChromaDB and kb_ingestion_log.
    """
    return await svc.trigger_ingestion(
        user_id=current_user["id"],
        domain=body.domain,
        max_items_per_platform=body.max_items_per_platform,
    )


@router.post("/generate-briefs", status_code=status.HTTP_202_ACCEPTED)
async def trigger_brief_generation(
    request: Request,
    body: GenerateBriefsBody = GenerateBriefsBody(),
    current_user: dict = Depends(require_2fa),
    svc: "BriefService" = Depends(get_brief_service),
):
    """
    Trigger immediate brief generation + NIP-17 delivery for the authenticated user.

    Optional body: {"domain": "macroeconomics"} to regenerate a single domain.
    Reuses the shared agent infrastructure (same ApiClient / Tor circuit as the live app).
    Returns 202 immediately; generation runs in the background.
    """
    return await svc.generate_briefs(
        user_id=current_user["id"],
        domain_filter=body.domain,
        infra=request.app.state.agent_infra,
    )


# ===========================================================================
# Stats
# ===========================================================================

@router.get("/stats")
async def get_kb_stats(
    domain: Optional[str] = None,
    current_user: dict = Depends(require_2fa),
):
    """
    Return ingestion statistics per domain / account / platform.

    Response shape::

        {
          "by_domain": [{"domain": str, "account_count": int, "item_count": int, "last_ingested": str|null}],
          "by_account": [{"account_id": str, "display_name": str, "domain": str,
                          "item_count": int, "last_ingested": str|null}],
          "by_platform": [{"platform": str, "item_count": int, "last_ingested": str|null}]
        }
    """
    user_id = current_user["id"]
    try:
        conn = sqlite3.connect(str(get_db_path()))
        conn.row_factory = sqlite3.Row

        # Per-domain: account count + ingestion log count
        domain_filter_clause = "AND a.domains LIKE ?" if domain else ""
        domain_params: list = [user_id]
        if domain:
            domain_params.append(f"%{domain}%")

        cur = conn.execute(
            f"""
            SELECT
                a.domains,
                COUNT(DISTINCT a.id) AS account_count,
                COUNT(l.id)          AS item_count,
                MAX(l.ingested_at)   AS last_ingested
            FROM kb_accounts a
            LEFT JOIN kb_ingestion_log l ON l.account_id = a.id AND l.user_id = a.user_id AND l.status = 'ok'
            WHERE a.user_id = ? AND a.active = 1 {domain_filter_clause}
            GROUP BY a.domains
            ORDER BY a.domains
            """,
            domain_params,
        )
        raw_domain = [dict(r) for r in cur.fetchall()]

        # Flatten pipe-separated domains into individual rows
        domain_agg: dict[str, dict] = defaultdict(lambda: {"domain": "", "account_count": 0, "item_count": 0, "last_ingested": None})
        for row in raw_domain:
            for d in (row["domains"] or "").split("|"):
                d = d.strip()
                if not d:
                    continue
                domain_agg[d]["domain"] = d
                domain_agg[d]["account_count"] += row["account_count"]
                domain_agg[d]["item_count"] += row["item_count"]
                last = row["last_ingested"]
                prev = domain_agg[d]["last_ingested"]
                if last and (prev is None or last > prev):
                    domain_agg[d]["last_ingested"] = last
        by_domain = sorted(domain_agg.values(), key=lambda x: x["domain"])

        # Per-account
        account_params: list = [user_id]
        if domain:
            account_params.append(f"%{domain}%")
        cur2 = conn.execute(
            f"""
            SELECT
                a.id AS account_id,
                a.display_name,
                a.domains,
                a.layer,
                COUNT(l.id)        AS item_count,
                MAX(l.ingested_at) AS last_ingested
            FROM kb_accounts a
            LEFT JOIN kb_ingestion_log l ON l.account_id = a.id AND l.user_id = a.user_id AND l.status = 'ok'
            WHERE a.user_id = ? AND a.active = 1 {domain_filter_clause}
            GROUP BY a.id
            ORDER BY item_count DESC
            """,
            account_params,
        )
        by_account = [dict(r) for r in cur2.fetchall()]

        # Per-platform
        platform_params: list = [user_id]
        cur3 = conn.execute(
            """
            SELECT
                platform,
                COUNT(*) AS item_count,
                MAX(ingested_at) AS last_ingested
            FROM kb_ingestion_log
            WHERE user_id = ? AND status = 'ok'
            GROUP BY platform
            ORDER BY item_count DESC
            """,
            platform_params,
        )
        by_platform = [dict(r) for r in cur3.fetchall()]

        # Per-account per-platform (for Accounts page per-platform breakdown)
        ap_params: list = [user_id]
        if domain:
            ap_params.append(f"%{domain}%")
        cur4 = conn.execute(
            f"""
            SELECT
                a.id          AS account_id,
                al.platform,
                COUNT(l.id)   AS item_count,
                MAX(l.ingested_at) AS last_ingested
            FROM kb_accounts a
            JOIN kb_account_aliases al ON al.account_id = a.id
            LEFT JOIN kb_ingestion_log l
                ON l.account_id = a.id
               AND l.user_id   = a.user_id
               AND l.platform  = al.platform
               AND l.status    = 'ok'
            WHERE a.user_id = ? AND a.active = 1 {domain_filter_clause}
            GROUP BY a.id, al.platform
            ORDER BY a.id, al.platform
            """,
            ap_params,
        )
        by_account_platform = [dict(r) for r in cur4.fetchall()]

        conn.close()
        return {
            "by_domain": by_domain,
            "by_account": by_account,
            "by_platform": by_platform,
            "by_account_platform": by_account_platform,
        }
    except sqlite3.Error as exc:
        logger.error("get_kb_stats failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to load KB stats")


# ===========================================================================
# Briefs (file-system reader)
# ===========================================================================

@router.get("/briefs")
async def list_briefs(
    domain: Optional[str] = None,
    current_user: dict = Depends(require_2fa),
    svc: "BriefService" = Depends(get_brief_service),
):
    """
    List available brief files with delivered status and preview.

    Returns [{"domain", "date", "filename", "size", "preview", "delivered"}] sorted newest-first.
    """
    return {"briefs": await svc.list_briefs(domain=domain)}


@router.get("/briefs/{domain}/{date}")
async def read_brief(
    domain: str,
    date: str,
    current_user: dict = Depends(require_2fa),
    svc: "BriefService" = Depends(get_brief_service),
):
    """
    Return the markdown content of a specific brief.
    date format: YYYY-MM-DD
    """
    try:
        return await svc.get_brief(domain=domain, date=date)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Brief not found")


# ===========================================================================
# Schedule (brief_days + brief_time per domain)
# ===========================================================================

@router.get("/schedule")
async def get_schedule(current_user: dict = Depends(require_2fa)):
    """Return per-domain schedule config for the authenticated user."""
    user_id = current_user["id"]
    from kb.brief_config import get_brief_config_service
    svc = get_brief_config_service(str(get_db_path()))
    configs = svc.get_all(user_id)
    return {
        "schedule": [
            {
                "domain": c.domain,
                "brief_enabled": c.brief_enabled,
                "brief_days": c.brief_days,
                "brief_time": c.brief_time,
                "brief_min_items": c.brief_min_items,
            }
            for c in configs
        ]
    }


@router.put("/schedule/{domain}")
async def update_schedule(
    domain: str,
    body: ScheduleUpdateBody,
    current_user: dict = Depends(require_2fa),
):
    """Update schedule for a specific domain."""
    user_id = current_user["id"]
    from kb.brief_config import get_brief_config_service
    import json as _json
    svc = get_brief_config_service(str(get_db_path()))
    kwargs: dict = {}
    if body.brief_enabled is not None:
        kwargs["brief_enabled"] = body.brief_enabled
    if body.brief_days is not None:
        kwargs["brief_days"] = _json.dumps(body.brief_days)
        # Derive brief_frequency from days for scheduler compatibility
        all_days = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
        if not body.brief_days:
            kwargs["brief_frequency"] = "disabled"
        elif set(body.brief_days) >= all_days:
            kwargs["brief_frequency"] = "daily"
        else:
            kwargs["brief_frequency"] = "custom"
    if body.brief_time is not None:
        kwargs["brief_time"] = body.brief_time
    if body.brief_min_items is not None:
        kwargs["brief_min_items"] = body.brief_min_items
    if kwargs:
        svc.update(user_id=user_id, domain=domain, **kwargs)
    return {"status": "ok"}


# ===========================================================================
# Document upload
# ===========================================================================

@router.post("/documents/upload", status_code=202)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    domain: str = "ba",
    source_tag: str = "",
    author: str = "Unknown",
    published_at: str = "1970-01-01",
    current_user: dict = Depends(require_2fa),
):
    """
    Upload a PDF and index it into the KB vector store (background).

    Returns 202 Accepted immediately; indexing runs asynchronously.
    """
    import re as _re

    # Validate content type
    ct = file.content_type or ""
    if ct not in _ALLOWED_CONTENT_TYPES:
        # Accept even if browser sends generic octet-stream for .pdf
        fname = (file.filename or "").lower()
        if not fname.endswith(".pdf"):
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Only PDF files are accepted.",
            )

    # Sanitize filename
    safe_name = _re.sub(r"[^A-Za-z0-9._\-]", "_", Path(file.filename or "upload").name)
    if not safe_name.lower().endswith(".pdf"):
        safe_name += ".pdf"

    # Derive source_tag from filename if not supplied
    if not source_tag:
        source_tag = _re.sub(r"[^a-z0-9_]", "_", safe_name.lower().removesuffix(".pdf"))[:64]

    # Read and size-check
    data = await file.read()
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large (max {_MAX_UPLOAD_BYTES // 1024 // 1024} MB).",
        )
    if len(data) < 5 or data[:4] != b"%PDF":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File does not appear to be a valid PDF.",
        )

    # Save to disk
    dest = _docs_dir() / safe_name
    dest.write_bytes(data)
    logger.info(
        "PDF upload saved: %s (%.1f KB, domain=%s, source_tag=%s, user=%s)",
        dest, len(data) / 1024, domain, source_tag, current_user["id"],
    )

    # Queue background indexing
    background_tasks.add_task(
        _run_document_load,
        pdf_path=str(dest),
        source_tag=source_tag,
        domain=domain,
        author=author,
        published_at=published_at,
        user_id=current_user["id"],
    )

    return {
        "status": "accepted",
        "message": f"PDF '{safe_name}' saved. Indexing started in background.",
        "filename": safe_name,
        "source_tag": source_tag,
        "domain": domain,
    }


@router.get("/documents")
async def list_documents(current_user: dict = Depends(require_2fa)):
    """List uploaded PDF documents with basic metadata."""
    import datetime as _dt

    docs_dir = _docs_dir()
    docs = []
    for p in sorted(docs_dir.glob("*.pdf"), key=lambda x: x.stat().st_mtime, reverse=True):
        st = p.stat()
        docs.append({
            "filename": p.name,
            "size_kb": round(st.st_size / 1024, 1),
            "uploaded_at": _dt.datetime.fromtimestamp(st.st_mtime, tz=_dt.timezone.utc).isoformat(),
        })
    return {"documents": docs}


# ===========================================================================
# Predictions (KB-15)
# ===========================================================================

@router.get("/predictions")
async def list_predictions(
    account_id: Optional[str] = None,
    status_filter: Optional[str] = None,
    limit: int = 100,
    current_user: dict = Depends(require_2fa),
):
    """
    List extracted predictions for the authenticated user.

    Query params:
      account_id:    filter to one source account
      status_filter: 'pending' | 'verified' | 'failed' | 'inconclusive' | 'expired'
      limit:         max rows returned (default 100)
    """
    user_id = current_user["id"]
    try:
        conn = sqlite3.connect(str(get_db_path()))
        conn.row_factory = sqlite3.Row

        query = """
            SELECT p.id, p.source_account, a.display_name AS account_name,
                   p.source_chunk_id, p.prediction_text, p.predicted_outcome,
                   p.predicted_date, p.confidence_stated, p.extracted_at,
                   p.verification_status, p.verified_at,
                   p.verification_evidence, p.verification_url
            FROM   kb_predictions p
            LEFT JOIN kb_accounts a ON a.id = p.source_account
            WHERE  p.user_id = ?
        """
        params: list = [user_id]

        if account_id:
            query += " AND p.source_account = ?"
            params.append(account_id)
        if status_filter:
            query += " AND p.verification_status = ?"
            params.append(status_filter)

        query += " ORDER BY p.extracted_at DESC LIMIT ?"
        params.append(limit)

        rows = [dict(r) for r in conn.execute(query, params).fetchall()]
        conn.close()
        return {"predictions": rows}
    except sqlite3.Error as exc:
        logger.error("list_predictions failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to load predictions")


@router.get("/predictions/leaderboard")
async def predictions_leaderboard(
    current_user: dict = Depends(require_2fa),
):
    """
    Per-account prediction accuracy leaderboard.

    Returns accounts sorted by accuracy (verified / (verified + failed)).
    Accounts with no resolved predictions are listed at the end.
    """
    user_id = current_user["id"]
    try:
        conn = sqlite3.connect(str(get_db_path()))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                p.source_account                                        AS account,
                COALESCE(a.display_name, p.source_account)             AS account_name,
                SUM(CASE WHEN p.verification_status = 'verified'    THEN 1 ELSE 0 END) AS correct,
                SUM(CASE WHEN p.verification_status = 'failed'      THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN p.verification_status = 'inconclusive' THEN 1 ELSE 0 END) AS inconclusive,
                SUM(CASE WHEN p.verification_status = 'pending'     THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN p.verification_status = 'expired'     THEN 1 ELSE 0 END) AS expired,
                COUNT(*)                                               AS total
            FROM kb_predictions p
            LEFT JOIN kb_accounts a ON a.id = p.source_account
            WHERE p.user_id = ?
            GROUP BY p.source_account
            ORDER BY
                (CASE WHEN (SUM(CASE WHEN p.verification_status IN ('verified','failed') THEN 1 ELSE 0 END)) > 0
                      THEN CAST(SUM(CASE WHEN p.verification_status='verified' THEN 1 ELSE 0 END) AS REAL)
                           / SUM(CASE WHEN p.verification_status IN ('verified','failed') THEN 1 ELSE 0 END)
                      ELSE -1
                 END) DESC
            """,
            (user_id,),
        ).fetchall()
        conn.close()

        result = []
        for r in rows:
            resolved = (r["correct"] or 0) + (r["failed"] or 0)
            accuracy = round(r["correct"] / resolved, 3) if resolved > 0 else None
            result.append({
                "account": r["account"],
                "account_name": r["account_name"],
                "correct": r["correct"] or 0,
                "failed": r["failed"] or 0,
                "inconclusive": r["inconclusive"] or 0,
                "pending": r["pending"] or 0,
                "expired": r["expired"] or 0,
                "total": r["total"] or 0,
                "accuracy": accuracy,
            })
        return {"leaderboard": result}
    except sqlite3.Error as exc:
        logger.error("predictions_leaderboard failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to load leaderboard")


@router.patch("/predictions/{prediction_id}/status", status_code=status.HTTP_204_NO_CONTENT)
async def update_prediction_status(
    prediction_id: str,
    body: PredictionStatusBody,
    current_user: dict = Depends(require_2fa),
):
    """Manually update the verification status of a prediction."""
    if body.status not in _VALID_PREDICTION_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid status. Must be one of: {sorted(_VALID_PREDICTION_STATUSES)}",
        )
    user_id = current_user["id"]
    from datetime import datetime, timezone as _tz
    now = datetime.now(_tz.utc).isoformat()
    try:
        conn = sqlite3.connect(str(get_db_path()))
        cur = conn.execute(
            """
            UPDATE kb_predictions
            SET    verification_status   = ?,
                   verified_at           = ?,
                   verification_evidence = COALESCE(?, verification_evidence),
                   verification_url      = COALESCE(?, verification_url)
            WHERE  id = ? AND user_id = ?
            """,
            (
                body.status,
                now,
                body.verification_evidence,
                body.verification_url,
                prediction_id,
                user_id,
            ),
        )
        conn.commit()
        changed = cur.rowcount
        conn.close()
        if changed == 0:
            raise HTTPException(status_code=404, detail="Prediction not found")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("update_prediction_status failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to update prediction")


@router.post("/predictions/verify", status_code=status.HTTP_202_ACCEPTED)
async def trigger_prediction_verification(
    current_user: dict = Depends(require_2fa),
):
    """
    Trigger an immediate prediction verification run for the authenticated user.

    Runs asynchronously; returns 202. Progress visible in logs.
    """
    import asyncio

    user_id = current_user["id"]
    db = str(get_db_path())
    api_key = os.environ.get("NANO_GPT_API_KEY", "")

    async def _run():
        import concurrent.futures
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            def _verify_sync():
                from kb.prediction_verifier import PredictionVerifier
                v = PredictionVerifier(api_key=api_key, db_path=db)
                return v.verify_pending(user_id=user_id)
            counts = await loop.run_in_executor(pool, _verify_sync)
            logger.info("Manual prediction verification user=%s: %s", user_id, counts)

    asyncio.create_task(_run())
    return {"status": "accepted", "message": f"Prediction verification started for user={user_id!r}."}


# ===========================================================================
# Source Reliability (KB-16)
# ===========================================================================

@router.get("/reliability")
async def list_reliability_scores(current_user: dict = Depends(require_2fa)):
    """
    Return source reliability scores for all accounts, ordered by overall_score DESC.

    Scores are computed from three rule-based signals:
      contradiction_rate — fraction of chunks that contradict another source (lower = better)
      activity_score     — relative ingestion activity in last 30 days
      citation_rate      — relative citation frequency in search results
    """
    from kb.source_reliability import get_reliability_service
    svc = get_reliability_service(str(get_db_path()))
    scores = svc.get_all_scores(user_id=current_user["id"])
    return {"scores": scores, "count": len(scores)}


@router.post("/reliability/recompute", status_code=status.HTTP_202_ACCEPTED)
async def recompute_reliability(
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(require_2fa),
):
    """
    Trigger a background recomputation of reliability scores for all accounts
    that have ingestion history for the authenticated user.
    """
    from kb.source_reliability import get_reliability_service

    user_id = current_user["id"]

    def _run():
        try:
            svc = get_reliability_service(str(get_db_path()))
            n = svc.recompute_all(user_id)
            logger.info("Reliability recompute done: %d accounts (user=%s)", n, user_id)
        except Exception as exc:
            logger.warning("Reliability recompute failed: %s", exc)

    background_tasks.add_task(_run)
    return {"status": "accepted", "message": "Reliability recomputation started in background"}


@router.get("/reliability/{account_id}")
async def get_account_reliability(
    account_id: str,
    current_user: dict = Depends(require_2fa),
):
    """Return the reliability score for a single account."""
    from kb.source_reliability import get_reliability_service
    svc = get_reliability_service(str(get_db_path()))
    score = svc.get_score(account_id)
    if score is None:
        raise HTTPException(status_code=404, detail=f"No reliability data for account '{account_id}'")
    return score


# ===========================================================================
# Action Item Inbox — Phase KB-17
# ===========================================================================

@router.get("/inbox")
async def list_inbox(
    status_filter: Optional[str] = None,
    urgency: Optional[str] = None,
    domain: Optional[str] = None,
    limit: int = 100,
    current_user: dict = Depends(require_2fa),
):
    """
    Return action items for the authenticated user.

    Query params:
      status_filter: 'pending' | 'done' | 'dismissed' (default: all)
      urgency:       'high' | 'normal' | 'low'
      domain:        filter by domain
      limit:         max rows (default 100)
    """
    user_id = current_user["id"]
    clauses = ["i.user_id = ?"]
    params: list = [user_id]

    if status_filter:
        clauses.append("i.status = ?")
        params.append(status_filter)
    if urgency:
        clauses.append("i.urgency = ?")
        params.append(urgency)
    if domain:
        clauses.append("i.domain = ?")
        params.append(domain)

    params.append(max(1, min(limit, 500)))
    where = " AND ".join(clauses)

    try:
        conn = sqlite3.connect(str(get_db_path()))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT i.id, i.source_chunk_id, i.source_account,
                   i.domain, i.action_text, i.urgency,
                   i.context_snippet, i.extracted_at, i.status, i.updated_at,
                   a.display_name AS account_name
            FROM kb_action_items i
            LEFT JOIN kb_accounts a ON a.id = i.source_account
            WHERE {where}
            ORDER BY
                CASE i.urgency WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,
                i.extracted_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        conn.close()
        return {"items": [dict(r) for r in rows]}
    except Exception as exc:
        logger.error("Failed to list inbox for user=%s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Failed to load inbox")


@router.get("/inbox/count")
async def inbox_count(current_user: dict = Depends(require_2fa)):
    """
    Return count of pending action items (for sidebar badge).

    Response: {"pending": <int>}
    """
    user_id = current_user["id"]
    try:
        conn = sqlite3.connect(str(get_db_path()))
        row = conn.execute(
            "SELECT COUNT(*) FROM kb_action_items WHERE user_id = ? AND status = 'pending'",
            (user_id,),
        ).fetchone()
        conn.close()
        return {"pending": (row[0] or 0) if row else 0}
    except sqlite3.Error as exc:
        logger.debug("inbox count failed for user=%s: %s", user_id, exc)
        return {"pending": 0}


@router.patch("/inbox/{item_id}", status_code=status.HTTP_200_OK)
async def update_inbox_item(
    item_id: str,
    body: ActionItemStatusBody,
    current_user: dict = Depends(require_2fa),
):
    """
    Update the status of an action item (mark done or dismissed).

    Accepted status values: 'pending' | 'done' | 'dismissed'
    """
    allowed = {"pending", "done", "dismissed"}
    if body.status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"status must be one of: {', '.join(sorted(allowed))}",
        )

    user_id = current_user["id"]
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()

    try:
        conn = sqlite3.connect(str(get_db_path()))
        conn.execute("PRAGMA journal_mode=WAL")
        result = conn.execute(
            "UPDATE kb_action_items SET status = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (body.status, now, item_id, user_id),
        )
        conn.commit()
        affected = result.rowcount
        conn.close()
    except sqlite3.Error as exc:
        logger.error("Failed to update inbox item %s: %s", item_id, exc)
        raise HTTPException(status_code=500, detail="Update failed")

    if affected == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

    return {"id": item_id, "status": body.status}


@router.post("/inbox/run", status_code=status.HTTP_202_ACCEPTED)
async def trigger_inbox_extraction(current_user: dict = Depends(require_2fa)):
    """
    Manually trigger action item extraction for today's ingested content.

    Returns 202 immediately; extraction runs in the background.
    """
    import asyncio

    user_id = current_user["id"]

    async def _run():
        import concurrent.futures
        loop = asyncio.get_event_loop()
        from kb.action_item_extractor import get_action_item_job
        job = get_action_item_job(db_path=str(get_db_path()))
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                count = await loop.run_in_executor(pool, job.run, user_id)
            logger.info("Manual inbox extraction: stored %d items for user=%s", count, user_id)
        except Exception as exc:
            logger.error("Manual inbox extraction failed for user=%s: %s", user_id, exc)

    asyncio.create_task(_run())
    return {"status": "accepted", "message": f"Action item extraction started for user={user_id!r}."}
