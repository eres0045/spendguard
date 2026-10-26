# SpendGuard Agent — PayPal AI Hackathon

**Event:** PayPal AI Hackathon ("Build What's Next with PayPal and AI"), Devpost.
**Deadline:** Nov 13, 2026, 1:30 AM IST (Nov 12, 3:00 PM EST). Solo entry.
**Status:** BUILT 2026-10-26 (subagent build) — full Flask app with mandate,
catalog, compare, guardrail, PayPal Orders v2, audit log, and webhook
receiver. Tests: 22/22 passing. NOT deployed (deploy waits for keys).
No secrets live in code or files — all keys via environment variables only.

## Concept

The user writes a shopping mandate in plain language, e.g.
"wireless earbuds under $60, only from these stores, max $100/week".
SpendGuard searches a real catalog, compares options, explains its pick, and
executes a **real PayPal sandbox checkout** with buyer approval. Every decision
is logged with reasons. The demo's signature moment: the agent **refuses** an
over-budget item live, on camera, with the blocked attempt visible in the
audit log. That refusal is the proof the guardrails are real code, not copy.

## Hard requirements (official page, re-read 2026-10-26)

- Meaningfully use BOTH the PayPal developer platform (free sandbox) AND AI.
- Working prototype — static mockups do not count.
- Public repo with a visible open-source license.
- Text description, functional demo (hosted URL or complete run instructions),
  tools used identified, demo video under 3 minutes on public YouTube.
- Judging (5): Implementation, Design, Potential Impact, Innovation/Idea,
  Presentation.

## Architecture (as built)

- `app/` — Flask backend:
  - `mandate.py` — deterministic regex parsing (budget NEVER comes from an LLM).
  - `catalog.py` — Channel3 SDK search + normalization; honest 503 when the key
    is absent, never fake data.
  - `llm.py` — Groq chat completions (GROQ_API_KEY env on deploy; Muse
    connector surrogate for local verification). Falls back to a clearly
    labeled template comparison with `llm_used=false` — never fake AI text.
  - `guardrail.py` — single choke point: server-side price vs mandate cap
    (+ weekly cap, USD-only). Runs at BOTH checkout and capture.
  - `paypal.py` — Orders v2 create/capture, sandbox only. No Payouts, no Vault.
  - `audit.py` + `store.py` — in-memory audit log and mandate/search/order
    stores (a restart clears them; the audit log is a demo trail, not a ledger).
  - `__init__.py` — routes: `/mandate`, `/search`, `/compare`, `/checkout`,
    `/capture`, `/audit`, `/webhook/paypal`, `/health`.
- `tests/test_spendguard.py` — 22 tests (guardrail, audit, mandate parsing,
  honest no-key errors). Run: `.venv/bin/python -m pytest tests/ -q`
- `scripts/verify_groq.py` — one-off live Groq check via the connector
  (prints model + reply only, never a key).
- `web/` — frontend placeholder (not built yet; the API is the prototype).
- Deploy target: Vercel (public HTTPS webhook endpoint for
  `PAYMENT.CAPTURE.COMPLETED`).

## Environment variables (deploy needs these)

| Variable | Purpose |
|---|---|
| `CHANNEL3_API_KEY` | Channel3 catalog search (app "SpendGuard") |
| `PAYPAL_CLIENT_ID` | PayPal sandbox REST app client id |
| `PAYPAL_CLIENT_SECRET` | PayPal sandbox REST app secret |
| `PAYPAL_BASE_URL` | Optional; defaults to the sandbox host |
| `GROQ_API_KEY` | Optional on deploy; without it `/compare` uses the template fallback |
| `GROQ_MODEL` | Optional; defaults to `openai/gpt-oss-20b` |

See `.env.example` (placeholders only, never a filled `.env`).

## Run locally

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests/ -q
.venv/bin/python app/main.py   # http://127.0.0.1:5000
```

## Deliberately avoided

- **Payouts** — limited-release; a fresh sandbox app is not guaranteed access.
- **Vault / billing agreements** — merchant-initiated charges need approval.
- **Multi-vendor payout ideas** — collision with CatalogPay Agent (same event).
- **Donation/sponsorship angles** — collision with PatronAI (same event).

## Prize targets

Best Use of Agentic Commerce ($5,000), Best Use of PayPal + AI ($5,000),
Best Use of Channel3 ($1,500), plus overall placements.

## Verification checklist (in order — build follows verification)

1. [x] Channel3 access (research 2026-10-26): free account at trychannel3.com,
   API key on signup, 1,000 free searches, no credit card required. NOTE: catalog
   is US-only — the demo mandate is USD-denominated.
2. [x] Channel3 account + API key actually issued (2026-10-26 09:28 IST: free
   account created, email verified, app saved as "SpendGuard", 1 API key
   issued and masked, free tier confirmed — 1,000 monthly credits + 1,000
   one-time bonus credits, 0 used, no payment method added). Profile-completion
   banner remains (brands may deny commissions if identity unverified) —
   affects commissions only, not free search credits.
3. [ ] PayPal sandbox app: Orders v2 + capture + webhooks with no special
   approval (app created 2026-10-26 19:00 IST; live create/capture call
   still to be verified once credentials are wired to the deploy).
4. [ ] Webhook receipt on a deployed Vercel endpoint during a test capture.
5. [x] LLM provider: Groq verified live 2026-10-26 (connector surrogate:
   model openai/gpt-oss-20b answered; full comparison path tested with
   llm_used=true and a correct within-budget recommendation). Deploy uses
   GROQ_API_KEY env instead of the surrogate.
6. [ ] Sandbox buyer approval flow films cleanly for the demo video.
7. [ ] Channel3 live search verified once the key is wired to the deploy
   (SDK integration written against channel3-sdk 4.5.0 and introspected;
   no live search run yet — key absent locally).
8. [ ] Webhook signature verification implemented (currently logged as
   UNVERIFIED with a TODO; capture state comes from the Orders API, never
   from webhooks, so this is safe but must be finished before submission).

## Honesty notes

- All money movement is PayPal **sandbox** money, never real money. The demo
  says so.
- Catalog data is live Channel3 data or an explicit 503 "not configured"
  error — never presented as live when it is not. There is no seeded
  fallback in the built app (the scaffold's seeded-catalog idea was dropped
  deliberately: a fake catalog would weaken the guardrail demo).
- Webhook events are logged as UNVERIFIED until signature verification lands;
  capture state is read from the Orders API, never trusted from webhooks.
- The audit log and mandate/search/order stores are in-memory and clear on
  restart.
- Sandbox-VM quirk (not a deploy concern): the VM's egress proxy list breaks
  httpx's default client construction, so the Channel3 client is built with
  `httpx.Client(trust_env=False)`.
