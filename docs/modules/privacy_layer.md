# Privacy Layer

4S1T is designed to send as little identifiable information to AI providers as possible. This document explains the threat model, the mechanisms in place, and their limitations.

---

## Threat Model

The privacy layer protects against a specific set of threats from **AI service providers** (the companies whose APIs you call):

| Threat | Description |
|---|---|
| **PII leakage** | Your prompts contain personal data (names, IDs, bank accounts) that the provider stores and can read |
| **Request linking** | A provider correlates multiple requests from the same user and builds a profile |
| **IP fingerprinting** | The provider identifies you by your IP address across sessions |
| **Prompt fingerprinting** | Consistent system prompt wording lets the provider recognise that requests come from the same system |
| **Header metadata** | HTTP headers (User-Agent, Accept-Language, etc.) identify your client software and locale |

### What the privacy layer does NOT protect against

- A compromised relay operator reading your Nostr messages (mitigated by NIP-17 encryption, but the relay sees traffic metadata)
- A malicious user on the same LAN intercepting unencrypted local traffic
- The content of the final response stored by the provider after it is delivered
- Legal compulsion applied to the provider (e.g. court orders)
- Your own IP address if Tor is not running

---

## Components

### 1. PII Scrubber

**Source:** [src/privacy/pii_scrubber.py](../../src/privacy/pii_scrubber.py)
**Patterns:** [src/privacy/pii_patterns.py](../../src/privacy/pii_patterns.py)

Before any prompt leaves the machine and is sent to an AI provider, the PII scrubber:

1. Scans the prompt for 13 types of personal data using regex + checksum validators
2. Replaces each detected value with a numbered placeholder (`[PESEL_1]`, `[EMAIL_2]`, etc.)
3. Keeps a session-scoped map of placeholder → original value
4. After the LLM responds, re-injects original values into the response

The LLM reasons over placeholders, not real data. The provider never sees the actual values.

#### Detected PII Types

**Tier 1 — Critical identifiers** (always scrubbed, always alert)

| Type | Description | Validation |
|---|---|---|
| `PESEL` | Polish national ID number (11 digits) | Checksum |
| `NIP` | Polish tax identification number (10 digits) | Checksum |
| `IBAN_PL` | Polish bank account (`PL` + 26 digits) | IBAN mod-97 |
| `IBAN_EU` | Generic EU IBAN (2-letter code + BBAN) | IBAN mod-97 |
| `CREDIT_CARD` | 13–19 digit card numbers (spaced or continuous) | Luhn algorithm |
| `DOWOD` | Polish national ID card (3 letters + 6 digits) | Pattern only |

**Tier 2 — Contact and location data** (scrubbed; alert raised if count ≥ threshold)

| Type | Description |
|---|---|
| `EMAIL` | Standard email addresses |
| `PHONE_PL` | Polish phone numbers (+48 or bare 9-digit) |
| `PHONE_EU` | International phone numbers (non-PL) |
| `POSTAL_PL` | Polish postal codes (NN-NNN) |
| `IPV4` | IPv4 addresses |
| `DATE_PL` | Dates in DD.MM.YYYY, DD/MM/YYYY, YYYY-MM-DD |
| `PASSPORT` | Polish passport numbers (2 letters + 7 digits) |

#### Scrub-Restore Cycle

```
User prompt:
  "Analyse account PL61 1090 1014 0000 0712 1981 2874 for Jan Kowalski (PESEL 85041512345)"

After scrubbing (sent to provider):
  "Analyse account [IBAN_PL_1] for Jan Kowalski ([PESEL_1])"

Provider response:
  "The account [IBAN_PL_1] has the following transactions..."

After restore (shown to user):
  "The account PL61 1090 1014 0000 0712 1981 2874 has the following transactions..."
```

Placeholders are scoped to the current session (`PIISessionState`). They do not persist across sessions.

---

### 2. Tor Routing

**Source:** [src/privacy/](../../src/privacy/) + httpx SOCKS5 transport

All outbound HTTP requests to AI provider APIs are routed through the Tor network via a SOCKS5 proxy (`127.0.0.1:9050` by default).

This provides:
- **IP anonymity**: The provider sees the exit node's IP, not your real IP
- **Session unlinkability**: Tor rotates circuits periodically; the same source IP is not reused across sessions
- **Geographic obfuscation**: Exit nodes can be in different countries

#### How it works

The `api_client.py` uses `httpx` with the `socksio` transport:

```python
transport = httpx.AsyncHTTPTransport(
    proxy=httpx.Proxy("socks5://127.0.0.1:9050")
)
```

