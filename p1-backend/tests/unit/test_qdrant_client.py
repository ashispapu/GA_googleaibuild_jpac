"""Infrastructure connections must not inherit the office proxy used for outbound LLM calls."""
import os

import pytest

from app.rag.qdrant_store import make_client


@pytest.mark.parametrize("url,ca", [("http://qdrant:6333", None),
                                   ("https://qdrant.plantguard.internal:6333", "/certs/qdrant-ca.pem")])
def test_remote_client_bypasses_proxy_and_keeps_tls_settings(monkeypatch, tmp_path, url, ca):
    import qdrant_client

    captured = {}
    sentinel = object()

    def fake_client(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(qdrant_client, "QdrantClient", fake_client)
    monkeypatch.setenv("QDRANT_URL", url)
    monkeypatch.setenv("QDRANT_API_KEY", "test-qdrant-key")
    monkeypatch.setenv("HTTP_PROXY", "http://corporate-proxy:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://corporate-proxy:3128")
    if ca:
        monkeypatch.setenv("QDRANT_CA_CERT", ca)
    else:
        monkeypatch.delenv("QDRANT_CA_CERT", raising=False)

    assert make_client(tmp_path) is sentinel
    assert captured["url"] == url
    assert captured["api_key"] == "test-qdrant-key"
    assert captured["trust_env"] is False
    assert captured["https"] is url.startswith("https")
    if ca:
        assert captured["verify"] == ca
    else:
        assert "verify" not in captured
    # Do not remove the proxy settings needed by Gemini or other outbound calls.
    assert os.environ["HTTPS_PROXY"] == "http://corporate-proxy:3128"


def test_embedded_client_still_uses_local_storage(monkeypatch, tmp_path):
    import qdrant_client

    captured = {}
    sentinel = object()

    def fake_client(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(qdrant_client, "QdrantClient", fake_client)
    monkeypatch.delenv("QDRANT_URL", raising=False)

    assert make_client(tmp_path) is sentinel
    assert captured == {"path": str(tmp_path / "qdrant")}
    assert (tmp_path / "qdrant").is_dir()
