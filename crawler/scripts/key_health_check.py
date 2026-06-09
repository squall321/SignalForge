"""통합 키 헬스 체크 — Groq (external LLM) + Slack 동시 검증.

Harvest 3 Track E.  ``groq_health_check.py`` 의 Groq 검증 + Slack webhook 형식
검증 + backend ``/api/v1/_internal/key-status`` cross-check 까지 한 스크립트로
묶었다.  운영자는 ``.env`` 한 번 편집 후 본 스크립트만 실행하면 두 통합 키의
활성 여부를 동시에 진단할 수 있다.

키 미입력 환경: graceful skip (exit 0).
키 입력 환경 + 정상: exit 0, status='ok'.
키 입력 환경 + 어느 한 쪽 실패: exit 1, status='partial' 또는 'fail'.

CLI::

    python -m scripts.key_health_check
    python -m scripts.key_health_check --json
    python -m scripts.key_health_check --backend-url http://127.0.0.1:8000
    python -m scripts.key_health_check --skip-groq        # Slack 만 검증
    python -m scripts.key_health_check --skip-slack       # Groq 만 검증

종료 코드::

    0  모두 skipped (graceful) 또는 모두 ok
    1  키는 있는데 검증 실패 (status='partial'|'fail')

본 스크립트는 ``slack_notifier`` 의 dry-run 안전성을 절대 깨지 않는다
(외부 Slack POST 는 안 함; 형식 검증과 backend status 비교만).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("key_health_check")

# repo root .env 자동 로드 (있으면).
_THIS = Path(__file__).resolve()
_CRAWLER_DIR = _THIS.parent.parent
_REPO_ROOT = _CRAWLER_DIR.parent
try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv(_REPO_ROOT / ".env", override=False)
except Exception:  # pragma: no cover
    pass


DEFAULT_BACKEND = "http://127.0.0.1:8000"
PING_PROMPT = "ping"
PING_TIMEOUT_S = 10.0
ENDPOINT_TIMEOUT_S = 15.0


# ── Groq (external LLM) 검증 ─────────────────────────────────────────────
def _groq_env_triplet() -> Dict[str, str]:
    return {
        "EXTERNAL_API_KEY": (os.getenv("EXTERNAL_API_KEY") or "").strip(),
        "EXTERNAL_BASE_URL": (os.getenv("EXTERNAL_BASE_URL") or "").strip(),
        "EXTERNAL_MODEL": (os.getenv("EXTERNAL_MODEL") or "").strip(),
    }


def _groq_configured(triplet: Dict[str, str]) -> bool:
    return all(bool(v) for v in triplet.values())


def _redact(val: str) -> str:
    if not val:
        return ""
    if len(val) <= 8:
        return "***"
    return f"{val[:4]}...{val[-4:]}"


def ping_groq(triplet: Dict[str, str]) -> Dict[str, Any]:
    """OpenAI 호환 ``/chat/completions`` 1회 POST.  외부 호출은 여기 1곳뿐."""
    url = triplet["EXTERNAL_BASE_URL"].rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {triplet['EXTERNAL_API_KEY']}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": triplet["EXTERNAL_MODEL"],
        "messages": [{"role": "user", "content": PING_PROMPT}],
        "max_tokens": 8,
        "temperature": 0,
    }
    try:
        with httpx.Client(timeout=PING_TIMEOUT_S) as client:
            r = client.post(url, headers=headers, json=payload)
        ok = 200 <= r.status_code < 300
        snippet: Optional[str] = None
        if ok:
            try:
                body = r.json()
                choices = body.get("choices") or []
                if choices:
                    snippet = ((choices[0].get("message") or {}).get("content") or "")[:80]
            except Exception:
                snippet = (r.text or "")[:80]
        else:
            snippet = (r.text or "")[:160]
        return {"reachable": ok, "status_code": r.status_code, "snippet": snippet}
    except Exception as e:
        return {"reachable": False, "status_code": None, "snippet": f"exc:{e}"}


# ── Slack 검증 (형식만, 외부 POST 안 함) ──────────────────────────────────
def _slack_env() -> Dict[str, str]:
    """ALERT_WEBHOOK_URL 우선, SLACK_WEBHOOK_URL fallback."""
    raw = (os.getenv("ALERT_WEBHOOK_URL") or "").strip()
    src = "ALERT_WEBHOOK_URL"
    if not raw:
        raw = (os.getenv("SLACK_WEBHOOK_URL") or "").strip()
        src = "SLACK_WEBHOOK_URL" if raw else "none"
    return {
        "url": raw,
        "source": src,
        "provider": (os.getenv("ALERT_PROVIDER") or "slack").strip() or "slack",
        "channel": (os.getenv("SLACK_CHANNEL") or "").strip(),
    }


def evaluate_slack(env: Dict[str, str]) -> Dict[str, Any]:
    """Slack webhook 의 입력 여부 + Slack incoming webhook 형식 검사.

    외부 호출 없음 — dry-run 안전성 보장.  실 송신은
    ``POST /api/v1/alerts/channels/slack/test`` 로 별도 호출.
    """
    url = env["url"]
    configured = bool(url)
    enabled = configured and url.startswith("https://hooks.slack.com/")
    if not configured:
        status = "skipped"
        reason = "ALERT_WEBHOOK_URL / SLACK_WEBHOOK_URL 미입력 (graceful skip)"
    elif not enabled:
        status = "fail"
        reason = "webhook URL 이 https://hooks.slack.com/ 형식이 아님"
    else:
        status = "ok"
        reason = "형식 검증 통과 (실 송신 검증은 /alerts/channels/slack/test 별도)"
    return {
        "status": status,
        "configured": configured,
        "enabled": enabled,
        "dry_run": not enabled,
        "source": env["source"],
        "provider": env["provider"],
        "channel": env["channel"] or None,
        "url_redacted": (f"{url[:30]}...{url[-4:]}" if len(url) > 40 else ("***" if url else None)),
        "reason": reason,
    }


# ── Backend cross-check ──────────────────────────────────────────────────
def fetch_backend_key_status(backend_url: str, ping: bool = True) -> Dict[str, Any]:
    url = backend_url.rstrip("/") + "/api/v1/_internal/key-status"
    params = {"ping": "true"} if ping else {}
    try:
        with httpx.Client(timeout=ENDPOINT_TIMEOUT_S) as client:
            r = client.get(url, params=params)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": str(e)}


# ── 평가 본문 ────────────────────────────────────────────────────────────
def evaluate_groq(triplet: Dict[str, str]) -> Dict[str, Any]:
    configured = _groq_configured(triplet)
    redacted = {
        "EXTERNAL_API_KEY": _redact(triplet["EXTERNAL_API_KEY"]),
        "EXTERNAL_BASE_URL": triplet["EXTERNAL_BASE_URL"] or None,
        "EXTERNAL_MODEL": triplet["EXTERNAL_MODEL"] or None,
    }
    if not configured:
        missing = [k for k, v in triplet.items() if not v]
        return {
            "status": "skipped",
            "configured": False,
            "reason": "external triplet 미입력 (graceful skip)",
            "missing": missing,
            "env_redacted": redacted,
        }
    direct = ping_groq(triplet)
    return {
        "status": "ok" if direct.get("reachable") else "fail",
        "configured": True,
        "env_redacted": redacted,
        "direct_ping": direct,
        "expected_tier_label": f"external:{triplet['EXTERNAL_MODEL']}",
    }


def evaluate(
    backend_url: str = DEFAULT_BACKEND,
    skip_groq: bool = False,
    skip_slack: bool = False,
) -> Dict[str, Any]:
    """Groq + Slack 통합 평가.  backend cross-check 포함."""
    groq_result: Dict[str, Any]
    slack_result: Dict[str, Any]

    if skip_groq:
        groq_result = {"status": "skipped", "reason": "--skip-groq"}
    else:
        groq_result = evaluate_groq(_groq_env_triplet())

    if skip_slack:
        slack_result = {"status": "skipped", "reason": "--skip-slack"}
    else:
        slack_result = evaluate_slack(_slack_env())

    # backend cross-check — ping=true 로 Groq 까지 종합 검증.
    backend = fetch_backend_key_status(backend_url, ping=not skip_groq)
    backend_groq = backend.get("groq") if isinstance(backend, dict) else None
    backend_slack = backend.get("slack") if isinstance(backend, dict) else None
    backend_match_groq: Optional[bool] = None
    backend_match_slack: Optional[bool] = None
    if not skip_groq and isinstance(backend_groq, dict) and groq_result["status"] == "ok":
        backend_match_groq = bool(
            backend_groq.get("configured")
            and backend_groq.get("reachable") is True
            and backend_groq.get("tier_label") == groq_result["expected_tier_label"]
        )
    if not skip_slack and isinstance(backend_slack, dict) and slack_result["status"] == "ok":
        backend_match_slack = bool(backend_slack.get("enabled"))

    # 종합 status.
    statuses = [groq_result["status"], slack_result["status"]]
    if all(s == "skipped" for s in statuses):
        overall = "skipped"
    elif all(s == "ok" for s in statuses):
        overall = "ok"
    elif "fail" in statuses:
        # 둘 다 fail = fail, 한쪽만 fail = partial.
        if statuses.count("fail") == len([s for s in statuses if s != "skipped"]):
            overall = "fail"
        else:
            overall = "partial"
    else:
        # ok + skipped 조합 — partial 도 ok 도 아닌 중립; 운영자는 한 쪽만 활성.
        overall = "partial"

    return {
        "status": overall,
        "groq": groq_result,
        "slack": slack_result,
        "backend_status": {
            "groq": backend_groq,
            "slack": backend_slack,
            "matches_expected_groq": backend_match_groq,
            "matches_expected_slack": backend_match_slack,
            "error": backend.get("error") if isinstance(backend, dict) else None,
        },
    }


# ── CLI ──────────────────────────────────────────────────────────────────
def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Groq + Slack 통합 키 헬스 체크 (Harvest 3 Track E)."
    )
    ap.add_argument("--backend-url", default=DEFAULT_BACKEND)
    ap.add_argument("--json", action="store_true", help="결과를 JSON 으로 출력")
    ap.add_argument("--skip-groq", action="store_true")
    ap.add_argument("--skip-slack", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    result = evaluate(
        backend_url=args.backend_url,
        skip_groq=args.skip_groq,
        skip_slack=args.skip_slack,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        overall = result["status"]
        g = result["groq"]
        s = result["slack"]
        print(f"[overall] {overall}")
        # Groq line
        if g["status"] == "skipped":
            miss = g.get("missing") or g.get("reason", "")
            print(f"  groq:  skip — {miss}")
        elif g["status"] == "ok":
            label = g.get("expected_tier_label", "?")
            sc = g.get("direct_ping", {}).get("status_code")
            print(f"  groq:  ok   — tier_label={label} (HTTP {sc})")
        else:
            sn = g.get("direct_ping", {}).get("snippet")
            print(f"  groq:  fail — {sn}")
        # Slack line
        if s["status"] == "skipped":
            print(f"  slack: skip — {s.get('reason','')}")
        elif s["status"] == "ok":
            ch = s.get("channel") or "(webhook default)"
            print(f"  slack: ok   — {s.get('source')} → {ch} ({s.get('url_redacted')})")
        else:
            print(f"  slack: fail — {s.get('reason','')}")
        # backend cross-check hints
        be = result["backend_status"]
        if be.get("error"):
            print(f"  backend: unreachable — {be['error']}")
        else:
            if be.get("matches_expected_groq") is False:
                print("  backend: groq mismatch — backend 재시작 필요 (settings 캐시)")
            if be.get("matches_expected_slack") is False:
                print("  backend: slack mismatch — backend 재시작 필요 (settings 캐시)")
        print("  guide: docs/dashboard/HARVEST_KEY_SETUP.md")

    # ok / skipped = 0, partial / fail = 1 (운영 CI 가 partial 도 실패로 인식하도록).
    return 0 if result["status"] in ("ok", "skipped") else 1



if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
