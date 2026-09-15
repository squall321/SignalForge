# 예산 소진 뒤 네트워크를 타지 않는지, 그리고 모든 크롤러가 그 길목을 지나는지 검증한다.
import ast
import pathlib
import sys
import time

import httpx
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from base.crawler import BaseCrawler, RawVOC, _BudgetTransport

PLATFORMS = pathlib.Path(__file__).resolve().parents[1] / "platforms"


class _Stub(BaseCrawler):
    async def crawl(self):
        return []


def _crawler(budget=0.0):
    c = _Stub("test")
    c.CRAWL_BUDGET_SEC = budget
    return c


class _CountingInner(httpx.AsyncBaseTransport):
    def __init__(self):
        self.calls = 0

    async def handle_async_request(self, request):
        self.calls += 1
        return httpx.Response(200, request=request, content=b"ok")


@pytest.mark.asyncio
async def test_budget_left_passes_through():
    inner = _CountingInner()
    c = _crawler(budget=9999.0)
    async with httpx.AsyncClient(transport=_BudgetTransport(c, inner)) as cl:
        r = await cl.get("https://example.invalid/")
    assert r.status_code == 200 and inner.calls == 1


@pytest.mark.asyncio
async def test_budget_spent_short_circuits_without_network():
    inner = _CountingInner()
    c = _crawler(budget=0.0)          # 이미 소진
    async with httpx.AsyncClient(transport=_BudgetTransport(c, inner)) as cl:
        r = await cl.get("https://example.invalid/")
    # 네트워크를 타지 않아야 한다 — 이게 이 장치의 전부다
    assert inner.calls == 0
    # **에러 상태코드면 안 된다.** 크롤러 60개가 raise_for_status() 를 부르고
    # 거기서 예외가 crawl() 밖으로 새면 모은 것을 통째로 잃는다.
    r.raise_for_status()                      # 예외가 나면 이 시험이 실패한다
    assert r.status_code == 200
    assert r.headers.get("x-sf-budget") == "exceeded"
    # 두 소비 경로가 모두 안전해야 한다
    assert r.json() == {}
    assert r.text == "{}"


@pytest.mark.asyncio
async def test_drain_is_fast():
    """남은 반복이 네트워크 없이 빠르게 소진되는지 — 100회가 1초 미만."""
    inner = _CountingInner()
    c = _crawler(budget=0.0)
    t = time.monotonic()
    async with httpx.AsyncClient(transport=_BudgetTransport(c, inner)) as cl:
        for _ in range(100):
            await cl.get("https://example.invalid/")
            await c._random_delay()      # 예산 초과면 대기하지 않아야 한다
    assert time.monotonic() - t < 1.0, "예산 초과 후에도 대기하고 있다"


def test_make_httpx_client_has_budget_transport():
    c = _crawler(budget=9999.0)
    assert isinstance(c._make_httpx_client()._transport, _BudgetTransport)


def _own_client_files():
    """httpx.AsyncClient 를 직접 만드는 플랫폼 모듈."""
    out = []
    for p in sorted(PLATFORMS.glob("*.py")):
        if p.stem == "__init__":
            continue
        if "httpx.AsyncClient(" in p.read_text():
            out.append(p)
    return out


@pytest.mark.parametrize("path", _own_client_files(), ids=lambda p: p.stem)
def test_own_client_uses_budget_transport(path):
    """자체 클라이언트를 만든다면 반드시 transport= 로 예산 가드를 끼워야 한다.

    이걸 빠뜨리면 그 크롤러만 조용히 예산을 무시하고 SoftTimeLimit 으로 죽는다
    — 실측 ppomppu·kompas 등 8개 소스가 하루 24회씩 그렇게 죽고 있었다.
    """
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "AsyncClient"):
            continue
        kws = {k.arg for k in node.keywords}
        assert "transport" in kws, (
            f"{path.stem}:{node.lineno} 의 AsyncClient 에 transport= 가 없다. "
            "self._budget_transport() 를 넘기거나 self._make_httpx_client() 를 써라")
