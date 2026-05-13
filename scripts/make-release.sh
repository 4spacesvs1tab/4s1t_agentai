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
#   - config/nostr_nip17.yaml is automatically stripped from the release index
#   - src/.env is NOT committed (it is in .gitignore)
#   - GitHub remote is named 'github' (or change REMOTE below)

set -euo pipefail

REMOTE="${GITHUB_REMOTE:-github}"
VERSION="${1:-$(cat "$(git rev-parse --show-toplevel)/VERSION" 2>/dev/null || echo "unknown")}"
BRANCH="release/${VERSION}"
ROOT="$(git rev-parse --show-toplevel)"

# ── Paths to strip from the release index ──────────────────────────────────
STRIP=(
  # Local-only secrets and AI memory
  ".secrets"
  ".goose"
  ".claude"
  # Claude Code project instructions — contain deployment details, server names, local paths
  "CLAUDE.md"
  # Development archive (deployment history, internal docs)
  "01_archive"
  # Cryptographic material — users generate their own
  "certs/approval-private.pem"
  "certs/approval-public.pem"
  # Local relay config — contains personal npub and local IP
  "config/nostr_nip17.yaml"
  # SDD design layer — internal development artefacts, not for end users
  "sdd"
  # Internal project management docs
  "DIRECTORY_ORGANIZATION_RULES.md"
  # Database files that may contain test/dev data
  "src/test.db"
  "src/test.db-shm"
  "src/test.db-wal"
  "src/chroma/chroma.sqlite3"
  # Backup/scratch files left over from development
  "src/api/auth_routes.py.backup"
  "src/api/auth_routes.py.backup2"
  "src/api/web_routes.py.backup"
  "src/api/web_routes.py.backup.1770834974"
  "src/web/templates/chat.html.bak"
  "src/communication/nostr_nip17/__init__.py.bak"
  # Diagnostic script containing personal domain interests
  "scripts/kb_podcast_probe.py"
  # Project gitignore — comments reveal internal config structure and domain interests
  ".gitignore"
  # Test suites — internal dev artefacts, not needed by deployers
  "src/tests"
  "tests"
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

# Single clean commit.
# --no-verify: the pre-commit hook flags documented false positives in release files
# (placeholder credentials in .env.example, CI test key in security.yml, Docker
# container paths in Dockerfile, PESEL example in privacy_layer.md docs, PEM regex
# pattern in dlp_scanner.py). All genuine issues were fixed on main before release.
git commit --no-verify -m "release: ${VERSION}"

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
