# Quick Start — 4S1T Agent AI

Up and running in 5 steps. For the full setup (Tor, self-hosted relay, non-Docker install), see [INSTALL.md](INSTALL.md).

---

## What you need before you start

- Docker + Docker Compose v2
- An API key from one AI provider — [Nano-GPT](https://nano-gpt.com), [OpenRouter](https://openrouter.ai/keys), or [OpenAI](https://platform.openai.com/api-keys)
- A Nostr keypair (the agent uses it to send you encrypted DMs with results)
- A NIP-17 compatible Nostr client on your phone — [Keychat](https://keychat.io) is the easiest option

---

## Step 1 — Clone

```bash
git clone https://github.com/4spacesvs1tab/4s1t_agentai.git
cd 4s1t_agentai
```

## Step 2 — Create your environment file

```bash
cp .env.example .env
```

Open `.env` and fill in at minimum:

| Variable | What to put |
|---|---|
| `SECRET_KEY` | Run `openssl rand -hex 64` and paste the result |
| `EXECUTOR_JWT_SECRET` | Run `openssl rand -hex 32` and paste the result |
| `ACTIVE_PROVIDER` | One of: `nano_gpt`, `openrouter`, `openai`, `local_ollama` |
| Your provider's API key | e.g. `NANO_GPT_API_KEY=...` |

## Step 3 — Generate your approval certificate

The executor service uses an EC key pair to sign approval tokens:

```bash
mkdir -p certs
openssl ecparam -name prime256v1 -genkey -noout -out certs/approval-private.pem
openssl ec -in certs/approval-private.pem -pubout -out certs/approval-public.pem
```

Add both to `.env`:

```bash
APPROVAL_PRIVATE_KEY="$(cat certs/approval-private.pem)"
APPROVAL_PUBLIC_KEY="$(cat certs/approval-public.pem)"
```

## Step 4 — Configure Nostr (to receive results on your phone)

```bash
cp config/nostr_nip17.example.yaml config/nostr_nip17.yaml
```

Open `config/nostr_nip17.yaml` and set `recipient_pubkey` to your personal `npub`. The five pre-configured public relays work without any other changes.

If you do not have a Nostr keypair yet, generate one:

```bash
pip install nostr-sdk
python3 -c "
from nostr_sdk import Keys
k = Keys.generate()
print('nsec (agent private):', k.secret_key().to_bech32())
print('npub (agent public): ', k.public_key().to_bech32())
"
```

Add `AGENT_NOSTR_PRIVATE_KEY=nsec1...` to `.env`.

## Step 5 — Start

```bash
docker compose up --build -d
```

Open `http://localhost:8000`, register an account, and send your first task from the Chat tab.

---

## First task ideas

- *"Search the web for the latest AI agent frameworks and summarise the top 5"*
- *"Analyse this CSV file and generate a chart showing the trend"*  
- *"Write a stakeholder analysis for a mobile app launch"*

---

## What happens when you send a task

1. The orchestrator decomposes your request into parallel sub-tasks
2. Specialist agents (research, data, business analysis, synthesis) run in waves
3. Skills that need your approval (e.g. running Python code) send you a Nostr DM first
4. The final result arrives both in the browser and as an encrypted DM on your phone

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Port 8000 not responding | `docker compose ps` — check the `agent` container is `Up` |
| No DMs on phone | Check `recipient_pubkey` in `config/nostr_nip17.yaml` matches your client |
| `SECRET_KEY too short` error | Re-run `openssl rand -hex 64` and update `.env` |
| ChromaDB warning on startup | Safe to ignore — BABOK lookup degrades gracefully without it |

Full troubleshooting: [INSTALL.md — Troubleshooting](INSTALL.md#troubleshooting)
