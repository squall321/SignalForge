# YouTube Data API 일일 quota 를 실제 상수·주기로 곱해 한도와 비교한다.
"""막는 고장(2026-10-15 실측).

독스트링의 산수가 **질의 5개 시절 값**이었고 실제와 3배 어긋나 있었다.
  문서: 525u/run × 6회 = 3,150u/일 (한도의 1/3)
  실제: _DEFAULT_QUERIES 23개 → 2,415u/run × 4회 = 9,660u
        + youtube-backfill.sh(질의 8개) 840u × 1회
        = 10,500u / 무료 10,000u = **105% 초과**
매일 마지막 실행이 quotaExceeded 로 조용히 죽는 상태였다.

StackExchange 할당량 사건과 같은 부류다 — 코드는 맞고 산수가 틀렸다.
주석에 적어두면 다음 사람이 질의를 늘릴 때 읽지 않는다. 곱셈은 테스트가 한다.
"""
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
# 이 산수는 **호스트 전용**이다 — cron 주기와 scripts/ 의 백필 질의를 함께 봐야
# 하루 소비량이 나온다. 컨테이너는 /crawler 만 바인드하므로 둘 다 없다.
# 조용히 통과시키지 않고 skip 으로 남긴다(호스트 수트에서는 반드시 돈다).
_BF = ROOT.parent / "scripts" / "youtube-backfill.sh"
_HOST_ONLY = pytest.mark.skipif(
    not (_BF.is_file() and shutil.which("crontab")),
    reason="호스트 전용 — 컨테이너에는 scripts/ 와 crontab 이 없다")
sys.path.insert(0, str(ROOT))

DAILY_QUOTA = 10_000      # YouTube Data API v3 무료 한도
SAFETY = 0.90             # 재시도·수동 실행 여유
SEARCH_COST = 100         # search.list
COMMENT_COST = 1          # commentThreads.list (영상당 1회)


def _default_queries():
    from platforms.youtube_comments import _DEFAULT_QUERIES
    return list(_DEFAULT_QUERIES)


def _videos_per_query():
    src = (ROOT / "platforms" / "youtube_comments.py").read_text()
    m = re.search(r'YOUTUBE_VIDEOS_PER_QUERY[^\n]*?기본\s*(\d+)', src)
    return int(m.group(1)) if m else 5


def _backfill_queries():
    sh = _BF.read_text()
    m = re.search(r'BF_QUERIES="([^"]+)"', sh)
    assert m, "BF_QUERIES 를 못 읽었다"
    return [q for q in m.group(1).split(",") if q.strip()]


def _runs_per_day(script_name: str) -> float:
    """crontab 의 해당 스크립트 주기 → 하루 실행 횟수."""
    out = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if script_name not in line or line.strip().startswith("#"):
            continue
        hour = line.split()[1]
        if hour.startswith("*/"):
            return 24.0 / int(hour[2:])
        return 1.0
    return 0.0


def _cost(n_queries: int, videos: int) -> int:
    return n_queries * SEARCH_COST + n_queries * videos * COMMENT_COST


@_HOST_ONLY
def test_queries_are_discoverable():
    """상수를 못 읽으면 아래 계산이 0 이 되어 테스트가 무의미해진다."""
    assert len(_default_queries()) >= 5
    assert len(_backfill_queries()) >= 3
    assert _runs_per_day("youtube-collect.sh") > 0, "cron 에 정기수집이 없다"


@_HOST_ONLY
def test_daily_quota_is_not_exceeded():
    vids = _videos_per_query()
    collect = _cost(len(_default_queries()), vids) * _runs_per_day("youtube-collect.sh")
    backfill = _cost(len(_backfill_queries()), vids) * _runs_per_day("backfill-runner.sh")
    total = collect + backfill
    limit = DAILY_QUOTA * SAFETY
    assert total <= limit, (
        f"하루 {total:.0f}u — 무료 한도 {DAILY_QUOTA} 의 {SAFETY:.0%}({limit:.0f}u)를 넘는다.\n"
        f"  정기수집 {collect:.0f}u (질의 {len(_default_queries())}개 × "
        f"하루 {_runs_per_day('youtube-collect.sh'):.0f}회)\n"
        f"  백필     {backfill:.0f}u (질의 {len(_backfill_queries())}개)\n"
        "질의를 줄이거나 cron 주기를 늘려라.")


def test_docstring_arithmetic_matches_reality():
    """독스트링이 틀린 숫자를 말하면 다음 사람이 그걸 믿는다."""
    src = (ROOT / "platforms" / "youtube_comments.py").read_text()
    head = src[:3000]
    assert "질의 5 × 영상 5" not in head, "질의 5개 시절 산수가 그대로 남아 있다"
    assert "3,150" not in head, "실제와 3배 어긋난 수치가 남아 있다"
