# 연결 자체가 거부되는 소스가 '요청 0건'으로 묻히지 않는지 검증한다.
"""이 테스트가 막는 고장.

_wall_total 증가가 inner 호출 **뒤**에 있을 때, 연결이 통째로 거부되는
사이트(slrclub: 80/443 refused)는 예외가 먼저 빠져나가 카운터를 0 으로 남겼다.

그러면 두 가지가 똑같이 '요청 0' 으로 보인다.
  - 접속이 안 되는 사이트
  - 요청을 시도조차 안 한 크롤러(자격증명 게이트 등)
대응이 완전히 다른데 구분이 안 되니, 6일째 "새 글이 없나 보다"로 넘어갔다.

또 연결 실패는 '벽'과 다른 고장이다. 벽은 UA·쿠키·브라우저로 넘볼 여지가
있지만, 이쪽은 포트가 닫혔거나 도메인이 사라진 것이다. 사유를 갈라 적어야
다음 사람이 헛수고를 안 한다.
"""
import pathlib
import sys

import httpx
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from base.crawler import BaseCrawler, _BudgetTransport  # noqa: E402


class _Crawler(BaseCrawler):
    async def crawl(self):
        return []


class _RefusingTransport(httpx.AsyncBaseTransport):
    """포트가 닫힌 서버를 흉내낸다."""

    async def handle_async_request(self, request):
        raise httpx.ConnectError("[Errno 111] Connection refused", request=request)


class _OkTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request):
        return httpx.Response(200, request=request, content=b"x" * 500)


def _crawler():
    return _Crawler(platform_code="t", product_code=None, job_id=None)


@pytest.mark.asyncio
async def test_connection_refusal_is_counted_as_an_attempt():
    c = _crawler()
    t = _BudgetTransport(c, _RefusingTransport())
    for _ in range(4):
        with pytest.raises(httpx.ConnectError):
            await t.handle_async_request(httpx.Request("GET", "https://x.test/"))
    assert c._wall_total == 4, "연결 실패가 요청 건수로 세어지지 않는다 — '요청 0'으로 묻힌다"
    assert c._conn_fails == 4


@pytest.mark.asyncio
async def test_connection_refusal_produces_a_reason():
    c = _crawler()
    t = _BudgetTransport(c, _RefusingTransport())
    for _ in range(4):
        with pytest.raises(httpx.ConnectError):
            await t.handle_async_request(httpx.Request("GET", "https://x.test/"))
    summary = c._wall_summary()
    assert summary, "연결 실패인데 사유가 None 이다 — 조용히 묻힌다"
    assert "연결 실패" in summary
    assert "Connection refused" in summary, "무엇이 막았는지 남지 않는다"


@pytest.mark.asyncio
async def test_connection_failure_is_distinct_from_a_wall():
    """벽과 연결 실패는 대응이 다르므로 같은 문구로 뭉뚱그리면 안 된다."""
    c = _crawler()
    t = _BudgetTransport(c, _RefusingTransport())
    for _ in range(4):
        with pytest.raises(httpx.ConnectError):
            await t.handle_async_request(httpx.Request("GET", "https://x.test/"))
    assert "차단 응답" not in (c._wall_summary() or "")


@pytest.mark.asyncio
async def test_healthy_source_reports_nothing():
    """정상 소스에 없는 사유를 만들어 내면 진짜 고장이 묻힌다."""
    c = _crawler()
    t = _BudgetTransport(c, _OkTransport())
    for _ in range(6):
        await t.handle_async_request(httpx.Request("GET", "https://x.test/"))
    assert c._conn_fails == 0
    assert c._wall_summary() is None


@pytest.mark.asyncio
async def test_occasional_connection_failure_is_not_a_verdict():
    """한두 건 실패는 소스가 죽은 게 아니다 — 비율이 높을 때만 판정한다."""
    c = _crawler()
    bad = _BudgetTransport(c, _RefusingTransport())
    good = _BudgetTransport(c, _OkTransport())
    with pytest.raises(httpx.ConnectError):
        await bad.handle_async_request(httpx.Request("GET", "https://x.test/"))
    for _ in range(9):
        await good.handle_async_request(httpx.Request("GET", "https://x.test/"))
    assert c._wall_summary() is None, "1/10 실패로 소스를 죽었다고 판정한다"
