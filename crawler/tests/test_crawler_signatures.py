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
