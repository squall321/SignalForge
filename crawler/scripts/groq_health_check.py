"""Groq (external LLM tier) 헬스 체크 — Track E 자동 검증.

목적:
    EXTERNAL_API_KEY / EXTERNAL_BASE_URL / EXTERNAL_MODEL 3개 슬롯이
    채워져 있는지 검사 → 채워져 있으면 1회 실제 호출로 reachable 확인 →
    backend /api/v1/_internal/llm-status?ping=true 와 cross-check.

키 미입력 환경: graceful skip (exit 0, status='skipped').
키 입력 환경: ping 호출 성공 + tier_label='external:<model>' 확인.

CLI:
    python -m scripts.groq_health_check
    python -m scripts.groq_health_check --json
    python -m scripts.groq_health_check --backend-url http://127.0.0.1:8000

종료 코드:
    0  skipped (키 미입력) 또는 정상
    1  키는 있는데 외부 호출/엔드포인트 검증 실패
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

logger = logging.getLogger("groq_health_check")

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


def _env_triplet() -> Dict[str, str]:
    return {
        "EXTERNAL_API_KEY": os.getenv("EXTERNAL_API_KEY", "").strip(),
        "EXTERNAL_BASE_URL": os.getenv("EXTERNAL_BASE_URL", "").strip(),
        "EXTERNAL_MODEL": os.getenv("EXTERNAL_MODEL", "").strip(),
    }


def _all_configured(triplet: Dict[str, str]) -> bool:
    return all(bool(v) for v in triplet.values())


def _redact(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}...{key[-4:]}"


def ping_external(triplet: Dict[str, str]) -> Dict[str, Any]:
    """OpenAI 호환 /chat/completions 1회 호출 → reachable bool + raw 응답 일부."""
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
                    snippet = (
                        (choices[0].get("message") or {}).get("content") or ""
                    )[:80]
            except Exception:
                snippet = (r.text or "")[:80]
        else:
            snippet = (r.text or "")[:160]
        return {
            "reachable": ok,
            "status_code": r.status_code,
            "snippet": snippet,
        }
    except Exception as e:
        return {"reachable": False, "status_code": None, "snippet": f"exc:{e}"}


def fetch_backend_status(backend_url: str, ping: bool = True) -> Dict[str, Any]:
    url = backend_url.rstrip("/") + "/api/v1/_internal/llm-status"
    params = {"ping": "true"} if ping else {}
    try:
        with httpx.Client(timeout=ENDPOINT_TIMEOUT_S) as client:
            r = client.get(url, params=params)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def evaluate(backend_url: str = DEFAULT_BACKEND) -> Dict[str, Any]:
    triplet = _env_triplet()
    configured = _all_configured(triplet)
    redacted = {
        "EXTERNAL_API_KEY": _redact(triplet["EXTERNAL_API_KEY"]),
        "EXTERNAL_BASE_URL": triplet["EXTERNAL_BASE_URL"] or None,
        "EXTERNAL_MODEL": triplet["EXTERNAL_MODEL"] or None,
    }

    if not configured:
        missing = [k for k, v in triplet.items() if not v]
        return {
            "status": "skipped",
            "reason": "external triplet 미입력 (graceful skip)",
            "missing": missing,
            "configured": False,
            "env_redacted": redacted,
        }

    direct = ping_external(triplet)
    backend = fetch_backend_status(backend_url, ping=True)
    expected_label = f"external:{triplet['EXTERNAL_MODEL']}"
    backend_ext = backend.get("external") if isinstance(backend, dict) else None
    backend_ok = bool(
        isinstance(backend_ext, dict)
        and backend_ext.get("configured")
        and backend_ext.get("tier_label") == expected_label
        and backend_ext.get("reachable") is True
    )

    status = "ok" if (direct.get("reachable") and backend_ok) else "fail"
    return {
        "status": status,
        "configured": True,
        "env_redacted": redacted,
        "direct_ping": direct,
        "backend_status": {
            "external": backend_ext,
            "expected_tier_label": expected_label,
            "matches_expected": backend_ok,
            "error": backend.get("error") if isinstance(backend, dict) else None,
        },
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Groq/external LLM tier 헬스 체크 (Track E)."
    )
    ap.add_argument("--backend-url", default=DEFAULT_BACKEND)
    ap.add_argument("--json", action="store_true", help="결과를 JSON 으로 출력")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    result = evaluate(backend_url=args.backend_url)
    st = result["status"]

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if st == "skipped":
            print(f"[skip] external triplet 미입력 — 누락: {result['missing']}")
            print(f"       (LLM_EXTERNAL_GUIDE.md 참고 후 .env 3 슬롯 채우면 활성)")
        elif st == "ok":
            print(f"[ok] external reachable — tier_label={result['backend_status']['expected_tier_label']}")
            print(f"     direct ping HTTP {result['direct_ping']['status_code']}, snippet={result['direct_ping'].get('snippet')!r}")
        else:
            print("[fail] external 키는 입력됐으나 검증 실패:")
            print(json.dumps(result, ensure_ascii=False, indent=2))

    if st == "fail":
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
