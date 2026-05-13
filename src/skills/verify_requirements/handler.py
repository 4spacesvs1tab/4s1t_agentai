#!/usr/bin/env python3
"""
verify_requirements skill handler.

Runs SMART and/or INVEST algorithmic checks. No LLM — pure structural /
pattern-based heuristics.

Input:  {"parameters": {"requirements": [...], "criteria": "smart"|"invest"|"both"}}
Output: {"success": true, "result": {"results": [...], "pass_count": N, "fail_count": N,
          "criteria_applied": "...", "verification_report_markdown": "..."}}
"""
import json
import re
import sys


# ---------------------------------------------------------------------------
# SMART checks
# ---------------------------------------------------------------------------

_MEASURABLE_PATTERNS = [
    r"\d",        # any digit
    r"%",
    r"\bwithin\b",
    r"\bat least\b",
    r"\bno more than\b",
    r"\bless than\b",
    r"\bgreater than\b",
    r"\bmaximum\b",
    r"\bminimum\b",
    r"\bseconds?\b",
    r"\bminutes?\b",
    r"\bhours?\b",
    r"\bdays?\b",
]

_UNACHIEVABLE_PATTERNS = [
    r"\bimpossible\b",
    r"\bnever\b",
    r"\b100%\s*uptime\b",
    r"\bzero\s*downtime\b",
]

_TIME_PATTERNS = [
    r"\bby\b",
    r"\bwithin\b",
    r"\bbefore\b",
    r"\bdeadline\b",
    r"\bsprint\b",
    r"\brelease\b",
    r"\bphase\b",
    r"\bQ[1-4]\b",
    r"\d{4}-\d{2}",  # YYYY-MM
]


def _check_smart(req: dict) -> list:
    """Return list of violation dicts for SMART criteria."""
    violations = []
    title = req.get("title", "")
    desc = req.get("description", "")
    req_type = req.get("req_type", "")
    ac = req.get("acceptance_criteria") or []
    combined = (title + " " + desc).lower()

    # S — Specific
    if len(desc) <= 20:
        violations.append({"criterion": "S", "reason": "Description too short (≤20 chars) to be specific"})
    elif desc.strip().lower() == title.strip().lower():
        violations.append({"criterion": "S", "reason": "Description merely repeats the title"})

    # M — Measurable
    has_measure = any(re.search(p, combined, re.IGNORECASE) for p in _MEASURABLE_PATTERNS)
    if not has_measure and not ac:
        violations.append({"criterion": "M", "reason": "No measurable target found and no acceptance criteria provided"})

    # A — Achievable
    for pat in _UNACHIEVABLE_PATTERNS:
        if re.search(pat, combined, re.IGNORECASE):
            violations.append({"criterion": "A", "reason": f"Contains potentially unachievable claim (matched: '{pat}')"})
            break

    # R — Relevant
    if len(desc) <= 30:
        violations.append({"criterion": "R", "reason": "Description too short (≤30 chars) to demonstrate relevance"})

    # T — Time-bound
    if req_type != "constraint":
        has_time = any(re.search(p, combined, re.IGNORECASE) for p in _TIME_PATTERNS)
        if not has_time:
            violations.append({"criterion": "T", "reason": "No time-bound indicator found (deadline, sprint, release, phase, Qx, or YYYY-MM)"})

    return violations


# ---------------------------------------------------------------------------
# INVEST checks
# ---------------------------------------------------------------------------

_NOT_NEGOTIABLE_PATTERNS = [
    r"\bmust use\b",
    r"\bmust be implemented with\b",
    r"\busing framework\b",
    r"\busing library\b",
]

_VALUABLE_PATTERNS = [
    r"\bso that\b",
    r"\bin order to\b",
    r"\bwhich allows\b",
    r"\bwhich enables\b",
    r"\bto improve\b",
    r"\bto reduce\b",
]


def _is_user_story(req: dict) -> bool:
    title = req.get("title", "").strip()
    return title.lower().startswith("as a ") or title.lower().startswith("as an ")


