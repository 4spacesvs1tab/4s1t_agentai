# Agent Orchestration

This document explains how 4S1T decomposes tasks into parallel work, assigns them to specialised agents, and coordinates execution through skills.

---

## Overview

When you send a task to the system, it goes through three layers:

```
User input
    │
    ▼
OrchestratorAgent          — decomposes task into a TaskGraph
    │
    ├─► Wave 1 (parallel)  — spawns worker agents
    │       ├─► ba_agent
    │       ├─► research_agent
    │       └─► data_agent
    │
    ├─► Wave 2 (parallel, uses Wave 1 results)
    │       └─► synthesis_agent
    │
    └─► Final result        — sent to user via web UI + Nostr DM
```

Each worker agent runs an independent tool-call loop and returns a result. The orchestrator compresses results between waves and passes the compressed context into the next wave.

---

## OrchestratorAgent

**Source:** [src/agents/orchestrator.py](../../src/agents/orchestrator.py)

The orchestrator is responsible for:

1. **Task decomposition** — uses an LLM to convert the user's request into a `TaskGraph`: a list of subtasks with dependencies
2. **Wave scheduling** — groups independent subtasks into waves; tasks in the same wave run in parallel
3. **Result compression** — after each wave, compresses all results so the next wave's context stays within the model's token budget
4. **Audit logging** — emits `WORKFLOW_START`, `WAVE_COMPLETE`, and `WORKFLOW_END` events to the append-only audit log

### Task Graph

A `TaskGraph` (see [src/agents/task_graph.py](../../src/agents/task_graph.py)) is a directed acyclic graph where:
- Each **node** is a subtask assigned to one persona type
- **Edges** represent dependencies (a task cannot start until its dependencies complete)
- Tasks with no pending dependencies form a *wave* and execute concurrently

Example task graph for "Analyse our CRM data and write a stakeholder report":

```
Wave 1 (parallel):
  [research_agent]  → research CRM market context
  [data_agent]      → analyse CRM dataset
  [ba_agent]        → identify stakeholders from requirements

Wave 2 (uses Wave 1 results):
  [synthesis_agent] → produce final stakeholder report
```

---

## BaseAgent

**Source:** [src/agents/base_agent.py](../../src/agents/base_agent.py)

Every worker agent (ba_agent, data_agent, etc.) is an instance of `BaseAgent`. The base agent implements the core execution loop:

```
1. Receive task + context
2. Build system prompt from persona (variant selected randomly — see Privacy Layer)
3. Call LLM with tools list
4. Parse tool calls from response
5. For each tool call:
     a. Check skill is in persona's allowed_skills list
     b. If skill requires_approval → send Nostr DM, wait for user reply
     c. Execute skill via SkillExecutor
     d. Append result to conversation
6. If context > 80% of model's token budget → compact (summarise) conversation
7. Repeat from step 3 until LLM returns a final answer (no tool calls)
8. Return result to orchestrator
```

**Maximum steps:** 20 tool-call iterations per agent. If reached without a final answer, the agent returns the best partial result with a warning.

**Context compaction:** When the accumulated conversation exceeds 80% of the model's token window, the agent calls the LLM to produce a summary, replaces the conversation with the summary, and continues. This enables long-running multi-step workflows.

---

## Personas

**Source:** [src/agents/personas.py](../../src/agents/personas.py)

A persona defines everything about a worker agent's identity and capability:

```python
@dataclass
class Persona:
    agent_type: str
    system_prompt: str
    system_prompt_variants: list[str]   # for fingerprint resistance
    allowed_skills: list[str]           # capability whitelist (FR-14)
    model_preference: str               # "reasoning" | "fast" | "coding" | "general"
    requires_approval: list[str]        # HITL gate for sensitive skills (FR-15)
```

### Registered Personas

| Persona | Role | Allowed Skills | Model Preference | Approval Required |
|---|---|---|---|---|
| `ba_agent` | CBAP-certified business analyst | babok_lookup, stakeholder_analysis, process_model, requirements_template, gap_analysis, web_search, file_read, get_current_datetime | reasoning | gap_analysis |
| `data_agent` | Data analyst (Python/Pandas/Matplotlib) | python_execute, data_read, chart_generate, export_results, web_search, file_read, get_current_datetime | coding | python_execute |
| `research_agent` | Research and information synthesis | web_search, web_scrape, knowledge_base_search, file_read, get_current_datetime | general | _(none)_ |
| `synthesis_agent` | Technical writer / report composer | report_generate, file_write, knowledge_base_search, get_current_datetime | general | _(none)_ |

### Model Preferences

Each persona declares a preference that is resolved by `ModelSelector` to an actual model:

| Preference | Default model | Why |
|---|---|---|
| `reasoning` | deepseek-r1 | Complex logic, BABOK analysis |
| `coding` | qwen3-coder | Code generation and data analysis |
| `fast` | deepseek-v3.2 | Quick responses, orchestration |
| `general` | user-configured fallback | Research, synthesis |

