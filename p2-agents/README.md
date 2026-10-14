# P2 — Agents + RAG

**Your job:** the "thinking" part. Seven agents investigate the alert, show their reasoning and evidence (streamed
live), and produce costed options. They never act without approval.

## Run it

```bash
pip install -r requirements.txt                 # includes litellm
export GEMINI_API_KEY=...                       # without it the backend streams the recorded run
make run-p1                                     # real backend + live agents on :8000
make test-p2                                    # agent-layer tests (offline, no key needed)
```

Change the model, thinking, flows, tools, limits or any business number in **`config/agents.ini`** — nothing is
hard-coded. Point to another file with `PLANTGUARD_AGENTS_INI=/path/agents.ini`; force the recording with
`AGENT_RUNTIME=replay`.

## Layout

```
p2-agents/
  config/agents.ini        central config: [runtime] [streaming] [llm] [tools] [flow.*] [agent.*] [policy.*] [option.*]
  harness/                 scaffolding shared by every agent
    config.py              .ini loader + validation (allow-list, write tools, flows, sub-agents, hand-offs)
    base_agent.py          BaseAgent.run(ctx, task, tools): streamed tool loop, required data, finalize -> RESULT
    events.py              EventEmitter: seq, clipping, redaction restore, delta streams (thought/message)
    runtime.py             PlantAgentRuntime (C3): flows per trigger, hand-off queue, run budget, failure policy
    context.py             RunContext: blackboard (facts, results), tool log, gateway
    registry.py            finds agents/<name>/agent.py classes
    factory.py             build_runtime() / live_runtime_from_config() for the backend
    prompts.py             {{var}} rendering of the markdown prompts
    memory.py              case memory (feedback, AG-9)
  llm/
    client.py              LLMClient: redaction of every prompt (C7), one RedactionReport per call, streaming
    providers/litellm_provider.py   LiteLLM streaming adapter (Gemini default; reasoning_content -> thinking)
    providers/scripted.py  offline scripted model (tests, rehearsals)
  tools/
    base.py                Tool / ToolOutcome / Handoff / Finish
    registry.py            Toolbox: builds each agent's tool list from the .ini (passed into run())
    plant/                 one PlantTool per contract read tool; presenters per domain (ml, ot, erp, mes, ...)
    agent/                 ask_<agent> (sub-agent), handoff_task (divert), submit_result (structured wording)
  domain/                  deterministic planning maths (part sources, repair window, energy, options)
  agents/<name>/           one folder per agent: agent.py, agent.ini, system_prompt.md, task*.md
    orchestrator monitor diagnosis spare_parts scheduler energy decision manual_researcher (helper)
  tests/                   pytest suite (see below)
```

## How a run works

1. The backend calls `run(incident)`; the runtime picks `[flow.<trigger>]`, e.g.
   `orchestrator:plan, monitor, diagnosis, spare_parts, scheduler, energy, decision, orchestrator:summary`.
2. For each step the runtime creates the agent from its folder, renders its task template and **passes the tools
   as an argument**: `agent.run(ctx, task, tools=toolbox.for_agent(agent, mode))`.
3. Each LLM turn streams: reasoning → `thought_delta`, text → `message_delta`; each stream closes with a `THOUGHT`
   (≤ 200 chars) or `MESSAGE`. Tool calls go through the ToolGateway (allow-list, validation, audit) with
   `TOOL_CALL` / `TOOL_RESULT` / `EVIDENCE` events.
4. If the model skipped data the result needs (`required_tools`), code fetches it. `finalize()` builds the typed
   RESULT **in code** (C3: numbers never come from the LLM). Shipped values reproduce C9 exactly.

### Sub-agent vs hand-off
| | `ask_<agent>` (sub-agent) | `handoff_task` (divert) |
|---|---|---|
| Who answers | the caller, after the sub-agent reports back | the target agent, directly |
| Caller | keeps working | stops, says "This is now with X" |
| Events | sub-agent's own STATUS/tool events (helpers carry `sub_agent`) | `HANDOFF` (args `to`, `task`), then the target runs |
| Configure | `sub_agents = ...` | `handoff_targets = ...` |

Helpers (`principal` ≠ folder name, e.g. `manual_researcher` → `diagnosis`) use their principal's allow-list.

### Adding an agent
Create `agents/<name>/` with `agent.py` (subclass `harness.base_agent.BaseAgent`, or `Agent` for a plain one),
`agent.ini`, `system_prompt.md`; then reference it in a flow, `sub_agents` or `handoff_targets`. Nothing else changes.

### Retries, rate limits, proxy
Model calls that fail with a rate limit (429), overload/5xx, a timeout or a dropped connection are retried with
**exponential backoff** (`[llm] retry_max_attempts / retry_base_s / retry_max_s`: 2 s, 4 s, 8 s ... ±25 % jitter, never
shorter than the server's "retry in N s"). Each wait is shown in the UI as a "Waiting" update. Retries happen only
before anything was streamed (no duplicated text). Bad keys, bad requests and a used-up daily quota fail at once.
`[llm] requests_per_minute` paces calls client-side (Gemini free tier: 5). Proxy: `[llm] proxy`, `LLM_PROXY` or
`HTTP(S)_PROXY` in `.env` (see `llm/network.py`).

### LLM failure
`[runtime] on_llm_failure = raise` (default): the backend's FallbackAgentRuntime emits `LLM_UNAVAILABLE` and replays
the recording. `degrade`: one ERROR event, then every agent finishes rule-based with the same numbers.

## Tests (`make test-p2`)
| File | Covers |
|---|---|
| `test_story_flow.py` | full ml_alert flow on the mock API: C3 event contract, C9 golden numbers, equality with the recording, delta streams rebuild closing events, sub-agent evidence, redaction (no names reach the model; restored for UI), lazy model |
| `test_handoff_and_subagents.py` | divert vs sub-agent in the question flow, hand-off limit, per-mode tools |
| `test_failures.py` | raise / degrade policies, run budget, bad tool args fed back to the model |
| `test_retry.py` | backoff classification (real Gemini 429), delays + server hint, give-up, no retry after partial output, pacing, full run surviving 429s |
| `test_network.py` | proxy switch (env / LLM_PROXY / none), real LiteLLM call through a test proxy |
| `test_llm_litellm.py` | Gemini kwargs, LiteLLM stream parsing (reasoning, fragmented tool calls), redaction + restore, errors, real `litellm` mock stream |
| `test_config_and_tools.py` | .ini validation errors, overrides, new agent = new folder, tool schemas, presenters, emitter |
| `test_backend_integration.py` | live runtime inside P1's backend: WS stream, catch-up, options, approval executes, audit, redactions, follow-up question thread, fallback, no-key replay |

## Rules (unchanged)
- Agents only touch data through tools, each only on its allow-list. Write tools are never available to agents.
- The LLM picks tools and writes explanations; numbers come from code (`domain/`, `agents/cost_model.py`).
- `THOUGHT` ≤ 200 characters, plain English. Each pipeline agent emits exactly one `RESULT`.
