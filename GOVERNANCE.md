# Governance

How data moves through this system, what reaches which model, who may do what, and what this project does **not** do. Written for a demo running on one laptop; every claim here is either visible in the code or listed as a gap.

## 1. Data flow

```
customer question (HTTP webhook, header-auth)
   │  full text incl. personal data
   ▼
n8n: Set run_id ──▶ Respond 202 (no answer yet)
   │
   ▼
POST /redact (local Flask)          ── email, IBAN, card, phone, postcode+town → [EMAIL] etc.
   │  redacted text only from here on
   ├──▶ audit_log: request_received (counts of redactions, never the values)
   ▼
POST /search (local Flask → Postgres/pgvector)   ── embeddings computed locally, no network call
   │  top 5 chunks (sample policies + Open Food Facts products)
   ▼
confidence gate ── below threshold: templated "cannot answer", no model call
   ▼
budget_status() ── daily cap reached: stop, audit_log: budget_blocked
   ▼
LLM draft (external API)            ── redacted question + retrieved chunks + system prompt
   │                                   LEAVES THE MACHINE. See §3.
   ├──▶ model_calls: tokens and cost
   ├──▶ POST /claims-check (local)  ── heuristic flag vs EU register
   ├──▶ audit_log: drafted, approval_requested (stores the draft)
   ▼
human approval (n8n Wait node, resume URL)
   │
   ├── invalid approver / self-approval → audit_log: approval_denied | self_approval_denied, stop
   ├── rejected  → audit_log: rejected (actor = approver)
   └── approved  → send → audit_log: approved, sent (actor = approver)

failures anywhere → kb-error-handler workflow → audit_log: error
```

## 2. Data stored, and for how long

| Where | What | Retention |
|---|---|---|
| `chunks` (Postgres) | Open Food Facts product data, sample policy text. No personal data. | Until re-ingested |
| `claims` (Postgres) | EU health claims register. Public data. | Until re-ingested |
| `audit_log` (Postgres) | Redaction counts, model and cost, the **redacted draft**, approver identity, errors | **Unbounded, append-only.** No deletion routine exists. See gaps. |
| `model_calls` (Postgres) | run id, model, token counts, cost | Unbounded |
| `approvers` (Postgres) | approver email, SHA-256 of their approval key | Until removed by an admin |
| n8n execution data | **The full input and output of every node, including the unredacted question** | 14 days (`EXECUTIONS_DATA_PRUNE`, `EXECUTIONS_DATA_MAX_AGE=336h`) |
| `data/raw/` | Source snapshots, gitignored | Manual |

The n8n execution store is the largest personal-data exposure in the system: it holds the original question before redaction. It is pruned after 14 days, and n8n's own telemetry is disabled (`N8N_DIAGNOSTICS_ENABLED=false`).

## 3. What reaches which model

| Model | Runs | Receives |
|---|---|---|
| `intfloat/multilingual-e5-small` (embeddings) | **Locally**, in the Flask process | Redacted question; corpus text at ingestion |
| `cross-encoder/mmarco-mMiniLMv2-L12` (reranker) | **Locally** | Redacted question + candidate chunks |
| Drafting LLM | **External API** | Redacted question, the 5 retrieved chunks, the system prompt |

Retrieval never leaves the machine. Only the drafting step sends anything outside, and only redacted text.

**The drafting model is currently `openai/gpt-oss-120b` via Groq's free tier, not Claude.** The design targets Claude (the request is a plain HTTP call with the model and prices in `model_prices`), but there was no Anthropic API credit during development. Consequences to state plainly: data is processed by a third party in the US under a free-tier agreement whose retention and training terms must be read before any real data is used; and `cost_usd` is 0.00 because the tier is free, not because the pipeline is cheap.

## 4. Personal data handling

Redaction (`retrieval/redaction.py`) is pattern-based and catches: email addresses, IBANs, payment card numbers (Luhn-checked), phone numbers, German postcode + town.

**It does not catch personal names, street addresses, dates of birth or order numbers tied to a person.** This is a documented gap with a test pinning the behaviour (`test_names_are_not_redacted_documented_gap`). A customer writing "Hallo, ich bin Jana Muster" sends that name to the drafting model. Closing it needs named-entity recognition, which is not in this project.

