"""Prompt files live in each agent's folder (system_prompt.md, task*.md). Placeholders use {{name}} so JSON examples
with single braces in the markdown stay untouched. Unknown placeholders are left as-is (and logged once)."""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

log = logging.getLogger("plantguard.prompts")
_VAR = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


@lru_cache(maxsize=256)
def _read(path: str, mtime: float) -> str:
    return Path(path).read_text(encoding="utf-8")


def load(path: Path) -> str:
    """Read a prompt file (cached, re-read automatically when the file changes)."""
    return _read(str(path), path.stat().st_mtime)


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, default=str, separators=(",", ":"))
    return str(v)


def render(text: str, variables: dict[str, Any]) -> str:
    def sub(m):
        key = m.group(1)
        cur: Any = variables
        for part in key.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                log.debug("prompt placeholder {{%s}} has no value", key)
                return m.group(0)
        return _fmt(cur)
    return _VAR.sub(sub, text)
