"""MCP 서버용 DB 연결"""
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from contextlib import asynccontextmanager
import os

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL:  # 기본 DSN 은 두지 않는다 — 공개 리포의 옛 기본 비밀번호였다(2026-11-27)
    raise RuntimeError("DATABASE_URL 이 환경에 없다 — up.sh 가 .env 로 넘긴다(손으로 띄우면 .env 를 읽고)")

_engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
_AsyncSession = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


# @lat: get_db_session — [[mcp-server#DB Connection]] 참조.
@asynccontextmanager
async def get_db_session():
    async with _AsyncSession() as session:
        try:
            yield session
        finally:
            await session.close()
