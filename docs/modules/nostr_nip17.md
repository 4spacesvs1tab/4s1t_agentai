# Nostr NIP-17 Integration

4S1T uses Nostr as its out-of-band communication channel. All agent notifications, approval requests, and chat messages are delivered as **NIP-17 encrypted direct messages** — end-to-end encrypted, relay-transported, no central server.

---

## What Is Nostr?

Nostr is a decentralised messaging protocol. Messages (called *events*) are signed with a keypair and broadcast to one or more *relays* — simple WebSocket servers. Anyone who knows your public key (`npub`) and has access to a shared relay can send you messages. No account, no email, no central authority.

**NIP-17** is the Nostr specification for encrypted direct messages using the *GiftWrap* scheme. The content, sender identity, and recipient identity are all encrypted. A relay operator sees only opaque blobs — they cannot read messages or determine who is talking to whom.

---

## Why Nostr for an AI Agent?

| Requirement | How Nostr satisfies it |
|---|---|
| Receive results on mobile without opening a browser | Any NIP-17 client app delivers DMs to your phone |
| Approve sensitive actions from anywhere | Agent sends approval request DM; you reply yes/no |
| No polling / no open browser tab | Nostr push delivery |
| Censorship-resistant | Multi-relay failover; if one relay blocks your message, others deliver it |
| End-to-end encryption | GiftWrap: only you (with your `nsec`) can decrypt |

---

## Architecture

```
  Agent service
       │
       │  NIP-17 GiftWrap event (encrypted)
       ▼
  Relay 1 ──────────────────► Your Nostr client (phone)
  Relay 2 ──┐                        │
  Relay 3 ──┤  multi-relay fanout    │ reply (encrypted DM)
  Relay 4 ──┤                        ▼
  Relay 5 ──┘                 Agent service
                              (polls relays for replies)
```

The agent publishes every outbound message to all configured relays simultaneously. For inbound messages, it polls relays using `get_events_of()` to retrieve historical messages — this is robust to relay delays and transient disconnections.

---

## Two Use Cases

### 1. Approval Flow (Human-in-the-Loop)

Some skills require explicit user approval before they execute. When an agent reaches one of these skills:

1. Agent sends you a NIP-17 DM describing the pending action
2. You receive a notification on your Nostr client
3. You reply `yes` (or `approve`) to allow, or `no` (or `deny`) to cancel
4. The agent polls for your reply and proceeds accordingly

Skills requiring approval by default:
- `gap_analysis` (ba_agent)
- `python_execute` (data_agent)

You can extend this list per persona in `src/agents/personas.py` (`requires_approval` field).

### 2. Task Result Delivery

When the orchestrator completes a workflow, it sends you the final result as a NIP-17 DM. This means you can start a task from the web UI, close the browser, and receive the result on your phone when it's done.

---

## Keypairs

The system uses **two separate keypairs**:

| Keypair | Where stored | What it does |
|---|---|---|
| **Agent keypair** | `.env` → `APPROVAL_PRIVATE_KEY` / `APPROVAL_PUBLIC_KEY` | Agent signs and sends outbound DMs |
| **Your personal keypair** | Your Nostr client app | You receive and decrypt messages; you sign replies |

The agent knows your `npub` (set in `config/nostr_nip17.yaml` → `recipient_pubkey`) and uses it to encrypt messages so only you can read them.

---

## Configuration

### `config/nostr_nip17.yaml`

```yaml
relays:
  - url: wss://relay.damus.io
    priority: 1
  - url: wss://relay.primal.net
    priority: 2
  - url: wss://nos.lol
    priority: 3
  - url: wss://nostr.wine
    priority: 4
  - url: wss://relay.nostr.band
    priority: 5

recipient_pubkey: "npub1..."      # YOUR personal npub (not the agent's)

timeouts:
  connect: 10                     # seconds to connect to a relay
  send: 15                        # seconds to send a message

rate_limit:
  max_messages_per_minute: 10     # outbound rate limit

auto_connect: true                # connect to relays at startup
```

### `.env` — agent keypair

```bash
APPROVAL_PRIVATE_KEY=nsec1...     # agent's private key
APPROVAL_PUBLIC_KEY=npub1...      # agent's public key
```

> Generate a fresh keypair for the agent — do not reuse your personal nsec.

---

## Relay Selection

### Pre-configured public relays

The five default relays are chosen for reliability and geographic distribution:

| Relay | Operator | Notes |
|---|---|---|
| `relay.damus.io` | Damus team | Largest relay; most clients connected |
| `relay.primal.net` | Primal team | Good uptime; fast |
| `nos.lol` | Community | General-purpose |
| `nostr.wine` | Community | Privacy-friendly |
| `relay.nostr.band` | nostr.band | High availability |

The agent tries relays in priority order and falls over to the next if a relay is unreachable or times out.

### Adding a self-hosted relay

```yaml
relays:
  - url: wss://my-relay.internal:7000
    priority: 1
  # ... existing relays with priority 2-6
```

Self-hosted relay options:
- [nostr-rs-relay](https://github.com/scsibug/nostr-rs-relay) — Rust, lightweight
- [strfry](https://github.com/hoytech/strfry) — high-throughput
- [nostream](https://github.com/Cameri/nostream) — TypeScript, PostgreSQL

---

## Compatible Clients

To receive NIP-17 encrypted DMs, your client must support NIP-17 (GiftWrap). Verified compatible clients:

| Client | Platform | Notes |
|---|---|---|
| **Keychat** | iOS, Android | Designed for NIP-17 messaging; recommended |
| **Amethyst** | Android | Supports NIP-17 |
| **Damus** | iOS | Supports NIP-17 |
| **Coracle** | Web | Browser-based; supports NIP-17 |

### Setting up Keychat (recommended)

1. Install Keychat from the App Store or Google Play
2. On first launch, generate or import your keypair
3. Export your `npub` and paste it into `config/nostr_nip17.yaml` → `recipient_pubkey`
4. Add the agent's `npub` as a contact so messages are not filtered as spam

---

## Message Polling

The agent retrieves inbound messages using **historical polling** (`get_events_of()`) rather than a persistent real-time subscription. This is more reliable:

- Works even if the agent was offline when you sent the reply
- Survives relay reconnections without missing events
- Tolerates the variable delivery delay of different relays

The polling interval and look-back window are configurable in `src/communication/nostr_nip17/`.

---

## Rate Limiting

Outbound messages are rate-limited to **10 messages per minute** by default. This is a protection against runaway agent loops flooding your Nostr inbox. Adjust in `config/nostr_nip17.yaml` → `rate_limit.max_messages_per_minute`.

---

## Troubleshooting

**No DMs arriving on my phone**

1. Confirm `recipient_pubkey` in `config/nostr_nip17.yaml` matches your client's `npub` exactly
2. Check agent logs: `docker compose logs agent | grep -i nostr`
3. Confirm at least one relay connected: look for `Connected to relay wss://...`
4. Ensure your Nostr client is connected to at least one of the same relays

**Agent not receiving my approval replies**

1. Confirm your client sends to a relay the agent also connects to
2. Check the agent keypair `APPROVAL_PUBLIC_KEY` matches what you're replying to
3. Look for `poll` and `inbound` log entries

**`nostr-sdk` import error at startup**

```bash
pip install nostr-sdk>=0.44.0
```

Or inside Docker: rebuild with `docker compose build --no-cache`.
