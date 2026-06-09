"""
/api/v1/_internal/llm-status endpoint 단위 테스트 (P3.7 + P4.1 트랙 A).

검증:
  - 응답 JSON 에 fast / high / env / shared / prompt_version / last_grounding_score 키 존재
  - P4.1: high 는 cloud 키 0개일 때 fast 와 동일 ollama 로 폴백 → shared=true
  - prompt_version 이 'v3-fewshot-grounded'
  - env.has_* 플래그가 실제 환경 변수와 일치

실행:
    cd backend && .venv/bin/python tests/test_llm_status_endpoint.py
    cd backend && .venv/bin/pytest tests/test_llm_status_endpoint.py -v
"""
import os
import sys
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def test_llm_status_no_cloud_keys_high_is_shared():
    """P4.1: 클라우드 키 모두 없을 때 high.provider 는 fast 와 동일 ollama 서버 공유.

    - shared=true
    - high.provider, high.base_url 모두 None 이 아님 (ollama)
    - prompt_version='v3-fewshot-grounded'
    """
    # TestClient 기본 client host 는 "testclient" — localhost 가드 우회를 위해
    # ASGI 스코프의 client tuple 을 127.0.0.1 로 강제.
    client = TestClient(app, client=("127.0.0.1", 50000))
    with mock.patch.dict(
        os.environ,
        {
            "ANTHROPIC_API_KEY": "",
            "OPENAI_API_KEY": "ollama",
            "OPENAI_BASE_URL": "http://127.0.0.1:11434/v1",
            "OPENAI_MODEL": "qwen2.5:7b",
        },
        clear=False,
    ):
        # ping=false 로 호출 — provider 인스턴스 생성만 확인 (외부 호출 없음)
        resp = client.get("/api/v1/_internal/llm-status?ping=false")
    assert resp.status_code == 200, f"status={resp.status_code} body={resp.text}"
    body = resp.json()
    assert set(body.keys()) >= {
        "fast", "high", "env", "shared", "prompt_version", "last_grounding_score",
    }, body
    # P4.1: high 도 ollama 공유 → shared=true
    assert body["high"]["provider"] == "openai", body["high"]
    assert body["fast"]["provider"] == "openai", body["fast"]
    assert body["shared"] is True, body
    assert body["prompt_version"] == "v3-fewshot-grounded", body
    # last_grounding_score 는 history 가 없을 수도 None, 있을 수도 float.
    assert body["last_grounding_score"] is None or isinstance(
        body["last_grounding_score"], (int, float)
    )
    # env 가 실제 환경을 반영
    assert body["env"]["has_anthropic_key"] is False
    assert body["env"]["has_openai_sk_key"] is False
    print(
        f"[ok] llm-status: shared={body['shared']} prompt_version={body['prompt_version']} "
        f"last_grounding_score={body['last_grounding_score']}"
    )


if __name__ == "__main__":
    test_llm_status_no_cloud_keys_high_is_shared()
    print("\nllm-status endpoint 테스트 통과.")
