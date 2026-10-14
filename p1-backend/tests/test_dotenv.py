"""The startup .env loader: comments, quotes, empty values, and real env vars winning."""
from pathlib import Path

from app.core.dotenv import _parse_line, load_dotenv

ROOT = Path(__file__).resolve().parents[2]


def test_parse_line_variants():
    assert _parse_line("GEMINI_API_KEY=abc   # read via api_key_env") == ("GEMINI_API_KEY", "abc")
    assert _parse_line("PLANTGUARD_AGENTS_INI=               # optional: path") == ("PLANTGUARD_AGENTS_INI", "")
    assert _parse_line("KEY=#") == ("KEY", "")
    assert _parse_line('export Q="a # not a comment"') == ("Q", "a # not a comment")
    assert _parse_line("X='single'  # c") == ("X", "single")
    assert _parse_line("URL=http://h:8080/a#frag") == ("URL", "http://h:8080/a#frag")
    assert _parse_line("# whole-line comment") is None
    assert _parse_line("   ") is None
    assert _parse_line("no_equals") is None


def test_example_file_sets_no_comment_values(tmp_path, monkeypatch):
    """Copying .env.example verbatim must never turn a comment into a value."""
    env = tmp_path / ".env"
    env.write_text((ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
    for k in ("PLANTGUARD_AGENTS_INI", "GEMINI_API_KEY", "KEYCLOAK_INTERNAL_URL", "AGENT_RUNTIME"):
        monkeypatch.delenv(k, raising=False)
    import os
    monkeypatch.setattr(os, "environ", dict(os.environ))
    load_dotenv([env])
    assert "PLANTGUARD_AGENTS_INI" not in os.environ
    assert "GEMINI_API_KEY" not in os.environ
    assert os.environ["AGENT_RUNTIME"] == "config"
    assert not any(v.lstrip().startswith("#") for v in os.environ.values())


def test_real_env_wins(tmp_path, monkeypatch):
    import os
    env = tmp_path / ".env"
    env.write_text("JWT_SECRET=from-file\nNEW_ONE=x\n", encoding="utf-8")
    monkeypatch.setenv("JWT_SECRET", "from-shell")
    monkeypatch.delenv("NEW_ONE", raising=False)
    load_dotenv([env])
    assert os.environ["JWT_SECRET"] == "from-shell"
    assert os.environ.pop("NEW_ONE") == "x"


def test_proxy_lines_in_file_beat_machine_wide_ones(tmp_path, monkeypatch):
    import os
    env = tmp_path / ".env"
    env.write_text("HTTPS_PROXY=http://127.0.0.1:3128\n# HTTP_PROXY=http://commented.out:1\n", encoding="utf-8")
    monkeypatch.setenv("HTTPS_PROXY", "http://rb-proxy-in.example:8080")
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    load_dotenv([env])
    assert os.environ["HTTPS_PROXY"] == "http://127.0.0.1:3128"
    assert "HTTP_PROXY" not in os.environ
