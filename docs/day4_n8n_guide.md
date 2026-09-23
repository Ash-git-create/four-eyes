# Day 4: the full n8n workflow

You build this in the editor, one node at a time, and test each node before adding the next. Export to `workflows/kb-assistant.json` when it works.

⚠️ = check the n8n 2.40 docs before relying on it. The guide was written without the editor open.

## Shape

```
Webhook ─▶ Set run_id ─▶ Respond "received" (202)
                │
                ▼
         /redact ─▶ audit: request_received
                │
                ▼
         /search ─▶ IF top score too low? ──yes──▶ template "can't answer" ─┐
                │ no                                                         │
                ▼                                                            │
         budget_status() ─▶ IF allowed? ──no──▶ audit: budget_blocked (stop) │
                │ yes                                                        │
                ▼                                                            │
         Claude (HTTP) ─▶ log_model_call() ─▶ audit: drafted                 │
                │                                                            │
                ▼                                                            │
         /claims-check (flags unmatched health statements) ◀───────────────┘
                │
                ▼
         Wait for approval ─▶ IF approved ─▶ send + audit: approved, sent
                                   └──────▶ audit: rejected

Separate workflow: Error Trigger ─▶ audit: error
```

## Before you start: two credentials

1. **Postgres, `kb (n8n_app)`**: host `postgres` (the compose service name, not localhost), database `kb`, user `n8n_app`, password `N8N_APP_DB_PASSWORD` from `.env`. This role can only INSERT into `audit_log` and `model_calls` and read the budget; the tests in `retrieval/tests/test_governance_db.py` prove it. **Do not** give n8n the `kb_app` owner password.
2. **Anthropic API key**: create it as a Header Auth credential (name `x-api-key`), so the key sits in n8n's encrypted credential store and never in the workflow JSON. ⚠️ n8n also has a built-in Anthropic credential type; either works for the HTTP Request node.

## Nodes

### 1. Webhook (reuse from Day 1) + Respond immediately
Input: `{"question": "...", "customer_email": "..."}`.
**Respond to Webhook comes right after the trigger** and returns 202 "received, pending review". A human approval can take hours, and no HTTP caller waits that long. The reply goes out later by email, not in the webhook response.

### 2. Edit Fields (Set): `run_id`
`run_id = {{ $execution.id }}`. Every audit row and model call carries it, so one query shows the whole story of one request. ⚠️ Check the expression name in 2.40.

### 3. HTTP Request → `POST http://host.docker.internal:8000/redact`
Body `{ "text": {{ JSON.stringify($json.body.question) }} }`. From here on, use **only** `$json.text` (redacted). `customer_email` stays in n8n for the reply and is never sent to Claude.
Why a Python endpoint and not a Code node: it's tested (`test_redaction.py`), including the documented gap that names are not caught.

### 4. Postgres: audit `request_received`
Operation: Execute Query.
```sql
INSERT INTO audit_log (run_id, event, actor, details)
VALUES ($1, 'request_received', 'system', $2::jsonb)
```
Parameters: `run_id`, and `{{ JSON.stringify({redactions: $json.redactions}) }}`. Log counts, never the raw text.
⚠️ Use the node's query-parameter option rather than pasting expressions into the SQL string, because string pasting is SQL injection.

### 5. HTTP Request → `POST /search`
`{ "query": <redacted text>, "k": 5, "mode": "balanced" }`. Balanced reserves 2 of the 5 slots each for policy and product chunks, because plain reranking pushed products above the policy a question was about (EVIDENCE.md). Keep k=5: that's the k the eval measured.

### 6. IF: low retrieval confidence
Condition: `results[0].rerank_score < -5` (results are sorted by rerank score, so `[0]` is the best). In the Day 3 eval run, this cut-off refuses 5/10 unanswerable questions and wrongly refuses 2/45 answerable ones (balanced mode, `eval/results/retrieval_2026-09-22T144446Z.json`). It's a cheap pre-filter for obvious misses, not the main defence. The main defence is the prompt's "say you don't know" rule and the approver.
Yes-branch: Set a template reply ("We can't answer this from our information; a colleague will get back to you."), then jump to approval. It skips Claude, so it costs nothing.

