# Postgres 비밀번호를 환경에서만 읽는 도우미 — 코드에 박아 둔 기본 비밀번호를 쓰지 않는다
"""왜 필요한가 (2026-11-27).

모듈마다 공개 리포의 옛 기본 비밀번호를 기본값으로 박아 두었다. dev 는 11-14 에 비밀번호를 바꿨지만 루프백이 trust 라
틀린 비밀번호로도 붙어 아무도 몰랐다. scram 으로 바꾸자 PGPASSWORD 가 없는 자리(알림 규칙 평가의 psql)와 옛 값을 박아 둔
백필 스크립트가 옛 값으로 붙으려 했다. 이제는 환경에서만 읽고, 없으면 바로 멈춘다. 크론 백필은 DATABASE_URL 만 넘기므로
거기서도 꺼낸다.
"""
import os
from urllib.parse import unquote, urlsplit

__all__ = ["pg_password", "database_url"]


def pg_password() -> str:
    """PGPASSWORD → POSTGRES_PASSWORD → DATABASE_URL 의 비밀번호. 다 없으면 RuntimeError."""
    for key in ("PGPASSWORD", "POSTGRES_PASSWORD"):
        if os.environ.get(key):
            return os.environ[key]
    password = urlsplit(os.environ.get("DATABASE_URL", "")).password
    if password:
        return unquote(password)
    raise RuntimeError("Postgres 비밀번호가 환경에 없다 — .env 를 읽고(set -a; . ./.env; set +a) 부르거나 DATABASE_URL 을 준다")


def database_url() -> str:
    """DATABASE_URL. 없으면 RuntimeError — 기본 DSN 은 두지 않는다."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL 이 환경에 없다 — .env 를 읽고(set -a; . ./.env; set +a) 부른다")
    return url
