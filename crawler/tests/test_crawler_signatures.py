# 전 크롤러가 tasks.crawl_platform 의 호출 규약(platform_code 키워드)을 지키는지 전수 검사
"""
크롤러 생성자 시그니처 회귀 테스트.

`tasks.crawl_platform` 은 모든 크롤러를
    CrawlerClass(platform_code=..., product_code=..., job_id=...)
로 생성한다. 이 규약을 어긴 크롤러는 **매 실행 TypeError 로 즉사**하고,
celery 는 그것을 retry 로 흡수하므로 로그를 파기 전까지 아무도 모른다.

실측 사고(2026-09-09 발견) — 공통 리팩터가 6개 크롤러를 빠뜨려
`(self, product_code=None, job_id=None)` 구형 시그니처가 남았고,
그중 beat 에 배선된 5개가 11~18일간 수집 0건이었다
(googlenews·appstore·recalls 12일, waybacknews 11일, wpnews 18일).
youtube 만 별도 cron 경로라 살아남았다.

crawl_jobs 테이블이 비어 있어 DB 로도 관측되지 않았다. 그래서 테스트로 고정한다.
"""
import importlib
import inspect
import os
import pkgutil
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import platforms  # noqa: E402
from base.crawler import BaseCrawler  # noqa: E402


def _crawler_classes():
    """platforms 패키지의 모든 BaseCrawler 하위 클래스 (import 실패 모듈은 건너뜀)."""
    out = []
    for m in pkgutil.iter_modules(platforms.__path__):
        try:
            mod = importlib.import_module(f"platforms.{m.name}")
        except Exception:
            continue          # 선택적 의존성 부재 등 — 여기서 볼 문제가 아니다
        for name, obj in vars(mod).items():
            if (inspect.isclass(obj) and issubclass(obj, BaseCrawler)
                    and obj is not BaseCrawler and obj.__module__ == mod.__name__):
                out.append((m.name, name, obj))
    return out


CLASSES = _crawler_classes()


def test_crawler_classes_discovered():
    """탐색 자체가 깨지면 아래 테스트가 전부 공허하게 통과한다."""
    assert len(CLASSES) >= 50, f"크롤러를 {len(CLASSES)}개밖에 못 찾았다 — 탐색 로직 확인"


@pytest.mark.parametrize("module,name,cls",
                         CLASSES, ids=[f"{m}.{n}" for m, n, _ in CLASSES])
def test_accepts_platform_code_kwarg(module, name, cls):
    """crawl_platform 의 호출 규약을 만족해야 한다."""
    params = inspect.signature(cls.__init__).parameters
    ok = ("platform_code" in params
          or any(p.kind == p.VAR_KEYWORD for p in params.values()))
    assert ok, (f"{module}.{name} 이 platform_code 를 받지 못한다 — "
                f"tasks.crawl_platform 이 매 실행 TypeError 로 죽는다. "
                f"현재 시그니처: {inspect.signature(cls.__init__)}")


# ── 수집 예산 — 시간과 건수를 함께 봐야 한다 ─────────────────────────
def test_budget_time_and_volume():
    """실측 근거: crawl 452.9s/897건 뒤 NLP 271.4s 가 붙어 725.3s 로 600s 를 넘겼다.

    NLP 비용이 수집량에 비례하므로 시간만 보는 예산으로는 부족하다.
    """
    from base.crawler import BaseCrawler

    class _Stub(BaseCrawler):
        async def crawl(self):
            return []

    c = _Stub("test")
    assert c.budget_exceeded(0) is False            # 갓 시작 — 여유
    assert c.budget_exceeded(c.CRAWL_MAX_ITEMS) is True      # 건수 상한
    assert c.budget_exceeded(c.CRAWL_MAX_ITEMS - 1) is False
    c._started -= c.CRAWL_BUDGET_SEC + 1
    assert c.budget_exceeded(0) is True             # 시간 상한


