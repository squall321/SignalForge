"""Workflow drift stats — 라운드·트랙별 자기 보고 drift 정량화 + 신뢰도 점수.

R22 권고 5 (workflow drift 자동 정량 → 차등 신뢰도 부여) 의 본체.

설계 원칙
=========
1. 입력: ``docs/dashboard/R*.md`` 보고서 본문 (외부 의존 없음, 파일 stat 만).
2. 추출 신호 (세 종류):

   * **LoC drift** — "보고 X (lines|LoC) vs 실측 Y" / "실측 X vs 보고 Y" /
     "(실측 X lines, 보고 Y lines 차이)" / "(실측 X LoC, 보고 Y LoC 차이 +Z)" 패턴.
     R18~R22 전반에서 가장 일관된 drift 신호.
   * **자기 보고 drift 절** — 보고서 "자기 보고 drift" 컬럼 / "확인된 자기 보고
     drift" 섹션의 항목 (정량 가능한 X→Y 패턴).
   * **workflow-sync 블록** — R22 트랙 B `workflow_validator --inject` 가
     남긴 `<!-- workflow-sync:begin -->` 블록 내 drift% 표 (가장 정확).

3. 트랙 식별: 표 형식 "| **A. ...** |" 또는 "| A LLM apply |" 의 셀 1번을
   파싱. 한 글자 알파벳 + 트랙명. 라운드별 트랙 의미가 변동해도 *알파벳* 자체는
   안정 — 신뢰도 표는 *(라운드, 트랙)* 페어 단위.

4. 통계: 라운드/트랙별
   - drift 표본 수 ``n``
   - 평균 ``mean_drift_pct`` (절댓값 평균)
   - 표준편차 ``std_drift_pct`` (절댓값 기준)
   - 부호 평균 ``signed_mean_pct`` (체계적 과소/과대 보고 지표)
   - 최대 ``max_drift_pct``
   - 분포 (히스토그램 bin: 0–5 / 5–10 / 10–20 / 20–50 / ≥50%)

5. 신뢰도 점수 (0~100):

       trust = 100 * (1 - clamp(mean_abs, 0, 1))
                   * (1 - clamp(std_abs / 2, 0, 0.5))

   - mean=0%, std=0%  → 100 (완벽)
   - mean=10%, std=10% → 100 * 0.9 * 0.95 = 85.5
   - mean=30%, std=20% → 100 * 0.7 * 0.9 = 63.0
   - mean=68%, std=20% → 100 * 0.32 * 0.9 = 28.8
   - mean ≥100% → 0

   *부호* 평균 (signed_mean_pct) 이 일관 양/음 이면 *systematic bias* 라벨
   추가 (|signed_mean| > 0.5 * mean_abs 기준).

   **R25 트랙 C/E — 안정 임계 (trust_level)**:
   - ``trust_score >= 80``  → ``normal``   — 운영 유지
   - ``60 <= score < 80``   → ``warning``  — 다음 라운드 prompt/임계 점검
   - ``score < 60``         → ``critical`` — 즉시 트랙 재실행 큐 추가

   환경변수 (실측 분포에 맞춰 운영 중 조정 가능)::

       SIGNALFORGE_TRUST_WARNING   (기본 80.0)
       SIGNALFORGE_TRUST_CRITICAL  (기본 60.0)

   7일 데이터 (R20~R22) 분포:
     mean trust 74.7 / R22 58.9 (critical) / R20 76.5 (warning) / R7 98.6 (normal).
   이 분포에서 critical=1, warning=2, normal=1 로 알림이 자연스럽게 분리된다.

6. graceful: 보고서가 없거나 패턴 미스매치면 빈 결과 + ``available=False``.
   ``runtime`` 의존 없음 (psql/HTTP 모두 미사용 — 순수 텍스트).

CLI 사용
========

.. code-block:: bash

    # 전체 라운드 통계 출력
    python -m insight.workflow_drift_stats

    # 특정 라운드만
    python -m insight.workflow_drift_stats --rounds R20,R21,R22

    # JSON
    python -m insight.workflow_drift_stats --json
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── 경로 ────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR_DEFAULT = REPO_ROOT / "docs" / "dashboard"

# bin 경계 (% 단위, 절댓값)
_BINS: Tuple[Tuple[str, float, float], ...] = (
    ("0-5",     0.0,   5.0),
    ("5-10",    5.0,  10.0),
    ("10-20",  10.0,  20.0),
    ("20-50",  20.0,  50.0),
    (">=50",   50.0,  float("inf")),
)


# ── R25 트랙 C/E — 안정 임계 (trust_level) ───────────────────────────────
# trust_score (0~100) 를 critical/warning/normal 로 분류.
# 환경변수로 운영 중 조정 가능 (실측 분포에 맞춰 조여 나간다).
#   SIGNALFORGE_TRUST_WARNING  (기본 80.0)  : 이 미만이면 warning
#   SIGNALFORGE_TRUST_CRITICAL (기본 60.0)  : 이 미만이면 critical
#
# 정합성: critical < warning. critical >= warning 이면 critical 을 warning-1 로 보정.
_DEFAULT_TRUST_WARNING = 80.0
_DEFAULT_TRUST_CRITICAL = 60.0


def _trust_thresholds() -> Tuple[float, float]:
    """(critical_below, warning_below) — env 우선, 기본 60/80."""
    import os
    try:
        warn = float(os.getenv("SIGNALFORGE_TRUST_WARNING") or _DEFAULT_TRUST_WARNING)
    except ValueError:
        warn = _DEFAULT_TRUST_WARNING
    try:
        crit = float(os.getenv("SIGNALFORGE_TRUST_CRITICAL") or _DEFAULT_TRUST_CRITICAL)
    except ValueError:
        crit = _DEFAULT_TRUST_CRITICAL
    # 보정: critical < warning 보장.
    if crit >= warn:
        crit = max(0.0, warn - 1.0)
    return crit, warn


def classify_trust(score: float) -> str:
    """trust_score → 'critical' | 'warning' | 'normal'.

    임계는 환경변수 ``SIGNALFORGE_TRUST_CRITICAL`` / ``SIGNALFORGE_TRUST_WARNING``
    로 운영 중 조정 가능. 기본 60 / 80.
    """
    crit, warn = _trust_thresholds()
    if score < crit:
        return "critical"
    if score < warn:
        return "warning"
    return "normal"


# ── 보고서 패턴 (LoC drift) ──────────────────────────────────────────────
#
# 핵심 4종 (R18~R22 실제 코퍼스 기반):
#   1) "보고 322 vs 실측 446 LoC (+38% drift)"
#   2) "보고 305 vs 실측 389 LoC, drift +27.5%"
#   3) "(실측 509 lines, 보고 358 lines 차이)"
#   4) "(실측 323 LoC, 보고 276 LoC 차이 +47)"  ← 절대값 변동 (퍼센트는 별도 계산)
#   5) "보고 1,623 대비 +41% 일관 drift" / "보고 X 대비 Y건"
#
# 모두 *한 라인* 안에서 X·Y·(percent or signed delta) 가 같이 등장 — 라인 단위 파싱.
_NUM = r"([0-9]+(?:[,_][0-9]{3})*(?:\.[0-9]+)?)"
_LOC_UNIT = r"(?:LoC|lines?|줄|L)"
# 따옴표 (선택), 부호 (선택), 일반 prefix 토큰 (선택, "+", "total_runs=" 등) — 보고서
# 한국어 narrative 가 종종 `보고 "+177"` / `보고 "total_runs=7"` 처럼 감싼다.
# 핵심: *마지막* 숫자만 캡처. 너무 길지 않게 (≤16 chars).
_QPREFIX = r'(?:[\"\'`]?\s*[+\-]?\s*(?:[A-Za-z_][A-Za-z_0-9]{0,15}=)?\s*)'
_QSUFFIX = r'(?:\s*[\"\'`]?\s*(?:건|개|회|times?)?)'

# 패턴 1·2: "보고 X vs 실측 Y" (선택적 단위 + 선택적 drift%).
# 한 라인 안에서 *짧은 거리* — 보고~실측 사이 ≤ 30자 (자유 narrative 차단).
_RE_REPORT_VS_ACTUAL = re.compile(
    rf"보고\s*{_QPREFIX}{_NUM}{_QSUFFIX}\s*(?:{_LOC_UNIT}\s*)?"
    rf"(?:vs|대비|→|·)\s*실측\s*(?:누계\s*|평균\s*)?{_QPREFIX}{_NUM}",
    re.IGNORECASE,
)

# 패턴 3·4: "(실측 X (LoC|lines) , 보고 Y (LoC|lines) 차이)"
_RE_ACTUAL_VS_REPORT = re.compile(
    rf"실측\s*{_NUM}\s*{_LOC_UNIT}\s*[,，]\s*보고\s*{_NUM}\s*{_LOC_UNIT}\s*차이",
    re.IGNORECASE,
)

# 패턴 5: "보고 X 대비 +Y%"  (X 만 알려진 보고값, drift % 직접 캡처)
_RE_REPORT_VS_PCT = re.compile(
    rf"보고\s*{_NUM}\s*대비\s*([+\-]?[0-9]+(?:\.[0-9]+)?)\s*%",
    re.IGNORECASE,
)

# 트랙 식별: 표 시작 셀 (boldface 또는 plain). "| **A. xxx** |" / "| A xxx |"
_RE_TRACK_CELL = re.compile(
    r"^\|\s*(?:\*\*)?\s*([A-Z])\b[.\s]*([^|*]+?)\s*(?:\*\*)?\s*\|"
)

# workflow-sync 블록 — `--inject` 가 남긴 표 행
_RE_SYNC_BLOCK_BEGIN = re.compile(r"<!--\s*workflow-sync:begin\s*-->")
_RE_SYNC_BLOCK_END = re.compile(r"<!--\s*workflow-sync:end\s*-->")
# 블록 내부 행:  "| metric | reported | actual | drift% | alert |"
# 데이터 행:    "| voc_total | 118430 | 119981 | +1.3% |  |"
_RE_SYNC_ROW = re.compile(
    r"^\|\s*([a-z][a-z0-9_]*)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*"
    r"\|\s*([+\-]?[0-9]+(?:\.[0-9]+)?)\s*%\s*\|",
    re.IGNORECASE,
)

# 라운드 코드: R<숫자>
_RE_ROUND_FROM_NAME = re.compile(r"^(R\d+)[_\-A-Za-z]")

# ── self-report 감지 ────────────────────────────────────────────────────
# C 트랙 (drift_stats) 본인이 자기 LoC drift 를 보고하면 *재귀 편향* 발생.
# 같은 표본이 다음 라운드 통계에도 반영되어 평균을 끌어올린다.
#
# 두 단계 감지:
#
# 1. **섹션 헤더 키워드**: ``## X. self-report drift``,
#    ``## X. 자기 보고 drift 절`` 같은 섹션 헤더에 키워드 있으면 *그 섹션 전체*
#    (다음 동급 헤더까지) 의 표본을 self 분류.  표 내부 "자기 보고 drift"
#    *컬럼 헤더* 는 의미상 메타용어일 뿐 표본 자체는 정상 보고 — 헤더 매치는
#    Markdown ``#``/``##``/``###`` 로 시작하는 라인만 인정.
# 2. **모듈명 직접 언급**: ``workflow_drift_stats`` / ``drift_stats.py`` 가 표본
#    같은 라인 또는 ±2 라인에 등장하면 self 분류 (C 트랙이 자기 LoC 를 측정).
#
# 이렇게 분리하면 R23 의 "10. self-report drift 명시" 섹션은 정확히 잡고,
# R22 표 헤더 "자기 보고 drift" 같은 메타용어는 무시한다.
# 섹션 헤더 self 매치는 *명확한 self-report 절* 만 인정.
# 단순 부가 설명 ("자기 보고 정확도" 같은) 은 메타용어로 보고 제외.
# 매치 필수 표현: 'drift', 'LoC', '자체', '명시', '본 보고서' 중 하나가
# 키워드와 함께 등장해야 함.
_SECTION_SELF_KEYWORDS: Tuple[str, ...] = (
    "self-report drift",
    "self report drift",
    "자기 보고 drift",
    "자기보고 drift",
    "자기 drift 보고",
    "자기 loc",  # 자기 LoC 자기 측정
    "자기 측정 drift",
    "자체 보고 drift",
    "자체보고 drift",
    "자가 정직",  # 단독 (자체 의미 강함)
    "본 보고서 자체",
)
_MODULE_SELF_KEYWORDS: Tuple[str, ...] = (
    "workflow_drift_stats",
    "drift_stats.py",
)
_SELF_MODULE_WINDOW = 2  # 모듈명 자기 언급 컨텍스트
_RE_MD_HEADER = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


# ── dataclass ───────────────────────────────────────────────────────────
@dataclass
class DriftSample:
    """드리프트 단일 표본."""
    round: str
    track: str                # 'A'/'B'/...  또는 '?' (트랙 미식별)
    track_name: str = ""      # 'LLM apply' 같은 자유 라벨 (참고용)
    reported: Optional[float] = None
    actual: Optional[float] = None
    drift_pct: float = 0.0    # signed (실측-보고)/max(|보고|,|실측|)
    abs_drift_pct: float = 0.0
    kind: str = "loc"         # 'loc' / 'sync_block' / 'pct_only'
    source_line: int = 0
    # R24 트랙 C: self-report 재귀 편향 보정.
    # True 이면 통계 집계에서 기본 제외 (self_drift 별도 섹션에 분리 보고).
    is_self_report: bool = False
    self_marker: str = ""     # 매치된 키워드 (디버그용)


@dataclass
class RoundStats:
    """라운드별 집계."""
    round: str
    n: int = 0
    mean_abs_pct: float = 0.0   # 절댓값 평균
    std_abs_pct: float = 0.0
    signed_mean_pct: float = 0.0
    max_abs_pct: float = 0.0
    distribution: Dict[str, int] = field(default_factory=dict)
    trust_score: float = 100.0
    trust_level: str = "normal"  # R25 — critical/warning/normal
    systematic_bias: Optional[str] = None  # 'over_report' / 'under_report' / None


@dataclass
class TrackStats:
    """라운드 X 트랙 신뢰도 — agent 차등 신뢰도 표의 행."""
    round: str
    track: str
    track_name: str = ""
    n: int = 0
    mean_abs_pct: float = 0.0
    std_abs_pct: float = 0.0
    signed_mean_pct: float = 0.0
    max_abs_pct: float = 0.0
    trust_score: float = 100.0
    trust_level: str = "normal"  # R25 — critical/warning/normal
    systematic_bias: Optional[str] = None


# ── 보조 함수 ────────────────────────────────────────────────────────────
def _to_float(s: Optional[str]) -> Optional[float]:
    if s is None:
        return None
    s = s.strip().rstrip("%").replace(",", "").replace("_", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _round_from_filename(p: Path) -> str:
    m = _RE_ROUND_FROM_NAME.match(p.name)
    return m.group(1) if m else p.stem


def _pct(reported: float, actual: float) -> float:
    """signed drift = (actual - reported) / max(|reported|, |actual|, eps)."""
    denom = max(abs(reported), abs(actual), 1e-9)
    return (actual - reported) / denom


def _find_track_for_line(track_lines: List[Tuple[int, str, str]], ln: int) -> Tuple[str, str]:
    """해당 라인의 직전 표 행에서 (track_code, track_name) 추론. 없으면 ('?', '')."""
    # track_lines: 정렬됨 (line asc)
    track = "?"
    name = ""
    for tl, code, nm in track_lines:
        if tl <= ln:
            track = code
            name = nm
        else:
            break
    return track, name


def _build_self_section_ranges(lines: List[str]) -> List[Tuple[int, int, str]]:
    """self-report *섹션* (헤더 키워드 매치) 의 라인 범위 목록.

    각 항목: (start_line_1based, end_line_1based, matched_kw).
    end 는 다음 동급 (또는 상위) 헤더 직전 라인, 마지막 섹션이면 EOF.
    """
    headers: List[Tuple[int, int, str]] = []  # (line, level, text)
    for ln, raw in enumerate(lines, start=1):
        m = _RE_MD_HEADER.match(raw)
        if m:
            level = len(m.group(1))
            text = m.group(2)
            headers.append((ln, level, text))
    ranges: List[Tuple[int, int, str]] = []
    for i, (ln, lvl, text) in enumerate(headers):
        text_lc = text.lower()
        matched = ""
        for kw in _SECTION_SELF_KEYWORDS:
            if kw.lower() in text_lc:
                matched = kw
                break
        if not matched:
            continue
        # 다음 동급/상위 헤더 찾기
        end_ln = len(lines)
        for nxt_ln, nxt_lvl, _ in headers[i + 1:]:
            if nxt_lvl <= lvl:
                end_ln = nxt_ln - 1
                break
        ranges.append((ln, end_ln, matched))
    return ranges


def _detect_self_report(
    lines: List[str],
    line_idx0: int,
    self_sections: Optional[List[Tuple[int, int, str]]] = None,
) -> Tuple[bool, str]:
    """``line_idx0`` (0-based) 표본 라인이 self-report 인지 판정.

    두 조건 중 하나라도 만족하면 True:
    1) 라인이 self-report 섹션 범위 안에 있음 (헤더 키워드 매치 섹션)
    2) 모듈명 자기 언급 (``workflow_drift_stats`` / ``drift_stats.py``) 이
       ±2 라인에 등장

    Returns
    -------
    (is_self, marker)
    """
    ln_1based = line_idx0 + 1
    # 섹션 매치
    if self_sections is None:
        self_sections = _build_self_section_ranges(lines)
    for start, end, kw in self_sections:
        if start <= ln_1based <= end:
            return True, f"section:{kw}"
    # 모듈명 ±_SELF_MODULE_WINDOW 라인 검색
    lo = max(0, line_idx0 - _SELF_MODULE_WINDOW)
    hi = min(len(lines), line_idx0 + _SELF_MODULE_WINDOW + 1)
    blob = "\n".join(lines[lo:hi]).lower()
    for kw in _MODULE_SELF_KEYWORDS:
        if kw.lower() in blob:
            return True, f"module:{kw}"
    return False, ""


# ── 보고서 1편 파싱 ──────────────────────────────────────────────────────
def parse_report_samples(report_path: Path) -> List[DriftSample]:
    """보고서 1편의 drift 표본 추출 (LoC + sync_block + pct_only)."""
    if not report_path.is_file():
        return []
    text = report_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    round_id = _round_from_filename(report_path)

    # 1차 패스: 트랙 셀 위치 색인
    track_lines: List[Tuple[int, str, str]] = []
    for ln, raw in enumerate(lines, start=1):
        m = _RE_TRACK_CELL.match(raw)
        if m:
            code = m.group(1).upper()
            # 구분 셀 (|---|) 도 매칭될 수 있어 제외 (track_name 이 '-' 만)
            if not re.fullmatch(r"[-\s]*", m.group(2) or ""):
                track_lines.append((ln, code, m.group(2).strip()))

    samples: List[DriftSample] = []
    # self-section 범위 1회 계산 (재사용)
    self_sections = _build_self_section_ranges(lines)

    # 2차 패스: LoC drift 패턴 (+ self-report 감지)
    for ln, raw in enumerate(lines, start=1):
        line_idx0 = ln - 1  # 0-based for context window
        is_self, marker = _detect_self_report(lines, line_idx0, self_sections)
        # 패턴 1: "보고 X vs 실측 Y"
        for m in _RE_REPORT_VS_ACTUAL.finditer(raw):
            rep = _to_float(m.group(1))
            act = _to_float(m.group(2))
            if rep is None or act is None or rep <= 0:
                continue
            d = _pct(rep, act)
            tk, tn = _find_track_for_line(track_lines, ln)
            samples.append(DriftSample(
                round=round_id, track=tk, track_name=tn,
                reported=rep, actual=act,
                drift_pct=d, abs_drift_pct=abs(d),
                kind="loc", source_line=ln,
                is_self_report=is_self, self_marker=marker,
            ))
        # 패턴 3·4: "(실측 X lines, 보고 Y lines 차이)"
        for m in _RE_ACTUAL_VS_REPORT.finditer(raw):
            act = _to_float(m.group(1))
            rep = _to_float(m.group(2))
            if rep is None or act is None or rep <= 0:
                continue
            d = _pct(rep, act)
            tk, tn = _find_track_for_line(track_lines, ln)
            samples.append(DriftSample(
                round=round_id, track=tk, track_name=tn,
                reported=rep, actual=act,
                drift_pct=d, abs_drift_pct=abs(d),
                kind="loc", source_line=ln,
                is_self_report=is_self, self_marker=marker,
            ))
        # 패턴 5: "보고 X 대비 +Y%"  (drift 직접 캡처)
        for m in _RE_REPORT_VS_PCT.finditer(raw):
            rep = _to_float(m.group(1))
            pct = _to_float(m.group(2))
            if rep is None or pct is None:
                continue
            d = pct / 100.0
            tk, tn = _find_track_for_line(track_lines, ln)
            samples.append(DriftSample(
                round=round_id, track=tk, track_name=tn,
                reported=rep, actual=None,
                drift_pct=d, abs_drift_pct=abs(d),
                kind="pct_only", source_line=ln,
                is_self_report=is_self, self_marker=marker,
            ))

    # 3차 패스: workflow-sync 블록 (가장 정확한 측정)
    in_block = False
    for ln, raw in enumerate(lines, start=1):
        if _RE_SYNC_BLOCK_BEGIN.search(raw):
            in_block = True
            continue
        if _RE_SYNC_BLOCK_END.search(raw):
            in_block = False
            continue
        if not in_block:
            continue
        m = _RE_SYNC_ROW.match(raw)
        if not m:
            continue
        # m.group(1)=metric, (2)=reported, (3)=actual, (4)=drift_pct (string)
        rep = _to_float(m.group(2))
        act = _to_float(m.group(3))
        d = (_to_float(m.group(4)) or 0.0) / 100.0
        tk, tn = _find_track_for_line(track_lines, ln)
        is_self, marker = _detect_self_report(lines, ln - 1, self_sections)
        samples.append(DriftSample(
            round=round_id, track=tk, track_name=tn,
            reported=rep, actual=act,
            drift_pct=d, abs_drift_pct=abs(d),
            kind="sync_block", source_line=ln,
            is_self_report=is_self, self_marker=marker,
        ))

    return samples


# ── 통계 계산 ────────────────────────────────────────────────────────────
def _trust_score(mean_abs: float, std_abs: float) -> float:
    """0~100 신뢰도 점수.

    mean_abs/std_abs 는 *비율* (0.0~1.0 = 0~100%) 형식.
    """
    m = max(0.0, min(1.0, mean_abs))
    s = max(0.0, min(0.5, std_abs / 2.0))
    return round(100.0 * (1.0 - m) * (1.0 - s), 1)


def _systematic_bias(signed_mean: float, mean_abs: float) -> Optional[str]:
    if mean_abs < 1e-6:
        return None
    if abs(signed_mean) < 0.5 * mean_abs:
        return None
    return "under_report" if signed_mean > 0 else "over_report"


def _distribution(samples: List[DriftSample]) -> Dict[str, int]:
    """절댓값 % 분포 히스토그램."""
    out: Dict[str, int] = {name: 0 for name, *_ in _BINS}
    for s in samples:
        v = abs(s.drift_pct) * 100.0  # → %
        for name, lo, hi in _BINS:
            if lo <= v < hi:
                out[name] += 1
                break
    return out


def _aggregate_round(round_id: str, samples: List[DriftSample]) -> RoundStats:
    if not samples:
        return RoundStats(round=round_id)
    abs_pcts = [abs(s.drift_pct) for s in samples]
    signed = [s.drift_pct for s in samples]
    mean_abs = statistics.fmean(abs_pcts)
    std_abs = statistics.pstdev(abs_pcts) if len(abs_pcts) > 1 else 0.0
    signed_mean = statistics.fmean(signed)
    trust = _trust_score(mean_abs, std_abs)
    return RoundStats(
        round=round_id,
        n=len(samples),
        mean_abs_pct=round(mean_abs * 100, 2),
        std_abs_pct=round(std_abs * 100, 2),
        signed_mean_pct=round(signed_mean * 100, 2),
        max_abs_pct=round(max(abs_pcts) * 100, 2),
        distribution=_distribution(samples),
        trust_score=trust,
        trust_level=classify_trust(trust),
        systematic_bias=_systematic_bias(signed_mean, mean_abs),
    )


def _aggregate_track(round_id: str, track: str,
                     samples: List[DriftSample]) -> TrackStats:
    if not samples:
        return TrackStats(round=round_id, track=track)
    abs_pcts = [abs(s.drift_pct) for s in samples]
    signed = [s.drift_pct for s in samples]
    mean_abs = statistics.fmean(abs_pcts)
    std_abs = statistics.pstdev(abs_pcts) if len(abs_pcts) > 1 else 0.0
    signed_mean = statistics.fmean(signed)
    # 가장 자주 등장한 track_name 사용 (라운드 내 한 트랙 의미 단일)
    names = [s.track_name for s in samples if s.track_name]
    name = ""
    if names:
        # mode 동률이면 첫번째
        try:
            name = statistics.mode(names)
        except statistics.StatisticsError:
            name = names[0]
    trust = _trust_score(mean_abs, std_abs)
    return TrackStats(
        round=round_id, track=track, track_name=name,
        n=len(samples),
        mean_abs_pct=round(mean_abs * 100, 2),
        std_abs_pct=round(std_abs * 100, 2),
        signed_mean_pct=round(signed_mean * 100, 2),
        max_abs_pct=round(max(abs_pcts) * 100, 2),
        trust_score=trust,
        trust_level=classify_trust(trust),
        systematic_bias=_systematic_bias(signed_mean, mean_abs),
    )


# ── 외부 진입점 ──────────────────────────────────────────────────────────
def compute(
    report_paths: List[Path],
    exclude_self: bool = True,
) -> Dict[str, Any]:
    """여러 보고서를 한꺼번에 처리 → 통계 + 신뢰도.

    Parameters
    ----------
    report_paths
        보고서 경로 리스트.
    exclude_self
        True (기본) — self-report 표본을 통계 집계에서 제외하고 ``self_drift``
        섹션에 분리 보고. R23 권고 C (재귀 편향 +26.45% 제거) 의 본체.
        False — 종전 (R23 이전) 호환 동작.

    응답::

        {
          "generated_at_utc": "...",
          "rounds": [
            {
              "round": "R20",
              "n": 6,
              "mean_abs_pct": 30.5,
              "std_abs_pct": 12.3,
              "signed_mean_pct": +30.5,
              "max_abs_pct": 68.0,
              "distribution": {"0-5":1, "5-10":0, "10-20":1, "20-50":3, ">=50":1},
              "trust_score": 65.4,
              "systematic_bias": "under_report"
            },
            ...
          ],
          "tracks": [
            {"round":"R20","track":"B","track_name":"Crisis 한국...",
             "n":1,"mean_abs_pct":29.6,"std_abs_pct":0.0,
             "signed_mean_pct":+29.6,"max_abs_pct":29.6,
             "trust_score":70.4,"systematic_bias":null},
            ...
          ],
          "overall": {
             "rounds_analyzed": 5,
             "total_samples": 22,
             "mean_trust": 71.2,
             "weakest": {"round":"R21","trust_score":52.1},
             "strongest": {"round":"R18","trust_score":91.4}
          },
          "available": true
        }
    """
    all_samples: List[DriftSample] = []
    for rp in report_paths:
        all_samples.extend(parse_report_samples(rp))

    # self-report 분리 (R24 트랙 C 보정)
    if exclude_self:
        active_samples = [s for s in all_samples if not s.is_self_report]
        self_samples = [s for s in all_samples if s.is_self_report]
    else:
        active_samples = list(all_samples)
        self_samples = []

    # 라운드별 집계 (self 제외)
    by_round: Dict[str, List[DriftSample]] = {}
    for s in active_samples:
        by_round.setdefault(s.round, []).append(s)
    round_stats = [
        _aggregate_round(r, by_round[r])
        for r in sorted(by_round.keys())
    ]

    # 라운드 X 트랙 집계 (self 제외)
    by_rt: Dict[Tuple[str, str], List[DriftSample]] = {}
    for s in active_samples:
        # track '?' 도 별도 그룹 (트랙 미식별 표본)
        by_rt.setdefault((s.round, s.track), []).append(s)
    track_stats = [
        _aggregate_track(r, t, by_rt[(r, t)])
        for (r, t) in sorted(by_rt.keys())
    ]

    # R25 — trust 임계 (env 또는 기본 60/80)
    crit_th, warn_th = _trust_thresholds()
    overall: Dict[str, Any] = {
        "rounds_analyzed": len(round_stats),
        "total_samples": len(active_samples),
        "self_samples_excluded": len(self_samples),
        "exclude_self": exclude_self,
        "trust_thresholds": {
            "critical_below": crit_th,
            "warning_below": warn_th,
        },
    }
    if round_stats:
        trusts = [rs.trust_score for rs in round_stats]
        overall["mean_trust"] = round(statistics.fmean(trusts), 1)
        overall["mean_trust_level"] = classify_trust(overall["mean_trust"])
        # R25 — level별 카운트 (운영 알림 발화 패턴 추적).
        level_counts: Dict[str, int] = {"critical": 0, "warning": 0, "normal": 0}
        for rs in round_stats:
            level_counts[rs.trust_level] = level_counts.get(rs.trust_level, 0) + 1
        overall["trust_level_counts"] = level_counts
        weakest = min(round_stats, key=lambda rs: rs.trust_score)
        strongest = max(round_stats, key=lambda rs: rs.trust_score)
        overall["weakest"] = {"round": weakest.round,
                              "trust_score": weakest.trust_score,
                              "trust_level": weakest.trust_level}
        overall["strongest"] = {"round": strongest.round,
                                "trust_score": strongest.trust_score,
                                "trust_level": strongest.trust_level}

    # self_drift 별도 집계 (분리 보고, 신뢰도 점수에는 비반영)
    self_drift_section: Dict[str, Any] = {"n": len(self_samples)}
    if self_samples:
        abs_pcts = [abs(s.drift_pct) for s in self_samples]
        signed = [s.drift_pct for s in self_samples]
        self_drift_section["mean_abs_pct"] = round(
            statistics.fmean(abs_pcts) * 100, 2
        )
        self_drift_section["signed_mean_pct"] = round(
            statistics.fmean(signed) * 100, 2
        )
        self_drift_section["max_abs_pct"] = round(
            max(abs_pcts) * 100, 2
        )

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rounds": [asdict(rs) for rs in round_stats],
        "tracks": [asdict(ts) for ts in track_stats],
        "samples": [asdict(s) for s in active_samples],
        "self_samples": [asdict(s) for s in self_samples],
        "self_drift": self_drift_section,
        "overall": overall,
        "available": len(active_samples) > 0 or len(self_samples) > 0,
    }


# ── R27 트랙 D — trust 7일 분포 + 임계 정밀화 ────────────────────────────
# 목표: 보고서 drift trust + audit JSONL trust 를 통합한 *7일 분포* 산출 +
# 분포 기반 임계 (critical/warning) 정밀화 권고.
#
# 핵심 관찰 (R20~R26 수집):
#   * drift trust 표본은 R20=76.5 / R21=64.9 / R22=58.9 만 검출 (R23~R26 파싱 0).
#   * audit trust 표본은 R24=100 / R25=100 / R26=0 / R26_TEST=0 / unlabeled=95.8.
#     R26 viol_ratio=2.0 capped → trust=0 인 *outlier* (1 run / 2 violations).
#   * 두 source 합쳤을 때 mean=62 / median=70.7 / std=43 — 양봉 분포.
#
# 임계 정밀화 알고리즘 (data-driven):
#   1. P25 / P50 / P75 quantile 계산.
#   2. 권장 critical = clamp(P25 - 5, 0, 95).
#   3. 권장 warning  = clamp(P50 + 5, critical + 5, 100).
#   4. 현재 (60/80) 와의 diff 가 ±5 이내면 'keep_current' 권고.
#      그 외엔 'tighten' (현재가 너무 느슨) 또는 'loosen' (현재가 너무 빡셈).
#
# graceful: 표본 < 3 이면 권고는 'insufficient_samples' 로 nil 반환.
def _percentile(sorted_vals: List[float], q: float) -> float:
    """선형 보간 백분위수 (q ∈ [0, 100]).  sorted_vals 는 오름차순."""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = (q / 100.0) * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_vals[lo]
    frac = pos - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def compute_trust_7day_distribution(
    reports_dir: Path,
    audit_path: Optional[Path] = None,
    days: int = 7,
    drift_rounds: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """7일 trust 분포 + 임계 정밀화 권고.

    Parameters
    ----------
    reports_dir
        ``docs/dashboard`` 디렉터리.
    audit_path
        ``reports/backfill_audit.jsonl`` 경로.  None 이면 audit 측 건너뛰고
        drift trust 만 사용.
    days
        7일 윈도우 (audit 측 필터링에만 의미; drift 측은 라운드 전체).
    drift_rounds
        drift 통계에 사용할 라운드 목록.  None 이면 reports_dir 의 전체 R*.md.

    Returns
    -------
    {
      "generated_at_utc": "...",
      "window_days": 7,
      "available": true,
      "trust_samples": {
        "drift": [{"round":"R20","trust":76.5,"level":"warning"}, ...],
        "audit": [{"round":"R24","trust":100.0,"level":"normal"}, ...],
        "combined": [76.5, 64.9, ...]
      },
      "stats": {
        "n": 8,
        "mean": 62.0, "median": 70.7, "std": 42.6,
        "min": 0.0, "max": 100.0,
        "p25": 36.1, "p50": 70.7, "p75": 98.9
      },
      "current_thresholds": {"critical_below": 60.0, "warning_below": 80.0},
      "recommendation": {
        "action": "tighten" | "loosen" | "keep_current" | "insufficient_samples",
        "suggested_critical_below": 31.1,
        "suggested_warning_below":  75.7,
        "rationale": "..."
      }
    }
    """
    out: Dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window_days": int(days),
        "available": False,
        "trust_samples": {"drift": [], "audit": [], "combined": []},
        "stats": {},
        "current_thresholds": {},
        "recommendation": {},
    }
    crit_th, warn_th = _trust_thresholds()
    out["current_thresholds"] = {
        "critical_below": crit_th, "warning_below": warn_th,
    }

    # ── drift trust 표본 ────────────────────────────────────────────────
    drift_samples: List[Dict[str, Any]] = []
    if reports_dir and reports_dir.is_dir():
        paths = _select_reports(
            reports_dir,
            rounds=drift_rounds,
            all_reports=drift_rounds is None,
        )
        if paths:
            drift_result = compute(paths)
            for rs in drift_result.get("rounds", []):
                drift_samples.append({
                    "round": rs["round"],
                    "trust": float(rs["trust_score"]),
                    "level": rs.get("trust_level", "normal"),
                })

    # ── audit trust 표본 ────────────────────────────────────────────────
    audit_samples: List[Dict[str, Any]] = []
    if audit_path and audit_path.is_file():
        try:
            from insight.backfill_audit_monitor import (  # type: ignore
                _load_jsonl, summarize,
            )
        except Exception:
            try:
                from .backfill_audit_monitor import (  # type: ignore
                    _load_jsonl, summarize,
                )
            except Exception:
                _load_jsonl = None
                summarize = None
        if _load_jsonl is not None and summarize is not None:
            runs = _load_jsonl(audit_path)
            if runs:
                payload = summarize(runs, window_days=int(days))
                for rnd, slot in (payload.get("by_round") or {}).items():
                    runs_n = int(slot.get("runs") or 0)
                    ok_n = int(slot.get("ok") or 0)
                    viol_n = int(slot.get("violations") or 0)
                    if runs_n <= 0:
                        continue
                    ok_ratio = ok_n / runs_n
                    viol_ratio = viol_n / runs_n
                    viol_clamped = max(0.0, min(1.0, viol_ratio))
                    trust = round(100.0 * ok_ratio * (1.0 - viol_clamped), 1)
                    audit_samples.append({
                        "round": rnd,
                        "trust": trust,
                        "level": classify_trust(trust),
                    })

    combined = [s["trust"] for s in drift_samples] + [
        s["trust"] for s in audit_samples
    ]
    out["trust_samples"] = {
        "drift": drift_samples,
        "audit": audit_samples,
        "combined": combined,
    }

    if not combined:
        out["recommendation"] = {
            "action": "insufficient_samples",
            "rationale": "drift / audit 양측 모두 표본 없음",
        }
        return out

    out["available"] = True
    sorted_v = sorted(combined)
    n = len(sorted_v)
    mean = statistics.fmean(sorted_v)
    median = statistics.median(sorted_v)
    std = statistics.pstdev(sorted_v) if n > 1 else 0.0
    p25 = _percentile(sorted_v, 25)
    p50 = _percentile(sorted_v, 50)
    p75 = _percentile(sorted_v, 75)
    out["stats"] = {
        "n": n,
        "mean": round(mean, 1),
        "median": round(median, 1),
        "std": round(std, 1),
        "min": round(sorted_v[0], 1),
        "max": round(sorted_v[-1], 1),
        "p25": round(p25, 1),
        "p50": round(p50, 1),
        "p75": round(p75, 1),
    }

    # 임계 권고 (n >= 3 일 때만 계산).
    if n < 3:
        out["recommendation"] = {
            "action": "insufficient_samples",
            "rationale": f"n={n} (< 3) — 1 라운드 추가 관측 권장",
        }
        return out

    sug_crit = max(0.0, min(95.0, round(p25 - 5.0, 1)))
    sug_warn = max(sug_crit + 5.0, min(100.0, round(p50 + 5.0, 1)))
    diff_crit = sug_crit - crit_th
    diff_warn = sug_warn - warn_th
    # 권고 분류:
    #   |diff| <= 5 양쪽 → keep_current
    #   sug 가 현재보다 크게 낮음 (-5 이상) → loosen (현재가 너무 빡셈)
    #   sug 가 현재보다 크게 높음 (+5 이상) → tighten (현재가 너무 느슨)
    if abs(diff_crit) <= 5.0 and abs(diff_warn) <= 5.0:
        action = "keep_current"
        rationale = (
            f"P25={p25:.1f} P50={p50:.1f} → 권장 {sug_crit:.0f}/{sug_warn:.0f} "
            f"가 현재 {crit_th:.0f}/{warn_th:.0f} 와 ±5 이내. 유지 권고."
        )
    elif diff_crit < -5.0 or diff_warn < -5.0:
        action = "loosen"
        rationale = (
            f"P25={p25:.1f} (현재 critical {crit_th:.0f} 보다 낮음) → "
            f"권장 critical {sug_crit:.0f} / warning {sug_warn:.0f} 로 완화. "
            f"현재 임계는 분포보다 빡셈 (false-positive 위험)."
        )
    else:
        action = "tighten"
        rationale = (
            f"P25={p25:.1f} (현재 critical {crit_th:.0f} 보다 높음) → "
            f"권장 critical {sug_crit:.0f} / warning {sug_warn:.0f} 로 강화. "
            f"현재 임계는 분포보다 느슨 (false-negative 위험)."
        )

    out["recommendation"] = {
        "action": action,
        "suggested_critical_below": sug_crit,
        "suggested_warning_below": sug_warn,
        "diff_vs_current": {
            "critical": round(diff_crit, 1),
            "warning": round(diff_warn, 1),
        },
        "rationale": rationale,
    }
    return out


# ── 보고서 선택 (workflow_validator 와 동일 규약) ─────────────────────────
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
    """사람 가독 표 — 라운드 통계 + 신뢰도 점수."""
    rows: List[str] = []
    rows.append("# Workflow drift 통계")
    rows.append("")
    rows.append(f"생성: {result['generated_at_utc']}  "
                f"available={result['available']}")
    rows.append("")
    rows.append("## 라운드별 통계")
    rows.append("")
    rows.append("| round | n | mean abs% | std% | signed mean% | "
                "max abs% | trust | level | bias |")
    rows.append("|---|---:|---:|---:|---:|---:|---:|---|---|")
    for rs in result["rounds"]:
        rows.append(
            f"| {rs['round']} | {rs['n']} | {rs['mean_abs_pct']:.2f} "
            f"| {rs['std_abs_pct']:.2f} | {rs['signed_mean_pct']:+.2f} "
            f"| {rs['max_abs_pct']:.2f} | **{rs['trust_score']:.1f}** "
            f"| {rs.get('trust_level', 'normal')} "
            f"| {rs.get('systematic_bias') or '—'} |"
        )
    rows.append("")
    rows.append("## 라운드 X 트랙 신뢰도")
    rows.append("")
    rows.append("| round | track | name | n | mean abs% | std% | "
                "signed% | trust |")
    rows.append("|---|---|---|---:|---:|---:|---:|---:|")
    for ts in result["tracks"]:
        nm = (ts.get("track_name") or "")[:30]
        rows.append(
            f"| {ts['round']} | {ts['track']} | {nm} | {ts['n']} "
            f"| {ts['mean_abs_pct']:.2f} | {ts['std_abs_pct']:.2f} "
            f"| {ts['signed_mean_pct']:+.2f} | **{ts['trust_score']:.1f}** |"
        )
    rows.append("")
    o = result.get("overall", {})
    if o.get("rounds_analyzed"):
        rows.append("## 종합")
        rows.append("")
        rows.append(f"- 분석 라운드: {o['rounds_analyzed']}  "
                    f"표본 (active): {o['total_samples']}  "
                    f"평균 trust: {o.get('mean_trust')}")
        if o.get("weakest"):
            rows.append(f"- 최약: {o['weakest']['round']} "
                        f"(trust={o['weakest']['trust_score']})")
        if o.get("strongest"):
            rows.append(f"- 최강: {o['strongest']['round']} "
                        f"(trust={o['strongest']['trust_score']})")
    # self-drift 섹션 (재귀 편향 보정 결과)
    sd = result.get("self_drift") or {}
    if sd.get("n"):
        rows.append("")
        rows.append("## self-report drift (집계 제외)")
        rows.append("")
        rows.append(f"- n={sd['n']}  "
                    f"mean_abs={sd.get('mean_abs_pct', 0)}%  "
                    f"signed_mean={sd.get('signed_mean_pct', 0)}%  "
                    f"max_abs={sd.get('max_abs_pct', 0)}%")
        rows.append(f"- exclude_self={o.get('exclude_self')}  "
                    f"(R24 트랙 C 재귀 편향 보정)")
    return "\n".join(rows)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--report", action="append", default=[],
                   help="단일 보고서 경로 (반복 가능).")
    p.add_argument("--rounds", default="",
                   help="콤마 구분 round 코드 (예: R20,R21,R22).")
    p.add_argument("--all", action="store_true",
                   help="docs/dashboard/R*.md 전체.")
    p.add_argument("--reports-dir", default=str(REPORTS_DIR_DEFAULT),
                   help="기본: docs/dashboard")
    p.add_argument("--json", action="store_true",
                   help="JSON 출력.")
    p.add_argument("--include-self", action="store_true",
                   help="self-report 표본도 통계에 포함 (R23 이전 호환). "
                        "기본은 분리 — R24 트랙 C 재귀 편향 보정.")
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
    # 중복 제거
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

    result = compute(unique, exclude_self=not args.include_self)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(_fmt_table(result))
    return 0 if result["available"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