The customer's email address is kept in n8n for the reply and is never part of a model prompt.

## 5. Roles and permissions

**Three roles:** requester (submits a question), approver (releases a draft), admin (changes the system).

**What n8n's community edition enforces** (verified in Settings → Users on n8n 2.40.5 self-hosted): there is one Owner (the admin). Other people can be invited as Members. The **Admin role and workflow/credential sharing are paid features**. So on this instance, only the owner can edit workflows or see the credentials; invited members cannot act on this workflow at all. Approvers therefore do not work through the n8n UI.

**What this project enforces itself**, in the database, so it does not depend on the n8n edition:

| Control | Mechanism |
|---|---|
| Only listed approvers may release a draft | `approvers` table + `check_approval()`; keys stored as SHA-256. Verified live: wrong key and unknown approver both refused (`approval_denied`) |
| Nobody approves their own request | `check_approval()` compares approver to requester; refusal is audited as `self_approval_denied`. Verified live |
| n8n cannot grant itself approval rights | `n8n_app` may EXECUTE `check_approval()` but has no privileges on `approvers` |
| n8n cannot alter history | `n8n_app` has INSERT only on `audit_log`; UPDATE/DELETE/TRUNCATE are additionally blocked by triggers for everyone |
| An unpriced model cannot bypass the budget | `log_model_call()` raises on a model with no price row |

**A failure mode worth naming:** n8n 2.x separates a draft from the published version, and production runs use the published one. Three approvals passed unchecked (audit runs 28-30) because the approval nodes existed only in the draft. The audit log records them permanently; publishing the workflow fixed it (runs 31-33).

## 6. Credentials

| Secret | Where it lives |
|---|---|
| Postgres passwords, n8n encryption key | `.env`, gitignored, not committed |
| Webhook API key, LLM API key, database credential | n8n's credential store, encrypted with `N8N_ENCRYPTION_KEY` |
| Approval keys | Never stored: only SHA-256 hashes in `approvers` |

Workflow exports in `workflows/` reference credentials by id and contain no secret values (checked before each commit). Losing `N8N_ENCRYPTION_KEY` makes every stored credential unreadable.

Postgres uses three separate roles: `kb_app` owns the knowledge base, `n8n_app` has the narrow grants above, and n8n's own internal database is a different database with a different owner.

## 7. The claims check is not a compliance check

`/claims-check` flags sentences that read like a health claim and have no close match in the EU register of authorised claims. It is a similarity score against a snapshot, with a **provisional threshold set from five example sentences**. It is a prompt for the human approver, nothing more. It must never be described as a compliance, legal or regulatory check. The endpoint returns that disclaimer in every response.

## 8. Known gaps

1. **Approval by bearer URL.** The n8n Wait node's resume URL is `/webhook-waiting/<executionId>?signature=<hmac>`; n8n rejects a wrong or missing signature with 401 "Invalid token" (verified). It is still a bearer secret: anyone holding the link can call it, and it is single-use. The real control is the approval key checked in the database (§5), plus an expiry on the wait. A production system would use an approval form behind login.
2. **The requester's identity is self-declared.** The self-approval rule compares the approver against the address supplied in the request, so it stops mistakes, not a determined attacker.
3. **Names are not redacted** (§4).
4. **No retention limit on `audit_log`.** It grows forever and contains drafts. A real deployment needs a documented retention period and a lawful way to apply it — which conflicts with append-only storage and must be designed deliberately (e.g. partition and drop whole periods).
5. **Append-only has a boundary.** The triggers stop the application and the table owner from changing rows. A superuser, or anyone with the container's disk, can still drop the table. Append-only here means tamper-evident against the app, not tamper-proof.
6. **The budget cap can be overshot** by the calls already in flight when two runs check at the same moment.
7. **No data subject access or erasure process.** There is no way to find and remove one person's data across `audit_log` and the n8n execution store.
8. **Drafting data leaves the EU** (§3).
9. **Single machine, no backups, no encryption at rest** beyond what the host provides.

## 9. Out of scope

Legal compliance advice, medical advice, a production deployment, multi-tenancy, and any claim that this system has been used with real customer data. It has not.