---

## Skills Framework

**Source:** [src/skills/](../../src/skills/)

Skills are the agent's actions — anything that touches the outside world (web, filesystem, code execution) must go through a skill.

### Skill Execution Flow

```
BaseAgent calls skill "web_search" with args
    │
    ▼
SkillExecutor checks:
  1. Is skill in persona's allowed_skills? (persona-level gate)
  2. Is skill registered in registry? (existence check)
  3. Does skill require approval? (HITL gate via Nostr)
    │
    ▼
Skill subprocess launched with:
  - Restricted environment (only declared secrets as env vars)
  - Resource limits (CPU, memory, file descriptors via setrlimit)
  - Timeout enforcement
    │
    ▼
Result returned to BaseAgent as tool response
AuditLog entry written (SKILL_CALL or SKILL_ERROR)
```

### Skill Catalogue

| Skill | What it does | Network | Filesystem | Approval |
|---|---|---|---|---|
| `web_search` | DuckDuckGo search (no API key needed) | Yes | No | No |
| `web_scrape` | Fetch and extract content from a URL | Yes | No | No |
| `python_execute` | Run Python code in sandboxed Docker container | No | tmp only | **Yes** |
| `data_read` | Parse Excel/CSV files | No | Read | No |
| `file_read` | Read files from whitelist | No | Read | No |
| `file_write` | Write files to whitelist | No | Write | No |
| `chart_generate` | Generate PNG charts with matplotlib | No | Write | No |
| `export_results` | Export results to xlsx/pdf/html | No | Write | No |
| `report_generate` | Compose formatted reports | No | Write | No |
| `babok_lookup` | RAG lookup in BABOK knowledge base (ChromaDB) | No | No | No |
| `knowledge_base_search` | RAG search in general knowledge base | No | No | No |
| `stakeholder_analysis` | Generate structured stakeholder analysis | No | No | No |
| `process_model` | Produce BPMN-ready process model | No | No | No |
| `gap_analysis` | Identify requirements gaps | No | No | **Yes** |
| `requirements_template` | Generate BA requirements template | No | No | No |
| `get_current_datetime` | Return current date/time | No | No | No |

### Skill Security Model

Each skill directory contains a `meta.json` that declares its security profile:

```json
{
  "name": "python_execute",
  "description": "Execute Python code in an isolated sandbox",
  "agent_scope": ["data_agent"],
  "timeout_seconds": 60,
  "network_allowed": false,
  "filesystem_access": "tmp_only",
  "secrets": [],
  "requires_approval": true
}
```

The executor enforces these constraints at the OS level (not just policy):
- **Resource limits** via POSIX `setrlimit`: CPU seconds, memory bytes, max file descriptors, max processes
- **Secrets** passed only as environment variables; never written to disk or logs
- **`python_execute`** runs inside the air-gapped executor Docker container (`network_mode: none`, read-only filesystem)

---

## Human-in-the-Loop (HITL) Approval

When an agent reaches a skill marked `requires_approval: true`, execution pauses:

1. Agent sends a NIP-17 DM to the user describing the pending action and its arguments
2. Agent polls relays for a reply containing `yes`/`approve` or `no`/`deny`
3. On `yes`: skill executes and agent continues
4. On `no`: skill is skipped; agent is informed and produces an alternative

This ensures sensitive operations (code execution, gap analysis) always have a human checkpoint, even when the agent is running autonomously.

---

## Audit Log

Every significant event is written to an append-only SQLite audit log via `src/core/audit.py`.

| Event type | When emitted |
|---|---|
| `WORKFLOW_START` | Orchestrator receives a new task |
| `WAVE_COMPLETE` | A parallel wave finishes |
| `WORKFLOW_END` | All waves complete; result delivered |
| `AGENT_SPAWN` | A worker agent is created |
| `SKILL_CALL` | A skill begins execution |
| `SKILL_ERROR` | A skill raises an exception |
| `AGENT_ERROR` | An agent fails |
| `AUTH_*` | Login, logout, failed auth attempts |

Writes are non-blocking: events are queued and drained by a background worker so audit logging does not slow down the agent loop.

---

## Adding a New Persona

1. Add a `Persona` entry to `_PERSONAS` in [src/agents/personas.py](../../src/agents/personas.py)
2. Provide at least 3 `system_prompt_variants` (for fingerprint resistance)
3. Declare `allowed_skills` — only skills listed here can be called
4. Set `requires_approval` for any skill that should gate on user confirmation
5. Optionally create a dedicated agent class in `src/agents/` (or reuse `BaseAgent` directly)

## Adding a New Skill

1. Create a directory under `src/skills/<skill_name>/`
2. Add `meta.json` with the security profile (see above)
3. Add the skill entry point (Python script or executable)
4. Register the skill in `src/skills/registry.py`
5. Add the skill name to the `allowed_skills` of any persona that should use it
