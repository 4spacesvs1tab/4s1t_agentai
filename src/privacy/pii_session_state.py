"""
Per-workflow PII session state for 4S1T Agent AI.

PIISessionState is created once per OrchestratorAgent workflow and shared
across all BaseAgent instances spawned within that workflow. It tracks:

  approved_proceed  — user chose "proceed as-is (remember for this workflow)"
                      so subsequent tasks in the same workflow skip the alert
  scrub_session     — user chose "enable scrubbing for this workflow"
                      so all remaining tasks are scrubbed
  reverse_maps      — list of placeholder→original dicts accumulated across tasks;
                      used when restore-after-response is needed

State is purely in-memory and never persisted. It resets when the workflow ends.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PIISessionState:
    """
    Workflow-scoped PII decision state.

    Attributes:
        approved_proceed: True after user selects "proceed as-is for this workflow".
                          Prevents repeated NIP-17 alerts within one workflow run.
        scrub_session:    True after user selects "enable scrubbing for this workflow".
                          Forces scrubbing on all subsequent tasks in the workflow.
        reverse_maps:     Accumulated placeholder→original value maps. Each scrub()
                          call appends one dict. Used for optional response restoration.
    """
    approved_proceed: bool = False
    scrub_session: bool = False
    reverse_maps: list[dict[str, str]] = field(default_factory=list)
