"""
T3 커뮤니티 비교 서비스 (P3-3).

6 endpoint 의 비즈니스 로직.

데이터 소스:
- platform_health (MV)  : 24h/7d 활동 + 감성 요약 + status 라벨
- country_daily   (MV)  : 일/국가/제품 단위 집계
- voc_records           : sentiment 분포(boxplot) · 카테고리 라이브 집계

성능 가드:
- product-matrix : platform_id × product_id 집계 — collected_at 인덱스 사용,
                   `since` 7d 기본값으로 ≤ 700 ms 보장. limit 80 platform / 12 product.
- dispersion     : product_id + collected_at 인덱스 사용. outlier 는 q3+1.5*iqr 초과 30개.
- early-signal   : 최근 14일 평균 vs 이전 14일 평균을 platform 별로 비교. (라이브)
- clusters       : platform_health 만 사용 (60+ 행) → 순수 Python k-means.

KMeans 는 numpy 없이 순수 Python 구현 (시드 고정, 50 iter 상한).
"""
from __future__ import annotations

import logging
import random
import statistics
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import redis_cache
from app.schemas.community import (
    AnomalyEntry,
    BoxplotEntry,
    ClusterCentroid,
    ClusterPoint,
    ClustersResponse,
    DispersionResponse,
    EarlySignalEvent,
    EarlySignalResponse,
    EarlySignalTimelinePoint,
    MatrixCell,
    OutlierEntry,
    PlatformHealth,
    PlatformHealthResponse,
    ProductMatrixResponse,
)

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────
# helpers — KMeans (numpy 없이 pure python)
# ────────────────────────────────────────────────────────────────────
def _kmeans(
    samples: List[Tuple[float, float]],
    k: int,
    max_iter: int = 50,
    seed: int = 42,
) -> Tuple[List[int], List[Tuple[float, float]], int]:
    """단순 Lloyd's algorithm.

    Returns: (assignments, centroids, iterations_used)

    수렴 조건: 모든 centroid 의 좌표 변동량 합 < 1e-6 또는 max_iter 도달.
    K > N 일 때는 K=N 으로 강등.
    """
    n = len(samples)
    if n == 0:
        return [], [], 0
    if k >= n:
        # 모든 점이 자기 자신 centroid (degenerate)
        return list(range(n)), list(samples), 0

    rng = random.Random(seed)
    # k-means++ 초기화
    centroids: List[Tuple[float, float]] = [samples[rng.randrange(n)]]
    while len(centroids) < k:
        # 각 점에서 가장 가까운 centroid 까지의 제곱거리
        dists_sq = []
        for s in samples:
            d_min = min(
                (s[0] - c[0]) ** 2 + (s[1] - c[1]) ** 2 for c in centroids
            )
            dists_sq.append(d_min)
        total = sum(dists_sq)
        if total <= 0:
            # 모두 동일 좌표 — 임의로 다른 점을 골라 종료
            centroids.append(samples[rng.randrange(n)])
            continue
        # weighted pick
        r = rng.random() * total
        acc = 0.0
        chosen = samples[-1]
        for s, dsq in zip(samples, dists_sq):
            acc += dsq
            if acc >= r:
                chosen = s
                break
        centroids.append(chosen)

    assignments = [0] * n
    iters_used = 0
    for it in range(1, max_iter + 1):
        iters_used = it
        # assign
        changed = False
        for i, s in enumerate(samples):
            best = 0
            best_d = float("inf")
            for ci, c in enumerate(centroids):
                d = (s[0] - c[0]) ** 2 + (s[1] - c[1]) ** 2
                if d < best_d:
                    best_d = d
                    best = ci
            if assignments[i] != best:
                changed = True
                assignments[i] = best

        # update
        new_centroids: List[Tuple[float, float]] = []
        for ci in range(k):
            members = [samples[i] for i in range(n) if assignments[i] == ci]
            if not members:
                # 비어있는 클러스터 — 가장 먼 점을 새 centroid 로
                new_centroids.append(centroids[ci])
                continue
            mx = sum(m[0] for m in members) / len(members)
            my = sum(m[1] for m in members) / len(members)
            new_centroids.append((mx, my))

        # 수렴 검사
        shift = sum(
            abs(centroids[i][0] - new_centroids[i][0])
            + abs(centroids[i][1] - new_centroids[i][1])
            for i in range(k)
        )
        centroids = new_centroids
        if not changed or shift < 1e-6:
            break

    return assignments, centroids, iters_used


