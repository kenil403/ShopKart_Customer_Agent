# ShopKart Customer Support AI Agent

A support agent for a fictional Indian online store. It answers policy questions from six documents **with a citation for every claim**, refuses to answer when the documents don't cover something, and takes real actions on customer orders (status, order list, returns) — but only after verifying the customer's email and checking the current return policy.

Built with **LangGraph** (agent), a self-written **MCP server** (order tools), **BM25 retrieval** (RAG), **FastAPI** (backend) and **React** (frontend). Includes a 35-case evaluation suite and 22 offline tests.

---

## Contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [How the agent decides](#how-the-agent-decides)
- [Setup](#setup)
- [Running the evaluation](#running-the-evaluation)
- [Project structure](#project-structure)
- [Design decisions](#design-decisions)
- [Evaluation results](#evaluation-results)
- [What didn't work / what I'd improve](#what-didnt-work--what-id-improve)

---

## What it does

| Capability | How it works |
| --- | --- |
| **Answers policy questions** | BM25 search over 27 sections from 6 policy documents. Every claim ends with `[file.md § section]`. Citations are validated against the real index — fabricated ones are flagged. |
| **Says "I don't know"** | If no section scores above a relevance floor, the retriever returns `NO_MATCH` and the agent refuses rather than guessing. |
| **Checks order status** | Through an MCP server, only after the customer's email matches the order. |
| **Lists a customer's orders** | Numbered (1, 2, 3…) so follow-ups like "the second one" resolve correctly. |
| **Files returns** | A deterministic rules engine decides eligibility under the current policy and returns the exact sections it applied. |
| **Remembers the conversation** | LangGraph checkpointer in SQLite, so "return the second one, it arrived damaged" works — and survives a server restart. |
| **Shows its work** | Every answer displays which documents and tools produced it. `?dev=1` reveals the full technical trace. |

### A real example

> **Customer:** What's the status of my orders? My email is priya.sharma@example.com
> **Agent:** *(lists 3 orders, numbered)*
> **Customer:** Return the second one, it arrived damaged.
> **Agent:** I've created a return for **ORD-1002** (Bluetooth Earbuds). Damaged electronics can be returned within 10 days of delivery [03_returns_policy_update_2026.md § 1. New Return Window], and pickup is free [03_returns_policy_update_2026.md § 3. Return Pickup Charges].

Between those two turns the agent resolved "the second one" from memory, verified the email against the order, searched the returns policy, and ran the eligibility engine — all without being told to.

---

## Architecture

```
                   ┌──────────────────────────────┐
   Customer ─────▶ │  React UI                    │
                   │  chat · FAQ · history        │
                   └──────────────┬───────────────┘
                                  │ POST /api/chat
                   ┌──────────────▼───────────────┐
                   │  FastAPI (api/main.py)       │
                   └──────────────┬───────────────┘
                   ┌──────────────▼───────────────┐
                   │  SupportAgent (runner.py)    │──── SQLite (conversation memory)
                   └──────────────┬───────────────┘
                   ┌──────────────▼───────────────┐
                   │  LangGraph (graph.py)        │
                   │  agent node ⇄ tools node     │
                   └───┬──────────────────────┬───┘
                       │                      │
           ┌───────────▼──────────┐  ┌────────▼─────────────┐
           │ ModelRouter (llm.py) │  │ ToolExecutor         │
           │ Groq → Gemini →      │  │ ① email gate         │
           │ Mistral, with        │  │ ② policy check       │
           │ cooldowns            │  └────┬────────────┬────┘
           └──────────────────────┘       │            │
                                  ┌───────▼─────┐  ┌───▼──────────────┐
                                  │ BM25 search │  │ MCP client       │
                                  │ 27 sections │  │ (stdio session)  │
                                  └─────────────┘  └───┬──────────────┘
                                                       │ MCP protocol
                                            ┌──────────▼───────────────┐
                                            │ MCP SERVER (own process) │
                                            │ get_order_status         │
                                            │ get_customer_orders      │
                                            │ request_return           │
                                            │ orders.json + rules      │
                                            └──────────────────────────┘
```

**Two boundaries that matter:**

1. The agent **cannot read `orders.json` directly** — order data only arrives through the MCP server.
2. The LLM **never talks to the MCP server directly** — every tool call passes through `ToolExecutor`, where the security checks live in code.

---

## How the agent decides

Each turn, the LLM chooses one of three paths on its own:

| Situation | What it does |
| --- | --- |
| Policy question | Calls `search_policy`, answers only from the returned sections, cites them |
| Question about the customer's own order | Needs an email the customer typed; if missing, asks for it |
| Anything ambiguous or incomplete | Asks a short clarifying question instead of calling tools |

### The two security gates (enforced in code, not the prompt)

**Gate 1 — email verification.** The email must appear in a message the customer actually typed in this conversation. An email the LLM invents, or copies from a tool result, is rejected. For a specific order, the agent asks the MCP server for that order and compares emails. A mismatch returns the same message as "not found", so the reply can't even confirm the order exists.

**Gate 2 — policy check before returns.** A return is never filed until the current return policy has been read in this conversation. If it hasn't, `ToolExecutor` retrieves the relevant sections automatically — targeted at that item's category and the customer's stated reason — before the return is allowed through.

### Return eligibility (deterministic, in `policy/return_rules.py`)

Checked in this order, each result carrying the policy sections it used:

| # | Check | Outcome | Source |
| --- | --- | --- | --- |
| 1 | Already returned / cancelled | Rejected | — |
| 2 | Not delivered yet | Rejected (window starts at delivery) | 2026 § 1 |
| 3 | Final Sale item | Rejected | 2026 § 4 |
| 4 | Innerwear, lingerie, socks, gift card | Rejected | 2024 § 2 |
| 5 | Customised / personalised | Rejected | 2024 § 2 |
| 6 | Beauty / personal care | Opened → rejected; unopened → manual review | 2024 § 2 |
| 7 | Category with no window in the 2026 update | **Manual review** (never guess) | 2026 § 1 |
| 8 | Damaged, reported within 48 hours | Approved, photo required | 2026 § 2 |
| 9 | Electronics + change of mind | Rejected | 2026 § 1 |
| 10 | Past the window (15 days clothing/footwear/home; 10 days electronics) | Rejected | 2026 § 1 |
| 11 | Otherwise | Approved, free pickup | 2026 § 1, § 3 |

This is code rather than LLM judgement because a return is an irreversible action: date arithmetic and rule precedence must be exact, repeatable and testable. The LLM's job is to explain the decision warmly; the engine's job is to make it.

---

## Setup

**Requirements:** Python 3.10+, [uv](https://docs.astral.sh/uv/), Node 18+, and at least one free API key (Groq, Gemini or Mistral).

### 1. Backend

```bash
cd backend
uv sync                          # installs the exact versions from uv.lock
cp .env.example .env             # Windows: copy .env.example .env
```

Open `backend/.env` and add your keys:

```env
GROQ_API_KEY=gsk_...
GOOGLE_API_KEY=AIza...
MISTRAL_API_KEY=...
LLM_MODEL=groq:openai/gpt-oss-120b
LLM_FALLBACKS=google_genai:gemini-3.1-flash-lite,mistralai:mistral-small-latest
STORE_TODAY=2026-09-26
```

`LLM_MODEL` answers every message. If it hits a rate limit or breaks, the router automatically switches to the next model and returns to the main one once it recovers. One key is enough to run — models without a key are skipped.

```bash
uv run python -m scripts.check_llm     # tests each key with a real tool call
uv run pytest -q                       # 22 offline tests, no API key needed
uv run python run.py                   # API on http://127.0.0.1:8000
```

### 2. Frontend (second terminal)

```bash
cd frontend
npm install
npm run dev
```

- Customer view: **http://127.0.0.1:5173**
- Developer view: **http://127.0.0.1:5173/?dev=1** — shows the model in use, MCP/RAG tool calls and BM25 scores

### Free-tier models

| Model | Free limits (Sept 2026) | Role |
| --- | --- | --- |
| `groq:openai/gpt-oss-120b` | 30 req/min, 1,000/day, 8,000 tokens/min | Main — best tool calling |
| `google_genai:gemini-3.1-flash-lite` | 15 req/min, ~500/day | First fallback |
| `mistralai:mistral-small-latest` | 1 req/second | Second fallback |

Run `uv run python -m scripts.check_llm --list` to see every model your keys can use.

### Using the official assessment data

The repo ships **sample** `orders.json` and `questions.txt`. To swap in the official files:

1. Replace `backend/data/orders.json` and `backend/data/questions.txt`.
2. If field names differ, add them to `ALIASES` in `app/orders_repo.py` (it already handles `id`/`order_id`, `email`/`customer_email`, etc.).
3. Update the sample order IDs in `eval/build_cases.py`, then run `uv run python eval/build_cases.py`.
4. Set `STORE_TODAY` to a date that fits the new delivery dates.

---

## Running the evaluation

```bash
cd backend

# The 10 provided questions -> answers.json
uv run python -m eval.run_questions

# A wider question set, to probe different behaviours
uv run python -m eval.run_questions --questions data/questions_extra.txt --out answers_extra.json

# Full 35-case evaluation -> eval/results/report.md + results.json
uv run python -m eval.run_eval
```

Both scripts save progress after every question, wait out rate limits and retry, and accept `--resume` to continue after an interruption.

### The evaluation set (35 cases, 7 multi-turn)

| Category | Cases | Checks |
| --- | --- | --- |
| Policy questions | 14 | Correct fact + correct citation, including 2026-over-2024 precedence and table lookups |
| Must refuse | 4 | Says "I don't know" for things not in the documents |
| Verification | 4 | Asks for email; never leaks another customer's order |
| Order tools | 2 | Status and listing via MCP |
| Returns | 6 | Final Sale, non-returnable, not delivered, expired window, electronics change-of-mind, one success |
| Multi-turn | 5 | "Return the second one", follow-ups, ask-then-act, cross-account probing |

Each turn is graded on: tools that must or must not run, required and forbidden phrases, required citations, and citation validity. Failures are classified as `tool_selection`, `verification_bypass`, `missed_verification`, `privacy_leak`, `hallucination`, `wrong_answer`, `missing_citation`, `invalid_citation` or `runtime_error` — so the report says *what kind* of thing broke, not just that something did.

### Offline tests

```bash
uv run pytest -q     # 22 tests, ~3 seconds, no API key
```

A scripted fake LLM replays fixed tool calls, so these are deterministic. They cover every return-eligibility branch, retrieval quality, both security gates, the model router (including a replay of a real three-provider failure), and memory surviving a restart.

---

## Project structure

```
shopkart-support-agent/
├── backend/
│   ├── app/
│   │   ├── config.py                 # settings, model list, pinned store date
│   │   ├── orders_repo.py            # loads orders.json, normalises field names
│   │   ├── rag/
│   │   │   ├── ingest.py             # section chunking + supersession tags
│   │   │   └── retriever.py          # BM25 + synonyms, NO_MATCH floor
│   │   ├── policy/return_rules.py    # deterministic eligibility engine
│   │   ├── mcp_server/server.py      # MCP server: the 3 order tools
│   │   ├── agent/
│   │   │   ├── llm.py                # ModelRouter: cooldowns, pacing, safe errors
│   │   │   ├── mcp_client.py         # persistent stdio MCP session
│   │   │   ├── tools.py              # tool schemas + the two security gates
│   │   │   ├── prompts.py            # system prompt
│   │   │   ├── graph.py              # LangGraph agent ⇄ tools loop
│   │   │   └── runner.py             # SupportAgent: citations, trace, memory
│   │   └── api/main.py               # FastAPI routes
│   ├── data/
│   │   ├── policy_docs/              # 6 policy markdown files
│   │   ├── orders.json               # sample orders
│   │   ├── questions.txt             # the 10 provided questions
│   │   └── questions_extra.txt       # 15 extra probing questions
│   ├── eval/
│   │   ├── build_cases.py            # generates test_cases.json
│   │   ├── test_cases.json           # 35 evaluation cases
│   │   ├── run_eval.py               # accuracy + failure-type report
│   │   ├── run_questions.py          # questions.txt -> answers.json
│   │   └── common.py                 # wait-and-retry when models are busy
│   ├── scripts/
│   │   ├── check_llm.py              # test API keys, list valid models
│   │   └── check_mcp.py              # call MCP tools directly, no LLM
│   ├── tests/test_offline.py         # 22 offline tests
│   ├── run.py                        # start the API (Windows-safe)
│   └── pyproject.toml, uv.lock
├── frontend/
│   └── src/
│       ├── App.jsx                   # state: conversations, history, sending
│       ├── faq.js, history.js        # FAQ content, localStorage history
│       └── components/               # HistorySidebar, Faq, Message, Evidence, StatusBar
└── docs/PRESENTATION_GUIDE.md
```

---

## Design decisions

**One chunk per `##` section.** The brief requires citing document *and* section, so a section is exactly the unit to retrieve. It also keeps tables (like the shipping charge grid) intact.

**BM25 before embeddings.** The corpus is 27 short sections dense with exact terms — city names, "COD", "₹499", "Final Sale". Keyword search matches those precisely, costs nothing and runs offline. Its weakness is paraphrase ("jeans" vs "clothing"), handled with a small domain synonym map. Embeddings can be enabled with `EMBEDDING_MODEL` and are fused with BM25 using Reciprocal Rank Fusion.

**Security in code, not prompts.** A prompt is a request; code is a guarantee. Even if the LLM is talked into trying, the tool refuses and the customer's data never enters the model's context.

**MCP over stdio.** The server runs on the same machine and only this backend uses it, so stdio needs no port, no auth and no network. It stops with the backend. A shared server would use the HTTP transport instead.

**A custom model router instead of LangChain's `with_fallbacks`.** Fallback chains retry every model on every message, which burns free-tier quota and keeps hammering a retired model. The router tracks each model as ready, cooling or disabled, honours the provider's own "retry in N seconds" hint, and returns to the main model automatically.

**Pinned store date.** `STORE_TODAY` fixes "today", so a case that passes now ("delivered 8 days ago, within 15") still passes next month.

### The hardest decision: which return rules are current

The 2026 update says it *replaces the return window and pickup charge rules* and that *all other rules remain unchanged*. That creates three cases I had to resolve explicitly:

1. **Window and pickup fee** — the 2026 rules win (15 days clothing/footwear/home & kitchen; 10 days electronics, defects only; free pickup).
2. **Non-returnable items, item condition, exchanges** — still governed by the 2024 policy.
3. **Categories the 2026 update never lists** (beauty, books, and anything else) — the old 7-day "most items" rule was replaced, but no new window was given. Applying 7 days would be a guess; so would 15. I route these to **manual review** instead.

That third case is the one I'd defend in a review: the brief says the agent must never guess when answering. I applied the same standard to *acting*.

To make the precedence visible to the model as well, sections of the 2024 policy that the 2026 update replaced are tagged `⚠ SUPERSEDED` inside the retrieved text, pointing at the newer section.

---

## Evaluation results

> Run `uv run python -m eval.run_eval`, then paste the contents of `backend/eval/results/report.md` below. The report records which model answered each case.

| Category | Passed | Total |
| --- | --- | --- |
| Policy questions | – | 14 |
| Must refuse | – | 4 |
| Verification | – | 4 |
| Order tools | – | 2 |
| Returns | – | 6 |
| Multi-turn | – | 5 |
| **Total** | **–** | **35** |

Offline test suite: **22/22 passing**.

---

## What didn't work / what I'd improve

### Things that broke, and what I did about them

**Free-tier LLMs failed after the first hour.** The first version worked, then started failing: Groq had retired `llama-3.3-70b-versatile` (404 on every call), Gemini 2.5 Flash allows only 20 requests per *day*, and Mistral's free tier caps at 1 request per second. Worse, LangChain's fallback chain combined with the SDKs' own retries meant each failing model was hit *twice per message*, burning quota at double speed. I replaced it with a router that cools down rate-limited models for the exact time the provider asks, disables retired ones outright, paces requests client-side, and switches back to the main model when it recovers. I also cut tokens per request by roughly 35% and reduced a return from 4 model calls to 2.

**A stale lockfile broke a clean install.** `requirements.txt` had been exported before I added the SQLite checkpointer, so installing from it on a fresh machine crashed at startup with `ModuleNotFoundError`. It only surfaced because I simulated a fresh environment rather than trusting my working one. Lesson: test the install path, not just the code path.

**Windows-only crashes.** Two separate issues. The MCP server is started as a subprocess, which fails on Windows under the Selector event loop, so `run.py` forces the Proactor loop. And a test wrote its log to `/tmp`, a path that doesn't exist on Windows; the return was approved but the log write crashed, making a passing feature look broken. The server now creates the folder and treats log failures as non-fatal — a customer's return should never fail because a log file couldn't be written.

**IPv6 vs IPv4.** The frontend proxy pointed at `localhost:8000`. Node resolves `localhost` to IPv6 `::1` first, while Uvicorn listens on IPv4, producing `ECONNREFUSED ::1:8000` and an opaque 500 in the UI. Fixed by targeting `127.0.0.1`.

### What I'd improve with more time

**Email-only verification is weak.** Knowing someone's email is enough to see their orders. It's what the brief specifies, but production needs an OTP or an authenticated session.

**The "I don't know" threshold is blunt.** A fixed BM25 score floor catches clearly off-topic questions, but partially related ones ("customer care phone number" shares vocabulary with "support") slip past and rely on the prompt. I'd add an answerability check — a small NLI or LLM-judge step verifying each claim is actually entailed by a cited chunk before the reply goes out.

**Return reasons are classified by keywords.** "It arrived damaged" is caught reliably, but unusual phrasing could be misread as change-of-mind. I'd have the LLM extract a schema-validated `reason_type` and keep the keyword classifier as a cross-check, flagging disagreements for review.

**The photo requirement isn't enforced.** Damaged-within-48-hours returns tell the customer a photo is required, but there's no upload. Next step: a file input in the chat, stored against the return.

**Rule-based grading is brittle.** A correct answer phrased unexpectedly can fail a phrase check. I'd add an LLM-as-judge pass over the `wrong_answer` bucket and run each case several times to measure variance rather than trusting a single run.

**Manual review adds friction.** Routing unlisted categories to a human is the safe call, but it's a workaround for an ambiguous policy. The real fix is a clarification from the business, then encoding it.

**Everything is single-process.** Conversation memory is SQLite and order state lives in the MCP server's memory, so returns reset on restart. Production needs Postgres for both, plus a real orders database instead of a JSON file.

**History is per-browser.** The sidebar list lives in `localStorage`, so it doesn't follow a customer to another device. Tying conversations to an account server-side would fix it.
