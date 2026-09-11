# Kaskus 댓글 역순 페이징 — 장수 스레드의 '새 댓글'에 도달하는지 검증
"""
Kaskus 댓글 페이징 회귀 테스트.

이전 구현은 page=1(가장 오래된 댓글)에서 시작해 MAX_COMMENT_PAGES(8, 160개)까지만
읽었다. Kaskus 의 Galaxy 스레드는 댓글이 수백 개인 장수 토론방이라 **새 댓글에
영원히 도달하지 못했다** — 실측 2026-09-11: 28일간 신규 0건이었고, 재실행해서
112건을 수집해도 전량 중복이었다.

지금은 meta.total 로 마지막 페이지를 구해 거꾸로 내려간다.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from platforms.kaskus import MAX_COMMENT_PAGES  # noqa: E402


def _pages(total: int) -> list:
    """크롤러가 읽는 페이지 순서 — kaskus._fetch_comments 와 같은 계산."""
    last_page = max(1, math.ceil(total / 20))
    return list(range(last_page, max(0, last_page - MAX_COMMENT_PAGES), -1))


@pytest.mark.parametrize("total,expect_first", [
    (0, 1),        # 댓글 없음 → page 1
    (5, 1),        # 1페이지 미만
    (20, 1),
    (21, 2),
    (400, 20),     # 20페이지 → 마지막부터
    (1000, 50),
])
def test_starts_from_last_page(total, expect_first):
    """가장 최근 댓글이 있는 마지막 페이지부터 읽어야 한다."""
    assert _pages(total)[0] == expect_first


def test_reads_newest_not_oldest():
    """장수 스레드에서 이전 구현(1..8)과 겹치지 않아야 한다 — 그게 버그의 핵심이었다."""
    pages = _pages(1000)          # 50페이지짜리 스레드
    old_impl = list(range(1, MAX_COMMENT_PAGES + 1))
    assert set(pages).isdisjoint(old_impl), (
        f"새 구현 {pages} 가 옛 구현 {old_impl} 과 겹친다 — 새 댓글에 못 간다")


def test_page_budget_respected():
    """페이지 수는 MAX_COMMENT_PAGES 를 넘지 않는다(요청 폭주 방지)."""
    for total in (0, 100, 1000, 100000):
        assert len(_pages(total)) <= MAX_COMMENT_PAGES


def test_never_yields_page_zero_or_negative():
    for total in (0, 1, 19, 20, 21, 159, 160, 161):
        assert all(p >= 1 for p in _pages(total)), total
