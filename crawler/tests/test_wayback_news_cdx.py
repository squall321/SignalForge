# Wayback 뉴스 CDX 응답이 목록이 아닐 때 그해 수집 전체가 죽지 않는지 고정한다 (네트워크 없이)
import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from platforms.wayback_news import WaybackNewsCrawler  # noqa: E402


def _crawler(year=2016):
    c = WaybackNewsCrawler.__new__(WaybackNewsCrawler)   # DB·설정 없이 _cdx 만 본다
    c.year = year
    return c


def _client(*payloads):
    resp = []
    for p in payloads:
        r = MagicMock(status_code=200)
        r.json.return_value = p
        resp.append(r)
    client = MagicMock()
    client.get = AsyncMock(side_effect=resp)
    return client


def _run(coro):
    with patch("platforms.wayback_news.asyncio.sleep", new=AsyncMock()):
        return asyncio.run(coro)


def test_cdx_rows_to_timestamps():
    client = _client([["timestamp"], ["20160105000000"], ["20160210000000"]])
    assert _run(_crawler()._cdx(client, "https://example.com/rss")) == ["20160105000000", "20160210000000"]


def test_cdx_object_response_is_retried_then_skipped():
    """200 + 객체 응답 — KeyError 로 그해 전체가 죽던 경우. 재시도 뒤 빈손이어야 한다."""
    client = _client({"error": "x"}, {}, {"error": "y"})
    assert _run(_crawler()._cdx(client, "https://example.com/rss")) == []
    assert client.get.await_count == 3


def test_cdx_object_then_list_recovers():
    client = _client({}, [["timestamp"], ["20160301000000"]])
    assert _run(_crawler()._cdx(client, "https://example.com/rss")) == ["20160301000000"]


def test_cdx_empty_list_and_blank_rows():
    assert _run(_crawler()._cdx(_client([]), "https://example.com/rss")) == []
    assert _run(_crawler()._cdx(_client([["timestamp"], [], ["20160401000000"]]), "u")) == ["20160401000000"]
