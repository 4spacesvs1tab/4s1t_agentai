#!/usr/bin/env python3
"""
requirements_template skill handler — BA artifact template generator.

Returns a pre-structured template for the requested artifact type.

Input:  {"parameters": {"artifact_type": "use_case", "project_name": "..."}}
Output: {"success": true, "result": {"template_markdown": "...", "sections": [...], "guidance": "..."}}
"""
import json
import sys

_TEMPLATES: dict[str, dict] = {
    "use_case": {
        "sections": [
            "Use Case ID and Name", "Actors", "Preconditions", "Main Success Scenario",
            "Extensions / Alternate Flows", "Postconditions", "Business Rules",
            "Non-Functional Requirements", "Open Issues",
        ],
        "guidance": (
            "Fill in each section. The Main Success Scenario should be numbered steps. "
            "Extensions use the format '3a. If X then Y'. "
            "Reference BABOK v3 Chapter 7 (Solution Evaluation) for acceptance criteria guidance."
        ),
        "template": """\
# Use Case: {project_name}

**ID:** UC-XXX
**Version:** 1.0
**Date:** [Date]
**Author:** [Author]
**Status:** Draft

---

## Actors

| Actor | Type | Description |
|-------|------|-------------|
| [Primary Actor] | Primary | |
| [Secondary Actor] | Secondary | |

## Preconditions

- [Condition 1]
- [Condition 2]

## Main Success Scenario

1. [Step 1 — Actor does X]
2. [Step 2 — System responds with Y]
3. [Step 3 — ...]

## Extensions (Alternate Flows)

**3a. [Condition]:**
1. [Alternate step]
2. [Return to step 3 or end]

## Postconditions

- [Guaranteed outcome on success]

## Business Rules

| Rule ID | Description |
|---------|-------------|
| BR-XXX | |

## Non-Functional Requirements

- **Performance:** [e.g. Response within 2 seconds]
- **Security:** [e.g. Only authenticated users]

## Open Issues

| Issue | Owner | Due Date |
|-------|-------|----------|
| | | |
""",
    },
    "brd": {
        "sections": [
            "Executive Summary", "Business Objectives", "Project Scope", "Assumptions and Constraints",
            "Stakeholder Summary", "High-Level Requirements", "Business Rules",
            "Risks and Mitigation", "Glossary", "Approval",
        ],
        "guidance": (
            "A BRD captures what the business needs. Keep requirements measurable (SMART). "
            "Reference BABOK v3 Chapter 4 (Requirements Analysis and Design Definition)."
        ),
        "template": """\
# Business Requirements Document (BRD)
# {project_name}

**Version:** 1.0
**Date:** [Date]
**Author:** [Author]
**Status:** Draft

---

## 1. Executive Summary

[Brief description of the business problem and proposed solution.]

## 2. Business Objectives

| ID | Objective | Success Metric |
|----|-----------|----------------|
| BO-1 | | |

## 3. Project Scope

### In Scope
- [Item 1]

### Out of Scope
- [Item 1]

## 4. Assumptions and Constraints

| Type | Description |
|------|-------------|
| Assumption | |
| Constraint | |

## 5. Stakeholder Summary

| Stakeholder | Role | Influence | Interest |
|-------------|------|-----------|----------|
| | | High/Med/Low | High/Med/Low |

## 6. High-Level Requirements

| ID | Category | Requirement | Priority | Source |
|----|----------|-------------|----------|--------|
| BR-001 | Functional | | MoSCoW | |

## 7. Business Rules

| Rule ID | Description | Source |
|---------|-------------|--------|
| BR-R001 | | |

## 8. Risks and Mitigation

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|-----------|
| | H/M/L | H/M/L | |

## 9. Glossary

| Term | Definition |
|------|-----------|
| | |

## 10. Approval

| Role | Name | Signature | Date |
|------|------|-----------|------|
| Sponsor | | | |
| BA Lead | | | |
""",
    },
    "urd": {
        "sections": [
            "Introduction", "User Characteristics", "User Stories", "Acceptance Criteria",
            "Non-Functional Requirements", "Dependencies", "Glossary",
        ],
        "guidance": (
            "A URD focuses on what users need to accomplish. Write user stories in the format: "
            "'As a [role], I want [capability] so that [benefit].' "
            "Reference BABOK v3 Chapter 3 (Business Analysis Planning and Monitoring)."
        ),
        "template": """\
# User Requirements Document (URD)
# {project_name}

**Version:** 1.0
**Date:** [Date]
**Author:** [Author]

---

## 1. Introduction

[Purpose and scope of this document.]

## 2. User Characteristics

| User Type | Description | Technical Proficiency |
|-----------|-------------|----------------------|
| | | Low/Medium/High |

## 3. User Stories

| ID | User Story | Priority |
|----|------------|----------|
| US-001 | As a [role], I want [feature] so that [benefit] | Must Have |

## 4. Acceptance Criteria

| US ID | Criterion | Pass Condition |
|-------|-----------|----------------|
| US-001 | Given [context] When [action] Then [outcome] | |

## 5. Non-Functional Requirements

| Category | Requirement |
|----------|-------------|
| Usability | |
| Accessibility | |
| Performance | |

## 6. Dependencies

- [External system/service dependencies]

## 7. Glossary

| Term | Definition |
|------|-----------|
| | |
""",
    },
    "stakeholder_register": {
        "sections": [
            "Register Overview", "Stakeholder Entries", "Engagement Plan", "Communication Matrix",
        ],
        "guidance": (
            "The stakeholder register is a living document — update it throughout the project. "
            "Reference BABOK v3 Chapter 2 (Stakeholder Engagement)."
        ),
        "template": """\
# Stakeholder Register
# {project_name}

**Version:** 1.0
**Date:** [Date]
**Maintained By:** [Business Analyst]

---

## Stakeholder Entries

| ID | Name | Organisation | Role | Influence | Interest | Classification |
|----|------|-------------|------|-----------|----------|----------------|
| SH-001 | | | | High/Med/Low | High/Med/Low | Internal/External |

## Engagement Plan

| Stakeholder ID | Engagement Level | Approach | Key Messages |
|----------------|-----------------|----------|--------------|
| SH-001 | Keep Satisfied / Keep Informed / Manage Closely / Monitor | | |

## Communication Matrix

| Stakeholder | Information Needed | Frequency | Channel | Owner |
|-------------|-------------------|-----------|---------|-------|
| | | Weekly/Monthly/Ad-hoc | Email/Meeting/Report | |
""",
    },
    "business_case": {
        "sections": [
            "Executive Summary", "Problem Statement", "Proposed Solution",
            "Cost-Benefit Analysis", "Risk Assessment", "Recommendation",
        ],
        "guidance": (
            "A business case justifies investment. Quantify benefits and costs where possible. "
            "Reference BABOK v3 Chapter 5 (Strategy Analysis)."
        ),
        "template": """\
# Business Case
# {project_name}

**Version:** 1.0
**Date:** [Date]
**Author:** [Author]

---

## 1. Executive Summary

[One-paragraph summary of the opportunity and recommended action.]

## 2. Problem Statement

[Clear description of the problem or opportunity.]

**Impact of inaction:**
- [Consequence 1]

## 3. Proposed Solution

[Description of the recommended solution.]

### Alternatives Considered

| Option | Description | Pros | Cons |
|--------|-------------|------|------|
| A. Do nothing | | | |
| B. Recommended | | | |

## 4. Cost-Benefit Analysis

### Costs

| Item | Year 1 | Year 2 | Year 3 |
|------|--------|--------|--------|
| Implementation | | | |
| Ongoing | | | |
| **Total** | | | |

### Benefits

| Benefit | Year 1 | Year 2 | Year 3 |
|---------|--------|--------|--------|
| | | | |
| **Total** | | | |

**Payback period:** [N months]
**ROI:** [N%] over [N years]

## 5. Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|-----------|
| | H/M/L | H/M/L | |

## 6. Recommendation

[Clear recommendation with rationale.]
""",
    },
    "process_narrative": {
        "sections": [
            "Process Overview", "Scope", "Process Steps", "Roles and Responsibilities",
            "Inputs and Outputs", "Exception Handling", "KPIs",
        ],
        "guidance": (
            "A process narrative describes how a business process operates. "
            "Write each step in active voice. Complement with a BPMN model for visual representation. "
            "Reference BABOK v3 Chapter 10.8 (Process Modeling)."
        ),
        "template": """\
# Process Narrative
# {project_name}

**Version:** 1.0
**Date:** [Date]
**Process Owner:** [Role]

---

## 1. Process Overview

**Process Name:** [Name]
**Purpose:** [Why this process exists]
**Trigger:** [What starts the process]
**End State:** [What signifies completion]

## 2. Scope

**In Scope:** [What is covered]
**Out of Scope:** [What is not covered]

## 3. Process Steps

| Step | Actor | Activity | Input | Output | Decision? |
|------|-------|----------|-------|--------|-----------|
| 1 | | | | | Yes/No |
| 2 | | | | | |

## 4. Roles and Responsibilities

| Role | Responsibilities |
|------|----------------|
| | |

## 5. Inputs and Outputs

**Inputs:**
- [Document/data/trigger]

**Outputs:**
- [Deliverable/action/notification]

## 6. Exception Handling

| Exception | How Handled | Escalation Path |
|-----------|-------------|-----------------|
| | | |

## 7. KPIs

| Metric | Target | Measurement Method |
|--------|--------|-------------------|
| Cycle Time | | |
| Error Rate | | |
""",
    },
}


def execute(params: dict) -> dict:
    artifact_type = params.get("artifact_type", "").lower()
    project_name = params.get("project_name", "").strip() or "[Project Name]"

    if artifact_type not in _TEMPLATES:
        valid = sorted(_TEMPLATES.keys())
        raise ValueError(
            f"Unknown artifact_type '{artifact_type}'. Valid types: {valid}"
        )

    tpl = _TEMPLATES[artifact_type]
    markdown = tpl["template"].format(project_name=project_name)

    return {
        "template_markdown": markdown,
        "sections": tpl["sections"],
        "guidance": tpl["guidance"],
    }


def main() -> None:
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
