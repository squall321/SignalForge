"""
LLM Grounding — JSON payload 를 LLM 이 따라가기 쉽도록 markdown table 로 변환하고,
LLM 출력이 payload 의 핵심 수치/키워드를 인용했는지 검증.

핵심 원리:
- LLM(qwen 등 소형 모델 포함)은 prose 안의 숫자를 자유롭게 변조한다.
  payload 를 "표(table)" 로 보여주면 모델이 표를 따라가며 수치를 그대로
  인용하는 경향이 강해진다.
- 출력 후 validate_response 로 핵심 수치/키워드가 인용됐는지 점수화 (0~1).
  점수가 낮으면 호출자가 1회 재요청.

설계:
- 입력 shape 두 가지를 모두 지원
    A) temporal series_payload: { series:[...], events:[...], changepoints:[...], meta:{} }
    B) daily_insight DailyMetrics-like dict: { target_date, total, by_sentiment, by_category, by_product, by_platform, ... }
- 함수는 순수(pure): I/O 없음, 결정적, 테스트 친화.

P4.1 강화:
- 모든 수치 + 키워드(코드/이름) 통합 grounding 점수.
- 숫자는 **bold** 로 강조한 markdown 표.
- 컬럼명에 한국어 별칭(건수/비율(%)/변화율(%pp)) 병기.
- build_fewshot_examples: payload 모양에 따라 2개 예시 자동 생성.

API:
    metrics_to_markdown(payload, schema_desc=None) -> str
    extract_key_numbers(payload) -> List[str]
    extract_key_terms(payload) -> List[str]
    validate_response(text, payload, *, min_required=1) -> float  # 0~1
    contains_hanzi(text) -> bool
    build_fewshot_examples(payload) -> str
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set


# ────────────────────────────────────────────────────────────────────────────
# 1) markdown 변환
# ────────────────────────────────────────────────────────────────────────────
def _fmt_num(x: Any) -> str:
    """숫자 → 천 단위 콤마 또는 소수 3자리.  None/문자 → 원본."""
    if isinstance(x, bool):
        return str(x)
    if isinstance(x, int):
        return f"{x:,}"
    if isinstance(x, float):
        if abs(x) >= 100:
            return f"{x:,.2f}"
        return f"{x:.3f}"
    return str(x) if x is not None else ""


def _fmt_cell(x: Any, *, bold_numbers: bool = False) -> str:
    """표의 셀 값 포맷. bold_numbers=True 이면 정수/실수에 **bold** 적용."""
    s = _fmt_num(x)
    if not bold_numbers:
        return s
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return f"**{s}**"
    return s


# 컬럼 헤더 한국어 별칭 — P4.1: LLM 이 한국어 컬럼명을 그대로 인용하도록 유도.
_HEADER_KO: Dict[str, str] = {
    "n": "건수",
    "count": "건수",
    "neg": "부정 건수",
    "pos": "긍정 건수",
    "neg_rate(%)": "부정 비율(%)",
    "pos_rate(%)": "긍정 비율(%)",
    "sent_avg": "평균 감성",
    "|Δ|": "변화율(%pp)",
    "magnitude": "변화율(%pp)",
    "name_ko": "한국어 이름",
    "name": "이름",
    "code": "코드",
    "cc": "국가 코드",
    "region": "지역",
    "platform": "플랫폼",
    "product": "제품",
    "score": "감성 점수",
    "text": "문장",
    "type": "유형",
    "title": "제목",
    "metric": "지표",
    "direction": "방향",
    "key": "키",
    "value": "값",
    "date": "일자",
    "label": "라벨",
}


def _ko_header(h: str) -> str:
    """헤더 라벨 → 한국어 병기. 매핑 없으면 원본 유지."""
    ko = _HEADER_KO.get(h)
    return f"{h}({ko})" if ko else h


def _table(
    headers: Sequence[str],
    rows: Iterable[Sequence[Any]],
    *,
    bold_numbers: bool = True,
    ko_headers: bool = True,
) -> str:
    """간단 markdown table.  rows 가 0개면 '(없음)' 반환.

    P4.1: 기본적으로 숫자 셀은 **bold**, 헤더에는 한국어 별칭 병기.
    """
    rows_list = list(rows)
    if not rows_list:
        return "(없음)"
    head_labels = [_ko_header(h) if ko_headers else h for h in headers]
    h = "| " + " | ".join(head_labels) + " |"
    sep = "| " + " | ".join("---" for _ in headers) + " |"
    body = "\n".join(
        "| " + " | ".join(_fmt_cell(c, bold_numbers=bold_numbers) for c in r) + " |"
        for r in rows_list
    )
    return "\n".join([h, sep, body])


def _series_table(series: List[Dict[str, Any]], limit: int = 60) -> str:
    if not series:
        return "(시계열 데이터 없음)"
    rows = []
    for p in series[:limit]:
        rows.append(
            [
                p.get("date"),
                p.get("count", 0),
                p.get("sent_avg", 0),
                p.get("neg_rate", 0),
                p.get("pos_rate", 0),
            ]
        )
    return _table(["date", "count", "sent_avg", "neg_rate(%)", "pos_rate(%)"], rows)


def metrics_to_markdown(payload: Dict[str, Any], schema_desc: Optional[str] = None) -> str:
    """payload → markdown 표 모음.  LLM 프롬프트에 그대로 삽입.

    입력 shape 가 series 형이면 시계열/이벤트/change-points/meta 표를,
    daily metrics 형이면 카테고리/제품/플랫폼/감성 분포 표를 생성.

    schema_desc: 표 위에 띄울 한 줄 설명 (모델이 헷갈리지 않게).
    """
    if not isinstance(payload, dict):
        return "(payload 형식 오류 — dict 필요)"

    out: List[str] = []
    if schema_desc:
        out.append(f"> {schema_desc}")
        out.append("")

    # ── A) temporal series payload ───────────────────────────────────
    if "series" in payload or "changepoints" in payload:
        meta = payload.get("meta") or {}
        if meta:
            out.append("### meta")
            out.append(_table(["key", "value"], [[k, v] for k, v in meta.items()]))
            out.append("")

        series = payload.get("series") or []
        if series:
            counts = [int(p.get("count", 0)) for p in series]
            sents = [float(p.get("sent_avg", 0)) for p in series]
            total = sum(counts)
            avg_sent = (sum(sents) / len(sents)) if sents else 0.0
            peak = max(series, key=lambda p: int(p.get("count", 0)))
            trough = min(series, key=lambda p: int(p.get("count", 0)))
            out.append("### 요약 통계")
            out.append(
                _table(
                    ["metric", "value"],
                    [
                        ["total_count", total],
                        ["avg_sent", round(avg_sent, 4)],
                        ["peak_date", peak.get("date")],
                        ["peak_count", peak.get("count", 0)],
                        ["trough_date", trough.get("date")],
                        ["trough_count", trough.get("count", 0)],
                        ["points", len(series)],
                    ],
                )
            )
            out.append("")
            out.append("### 시계열")
            out.append(_series_table(series))
            out.append("")

        events = payload.get("events") or []
        if events:
            rows = [
                [
                    ev.get("date") or ev.get("event_date"),
                    ev.get("type") or ev.get("event_type"),
                    ev.get("title"),
                    ev.get("product_code"),
                ]
                for ev in events[:20]
            ]
            out.append("### 이벤트")
            out.append(_table(["date", "type", "title", "product"], rows))
            out.append("")

        changepoints = payload.get("changepoints") or []
        if changepoints:
            rows = [
                [
                    cp.get("date"),
                    cp.get("metric"),
                    cp.get("direction"),
                    cp.get("magnitude"),
                ]
                for cp in changepoints[:20]
            ]
            out.append("### 변곡점")
            out.append(_table(["date", "metric", "direction", "|Δ|"], rows))
            out.append("")
        return "\n".join(out).strip()

    # ── B) daily metrics-like payload ─────────────────────────────────
    if "target_date" in payload or "by_sentiment" in payload or "by_product" in payload:
        out.append("### 메타")
        meta_rows = []
        if "target_date" in payload:
            meta_rows.append(["target_date", payload["target_date"]])
        if "total" in payload:
            meta_rows.append(["total", payload["total"]])
        if payload.get("sentiment_score_avg") is not None:
            meta_rows.append(["sentiment_score_avg", payload["sentiment_score_avg"]])
        if payload.get("avg_engagement") is not None:
            meta_rows.append(["avg_engagement", payload["avg_engagement"]])
        out.append(_table(["key", "value"], meta_rows))
        out.append("")

        if payload.get("by_sentiment"):
            rows = sorted(
                payload["by_sentiment"].items(), key=lambda x: -x[1]
            )
            out.append("### 감성 분포")
            out.append(_table(["label", "n"], rows))
            out.append("")

        for sec_key, sec_title, cols in [
            ("by_category", "카테고리 TOP (전체)", ["code", "name_ko", "n"]),
            ("by_category_neg", "부정 카테고리 TOP", ["code", "name_ko", "n"]),
            ("by_product", "제품 TOP", ["code", "name_ko", "n", "neg", "pos"]),
            ("by_platform", "플랫폼 TOP", ["code", "name", "region", "n", "neg"]),
            ("by_country", "국가 분포", ["cc", "n"]),
            ("new_products_today", "신규 제품", ["code", "name_ko"]),
        ]:
            data = payload.get(sec_key) or []
            if not data:
                continue
            rows = [[item.get(c) for c in cols] for item in data]
            out.append(f"### {sec_title}")
            out.append(_table(cols, rows))
            out.append("")

        for sec_key, sec_title in [
            ("top_negative", "부정 대표 문장"),
            ("top_positive", "긍정 대표 문장"),
        ]:
            data = payload.get(sec_key) or []
            if not data:
                continue
            rows = [
                [
                    item.get("product"),
                    item.get("platform"),
                    item.get("score"),
                    item.get("text"),
                ]
                for item in data
            ]
            out.append(f"### {sec_title}")
            out.append(_table(["product", "platform", "score", "text"], rows))
            out.append("")
        return "\n".join(out).strip()

    # 알 수 없는 shape → 키 나열만
    out.append("### payload 키 목록")
    out.append(_table(["key", "type"], [[k, type(v).__name__] for k, v in payload.items()]))
    return "\n".join(out).strip()


# ────────────────────────────────────────────────────────────────────────────
# 2) 핵심 수치 추출
# ────────────────────────────────────────────────────────────────────────────
def _add_num(s: Set[str], n: Any) -> None:
    """정수 → 포맷 콤마/비콤마 양쪽, float → 소수 자리 두 가지를 모두 후보 등록."""
    if n is None:
        return
    if isinstance(n, bool):
        return
    if isinstance(n, int):
        s.add(str(n))
        s.add(f"{n:,}")
    elif isinstance(n, float):
        if abs(n - round(n)) < 1e-9:
            s.add(str(int(round(n))))
            s.add(f"{int(round(n)):,}")
        else:
            # 일반적 자리수 후보
            s.add(f"{n:.2f}")
            s.add(f"{n:.3f}")
            s.add(f"{n:.1f}")


def extract_key_numbers(payload: Dict[str, Any]) -> List[str]:
    """payload 에서 LLM 이 인용해야 하는 핵심 수치를 추출.

    P4.1 강화: TOP 3 → TOP 5, neg/pos 값 / total 모두 포함하여 grounding 측정 범위 확대.

    추출 대상:
    - series: total / peak count / peak date / trough count
    - events: 첫 이벤트 날짜
    - changepoints: 각 |Δ| 정수부
    - by_product/by_category/by_platform: TOP 5 의 n / neg / pos
    - total / by_sentiment 값들
    """
    s: Set[str] = set()
    if not isinstance(payload, dict):
        return []

    series = payload.get("series") or []
    if series:
        counts = [int(p.get("count", 0)) for p in series]
        _add_num(s, sum(counts))
        peak = max(series, key=lambda p: int(p.get("count", 0)))
        trough = min(series, key=lambda p: int(p.get("count", 0)))
        _add_num(s, int(peak.get("count", 0)))
        _add_num(s, int(trough.get("count", 0)))
        if peak.get("date"):
            s.add(str(peak["date"]))

    for cp in (payload.get("changepoints") or [])[:5]:
        mag = cp.get("magnitude")
        if isinstance(mag, (int, float)):
            _add_num(s, int(round(mag)))
        if cp.get("date"):
            s.add(str(cp["date"]))

    for ev in (payload.get("events") or [])[:5]:
        d = ev.get("date") or ev.get("event_date")
        if d:
            s.add(str(d))

    if "total" in payload:
        _add_num(s, payload["total"])
    for k, v in (payload.get("by_sentiment") or {}).items():
        _add_num(s, v)
    for sec in ("by_product", "by_category", "by_category_neg", "by_platform"):
        for item in (payload.get(sec) or [])[:5]:
            _add_num(s, item.get("n"))
            _add_num(s, item.get("neg"))
            _add_num(s, item.get("pos"))

    # 빈 문자열 제거
    return sorted(x for x in s if x)


def extract_key_terms(payload: Dict[str, Any]) -> List[str]:
    """LLM 이 인용해야 하는 핵심 키워드(코드/이름) 목록.

    카테고리/제품/플랫폼 TOP 의 code 와 한국어 이름을 둘 다 후보로 등록.
    grounding 점수 계산 시 숫자와 별개 dimension 으로 집계한다.
    """
    if not isinstance(payload, dict):
        return []
    terms: Set[str] = set()
    for sec in ("by_product", "by_category", "by_category_neg"):
        for item in (payload.get(sec) or [])[:5]:
            for k in ("code", "name_ko"):
                v = item.get(k)
                if isinstance(v, str) and v.strip():
                    terms.add(v.strip())
    for item in (payload.get("by_platform") or [])[:5]:
        for k in ("code", "name"):
            v = item.get(k)
            if isinstance(v, str) and v.strip():
                terms.add(v.strip())
    for item in (payload.get("new_products_today") or [])[:5]:
        for k in ("code", "name_ko"):
            v = item.get(k)
            if isinstance(v, str) and v.strip():
                terms.add(v.strip())
    return sorted(terms)


# ────────────────────────────────────────────────────────────────────────────
# 3) 응답 검증
# ────────────────────────────────────────────────────────────────────────────
_HANZI_RE = re.compile(r"[一-鿿]")


def contains_hanzi(text: str) -> bool:
    """한자(또는 일본 한자 — 동일 코드포인트) 포함 여부."""
    if not text:
        return False
    return bool(_HANZI_RE.search(text))


def validate_response(
    text: str,
    payload: Dict[str, Any],
    *,
    min_required: int = 1,
) -> float:
    """text 가 payload 의 핵심 수치/키워드를 얼마나 인용했는지 점수화.

    반환: 0.0 ~ 1.0
    - hanzi 포함 시 페널티 *0.5
    - text 가 비어있거나 너무 짧으면 0
    - min_required: 0이 아닌 점수를 받기 위한 최소 hit 수 (숫자 + 키워드 합산)

    P4.1 스코어 = 0.7 * (num_hit / num_total) + 0.3 * (term_hit / term_total)
    숫자 grounding 을 주, 키워드 grounding 을 보조 dimension 으로 가중치.
    실용적으로 0.5 이상이면 "수치 grounded" 로 본다.
    """
    if not text or len(text.strip()) < 20:
        return 0.0
    nums = extract_key_numbers(payload)
    terms = extract_key_terms(payload)
    if not nums and not terms:
        return 1.0  # 검증 가능한 토큰이 없으면 통과 처리.

    num_hits = sum(1 for n in nums if n in text) if nums else 0
    term_hits = sum(1 for t in terms if t in text) if terms else 0
    total_hits = num_hits + term_hits

    if total_hits < min_required:
        return 0.0

    num_ratio = (num_hits / len(nums)) if nums else None
    term_ratio = (term_hits / len(terms)) if terms else None

    if num_ratio is not None and term_ratio is not None:
        score = 0.7 * num_ratio + 0.3 * term_ratio
    elif num_ratio is not None:
        score = num_ratio
    else:
        score = term_ratio or 0.0

    if contains_hanzi(text):
        score *= 0.5
    return round(min(score, 1.0), 4)


# ────────────────────────────────────────────────────────────────────────────
# 4) few-shot 예시 자동 생성
# ────────────────────────────────────────────────────────────────────────────
def build_fewshot_examples(payload: Dict[str, Any]) -> str:
    """payload 의 실제 값을 토대로 2개의 few-shot 예시를 자동 생성.

    예시는 서로 다른 카테고리(제품 vs 카테고리, 또는 series vs daily)에서 뽑아
    LLM 이 "표 값 → bold 인용" 패턴을 학습하도록 한다.
    payload 가 비어있으면 generic 예시 2개 반환.

    출력 형식: markdown bullet 2개 (각 1-2문장, **bold** 강조).
    """
    if not isinstance(payload, dict):
        return ""

    examples: List[str] = []

    # series payload
    if payload.get("series"):
        series = payload["series"]
        counts = [int(p.get("count", 0)) for p in series]
        total = sum(counts)
        peak = max(series, key=lambda p: int(p.get("count", 0)))
        examples.append(
            f"- 입력 표: series total={_fmt_num(total)}, "
            f"peak={_fmt_num(peak.get('count',0))} ({peak.get('date')})\n"
            f"  → 올바른 출력: '관측 기간 총 **{_fmt_num(total)}건**이 수집되었으며, "
            f"**{peak.get('date')}**에 **{_fmt_num(peak.get('count',0))}건**으로 정점을 기록했습니다.'"
        )
        cps = payload.get("changepoints") or []
        if cps:
            cp = cps[0]
            mag = cp.get("magnitude")
            mag_s = _fmt_num(int(round(mag))) if isinstance(mag, (int, float)) else str(mag)
            examples.append(
                f"- 입력 표: changepoints[0]={cp.get('metric')} {cp.get('direction')} |Δ|={mag_s}\n"
                f"  → 올바른 출력: '**{cp.get('date')}** 에 지표 {cp.get('metric')} 가 "
                f"**{mag_s}** 만큼 {cp.get('direction')} 방향 변곡을 일으켰습니다.'"
            )

    # daily payload
    if "total" in payload or payload.get("by_product"):
        total = payload.get("total")
        if total is not None:
            neg = (payload.get("by_sentiment") or {}).get("negative")
            neg_str = f", 부정 **{_fmt_num(neg)}건**" if neg is not None else ""
            examples.append(
                f"- 입력 표: total={_fmt_num(total)}{', negative=' + _fmt_num(neg) if neg is not None else ''}\n"
                f"  → 올바른 출력: '어제 수집 총량은 **{_fmt_num(total)}건**{neg_str} 이었습니다.'"
            )
        prod = (payload.get("by_product") or [None])[0]
        if prod:
            n = prod.get("n")
            ng = prod.get("neg")
            examples.append(
                f"- 입력 표: by_product TOP1 code={prod.get('code')} name_ko={prod.get('name_ko')} "
                f"n={_fmt_num(n)} neg={_fmt_num(ng)}\n"
                f"  → 올바른 출력: '제품 TOP 1 은 {prod.get('name_ko')}({prod.get('code')})로 "
                f"**{_fmt_num(n)}건** (부정 **{_fmt_num(ng)}건**) 이었습니다.'"
            )
        cat = (payload.get("by_category_neg") or payload.get("by_category") or [None])[0]
        if cat:
            n = cat.get("n")
            examples.append(
                f"- 입력 표: by_category TOP1 code={cat.get('code')} name_ko={cat.get('name_ko')} n={_fmt_num(n)}\n"
                f"  → 올바른 출력: '부정 카테고리 1위는 {cat.get('name_ko')}({cat.get('code')})로 "
                f"**{_fmt_num(n)}건** 이었습니다.'"
            )

    # 서로 다른 카테고리에서 최대 2개 선택 (중복 제거 후 앞에서 2개).
    seen: Set[str] = set()
    unique: List[str] = []
    for ex in examples:
        key = ex.split("→", 1)[0]
        if key in seen:
            continue
        seen.add(key)
        unique.append(ex)
        if len(unique) >= 2:
            break

    if not unique:
        # generic 폴백.
        unique = [
            "- 입력 표 에 'total=10,000' 이 있으면 출력은 '수집 총량은 **10,000건** 입니다.'",
            "- 입력 표 에 'by_product TOP1 GS25 n=300' 이 있으면 출력은 '제품 TOP 1 은 GS25 로 **300건** 입니다.'",
        ]
    return "\n".join(unique)


__all__ = [
    "metrics_to_markdown",
    "extract_key_numbers",
    "extract_key_terms",
    "validate_response",
    "contains_hanzi",
    "build_fewshot_examples",
]
