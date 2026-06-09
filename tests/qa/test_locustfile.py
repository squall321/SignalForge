"""locustfile import / 상수 무결성 테스트.

locustfile.py 가 syntax / import 오류 없이 로드되고,
필수 상수가 적절한 형태인지 검증한다.

실행:
    pytest tests/qa/test_locustfile.py -v
"""

from __future__ import annotations


def test_import_locustfile() -> None:
    """locustfile.py 가 import 가능하고 핵심 심볼이 존재해야 한다."""
    from tests.qa import locustfile

    assert hasattr(locustfile, "DashboardOverviewUser")
    assert hasattr(locustfile, "P95_SLA_MS")
    assert locustfile.P95_SLA_MS == 200


def test_product_codes_non_empty() -> None:
    """PRODUCT_CODES 가 비어 있지 않고 모두 문자열이어야 한다."""
    from tests.qa.locustfile import PRODUCT_CODES

    assert len(PRODUCT_CODES) >= 3
    assert all(isinstance(c, str) and len(c) >= 2 for c in PRODUCT_CODES)


def test_period_days_options_sane() -> None:
    """period_days 옵션은 양의 정수여야 한다."""
    from tests.qa.locustfile import PERIOD_DAYS_OPTIONS

    assert all(isinstance(p, int) and 1 <= p <= 365 for p in PERIOD_DAYS_OPTIONS)


def test_granularity_options() -> None:
    """granularity 는 day/week/month 만 허용."""
    from tests.qa.locustfile import GRANULARITY_OPTIONS

    assert set(GRANULARITY_OPTIONS) <= {"day", "week", "month"}


def test_user_class_has_expected_tasks() -> None:
    """DashboardOverviewUser 에 5+ 개 task 메소드가 정의돼야 한다."""
    from tests.qa.locustfile import DashboardOverviewUser

    method_names = [
        m for m in dir(DashboardOverviewUser)
        if not m.startswith("_") and callable(getattr(DashboardOverviewUser, m))
    ]
    # locust 가 wrapping 한 task 들도 attribute 로 잡힌다
    expected = {"category_dist", "top_issues", "sentiment_trend",
                "country_heatmap", "compare", "health"}
    assert expected.issubset(set(method_names))
