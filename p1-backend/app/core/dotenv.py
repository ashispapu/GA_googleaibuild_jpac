"""Load `.env` into os.environ at startup (no extra dependency).

Looks in the repo root and the current folder. Real environment variables win over the file, so
`$env:GEMINI_API_KEY=...` / `export ...` still override it. Exception: proxy settings (HTTP(S)_PROXY, NO_PROXY,
ALL_PROXY, LLM_PROXY) in .env win over machine-wide ones, so switching networks is just editing .env.
Supports `KEY=value`, `export KEY=value`, quoted values and trailing `# comments` (as in .env.example).
Empty values are skipped.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("plantguard")
ROOT = Path(__file__).resolve().parents[3]
FILE_WINS = {"HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "LLM_PROXY"}


def _parse_line(line: str) -> tuple[str, str] | None:
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    if line.startswith("export "):
        line = line[len("export "):].lstrip()
    key, _, value = line.partition("=")
    key, value = key.strip(), value.strip()
    if not key:
        return None
    if value.startswith("#"):  # `KEY=   # comment` means an empty value
        return key, ""
    if value[:1] in ("'", '"'):
        quote = value[0]
        end = value.find(quote, 1)
        value = value[1:end] if end != -1 else value[1:]
    else:
        # unquoted: drop a trailing " # comment"
        for marker in (" #", "\t#"):
            idx = value.find(marker)
            if idx != -1:
                value = value[:idx]
        value = value.strip()
    return key, value


def load_dotenv(paths: list[Path] | None = None) -> list[Path]:
    """Read each existing file once; never override variables already set. Returns the files loaded."""
    candidates = paths or [ROOT / ".env", Path.cwd() / ".env"]
    loaded, seen = [], set()
    for p in candidates:
        try:
            rp = p.resolve()
        except OSError:
            continue
        if rp in seen or not rp.is_file():
            continue
        seen.add(rp)
        for raw in rp.read_text(encoding="utf-8-sig").splitlines():
            parsed = _parse_line(raw)
            if parsed is None:
                continue
            key, value = parsed
            if value and (key not in os.environ or key.upper() in FILE_WINS):
                os.environ[key] = value
        loaded.append(rp)
    if loaded:
        log.info("loaded settings from %s", ", ".join(str(p) for p in loaded))
    return loaded
