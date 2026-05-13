"""
BriefGenerationPort — domain interface for brief generation.

Rule: this file must never import agents/, infrastructure/, httpx, sqlite3,
os.environ, or any I/O library. Only standard-library ABCs are allowed here.
"""
from abc import ABC, abstractmethod


class BriefGenerationPort(ABC):
    """Abstract brief generation service.

    Implementations live in src/infrastructure/agents/.
    Wire a concrete adapter at the composition root (lifespan.py).
    """

    @abstractmethod
    async def generate_domain_brief(
        self,
        domain: str,
        user_id: str,
        today: str,
    ) -> str:
        """Generate and write a brief for one domain.

        Args:
            domain: KB domain identifier (e.g. "domain_a").
            user_id: User for whom to generate the brief (enforces user isolation).
            today: ISO date string e.g. "2026-04-15".

        Returns:
            Output text from the agent (used for logging character count).
        """
        ...
