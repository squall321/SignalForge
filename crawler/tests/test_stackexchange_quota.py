# SE API 요청 예산이 무료 할당량(300/day)을 넘지 않는지 고정한다.
"""왜 테스트로 묶는가.

이 고장은 코드가 틀려서가 아니라 **숫자가 조용히 넘쳐서** 생겼다.
MAX_QUESTIONS=40 · 6시간 주기 = 344요청/day 로 300 을 넘었고, 초과분의 429 는
호출부 try/except 가 삼켜 "done, 0건"으로 찍혔다. 13일간 아무도 몰랐다.

주석으로 적어두면 다음 사람이 MAX_QUESTIONS 를 올릴 때 안 읽는다. 숫자끼리
곱해서 한도와 비교하는 일은 테스트가 해야 한다.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from platforms.stackexchange import (  # noqa: E402
    MAX_QUESTIONS,
    QUERY_TERMS,
    QuotaExhausted,
)

DAILY_QUOTA = 300           # 키 없이 IP 당 300/day (stackapps 키를 받으면 10,000)
SAFETY = 0.75               # 여유 — 재시도·부분실패를 감안해 한도를 다 쓰지 않는다


def _schedule_seconds() -> float:
    """celery_app.py 에 등록된 stackexchange 주기(초)."""
    src = (ROOT / "celery_app.py").read_text()
    for line in src.splitlines():
        if line.lstrip().startswith("#"):
            continue
        if '("stackexchange"' not in line:
            continue
        m = re.search(r'"schedule"\s*:\s*([0-9.]+)', line)
        assert m, f"주기를 못 읽었다: {line}"
        return float(m.group(1))
    raise AssertionError("celery_app.py 에 활성 stackexchange 스케줄이 없다")


def _requests_per_run() -> int:
    """검색 1회씩 + 질문마다 답변·코멘트 2회."""
    return len(QUERY_TERMS) + MAX_QUESTIONS * 2


def test_daily_requests_fit_in_free_quota():
    per_run = _requests_per_run()
    runs_per_day = 86400.0 / _schedule_seconds()
    daily = per_run * runs_per_day
    assert daily <= DAILY_QUOTA * SAFETY, (
        f"하루 {daily:.0f}요청 — 무료 할당량 {DAILY_QUOTA} 의 {SAFETY:.0%}"
        f"({DAILY_QUOTA * SAFETY:.0f})를 넘는다. "
        f"실행당 {per_run}회 × 하루 {runs_per_day:.1f}회. "
        "MAX_QUESTIONS 를 줄이거나 주기를 늘려라."
    )


def test_quota_exhaustion_is_reported_not_swallowed():
    """429 를 만나면 report_blocked 로 사실을 남기고 즉시 접어야 한다.

    이게 없으면 할당량이 바닥난 날도 status=done, 0건으로 보인다.
    """
    src = (ROOT / "platforms" / "stackexchange.py").read_text()
    body = src.split("async def _se_get", 1)[1].split("async def ", 1)[0]
    assert "429" in body, "_se_get 이 429 를 따로 보지 않는다"
    assert "report_blocked" in body, "429 를 만나도 차단 사실을 남기지 않는다"
    assert "QuotaExhausted" in body, "429 에서 즉시 중단하지 않는다"


def test_quota_exception_is_not_caught_by_generic_handlers():
    """루프의 `except Exception` 이 QuotaExhausted 를 먼저 삼키면 안 된다.

    삼키면 바닥난 뒤에도 남은 80여 요청을 전부 429 로 흘려보낸다.
    """
    src = (ROOT / "platforms" / "stackexchange.py").read_text()
    generic = src.count("except Exception as e:")
    guarded = src.count("except QuotaExhausted:")
    assert guarded >= generic, (
        f"`except Exception` {generic}곳 중 {guarded}곳만 QuotaExhausted 를 "
        "먼저 통과시킨다"
    )


def test_partial_results_survive_quota_death():
    """할당량이 죽어도 그때까지 모은 건 반환해야 한다 — 하루치를 통째로 버리지 않는다."""
    src = (ROOT / "platforms" / "stackexchange.py").read_text()
    crawl = src.split("async def crawl", 1)[1].split("async def _collect", 1)[0]
    assert "except QuotaExhausted" in crawl, "crawl 이 할당량 소진을 받아내지 않는다"
    assert "return raw_vocs" in crawl, "부분 결과를 반환하지 않는다"


def test_quota_exhausted_is_an_exception():
    assert issubclass(QuotaExhausted, Exception)
