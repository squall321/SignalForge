# 봇 차단벽에 막힌 실행이 조용한 0건이 아니라 blocked 로 드러나는지 검증한다.
import pathlib
import sys

import httpx
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from base.crawler import BaseCrawler


def _resp(status=200, body=b"", url="https://example.invalid/"):
    return httpx.Response(status, content=body,
                          request=httpx.Request("GET", url))


def _walled(status=200, body=b"", url="https://example.invalid/"):
    """transport 가 부르는 방식 그대로 — url/body 를 밖에서 넘긴다."""
    from base.crawler import _looks_walled as f
    return f(_resp(status, body, url), url=url, body=body)


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
    assert _walled(200, b"<html>redirecting</html>",
                   "https://forums.androidcentral.com/.stile/challenge?rung=nojs")


def test_cloudflare_body_is_a_wall():
    assert _walled(200, b"<title>Just a moment...</title>")


def test_403_is_a_wall():
    assert _walled(403)


def test_normal_page_is_not_a_wall():
    assert not _walled(200, b"<html><body>normal forum thread</body></html>")


def test_large_page_is_not_a_wall():
    """본문이 크면 챌린지 페이지가 아니다 — 긴 글에 'are you a robot' 이
    우연히 섞여도 벽으로 오인하면 안 된다."""
    body = b"x" * 30_000 + b"are you a robot"
    assert not _walled(200, body)


def test_404_is_not_a_wall():
    """사라진 게시판은 차단이 아니다 — 대응이 다르다."""
    assert not _walled(404, b"not found")


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


# ── transport 실제 경로 ─────────────────────────────────────────────────
# 앞의 시험들은 content= 로 만든 Response 라 .content 가 이미 읽혀 있었다.
# 실제 transport 응답은 읽기 전이라 .content 가 ResponseNotRead 로 터진다 —
# 그걸 try 로 삼키는 바람에 본문 판정이 조용히 죽어 있었다(실측 벽 0건).
# 그래서 진짜 transport 를 통과시켜 본다.
class _FakeInner(httpx.AsyncBaseTransport):
    """본문을 스트림으로만 주는 전송 — 실제 네트워크 전송과 같은 상태."""

    def __init__(self, status=200, body=b"", headers=None):
        self.status, self.body, self.headers = status, body, headers or {}

    async def handle_async_request(self, request):
        async def stream():
            yield self.body
        return httpx.Response(self.status, headers=self.headers,
                              stream=_Gen(stream()), request=request)


class _Gen(httpx.AsyncByteStream):
    def __init__(self, agen):
        self._agen = agen

    async def __aiter__(self):
        async for chunk in self._agen:
            yield chunk


@pytest.mark.asyncio
async def test_transport_detects_wall_in_streamed_body():
    from base.crawler import _BudgetTransport
    c = _Stub()
    c.CRAWL_BUDGET_SEC = 9999
    inner = _FakeInner(200, b"<title>Just a moment...</title>")
    async with httpx.AsyncClient(transport=_BudgetTransport(c, inner)) as cl:
        r = await cl.get("https://example.invalid/")
    assert r.status_code == 200
    assert c._wall_hits == 1, "스트림 본문에서 벽을 못 잡았다"
    # 본문이 호출자에게 온전히 전달돼야 한다
    assert b"Just a moment" in r.content


@pytest.mark.asyncio
async def test_transport_passes_normal_body_through():
    from base.crawler import _BudgetTransport
    c = _Stub()
    c.CRAWL_BUDGET_SEC = 9999
    payload = b"<html><body>" + b"real content " * 100 + b"</body></html>"
    inner = _FakeInner(200, payload)
    async with httpx.AsyncClient(transport=_BudgetTransport(c, inner)) as cl:
        r = await cl.get("https://example.invalid/")
    assert r.content == payload, "본문이 변형됐다"
    assert c._wall_hits == 0