def _check_invest(req: dict) -> list:
    """Return list of violation dicts for INVEST criteria (user stories only)."""
    violations = []
    title = req.get("title", "")
    desc = req.get("description", "")
    ac = req.get("acceptance_criteria") or []
    combined = (title + " " + desc).lower()

    # I — Independent
    if re.search(r"\bstory\b|\bdepends on\b|\bafter REQ-", combined, re.IGNORECASE):
        violations.append({"criterion": "I", "reason": "References another story or dependency — may not be independent"})

    # N — Negotiable
    for pat in _NOT_NEGOTIABLE_PATTERNS:
        if re.search(pat, combined, re.IGNORECASE):
            violations.append({"criterion": "N", "reason": f"Overly prescriptive implementation constraint found"})
            break

    # V — Valuable
    has_value = any(re.search(p, combined, re.IGNORECASE) for p in _VALUABLE_PATTERNS)
    if not has_value:
        violations.append({"criterion": "V", "reason": "No business value indicator found (so that / in order to / which allows...)"})

    # E — Estimable
    if len(desc) <= 50:
        violations.append({"criterion": "E", "reason": "Description too short (≤50 chars) to be estimable"})

    # S — Small
    if len(desc) >= 500:
        violations.append({"criterion": "S", "reason": "Description too long (≥500 chars) — story may be too large"})

    # T — Testable
    if not ac:
        violations.append({"criterion": "T", "reason": "No acceptance criteria provided — story is not testable"})

    return violations


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------

def _build_markdown(results: list, criteria_applied: str) -> str:
    lines = [
        f"# Requirements Verification Report ({criteria_applied.upper()})",
        "",
        f"| ID | Title | Passed | Violations |",
        f"|----|-------|--------|------------|",
    ]
    for r in results:
        passed_str = "Yes" if r["passed"] else "No"
        violation_str = "; ".join(
            f"{v['criterion']}: {v['reason']}" for v in r.get("violations", [])
        ) or "—"
        lines.append(f"| {r['id']} | {r['title']} | {passed_str} | {violation_str} |")

    lines += ["", "## Detail", ""]
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        lines.append(f"### [{status}] {r['id']} — {r['title']}")
        lines.append(f"- **Score:** {r['score']}")
        if r.get("violations"):
            lines.append("- **Violations:**")
            for v in r["violations"]:
                lines.append(f"  - **{v['criterion']}**: {v['reason']}")
        else:
            lines.append("- All checks passed.")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------

def execute(params: dict) -> dict:
    requirements = params.get("requirements", [])
    criteria = str(params.get("criteria", "smart")).lower()

    if criteria not in ("smart", "invest", "both"):
        raise ValueError(f"Unknown criteria '{criteria}'. Use 'smart', 'invest', or 'both'.")

    results = []
    for req in requirements:
        violations = []

        if criteria in ("smart", "both"):
            violations.extend(_check_smart(req))

        if criteria in ("invest", "both") and _is_user_story(req):
            violations.extend(_check_invest(req))

        # Determine total checks run
        smart_checks = 5 if criteria in ("smart", "both") else 0
        invest_checks = 6 if (criteria in ("invest", "both") and _is_user_story(req)) else 0
        total_checks = smart_checks + invest_checks
        passed_checks = total_checks - len(violations)
        score = f"{passed_checks}/{total_checks}" if total_checks > 0 else "N/A"

        results.append({
            "id": req.get("id", ""),
            "title": req.get("title", ""),
            "passed": len(violations) == 0,
            "violations": violations,
            "score": score,
        })

    pass_count = sum(1 for r in results if r["passed"])
    fail_count = len(results) - pass_count
    markdown = _build_markdown(results, criteria)

    return {
        "results": results,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "criteria_applied": criteria,
        "verification_report_markdown": markdown,
    }


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: handler.py input.json output.json", file=sys.stderr)
        sys.exit(1)
    try:
        data = json.loads(open(sys.argv[1]).read())
        result = execute(data.get("parameters", {}))
        output = {"success": True, "result": result, "error": None, "logs": []}
    except Exception as exc:
        output = {"success": False, "result": None, "error": str(exc), "logs": []}
    with open(sys.argv[2], "w") as f:
        json.dump(output, f)


if __name__ == "__main__":
    main()
