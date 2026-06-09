"""Workflow validator — 워크플로우 보고서의 **수치 표기 vs 실측** 자동 동기화.

R21 권고 2 (workflow 자기 보고 자동 동기화) 의 본체. R21 트랙 B `loc_validator`
가 *코드 LoC* 만 다뤘다면, 본 모듈은 *데이터·평가 수치* 를 다룬다 — voc 총수,
linked, sentiment %, topic %, F1 (overall + per-topic), regression baseline 등.

설계 원칙
=========
1. 보고서 (`docs/dashboard/R*.md`) 를 *문자열* 단위로 파싱 — 정규식 기반,
   외부 의존 없음. 같은 호스트 (loc_validator) 패턴을 따른다.
2. 측정은 *backend HTTP endpoint* + *옵션 JSON 파일* 만 사용 — psql 직접 접속
   제거 (테스트성·이식성). backend 가 내려가 있으면 `available=False` 로
   graceful 표기, alert 미발생.
3. drift = (actual - reported) / max(|reported|, |actual|, eps).
   |drift| > threshold (기본 10%) → alert.
4. 옵션 ``--inject`` 모드: 보고서 본문 끝에
   ``> 워크플로우 자동 동기화 (R<N>): 보고 X / 실측 Y / drift Z%``
   라인을 *idempotent* 하게 append. 이미 같은 round 의 동기화 블록이 있으면
   덮어쓴다 (위치는 보고서 마지막). 절대 데이터/원본 본문은 수정하지 않는다.

지원 지표 패턴
==============
다음과 같은 "지표명 + 숫자" 패턴을 보고서 본문에서 추출.

* ``voc_total | voc 총수 | voc 전체 | total voc`` → DB ``voc_records`` 총수
* ``linked | linked_total | 매핑된`` → ``voc_records.product_id IS NOT NULL``
* ``sentiment %`` → ``analyzable / voc_total * 100`` 의 sentiment 채움률
  *(보고서 한국어 관습상 100%면 거의 모두 채움 의미)* — 본 모듈은 단순히
  ``coverage-status`` 의 ``analyzable_pct`` 로 *근사* 비교. 정확한 sentiment%
  endpoint 가 없는 현 시점의 차선책. (정확 측정 endpoint 추가 시 교체)
* ``topic %`` → ``coverage-status.analyzable_pct`` 와 동일 근사 — 둘 다
  "비고: 분모 = voc_total". 추후 분리 시 교체.
* ``F1 (overall) | overall F1`` → 가장 최근 ``reports/topic_eval_*.json`` 의
  ``overall_accuracy_r*`` 또는 ``overall_accuracy_*`` 필드 (라운드별 키 변동에
  유연하게 대응).
* ``regression baseline`` 값들 (GN7, GZF1, GS22, GS25, GB3, hn_linked_pct,
  topics_filled, products_count, hn_total, voc_total) → ``regression-baseline``
  endpoint 의 ``current`` 값과 직접 비교.

본 모듈이 *명시적으로 지원하지 않는* 지표 (예: agree rate 25%) 는 보고서에
값이 등장해도 무시 — 측정 정의가 라운드별로 변동·휘발성이라 자동 동기화에 부적합.
이런 항목은 보고서 작성자가 명시적으로 기록해야 한다.

CLI 사용
========

.. code-block:: bash

    # R20 보고서의 수치 vs 실측 비교 (alert 만 종료코드 1)
    python -m insight.workflow_validator --rounds R20

    # 자동 동기화 라인을 보고서 끝에 삽입 (idempotent)
    python -m insight.workflow_validator --rounds R20 --inject

    # JSON 출력 (CI/dashboard)
    python -m insight.workflow_validator --all --json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
import urllib.error
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── repo 루트 ─────────────────────────────────────────────────────────────
# crawler/insight/workflow_validator.py → repo_root
REPO_ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR_DEFAULT = REPO_ROOT / "docs" / "dashboard"
TOPIC_EVAL_DIR_DEFAULT = REPO_ROOT / "reports"

DEFAULT_BACKEND = os.getenv("WORKFLOW_VALIDATOR_BACKEND", "http://localhost:8000")
DEFAULT_THRESHOLD = 0.10  # ±10% (LoC 의 20% 보다 엄격 — 데이터 수치는 정확해야)

# R25 트랙 B: 메타-루프 재귀 cap 환경변수화.
# 기본값 3 (R23 도입 당시) → 환경변수 META_CAP 로 5 등 확장 허용.
# 안전 가드: 1 ≤ cap ≤ 10 — 음수/0 또는 과도한 cap (무한 재귀 위험) 차단.
def _meta_cap_default() -> int:
    raw = os.getenv("META_CAP", "3")
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return 3
    if v < 1:
        return 1
    if v > 10:
        return 10
    return v


DEFAULT_META_CAP = _meta_cap_default()


# R27 트랙 E: 메타 파서 L6+ hard cap 가드.
# ----------------------------------------
# `_meta_cap_default()` / `validate_meta` 의 ``max(1, min(10, ...))`` 클램프는
# 1..10 *밖* 값을 *조용히 절단* 하기만 한다. 운영자가 의도적으로 cap=11 을
# 요청해도 silent 하게 10으로 줄어들어 *의도 vs 실행* 갭이 발생 — 사고 시
# 원인 추적이 어렵다 (R26 권고 5: L6+ 가드).
#
# 본 hard cap 은 *명시적* 차단:
#   - 환경변수 ``META_HARD_CAP`` (기본 10) 으로 절대 상한 설정.
#   - 요청 cap > hard cap → RuntimeError + audit alert (런타임 즉시 실패).
#   - 정상 사용 (cap ≤ hard cap) 은 기존 동작과 완전 동일 — 회귀 없음.
def _meta_hard_cap() -> int:
    """``META_HARD_CAP`` 환경변수 값 (기본 10). 1 미만은 1로 강제."""
    raw = os.getenv("META_HARD_CAP", "10")
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return 10
    return max(1, v)


# 고정점 (fixed-point) 감지 임계치 — 메타-루프가 더 이상 새 정보를 만들지
# 못할 때 (drift 변동 < FIXED_POINT_EPS) 조기 종료. L > 1 부터 적용 — L1 은
# 초기 측정으로 비교 대상 없음.
FIXED_POINT_EPS = 1e-3  # 0.001% (= 1e-5 ratio)


# ── 보고서 패턴 ────────────────────────────────────────────────────────────
# 모두 *행 단위* 파싱. 라인 안에 키워드 + 숫자 등장 시 캡처. 본문 어디든 등장 가능.
# (라운드별 표/문장 형식 변동에 강건)
#
# 캡처 정책: 같은 라인에 키워드가 여러 번 나오면 *마지막* 숫자 우선 (보고서가
# "R19 → R20: 117,698 → 118,430" 식으로 표기할 때 *최신 값*).
def _re(s: str) -> re.Pattern:
    return re.compile(s, re.IGNORECASE)

# 숫자 + 단위 : 117,958  /  88.50  /  88.5%  /  0.500
_NUM = r"([0-9]+(?:[,_][0-9]{3})*(?:\.[0-9]+)?)"

# 정밀 패턴 — 보고서 narrative 의 우연한 단어 결합 (예: "topic 22" 라는 임의 표현)
# 을 alert 로 잘못 분류하지 않도록 *키워드 직후 / 같은 셀* 만 매칭. 즉 "kw=number"
# 또는 "kw: number" 또는 "kw | number" 또는 "kw number" 의 *짧은 거리* 만.
# (분모 거리가 너무 멀면 무관한 숫자를 잡음)
#
# 수치 다음에 의미가 명확한 *단위 어휘* 가 따라붙으면 정확도 상승. 본문 양식 다양성을
# 흡수하기 위해 두 모드를 제공:
#   - 표 셀 형식: `| voc_total | 117,958 |`
#   - 본문 인라인: `voc_total = 117,958`  /  `voc 전체 117,958건`
#
# 핵심 정책: 키워드와 숫자 사이 거리 ≤ 8자 (공백/등호/콜론/파이프/탭 만).
_GAP = r"[\s=:|\t#]{0,8}"

_VOC_TOTAL_RE = _re(rf"(?:voc[_ ]?total|voc\s*총수|voc\s*전체|total\s*voc){_GAP}{_NUM}")
# linked 는 *그 자체* 가 흔한 영문 단어 — 숫자 ≥ 1000 만 인정 (보고서 narrative 작은 숫자 차단).
_LINKED_RE = _re(rf"(?:linked[_ ]?total|linked|매핑\s*된?\s*voc){_GAP}([0-9]{{1,3}}(?:[,_][0-9]{{3}})+(?:\.[0-9]+)?|[0-9]{{4,}}(?:\.[0-9]+)?)")
# sentiment % / topic % 는 *반드시* 숫자 직후 % 가 따라붙어야 인정.
_SENTIMENT_PCT_RE = _re(rf"sentiment\s*%?{_GAP}{_NUM}\s*%")
_TOPIC_PCT_RE = _re(rf"topic\s*%?{_GAP}{_NUM}\s*%")
_F1_OVERALL_RE = _re(rf"(?:overall\s*F1|F1\s*\(?overall\)?|overall\s*accuracy){_GAP}{_NUM}")
_HN_LINKED_PCT_RE = _re(rf"(?:HN\s*linked[_ ]?%|HN\s*매칭률){_GAP}{_NUM}")
_HN_TOTAL_RE = _re(rf"(?:hn[_ ]?total|HN\s*total){_GAP}{_NUM}")
_TOPICS_FILLED_RE = _re(rf"topics?[_ ]?filled{_GAP}{_NUM}")
_PRODUCTS_RE = _re(rf"products?(?:\s*count)?{_GAP}{_NUM}")

# 라운드별 product baseline (GN7=387 형식)
_PRODUCT_CODES = ("GN7", "GZF1", "GS22", "GS25", "GB3")


def _re_product(code: str) -> re.Pattern:
    # GN7=387  /  GN7: 387  — *equals/colon* 만 인정 (자유 narrative 차단).
    # 보고서 표 형식 "GN7=387 GZF1=281" 정확히 매칭.
    return _re(rf"\b{code}\b\s*(?:voc\s*)?(?:=|:)\s*{_NUM}")


# ── "N건" 패턴 (R25 트랙 — 보고/실측 cross-check 자동 캡처) ─────────────────
#
# 배경 (R24 D postmortem)
# -----------------------
# R24 D 보고서가 "Crisis VOC 변동 0건" 으로 자기 결과를 null 주장했지만, 실측은
# +508 폭증 (R25 컨텍스트 GN7 340 / GZF1 207 / GS22U 60 / GZFL3 61 / GS20 213).
# 기존 parse_report 의 metric 풀에 *Crisis 합계 / 증감* 이 없어 blind spot 이었다.
#
# 본 섹션은 보고서의 "N건" 자체를 일반 claim 으로 추출하여 *반드시* 실측 (DB
# Crisis VOC 합계) 과 cross-check 하도록 한다.
#
# 두 단계 패턴
# ------------
#  (1) STRICT  : `**N건**`  — bold 강조된 핵심 수치 (R25 spec 요구 정규식).
#                보고서 작성자가 의도적으로 강조한 숫자 → 가장 신뢰도 높음.
#                예: "**변동 0건**", "**R18 폭락 재발 0건**"
#  (2) CRISIS  : 동일 라인에 Crisis 키워드(crisis | GN7/GZF1/GS22U/GZFL3/GS20)
#                AND 변동/추가/신규/증감/증가/감소/delta 키워드 + N건 동시 출현.
#                bold 가 빠져도 narrative 안에 매장된 핵심 claim 을 포착.
#                R24 line 40 "Crisis VOC 변동 0건" 가 정확히 이 케이스.
#
# 두 경우 모두 *원시* N 값만 추출한다. 실측 baseline 은 `live` 의 `crisis_voc_sum`
# (5 product code 합계) 로 비교. metric 이름은 라인 컨텍스트로 결정:
#   - bold + crisis 컨텍스트  → "crisis_geon_bold"
#   - bold + non-crisis       → "geon_bold"               (cross-check 미수행, 표기만)
#   - crisis + 변동 컨텍스트  → "crisis_delta_geon"
# 측정값이 없으면 actual=None, drift=None, alert 미발생 (기존 정책 일치).
_BOLD_GEON_RE = _re(r"\*\*\s*([+\-]?[0-9]+(?:[,_][0-9]{3})*)\s*건\s*\*\*")
_CRISIS_LINE_RE = _re(r"\b(?:crisis|GN7|GZF1|GS22U|GZFL3|GS20)\b")
# 변동 키워드 + N건 — 키워드와 숫자 사이 거리 ≤ 8자.
_DELTA_GEON_RE = _re(
    r"(?:변동|증감|추가|신규|증가|감소|delta)"
    r"[\s:=]{0,8}([+\-]?[0-9]+(?:[,_][0-9]{3})*)\s*건"
)


# ── dataclass ────────────────────────────────────────────────────────────
@dataclass
class MetricClaim:
    """보고서 한 줄에서 추출한 단일 (metric, value) 쌍 + 실측 비교."""

    round: str
    metric: str          # canonical key: voc_total / linked / sentiment_pct / ...
    reported: Optional[float]
    actual: Optional[float]
    drift: Optional[float]
    drift_pct: Optional[float]
    source_line: int     # 보고서 내 1-based line
    alert: bool          # |drift_pct| > threshold
    note: str = ""       # 측정 가용성/한계 설명 (e.g. "backend unreachable")


@dataclass
class ReportValidation:
    round: str
    path: str
    claims: List[Dict[str, Any]] = field(default_factory=list)
    measurements: Dict[str, Any] = field(default_factory=dict)
    alerts: int = 0


# ── 측정 (backend HTTP) ─────────────────────────────────────────────────
def _http_get_json(url: str, timeout: float = 3.0) -> Optional[Dict[str, Any]]:
    """단순 HTTP GET → JSON. 실패/timeout/non-200 모두 None.

    인증/쿠키 불필요 — 모든 endpoint 가 localhost 만 허용하고 본 모듈도 localhost
    에서만 동작 (운영 정책).
    """
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            data = resp.read()
            return json.loads(data.decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def measure_live(
    backend: str = DEFAULT_BACKEND,
    *,
    topic_eval_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """현재 시점 실측값 dict.

    반환 schema::

        {
          "available": {
            "regression": true|false,
            "coverage": true|false,
            "topic_eval": true|false
          },
          "metrics": {
            "voc_total": 119981, "linked": 19534, "sentiment_pct": 88.5,
            "topic_pct": 88.5, "hn_linked_pct": 21.98, "hn_total": 34582,
            "topics_filled": 104184, "products_count": 389,
            "f1_overall": 0.5,
            "note7_voc": 391, "fold1_voc": 285, ...
          },
          "sources": {
            "voc_total": "regression-baseline",
            "f1_overall": "reports/topic_eval_2026-06-05_r20.json"
          }
        }

    backend 가 내려가 있어도 partial 결과 반환 — 호출자가 ``available`` 로 판단.
    """
    base = backend.rstrip("/")
    out_metrics: Dict[str, Optional[float]] = {}
    out_sources: Dict[str, str] = {}
    avail = {
        "regression": False, "coverage": False, "topic_eval": False,
        "crisis": False,
    }

    # 1) regression-baseline — 10 metrics
    reg = _http_get_json(f"{base}/api/v1/_internal/regression-baseline")
    if reg and isinstance(reg.get("checks"), list):
        avail["regression"] = True
        # name 매핑: 5 products + 5 globals
        name_map = {
            "note7_voc": "note7_voc",
            "fold1_voc": "fold1_voc",
            "s22_voc": "s22_voc",
            "s25_voc": "s25_voc",
            "buds3_voc": "buds3_voc",
            "hn_linked_pct": "hn_linked_pct",
            "topics_filled": "topics_filled",
            "products_count": "products_count",
            "hn_total": "hn_total",
            "voc_total": "voc_total",
        }
        # backend 가 이름 product_code 별 (GN7 등) 도 들고 있으므로 dual key 등록.
        code_alias = {
            "note7_voc": "GN7", "fold1_voc": "GZF1", "s22_voc": "GS22",
            "s25_voc": "GS25", "buds3_voc": "GB3",
        }
        for c in reg["checks"]:
            key = c.get("name")
            if key in name_map:
                val = c.get("current")
                out_metrics[name_map[key]] = val
                out_sources[name_map[key]] = "regression-baseline"
                if key in code_alias:
                    out_metrics[code_alias[key]] = val
                    out_sources[code_alias[key]] = "regression-baseline"

    # 2) coverage-status — sentiment/topic % 근사 + linked
    cov = _http_get_json(f"{base}/api/v1/_internal/coverage-status")
    if cov and isinstance(cov.get("voc_total"), (int, float)):
        avail["coverage"] = True
        out_metrics["voc_total"] = cov["voc_total"]
        out_sources["voc_total"] = (
            "coverage-status" if "voc_total" not in out_sources else out_sources["voc_total"]
        )
        out_metrics["linked"] = cov.get("linked")
        out_sources["linked"] = "coverage-status"
        # sentiment_pct/topic_pct 는 현 단계 *분석 가능 비율* 로 근사.
        # 정의 변동을 명시적으로 알리기 위해 sources 에 표기.
        out_metrics["sentiment_pct"] = cov.get("analyzable_pct")
        out_sources["sentiment_pct"] = "coverage-status.analyzable_pct (approx)"
        out_metrics["topic_pct"] = cov.get("analyzable_pct")
        out_sources["topic_pct"] = "coverage-status.analyzable_pct (approx)"

    # 3) topic_eval F1 overall — reports/topic_eval_<DATE>_<round>.json 의 최신.
    eval_dir = topic_eval_dir or TOPIC_EVAL_DIR_DEFAULT
    f1 = _load_latest_overall_f1(eval_dir)
    if f1 is not None:
        avail["topic_eval"] = True
        out_metrics["f1_overall"] = f1[1]
        out_sources["f1_overall"] = f"reports/{f1[0]}"

    # 4) Crisis 5 product VOC 합계 (R25 트랙 — "N건" 자동 cross-check 핵심).
    #    GN7 / GZF1 / GS22U / GZFL3 / GS20.  R24 D 트랙 blind spot 직접 해소.
    crisis = _http_get_json(f"{base}/api/v1/_internal/crisis-voc-sum")
    if crisis and isinstance(crisis.get("total"), (int, float)):
        avail["crisis"] = True
        out_metrics["crisis_voc_sum"] = crisis["total"]
        out_sources["crisis_voc_sum"] = "crisis-voc-sum"
        # 개별 코드도 노출 (line context 기반 비교에 사용).
        by_code = crisis.get("by_code") or {}
        if isinstance(by_code, dict):
            for code, val in by_code.items():
                if isinstance(val, (int, float)):
                    key = f"crisis_{code}"
                    out_metrics[key] = val
                    out_sources[key] = "crisis-voc-sum"

    return {
        "available": avail,
        "metrics": out_metrics,
        "sources": out_sources,
        "backend": base,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _load_latest_overall_f1(eval_dir: Path) -> Optional[Tuple[str, float]]:
    """가장 최근 topic_eval_*.json 의 overall F1 값 반환 (filename, value).

    필드 키는 라운드별 변동: ``overall_accuracy_r<N>`` 또는 ``overall_accuracy`` 또는
    상위 ``overall_f1``. 키가 없는 파일은 무시.

    정렬 기준: 파일명 자연 정렬 (R 코드의 라운드 번호 + 버전 v 식별자가 있는 경우
    버전 큰 쪽 우선). topic_eval_<date>_r<N>(_v<M>)?.json 가정.
    """
    if not eval_dir.is_dir():
        return None
    cand: List[Tuple[Tuple[int, int, str], Path]] = []
    for p in eval_dir.glob("topic_eval_*.json"):
        m = re.match(r"topic_eval_[\d\-]+_r(\d+)(?:_v(\d+))?\.json$", p.name)
        if not m:
            continue
        rnd = int(m.group(1))
        ver = int(m.group(2) or "0")
        cand.append(((rnd, ver, p.name), p))
    if not cand:
        return None
    cand.sort(key=lambda kv: kv[0], reverse=True)
    for _, path in cand:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        # 키 후보 — 라운드별 변동 흡수.
        for k in ("overall_f1", "overall_accuracy"):
            if isinstance(data.get(k), (int, float)):
                return (path.name, float(data[k]))
        for k in list(data.keys()):
            if k.startswith("overall_accuracy_") and isinstance(data.get(k), (int, float)):
                return (path.name, float(data[k]))
    return None


# ── 보고서 파싱 ──────────────────────────────────────────────────────────
def _to_float(s: str) -> Optional[float]:
    """'117,958' → 117958.0 / '88.5' → 88.5 / '88.50%' → 88.5"""
    if s is None:
        return None
    s = s.strip().rstrip("%").replace(",", "").replace("_", "")
    try:
        return float(s)
    except ValueError:
        return None


def _round_from_filename(report: Path) -> str:
    m = re.match(r"(R\d+)[_\-A-Za-z]", report.name)
    if m:
        return m.group(1)
    return report.stem


def _normalize_value(metric: str, raw: float) -> float:
    """일부 지표는 정수가 자연. 표기 통일."""
    if metric in ("voc_total", "linked", "hn_total", "topics_filled",
                  "products_count", "note7_voc", "fold1_voc", "s22_voc",
                  "s25_voc", "buds3_voc",
                  "GN7", "GZF1", "GS22", "GS25", "GB3",
                  "crisis_voc_sum", "crisis_GN7", "crisis_GZF1",
                  "crisis_GS22U", "crisis_GZFL3", "crisis_GS20",
                  "crisis_delta_geon", "geon_bold", "crisis_geon_bold"):
        return float(int(round(raw)))
    return float(raw)


def _drift(reported: Optional[float], actual: Optional[float]
           ) -> Tuple[Optional[float], Optional[float]]:
    if reported is None or actual is None:
        return None, None
    d = actual - reported
    denom = max(abs(reported), abs(actual), 1e-9)
    return d, round(d / denom, 4)


def parse_report(
    report_path: Path,
    live: Dict[str, Any],
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> List[MetricClaim]:
    """보고서 1편을 파싱하여 (metric, reported, actual, drift) 리스트 반환.

    같은 metric 이 여러 줄에 등장하면 *각 줄을 개별 claim* 으로 등록 — 보고서
    내부 모순 (예: 본문 117,698 / 표 118,430) 노출 목적.
    """
    if not report_path.is_file():
        return []
    text = report_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    round_id = _round_from_filename(report_path)
    metrics_live = live.get("metrics", {})

    claims: List[MetricClaim] = []

    def _push(metric: str, raw: str, ln: int) -> None:
        val = _to_float(raw)
        if val is None:
            return
        reported = _normalize_value(metric, val)
        actual_raw = metrics_live.get(metric)
        actual = _normalize_value(metric, float(actual_raw)) if actual_raw is not None else None
        d, dp = _drift(reported, actual)
        # 측정 정확도가 *approx* 인 metric 은 정의 차이로 인한 대용량 drift 가 자연.
        # alert 는 발화하지 않고 *경고 note* 만 부여 — 운영자가 endpoint 정밀화 후
        # threshold 검토.
        source = (live.get("sources") or {}).get(metric, "")
        is_approx = "approx" in source.lower()
        alert = bool(
            dp is not None
            and abs(dp) > threshold
            and not is_approx
        )
        note = ""
        if actual is None:
            note = "no live measurement"
        elif is_approx:
            note = f"approx source ({source}) — alert suppressed"
        claims.append(MetricClaim(
            round=round_id, metric=metric,
            reported=reported, actual=actual,
            drift=d, drift_pct=dp,
            source_line=ln, alert=alert, note=note,
        ))

    # Crisis 비교용 baseline (선행 라운드 보고서의 Crisis 합계 등). 호출자가 주입.
    # crisis_voc_sum_live: 현 시점 5 product 합계.
    # crisis_baseline:     비교 기준 (예: R23 = 373).
    # 둘 다 있으면 actual_delta = live - baseline. 없으면 drift 계산 생략.
    crisis_live = metrics_live.get("crisis_voc_sum")
    crisis_baseline = live.get("crisis_baseline")
    actual_crisis_delta: Optional[float] = None
    if isinstance(crisis_live, (int, float)) and isinstance(
        crisis_baseline, (int, float)
    ):
        actual_crisis_delta = float(crisis_live) - float(crisis_baseline)

    def _push_geon(metric: str, raw: str, ln: int, *, context: str) -> None:
        """`**N건**` / `<crisis>... N건` claim 등록.

        cross-check 정책:
          - metric `crisis_delta_geon` / `crisis_geon_bold` → actual =
            actual_crisis_delta (live sum - baseline). 없으면 None.
          - metric `geon_bold` (비-Crisis bold) → actual None, drift None.
            보고서가 강조한 핵심 수치임을 *기록* 만 한다 (운영자 검토용).
        """
        val = _to_float(raw)
        if val is None:
            return
        reported = _normalize_value(metric, val)
        actual: Optional[float] = None
        note_parts: List[str] = [f"context={context}"]
        if metric in ("crisis_delta_geon", "crisis_geon_bold"):
            if actual_crisis_delta is not None:
                actual = _normalize_value(metric, actual_crisis_delta)
                note_parts.append(
                    f"crisis_live={crisis_live} crisis_baseline={crisis_baseline} "
                    f"→ actual_delta={int(actual)}"
                )
            else:
                note_parts.append("no live crisis baseline (cross-check skipped)")
        else:  # geon_bold (비-Crisis)
            note_parts.append("no cross-check target (bold geon outside crisis context)")
        d, dp = _drift(reported, actual)
        alert = bool(dp is not None and abs(dp) > threshold)
        if actual is None and dp is None:
            note_parts.append("no live measurement")
        claims.append(MetricClaim(
            round=round_id, metric=metric,
            reported=reported, actual=actual,
            drift=d, drift_pct=dp,
            source_line=ln, alert=alert,
            note="; ".join(note_parts),
        ))

    for ln_idx, raw_line in enumerate(lines, start=1):
        line = raw_line
        # voc_total
        for m in _VOC_TOTAL_RE.finditer(line):
            _push("voc_total", m.group(1), ln_idx)
        # linked
        for m in _LINKED_RE.finditer(line):
            # voc_total 와 같은 라인이면 별개 metric 으로 인정 (캡처군 다름)
            _push("linked", m.group(1), ln_idx)
        # sentiment %
        for m in _SENTIMENT_PCT_RE.finditer(line):
            _push("sentiment_pct", m.group(1), ln_idx)
        # topic %
        for m in _TOPIC_PCT_RE.finditer(line):
            _push("topic_pct", m.group(1), ln_idx)
        # F1 overall
        for m in _F1_OVERALL_RE.finditer(line):
            _push("f1_overall", m.group(1), ln_idx)
        # HN linked %
        for m in _HN_LINKED_PCT_RE.finditer(line):
            _push("hn_linked_pct", m.group(1), ln_idx)
        # HN total
        for m in _HN_TOTAL_RE.finditer(line):
            _push("hn_total", m.group(1), ln_idx)
        # topics_filled
        for m in _TOPICS_FILLED_RE.finditer(line):
            _push("topics_filled", m.group(1), ln_idx)
        # products count
        for m in _PRODUCTS_RE.finditer(line):
            _push("products_count", m.group(1), ln_idx)
        # 5 product baseline
        for code in _PRODUCT_CODES:
            for m in _re_product(code).finditer(line):
                _push(code, m.group(1), ln_idx)

        # R25 트랙: "건" 패턴 (drift 자동 캡처) ────────────────────────────
        has_crisis_ctx = bool(_CRISIS_LINE_RE.search(line))
        # (1) STRICT bold: `**N건**`
        for m in _BOLD_GEON_RE.finditer(line):
            metric_name = "crisis_geon_bold" if has_crisis_ctx else "geon_bold"
            ctx = "crisis_bold" if has_crisis_ctx else "bold_only"
            _push_geon(metric_name, m.group(1), ln_idx, context=ctx)
        # (2) CRISIS narrative: Crisis 키워드 + 변동/추가/신규 + N건
        if has_crisis_ctx:
            for m in _DELTA_GEON_RE.finditer(line):
                _push_geon("crisis_delta_geon", m.group(1), ln_idx,
                           context="crisis_delta")

    return claims


# ── 자동 동기화 라인 주입 (--inject) ──────────────────────────────────────
_SYNC_BLOCK_BEGIN = "<!-- workflow-sync:begin -->"
_SYNC_BLOCK_END = "<!-- workflow-sync:end -->"


def _build_sync_block(round_id: str, claims: List[MetricClaim]) -> str:
    """idempotent 동기화 블록 본문 생성.

    *항상 동일 형식* — 같은 결과면 같은 문자열, 다시 inject 해도 변동 없음.
    """
    lines: List[str] = []
    lines.append(_SYNC_BLOCK_BEGIN)
    lines.append("")
    lines.append(f"> **워크플로우 자동 동기화 ({round_id})**  ")
    lines.append("> 보고된 수치 vs 실측 비교. drift |Δ| > 10% 면 alert. 본 블록은 "
                 "`workflow_validator --inject` 가 자동 생성·갱신.")
    lines.append("")
    lines.append("| metric | 보고 | 실측 | drift% | alert |")
    lines.append("|---|---:|---:|---:|---|")
    # claim 들을 metric 단위 *첫 등장* 으로 압축.
    seen: set = set()
    for c in claims:
        if c.metric in seen:
            continue
        seen.add(c.metric)
        rep = f"{c.reported:g}" if c.reported is not None else "—"
        act = f"{c.actual:g}" if c.actual is not None else "missing"
        pct = (f"{c.drift_pct*100:+.1f}%" if c.drift_pct is not None else "—")
        alert = "**ALERT**" if c.alert else ""
        lines.append(f"| {c.metric} | {rep} | {act} | {pct} | {alert} |")
    lines.append("")
    lines.append(_SYNC_BLOCK_END)
    return "\n".join(lines) + "\n"


def inject_sync_block(report_path: Path, claims: List[MetricClaim]) -> bool:
    """보고서 끝(또는 기존 블록 위치)에 자동 동기화 라인 삽입.

    동작:
      - 기존 ``<!-- workflow-sync:begin --> ... <!-- workflow-sync:end -->`` 가
        있으면 *해당 구간만* 교체 (앞뒤 본문 보존).
      - 없으면 파일 끝에 빈 줄 + 블록 append.

    반환: 실제로 파일 내용이 변경되면 True, 동일하면 False.
    """
    if not report_path.is_file() or not claims:
        return False
    new_block = _build_sync_block(_round_from_filename(report_path), claims)
    text = report_path.read_text(encoding="utf-8", errors="replace")
    # 기존 블록 검출
    bi = text.find(_SYNC_BLOCK_BEGIN)
    if bi >= 0:
        ei = text.find(_SYNC_BLOCK_END, bi)
        if ei >= 0:
            ei_end = ei + len(_SYNC_BLOCK_END)
            # 트레일링 newline 까지 같이 교체 (있을 때만).
            if ei_end < len(text) and text[ei_end] == "\n":
                ei_end += 1
            new_text = text[:bi] + new_block + text[ei_end:]
        else:
            # begin 만 있고 end 없음 — 안전상 append (변경 X 가까이)
            new_text = text.rstrip("\n") + "\n\n" + new_block
    else:
        new_text = text.rstrip("\n") + "\n\n" + new_block
    if new_text == text:
        return False
    report_path.write_text(new_text, encoding="utf-8")
    return True


# ── 외부 진입점 ──────────────────────────────────────────────────────────
def validate(
    report_paths: List[Path],
    *,
    backend: str = DEFAULT_BACKEND,
    threshold: float = DEFAULT_THRESHOLD,
    topic_eval_dir: Optional[Path] = None,
    inject: bool = False,
) -> Dict[str, Any]:
    """여러 보고서를 한꺼번에 검증.

    응답::

        {
          "generated_at_utc": "...",
          "threshold": 0.10,
          "backend": "http://localhost:8000",
          "available": {"regression": true, "coverage": true, "topic_eval": true},
          "measurements": {"voc_total": 119981, ...},
          "reports": [ReportValidation, ...],
          "summary": {
            "total_claims": N, "alerts": M, "files_with_alerts": K,
            "rounds": ["R20","R21"]
          }
        }
    """
    live = measure_live(backend=backend, topic_eval_dir=topic_eval_dir)
    out_reports: List[Dict[str, Any]] = []
    total = 0
    alerts = 0
    files_with_alerts = 0
    rounds: List[str] = []
    for rp in report_paths:
        claims = parse_report(rp, live, threshold=threshold)
        round_id = _round_from_filename(rp)
        rounds.append(round_id)
        # 정렬: alert 우선, 그 다음 metric 명.
        claims_sorted = sorted(claims, key=lambda c: (not c.alert, c.metric, c.source_line))
        if inject:
            try:
                inject_sync_block(rp, claims_sorted)
            except OSError:
                # 쓰기 실패는 graceful — alert 만 보고.
                pass
        rep_alerts = sum(1 for c in claims if c.alert)
        if rep_alerts > 0:
            files_with_alerts += 1
        alerts += rep_alerts
        total += len(claims)
        out_reports.append({
            "round": round_id,
            "path": (str(rp.relative_to(REPO_ROOT))
                     if str(rp).startswith(str(REPO_ROOT)) else str(rp)),
            "claims": [asdict(c) for c in claims_sorted],
            "alerts": rep_alerts,
        })
    return {
        "generated_at_utc": live["generated_at_utc"],
        "threshold": threshold,
        "backend": live["backend"],
        "available": live["available"],
        "measurements": live["metrics"],
        "sources": live["sources"],
        "reports": out_reports,
        "summary": {
            "total_claims": total,
            "alerts": alerts,
            "files_with_alerts": files_with_alerts,
            "rounds": sorted(set(rounds)),
        },
    }


# ── 메타-루프 (validator 가 자기 보고서도 검증) ─────────────────────────
#
# 배경
# ----
# `parse_report` 의 정규식은 *키워드 직후 ≤8자* 거리만 인정 (narrative 안의 우연한
# 숫자 차단). 그러나 본 validator 가 *스스로 생성한 보고서* (예 `reports/
# workflow_validate_R22.md`) 의 표 형식은
#
#     | voc_total | L38 | 150,000 | 120,423 | -19.72% | ALERT |
#                ^   ^^^   ^^^^^^
#               cell 사이 거리 > 8자 → 매칭 불가
#
# 가 되어 *자기 보고서를 자기가 못 읽는* blind spot 이 발생한다 (R22 § 2 자동
# 식별률 0/5 의 근본 원인). 메타 파서는 이 blind spot 을 해소한다.
#
# 동작
# ----
# 1. ``parse_report_meta`` : 마크다운 표 행을 *셀 단위* 로 분해.
#    - 첫 셀이 알려진 metric 이름이면 → 해당 행을 metric claim 으로 등록.
#    - 두 번째 *숫자* 셀 = reported (validator 자기 보고)
#    - 세 번째 *숫자* 셀 = actual_at_report_time (validator 가 측정 당시 기록)
#    - drift = (live_actual - reported) / max(|reported|, |live_actual|, eps)
# 2. ``validate_meta`` : 재귀 적용 — `iteration=0` 본 보고서 → `iteration=1`
#    validator 가 산출한 메타 보고서 → ... cap 3.
# 3. 각 iteration 의 *자기 drift* (meta_self_drift_pct) = validator 가 보고한
#    actual_at_report_time vs 현 시점 live_actual 사이 차이의 평균 — 보고서
#    작성 시점 ↔ 검증 시점 간 *시간차 drift* 정량.
#
# 알려진 metric 이름은 ``_META_KNOWN_METRICS`` — `parse_report` 와 동일 키 풀.

_META_KNOWN_METRICS = (
    "voc_total", "linked", "sentiment_pct", "topic_pct", "f1_overall",
    "hn_linked_pct", "hn_total", "topics_filled", "products_count",
    "note7_voc", "fold1_voc", "s22_voc", "s25_voc", "buds3_voc",
    "GN7", "GZF1", "GS22", "GS25", "GB3",
    # R26 트랙 B: `_BOLD_GEON_RE` / `_DELTA_GEON_RE` 미러링.
    # primary parser (`parse_report`) 가 ``**N건**`` / "변동 N건" 라인을
    # `geon_bold` / `crisis_geon_bold` / `crisis_delta_geon` 로 등록한다.
    # 메타 파서가 이 metric 이름을 모르면 L2~L5 에서 행이 사라져 L1 38 vs
    # L2~5 37 처럼 1 차이가 발생한다 (R25 트랙 B 권고). 동일 metric 풀에
    # 포함시키면 표 셀 인식이 자연 일치한다.
    "geon_bold", "crisis_geon_bold", "crisis_delta_geon",
)

# 표 셀 단위 숫자 추출 — `, _` 포함 정수/실수, 선택적 % 와 부호.
_META_CELL_NUM_RE = re.compile(
    r"^\s*([+\-]?[0-9]+(?:[,_][0-9]{3})*(?:\.[0-9]+)?)\s*%?\s*$"
)

# R26 트랙 B: primary parser 가 actual=None 인 경우 `_build_meta_report`
# 는 셀을 ``missing`` 으로 직렬화한다. 메타 파서가 이를 숫자로 오해하면
# 행 자체가 탈락한다. 명시적 sentinel 으로 인식하여 None 으로 흘린다.
_META_MISSING_CELL_TOKENS = frozenset({"missing", "—", "-", ""})


def _parse_md_table_row(line: str) -> Optional[List[str]]:
    """`| a | b | c |` 형태의 표 행을 셀 list 로 분해.

    구분 행 (`|---|---|`) 과 일반 텍스트는 None. 양 끝 파이프 제거 후 분리.
    """
    s = line.strip()
    if not s.startswith("|") or not s.endswith("|"):
        return None
    # 구분 행
    body = s[1:-1]
    cells = [c.strip() for c in body.split("|")]
    if not cells:
        return None
    # 모든 셀이 `---` 패턴이면 구분 행.
    if all(re.fullmatch(r":?-+:?", c) for c in cells):
        return None
    return cells


def _normalize_metric_name(name: str) -> Optional[str]:
    """표 첫 셀의 metric 이름을 canonical key 로 정규화. 알려진 풀에 없으면 None."""
    s = name.strip().strip("*`").lower()
    # 표 형식 "| metric | ..." 에서 굵게 표기 (**voc_total**) 처리.
    for known in _META_KNOWN_METRICS:
        if s == known.lower():
            return known
    return None


def parse_report_meta(
    report_path: Path,
    live: Dict[str, Any],
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> List[MetricClaim]:
    """validator 가 산출한 보고서 (`reports/workflow_validate_*.md`) 의
    표 셀을 *직접* 파싱하여 메타-claim 반환.

    표 row 형식 가정 (validator 자기 보고서 컨벤션)::

        | <metric> | <line ref> | <reported> | <actual_at_report_time> | <drift%> | <alert> | ...

    - 셀 1 (metric 이름) 이 알려진 풀에 있으면 row 채택.
    - 셀 중 *첫 번째 숫자 셀* = reported (validator 자기 보고 reported)
    - 셀 중 *두 번째 숫자 셀* = validator 가 측정 당시 기록한 actual
      (메타 비교 기준: 현 시점 live 와 비교 → time-shift drift)

    이는 *parse_report 의 정규식 거리 제약 (≤8자)* 으로는 잡을 수 없는
    표 셀 단위 수치를 추출한다 (R23 권고 2 — validator blind spot 해소).
    """
    if not report_path.is_file():
        return []
    text = report_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    round_id = _round_from_filename(report_path)
    metrics_live = live.get("metrics", {})
    claims: List[MetricClaim] = []

    # R26 트랙 B: geon-family 행도 메타 단계에서 보존하기 위한 cross-check
    # 컨텍스트. primary parser 의 `_push_geon` 와 동일 정책으로 `actual_delta`
    # 계산. live.crisis_baseline 이 없으면 actual=None (alert 미발생).
    crisis_live_val = metrics_live.get("crisis_voc_sum")
    crisis_baseline_val = live.get("crisis_baseline")
    actual_crisis_delta: Optional[float] = None
    if isinstance(crisis_live_val, (int, float)) and isinstance(
        crisis_baseline_val, (int, float)
    ):
        actual_crisis_delta = float(crisis_live_val) - float(crisis_baseline_val)

    for ln_idx, raw_line in enumerate(lines, start=1):
        cells = _parse_md_table_row(raw_line)
        if not cells or len(cells) < 3:
            continue
        # 첫 셀이 metric 이름이어야 함.
        canon = _normalize_metric_name(cells[0])
        if canon is None:
            continue
        # 숫자 셀 추출 (앞에서부터 최대 2개). `missing` / `—` sentinel 은
        # *None placeholder* 로 자리만 잡고 다음 셀로 진행 — primary 의
        # actual=None 직렬화를 보존한다 (R26 트랙 B).
        nums: List[Optional[float]] = []
        for c in cells[1:]:
            stripped = c.strip().lower()
            if stripped in _META_MISSING_CELL_TOKENS:
                nums.append(None)
                if len(nums) >= 2:
                    break
                continue
            m = _META_CELL_NUM_RE.match(c)
            if m:
                v = _to_float(m.group(1))
                if v is not None:
                    nums.append(v)
            if len(nums) >= 2:
                break
        # 숫자/None 이 하나도 없으면 metric 행으로 인정 불가 (전부 텍스트).
        if not nums:
            continue
        first = nums[0]
        reported = (_normalize_value(canon, first) if first is not None else None)
        # 두 번째 셀: recorded_actual — `missing` 이면 None, 숫자면 정규화.
        recorded_actual: Optional[float] = None
        if len(nums) >= 2 and nums[1] is not None:
            recorded_actual = _normalize_value(canon, nums[1])
        # R26 트랙 B: geon-family 는 *live metrics 직접 조회 대신* primary 와
        # 동일 cross-check (crisis_voc_sum - crisis_baseline). geon_bold 는
        # 비-Crisis 컨텍스트로 cross-check 미수행.
        if canon in ("crisis_delta_geon", "crisis_geon_bold"):
            if actual_crisis_delta is not None:
                live_actual = _normalize_value(canon, actual_crisis_delta)
            else:
                live_actual = None
        elif canon == "geon_bold":
            live_actual = None  # primary 와 동일 (bold_only context)
        else:
            actual_raw = metrics_live.get(canon)
            live_actual = (_normalize_value(canon, float(actual_raw))
                           if actual_raw is not None else None)
        # 메타-drift: live_actual vs reported (보고서 본문 claim 의 *원본* drift)
        d, dp = _drift(reported, live_actual)
        source = (live.get("sources") or {}).get(canon, "")
        is_approx = "approx" in source.lower()
        alert = bool(
            dp is not None
            and abs(dp) > threshold
            and not is_approx
        )
        note_parts: List[str] = []
        if recorded_actual is not None and live_actual is not None:
            # 시간차 drift = recorded vs live — *작을수록* validator 의 측정
            # 시점이 현 시점과 가까움 (자가 일치도).
            _, time_dp = _drift(recorded_actual, live_actual)
            if time_dp is not None:
                note_parts.append(f"time-shift drift={time_dp*100:+.2f}%")
        if is_approx:
            note_parts.append(f"approx source ({source}) — alert suppressed")
        if canon in ("crisis_delta_geon", "crisis_geon_bold") and live_actual is None:
            note_parts.append("no live crisis baseline (cross-check skipped)")
        elif canon == "geon_bold":
            note_parts.append("no cross-check target (bold geon outside crisis context)")
        if live_actual is None:
            note_parts.append("no live measurement")
        claims.append(MetricClaim(
            round=round_id, metric=canon,
            reported=reported, actual=live_actual,
            drift=d, drift_pct=dp,
            source_line=ln_idx, alert=alert,
            note="; ".join(note_parts),
        ))
    return claims


def _build_meta_report(
    iteration: int,
    parent_round: str,
    claims_by_path: Dict[str, List[MetricClaim]],
    live: Dict[str, Any],
    threshold: float,
    max_iter: Optional[int] = None,
) -> str:
    """iteration n 의 결과를 마크다운 메타 보고서로 직렬화.

    ``max_iter`` 미지정 시 환경변수 기본값을 사용 (헤더 표기용).
    """
    cap_display = max_iter if max_iter is not None else _meta_cap_default()
    out: List[str] = []
    out.append(f"# Workflow Validate Meta — {parent_round} (iter {iteration})")
    out.append("")
    out.append(f"생성 (UTC): {live.get('generated_at_utc','—')}  ")
    out.append(f"backend: `{live.get('backend','—')}`  ")
    out.append(f"available: `{live.get('available', {})}`  ")
    out.append(f"threshold: ±{threshold*100:.0f}%  ")
    out.append(f"메타 재귀 cap: {cap_display} iteration (현 iter={iteration})")
    out.append("")
    out.append("## 1. 메타 파서 산출 (validator 자기 보고서의 표 셀 직접 파싱)")
    out.append("")
    if not claims_by_path:
        out.append("> 메타 입력 보고서가 없습니다 (`reports/workflow_validate_*.md` 비어있음).")
        out.append("")
    for path, claims in claims_by_path.items():
        out.append(f"### `{path}` — claim {len(claims)}건, "
                   f"alert {sum(1 for c in claims if c.alert)}건")
        out.append("")
        out.append("| metric | line | 보고 (reported) | 실측 (live) | drift% | alert | note |")
        out.append("|---|---:|---:|---:|---:|---|---|")
        for c in claims:
            pct = (f"{c.drift_pct*100:+.2f}%" if c.drift_pct is not None else "—")
            rep_v = f"{c.reported:g}" if c.reported is not None else "—"
            act_v = f"{c.actual:g}" if c.actual is not None else "missing"
            tag = "**ALERT**" if c.alert else ""
            note = c.note or ""
            out.append(f"| {c.metric} | L{c.source_line} | {rep_v} | {act_v} "
                       f"| {pct} | {tag} | {note} |")
        out.append("")
    return "\n".join(out) + "\n"


def validate_meta(
    seed_report_paths: List[Path],
    *,
    backend: str = DEFAULT_BACKEND,
    threshold: float = DEFAULT_THRESHOLD,
    topic_eval_dir: Optional[Path] = None,
    max_iter: Optional[int] = None,
    meta_output_dir: Optional[Path] = None,
    parent_round: Optional[str] = None,
    write_reports: bool = True,
) -> Dict[str, Any]:
    """validator 재귀 적용 (메타-루프).

    iteration 0 : ``seed_report_paths`` (보통 docs/dashboard/R*.md) 를
        기존 `parse_report` 로 검증.
    iteration 1..max_iter-1 : 직전 iteration 이 *생성한 메타 보고서*
        (`reports/workflow_validate_<round>_meta_iter<N>.md`) 를 ``parse_report_meta``
        로 재검증. cap 도달 시 종료.

    ``max_iter`` 인자가 ``None`` 이면 ``META_CAP`` 환경변수 (기본 3, 가드 1..10)
    값 사용. R25 트랙 B 의 cap 확장 (3→5) 은 이 환경변수로 제어.

    반환::

        {
          "max_iter": 3,
          "cap_reached": bool,
          "parent_round": "R23",
          "iterations": [
            {
              "iter": 0,
              "kind": "primary",            # parse_report
              "input_paths": [...],
              "output_path": ".../workflow_validate_R23_meta_iter0.md",
              "claims_total": N, "alerts": M,
              "rounds": [...],
            },
            { "iter": 1, "kind": "meta", ... },
            ...
          ],
          "self_drift_pct": float,          # 마지막 iter mean |drift_pct|
          "available": {...},
          "measurements": {...}
        }

    iteration n+1 은 iteration n 이 만든 보고서를 본다. iteration n 이 0 claim
    을 만들었다면 *연쇄 종료* (보고서가 비어 다음 iter 입력 없음 → cap 미도달
    에도 멈춤).
    """
    live = measure_live(backend=backend, topic_eval_dir=topic_eval_dir)
    meta_output_dir = meta_output_dir or (REPO_ROOT / "reports")
    meta_output_dir.mkdir(parents=True, exist_ok=True)
    # R25 트랙 B: max_iter 미지정 시 환경변수 (META_CAP) 기본 사용.
    # R27 트랙 E: hard cap (META_HARD_CAP, 기본 10) 초과 *명시 요청* 은
    #   RuntimeError + audit alert. silent 클램프 대신 즉시 실패하여 의도
    #   vs 실행 갭을 사고 전에 노출. None / env 기본은 기존대로 클램프.
    hard_cap = _meta_hard_cap()
    if max_iter is None:
        max_iter = _meta_cap_default()
        # _meta_cap_default 자체가 1..10 클램프 후 반환. hard_cap < 10 인
        # 환경에선 추가 클램프 필요.
        if max_iter > hard_cap:
            max_iter = hard_cap
    else:
        req = int(max_iter)
        if req < 1:
            max_iter = 1
        elif req > hard_cap:
            raise RuntimeError(
                f"meta_cap_hard_cap_exceeded: requested={req} > "
                f"META_HARD_CAP={hard_cap}. "
                "Lower max_iter or raise META_HARD_CAP env."
            )
        else:
            max_iter = req
    # parent_round 결정: seed 첫 보고서 round 코드 또는 'Rx'.
    if parent_round is None:
        if seed_report_paths:
            parent_round = _round_from_filename(seed_report_paths[0])
        else:
            parent_round = "Rx"

    iterations: List[Dict[str, Any]] = []
    current_inputs = list(seed_report_paths)
    cap_reached = False
    last_mean_abs: float = 0.0
    # R27 트랙 E: 고정점 / cycle 감지 상태.
    #   prev_mean_abs : 직전 iter 의 mean_abs (L>1 부터 비교).
    #   seen_signatures : 각 iter 의 claim signature 집합 — 동일 signature
    #     재출현 = cycle (보고서가 자기를 재생산).  signature = sorted tuple of
    #     (metric, reported_value).  actual 은 live 측정값이라 회차마다 동일하므로
    #     reported 만 보는 것으로 충분.
    prev_mean_abs: Optional[float] = None
    seen_signatures: List[frozenset] = []
    fixed_point_stop = False
    cycle_stop = False

    for it in range(max_iter):
        if not current_inputs:
            # 입력 없음 → 종료 (cap 미도달 자연 종료).
            break
        # iter 0 = 기존 parser, iter ≥1 = 메타 parser.
        kind = "primary" if it == 0 else "meta"
        claims_by_path: Dict[str, List[MetricClaim]] = {}
        total_claims = 0
        total_alerts = 0
        all_drift_pcts: List[float] = []
        rounds_seen: List[str] = []
        for rp in current_inputs:
            if it == 0:
                cls = parse_report(rp, live, threshold=threshold)
            else:
                cls = parse_report_meta(rp, live, threshold=threshold)
            cls_sorted = sorted(cls,
                                key=lambda c: (not c.alert, c.metric, c.source_line))
            key = (str(rp.relative_to(REPO_ROOT))
                   if str(rp).startswith(str(REPO_ROOT)) else str(rp))
            claims_by_path[key] = cls_sorted
            total_claims += len(cls)
            total_alerts += sum(1 for c in cls if c.alert)
            rounds_seen.append(_round_from_filename(rp))
            for c in cls:
                if c.drift_pct is not None:
                    all_drift_pcts.append(abs(c.drift_pct))
        mean_abs = (sum(all_drift_pcts) / len(all_drift_pcts)
                    if all_drift_pcts else 0.0)
        max_abs = max(all_drift_pcts) if all_drift_pcts else 0.0
        min_abs = min(all_drift_pcts) if all_drift_pcts else 0.0
        last_mean_abs = mean_abs
        # R27 트랙 E: claim signature 계산 — cycle 감지에 사용.
        sig_items = []
        for _path, cls_list in claims_by_path.items():
            for c in cls_list:
                rep_v = c.reported if c.reported is not None else float("nan")
                sig_items.append((c.metric, round(rep_v, 6)
                                  if rep_v == rep_v else None))
        current_signature = frozenset(sig_items)
        # 직렬화.
        report_md = _build_meta_report(
            iteration=it,
            parent_round=parent_round,
            claims_by_path=claims_by_path,
            live=live,
            threshold=threshold,
            max_iter=max_iter,
        )
        output_path = meta_output_dir / (
            f"workflow_validate_{parent_round}_meta_iter{it}.md"
            if it > 0 else
            f"workflow_validate_{parent_round}_meta_iter{it}.md"
        )
        # iter==0 은 *기존 검증 산출물* 과 형식이 다르므로 별도 파일.
        if write_reports:
            try:
                output_path.write_text(report_md, encoding="utf-8")
            except OSError:
                pass
        # R27 트랙 E: 고정점 / cycle 감지 — *기록 후* 판정.
        #   - fixed-point : L > 1 이고 |mean_abs - prev_mean_abs| < FIXED_POINT_EPS
        #     (둘 다 ratio 단위. 0.001% = 1e-5).
        #   - cycle       : current_signature 가 이전 iter 어느 곳에서든 재출현.
        #     L1 자체와 L>1 사이 signature 일치도 cycle 로 본다.
        stop_reason = None
        if it > 0 and prev_mean_abs is not None:
            if abs(mean_abs - prev_mean_abs) < FIXED_POINT_EPS:
                stop_reason = "fixed_point"
                fixed_point_stop = True
        if (stop_reason is None and current_signature
                and current_signature in seen_signatures):
            stop_reason = "cycle"
            cycle_stop = True
        iterations.append({
            "iter": it,
            "kind": kind,
            "input_paths": [
                (str(p.relative_to(REPO_ROOT))
                 if str(p).startswith(str(REPO_ROOT)) else str(p))
                for p in current_inputs
            ],
            "output_path": (str(output_path.relative_to(REPO_ROOT))
                            if str(output_path).startswith(str(REPO_ROOT))
                            else str(output_path)),
            "claims_total": total_claims,
            "alerts": total_alerts,
            "mean_abs_drift_pct": round(mean_abs * 100, 4),
            "max_abs_drift_pct": round(max_abs * 100, 4),
            "min_abs_drift_pct": round(min_abs * 100, 4),
            "drift_samples": len(all_drift_pcts),
            "rounds": sorted(set(rounds_seen)),
            "stop_reason": stop_reason,
        })
        prev_mean_abs = mean_abs
        seen_signatures.append(current_signature)
        # 다음 iter 입력 = 이번 iter 가 만든 메타 보고서 (단일).
        # claim 이 0 이면 연쇄 종료.
        if total_claims == 0:
            break
        # R27 트랙 E: stop_reason 이 정해졌으면 다음 iter 진입 차단.
        if stop_reason in ("fixed_point", "cycle"):
            break
        current_inputs = [output_path]
        if it + 1 >= max_iter:
            cap_reached = True
            break

    # 메타 cap 별 drift 분포 요약 — R25 트랙 B 산출.
    # deep level n = iter n+1 (사용자 가독: level 1..N 로 노출).
    drift_distribution = [
        {
            "level": it["iter"] + 1,
            "kind": it["kind"],
            "claims": it["claims_total"],
            "mean_abs_pct": it["mean_abs_drift_pct"],
            "max_abs_pct": it["max_abs_drift_pct"],
            "min_abs_pct": it["min_abs_drift_pct"],
            "samples": it["drift_samples"],
        }
        for it in iterations
    ]
    return {
        "max_iter": max_iter,
        "cap_used": max_iter,
        "cap_env_default": _meta_cap_default(),
        "hard_cap": hard_cap,
        "cap_reached": cap_reached,
        "fixed_point_stop": fixed_point_stop,
        "cycle_stop": cycle_stop,
        "parent_round": parent_round,
        "iterations": iterations,
        "self_drift_pct": round(last_mean_abs * 100, 4),
        "drift_distribution": drift_distribution,
        "available": live.get("available"),
        "measurements": live.get("metrics"),
        "sources": live.get("sources"),
        "generated_at_utc": live.get("generated_at_utc"),
        "backend": live.get("backend"),
        "threshold": threshold,
    }


# ── 보고서 선택 (loc_validator 와 동일 규약) ───────────────────────────────
def _select_reports(
    reports_dir: Path,
    rounds: Optional[List[str]] = None,
    all_reports: bool = False,
) -> List[Path]:
    if not reports_dir.is_dir():
        return []
    files = sorted(reports_dir.glob("R*.md"))
    if all_reports:
        return files
    if not rounds:
        return files
    targets = {r.upper() for r in rounds}
    sel: List[Path] = []
    for f in files:
        for t in targets:
            if f.name.startswith(t + "_"):
                sel.append(f)
                break
    return sel


def _fmt_table(result: Dict[str, Any]) -> str:
    """사람 가독 표 (CLI 기본 출력)."""
    rows: List[str] = []
    rows.append(f"backend={result['backend']}  threshold={result['threshold']*100:.0f}%  "
                f"available={result['available']}")
    rows.append("")
    rows.append("| round | metric | reported | actual | drift% | alert |")
    rows.append("|---|---|---:|---:|---:|---|")
    for rep in result["reports"]:
        for c in rep["claims"]:
            pct = (f"{c['drift_pct']*100:+.1f}%"
                   if c["drift_pct"] is not None else "—")
            rep_v = f"{c['reported']:g}" if c["reported"] is not None else "—"
            act_v = (f"{c['actual']:g}" if c["actual"] is not None
                     else (c.get("note") or "missing"))
            tag = "ALERT" if c["alert"] else ""
            rows.append(f"| {c['round']} | {c['metric']} | {rep_v} | {act_v} "
                        f"| {pct} | {tag} |")
    s = result["summary"]
    rows.append("")
    rows.append(f"총 claim {s['total_claims']}건, alert {s['alerts']}건, "
                f"파일 중 alert {s['files_with_alerts']}건. rounds={','.join(s['rounds'])}.")
    return "\n".join(rows)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--report", action="append", default=[],
                   help="단일 보고서 경로 (반복 가능).")
    p.add_argument("--rounds", default="",
                   help="콤마 구분 round 코드 (예: R20,R21).")
    p.add_argument("--all", action="store_true",
                   help="docs/dashboard/R*.md 전체.")
    p.add_argument("--reports-dir", default=str(REPORTS_DIR_DEFAULT),
                   help="기본: docs/dashboard")
    p.add_argument("--backend", default=DEFAULT_BACKEND,
                   help="backend 베이스 URL (기본 http://localhost:8000).")
    p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                   help="|drift%%| 임계치 (기본 0.10 = 10%%).")
    p.add_argument("--inject", action="store_true",
                   help="보고서 끝에 자동 동기화 블록 삽입/갱신.")
    p.add_argument("--json", action="store_true",
                   help="JSON 출력 (기본은 마크다운 표).")
    p.add_argument("--meta", action="store_true",
                   help="메타-루프: validator 가 자기 산출 보고서도 재귀 검증.")
    p.add_argument("--meta-max-iter", type=int, default=DEFAULT_META_CAP,
                   help=f"메타-루프 재귀 cap (기본 {DEFAULT_META_CAP}, "
                        "환경변수 META_CAP 로 제어).")
    p.add_argument("--meta-output-dir", default=str(REPO_ROOT / "reports"),
                   help="메타 보고서 출력 디렉토리 (기본 reports/).")
    args = p.parse_args(argv)

    report_paths: List[Path] = []
    if args.report:
        report_paths.extend(Path(r) for r in args.report)
    rounds = [r.strip() for r in args.rounds.split(",") if r.strip()]
    if args.all or rounds or not report_paths:
        report_paths.extend(
            _select_reports(Path(args.reports_dir),
                            rounds=rounds or None,
                            all_reports=args.all and not rounds)
        )
    # 중복 제거.
    seen: set = set()
    unique: List[Path] = []
    for rp in report_paths:
        rk = str(rp.resolve())
        if rk in seen:
            continue
        seen.add(rk)
        unique.append(rp)
    if not unique:
        print("no reports selected", file=sys.stderr)
        return 2

    if args.meta:
        meta_result = validate_meta(
            unique,
            backend=args.backend,
            threshold=args.threshold,
            max_iter=args.meta_max_iter,
            meta_output_dir=Path(args.meta_output_dir),
        )
        if args.json:
            print(json.dumps(meta_result, ensure_ascii=False, indent=2))
        else:
            rows: List[str] = []
            rows.append(f"메타-루프 (cap {meta_result['max_iter']}) — "
                        f"parent={meta_result['parent_round']}  "
                        f"cap_reached={meta_result['cap_reached']}  "
                        f"self_drift={meta_result['self_drift_pct']:.2f}%")
            rows.append("")
            rows.append("| iter | kind | inputs | claims | alerts | mean |Δ|% | output |")
            rows.append("|---:|---|---:|---:|---:|---:|---|")
            for it in meta_result["iterations"]:
                rows.append(
                    f"| {it['iter']} | {it['kind']} | {len(it['input_paths'])} "
                    f"| {it['claims_total']} | {it['alerts']} "
                    f"| {it['mean_abs_drift_pct']:.2f} | `{it['output_path']}` |"
                )
            print("\n".join(rows))
        # 메타 모드 종료코드: 마지막 iteration alert 가 있으면 1, 아니면 0.
        last_alerts = (meta_result["iterations"][-1]["alerts"]
                       if meta_result["iterations"] else 0)
        return 1 if last_alerts > 0 else 0

    result = validate(unique, backend=args.backend,
                      threshold=args.threshold, inject=args.inject)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(_fmt_table(result))
    return 1 if result["summary"]["alerts"] > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
