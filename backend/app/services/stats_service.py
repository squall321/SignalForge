# 코퍼스 전체 통계 REST 서비스 — shared/stats_sql.py 를 MCP 와 공유한다
"""통계 서비스.

SQL 과 결과 가공은 **전부 shared/stats_sql.py 에 있다.** MCP 도구와 같은 모듈을
쓰기 위해서다 — 두 서비스가 각자 복제하면 반드시 갈라진다(전례: search 키워드
매칭이 갈라져 백엔드는 두 컬럼을 보는데 MCP 만 번역본만 봐 한국어 재현율 1.1%).
여기서는 AsyncSession 만 붙인다.
"""
import pathlib
import sys
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

# `shared/stats_sql.py` 를 어디서 찾나 — 컨테이너는 `/shared` 로 마운트하고(up.sh), 호스트에서
# 바로 띄우면 **리포 안**에서 찾아야 한다.
# ⚠ 예전 폴백 `"/app/../shared"` 는 정규화하면 `/shared` 라 **두 항목이 같은 경로**였다(폴백이 아니었다).
# dev 호스트엔 우연히 `/shared` 가 있어 드러나지 않았고, cae00 에서 `ModuleNotFoundError: stats_sql` 로
# MCP 가 아예 안 떴다(2026-09-20 update-all). 박스마다 경로가 다르므로 **절대경로를 박지 않고 파일 위치에서
# 리포 루트를 유도한다.**
_SHARED_PATHS = ("/shared", str(pathlib.Path(__file__).resolve().parents[3] / "shared"))
for _p in _SHARED_PATHS:
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
