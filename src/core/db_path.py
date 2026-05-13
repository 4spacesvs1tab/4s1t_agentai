from pathlib import Path
import os


def get_db_path() -> Path:
    """Return the SQLite database path.

    Resolution order:
    1. DATABASE_URL env var (sqlite:///... format)
    2. AGENT_DB_PATH env var (raw path)
    3. Safe default: <repo_root>/data/agent.db

    parents[0] = src/core/
    parents[1] = src/
    parents[2] = repo root
    """
    db_url = os.getenv("DATABASE_URL", "")
    if db_url.startswith("sqlite:///"):
        return Path(db_url.removeprefix("sqlite:///"))
    agent_db = os.getenv("AGENT_DB_PATH", "")
    if agent_db:
        return Path(agent_db)
    return Path(__file__).resolve().parents[2] / "data" / "agent.db"
