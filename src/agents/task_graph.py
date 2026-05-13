"""
TaskGraph — DAG model for multi-agent workflow decomposition.

SubTask   : one unit of work assigned to a specific agent type.
TaskGraph : the full DAG with topological wave ordering.

topological_waves() uses Kahn's algorithm to produce execution batches where
tasks within the same wave have no dependencies on each other and can run
concurrently via asyncio.gather().

Design reference: Design_aiAgentOrchestrationOfMany.md §3.5 (Phase 4)
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# SubTask
# ---------------------------------------------------------------------------

class SubTask(BaseModel):
    """One unit of work in a multi-agent workflow."""

    task_id: str = Field(..., description="Unique identifier within the workflow, e.g. 't1'")
    description: str = Field(..., description="Natural-language task description for the agent")
    agent_type: Literal[
        "ba_agent",
        "data_agent",
        "research_agent",
        "synthesis_agent",
    ] = Field(..., description="Which agent type should execute this task")
    model_preference: Literal["reasoning", "fast", "coding", "general"] = Field(
        default="general",
        description="Model tier hint (resolved by ModelSelector at runtime)",
    )
    depends_on: list[str] = Field(
        default_factory=list,
        description="task_id values this task must wait for before starting",
    )


# ---------------------------------------------------------------------------
# TaskGraph
# ---------------------------------------------------------------------------

class TaskGraph(BaseModel):
    """
    Directed acyclic graph of subtasks for a single workflow.

    workflow_id is propagated to every spawned agent so audit events
    can be correlated back to the originating orchestration run.
    """

    workflow_id: str
    subtasks: list[SubTask] = Field(default_factory=list)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def _validate_depends_on_refs(self) -> "TaskGraph":
        """All depends_on task_ids must refer to existing tasks in the graph."""
        known = {t.task_id for t in self.subtasks}
        for task in self.subtasks:
            for dep in task.depends_on:
                if dep not in known:
                    raise ValueError(
                        f"SubTask '{task.task_id}' depends on unknown task '{dep}'"
                    )
        return self

    # ------------------------------------------------------------------
    # Topological ordering (Kahn's algorithm)
    # ------------------------------------------------------------------

    def topological_waves(self) -> list[list[SubTask]]:
        """
        Return execution waves using Kahn's algorithm.

        Tasks within the same wave share no dependency edges and can
        execute in parallel (asyncio.gather).  Wave N+1 tasks depend on
        at least one task from wave N or earlier.

        Returns:
            List of waves, each wave being a list of SubTask objects.
            Empty list when the graph has no subtasks.

        Raises:
            ValueError: If the graph contains a cycle.
        """
        if not self.subtasks:
            return []

        task_map: dict[str, SubTask] = {t.task_id: t for t in self.subtasks}

        # Build in-degree and adjacency list (task_id → set of successors)
        in_degree: dict[str, int] = {tid: 0 for tid in task_map}
        successors: dict[str, list[str]] = {tid: [] for tid in task_map}

        for task in self.subtasks:
            for dep in task.depends_on:
                in_degree[task.task_id] += 1
                successors[dep].append(task.task_id)

        waves: list[list[SubTask]] = []
        ready: list[SubTask] = [t for t in self.subtasks if in_degree[t.task_id] == 0]

        while ready:
            waves.append(ready)
            next_ready: list[SubTask] = []
            for completed in ready:
                for successor_id in successors[completed.task_id]:
                    in_degree[successor_id] -= 1
                    if in_degree[successor_id] == 0:
                        next_ready.append(task_map[successor_id])
            ready = next_ready

        # If any task still has in_degree > 0, a cycle exists
        remaining = [tid for tid, deg in in_degree.items() if deg > 0]
        if remaining:
            raise ValueError(
                f"Cycle detected in task graph — the following tasks are "
                f"part of a cycle: {remaining}"
            )

        return waves
