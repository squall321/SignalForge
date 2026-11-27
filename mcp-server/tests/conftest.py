# 테스트에서 DB 연결 풀을 끈다 — 파일마다 asyncio.run 이 새 루프를 만들기 때문이다.
"""왜 필요한가.

db.py 의 _engine 은 모듈 수준이라 커넥션 풀이 **프로세스 전체에 하나**다.
그런데 테스트는 파일마다 asyncio.run() 으로 루프를 새로 만든다. asyncpg 연결은
자기를 만든 루프에 묶여 있어서, test_charts 의 루프 A 에서 풀에 들어간 연결을
test_insights 의 루프 B 가 꺼내 쓰면 터진다 —
    RuntimeError: ... attached to a different loop

혼자 돌리면 통과하고 전체로 돌리면 실패하는 형태라, "원래 하나 빨간 건 무시"로
굳기 딱 좋았다. 그러면 **진짜 실패가 묻힌다.**

NullPool 은 연결을 재사용하지 않는다 — 매번 새로 열고 블록이 끝나면 닫으므로
루프 경계를 넘어가는 연결 자체가 없어진다. 테스트 한정이라 운영 풀은 그대로다.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _repo_env() -> dict:
    """저장소 .env 의 KEY=VALUE — 시험은 셸에서 .env 를 읽지 않고 돈다."""
    env = {}
    path = ROOT.parent / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() and not key.lstrip().startswith("#"):
                env[key.strip()] = value.strip().strip("\"'")
    return env


# DATABASE_URL — 환경 → 저장소 .env(DATABASE_URL, 없으면 POSTGRES_*). 옛 기본 비밀번호를 박아 두지 않는다(2026-11-27 —
# 루프백이 trust 라 틀린 비밀번호로도 붙어 몰랐다). 다 없으면 닿지 않는 주소 — DB 시험만 실패하고 나머지는 돈다.
if not os.environ.get("DATABASE_URL"):
    _env = _repo_env()
    if _env.get("DATABASE_URL"):
        os.environ["DATABASE_URL"] = _env["DATABASE_URL"]
    elif _env.get("POSTGRES_PASSWORD"):
        os.environ["DATABASE_URL"] = (
            f"postgresql+asyncpg://{_env.get('POSTGRES_USER', 'signalforge')}:{_env['POSTGRES_PASSWORD']}"
            f"@127.0.0.1:{_env.get('POSTGRES_PORT', '5434')}/{_env.get('POSTGRES_DB', 'signalforge')}")
    else:
        os.environ["DATABASE_URL"] = "postgresql+asyncpg://no-db-env@127.0.0.1:1/none"

from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool  # noqa: E402

import db  # noqa: E402

db._engine = create_async_engine(db.DATABASE_URL, poolclass=NullPool)
db._AsyncSession = async_sessionmaker(
    db._engine, class_=AsyncSession, expire_on_commit=False
)