All AI provider calls go through this transport.

#### Verifying Tor is active

```bash
# From the agent's perspective:
docker compose logs agent | grep -i tor

# From outside:
curl --socks5-hostname 127.0.0.1:9050 https://check.torproject.org/api/ip
# Expected: {"IsTor":true}
```

#### Fallback behaviour

If Tor is unreachable at startup:
- The privacy layer logs a warning: `TOR_UNAVAILABLE — falling back to direct connection`
- API calls proceed without Tor (degraded privacy)
- To make Tor **required** (refuse to start without it), set `TOR_REQUIRED=true` in `.env`

---

### 3. Prompt Obfuscation

**Source:** [src/privacy/prompt_obfuscator.py](../../src/privacy/prompt_obfuscator.py)

Every persona has multiple **system prompt variants** — semantically equivalent phrasings of the same role definition. Each time a new agent is spawned, the obfuscator randomly selects one variant.

**Why this matters:** If the system prompt is always identical, a provider can fingerprint all requests as coming from the same software. Rotating variants makes the request signature vary across sessions and users.

Example — `ba_agent` has three variants:

- *Variant A*: "You are a Certified Business Analysis Professional (CBAP) with deep expertise in BABOK v3…"
- *Variant B*: "Your role is business analysis using IIBA BABOK v3 methodologies. You hold CBAP certification…"
- *Variant C*: "Act as an expert business analyst certified to CBAP level under BABOK v3…"

All variants produce equivalent agent behaviour. The provider cannot distinguish which one belongs to the same deployment.

---

### 4. Header Anonymisation

The HTTP client strips or randomises headers that could identify your installation:

- **User-Agent** — set to a generic value rather than `python-httpx/0.x.x`
- **Accept-Language** — not sent (avoids locale fingerprinting)
- **X-Forwarded-For** — not sent
- Custom headers added by some API clients — suppressed

---

## Privacy in Practice

### What the AI provider sees (with all protections active)

| Data point | What the provider actually receives |
|---|---|
| IP address | Tor exit node IP (rotates per circuit) |
| System prompt | Randomly selected variant (varies per request) |
| User prompt | Prompt with PII replaced by placeholders |
| User-Agent | Generic string |
| Language/locale | Not sent |
| Session continuity | None — each circuit rotation breaks linkability |

### What the AI provider does NOT see

- Your real IP address
- Your PESEL, NIP, IBAN, credit card numbers, passport, ID card number
- Your email addresses, phone numbers, postal codes
- The consistent system prompt wording that would identify your software

---

## Session State

**Source:** [src/privacy/pii_session_state.py](../../src/privacy/pii_session_state.py)

`PIISessionState` is a per-agent-invocation object that:
- Accumulates all placeholder → original value mappings during an agent's lifetime
- Is passed to the scrubber on input and restorer on output
- Is discarded when the agent completes (not persisted)

Each agent spawn creates a fresh `PIISessionState`. This means placeholders do not leak between concurrent agents or across sessions.

---

## Limitations

The privacy layer reduces exposure — it is not a guarantee of anonymity. Known limitations:

1. **Semantic content leakage**: Even with PII scrubbed, the topic and structure of your prompts may be identifiable (e.g. "Analyse cash flow for a Polish SME" is contextually specific)
2. **Timing correlation**: A determined provider can correlate requests by timing even if IPs rotate
3. **Tor exit node monitoring**: Malicious exit nodes can see decrypted HTTPS content (the TLS termination is at the destination, not the exit node — content is protected by TLS; metadata is not)
4. **Local storage**: PII appears in logs and the SQLite audit database on your machine — these are not encrypted at rest by default
5. **Response storage**: After the LLM generates a response and it is delivered to you, the provider may retain it; the system cannot control provider data retention policies
6. **Human error**: If you include PII in a field the scrubber does not recognise (e.g. a name written in an unusual format), it will not be scrubbed

---

## Configuration

| Setting | Location | Description |
|---|---|---|
| `TOR_SOCKS_PROXY` | `.env` | SOCKS5 proxy address (default: `socks5://127.0.0.1:9050`) |
| `TOR_REQUIRED` | `.env` | If `true`, agent refuses to start without Tor |
| PII patterns | `src/privacy/pii_patterns.py` | Add custom regex patterns here |
| Prompt variants | `src/agents/personas.py` | Add more variants per persona |
| Scrubbing on/off | Runtime API | PII scrubbing can be toggled per session via the API (admin only) |
