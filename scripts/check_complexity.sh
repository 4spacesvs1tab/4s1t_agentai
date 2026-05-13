#!/usr/bin/env bash
# check_complexity.sh — radon cyclomatic-complexity gate
#
# Counts functions with grade C or worse (CC > 10) in src/.
# Exits 1 (FAIL) if the count exceeds THRESHOLD.
#
# Usage:
#   bash scripts/check_complexity.sh
#
# Threshold formula: (current_count + 10) rounded to nearest 5.
# Sprint 10 baseline: 35  →  threshold: 45

set -euo pipefail

THRESHOLD=45

VIOLATIONS=$(python3 -m radon cc src/ -n C -s 2>&1 | grep -c "^src" || true)

echo "Complexity gate: ${VIOLATIONS} grade-C+ functions (threshold: ${THRESHOLD})"

if [ "${VIOLATIONS}" -gt "${THRESHOLD}" ]; then
    echo "FAIL: complexity violations exceed threshold (${VIOLATIONS} > ${THRESHOLD})"
    exit 1
else
    echo "PASS"
    exit 0
fi
