# 코퍼스 전체 통계 REST 서비스 — shared/stats_sql.py 를 MCP 와 공유한다
"""통계 서비스.

SQL 과 결과 가공은 **전부 shared/stats_sql.py 에 있다.** MCP 도구와 같은 모듈을
쓰기 위해서다 — 두 서비스가 각자 복제하면 반드시 갈라진다(전례: search 키워드
매칭이 갈라져 백엔드는 두 컬럼을 보는데 MCP 만 번역본만 봐 한국어 재현율 1.1%).
여기서는 AsyncSession 만 붙인다.
"""
import sys
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

# up.sh 가 /shared 로 마운트한다.
for _p in ("/shared", "/app/../shared"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import stats_sql  # noqa: E402


class StatsService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _execute(self, stmt, params=None):
        return await self.db.execute(stmt, params or {})

    async def corpus_overview(self, days: Optional[int] = None) -> dict:
        return await stats_sql.corpus_overview(self._execute, days)

    async def keyword_stats(self, keyword: str, **kw) -> dict:
        return await stats_sql.keyword_stats(self._execute, keyword, **kw)

    async def period_compare(self, **kw) -> dict:
        return await stats_sql.period_compare(self._execute, **kw)

    async def voc_breakdown(self, **kw) -> dict:
        return await stats_sql.voc_breakdown(self._execute, **kw)
