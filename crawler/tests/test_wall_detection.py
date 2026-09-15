# 봇 차단벽에 막힌 실행이 조용한 0건이 아니라 blocked 로 드러나는지 검증한다.
import pathlib
import sys

import httpx
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from base.crawler import BaseCrawler, _looks_walled


def _resp(status=200, body=b"", url="https://example.invalid/"):
    return httpx.Response(status, content=body,
                          request=httpx.Request("GET", url))


class _Stub(BaseCrawler):
    """crawl() 결과와 _update_job_status 를 시험에서 지정한다."""

    def __init__(self, raw=None):
        super().__init__("test")
        self._raw = raw or []
        self.job_states = []

    async def crawl(self):
        return self._raw

    async def _update_job_status(self, status, **kw):
        self.job_states.append((status, kw))

    async def save(self, vocs):
        return len(vocs)


# ── 벽 판정 ────────────────────────────────────────────────────────────
def test_stile_challenge_redirect_is_a_wall():
    r = _resp(200, b"<html>redirecting</html>",
              "https://forums.androidcentral.com/.stile/challenge?rung=nojs")
    assert _looks_walled(r)


def test_cloudflare_body_is_a_wall():
    assert _looks_walled(_resp(200, b"<title>Just a moment...</title>"))


def test_403_is_a_wall():
    assert _looks_walled(_resp(403))


def test_normal_page_is_not_a_wall():
    assert not _looks_walled(_resp(200, b"<html><body>normal forum thread</body></html>"))


def test_large_page_is_not_a_wall():
    """본문이 크면 챌린지 페이지가 아니다 — 긴 글에 'are you a robot' 이
    우연히 섞여도 벽으로 오인하면 안 된다."""
    body = b"x" * 30_000 + b"are you a robot"
    assert not _looks_walled(_resp(200, body))


def test_404_is_not_a_wall():
    """사라진 게시판은 차단이 아니다 — 대응이 다르다."""
    assert not _looks_walled(_resp(404, b"not found"))


# ── run() 의 보고 ──────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_zero_items_with_walls_reports_blocked():
    c = _Stub(raw=[])
    c._wall_total, c._wall_hits = 10, 9
    out = await c.run()
    assert out["status"] == "blocked", "막혔는데 done 으로 보고한다"
    assert "차단" in out["detail"]
    assert c.job_states[-1][0] == "failed"
    assert c.job_states[-1][1]["error_message"].startswith("blocked:")


@pytest.mark.asyncio
async def test_zero_items_without_walls_stays_done():
    """진짜로 할 말이 없었던 경우까지 blocked 로 만들면 안 된다."""
    c = _Stub(raw=[])
    c._wall_total, c._wall_hits = 10, 0
    assert (await c.run())["status"] == "done"


@pytest.mark.asyncio
async def test_collected_items_stay_done_even_with_walls():
    """상세 페이지 일부가 403 이어도 긁힌 게 있으면 소스는 동작한 것이다."""
    from base.crawler import RawVOC
    c = _Stub(raw=[RawVOC(external_id="a", content="x" * 40,
                          source_url="https://e.invalid/a")])
    c._wall_total, c._wall_hits = 10, 9
    assert (await c.run())["status"] == "done"


@pytest.mark.asyncio
async def test_few_requests_do_not_trigger_blocked():
    """요청이 한두 건뿐이면 표본이 부족하다."""
    c = _Stub(raw=[])
    c._wall_total, c._wall_hits = 2, 2
    assert (await c.run())["status"] == "done"
