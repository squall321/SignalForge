"""
UX R2 트랙 A — keyword-detail 단위 테스트.

실행:
    cd backend && .venv/bin/python tests/test_keyword_detail.py
    cd backend && .venv/bin/pytest tests/test_keyword_detail.py -v
"""
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from sqlalchemy import text  # noqa: E402

from app.database import AsyncSessionLocal  # noqa: E402
from app.schemas.deep import KeywordDetailResponse  # noqa: E402
from app.services.deep_service import DeepService  # noqa: E402


async def _pick_top_keyword(svc: DeepService) -> str | None:
    """anchor 인근 30일 내 가장 빈도 높은 키워드 선정 (실데이터 의존)."""
    from datetime import timedelta as _td
    anchor = await svc._anchor_date()
    d_from = anchor - _td(days=30)
    sql = """
        SELECT vk.keyword AS kw, COUNT(*) AS c
        FROM voc_keywords vk
        JOIN voc_records vr ON vr.id = vk.voc_id
        WHERE vr.published_at::date >= :d_from
          AND vr.published_at::date <= :d_to
        GROUP BY vk.keyword
        ORDER BY c DESC
        LIMIT 1
    """
    row = (await svc.db.execute(text(sql), {"d_from": d_from, "d_to": anchor})).first()
    return str(row.kw) if row and row.kw else None


async def _run_all() -> None:
    async with AsyncSessionLocal() as db:
        svc = DeepService(db)

        # 1) 실데이터 키워드 조회.
        kw = await _pick_top_keyword(svc)
        if not kw:
            # 데이터 없을 때 fallback — 임의 키워드 호출 → empty 200.
            r = await svc.keyword_detail(
                keyword="없는키워드xxxx", lang=None, period_days=7, limit=5
            )
            assert isinstance(r, KeywordDetailResponse)
            assert r.stats.total_count == 0
            assert r.samples == []
            print("[ok] keyword-detail empty fallback passed")
            return

        r = await svc.keyword_detail(
            keyword=kw, lang=None, period_days=7, limit=5
        )
        assert isinstance(r, KeywordDetailResponse)
        assert r.keyword == kw
        assert r.period_days == 7
        assert r.stats.total_count >= 0
        assert -1.0 <= r.stats.sentiment_avg <= 1.0
        assert len(r.samples) <= 5
        for s in r.samples:
            assert isinstance(s.id, int)
            assert len(s.content_preview) <= 200
            if s.sentiment_label is not None:
                assert s.sentiment_label in ("positive", "negative", "neutral")
        for rel in r.related_keywords:
            assert rel.keyword != kw
            assert rel.cooccur_count >= 1
        assert len(r.related_keywords) <= 10
        assert len(r.stats.top_products) <= 3
        assert len(r.stats.top_platforms) <= 3
        # 정렬: 샘플에서 negative 가 positive 보다 앞에 와야 함.
        labels = [s.sentiment_label for s in r.samples]
        neg_idx = [i for i, l in enumerate(labels) if l == "negative"]
        pos_idx = [i for i, l in enumerate(labels) if l == "positive"]
        if neg_idx and pos_idx:
            assert max(neg_idx) < min(pos_idx)
        assert "anchor_date" in r.meta
        print(
            f"[ok] keyword-detail kw={kw} total={r.stats.total_count} "
            f"samples={len(r.samples)} related={len(r.related_keywords)}"
        )


def test_keyword_detail() -> None:
    asyncio.run(_run_all())


if __name__ == "__main__":
    asyncio.run(_run_all())
    print("\nkeyword-detail endpoint passed.")
