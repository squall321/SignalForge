# 백엔드 주소 기본값이 :8000(남의 vLLM)으로 되돌아가지 않도록 고정한다.
"""이 테스트가 막는 고장.

크롤러·인사이트 8곳이 백엔드 주소를 제각기 ``http://127.0.0.1:8000`` 으로
기본값을 들고 있었다. 컨테이너화로 백엔드가 :18000 으로 옮겨간 뒤 전부 빗나갔는데,
**:8000 이 비어 있지 않았던 게 핵심이다** — 이 장비에서는 vLLM 이 그 포트를 쓴다.
그래서 연결 실패조차 아니고 404 가 정중히 돌아왔고, 대시보드 워밍은 15분마다
8건을 남의 서비스에 쏘면서 warmed=0 failed=8 로 '성공'했다(2026-09-17 발견).

포트만 고치면 401 이라 여전히 0건이다 — 인증 헤더까지 같이 줘야 한다.
"""
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from base.backend_url import auth_headers, backend_base  # noqa: E402

ENV_NAMES = ("SIGNALFORGE_API", "SF_BACKEND_URL", "WORKFLOW_VALIDATOR_BACKEND",
             "API_PORT", "API_KEY")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for n in ENV_NAMES:
        monkeypatch.delenv(n, raising=False)


def test_default_is_not_port_8000():
    """:8000 은 이 장비에서 vLLM 이 쓴다 — 기본값으로 쓰면 남의 서비스를 때린다."""
    base = backend_base()
    assert ":8000" not in base, f"기본값이 다시 :8000 이다 — {base}"
    assert ":18000" in base, base


def test_api_port_env_is_respected(monkeypatch):
    """배포마다 포트가 다를 수 있다 — .env 의 API_PORT 를 따라야 한다."""
    monkeypatch.setenv("API_PORT", "19999")
    assert backend_base() == "http://127.0.0.1:19999"


def test_explicit_url_wins(monkeypatch):
    monkeypatch.setenv("SIGNALFORGE_API", "http://backend.internal:9000/")
    assert backend_base() == "http://backend.internal:9000", "후행 슬래시를 안 떼면 //api 가 된다"


def test_legacy_env_names_still_work(monkeypatch):
    """옛 이름으로 설정해 둔 배포를 깨뜨리지 않는다."""
    monkeypatch.setenv("SF_BACKEND_URL", "http://old:8080")
    assert backend_base() == "http://old:8080"
    monkeypatch.setenv("WORKFLOW_VALIDATOR_BACKEND", "http://wf:7070")
    assert backend_base("WORKFLOW_VALIDATOR_BACKEND") == "http://wf:7070", \
        "우선 확인할 이름을 넘겨도 무시된다"


def test_auth_header_is_sent_when_key_exists(monkeypatch):
    """포트만 고치면 401 이다 — 키가 있으면 헤더를 줘야 200 이 된다."""
    monkeypatch.setenv("API_KEY", "s3cret")
    assert auth_headers() == {"X-API-Key": "s3cret"}


def test_placeholder_key_is_not_sent(monkeypatch):
    """change-me 는 백엔드가 인증 비활성으로 취급한다 — 보내면 오히려 혼란스럽다."""
    monkeypatch.setenv("API_KEY", "change-me")
    assert auth_headers() == {}
    monkeypatch.setenv("API_KEY", "   ")
    assert auth_headers() == {}


def test_no_module_hardcodes_port_8000():
    """한 곳만 고치면 나머지 7곳이 남는다 — 전수로 막는다."""
    offenders = []
    for py in list((ROOT / "insight").glob("*.py")) + \
              list((ROOT / "scripts").glob("*.py")) + [ROOT / "tasks.py"]:
        for i, line in enumerate(py.read_text().splitlines(), 1):
            if line.lstrip().startswith(("#", '"', "'")):
                continue          # 주석·문서 문자열의 설명은 봐준다
            if "127.0.0.1:8000" in line or "localhost:8000" in line:
                offenders.append(f"{py.name}:{i}  {line.strip()[:70]}")
    assert not offenders, "백엔드 주소를 :8000 으로 하드코딩한 곳:\n  " + "\n  ".join(offenders)


def test_warm_task_sends_the_auth_header():
    """헤더 없이 urlopen 하면 401 이라 warmed=0 이 된다."""
    src = (ROOT / "tasks.py").read_text()
    body = src.split("def warm_dashboard_cache", 1)[1].split("\n@app.task", 1)[0]
    assert "auth_headers()" in body, "워밍이 인증 헤더를 만들지 않는다"
    assert "urllib.request.Request(url, headers=" in body, "헤더를 요청에 싣지 않는다"


def test_every_backend_caller_sends_auth():
    """backend_base() 를 쓰는 모듈은 auth_headers() 도 보내야 한다.

    포트만 고치면 401 이다. edf7507 이 헬퍼를 만들고 tasks.py 한 곳만 연결해,
    인사이트·헬스체크 9개 모듈이 계속 401 을 받고 있었다(2026-09-26 발견) —
    엔드포인트는 멀쩡하고 호출자가 문 앞에서 돌아선 상태다.
    """
    offenders = []
    for py in list((ROOT / "insight").glob("*.py")) + list((ROOT / "scripts").glob("*.py")) + [ROOT / "tasks.py"]:
        src = py.read_text()
        if "backend_base" not in src:
            continue
        if "auth_headers" not in src:
            offenders.append(py.name)
    assert not offenders, (
        "backend_base 는 쓰지만 auth_headers 를 안 보내는 모듈(전부 401 을 받는다): "
        + ", ".join(sorted(offenders)))
