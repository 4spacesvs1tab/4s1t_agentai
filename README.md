# 4S1T Agent AI

**Version: v1.0.0 "Aardvark"** · Python 3.9+ · Docker · MIT License

An open-source, privacy-first AI agent platform. Run any task through a multi-agent pipeline on your own infrastructure, using your choice of AI provider (Nano-GPT, OpenRouter, OpenAI, or local Ollama). Comes with built-in specialisations for IT Business Analysis and Data Analysis, and is extensible for general-purpose use. Communicates back to you over encrypted Nostr DMs. Sends as little identifiable information to AI providers as possible.

---

> **Vibecoded Project — Please Read Before Use**
>
> This project was built entirely through AI-assisted ("vibe") coding. While the author has made a genuine effort to follow good programming practices and security standards, this development approach carries inherent risks: logic errors, security vulnerabilities, and subtle design flaws may be present that have not been caught through traditional code review or systematic testing. The codebase has not been audited by a security professional.
>
> If you intend to deploy this system in any environment where security matters — including exposure to the internet, handling of sensitive data, or use in a production context — **you must evaluate the risks yourself and perform your own security review.** Use at your own risk.

---

## What It Does

You give the agent a task. It:

1. Decomposes the task into a parallel work graph
2. Spawns specialised sub-agents (business analysis, data analysis, research, synthesis)
3. Runs skills (web search, code execution, file I/O, chart generation, BABOK lookup…)
4. Scrubs PII from every prompt before it leaves your machine
5. Routes outbound AI calls through Tor to break provider fingerprinting
6. Sends you the result (and approval requests) as encrypted Nostr DMs

---

## Architecture

```
  You (browser / Nostr client / API)
          │
          ▼
  ┌───────────────────┐
  │   Web UI / API    │  FastAPI on :8000
  │   (auth, chat)    │
  └────────┬──────────┘
           │
           ▼
  ┌───────────────────┐       ┌──────────────────────┐
  │  OrchestratorAgent│──────►│  Worker Agents        │
  │  (task graph)     │       │  ba / data / research │
  └────────┬──────────┘       │  / synthesis          │
           │                  └──────────┬───────────┘
           │                             │ skills
           │                  ┌──────────▼───────────┐
           │                  │  Executor Service     │  :8001 internal
           │                  │  (air-gapped Docker)  │  network_mode: none
           │                  └──────────────────────┘
           │
           ▼
  ┌───────────────────┐
  │  Privacy Layer    │  PII scrub → Tor → AI provider
  └───────────────────┘
           │
           ▼
  ┌───────────────────┐
  │  Nostr NIP-17     │  Encrypted DMs to your mobile client
  └───────────────────┘
```

---

## Features

| Area | Capability |
|---|---|
| **Agent orchestration** | OrchestratorAgent decomposes tasks into a parallel wave graph; up to 20 tool-call steps per agent |
| **Personas** | `ba_agent` (CBAP/BABOK), `data_agent` (Python/Pandas), `research_agent`, `synthesis_agent` |
| **Skills** | 15+ built-in: web search, Python execution (sandboxed), BABOK lookup, file I/O, chart generation, export, stakeholder analysis, process modelling… |
| **MCP integration** | 43+ Model Context Protocol tools; compatible with external MCP servers |
| **Privacy** | PII detection & scrubbing (13 types), prompt obfuscation, Tor routing, header anonymisation |
| **Nostr NIP-17** | End-to-end encrypted DMs for task results, approval requests, and live chat |
| **Multi-provider AI** | Nano-GPT, OpenRouter, OpenAI, local Ollama — switchable per user |
| **Security** | Argon2 passwords, JWT + optional TOTP/2FA, CSRF tokens, DLP whitelist, audit log |
| **Web UI** | Dark terminal theme, real-time chat, model filtering, user profile, health dashboard |
| **i18n** | English and Polish |

---

## Hardware Requirements

| | Minimum | Recommended |
|---|---|---|
| CPU | Core2Duo (SSE2) | Core i5 8th Gen+ |
| RAM | 4 GB | 16 GB |
| Storage | 200 GB HDD | 500 GB SSD |
| Network | Local LAN | LAN + Tor |

> `numpy` is pinned below 2.0 and `cryptography` uses pure-Python fallbacks — the system runs fully on hardware without SSE4.2 or AES-NI.

---

## Quick Start (Docker)

```bash
# 1. Clone
git clone https://github.com/4spacesvs1tab/4s1t_agentai.git
cd 4s1t_agentai

# 2. Configure
cp .env.example .env
# Edit .env — set SECRET_KEY, NANO_GPT_API_KEY (or other provider), Nostr keys

# 3. Configure Nostr relay
cp config/nostr_nip17.example.yaml config/nostr_nip17.yaml
# Edit: set recipient_pubkey to your npub

# 4. Build and start
docker compose up --build -d

# 5. Open
http://localhost:8000
```

See [INSTALL.md](INSTALL.md) for the full walkthrough including Tor setup, Nostr key generation, and external dependency configuration.

---

## Documentation

| Document | What it covers |
|---|---|
| [INSTALL.md](INSTALL.md) | Full installation: Tor, Nostr relay, client setup, `.env` reference |
| [docs/architecture/agent_orchestration.md](docs/architecture/agent_orchestration.md) | How agents, personas, skills, and the task graph work |
| [docs/modules/nostr_nip17.md](docs/modules/nostr_nip17.md) | NIP-17 setup, relay config, approval flow, compatible clients |
| [docs/modules/privacy_layer.md](docs/modules/privacy_layer.md) | PII scrubbing, Tor routing, prompt obfuscation, threat model |
| docs/modules/model_providers.md _(coming soon)_ | Provider config, per-user overrides, adding a new provider |
| docs/architecture/security_model.md _(coming soon)_ | Auth, RBAC, DLP, sandboxing, audit log |
| docs/operations/configuration.md _(coming soon)_ | All `.env` and YAML config references |
| docs/operations/deployment.md _(coming soon)_ | Docker Compose architecture, volumes, resource limits |
| docs/operations/troubleshooting.md _(coming soon)_ | Common failures and fixes |

---

## External Dependencies

The following must be present on the host (or accessible from it) before starting:

- **Docker + Docker Compose** — container runtime
- **Tor** — anonymous outbound routing (`tor` daemon, SOCKS5 on `127.0.0.1:9050`)
- **Nostr client** — to receive encrypted DMs (e.g. Keychat on mobile)
- **Redis** — session store and rate limiter (included in Docker Compose or run natively)
- **Nostr relay** — public relays are pre-configured; self-hosted is optional

See [INSTALL.md](INSTALL.md) for setup instructions for each.

---

## API Endpoints (summary)

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Service health |
| `POST` | `/auth/register` | Create account |
| `POST` | `/auth/login` | Login, receive JWT |
| `POST` | `/api/agent/chat` | Send a task to the agent |
| `GET` | `/api/models` | List available AI models |
| `GET` | `/mcp/tools` | List MCP tools |

Full API reference available at `/docs` (Swagger UI) when running in `DEBUG=true` mode.

---

## Release History

| Version | Codename | Highlights |
|---|---|---|
| v1.0.0 | Aardvark | First public release — full agent orchestration, NIP-17, privacy layer, skills framework, web UI, i18n |

---

## Contributing

Bug reports, feature requests, and pull requests are welcome.

- Report issues at [GitHub Issues](https://github.com/4spacesvs1tab/4s1t_agentai/issues)
- Follow existing code patterns and keep PRs focused
- All new skills must include `meta.json` with scope declarations

---

## License

MIT License — see [LICENSE](LICENSE) for details.
