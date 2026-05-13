"""
4S1T Agent AI — agents package.

Exports the public surface for creating and running agents.
"""
from agents.agent_result import AgentResult
from agents.personas import Persona, get_persona, all_agent_types
from agents.base_agent import BaseAgent
from agents.task_graph import SubTask, TaskGraph
from agents.orchestrator import OrchestratorAgent

__all__ = [
    "AgentResult",
    "Persona",
    "get_persona",
    "all_agent_types",
    "BaseAgent",
    "SubTask",
    "TaskGraph",
    "OrchestratorAgent",
]
