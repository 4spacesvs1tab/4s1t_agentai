# Release Procedure — Public GitHub Release

This procedure is used for every public release to GitHub.
It produces a **clean orphan branch** with no git history — so no local development
artifacts, infrastructure details, or secrets ever reach GitHub.

---

## How It Works

An orphan branch has no parent commits. When pushed, GitHub receives **only that
branch's commit and its file tree** — nothing from previous local commits.
Your local `.git/` history stays on your machine.

```
Local history:    A ← B ← C ← D (main)   ← stays local, never pushed

GitHub gets:                  E (release)  ← orphan, no parents, no connection
```

This means: even if your local history contains secrets, IPs, or keys,
none of that is accessible via GitHub.

---

## Pre-Release Checklist

Before creating the orphan commit, verify the source code contains no private data.

### Files to scan every time

Run these greps — all must return **empty**:

```bash
# Private IPs
grep -rn "10\.66\.66\.\|172\.20\.0\." src/ config/ docker-compose.yml

# Private .local mDNS domains (non-generic)
grep -rn "\.local" src/ config/ docker-compose.yml | grep -v "YOUR_SERVER\|\.env\.local\|locale\|skip_tor\|RFC-1918\|mDNS\|Tor"

# Usernames
grep -rn "pop1\|/Users/kuba" src/ config/ docs/ docker-compose.yml

# Real keys (nsec1 / EC private)
grep -rn "nsec1[a-z0-9]\{20,\}" src/ config/
```

### Files that must NOT be tracked

```bash
# This must return empty before committing
git ls-files | grep -E "\.secrets|\.goose|01_archive|approval-private|test\.db|\.backup|\.bak$"
```

---

## Phase 1 — Source Code Sanitisation

These three files contain inline infrastructure examples that must use
generic placeholders for every public release.

### 1. `src/communication/nostr_nip17/config.py`

All occurrences of the private relay IP and .local domain appear in docstrings
and the `create_example_config()` method (8 locations total).

Replace every instance of your private relay IP:PORT and your server's .local domain
with the generic placeholders `YOUR_LOCAL_RELAY_IP:PORT` and `YOUR_SERVER.local`.

Quick check after editing (must return empty):
```bash
grep -n "YOUR_PRIVATE_RELAY_IP\|YOUR_SERVER_LOCAL_DOMAIN" src/communication/nostr_nip17/config.py
```

### 2. `docker-compose.yml`

The `extra_hosts` block may contain a `.local` hostname mapped to a private IP.
Replace any real hostname:IP pair with:
```yaml
    extra_hosts:
      # Add your server's .local hostname here if Docker can't resolve it via mDNS:
      # - "YOUR_SERVER.local:YOUR_SERVER_IP"
```

Quick check (must return empty):
```bash
grep -n "\.local:[0-9]" docker-compose.yml
```

### 3. `config/nostr_nip17.example.yaml`

Replace any real relay IP:PORT and .local domain in comments with
`YOUR_RELAY_IP:PORT` and `YOUR_SERVER.local`.

Quick check (must return empty):
```bash
grep -En "[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}" config/nostr_nip17.example.yaml
```

---

## Phase 2 — Orphan Branch and Commit

```bash
# 1. Create orphan branch — no parent commits, index preserved
git checkout --orphan release-vX.Y.Z

# 2. Untrack sensitive and junk files from the index (keeps them on disk)
git rm --cached -r .secrets/ .goose/ 01_archive/
git rm --cached certs/approval-private.pem certs/approval-public.pem
git rm --cached src/test.db src/test.db-shm src/test.db-wal
git rm --cached src/api/auth_routes.py.backup src/api/auth_routes.py.backup2
git rm --cached src/api/web_routes.py.backup "src/api/web_routes.py.backup.1770834974"
git rm --cached src/communication/nostr_nip17/__init__.py.bak
git rm --cached src/web/templates/chat.html.bak

# 3. Stage everything remaining
#    .gitignore will block all sensitive dirs (.secrets/, .goose/, 01_archive/, certs/approval-*.pem)
git add .

# 4. VERIFY — both of these must return empty
git ls-files | grep -E "\.secrets|\.goose|01_archive|approval-private|test\.db|\.backup|\.bak$"
# Run the Pre-Release Checklist scans from the top of this file

# 5. Commit
git commit -m "release: vX.Y.Z <Codename> — <summary>"
```

---

## Phase 3 — Push to GitHub

```bash
# Force-push orphan as main
git push origin release-vX.Y.Z:main --force

# Tag the release
git tag -a vX.Y.Z release-vX.Y.Z -m "vX.Y.Z <Codename>"
git push origin vX.Y.Z
```

> Force-push is safe here because each release is an intentional,
> complete replacement of the public history. The remote's previous
> release commit is replaced — not corrupted.

---

## Phase 4 — Post-Release (Production Machine)

After any release that rotates keys:

1. Generate new Nostr keypair on the production machine:
   ```bash
   # use any nostr key generator tool
   # place new nsec1... in YOUR_DEPLOY_PATH/.secrets/agent_nostr.key
   ```

2. Generate new EC keypair for approval signing:
   ```bash
   openssl ecparam -name prime256v1 -genkey -noout -out certs/approval-private.pem
   openssl ec -in certs/approval-private.pem -pubout -out certs/approval-public.pem
   ```

3. Update `config/nostr_nip17.yaml` with the new agent pubkey.

4. Restart the container:
   ```bash
   docker restart 4s1t-agent
   ```

---

## Key Rules

- **Never push from `main`** — always use an orphan branch
- **Never `git push` without running the verify checks in Phase 2, step 4**
- **Rotate keys after any release** if the old keys were ever committed locally
- **`01_archive/` stays local** — it contains deployment history with infrastructure details
- **`.secrets/` stays local** — production keys are on the R500, not in any repo
