"""
pytest conftest — backend tests.

문제
----
backend/tests/ 의 각 테스트는 ``asyncio.run(_run_all())`` 패턴이라
테스트 함수마다 새 event loop 가 생성된다. 그런데 ``app.database.engine`` 은
모듈 import 시점에 1회만 만들어지고 asyncpg connection 은 첫 호출 loop 에
영구 바인딩된다 → 두 번째 테스트의 새 loop 에서 동일 connection 을 재사용하면
"Task ... attached to a different loop" / "Connection._cancel was never awaited"
충돌이 발생한다 (격리 실행은 통과, 일괄 실행 시 5개 fail).

해결
----
autouse fixture 가 매 테스트 종료마다 engine.dispose() 호출 →
다음 테스트가 새 connection pool 을 만들어 자기 loop 에 바인딩.
운영 코드 (app/database.py) 는 건드리지 않는다.
"""
import asyncio
import pytest

from app.database import engine


@pytest.fixture(autouse=True)
def _dispose_async_engine_each_test():
    """
    각 테스트 종료 시 asyncpg connection pool 을 비운다.
    다음 테스트의 새 event loop 에서 connection 이 재초기화되므로
    cross-loop 충돌을 차단한다.
    """
    yield
    # 동기 컨텍스트에서 호출되므로 새 loop 로 dispose 를 await 한다.
    try:
        asyncio.run(engine.dispose())
    except RuntimeError:
        # 이미 닫혔거나 다른 loop 가 실행 중이면 무시.
        pass