def test_run_budget_under_soft_limit():
    """전체 예산이 celery soft time limit(600s) 안에 있어야 한다.

    건수 상한만으로는 못 막는다 — 루프 한 바퀴가 수백 건을 한꺼번에 더해
    상한을 넘기고(실측 appstore 980건 vs 상한 500), NLP 단가도 소스마다
    2.6배 차이난다(dogdrip 0.254s/건 vs mlbpark 0.667s/건). 그래서 correctness 는
    청크 커밋이 담보하고, 이 예산은 마지막 청크 하나만 잃도록 하는 장치다.
    """
    from base.crawler import BaseCrawler
    WORST_PER_ITEM = 0.667        # 실측 mlbpark 368.8s / 553건
    # **soft limit 기준으로 봐야 한다.** 예산을 확인한 뒤 시작한 청크는 끝까지
    # 돌므로 예산 + 청크 1개가 실제 상한이다. hard limit 만 보다가 602.8초를
    # 놓쳤다(mobile_review).
    worst = BaseCrawler.RUN_BUDGET_SEC + BaseCrawler.NLP_CHUNK * WORST_PER_ITEM
    assert worst < 600, f"최악 {worst:.0f}s 가 soft limit 600s 를 넘는다"


def test_chunked_commit_wired():
    """run() 이 청크 단위로 save 해야 타임아웃에 전량을 잃지 않는다."""
    import inspect as _i
    from base.crawler import BaseCrawler
    src = _i.getsource(BaseCrawler.run)
    assert "NLP_CHUNK" in src and "run_budget_exceeded" in src
    # save 가 루프 안에 있어야 한다 — 루프 밖 단일 호출이면 의미가 없다
    assert src.index("for i in range(0, len(raw_vocs)") < src.index("await self.save(")


# ── 예산 가드 위치 — 가장 안쪽 루프에 있어야 한다 ─────────────────────
# 실수를 세 번 반복했다. 가드를 바깥 루프에 두면 그 안쪽이 통째로 돌아 발화가 늦다 —
#   appstore     마켓 루프에만 → 31/32 실패
#   telepolis    기사 루프에만 → 목록이 예산을 다 먹어 694.6초, 수집 10건
#   mobile_review term 루프에만 → 622.0초
# 아래는 "가드가 존재하는지"만 보는 최소 방어다. 위치까지 정적으로 검증할 수는
# 없지만, 타임아웃 이력이 있는 소스에 가드가 **빠지는 것**은 막는다.
_TIMEOUT_PRONE = [
    "clien", "dogdrip", "kaskus", "dcinside", "donanimhaber",
    "mlbpark", "appstore", "bestbuy",
    "telepolis", "hardware_fr", "mobile_review", "computerbase",
]


@pytest.mark.parametrize("module_name", _TIMEOUT_PRONE)
def test_timeout_prone_crawlers_have_budget_guard(module_name):
    """SoftTimeLimitExceeded 이력이 있는 크롤러는 예산 가드를 가져야 한다."""
    import pathlib
    src = pathlib.Path(__file__).parent.parent / "platforms" / f"{module_name}.py"
    if not src.exists():
        pytest.skip(f"{module_name} 없음")
    text = src.read_text()
    assert "budget_exceeded" in text, (
        f"{module_name} 에 예산 가드가 없다 — 타임아웃 시 수집분이 전량 버려진다")


def test_multi_loop_crawlers_guard_inner_loop():
    """3중 루프 크롤러는 가장 안쪽에서 확인해야 한다(mobile_review 622초 사례)."""
    import pathlib
    src = (pathlib.Path(__file__).parent.parent / "platforms" / "mobile_review.py").read_text()
    guard_at = src.index("budget_exceeded")
    page_loop_at = src.index("for page in range(1, LIST_PAGES + 1)")
    assert page_loop_at < guard_at, "가드가 페이지 루프 바깥에 있다"
