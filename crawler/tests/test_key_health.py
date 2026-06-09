"""scripts.key_health_check 단위 테스트 — Groq + Slack 통합 키 검증.

Harvest 3 Track E.  네트워크 비의존 — httpx.Client 와 backend cross-check 를
모두 monkeypatch 한다.  4 경로 검증:

1. 둘 다 미입력 → status='skipped' (graceful) + exit 0.
2. Slack 만 정상, Groq 미입력 → status='partial' (둘 다 ok 가 아니므로).
3. Slack URL 형식 잘못 → status='fail' + exit 1.
4. Slack + Groq 둘 다 정상 + backend 일치 → status='ok' + exit 0.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

# crawler/ 디렉토리 보장
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import key_health_check  # noqa: E402


# ── 가짜 httpx.Client ────────────────────────────────────────────────────
class _FakeResponse:
    def __init__(self, status_code: int, json_payload: Dict[str, Any] | None = None, text: str = ""):
        self.status_code = status_code
        self._json = json_payload
        self.text = text

    def json(self) -> Dict[str, Any]:
        if self._json is None:
            raise ValueError("no json")
        return self._json

    def raise_for_status(self) -> None:
        if not (200 <= self.status_code < 300):
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    """httpx.Client(timeout=..) 가 with 블록에서 .post/.get 만 호출됨."""

    def __init__(self, *, post_response: _FakeResponse | None = None, get_response: _FakeResponse | None = None):
        self.post_response = post_response
        self.get_response = get_response
        self.posts: list[tuple] = []
        self.gets: list[tuple] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, headers=None, json=None):
        self.posts.append((url, headers, json))
        assert self.post_response is not None, "unexpected POST"
        return self.post_response

    def get(self, url, params=None):
        self.gets.append((url, params))
        assert self.get_response is not None, "unexpected GET"
        return self.get_response


def _patch_httpx(monkeypatch, *, post_resp: _FakeResponse | None, get_resp: _FakeResponse | None) -> _FakeClient:
    """httpx.Client(...) 호출을 _FakeClient 로 대체."""
    fc = _FakeClient(post_response=post_resp, get_response=get_resp)

    def _factory(*args, **kwargs):  # timeout=... 등은 무시
        return fc

    monkeypatch.setattr(key_health_check.httpx, "Client", _factory)
    return fc


# ── 테스트 ────────────────────────────────────────────────────────────────
def test_both_missing_returns_skipped(monkeypatch):
    """1) Groq + Slack 모두 미입력 → status='skipped', exit 0."""
    for k in [
        "EXTERNAL_API_KEY", "EXTERNAL_BASE_URL", "EXTERNAL_MODEL",
        "ALERT_WEBHOOK_URL", "SLACK_WEBHOOK_URL", "SLACK_CHANNEL", "ALERT_PROVIDER",
    ]:
        monkeypatch.delenv(k, raising=False)
    # backend 도 미응답 / 또는 backend 없이도 skipped 판정만 보면 됨.
    _patch_httpx(monkeypatch, post_resp=None, get_resp=_FakeResponse(200, {"groq": {}, "slack": {}}))

    result = key_health_check.evaluate(backend_url="http://127.0.0.1:0")
    assert result["status"] == "skipped"
    assert result["groq"]["status"] == "skipped"
    assert result["slack"]["status"] == "skipped"
    assert result["slack"]["configured"] is False


def test_slack_only_ok_partial(monkeypatch):
    """2) Slack 형식 정상 + Groq 미입력 → status='partial', exit 1."""
    for k in ["EXTERNAL_API_KEY", "EXTERNAL_BASE_URL", "EXTERNAL_MODEL"]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://hooks.slack.com/services/T123/B456/abcdEFGHijklMNOP")
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    backend_resp = {
        "groq": {"configured": False, "reachable": None, "tier_label": None},
        "slack": {"configured": True, "enabled": True, "dry_run": False, "source": "ALERT_WEBHOOK_URL"},
    }
    _patch_httpx(monkeypatch, post_resp=None, get_resp=_FakeResponse(200, backend_resp))

    result = key_health_check.evaluate(backend_url="http://127.0.0.1:0")
    assert result["status"] == "partial"
    assert result["slack"]["status"] == "ok"
    assert result["slack"]["enabled"] is True
    assert result["groq"]["status"] == "skipped"
    # backend matches expected slack
    assert result["backend_status"]["matches_expected_slack"] is True


def test_slack_bad_format_fails(monkeypatch):
    """3) Slack URL 이 hooks.slack.com 으로 시작 안 함 → fail, exit 1."""
    for k in ["EXTERNAL_API_KEY", "EXTERNAL_BASE_URL", "EXTERNAL_MODEL"]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://example.com/not/slack")
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    backend_resp = {
        "groq": {"configured": False},
        "slack": {"configured": True, "enabled": False, "dry_run": True},
    }
    _patch_httpx(monkeypatch, post_resp=None, get_resp=_FakeResponse(200, backend_resp))

    result = key_health_check.evaluate(backend_url="http://127.0.0.1:0")
    # Slack fail + Groq skipped → fail 카운트 1, skipped 1 → partial 아니라 'fail'
    # (구현 규칙: fail 카운트가 non-skipped 전부면 fail)
    assert result["status"] == "fail"
    assert result["slack"]["status"] == "fail"


def test_both_ok_full_flow(monkeypatch):
    """4) Groq + Slack 정상 + backend 일치 → status='ok', exit 0."""
    monkeypatch.setenv("EXTERNAL_API_KEY", "gsk_TEST1234567890ABCD")
    monkeypatch.setenv("EXTERNAL_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setenv("EXTERNAL_MODEL", "llama-3.3-70b-versatile")
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://hooks.slack.com/services/T123/B456/abcdEFGHijklMNOP")
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    # Groq ping 200 OK + backend status 일치.
    groq_resp = _FakeResponse(
        200,
        {"choices": [{"message": {"content": "pong"}}]},
    )
    backend_payload = {
        "groq": {
            "configured": True,
            "reachable": True,
            "tier_label": "external:llama-3.3-70b-versatile",
        },
        "slack": {
            "configured": True,
            "enabled": True,
            "dry_run": False,
            "source": "ALERT_WEBHOOK_URL",
        },
    }
    backend_resp = _FakeResponse(200, backend_payload)
    fc = _patch_httpx(monkeypatch, post_resp=groq_resp, get_resp=backend_resp)

    result = key_health_check.evaluate(backend_url="http://127.0.0.1:0")
    assert result["status"] == "ok"
    assert result["groq"]["status"] == "ok"
    assert result["groq"]["direct_ping"]["reachable"] is True
    assert result["groq"]["direct_ping"]["status_code"] == 200
    assert result["slack"]["status"] == "ok"
    assert result["backend_status"]["matches_expected_groq"] is True
    assert result["backend_status"]["matches_expected_slack"] is True
    # POST 1회 + GET 1회.
    assert len(fc.posts) == 1
    assert len(fc.gets) == 1
    # 호출 URL 검증.
    assert fc.posts[0][0].endswith("/chat/completions")
    assert "/api/v1/_internal/key-status" in fc.gets[0][0]


def test_main_exit_codes(monkeypatch, capsys):
    """main() 의 종료 코드 — skipped/ok 는 0, partial/fail 은 1."""
    for k in [
        "EXTERNAL_API_KEY", "EXTERNAL_BASE_URL", "EXTERNAL_MODEL",
        "ALERT_WEBHOOK_URL", "SLACK_WEBHOOK_URL",
    ]:
        monkeypatch.delenv(k, raising=False)
    _patch_httpx(monkeypatch, post_resp=None, get_resp=_FakeResponse(200, {"groq": {}, "slack": {}}))

    rc = key_health_check.main(["--backend-url", "http://127.0.0.1:0"])
    assert rc == 0  # 모두 skipped → 0

    captured = capsys.readouterr()
    assert "overall" in captured.out
    assert "skip" in captured.out
