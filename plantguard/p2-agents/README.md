# P2 — Agents + RAG

**Your job:** the "thinking" part. Seven agents investigate the alert, show their reasoning and evidence, and produce costed options. They never act without approval.

## Start here
1. `make mock` from the repo root. Agents call tools with `agents/tool_client.py`: it works against the mock today and P1's gateway later, with no code change.
2. Read `CONTRACTS.md` sections **C3, C4, C7** and look at `fixtures/story/agent_events_INC-0042.jsonl`. Your output must have the same shape.
3. Ship `agents/replay.py` first (the safety net), then make agents real one by one.

## Your tasks
| ID | Task | Pts | Done when |
|---|---|---|---|
| AG-1 | LLM client (`llm/client.py`, starter done): provider + model from `.env` | 1 | one tool-calling round trip works |
| AG-2 | Redactor (`guard/redactor.py`, starter done) | 3 | no serial/name/tag reaches the LLM |
| AG-3 | RAG over P4's corpus: hybrid search + rerank (`rag/retriever.py`) | 5 | §7.4 is top hit for the story query |
| AG-4 | Orchestrator emitting `AgentEvent`s (`agents/runtime.py`) | 5 | stream matches fixture shape |
| AG-5 | Monitor + Diagnosis agents, confidence score | 4 | ~60% on story data |
| AG-6 | Spare Parts, Scheduler, Energy agents | 4 | results match fixture values |
| AG-7 | Decision agent: `agents/cost_model.py` (starter done) + LLM explanation | 3 | OPT-B €2,485, recommended |
| AG-8 | Replay fallback (`agents/replay.py`, done) + prompt-injection guard | 2 | demo works with LLM unplugged |
| AG-9 | Feedback → similar-case confidence update | 2 | 0.60 → 0.82 shown |

## Rules
- Agents only touch data through tools, and each agent only uses the tools on its allow-list.
- The LLM picks tools and writes explanations; **numbers come from code** (`cost_model.py`).
- `THOUGHT` messages are ≤ 200 characters in plain English. Each agent emits exactly one `RESULT`.
- Write tools (work order, stock transfer) are never called by agents. The backend executes after approval.

## You provide → you use
- **Provide:** C3 agent runtime (to P1)
- **Use:** C4 tools (from P1) · C9 corpus + fixtures (from P4) · C7 redaction rules