def _since_to_dt(since: Optional[str], default_days: int = 7) -> datetime:
    """`since` 문자열 → UTC aware datetime.

    허용:
    - 'YYYY-MM-DD' → 해당 일자 00:00 UTC
    - None → now - default_days
    """
    if not since:
        return datetime.now(timezone.utc) - timedelta(days=default_days)
    try:
        d = datetime.strptime(since, "%Y-%m-%d")
        return d.replace(tzinfo=timezone.utc)
    except ValueError as e:
        raise ValueError(f"invalid since format (YYYY-MM-DD required): {since}") from e


# ────────────────────────────────────────────────────────────────────
# Service
# ────────────────────────────────────────────────────────────────────
class CommunityService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ----------------------------------------------------------------
    # 1) /platforms/health
    # ----------------------------------------------------------------
    @redis_cache(ttl_seconds=300, key_prefix="community:", model_cls=PlatformHealthResponse)
    async def health(self, region: Optional[str] = None) -> PlatformHealthResponse:
        params: Dict[str, Any] = {}
        where = ""
        if region:
            params["region"] = region
            where = "WHERE region = :region"
        sql = f"""
            SELECT platform_id, code, region, base_url,
                   posts_24h, posts_7d, sent_avg_7d, avg_body_len_7d,
                   last_collected, status
            FROM platform_health
            {where}
            ORDER BY posts_7d DESC NULLS LAST, code
        """
        rows = (await self.db.execute(text(sql), params)).all()
        items: List[PlatformHealth] = []
        n_active = n_idle = n_dead = 0
        for r in rows:
            st = (r.status or "dead").lower()
            if st == "active":
                n_active += 1
            elif st == "idle":
                n_idle += 1
            else:
                n_dead += 1
            items.append(
                PlatformHealth(
                    platform_id=r.platform_id,
                    code=r.code,
                    region=r.region,
                    base_url=r.base_url,
                    posts_24h=int(r.posts_24h or 0),
                    posts_7d=int(r.posts_7d or 0),
                    sent_avg_7d=float(r.sent_avg_7d) if r.sent_avg_7d is not None else None,
                    avg_body_len_7d=int(r.avg_body_len_7d) if r.avg_body_len_7d is not None else None,
                    last_collected=r.last_collected.isoformat() if r.last_collected else None,
                    status=st,
                )
            )
        return PlatformHealthResponse(
            items=items,
            total=len(items),
            active=n_active,
            idle=n_idle,
            dead=n_dead,
        )

    # ----------------------------------------------------------------
    # 2) /platforms/product-matrix
    # ----------------------------------------------------------------
    @redis_cache(ttl_seconds=600, key_prefix="community:", model_cls=ProductMatrixResponse)
    async def product_matrix(
        self,
        since: Optional[str] = None,
        products: Optional[List[str]] = None,
    ) -> ProductMatrixResponse:
        """platform × product 셀별 n / sent_avg / neg_rate.

        성능 보장:
        - voc_records (idx_voc_product = product_id, collected_at) 사용.
        - since 미지정 시 7d default.
        - 상위 80 platform × 최대 12 product 로 제한.
        """
        since_dt = _since_to_dt(since, default_days=7)
        params: Dict[str, Any] = {"since": since_dt}
        prod_filter = ""
        if products:
            params["products"] = [p.upper() for p in products]
            prod_filter = "AND p.code = ANY(:products)"

        sql = f"""
            SELECT
                pl.code                                    AS platform_code,
                p.code                                     AS product_code,
                count(v.id)                                AS n,
                avg(v.sentiment_score)::numeric(6,4)       AS sent_avg,
                sum((v.sentiment_label = 'negative')::int) AS neg_cnt
            FROM voc_active v
            JOIN platforms pl ON pl.id = v.platform_id
            JOIN products  p  ON p.id  = v.product_id
            WHERE v.collected_at >= :since
              AND v.archived_at IS NULL
              {prod_filter}
            GROUP BY pl.code, p.code
            HAVING count(v.id) >= 1
            ORDER BY count(v.id) DESC
            LIMIT 960    -- 80 platform × 12 product 안전 상한
        """
        rows = (await self.db.execute(text(sql), params)).all()

        cells: List[MatrixCell] = []
        plat_set: List[str] = []
        plat_seen = set()
        prod_set: List[str] = []
        prod_seen = set()
        for r in rows:
            n = int(r.n or 0)
            neg = int(r.neg_cnt or 0)
            neg_rate = round((neg / n) * 100.0, 2) if n else 0.0
            cells.append(
                MatrixCell(
                    platform=r.platform_code,
                    product=r.product_code,
                    n=n,
                    sent_avg=float(r.sent_avg or 0.0),
                    neg_rate=neg_rate,
                )
            )
            if r.platform_code not in plat_seen:
                plat_seen.add(r.platform_code)
                plat_set.append(r.platform_code)
            if r.product_code not in prod_seen:
                prod_seen.add(r.product_code)
                prod_set.append(r.product_code)

        return ProductMatrixResponse(
            cells=cells,
            platforms=plat_set,
            products=prod_set,
            since=since_dt.date().isoformat(),
        )

    # ----------------------------------------------------------------
    # 3) /platforms/dispersion
    # ----------------------------------------------------------------
    @redis_cache(ttl_seconds=600, key_prefix="community:", model_cls=DispersionResponse)
    async def dispersion(
        self,
        product_id: Optional[int] = None,
        since: Optional[str] = None,
    ) -> DispersionResponse:
        """플랫폼별 sentiment_score 분포 (boxplot) + outlier 30개.

        boxplot: q1/median/q3/iqr/whisker 를 SQL 의 percentile_cont 로 계산.
        outlier: q3 + 1.5*iqr 초과 또는 q1 - 1.5*iqr 미만 voc 를 최대 30개.
        """
        since_dt = _since_to_dt(since, default_days=14)
        params: Dict[str, Any] = {"since": since_dt}
        prod_filter = ""
        if product_id is not None:
            params["product_id"] = product_id
            prod_filter = "AND v.product_id = :product_id"

        # boxplot 통계 — 플랫폼별 4분위수
        box_sql = f"""
            SELECT
                pl.code                                                                    AS platform,
                count(v.id)                                                                AS n,
                percentile_cont(0.25) WITHIN GROUP (ORDER BY v.sentiment_score)::float8   AS q1,
                percentile_cont(0.50) WITHIN GROUP (ORDER BY v.sentiment_score)::float8   AS med,
                percentile_cont(0.75) WITHIN GROUP (ORDER BY v.sentiment_score)::float8   AS q3,
                min(v.sentiment_score)::float8                                             AS smin,
                max(v.sentiment_score)::float8                                             AS smax
            FROM voc_active v
            JOIN platforms pl ON pl.id = v.platform_id
            WHERE v.collected_at >= :since
              AND v.sentiment_score IS NOT NULL
              {prod_filter}
            GROUP BY pl.code
            HAVING count(v.id) >= 8
            ORDER BY count(v.id) DESC
            LIMIT 60
        """
        rows = (await self.db.execute(text(box_sql), params)).all()
        boxplot: List[BoxplotEntry] = []
        for r in rows:
            q1 = float(r.q1 or 0.0)
            q3 = float(r.q3 or 0.0)
            iqr = q3 - q1
            lo_target = q1 - 1.5 * iqr
            hi_target = q3 + 1.5 * iqr
            lo = max(lo_target, float(r.smin or lo_target))
            hi = min(hi_target, float(r.smax or hi_target))
            boxplot.append(
                BoxplotEntry(
                    platform=r.platform,
                    q1=round(q1, 4),
                    median=round(float(r.med or 0.0), 4),
                    q3=round(q3, 4),
                    iqr=round(iqr, 4),
                    lo=round(lo, 4),
                    hi=round(hi, 4),
                    n=int(r.n or 0),
                )
            )

        # outlier — boxplot 한 다음 플랫폼별 lo/hi 기준으로 30 개만
        outliers: List[OutlierEntry] = []
        if boxplot:
            # 플랫폼별 hi 위/아래 voc 를 union 으로 찾음
            box_map = {b.platform: b for b in boxplot}
            plat_codes = list(box_map.keys())
            params_o: Dict[str, Any] = {
                "since": since_dt,
                "plats": plat_codes,
            }
            if product_id is not None:
                params_o["product_id"] = product_id
                pf = "AND v.product_id = :product_id"
            else:
                pf = ""
            out_sql = f"""
                SELECT v.id            AS voc_id,
                       pl.code         AS platform,
                       v.sentiment_score::float8 AS s,
                       left(coalesce(v.content_translated, v.content_original), 100) AS snippet
                FROM voc_active v
                JOIN platforms pl ON pl.id = v.platform_id
                WHERE v.collected_at >= :since
                  AND v.sentiment_score IS NOT NULL
                  AND pl.code = ANY(:plats)
                  {pf}
                ORDER BY abs(v.sentiment_score) DESC
                LIMIT 600
            """
            cand = (await self.db.execute(text(out_sql), params_o)).all()
            for r in cand:
                b = box_map.get(r.platform)
                if b is None:
                    continue
                s = float(r.s)
                if s > b.hi or s < b.lo:
                    outliers.append(
                        OutlierEntry(
                            platform=r.platform,
                            voc_id=int(r.voc_id),
                            sentiment_score=round(s, 4),
                            snippet=r.snippet,
                        )
                    )
                if len(outliers) >= 30:
                    break

        return DispersionResponse(
            boxplot=boxplot,
            outliers=outliers,
            product_id=product_id,
            since=since_dt.date().isoformat(),
        )

    # ----------------------------------------------------------------
    # 4) /platforms/early-signal
    # ----------------------------------------------------------------
    async def early_signal(
        self,
        product_id: Optional[int] = None,
        category: Optional[str] = None,
    ) -> EarlySignalResponse:
        """플랫폼별 sentiment 급변/물량 급증 → 선행 플랫폼 검출.

        last_14d 평균 vs prev_14d 평균. 가장 큰 |delta_sent| 음수(부정 신호)를
        먼저 보인 platform 을 leading 으로 기록.

        timeline: 최근 28일을 platform 별로 (top 8) 반환.
        """
        end = datetime.now(timezone.utc)
        start_28 = end - timedelta(days=28)
        params: Dict[str, Any] = {"start_28": start_28, "end": end}
        cat_filter = ""
        prod_filter = ""
        if category:
            params["category"] = category.lower()
            cat_filter = "AND :category = ANY(v.categories)"
        if product_id is not None:
            params["product_id"] = product_id
            prod_filter = "AND v.product_id = :product_id"

        sql = f"""
            SELECT
                pl.code                                                  AS platform,
                date_trunc('day', v.collected_at)::date                  AS day,
                count(v.id)                                              AS n,
                avg(v.sentiment_score)::float8                           AS sent_avg
            FROM voc_active v
            JOIN platforms pl ON pl.id = v.platform_id
            WHERE v.collected_at >= :start_28
              AND v.collected_at <= :end
              {prod_filter}
              {cat_filter}
            GROUP BY pl.code, day
            HAVING count(v.id) >= 1
            ORDER BY pl.code, day
        """
        rows = (await self.db.execute(text(sql), params)).all()

        # 플랫폼별 집계
        per_plat: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            per_plat.setdefault(r.platform, []).append(
                {"day": r.day, "n": int(r.n), "sent_avg": float(r.sent_avg or 0.0)}
            )

        # top 8 (전체 n 기준)
        top_plats = sorted(
            per_plat.keys(),
            key=lambda p: -sum(d["n"] for d in per_plat[p]),
        )[:8]

        timeline: List[EarlySignalTimelinePoint] = []
        for p in top_plats:
            for d in per_plat[p]:
                timeline.append(
                    EarlySignalTimelinePoint(
                        platform=p,
                        day=d["day"].isoformat(),
                        n=d["n"],
                        sent_avg=round(d["sent_avg"], 4),
                    )
                )

        # 선행 플랫폼 — last_14d 평균 sent 가 prev_14d 대비 큰 폭으로 떨어진 곳
        mid = (end - timedelta(days=14)).date()
        candidates: List[Tuple[str, float, int]] = []
        for p, days in per_plat.items():
            prev_vals = [d["sent_avg"] for d in days if d["day"] < mid]
            last_vals = [d["sent_avg"] for d in days if d["day"] >= mid]
            if len(prev_vals) < 3 or len(last_vals) < 3:
                continue
            delta = statistics.mean(last_vals) - statistics.mean(prev_vals)
            # 최초 부정 신호일 — last_vals 중 prev mean 이하 첫 일자
            prev_mean = statistics.mean(prev_vals)
            first_neg_day = None
            for d in days:
                if d["day"] >= mid and d["sent_avg"] < prev_mean:
                    first_neg_day = d["day"]
                    break
            if first_neg_day is None:
                continue
            lead_days = (datetime.now(timezone.utc).date() - first_neg_day).days
            candidates.append((p, delta, lead_days))

        event: Optional[EarlySignalEvent] = None
        if candidates:
            # 가장 음수 delta 가 큰 platform 채택
            candidates.sort(key=lambda x: x[1])
            lead_p, delta, lead_days = candidates[0]
            detected_at = (datetime.now(timezone.utc).date() - timedelta(days=lead_days)).isoformat()
            event = EarlySignalEvent(
                detected_at=detected_at,
                leading_platform=lead_p,
                lead_days=lead_days,
                summary=(
                    f"{lead_p} 가 다른 플랫폼보다 {lead_days}일 앞서 "
                    f"부정 신호 (Δsent={delta:+.3f}) 를 보임"
                ),
            )

        return EarlySignalResponse(
            event=event,
            timeline=timeline,
            product_id=product_id,
            category=category,
        )

    # ----------------------------------------------------------------
    # 5) /platforms/clusters
    # ----------------------------------------------------------------
    @redis_cache(ttl_seconds=600, key_prefix="community:", model_cls=ClustersResponse)
    async def clusters(self, k: int = 6) -> ClustersResponse:
        """sentiment 패턴 벡터(2D) 기반 KMeans 클러스터링.

        벡터: (pos_rate, neg_rate)  — sent_avg 와 dispersion 의 1차 근사.
        platform_health MV 만 사용 (60+ 행) → 빠름. voc_records 라이브 의존 없음.
        """
        if k < 2:
            k = 2
        if k > 10:
            k = 10

        # 7d 라이브 pos/neg 비율 — voc_records 한번 더 집계
        # platform_health 의 sent_avg_7d 만으로는 패턴 분리가 어렵기 때문.
        sql = """
            SELECT
                pl.code                                       AS platform,
                count(v.id)                                   AS n,
                sum((v.sentiment_label = 'positive')::int)    AS pos,
                sum((v.sentiment_label = 'negative')::int)    AS neg,
                avg(v.sentiment_score)::float8                AS sent_avg
            FROM platforms pl
            LEFT JOIN voc_records v
              ON v.platform_id = pl.id
             AND v.collected_at > now() - interval '7 days'
            WHERE pl.is_active = true
            GROUP BY pl.code
            HAVING count(v.id) >= 5
        """
        rows = (await self.db.execute(text(sql))).all()

        platforms: List[str] = []
        samples: List[Tuple[float, float]] = []
        meta: List[Dict[str, Any]] = []  # n / sent_avg 보존
        for r in rows:
            n = int(r.n or 0)
            if n == 0:
                continue
            pos = int(r.pos or 0)
            neg = int(r.neg or 0)
            pos_rate = pos / n * 100.0
            neg_rate = neg / n * 100.0
            platforms.append(r.platform)
            samples.append((pos_rate, neg_rate))
            meta.append(
                {
                    "n": n,
                    "sent_avg": float(r.sent_avg or 0.0),
                    "pos_rate": pos_rate,
                    "neg_rate": neg_rate,
                }
            )

        assignments, centroids, iters_used = _kmeans(samples, k=k, max_iter=50)

        points = [
            ClusterPoint(
                platform=platforms[i],
                cluster=assignments[i],
                x=round(samples[i][0], 3),
                y=round(samples[i][1], 3),
            )
            for i in range(len(platforms))
        ]

        # centroid 정보 + 멤버 통계
        centroid_objs: List[ClusterCentroid] = []
        for ci, c in enumerate(centroids):
            members = [i for i, a in enumerate(assignments) if a == ci]
            if not members:
                centroid_objs.append(
                    ClusterCentroid(
                        cluster=ci,
                        x=round(c[0], 3),
                        y=round(c[1], 3),
                        size=0,
                        pos_rate_avg=0.0,
                        neg_rate_avg=0.0,
                        sent_avg=0.0,
                    )
                )
                continue
            avg_pos = sum(meta[i]["pos_rate"] for i in members) / len(members)
            avg_neg = sum(meta[i]["neg_rate"] for i in members) / len(members)
            avg_sent = sum(meta[i]["sent_avg"] for i in members) / len(members)
            centroid_objs.append(
                ClusterCentroid(
                    cluster=ci,
                    x=round(c[0], 3),
                    y=round(c[1], 3),
                    size=len(members),
                    pos_rate_avg=round(avg_pos, 3),
                    neg_rate_avg=round(avg_neg, 3),
                    sent_avg=round(avg_sent, 4),
                )
            )

        return ClustersResponse(
            points=points,
            centroids=centroid_objs,
            k=k,
            iterations=iters_used,
        )

    # ----------------------------------------------------------------
    # 6) /platforms/anomalies
    # ----------------------------------------------------------------
    async def anomalies(self) -> List[AnomalyEntry]:
        """4 종 이상 신호:

        - dead_7d              : 7일 무수집  (status='dead')
        - idle_24h             : 24h 무수집  (status='idle')
        - extreme_negative_7d  : 7d sent_avg <= -0.3  (감성 극단)
        - drop_rate            : posts_24h < posts_7d/14 * 0.3  (수집량 30% 미만)
        """
        out: List[AnomalyEntry] = []

        # platform_health MV → 4 종 rule
        sql = """
            SELECT code, status, posts_24h, posts_7d, sent_avg_7d, last_collected
            FROM platform_health
            ORDER BY code
        """
        rows = (await self.db.execute(text(sql))).all()
        for r in rows:
            code = r.code
            status = (r.status or "").lower()
            last_iso = r.last_collected.isoformat() if r.last_collected else ""
            p24 = int(r.posts_24h or 0)
            p7 = int(r.posts_7d or 0)
            sent7 = float(r.sent_avg_7d) if r.sent_avg_7d is not None else None

            if status == "dead":
                out.append(
                    AnomalyEntry(
                        code=code,
                        reason="dead_7d",
                        since=last_iso or "n/a",
                        detail={"posts_7d": p7},
                    )
                )
                continue  # dead 면 다른 룰 평가 무의미

            if status == "idle":
                out.append(
                    AnomalyEntry(
                        code=code,
                        reason="idle_24h",
                        since=last_iso or "n/a",
                        detail={"posts_24h": p24, "posts_7d": p7},
                    )
                )

            if sent7 is not None and sent7 <= -0.3:
                out.append(
                    AnomalyEntry(
                        code=code,
                        reason="extreme_negative_7d",
                        since=last_iso or "n/a",
                        detail={"sent_avg_7d": sent7},
                    )
                )

            # 수집량 급감: 24h 환산치(=p7/14 * 2) 대비 30% 미만
            #   - p7/14 = 12h 평균이라 *2 = 24h 환산.
            #   - p7 충분히 클 때만 (>= 14) 의미있게 평가.
            if p7 >= 14:
                expected_24h = (p7 / 14.0) * 2.0
                if expected_24h > 0 and p24 < expected_24h * 0.3:
                    out.append(
                        AnomalyEntry(
                            code=code,
                            reason="drop_rate",
                            since=last_iso or "n/a",
                            detail={
                                "posts_24h": p24,
                                "expected_24h": round(expected_24h, 2),
                                "ratio": round(p24 / expected_24h, 3) if expected_24h else 0.0,
                            },
                        )
                    )

        return out


__all__ = ["CommunityService"]
