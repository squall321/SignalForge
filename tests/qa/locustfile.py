"""SignalForge Analytics API Locust load test.

목표:
    - 동시 사용자 100명, 1분간 부하
    - GET /api/v1/analytics/* (대시보드 overview 격) 호출
    - p95 응답시간 ≤ 200ms

실행 예:
    locust --headless --users 100 --spawn-rate 10 --run-time 60s \
        -H http://127.0.0.1:8000 \
        -f tests/qa/locustfile.py

스모크 (CI):
    locust --headless --users 10 --spawn-rate 5 --run-time 30s \
        -H http://127.0.0.1:8000 \
        -f tests/qa/locustfile.py

주의:
    실제 라우터에 /dashboard/overview 가 없으므로,
    대시보드 첫 화면을 구성하는 5개 핵심 analytics endpoint 를
    "overview" 한 단위로 호출한다.
"""

from __future__ import annotations

import random

from locust import HttpUser, between, events, task

# 실제 DB 에 존재하는 제품 코드 샘플 (products 테이블 기준)
PRODUCT_CODES: list[str] = [
    "GS25U", "GS25P", "GS25",
    "GS24U", "GS24P", "GS24",
    "GS26U", "GS26P", "GS26",
    "GZF6", "GZF7", "GZF5",
    "AP15", "AP15P", "AP15PM",
    "AP16", "AP16P", "AP16PM",
]

# period_days 옵션 다양화 (라이브 필터 다양성 시뮬레이션)
PERIOD_DAYS_OPTIONS: list[int] = [7, 14, 30, 60, 90]

# granularity 옵션
GRANULARITY_OPTIONS: list[str] = ["day", "week", "month"]


class DashboardOverviewUser(HttpUser):
    """대시보드 Overview 사용자 시나리오.

    한 명의 사용자가 대시보드 페이지를 열 때 호출되는
    5개 핵심 endpoint 를 그룹으로 호출한다.
    """

    wait_time = between(1, 3)

    # 각 task 의 weight 는 endpoint 호출 빈도 반영.
    # category-dist / top-issues 가 가장 자주 (탭 전환 시) 호출되도록 가중치 부여.

    @task(3)
    def category_dist(self) -> None:
        product = random.choice(PRODUCT_CODES)
        period = random.choice(PERIOD_DAYS_OPTIONS)
        self.client.get(
            f"/api/v1/analytics/category-dist?product={product}&period_days={period}",
            name="/analytics/category-dist",
        )

    @task(3)
    def top_issues(self) -> None:
        product = random.choice(PRODUCT_CODES)
        period = random.choice(PERIOD_DAYS_OPTIONS)
        self.client.get(
            f"/api/v1/analytics/top-issues?product={product}&period_days={period}&top_n=10",
            name="/analytics/top-issues",
        )

    @task(2)
    def sentiment_trend(self) -> None:
        product = random.choice(PRODUCT_CODES)
        period = random.choice(PERIOD_DAYS_OPTIONS)
        gran = random.choice(GRANULARITY_OPTIONS)
        self.client.get(
            f"/api/v1/analytics/sentiment-trend?product={product}"
            f"&period_days={period}&granularity={gran}",
            name="/analytics/sentiment-trend",
        )

    @task(2)
    def country_heatmap(self) -> None:
        product = random.choice(PRODUCT_CODES)
        period = random.choice(PERIOD_DAYS_OPTIONS)
        self.client.get(
            f"/api/v1/analytics/country-heatmap?product={product}&period_days={period}",
            name="/analytics/country-heatmap",
        )

    @task(1)
    def compare(self) -> None:
        # 2~3 개 제품 비교
        sample = random.sample(PRODUCT_CODES, k=random.choice([2, 3]))
        products_param = ",".join(sample)
        period = random.choice(PERIOD_DAYS_OPTIONS)
        self.client.get(
            f"/api/v1/analytics/compare?products={products_param}&period_days={period}",
            name="/analytics/compare",
        )

    @task(1)
    def health(self) -> None:
        # 헬스체크는 가벼우므로 baseline 측정용
        self.client.get("/health", name="/health")


# ── p95 SLA 게이트 ─────────────────────────────────────────────

P95_SLA_MS = 200


@events.quitting.add_listener
def _check_p95_sla(environment, **_kwargs) -> None:
    """run 종료 시 p95 ≤ 200 ms 검증.

    CI 에서 exit code 1 로 떨어뜨려 SLA 위반을 표시한다.
    스모크 (10 users, 30s) 에서는 fail_ratio 만 검증해
    오탐을 줄인다 (users 가 적어 p95 변동성 큼).
    """
    stats = environment.stats.total
    p95 = stats.get_response_time_percentile(0.95)
    failure_ratio = stats.fail_ratio
    print(f"\n[QA] aggregated p95={p95} ms / failure_ratio={failure_ratio:.4f}")

    # 실패율 0.5 % 이상이면 무조건 실패
    if failure_ratio > 0.005:
        environment.process_exit_code = 1
        print(f"[QA] FAIL — failure_ratio {failure_ratio:.4f} > 0.5 %")
        return

    # p95 SLA 게이트 (스모크 모드에서는 skip)
    # 사용자 수가 50 명 이상일 때만 SLA 강제
    if environment.runner is not None and environment.runner.user_count >= 50:
        if p95 is not None and p95 > P95_SLA_MS:
            environment.process_exit_code = 1
            print(f"[QA] FAIL — p95 {p95} ms > SLA {P95_SLA_MS} ms")
        else:
            print(f"[QA] PASS — p95 {p95} ms ≤ SLA {P95_SLA_MS} ms")
    else:
        print(f"[QA] smoke run — SLA gate skipped (users < 50)")