@pytest.mark.asyncio
async def test_transport_handles_gzip_without_double_decode():
    """aread() 가 이미 압축을 풀었는데 Content-Encoding 을 그대로 붙이면
    httpx 가 한 번 더 풀려다 DecodingError 를 낸다."""
    import gzip
    from base.crawler import _BudgetTransport
    raw = b"<html>normal page</html>"
    inner = _FakeInner(200, gzip.compress(raw),
                       headers={"content-encoding": "gzip"})
    c = _Stub()
    c.CRAWL_BUDGET_SEC = 9999
    async with httpx.AsyncClient(transport=_BudgetTransport(c, inner)) as cl:
        r = await cl.get("https://example.invalid/")
    assert r.content == raw


# ── 레이트리밋과 차단 구분 ─────────────────────────────────────────────
def test_large_body_is_never_a_wall_regardless_of_status():
    """본문이 실하면 거절이 아니다 — 상태코드가 무엇이든 파싱할 수 있다.

    computerbase 는 429 와 함께 32KB 짜리 정상 포럼 HTML 을 돌려주는데
    상태코드만 보고 차단으로 적었다(실측 오탐).
    """
    real_page = b"<!doctype html>" + b"<div>forum thread</div>" * 2000
    assert len(real_page) > 20_000
    assert not _walled(429, real_page)
    assert not _walled(403, real_page)


def test_429_is_not_a_wall():
    """레이트리밋은 일시적이고 백오프가 다루는 정상 상황이다.

    이걸 벽으로 치면 건강한 소스에 오경보가 난다 — computerbase 는 30일
    574건을 수집하는 정상 소스인데 반복 호출로 429 가 나자 차단으로 적혔다.
    """
    assert not _walled(429, b"http 429 too many requests")


@pytest.mark.asyncio
async def test_rate_limited_run_stays_done():
    """429 뿐이면 blocked 가 아니라 done — 다음 주기에 다시 하면 된다."""
    c = _Stub(raw=[])
    c._wall_total, c._wall_hits, c._throttle_hits = 10, 0, 10
    assert (await c.run())["status"] == "done"


@pytest.mark.asyncio
async def test_structural_wall_still_blocks():
    """403·챌린지는 구조적이라 blocked 여야 한다(35일 은폐된 androidcentral)."""
    c = _Stub(raw=[])
    c._wall_total, c._wall_hits, c._throttle_hits = 10, 9, 0
    out = await c.run()
    assert out["status"] == "blocked"
    assert "차단 응답" in out["detail"], out["detail"]


# ── Playwright 경로 (감지 사각지대) ────────────────────────────────────
@pytest.mark.asyncio
async def test_report_blocked_marks_run_blocked():
    """크롤러가 스스로 선언한 차단도 blocked 로 남아야 한다.

    _BudgetTransport 는 httpx 요청만 본다. Playwright 로 봇 챌린지를 푸는
    fmkorea 는 그 길목을 지나지 않아, 챌린지에 막혀 0건을 반환해도
    "할 말이 없었다"와 구분되지 않았다.
    """
    c = _Stub(raw=[])
    c.report_blocked("Playwright 보안 챌린지 통과 실패")
    out = await c.run()
    assert out["status"] == "blocked"
    assert out["detail"] == "Playwright 보안 챌린지 통과 실패"
    assert c.job_states[-1][1]["error_message"].startswith("blocked:")


@pytest.mark.asyncio
async def test_report_blocked_is_ignored_when_items_were_collected():
    """일부라도 긁었으면 done 이다 — 자기 선언도 예외가 아니다."""
    from base.crawler import RawVOC
    c = _Stub(raw=[RawVOC(external_id="a", content="x" * 40,
                          source_url="https://e.invalid/a")])
    c.report_blocked("챌린지 실패")
    assert (await c.run())["status"] == "done"


def test_playwright_crawlers_declare_blocks():
    """Playwright 로 챌린지를 푸는 크롤러는 실패 시 report_blocked 를 불러야 한다.

    안 부르면 그 소스만 조용히 0건으로 남아 원인이 묻힌다.
    """
    import pathlib as _p
    plats = _p.Path(__file__).resolve().parents[1] / "platforms"
    for name in ("fmkorea",):
        src = (plats / f"{name}.py").read_text()
        assert "report_blocked(" in src, (
            f"{name} 은 Playwright 챌린지를 쓰는데 실패를 알리지 않는다 — "
            "self.report_blocked(사유) 를 불러라")
