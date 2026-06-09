"""
reports/daily.py / weekly.py 단위 테스트.

실 DB read-only — 어제 UTC 데이터로 build_daily_report / build_weekly_report
호출 후 마크다운 출력의 핵심 섹션을 검증한다.

실행:
  cd crawler && python -m pytest tests/test_reports.py -v
  cd crawler && python tests/test_reports.py    # 단독 실행
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reports.daily import build_daily_report, NEG_RATIO_ALERT_PP
from reports.weekly import build_weekly_report, sparkline
from reports._common import REPORTS_DIR, ensure_dir, fmt_delta


# ------------------------------------------------------------
# Test 1: sparkline 헬퍼 — 순수 함수
# ------------------------------------------------------------
def test_sparkline_basic():
    # 0~max 균등 분포면 마지막 인덱스는 최대 막대.
    s = sparkline([0, 1, 2, 3, 4, 5, 6, 7])
    assert len(s) == 8
    assert s[-1] == "█"
    assert s[0] == "▁"

    # 빈 리스트는 빈 문자열.
    assert sparkline([]) == ""

    # 모두 0 이면 최소 막대로 채워짐.
    assert sparkline([0, 0, 0]) == "▁▁▁"


# ------------------------------------------------------------
# Test 2: fmt_delta — 증감 표기
# ------------------------------------------------------------
def test_fmt_delta_zero_prev():
    # 전주 0 + 이번주 양수 → NEW 마킹
    assert "NEW" in fmt_delta(10, 0)
    # 둘 다 0
    assert fmt_delta(0, 0).startswith("0")
    # 양수 증가
    s = fmt_delta(150, 100)
    assert "+50" in s and "+50.0%" in s
    # 감소
    s2 = fmt_delta(80, 100)
    assert "-20" in s2 and "-20.0%" in s2


# ------------------------------------------------------------
# Test 3: build_daily_report — 실 DB, 어제 UTC. 파일 생성 + 핵심 섹션 검증
# ------------------------------------------------------------
def test_daily_report_yesterday():
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    path = asyncio.run(build_daily_report(yesterday))
    assert path.exists(), f"리포트 파일 미생성: {path}"
    assert path.name == f"daily_{yesterday.isoformat()}.md"

    md = path.read_text(encoding="utf-8")
    # 필수 섹션 헤더
    assert "# SignalForge Daily VOC Report" in md
    assert "## 사이트별 수집량" in md
    assert "## 지역별 분포" in md
    assert "## 제품별 Sentiment 분포" in md
    assert "## 카테고리 빈도" in md
    assert "## 부정 sentiment 비율 급증 알림" in md
    # 대상 날짜가 본문에 노출
    assert yesterday.isoformat() in md
    # 총 수집량 라인
    assert "총 수집량" in md


# ------------------------------------------------------------
# Test 4: build_weekly_report — 실 DB, 오늘 UTC. trend 섹션 검증
# ------------------------------------------------------------
def test_weekly_report_today():
    today = datetime.now(timezone.utc).date()
    path = asyncio.run(build_weekly_report(today))
    assert path.exists(), f"weekly 리포트 미생성: {path}"
    assert path.name == f"weekly_{today.isoformat()}.md"

    md = path.read_text(encoding="utf-8")
    assert "# SignalForge Weekly VOC Report" in md
    assert "## 사이트별 수집량" in md
    assert "## 사이트 Health — 최근 24h 0건" in md
    assert "## 제품 7일 Trend" in md
    # sparkline 블록 어딘가에 막대 문자가 포함되어야 정상 (제품이 있다면).
    # 데이터가 충분한 환경에서는 ▁~█ 중 하나는 나타난다.
    has_spark = any(ch in md for ch in "▁▂▃▄▅▆▇█")
    # 데이터가 정말 없을 가능성은 거의 없지만, 단정은 약하게.
    if "제품 매칭된 레코드 없음" not in md:
        assert has_spark, "sparkline 문자 미검출"


# ------------------------------------------------------------
# Test 5: REPORTS_DIR 경로가 SignalForge/reports/ 인지 검증
# ------------------------------------------------------------
def test_reports_dir_location():
    p = ensure_dir()
    assert p.is_dir()
    # 프로젝트 루트의 reports/ 이어야 함 (crawler/reports/ 아님)
    assert p.name == "reports"
    assert p.parent.name == "SignalForge"


if __name__ == "__main__":
    # 단독 실행 — pytest 없이도 동작
    test_sparkline_basic()
    print("OK test_sparkline_basic")
    test_fmt_delta_zero_prev()
    print("OK test_fmt_delta_zero_prev")
    test_reports_dir_location()
    print("OK test_reports_dir_location")
    test_daily_report_yesterday()
    print("OK test_daily_report_yesterday")
    test_weekly_report_today()
    print("OK test_weekly_report_today")
    print("ALL PASSED")
