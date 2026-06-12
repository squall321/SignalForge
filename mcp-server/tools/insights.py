"""MCP Insights Tools — 일일 브리핑, 알림 상태, 사이트 헬스, 신흥 키워드"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone, timedelta
from typing import Optional, List

from sqlalchemy import text

from db import get_db_session


# ── 한국어/영어 불용어 (간단한 토크나이저용) ─────────────────────────────────
_STOPWORDS_EN = {
    "the", "a", "an", "and", "or", "but", "if", "then", "else", "of", "to", "in", "on",
    "for", "with", "at", "by", "from", "as", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "this", "that", "these", "those",
    "i", "you", "he", "she", "it", "we", "they", "my", "your", "his", "her", "its", "our",
    "their", "me", "him", "us", "them", "what", "which", "who", "whom", "where", "when",
    "why", "how", "all", "any", "both", "each", "few", "more", "most", "other", "some",
    "such", "no", "nor", "not", "only", "own", "same", "so", "than", "too", "very",
    "can", "will", "just", "should", "now", "would", "could", "may", "might", "also",
    "really", "very", "much", "many", "lot", "lots", "get", "got", "going", "go", "one",
    "two", "three", "samsung", "galaxy", "phone", "device", "thing", "things", "people",
    "like", "use", "used", "using", "make", "made", "even", "still", "way", "year",
    "time", "back", "first", "new", "good", "bad", "well", "want", "need", "see", "know",
    "think", "look", "feel", "say", "said", "tell", "told", "asked",
}
_STOPWORDS_KO = {
    "그리고", "그러나", "하지만", "그래서", "그런데", "왜냐하면", "또한", "또는",
    "정말", "진짜", "그냥", "좀", "많이", "조금", "되게", "엄청", "너무", "아주",
    "그게", "이게", "저게", "이거", "그거", "저거", "여기", "거기", "저기",
    "지금", "그때", "오늘", "어제", "내일", "요즘", "오랜만",
    "있다", "없다", "되다", "하다", "이다", "아니다", "같다", "보다", "주다", "받다",
    "있어요", "없어요", "있는", "없는", "되는", "하는", "같은",
    "삼성", "갤럭시", "스마트폰", "폰", "휴대폰", "핸드폰", "제품",
    "사람", "사용", "사용자", "분들", "분도", "여러분",
    "근데", "이제", "그래도", "그럼", "거든요", "이런", "그런", "저런",
    "이런데", "그런데도", "어떻게", "어떤", "무슨", "어디", "어디서",
    "입니다", "합니다", "됩니다", "있습니다", "없습니다",
    "은", "는", "이", "가", "을", "를", "에", "의", "와", "과", "도", "만",
    "수", "것", "것을", "건", "거", "곳", "때", "분", "년", "월", "일",
}

# 한국어 음절 또는 영어 단어 매칭. 길이 ≥2.
_TOKEN_RE = re.compile(r"[가-힣]{2,}|[A-Za-z][A-Za-z0-9'+\-]{1,}")


def _tokenize(text_str: str) -> tuple[list[str], list[str]]:
    """텍스트를 한국어 토큰과 영어 토큰으로 분리."""
    ko_tokens: list[str] = []
    en_tokens: list[str] = []
    for tok in _TOKEN_RE.findall(text_str or ""):
        if re.match(r"^[가-힣]+$", tok):
            if tok not in _STOPWORDS_KO and len(tok) >= 2:
                ko_tokens.append(tok)
        else:
            low = tok.lower()
            if low not in _STOPWORDS_EN and not low.isdigit() and len(low) >= 3:
                en_tokens.append(low)
    return ko_tokens, en_tokens


# ── 1. daily_briefing(date) ──────────────────────────────────────────────────
async def daily_briefing_tool(date: Optional[str] = None) -> str:
    """지정 날짜(KST)의 VOC 현황을 자연어 브리핑으로 반환."""
    if date:
        try:
            target = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            return f"날짜 형식 오류: '{date}' (YYYY-MM-DD 필요)"
    else:
        # KST 기준 오늘
        target = (datetime.now(timezone.utc) + timedelta(hours=9)).date()

    start = datetime.combine(target, datetime.min.time(), tzinfo=timezone.utc) - timedelta(hours=9)
    end = start + timedelta(days=1)

    overall_stmt = text("""
        SELECT
            COUNT(*) AS total,
            ROUND(AVG(v.sentiment_score)::numeric, 3) AS avg_score,
            SUM(CASE WHEN v.sentiment_label = 'positive' THEN 1 ELSE 0 END) AS positive,
            SUM(CASE WHEN v.sentiment_label = 'negative' THEN 1 ELSE 0 END) AS negative,
            SUM(CASE WHEN v.sentiment_label = 'neutral'  THEN 1 ELSE 0 END) AS neutral,
            COUNT(DISTINCT v.platform_id) AS platforms_active,
            COUNT(DISTINCT v.product_id) AS products_mentioned
        FROM voc_active v
        WHERE v.collected_at >= :start AND v.collected_at < :end
    """)

    top_products_stmt = text("""
        SELECT p.code, p.name_ko, COUNT(*) AS cnt,
               ROUND(AVG(v.sentiment_score)::numeric, 3) AS avg_score
        FROM voc_active v JOIN products p ON p.id = v.product_id
        WHERE v.collected_at >= :start AND v.collected_at < :end
        GROUP BY p.code, p.name_ko
        ORDER BY cnt DESC LIMIT 5
    """)

    top_categories_stmt = text("""
        SELECT unnest(v.categories) AS cat, COUNT(*) AS cnt
        FROM voc_active v
        WHERE v.collected_at >= :start AND v.collected_at < :end
          AND v.categories IS NOT NULL
        GROUP BY cat ORDER BY cnt DESC LIMIT 5
    """)

    neg_spike_stmt = text("""
        SELECT p.code, p.name_ko, COUNT(*) AS neg_cnt
        FROM voc_active v JOIN products p ON p.id = v.product_id
        WHERE v.collected_at >= :start AND v.collected_at < :end
          AND v.sentiment_label = 'negative'
        GROUP BY p.code, p.name_ko
        ORDER BY neg_cnt DESC LIMIT 3
    """)

    params = {"start": start, "end": end}
    async with get_db_session() as db:
        overall = (await db.execute(overall_stmt, params)).mappings().one_or_none()
        top_products = (await db.execute(top_products_stmt, params)).mappings().all()
        top_cats = (await db.execute(top_categories_stmt, params)).mappings().all()
        neg_spikes = (await db.execute(neg_spike_stmt, params)).mappings().all()

    if not overall or overall["total"] == 0:
        return f"[일일 브리핑 {target.isoformat()}] 수집된 VOC 없음"

    total = overall["total"]
    pos_rate = round((overall["positive"] or 0) / total * 100, 1)
    neg_rate = round((overall["negative"] or 0) / total * 100, 1)

    lines = [
        f"[SignalForge 일일 브리핑 — {target.isoformat()} (KST)]",
        "",
        f"## 전체 요약",
        f"- 신규 VOC: {total:,}건 / 활성 플랫폼 {overall['platforms_active']}개 / 언급 제품 {overall['products_mentioned']}종",
        f"- 평균 감성: {overall['avg_score']} (긍정 {pos_rate}% · 부정 {neg_rate}%)",
        "",
        "## 언급량 TOP 5 제품",
    ]
    for r in top_products:
        lines.append(f"- {r['name_ko']} ({r['code']}): {r['cnt']:,}건, 감성 {r['avg_score']}")

    lines.append("")
    lines.append("## 핫 카테고리 TOP 5")
    for r in top_cats:
        lines.append(f"- {r['cat']}: {r['cnt']:,}건")

    if neg_spikes:
        lines.append("")
        lines.append("## 부정 의견 다발 제품 (주의)")
        for r in neg_spikes:
            lines.append(f"- {r['name_ko']} ({r['code']}): 부정 {r['neg_cnt']:,}건")

    return "\n".join(lines)


# ── 2. alert_check() ─────────────────────────────────────────────────────────
async def alert_check_tool() -> dict:
    """현재 임계치(부정 비율, 부정 급증, 수집 정체) 상태를 요약."""
    # 임계치 (필요시 .env 로 이동 가능)
    NEG_RATIO_THRESHOLD = 0.40  # 40%
    MIN_VOLUME = 30             # 노이즈 컷
    NEG_SURGE_RATIO = 2.0       # 24h 부정이 직전 24h 의 2배 이상

    # 1) 24h 부정 비율 높은 제품
    high_neg_stmt = text("""
        WITH recent AS (
            SELECT p.code, p.name_ko,
                   COUNT(*) AS total,
                   SUM(CASE WHEN v.sentiment_label = 'negative' THEN 1 ELSE 0 END) AS neg
            FROM voc_active v JOIN products p ON p.id = v.product_id
            WHERE v.collected_at > NOW() - INTERVAL '24 hours'
            GROUP BY p.code, p.name_ko
            HAVING COUNT(*) >= :min_vol
        )
        SELECT code, name_ko, total, neg,
               ROUND((neg::numeric / NULLIF(total, 0))::numeric, 3) AS neg_ratio
        FROM recent
        WHERE (neg::numeric / NULLIF(total, 0)) >= :threshold
        ORDER BY neg_ratio DESC LIMIT 10
    """)

    # 2) 부정 급증 (24h vs 직전 24h)
    surge_stmt = text("""
        WITH cur AS (
            SELECT p.code, p.name_ko, COUNT(*) AS neg
            FROM voc_active v JOIN products p ON p.id = v.product_id
            WHERE v.collected_at > NOW() - INTERVAL '24 hours'
              AND v.sentiment_label = 'negative'
            GROUP BY p.code, p.name_ko
        ),
        prev AS (
            SELECT p.code, COUNT(*) AS neg_prev
            FROM voc_active v JOIN products p ON p.id = v.product_id
            WHERE v.collected_at > NOW() - INTERVAL '48 hours'
              AND v.collected_at <= NOW() - INTERVAL '24 hours'
              AND v.sentiment_label = 'negative'
            GROUP BY p.code
        )
        SELECT cur.code, cur.name_ko, cur.neg AS neg_24h,
               COALESCE(prev.neg_prev, 0) AS neg_prev_24h,
               CASE WHEN COALESCE(prev.neg_prev, 0) = 0 THEN NULL
                    ELSE ROUND((cur.neg::numeric / prev.neg_prev)::numeric, 2)
               END AS surge_ratio
        FROM cur LEFT JOIN prev ON cur.code = prev.code
        WHERE cur.neg >= :min_vol
          AND (prev.neg_prev IS NULL
               OR cur.neg::numeric / NULLIF(prev.neg_prev, 0) >= :surge)
        ORDER BY cur.neg DESC LIMIT 10
    """)

    # 3) 수집 정체 (12h 이상 신규 없는 플랫폼)
    stale_stmt = text("""
        SELECT pl.code, pl.name,
               MAX(v.collected_at) AS last_seen,
               EXTRACT(EPOCH FROM (NOW() - MAX(v.collected_at))) / 3600 AS hours_since
        FROM platforms pl LEFT JOIN voc_active v ON v.platform_id = pl.id
        GROUP BY pl.code, pl.name
        HAVING MAX(v.collected_at) IS NULL
            OR MAX(v.collected_at) < NOW() - INTERVAL '12 hours'
        ORDER BY hours_since DESC NULLS FIRST LIMIT 15
    """)

    async with get_db_session() as db:
        high_neg = (await db.execute(high_neg_stmt, {
            "min_vol": MIN_VOLUME, "threshold": NEG_RATIO_THRESHOLD
        })).mappings().all()
        surge = (await db.execute(surge_stmt, {
            "min_vol": MIN_VOLUME, "surge": NEG_SURGE_RATIO
        })).mappings().all()
        stale = (await db.execute(stale_stmt)).mappings().all()

    return {
        "thresholds": {
            "neg_ratio": NEG_RATIO_THRESHOLD,
            "min_volume": MIN_VOLUME,
            "neg_surge_ratio": NEG_SURGE_RATIO,
            "stale_hours": 12,
        },
        "high_negative_ratio": [
            {
                "product_code": r["code"], "product_name": r["name_ko"],
                "total": r["total"], "negative": r["neg"],
                "neg_ratio": float(r["neg_ratio"]),
            } for r in high_neg
        ],
        "negative_surge": [
            {
                "product_code": r["code"], "product_name": r["name_ko"],
                "neg_24h": r["neg_24h"], "neg_prev_24h": r["neg_prev_24h"],
                "surge_ratio": float(r["surge_ratio"]) if r["surge_ratio"] is not None else None,
            } for r in surge
        ],
        "stale_platforms": [
            {
                "platform_code": r["code"], "platform_name": r["name"],
                "last_seen": r["last_seen"].isoformat() if r["last_seen"] else None,
                "hours_since": round(float(r["hours_since"]), 1) if r["hours_since"] else None,
            } for r in stale
        ],
        "summary": (
            f"부정비율 경보 {len(high_neg)}건 · 부정급증 {len(surge)}건 · "
            f"수집정체 플랫폼 {len(stale)}개"
        ),
    }


# ── 3. site_health() ─────────────────────────────────────────────────────────
async def site_health_tool() -> List[dict]:
    """플랫폼(사이트)별 최근 24h 활동 현황."""
    stmt = text("""
        SELECT
            pl.code, pl.name, pl.region,
            COUNT(v.id) AS voc_24h,
            COUNT(v.id) FILTER (WHERE v.sentiment_label = 'negative') AS neg_24h,
            ROUND(AVG(v.sentiment_score)::numeric, 3) AS avg_score,
            MAX(v.collected_at) AS last_collected,
            EXTRACT(EPOCH FROM (NOW() - MAX(v.collected_at))) / 3600 AS hours_since_last
        FROM platforms pl
        LEFT JOIN voc_active v
               ON v.platform_id = pl.id
              AND v.collected_at > NOW() - INTERVAL '24 hours'
        GROUP BY pl.code, pl.name, pl.region
        ORDER BY voc_24h DESC, pl.code
    """)

    async with get_db_session() as db:
        rows = (await db.execute(stmt)).mappings().all()

    out = []
    for r in rows:
        hrs = r["hours_since_last"]
        last = r["last_collected"]
        if r["voc_24h"] and r["voc_24h"] > 0:
            status = "healthy"
        elif hrs is not None and hrs <= 24:
            status = "quiet"
        elif hrs is None:
            status = "no_data_ever"
        else:
            status = "stale"

        out.append({
            "platform_code": r["code"],
            "platform_name": r["name"],
            "region": r["region"],
            "voc_24h": r["voc_24h"] or 0,
            "negative_24h": r["neg_24h"] or 0,
            "avg_sentiment": float(r["avg_score"]) if r["avg_score"] is not None else None,
            "last_collected": last.isoformat() if last else None,
            "hours_since_last": round(float(hrs), 1) if hrs is not None else None,
            "status": status,
        })
    return out


# ── 4. top_emerging_keywords(period_days) ────────────────────────────────────
async def top_emerging_keywords_tool(
    period_days: int = 7,
    product_code: Optional[str] = None,
    top_n: int = 20,
) -> dict:
    """기간 내 한국어/영어 키워드를 토큰화하여 빈도 TOP N 추출."""
    conds = ["v.collected_at > NOW() - make_interval(days => :days)"]
    params: dict = {"days": period_days, "row_limit": 8000}

    if product_code:
        conds.append("p.code = :product_code")
        params["product_code"] = product_code.upper()

    where = " AND ".join(conds)
    stmt = text(f"""
        SELECT v.content_original, v.content_translated, v.language_detected
        FROM voc_active v
        JOIN products p ON p.id = v.product_id
        WHERE {where}
          AND COALESCE(v.content_original, v.content_translated, '') <> ''
        ORDER BY v.collected_at DESC
        LIMIT :row_limit
    """)

    async with get_db_session() as db:
        rows = (await db.execute(stmt, params)).mappings().all()

    ko_counter: Counter = Counter()
    en_counter: Counter = Counter()
    sampled = 0

    for r in rows:
        original = r["content_original"] or ""
        translated = r["content_translated"] or ""
        ko_t, en_t = _tokenize(original)
        ko_counter.update(ko_t)
        # 영어는 번역본 + 원문 둘 다 활용 (번역본이 영어인 경우 많음)
        _, en_from_trans = _tokenize(translated)
        en_counter.update(en_t + en_from_trans)
        sampled += 1

    return {
        "period_days": period_days,
        "product_code": product_code.upper() if product_code else None,
        "sampled_records": sampled,
        "top_korean": [
            {"keyword": k, "count": c} for k, c in ko_counter.most_common(top_n)
        ],
        "top_english": [
            {"keyword": k, "count": c} for k, c in en_counter.most_common(top_n)
        ],
    }
