"""MCP Stats Tools — shared/stats_sql.py 의 얇은 어댑터.

SQL 과 결과 가공은 **전부 shared/stats_sql.py 에 있다.** backend REST 와 같은
모듈을 쓰기 위해서다 — 별도 컨테이너라 각자 복제하면 반드시 갈라진다
(실제 전례: search 키워드 매칭이 갈라져 MCP 만 한국어 재현율 1.1% 였다).
여기서는 DB 세션만 붙인다.
"""
import sys
from typing import Optional

from db import get_db_session

# up.sh 가 /shared 로 마운트한다. 로컬 테스트를 위해 리포 경로도 폴백으로 둔다.
for _p in ("/shared", "/app/../shared"):
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
