# Installation Guide

This document walks through everything needed to run 4S1T Agent AI, including the external services the system depends on.

---

## Prerequisites Checklist

Before you start, confirm you have or will install:

- [ ] Docker Engine 24+ and Docker Compose v2
- [ ] Tor daemon (`tor`)
- [ ] A Nostr keypair (nsec/npub)
- [ ] A Nostr NIP-17 compatible mobile or desktop client
- [ ] An API key from at least one AI provider (Nano-GPT, OpenRouter, OpenAI, or local Ollama)

---

## 1. Docker and Docker Compose

### macOS
```bash
# Install Docker Desktop (includes Compose)
brew install --cask docker
# Then open Docker Desktop app and wait for the engine to start
docker compose version   # should print v2.x
```

### Ubuntu / Debian
```bash
# Install Docker Engine
sudo apt update
sudo apt install -y docker.io docker-compose-plugin

# Allow running without sudo (log out and back in after this)
sudo usermod -aG docker $USER

docker compose version
```

### Windows
Install [Docker Desktop for Windows](https://docs.docker.com/desktop/install/windows-install/) with WSL 2 backend.

---

## 2. Tor

Tor provides anonymous outbound routing. The agent connects to AI providers through Tor's SOCKS5 proxy on `127.0.0.1:9050`.

### macOS
```bash
brew install tor
brew services start tor
# Verify
curl --socks5 127.0.0.1:9050 https://check.torproject.org/api/ip
# Expected: {"IsTor":true, ...}
```

### Ubuntu / Debian
```bash
sudo apt install -y tor
sudo systemctl enable --now tor
# Verify
curl --socks5 127.0.0.1:9050 https://check.torproject.org/api/ip
```

### Windows (WSL 2)
Install and start Tor inside the WSL 2 Ubuntu environment using the Debian instructions above.

### What port does Tor use?

The default SOCKS5 port is `9050`. If you change it in `/etc/tor/torrc`, update `TOR_SOCKS_PROXY` in your `.env` accordingly.

### Verifying Tor is working
```bash
curl --socks5-hostname 127.0.0.1:9050 https://check.torproject.org/api/ip
# Should return: {"IsTor":true,"IP":"..."}
```

> **Note:** Tor routing is optional at startup. If Tor is unavailable, the privacy layer logs a warning and falls back to direct connections. To make Tor required, set `TOR_REQUIRED=true` in `.env`.

---

## 3. Nostr Keypair

The agent uses a dedicated Nostr keypair to send and receive encrypted NIP-17 DMs (approval requests, task results, chat).

### Generating a keypair

**Option A — using nostr-sdk CLI (if you have Python installed):**
```bash
pip install nostr-sdk
python3 -c "
from nostr_sdk import Keys
k = Keys.generate()
print('nsec (private):', k.secret_key().to_bech32())
print('npub (public): ', k.public_key().to_bech32())
"
```

**Option B — using a Nostr client app:**
Most Nostr clients (Amethyst, Damus, Keychat) generate a keypair on first launch. Export the nsec from the app settings.

**Option C — using nak (command-line Nostr tool):**
```bash
nak key generate
```

> **Security:** The agent's `nsec` goes into `.env` as `AGENT_NOSTR_PRIVATE_KEY` (or into `.secrets/agent_nostr.key`). Never commit `.env` or `.secrets/` to version control.

---

## 4. Approval Signing Certificate

The executor service uses an EC (ECDSA P-256) key pair to sign and verify approval tokens. You must generate your own pair — never use someone else's.

```bash
# Create the certs directory if it doesn't exist
mkdir -p certs

# Generate EC private key (P-256 / prime256v1)
openssl ecparam -name prime256v1 -genkey -noout -out certs/approval-private.pem

# Derive the public key
openssl ec -in certs/approval-private.pem -pubout -out certs/approval-public.pem
```

Then add both keys to `.env`:

```bash
# Copy the private key content (single-line or keep newlines as \n)
APPROVAL_PRIVATE_KEY="$(cat certs/approval-private.pem)"
APPROVAL_PUBLIC_KEY="$(cat certs/approval-public.pem)"
```

> **Security:** `certs/approval-private.pem` must never be committed to version control. Add it to `.gitignore`. The public key (`approval-public.pem`) is safe to store but should also stay off public repositories since it is unique to your deployment.

---

## 5. Nostr Client (to receive DMs)

You need a NIP-17 compatible client installed on your phone or desktop to receive encrypted messages from the agent.

### Recommended: Keychat (mobile)
Keychat is specifically designed for NIP-17 encrypted messaging.
- iOS: [Keychat on App Store](https://apps.apple.com/app/keychat/id6472284494)
- Android: [Keychat on Google Play](https://play.google.com/store/apps/details?id=com.keychat)

### Alternatives
- **Amethyst** (Android) — supports NIP-17
- **Damus** (iOS) — supports NIP-17
- **Coracle** (web browser) — supports NIP-17

### Setup in the client
1. Import your user `nsec` into the client (this is **your personal** keypair, separate from the agent's keypair)
2. Add the agent's `npub` as a contact
3. Messages from the agent will appear as encrypted DMs

---

## 6. Nostr Relay

The agent communicates through Nostr relays. Five public relays are pre-configured in `config/nostr_nip17.yaml` with automatic failover:

```
wss://relay.damus.io      (priority 1)
wss://relay.primal.net    (priority 2)
wss://nos.lol             (priority 3)
wss://nostr.wine          (priority 4)
wss://relay.nostr.band    (priority 5)
```

**No action required** to use the public relays — they work out of the box.

### Self-hosted relay (optional)

If you want full control over message routing, you can run your own relay:

```bash
# Example: nostr-rs-relay
docker run -d \
  --name nostr-relay \
  -p 7000:8080 \
  -v $(pwd)/relay-data:/usr/src/app/db \
  scsibug/nostr-rs-relay
```

Then add `wss://your-relay-host:7000` to `config/nostr_nip17.yaml` as priority 1.

---

## 7. Clone and Configure

```bash
git clone https://github.com/4spacesvs1tab/4s1t_agentai.git
cd 4s1t_agentai
```

### 7.1 Environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in all required values:

| Variable | Required | Description |
|---|---|---|
| `SECRET_KEY` | **Yes** | Random string ≥ 64 chars. Generate: `openssl rand -hex 64` |
| `EXECUTOR_JWT_SECRET` | **Yes** | Random string ≥ 32 chars. Generate: `openssl rand -hex 32` |
| `ACTIVE_PROVIDER` | **Yes** | One of: `nano_gpt`, `openrouter`, `openai`, `local_ollama` |
| `NANO_GPT_API_KEY` | If nano_gpt | API key from [nano-gpt.com](https://nano-gpt.com) |
| `OPENROUTER_API_KEY` | If openrouter | API key from [openrouter.ai](https://openrouter.ai/keys) |
| `OPENAI_API_KEY` | If openai | API key from [platform.openai.com](https://platform.openai.com/api-keys) |
| `LOCAL_OLLAMA_API_KEY` | If local_ollama | API key from your Open WebUI instance |
| `APPROVAL_PRIVATE_KEY` | **Yes** | EC private key PEM (generated in step 4): `cat certs/approval-private.pem` |
| `APPROVAL_PUBLIC_KEY` | **Yes** | EC public key PEM (generated in step 4): `cat certs/approval-public.pem` |
| `ALLOWED_ORIGINS` | **Yes** | Your domain or `["http://localhost:8000"]` for local use |
| `DATABASE_URL` | No | Defaults to `sqlite:////app/data/agent.db` |
| `REDIS_URL` | No | Defaults to `redis://localhost:6379/0` |
| `LOG_LEVEL` | No | `INFO` (default) or `DEBUG` |

### 7.2 Nostr relay configuration

```bash
cp config/nostr_nip17.example.yaml config/nostr_nip17.yaml
```

Open `config/nostr_nip17.yaml` and set your **recipient pubkey** — this is the `npub` of **your personal Nostr account** (the one on your phone), not the agent's key:

```yaml
recipient_pubkey: "npub1yourpersonalpublickey..."
```

The pre-configured relays work without changes. See [docs/modules/nostr_nip17.md](docs/modules/nostr_nip17.md) for the full relay configuration reference.

---

## 8. Build and Start

```bash
# Build images and start all services
docker compose up --build -d

# Watch logs
docker compose logs -f agent

# Check health
curl http://localhost:8000/health
# Expected: {"status":"healthy", ...}
```

The agent service starts on port `8000`. The executor service (sandboxed code runner) is internal-only and not exposed.

---

## 9. First Login

1. Open `http://localhost:8000` in your browser
2. Click **Register** and create your account
3. (Optional) Enable 2FA in your profile settings
4. Go to **Profile → API Keys** to set your preferred AI model
5. Open the **Chat** tab and send your first task

---

## 10. Verifying the Nostr Connection

After starting the agent, send a test message through the web chat UI. If Nostr is configured correctly, you should receive the response both in the browser and as an encrypted DM on your mobile Nostr client within a few seconds.

To check the agent's Nostr connection status:
```bash
docker compose logs agent | grep -i nostr
```

Healthy output looks like:
```
INFO  nostr_service - Connected to relay wss://relay.damus.io
INFO  nostr_service - Connected to relay wss://relay.primal.net
```

---

## 11. Optional: Running Without Docker

If you prefer to run directly with Python:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Set environment variables (or use a .env file with python-dotenv)
export PYTHONPATH=src
export DATABASE_URL=sqlite:///data/agent.db
# ... set all other .env variables

cd src
python main.py
```

Note: without Docker, the `python_execute` skill runs subprocesses directly rather than in the air-gapped executor container. This is less secure and not recommended for production use.

---

## Troubleshooting

| Problem | Likely cause | Fix |
|---|---|---|
| `Connection refused` on port 8000 | Container not started | `docker compose ps` — check agent is `Up` |
| Nostr DMs not arriving | Wrong `recipient_pubkey` | Verify npub in `config/nostr_nip17.yaml` matches your client |
| `IsTor: false` on curl check | Tor not running | Start Tor: `brew services start tor` or `systemctl start tor` |
| `SECRET_KEY too short` error | `.env` not configured | Run `openssl rand -hex 64` and paste into `.env` |
| ChromaDB errors on startup | Optional dep absent | Safe to ignore — BABOK lookup degrades gracefully |
| Executor container exits immediately | Architecture note | See TODO in `docker-compose.yml` — executor `network_mode: none` conflicts with uvicorn; it restarts on demand |

See [docs/operations/troubleshooting.md](docs/operations/troubleshooting.md) for more.
