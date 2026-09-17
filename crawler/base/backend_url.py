# 백엔드 base URL 과 인증 헤더를 한 곳에서 결정한다 — 포트가 어긋나면 남의 서비스를 때린다.
"""왜 이 모듈이 필요한가.

크롤러·인사이트 계층 8곳이 제각기 백엔드 주소를 기본값으로 들고 있었고, 그 값이
전부 ``http://127.0.0.1:8000`` 이었다. 컨테이너화(2026-07-07)로 백엔드가 :18000
으로 옮겨간 뒤로 전부 빗나갔는데, **:8000 은 비어 있지 않았다** — 사용자의 vLLM
서버가 그 포트를 쓰고 있어서 404 를 정중히 돌려줬다.

그래서 "연결 실패"조차 아니었다. 대시보드 워밍은 15분마다 8건을 vLLM 에 쏘고
warmed=0 failed=8 로 '성공' 했다(2026-09-17 발견). 환경변수 이름이 넷으로
갈려 있던 것(SIGNALFORGE_API / SF_BACKEND_URL / ALERTS_API_URL /
WORKFLOW_VALIDATOR_BACKEND)도 아무도 한 번에 못 고친 이유였다.

기본값을 :18000 으로 맞추고, 인증 헤더(X-API-Key)도 여기서 같이 준다 —
포트만 고치면 401 이라 여전히 0건이다.
"""
import os

DEFAULT_PORT = "18000"


def backend_base(*extra_env: str) -> str:
    """백엔드 base URL. SIGNALFORGE_API 를 정본으로, 옛 이름들을 폴백으로 본다."""
    for name in (*extra_env, "SIGNALFORGE_API", "SF_BACKEND_URL"):
        v = (os.getenv(name) or "").strip()
        if v:
            return v.rstrip("/")
    port = (os.getenv("API_PORT") or DEFAULT_PORT).strip() or DEFAULT_PORT
    return f"http://127.0.0.1:{port}"


def auth_headers() -> dict:
    """API 키 헤더. 키가 없으면 빈 dict — 인증이 꺼진 배포도 있다."""
    key = (os.getenv("API_KEY") or "").strip()
    if not key or key == "change-me":
        return {}
    return {"X-API-Key": key}