### 7. Postgres: `SELECT * FROM budget_status()` → IF `allowed`
False: audit `budget_blocked`, then stop. The cap is `budget.daily_cap_usd` (2.00 USD by default), counted per Europe/Berlin day.
Known limit: two runs checking at the same moment can both pass. It's documented in `002_governance.sql`.

### 8. Code node: build the prompt
Small, so a Code node is fine here:
```js
const q = $('Redact').first().json.text;
const chunks = $('Search').first().json.results
  .map((r, i) => `[${i + 1}] chunk_id: ${r.chunk_id}\n${r.text}`).join('\n\n');
return [{ json: { user: `Question:\n${q}\n\nSources:\n${chunks}` } }];
```

### 9. HTTP Request → Anthropic Messages API
- `POST https://api.anthropic.com/v1/messages`, credential from above, header `anthropic-version: 2023-06-01`.
- Body:
  ```json
  {
    "model": "claude-opus-5",
    "max_tokens": 2000,
    "output_config": {"effort": "low"},
    "system": "<paste prompts/draft_system.md>",
    "messages": [{"role": "user", "content": "{{ $json.user }}"}]
  }
  ```
  Use `JSON.stringify` for the user content, as on Day 1.
- **The model is your decision.** Opus 5 is the most capable; Sonnet 5 costs 40% as much per token; both are priced in `model_prices`. Short, grounded drafts are a good case for low effort.
- Why HTTP Request and not n8n's Anthropic/AI Agent node: you see the exact request, and the response's `usage` block (`input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`) is right there for cost logging. ⚠️ Check whether the 2.40 Anthropic node exposes `usage`. If it does, it's a valid alternative, and knowing both is good for the interview.
- Check `stop_reason` before using the text. If it's `refusal` or `max_tokens`, go to the error path.

### 10. Postgres: `log_model_call(...)`
```sql
SELECT log_model_call($1, $2, $3, $4, $5, $6, $7) AS cost_usd
```
Parameters: run_id, **`$json.model` from the response** (the model that actually answered), and the four usage numbers plus latency. It raises an error on an unpriced model, so an unknown model can't silently skip the budget.

### 11. HTTP Request → `POST /claims-check`
Body: `{ "draft": <the text Claude wrote> }`. Returns each sentence with `health_claim_like`, `flagged`, and the closest authorised register entry (`policy_item_code`, `entry_id`, `claim`, `score`).
Pass `flagged_count` and the flagged sentences to the approver, with the closest authorised claim next to each, so they can see what the wording would have to look like.
The register (2,337 records, 269 authorised) is loaded with `uv run ingest-claims`.
**Never call this compliance.** It is a similarity flag against a register, with a provisional threshold. The response carries that disclaimer; keep it in the approval message.

### 12. Approval: Wait node
⚠️ This is the part to read up on most carefully in the 2.40 docs: the Wait node's "On Webhook Call" resume mode and `$execution.resumeUrl`, and the "Send and Wait for Response" operation on the email/Slack nodes.
Send the approver the redacted question, the draft with citations, the claims flags, and approve/reject links.
**Interview trap:** a resume URL is a bearer secret. Anyone who has it can approve, including the requester if it leaks. Note what 2.40 offers to restrict this (a form behind n8n login, or a check of who approved against an approver list), and write the gap in GOVERNANCE.md if nothing does.

### 13. IF approved
- Approved: send the reply to `customer_email` (for the demo: an email to yourself), then audit `approved` with actor = approver, then `sent`.
- Rejected: audit `rejected` with the reason.

### 14. Error workflow
Separate workflow: **Error Trigger → Postgres audit `error`** with the node name and message. Set it as the main workflow's error workflow in its settings. ⚠️ Check where this setting lives in 2.x.

## Done when
- One end-to-end run shows up in `SELECT * FROM audit_log WHERE run_id = '<id>' ORDER BY id` as a complete story.
- A run with the budget set to 0 stops before Claude and logs `budget_blocked`.
- A rejected draft is never sent.
- Real cost per request and p50 end-to-end latency go into EVIDENCE.md, measured from `model_calls`, not estimated.
