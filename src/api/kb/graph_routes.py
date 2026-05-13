"""
KB social graph, deployment config, teams, and wiki-pages endpoints.
/graph, /deployment-config, /teams, /wiki-pages
"""
from __future__ import annotations

import json
import sqlite3
import uuid as _uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from api.kb._deps import require_2fa
from core.db_path import get_db_path
from utils.logger import setup_logger

logger = setup_logger(__name__)

router = APIRouter()

_VALID_ROLES = {"owner", "admin", "member", "viewer"}


# ===========================================================================
# Pydantic models
# ===========================================================================

class DeploymentConfigBody(BaseModel):
    mode: str = Field("solo", description="'solo' | 'family' | 'team'")
    max_users: int = Field(1, ge=1)
    allow_registration: bool = False
    require_invite: bool = True


class TeamCreateBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None


class TeamUpdateBody(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    description: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None


class MemberAddBody(BaseModel):
    user_id: str
    role: str = Field("member", description="'admin' | 'member' | 'viewer'")


class MemberRoleBody(BaseModel):
    role: str = Field(..., description="'admin' | 'member' | 'viewer'")


class WikiPageGenerateBody(BaseModel):
    topic: str = Field(..., description="Topic to generate a wiki page about")
    force_refresh: bool = Field(False, description="Regenerate even if a cached page exists")


# ===========================================================================
# Helpers
# ===========================================================================

def _get_team_role(conn: sqlite3.Connection, team_id: str, user_id: str) -> Optional[str]:
    row = conn.execute(
        "SELECT role FROM team_members WHERE team_id = ? AND user_id = ?",
        (team_id, user_id),
    ).fetchone()
    return row["role"] if row else None


# ===========================================================================
# Relation graph
# ===========================================================================

@router.get("/graph")
async def get_kb_graph(
    domain: Optional[str] = None,
    account_id: Optional[str] = None,
    current_user: dict = Depends(require_2fa),
):
    """
    Return KB social graph data as JSON for Mermaid rendering.

    Response::

        {
          "nodes": [{"id": str, "label": str, "layer": int, "domains": str, "aliases": {}}],
          "edges": [{"from": str, "to": str, "relation_type": str, "weight": float,
                     "evidence_count": int}]
        }

    Filter by ``domain`` (substring match) or ``account_id`` (ego-graph: 1-hop neighbours).
    """
    user_id = current_user["id"]
    try:
        conn = sqlite3.connect(str(get_db_path()))
        conn.row_factory = sqlite3.Row

        # Load accounts
        acc_params: list = [user_id]
        acc_where = "WHERE a.user_id = ? AND a.active = 1"
        if domain:
            acc_where += " AND a.domains LIKE ?"
            acc_params.append(f"%{domain}%")
        if account_id:
            # Ego-graph: load the focal account + direct neighbours
            cur = conn.execute(
                f"""
                SELECT DISTINCT a.id, a.display_name, a.layer, a.domains
                FROM kb_accounts a
                WHERE a.user_id = ? AND a.active = 1
                  AND (a.id = ?
                       OR a.id IN (SELECT to_account_id FROM kb_relations WHERE from_account_id = ?)
                       OR a.id IN (SELECT from_account_id FROM kb_relations WHERE to_account_id = ?))
                """,
                [user_id, account_id, account_id, account_id],
            )
        else:
            cur = conn.execute(
                f"SELECT a.id, a.display_name, a.layer, a.domains FROM kb_accounts a {acc_where}",
                acc_params,
            )
        acc_rows = [dict(r) for r in cur.fetchall()]
        acc_ids = [r["id"] for r in acc_rows]

        # Load aliases for those accounts
        aliases_map: dict[str, dict] = {r["id"]: {} for r in acc_rows}
        if acc_ids:
            placeholders = ",".join("?" * len(acc_ids))
            cur2 = conn.execute(
                f"SELECT account_id, platform, platform_id FROM kb_account_aliases WHERE account_id IN ({placeholders})",
                acc_ids,
            )
            for row in cur2.fetchall():
                aliases_map[row["account_id"]][row["platform"]] = row["platform_id"]

        nodes = [
            {
                "id": r["id"],
                "label": r["display_name"],
                "layer": r["layer"],
                "domains": r["domains"],
                "aliases": aliases_map.get(r["id"], {}),
            }
            for r in acc_rows
        ]

        # Load edges between the loaded nodes
        edges: list = []
        if acc_ids:
            placeholders = ",".join("?" * len(acc_ids))
            cur3 = conn.execute(
                f"""
                SELECT from_account_id, to_account_id, relation_type, weight, evidence_count
                FROM kb_relations
                WHERE from_account_id IN ({placeholders}) AND to_account_id IN ({placeholders})
                ORDER BY weight DESC
                """,
                acc_ids * 2,
            )
            edges = [
                {
                    "from": r["from_account_id"],
                    "to": r["to_account_id"],
                    "relation_type": r["relation_type"],
                    "weight": r["weight"],
                    "evidence_count": r["evidence_count"],
                }
                for r in cur3.fetchall()
            ]

        conn.close()
        return {"nodes": nodes, "edges": edges}
    except Exception as exc:
        logger.error("get_kb_graph failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to load graph data")


# ===========================================================================
# Deployment config
# ===========================================================================

@router.get("/deployment-config")
async def get_deployment_config(current_user: dict = Depends(require_2fa)):
    """Return the singleton deployment configuration row."""
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM deployment_config WHERE id = 1").fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="deployment_config row missing — run migration 026")
    return dict(row)


@router.put("/deployment-config")
async def update_deployment_config(
    body: DeploymentConfigBody,
    current_user: dict = Depends(require_2fa),
):
    """Update deployment config. Admin-only."""
    if not current_user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin required.")
    if body.mode not in ("solo", "family", "team"):
        raise HTTPException(status_code=422, detail="mode must be 'solo', 'family', or 'team'")
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """UPDATE deployment_config
           SET mode=?, max_users=?, allow_registration=?, require_invite=?
           WHERE id=1""",
        (body.mode, body.max_users, int(body.allow_registration), int(body.require_invite)),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM deployment_config WHERE id = 1").fetchone()
    conn.close()
    return dict(row)


# ===========================================================================
# Teams CRUD
# ===========================================================================

@router.get("/teams")
async def list_teams(current_user: dict = Depends(require_2fa)):
    """List all teams the authenticated user belongs to."""
    user_id = current_user["id"]
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT t.id, t.name, t.description, t.created_by, t.created_at, t.settings,
                  tm.role
           FROM teams t
           JOIN team_members tm ON tm.team_id = t.id
           WHERE tm.user_id = ?
           ORDER BY t.created_at DESC""",
        (user_id,),
    ).fetchall()
    conn.close()
    return {"teams": [
        {
            "id": r["id"],
            "name": r["name"],
            "description": r["description"],
            "created_by": r["created_by"],
            "created_at": r["created_at"],
            "settings": json.loads(r["settings"]) if r["settings"] else {},
            "my_role": r["role"],
        }
        for r in rows
    ]}


@router.post("/teams", status_code=status.HTTP_201_CREATED)
async def create_team(
    body: TeamCreateBody,
    current_user: dict = Depends(require_2fa),
):
    """Create a new team. Creator is automatically assigned the 'owner' role."""
    user_id = current_user["id"]
    team_id = str(_uuid.uuid4())
    settings_json = json.dumps(body.settings) if body.settings else None
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "INSERT INTO teams (id, name, description, created_by, settings) VALUES (?,?,?,?,?)",
        (team_id, body.name, body.description, user_id, settings_json),
    )
    conn.execute(
        "INSERT INTO team_members (team_id, user_id, role) VALUES (?,?,?)",
        (team_id, user_id, "owner"),
    )
    conn.commit()
    conn.close()
    return {"team_id": team_id, "name": body.name, "role": "owner"}


@router.get("/teams/{team_id}")
async def get_team(team_id: str, current_user: dict = Depends(require_2fa)):
    """Get team details (any member)."""
    user_id = current_user["id"]
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    role = _get_team_role(conn, team_id, user_id)
    if not role:
        conn.close()
        raise HTTPException(status_code=404, detail="Team not found or access denied.")
    row = conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Team not found.")
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "settings": json.loads(row["settings"]) if row["settings"] else {},
        "my_role": role,
    }


@router.patch("/teams/{team_id}")
async def update_team(
    team_id: str,
    body: TeamUpdateBody,
    current_user: dict = Depends(require_2fa),
):
    """Update team name/description/settings. Owner or admin only."""
    user_id = current_user["id"]
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    role = _get_team_role(conn, team_id, user_id)
    if role not in ("owner", "admin"):
        conn.close()
        raise HTTPException(status_code=403, detail="Owner or admin required.")
    updates, vals = [], []
    if body.name is not None:
        updates.append("name=?");         vals.append(body.name)
    if body.description is not None:
        updates.append("description=?");  vals.append(body.description)
    if body.settings is not None:
        updates.append("settings=?");     vals.append(json.dumps(body.settings))
    if updates:
        vals.append(team_id)
        conn.execute(f"UPDATE teams SET {', '.join(updates)} WHERE id=?", vals)
        conn.commit()
    row = conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
    conn.close()
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "settings": json.loads(row["settings"]) if row["settings"] else {},
    }


@router.delete("/teams/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_team(team_id: str, current_user: dict = Depends(require_2fa)):
    """Delete a team. Owner only."""
    user_id = current_user["id"]
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    role = _get_team_role(conn, team_id, user_id)
    if role != "owner":
        conn.close()
        raise HTTPException(status_code=403, detail="Only the team owner can delete it.")
    conn.execute("DELETE FROM team_members WHERE team_id = ?", (team_id,))
    conn.execute("DELETE FROM teams WHERE id = ?", (team_id,))
    conn.commit()
    conn.close()


# ===========================================================================
# Team members
# ===========================================================================

@router.get("/teams/{team_id}/members")
async def list_team_members(team_id: str, current_user: dict = Depends(require_2fa)):
    """List members of a team (any member)."""
    user_id = current_user["id"]
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    if not _get_team_role(conn, team_id, user_id):
        conn.close()
        raise HTTPException(status_code=404, detail="Team not found or access denied.")
    rows = conn.execute(
        """SELECT tm.user_id, tm.role, tm.joined_at, u.username
           FROM team_members tm
           LEFT JOIN users u ON u.id = tm.user_id
           WHERE tm.team_id = ?
           ORDER BY tm.joined_at""",
        (team_id,),
    ).fetchall()
    conn.close()
    return {"members": [
        {"user_id": r["user_id"], "username": r["username"], "role": r["role"], "joined_at": r["joined_at"]}
        for r in rows
    ]}


@router.post("/teams/{team_id}/members", status_code=status.HTTP_201_CREATED)
async def add_team_member(
    team_id: str,
    body: MemberAddBody,
    current_user: dict = Depends(require_2fa),
):
    """Add a member to the team. Owner or admin only."""
    user_id = current_user["id"]
    if body.role not in ("admin", "member", "viewer"):
        raise HTTPException(status_code=422, detail="role must be 'admin', 'member', or 'viewer'")
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    my_role = _get_team_role(conn, team_id, user_id)
    if my_role not in ("owner", "admin"):
        conn.close()
        raise HTTPException(status_code=403, detail="Owner or admin required.")
    # Check target user exists
    target = conn.execute("SELECT id FROM users WHERE id = ?", (body.user_id,)).fetchone()
    if not target:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found.")
    try:
        conn.execute(
            "INSERT OR REPLACE INTO team_members (team_id, user_id, role) VALUES (?,?,?)",
            (team_id, body.user_id, body.role),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.close()
        raise HTTPException(status_code=409, detail=str(exc))
    conn.close()
    return {"team_id": team_id, "user_id": body.user_id, "role": body.role}


@router.patch("/teams/{team_id}/members/{target_user_id}")
async def update_member_role(
    team_id: str,
    target_user_id: str,
    body: MemberRoleBody,
    current_user: dict = Depends(require_2fa),
):
    """Change a member's role. Owner only (cannot demote owner)."""
    user_id = current_user["id"]
    if body.role not in ("admin", "member", "viewer"):
        raise HTTPException(status_code=422, detail="role must be 'admin', 'member', or 'viewer'")
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    my_role = _get_team_role(conn, team_id, user_id)
    if my_role != "owner":
        conn.close()
        raise HTTPException(status_code=403, detail="Only the team owner can change roles.")
    target_role = _get_team_role(conn, team_id, target_user_id)
    if not target_role:
        conn.close()
        raise HTTPException(status_code=404, detail="Member not found.")
    if target_role == "owner":
        conn.close()
        raise HTTPException(status_code=409, detail="Cannot demote the owner.")
    conn.execute(
        "UPDATE team_members SET role=? WHERE team_id=? AND user_id=?",
        (body.role, team_id, target_user_id),
    )
    conn.commit()
    conn.close()
    return {"team_id": team_id, "user_id": target_user_id, "role": body.role}


@router.delete("/teams/{team_id}/members/{target_user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_team_member(
    team_id: str,
    target_user_id: str,
    current_user: dict = Depends(require_2fa),
):
    """Remove a member from the team. Owner or admin (cannot remove owner)."""
    user_id = current_user["id"]
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    my_role = _get_team_role(conn, team_id, user_id)
    if my_role not in ("owner", "admin"):
        conn.close()
        raise HTTPException(status_code=403, detail="Owner or admin required.")
    target_role = _get_team_role(conn, team_id, target_user_id)
    if not target_role:
        conn.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Member not found.")
    if target_role == "owner":
        conn.close()
        raise HTTPException(status_code=409, detail="Cannot remove the team owner.")
    conn.execute(
        "DELETE FROM team_members WHERE team_id=? AND user_id=?",
        (team_id, target_user_id),
    )
    conn.commit()
    conn.close()


# ===========================================================================
# Team KB accounts (shared accounts within a team)
# ===========================================================================

@router.get("/teams/{team_id}/accounts")
async def list_team_accounts(team_id: str, current_user: dict = Depends(require_2fa)):
    """List KB accounts shared with a team (scope='team', scope_id=team_id)."""
    user_id = current_user["id"]
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    if not _get_team_role(conn, team_id, user_id):
        conn.close()
        raise HTTPException(status_code=404, detail="Team not found or access denied.")
    rows = conn.execute(
        """SELECT id, display_name, domains, layer, scope, scope_id
           FROM kb_accounts WHERE scope='team' AND scope_id=?
           ORDER BY display_name""",
        (team_id,),
    ).fetchall()
    conn.close()
    return {"accounts": [dict(r) for r in rows]}


# ===========================================================================
# Wiki Pages (KB-23)
# ===========================================================================

@router.get("/wiki-pages")
async def list_wiki_pages(current_user: dict = Depends(require_2fa)):
    """List all wiki pages for the authenticated user (no content body)."""
    from kb.wiki_service import get_wiki_service
    user_id = current_user["id"]
    svc = get_wiki_service()
    pages = svc.list(user_id)
    return {"pages": pages}


@router.get("/wiki-pages/{topic}")
async def get_wiki_page(topic: str, current_user: dict = Depends(require_2fa)):
    """
    Retrieve a wiki page by its topic slug (e.g. 'fed-rate-policy').
    Returns 404 if the page has not been generated yet.
    """
    from kb.wiki_service import get_wiki_service
    user_id = current_user["id"]
    svc = get_wiki_service()
    page = svc.get(user_id, topic)
    if not page:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Wiki page '{topic}' not found. POST to /wiki-pages to generate it.",
        )
    return page


@router.post("/wiki-pages", status_code=status.HTTP_201_CREATED)
async def generate_wiki_page(
    body: WikiPageGenerateBody,
    current_user: dict = Depends(require_2fa),
):
    """
    Generate (or refresh) a wiki page for a topic.

    - If a page for this topic already exists and force_refresh=false, the
      cached version is returned (HTTP 201 with cached=true).
    - Set force_refresh=true to regenerate; the version counter increments.
    """
    from kb.wiki_service import get_wiki_service
    user_id = current_user["id"]
    svc = get_wiki_service()
    result = svc.generate(user_id=user_id, topic=body.topic, force_refresh=body.force_refresh)
    if result.get("error"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=result["error"],
        )
    return result


@router.delete("/wiki-pages/{page_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_wiki_page(page_id: str, current_user: dict = Depends(require_2fa)):
    """Delete a wiki page by its UUID (owner-only)."""
    from kb.wiki_service import get_wiki_service
    user_id = current_user["id"]
    svc = get_wiki_service()
    ok = svc.delete(page_id, user_id)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Wiki page '{page_id}' not found.",
        )
