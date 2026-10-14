"""Check the LLM setup with ONE tiny request and print exactly what the provider says.

    python -m llm.check            (from p2-agents, or PYTHONPATH=.:p2-agents python -m llm.check from the repo root)

Uses the same .ini, .env, proxy and model as the agents. Costs one request of your quota."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_env() -> None:
    for p in (ROOT, ROOT / "p1-backend", ROOT / "p2-agents"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    try:
        from app.core.dotenv import load_dotenv  # same loader as the backend
        load_dotenv([ROOT / ".env"])
    except Exception:
        pass


HINTS = {
    "API_KEY_SERVICE_BLOCKED": "The key's API restrictions don't include the Gemini API. In Google Cloud console > APIs & "
                               "Services > Credentials > your key: under 'API restrictions' add 'Generative Language API' "
                               "(or choose 'Don't restrict key').",
    "SERVICE_DISABLED": "Enable 'Generative Language API' in that key's Google Cloud project (APIs & Services > Library), "
                        "wait a minute, then try again.",
    "API_KEY_INVALID": "The key is wrong or deleted. Copy it again into .env (GEMINI_API_KEY=...).",
    "RESOURCE_EXHAUSTED": "Quota used up. Free tier: 20 requests/day for gemini-2.5-flash. Use a key from a project with "
                          "billing, another model, or Vertex AI.",
    "407": "The proxy wants credentials, or is the wrong one. Check HTTPS_PROXY / LLM_PROXY in .env.",
    "No module named 'google'": "Install the Google sign-in package in this venv: pip install google-auth "
                                "(or pip install -r requirements.txt from the project root).",
    "DefaultCredentialsError": "No Google credentials for Vertex AI. Run `gcloud auth application-default login` once "
                               "(or set GOOGLE_APPLICATION_CREDENTIALS to a service-account JSON file).",
    "default credentials": "No Google credentials for Vertex AI. Run `gcloud auth application-default login` once "
                           "(or set GOOGLE_APPLICATION_CREDENTIALS to a service-account JSON file).",
    "aiplatform.googleapis.com": "Enable the Vertex AI API in the project, and give your account the 'Vertex AI User' role.",
    "PERMISSION_DENIED": "Your Google account lacks access: it needs the 'Vertex AI User' role in the project, and the "
                         "Vertex AI API must be enabled.",
}


async def main() -> int:
    _load_env()
    from harness.config import load_config
    from llm.client import LLMClient, _short
    from llm.network import apply_proxy
    cfg = load_config().llm
    model, vkw = cfg.provider_model()
    print(f"model    : {model}")
    if cfg.uses_vertex():
        print(f"vertex   : project={vkw.get('vertex_project') or cfg.extra_params.get('vertex_project') or 'NOT SET'} "
              f"location={vkw.get('vertex_location') or cfg.extra_params.get('vertex_location')}")
        print("auth     : Google credentials (gcloud auth application-default login, or GOOGLE_APPLICATION_CREDENTIALS)")
    else:
        print(f"key      : {cfg.api_key_env or '(none)'} = "
              f"{'set (' + str(len(cfg.api_key() or '')) + ' chars)' if cfg.api_key() else 'NOT SET'}")
    print(f"network  : {apply_proxy(cfg.proxy)}")
    try:
        out = await LLMClient().chat("check", "Reply with the single word OK.", [{"role": "user", "content": "ping"}],
                                     None, cfg)
        print(f"result   : OK - the model answered {out['text'][:40]!r}")
        return 0
    except Exception as e:
        text = str(e.__cause__ or e)
        print(f"result   : FAILED - {_short(text)}")
        hint = next((h for k, h in HINTS.items() if k in text), None)
        if hint:
            print(f"fix      : {hint}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
