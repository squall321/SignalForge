"""Track E — groq_health_check.py 단위 테스트.

3 케이스 (외부 네트워크 호출 없이 httpx.Client 를 MagicMock 으로 대체):

1. 키 미입력 (env 비움) → evaluate() status='skipped', configured=False.
2. 키 입력 + ping 성공 + backend tier_label 일치 → status='ok'.
3. 키 입력 + ping 성공 + backend tier_label 불일치 → status='fail'.

실행:
    cd crawler && /home/koopark/claude/SignalForge/.venv/bin/python \\
        -m pytest tests/test_groq_health.py -v
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
CRAWLER_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if CRAWLER_ROOT not in sys.path:
    sys.path.insert(0, CRAWLER_ROOT)

from scripts import groq_health_check as mod  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """각 테스트 시작 시 env 3 슬롯 비우기."""
    for k in ("EXTERNAL_API_KEY", "EXTERNAL_BASE_URL", "EXTERNAL_MODEL"):
        monkeypatch.delenv(k, raising=False)
    yield


def test_evaluate_skipped_when_env_missing():
    result = mod.evaluate(backend_url="http://unused")
    assert result["status"] == "skipped"
    assert result["configured"] is False
    # 3개 다 빠져있어야 함
    assert set(result["missing"]) == {
        "EXTERNAL_API_KEY",
        "EXTERNAL_BASE_URL",
        "EXTERNAL_MODEL",
    }


def _mk_post_response(status_code: int, content: str = "pong"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": content}}]
    }
    resp.text = content
    return resp


def _mk_get_response(payload: dict):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def test_evaluate_ok_when_keys_set_and_backend_matches(monkeypatch):
    monkeypatch.setenv("EXTERNAL_API_KEY", "gsk_testkey123456")
    monkeypatch.setenv("EXTERNAL_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setenv("EXTERNAL_MODEL", "llama-3.3-70b-versatile")

    fake_post = _mk_post_response(200, "pong")
    fake_get = _mk_get_response(
        {
            "external": {
                "configured": True,
                "tier_label": "external:llama-3.3-70b-versatile",
                "reachable": True,
                "provider": "openai",
            }
        }
    )

    fake_client_ctx = MagicMock()
    fake_client = MagicMock()
    fake_client.post.return_value = fake_post
    fake_client.get.return_value = fake_get
    fake_client_ctx.__enter__.return_value = fake_client
    fake_client_ctx.__exit__.return_value = False

    with patch.object(mod.httpx, "Client", return_value=fake_client_ctx):
        result = mod.evaluate(backend_url="http://127.0.0.1:8000")

    assert result["status"] == "ok"
    assert result["configured"] is True
    assert result["direct_ping"]["reachable"] is True
    assert result["direct_ping"]["status_code"] == 200
    assert result["backend_status"]["matches_expected"] is True
    assert (
        result["backend_status"]["expected_tier_label"]
        == "external:llama-3.3-70b-versatile"
    )
    # api key 가 redact 되어야 함 (raw 노출 금지)
    assert result["env_redacted"]["EXTERNAL_API_KEY"].endswith("3456")
    assert "gsk_testkey123456" not in str(result["env_redacted"])


def test_evaluate_fail_when_backend_label_mismatch(monkeypatch):
    monkeypatch.setenv("EXTERNAL_API_KEY", "gsk_testkey123456")
    monkeypatch.setenv("EXTERNAL_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setenv("EXTERNAL_MODEL", "llama-3.3-70b-versatile")

    fake_post = _mk_post_response(200, "pong")
    # backend 는 fast tier 로 폴백한 상태로 가정 (external 미설정처럼)
    fake_get = _mk_get_response(
        {
            "external": {
                "configured": False,
                "tier_label": None,
                "reachable": None,
                "provider": None,
            }
        }
    )

    fake_client_ctx = MagicMock()
    fake_client = MagicMock()
    fake_client.post.return_value = fake_post
    fake_client.get.return_value = fake_get
    fake_client_ctx.__enter__.return_value = fake_client
    fake_client_ctx.__exit__.return_value = False

    with patch.object(mod.httpx, "Client", return_value=fake_client_ctx):
        result = mod.evaluate(backend_url="http://127.0.0.1:8000")

    assert result["status"] == "fail"
    assert result["direct_ping"]["reachable"] is True  # 직접 호출은 성공
    assert result["backend_status"]["matches_expected"] is False
