#!/usr/bin/env python3
"""
create_decision_model skill handler — KB-14.

Validates and formats a decision table from conditions + rules.

Input:  {"parameters": {"decision_name": "...", "conditions": [...], "rules": [...], "action_options": [...]}}
Output: {"success": true, "result": {"valid": true, "decision_table_markdown": "...", "coverage": {...}, ...}}
"""
import json
import sys
from itertools import product


# ---------------------------------------------------------------------------
# Markdown builder
# ---------------------------------------------------------------------------

def _build_table(decision_name: str, conditions: list, rules: list) -> str:
    cond_names = [c["name"] for c in conditions]
    headers = cond_names + ["Action"]
    sep = ["-" * max(len(h), 6) for h in headers]

    lines = [
        f"## Decision Table: {decision_name}",
        "",
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(sep) + " |",
    ]
    for rule in rules:
        cv = rule.get("condition_values", {})
        row = [cv.get(n, "—") for n in cond_names] + [rule.get("action", "—")]
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core execute
# ---------------------------------------------------------------------------

def execute(params: dict) -> dict:
    decision_name = (params.get("decision_name") or "").strip()
    conditions = params.get("conditions") or []
    rules = params.get("rules") or []
    action_options = params.get("action_options") or None

    if not decision_name:
        raise ValueError("'decision_name' is required")
    if not conditions:
        raise ValueError("'conditions' is required and must not be empty")

    errors: list[str] = []
    warnings: list[str] = []

    cond_names = [c["name"] for c in conditions]

    # Validate rule keys match condition names
    for i, rule in enumerate(rules):
        cv = rule.get("condition_values", {})
        unknown = set(cv.keys()) - set(cond_names)
        if unknown:
            errors.append(f"Rule {i + 1}: unknown condition(s) {unknown}. Valid: {cond_names}")
        if action_options and rule.get("action") not in action_options:
            warnings.append(
                f"Rule {i + 1}: action '{rule.get('action')}' not in action_options {action_options}"
            )

    # Coverage analysis
    cond_value_sets = [c.get("possible_values", []) for c in conditions]
    if all(cond_value_sets):
        total_combinations = 1
        for vs in cond_value_sets:
            total_combinations *= len(vs)

        all_combos = set(product(*cond_value_sets))
        covered_combos = set()
        for rule in rules:
            cv = rule.get("condition_values", {})
            combo = tuple(cv.get(n, "") for n in cond_names)
            covered_combos.add(combo)

        gap_combos = all_combos - covered_combos
        gap_pct = round(len(gap_combos) / total_combinations * 100, 1) if total_combinations else 0.0

        # Represent gaps as dicts; cap at 20 for readability
        gap_list = [dict(zip(cond_names, g)) for g in list(gap_combos)[:20]]
        if len(gap_combos) > 20:
            warnings.append(f"{len(gap_combos)} gaps found — showing first 20.")

        coverage = {
            "total_combinations": total_combinations,
            "covered": len(covered_combos),
            "gap_pct": gap_pct,
            "gaps": gap_list,
        }
        if gap_combos:
            warnings.append(f"{len(gap_combos)} rule gap(s) detected ({gap_pct}% uncovered).")
    else:
        coverage = {"total_combinations": None, "covered": len(rules), "gap_pct": None, "gaps": []}
        warnings.append("Some conditions have no possible_values — coverage analysis skipped.")

    # Detect redundant rules
    seen: dict[tuple, int] = {}
    redundant: list[dict] = []
    for i, rule in enumerate(rules):
        cv = rule.get("condition_values", {})
        key = tuple(sorted(cv.items()))
        if key in seen:
            redundant.append({"rule_index": i + 1, "duplicate_of": seen[key] + 1})
            warnings.append(f"Rule {i + 1} is a duplicate of rule {seen[key] + 1}.")
        else:
            seen[key] = i

    markdown = _build_table(decision_name, conditions, rules)
    valid = len(errors) == 0

    return {
        "valid": valid,
        "decision_name": decision_name,
        "decision_table_markdown": markdown,
        "coverage": coverage,
        "redundant_rules": redundant,
        "errors": errors,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Subprocess entry point
# ---------------------------------------------------------------------------

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
