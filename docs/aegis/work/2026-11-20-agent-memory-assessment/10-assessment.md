# Agent Memory and Retrieval Assessment

Date: `2026-11-20`
Status: `read-only assessment; measurements pending`
Scope: how an agent turn gets knowledge beyond its own conversation, at commit `f2fe852`

## 1. What an agent can rely on

| Store | Lifetime | Written by | Reaches the model as |
| --- | --- | --- | --- |
| Conversation history with compaction | One session, per agent and per delegated call | Runtime | Input items |
| Work project ledger: assets, relations, findings, evidence, work items, attack paths | One work project | Agents and operators | A JSON section of the instructions, rebuilt every turn; list and detail tools |
| Credentials Vault | One work project, or one session outside a project | Agents and operators | A JSON section of the instructions, rebuilt every turn; `list_credentials` |
| Knowledge base (LightRAG) | Instance-wide, one workspace `z3r0` | Admins, by uploading Markdown or PDF | A text section of the instructions, retrieved every turn |

Nothing carries what an agent learned in a chat session or a Flow into a later
one, except credentials. Agents have no tool that reads or writes the
knowledge base.

## 2. Knowledge base retrieval, as implemented

- Runs before every model turn of the main agent (`core/runtime/session.py`)
  and of every subagent (`core/delegation/subagents.py`).
- The query is the current input, cut at 4000 characters, plus up to three
  recent user topics or the user goals of the latest compaction summary
  (`core/conversation/retrieval.py`).
- `retrieve_lightrag_context` returns nothing when no document is processed,
  after one status query. Otherwise it calls `aquery` in `mix` mode with
  `only_need_context`, which makes LightRAG extract keywords with an LLM call
  and then run the embedding, vector, and graph queries. LightRAG caches the
  keyword extraction for an identical query.
- The result is appended to the instructions after the stable prefix and the
  work project section (`core/agent/registry.py`). LightRAG's default budget
  for the whole context is 30000 tokens.
- Any failure is logged and treated as an empty result
  (`core/lightrag/runtime.py`). The agent and the operator see no difference
  between "nothing relevant" and "retrieval is down".
- Uploading and deleting documents is admin-only. Every user and every work
  project retrieves from the same corpus.

## 3. Conversation compaction, as implemented

- One compaction scope per session, viewing agent, and delegated call.
- Triggers when the estimated input reaches 90% of the agent's configured
  context window, aims at 20%, and refuses the turn at 98%.
- Keeps the most recent items and replaces the rest with a summary of at most
  8000 tokens in fixed sections, written by the agent's own model.
- The development configuration sets `context_window` to 1,000,000 for all six
  agents, so compaction starts at an estimated 900,000 tokens.

## 4. Delegation, as implemented

- A subagent receives the brief written by its parent and the id of the bound
  work item. It has its own history, keyed by the delegating tool call.
- Each of its turns gets the same work project and credentials sections and
  runs the same knowledge base retrieval as the main agent.

## 5. Findings

1. Retrieval is injected, never requested. The agent cannot ask the knowledge
   base a question, refine one, or know that the knowledge base exists.
2. Retrieval cost is paid on every turn of every agent once the knowledge base
   holds a document, whether or not the turn needs reference material.
3. A retrieval failure is silent.
4. The instructions change whenever the retrieved text changes. Whether that
   costs provider-side prompt caching is not measured.
5. Experience has no home. The ledger holds facts about one project; nothing
   holds what worked or failed across projects, chat sessions, or Flows.
6. The knowledge base has no tenant boundary. A cross-project memory built on
   it would carry target data between projects.
7. No fixed task set exists to tell whether a change to prompts, retrieval, or
   memory makes agents better or worse.

## 6. Not measured, and why

Per-turn retrieval latency, injected token count, and the cached share of
input tokens are not measured. The development database holds no knowledge
documents and no sessions, the configured embedding endpoint
`127.0.0.1:8317` does not answer, and a measurement calls the configured
model.

Measurement plan: a scratch database with a small fixed corpus; the same
scripted chat turns with injection on and off; per turn, record retrieval
time, keyword-extraction calls, instruction tokens, and `input_tokens` and
`cached_tokens` from the provider's usage.

## 7. Direction to design next

- Reference material becomes a tool the agent calls, and the per-turn
  injection goes away.
- Experience is written at fixed points (a Flow report, an accepted work item,
  a completed project) as entries linked to evidence, with an expiry, scoped
  so that target data does not cross tenants, and visible to the operator.
- A fixed task set with recorded outcomes comes before either change.
