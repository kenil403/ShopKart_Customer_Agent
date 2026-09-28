# Presentation guide (3–5 minute video, camera on)

## Before recording

1. Start the backend (`uv run python run.py`) and frontend (`npm run dev`).
   Record with the **developer view**: http://127.0.0.1:5173/?dev=1 (shows the model in use and the tool trace). Show the customer view briefly too.
2. Run `uv run python -m eval.run_eval` once so you have real numbers to show.
3. Open three tabs: the UI, `README.md` (architecture diagram), `eval/results/report.md`.

## Script

**0:00–0:30 · What it is**
"This is a customer support agent for ShopKart. It does two things: answers policy questions from six documents with a citation for every claim, and takes actions on orders (status, listing, returns) through an MCP server. It's built with LangGraph, FastAPI and React."

**0:30–2:30 · Live demo** (start on the home screen: FAQ in the middle, history on the left) (type these in order, pointing at the evidence strip under each answer)

| Type this | Point out |
|---|---|
| How long does delivery to Ahmedabad take? | green citation chip → `01_shipping_policy.md § 2`; "Searched policies" in the tools row |
| What's your customer care number? | says it doesn't know; no guessing |
| What's the status of ORD-1004? | asks for email; no MCP call ran |
| My email is priya.sharma@example.com | lists her 3 orders; purple **MCP** tag |
| What about ORD-1004? | "couldn't verify" (red blocked step): it's another customer's order |
| Return the second one, it arrived damaged. | memory resolves "second one" → ORD-1002; the return policy is checked automatically → return approved with 2026 citations |

**2:30–3:15 · Architecture** (show the diagram)
"The LLM decides each turn whether to search documents, call a tool, or ask a question. Every tool call passes through a guarded executor with two gates: the email must be typed by the customer and must match the order, and a return is refused until the agent has read the returns policy. The order tools live in a separate MCP server that also re-checks eligibility, so even a misbehaving agent can't create an ineligible return."

**3:15–4:15 · Hardest decision**
"The two returns policies overlap. The 2026 update replaces the return window and pickup fee but keeps every other 2024 rule. So I tag superseded 2024 sections in the retrieved text, and I encoded the current rules in a deterministic engine rather than trusting the LLM with dates. The tricky part: the update defines windows only for some categories. For a category like beauty, the old 7-day rule was replaced but no new rule was given. Using 7 or 15 days would be a guess, so I send those to manual review. That's the brief's 'never guess' rule applied to actions, not just answers."

**4:15–4:45 · Evaluation and what I'd improve**
Show `report.md`: "35 cases across six categories, including refusals and verification. Failures are classified by type, like hallucination, privacy leak, or missing citation." Then name one or two items from the README's improvement section (answerability check, OTP instead of email-only verification).

## Likely interview questions

**Why BM25 and not only embeddings?** The corpus is 27 short sections full of exact tokens (city names, ₹ amounts, "COD", "Final Sale"). Keyword search is precise and free here. Embeddings are an optional hybrid layer (Reciprocal Rank Fusion) for paraphrased questions.

**How do you stop hallucination?** Three layers: retrieval returns `NO_MATCH` when nothing is relevant; the prompt forbids answering beyond the returned text; the backend validates every citation against the index and flags fabricated ones.

**Why verify in the agent instead of the MCP server?** The brief fixes the tool signatures (no email on `get_order_status`). Verifying in the tools node means the LLM never sees an unverified order at all. The server still enforces return eligibility itself.

**How does memory work?** LangGraph's checkpointer stores the full message history and state (`verified_email`, `policy_checked`) per `session_id`. The UI sends the same session id every turn; "Start a new conversation" creates a new one.

**Why a custom tools node instead of the prebuilt ToolNode?** To run the gates and update graph state from tool results (marking the customer verified, recording that the policy was checked).

**What if the LLM makes up an email?** The executor only accepts emails that appear in the customer's own messages, so an invented or copied email is rejected.

**Why three providers, and why a custom router?** The first version used LangChain's `with_fallbacks`. It worked at first, then failed after some use: Groq retired the model (404), Gemini 2.5 Flash allows only 20 requests a day, and every failing model was retried on every message, burning quota twice. The router puts rate-limited models on a cooldown taken from the provider's own "retry in N s" hint, disables retired ones, paces requests, and switches back to the main model when it recovers. In the developer view you can watch one answer come from Groq and the next from Gemini.

**Why two views?** Customers shouldn't see model names or "MCP". The customer view says "Based on Returns Policy Update (2026) § 1" and "Created a return request". The brief's requirement to show the tool or document behind each answer is met in both views; `?dev=1` shows the raw trace for reviewers.

**How does history work?** The sidebar list is stored in the browser; each chat's id is also the LangGraph thread id, and the API stores that memory in SQLite. So you can reopen yesterday's chat, even after restarting the backend, and say "return the second one". Deleting a chat erases both copies. Demo tip: restart the backend on camera, reopen a chat from history, and continue it.
