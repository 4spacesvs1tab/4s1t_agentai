#!/usr/bin/env bash
# make-release.sh — create a clean orphan release branch with no git history
#
# Usage:
#   ./scripts/make-release.sh [version]
#
# If version is omitted, it is read from the VERSION file.
#
# What this does:
#   1. Creates an orphan branch (zero history) from the current working tree
#   2. Removes local-only paths from the index (files stay on disk)
#   3. Makes a single clean commit
#   4. Prints the push command — you must run it manually
#   5. Returns you to main and deletes the local orphan branch
#
# Pre-flight checklist:
#   - All changes you want in the release are committed on main
#   - config/nostr_nip17.yaml contains only placeholder keys (no real npub)
#   - src/.env is NOT committed (it is in .gitignore)
#   - GitHub remote is named 'github' (or change REMOTE below)

set -euo pipefail

REMOTE="${GITHUB_REMOTE:-github}"
VERSION="${1:-$(cat "$(git rev-parse --show-toplevel)/VERSION" 2>/dev/null || echo "unknown")}"
BRANCH="release/${VERSION}"
ROOT="$(git rev-parse --show-toplevel)"

# ── Paths to strip from the release index ──────────────────────────────────
STRIP=(
  ".secrets"
  ".goose"
  ".claude"
  "01_archive"
  "certs/approval-private.pem"
  "certs/approval-public.pem"
)
# ───────────────────────────────────────────────────────────────────────────

cd "$ROOT"

echo "==> Release: ${VERSION}"
echo "==> Branch:  ${BRANCH}"
echo ""

# Refuse to run with uncommitted changes to avoid confusion
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "ERROR: You have uncommitted changes. Commit or stash them first." >&2
  exit 1
fi

# Create orphan branch — no parent commits, working tree unchanged
git checkout --orphan "${BRANCH}"

# Remove local-only paths from the index ONLY (files stay on disk)
for path in "${STRIP[@]}"; do
  git rm --cached -r --ignore-unmatch "${path}" > /dev/null
done

# Verify none of the stripped paths are still staged
echo "==> Verifying no local-only paths remain in the release index..."
for path in "${STRIP[@]}"; do
  count=$(git diff --cached --name-only | grep -c "^${path}" || true)
  if [[ "$count" -gt 0 ]]; then
    echo "ERROR: '${path}' is still in the index. Aborting." >&2
    git checkout main
    git branch -D "${BRANCH}"
    exit 1
  fi
done

# Single clean commit
git commit -m "release: ${VERSION}"

echo ""
echo "==> Release branch '${BRANCH}' created with a single commit."
echo ""
echo "    To push to GitHub (overwrites main — no history):"
echo "      git push ${REMOTE} ${BRANCH}:main --force"
echo ""
echo "    To push as a versioned tag instead:"
echo "      git push ${REMOTE} ${BRANCH}"
echo "      git tag v${VERSION} ${BRANCH}"
echo "      git push ${REMOTE} v${VERSION}"
echo ""
echo "==> Push when ready, then run:"
echo "      git checkout main && git branch -D ${BRANCH}"
echo ""
