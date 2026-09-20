"""MCP Stats Tools — shared/stats_sql.py 의 얇은 어댑터.

SQL 과 결과 가공은 **전부 shared/stats_sql.py 에 있다.** backend REST 와 같은
모듈을 쓰기 위해서다 — 별도 컨테이너라 각자 복제하면 반드시 갈라진다
(실제 전례: search 키워드 매칭이 갈라져 MCP 만 한국어 재현율 1.1% 였다).
여기서는 DB 세션만 붙인다.
"""
import pathlib
import sys
from typing import Optional

from db import get_db_session

# `shared/stats_sql.py` 를 어디서 찾나 — 컨테이너는 `/shared` 로 마운트하고(up.sh), 호스트에서
# 바로 띄우면 **리포 안**에서 찾아야 한다.
# ⚠ 예전 폴백 `"/app/../shared"` 는 정규화하면 `/shared` 라 **두 항목이 같은 경로**였다(폴백이 아니었다).
# dev 호스트엔 우연히 `/shared` 가 있어 드러나지 않았고, cae00 에서 `ModuleNotFoundError: stats_sql` 로
# MCP 가 아예 안 떴다(2026-09-20 update-all). 박스마다 경로가 다르므로 **절대경로를 박지 않고 파일 위치에서
# 리포 루트를 유도한다.**
_SHARED_PATHS = ("/shared", str(pathlib.Path(__file__).resolve().parents[2] / "shared"))
for _p in _SHARED_PATHS:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import stats_sql  # noqa: E402


async def _run(fn, *args, **kw):
    async with get_db_session() as db:
        async def execute(stmt, params=None):
            return await db.execute(stmt, params or {})
        return await fn(execute, *args, **kw)


async def corpus_overview_tool(days: Optional[int] = None) -> dict:
    return await _run(stats_sql.corpus_overview, days)


async def voc_breakdown_tool(**kw) -> dict:
    return await _run(stats_sql.voc_breakdown, **kw)


async def keyword_stats_tool(keyword: str, **kw) -> dict:
    return await _run(stats_sql.keyword_stats, keyword, **kw)


async def period_compare_tool(**kw) -> dict:
    return await _run(stats_sql.period_compare, **kw)
