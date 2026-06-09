"""MCP 서버용 DB 연결"""
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from contextlib import asynccontextmanager
import os

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://signalforge:signalforge_pass@localhost:5432/signalforge"
)

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
