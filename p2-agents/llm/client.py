"""LLM client (AG-1). Agents ONLY call `chat()`; nothing else in the code talks to a model provider.
Every prompt goes through the redactor first. Model and provider come from .env:

    LLM_PROVIDER=anthropic          # anthropic | openai | ollama
    LLM_MODEL=claude-sonnet-4-5     # or gpt-4o, llama3.1 ...
    ANTHROPIC_API_KEY=... / OPENAI_API_KEY=... / OLLAMA_URL=http://localhost:11434
"""
import os

from guard.redactor import RegexRedactor

PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")
MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-5")
_redactor = RegexRedactor()


class LLMUnavailable(Exception):
    """Raised on any provider error; the runtime then falls back to agents/replay.py."""


def chat(agent: str, system: str, messages: list[dict], tools: list[dict] | None = None) -> dict:
    """messages: [{"role": "user"|"assistant", "content": str}]
    tools: openai_tool_schema(spec) list from plantguard_contracts.tools
    returns {"text": str, "tool_calls": [{"name": str, "args": dict}], "redaction": RedactionReport}"""
    clean, reports = [], []
    for m in messages:
        text, rep = _redactor.redact(m["content"], agent)
        clean.append({**m, "content": text})
        reports.append(rep)
    try:
        if PROVIDER == "anthropic":
            out = _anthropic(system, clean, tools)
        elif PROVIDER == "openai":
            out = _openai(system, clean, tools)
        elif PROVIDER == "ollama":
            out = _ollama(system, clean)
        else:
            raise LLMUnavailable(f"unknown provider {PROVIDER}")
    except LLMUnavailable:
        raise
    except Exception as e:  # network, auth, rate limit...
        raise LLMUnavailable(str(e)) from e
    return {**out, "redaction": reports}


def _anthropic(system, messages, tools):
    import anthropic  # pip install anthropic
    kw = {"tools": [{"name": t["function"]["name"], "description": t["function"]["description"],
                     "input_schema": t["function"]["parameters"]} for t in tools]} if tools else {}
    r = anthropic.Anthropic().messages.create(model=MODEL, max_tokens=1024, system=system, messages=messages, **kw)
    return {"text": "".join(b.text for b in r.content if b.type == "text"),
            "tool_calls": [{"name": b.name, "args": b.input} for b in r.content if b.type == "tool_use"]}


def _openai(system, messages, tools):
    import json
    from openai import OpenAI  # pip install openai
    r = OpenAI().chat.completions.create(model=MODEL, messages=[{"role": "system", "content": system}, *messages],
                                         **({"tools": tools} if tools else {}))
    msg = r.choices[0].message
    return {"text": msg.content or "",
            "tool_calls": [{"name": c.function.name, "args": json.loads(c.function.arguments)} for c in msg.tool_calls or []]}


def _ollama(system, messages):
    import httpx
    r = httpx.post(f"{os.getenv('OLLAMA_URL', 'http://localhost:11434')}/api/chat", timeout=60,
                   json={"model": MODEL, "stream": False, "messages": [{"role": "system", "content": system}, *messages]})
    r.raise_for_status()
    return {"text": r.json()["message"]["content"], "tool_calls": []}
