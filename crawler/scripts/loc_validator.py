"""LoC validator — 워크플로우 보고서의 LoC 표기 vs 실측 검증.

R20 권고 2 "query 정합성 검증" 의 LoC 측면 구현.

기능:
  1) ``docs/dashboard/R*.md`` 보고서를 파싱하여 LoC 표기 추출.
     지원 패턴:
       (a) "실측 N LoC" / "실측 N lines" / "실측 N 줄"   → 보고 N 줄
       (b) "보고 N LoC" / "보고 N lines"                → 보고 N 줄
       (c) "`path/to/file.py` (N lines)"                → 보고 N 줄
       (d) "`path/to/file.py` (N LoC)"                  → 보고 N 줄
  2) 파일 경로를 함께 추출 (백틱으로 둘러싸인 path 토큰).
  3) repo 의 실제 LoC 측정 (Path.exists() + wc -l 동등).
  4) |drift| / max(report, actual) > 0.20 면 alert.

CLI::

    python scripts/loc_validator.py [--report path|--all|--rounds R18,R19,R20]
                                    [--threshold 0.20]
                                    [--json]

종속성 없음 — 표준 라이브러리 only. backend endpoint 가 동일 모듈을 임포트한다.

R21 트랙 B 신규.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── repo 루트 ─────────────────────────────────────────────────────────────
# crawler/scripts/loc_validator.py → repo_root
REPO_ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR_DEFAULT = REPO_ROOT / "docs" / "dashboard"

# ── 패턴 정의 ────────────────────────────────────────────────────────────
# 백틱 path 토큰: `crawler/scripts/foo.py` 또는 `backend/app/api/_internal.py`
# 확장자 .py / .ts / .tsx / .js / .jsx / .sql / .md / .yml / .yaml 만 추출.
_PATH_RE = re.compile(
    r"`([A-Za-z0-9_./\-]+\.(?:py|ts|tsx|js|jsx|sql|md|yml|yaml))`"
)

# "실측 N LoC" / "실측 N lines" / "실측 N 줄"  (N 정수)
_ACTUAL_RE = re.compile(
    r"실측\s*([0-9,]+)\s*(?:LoC|lines?|줄)", re.IGNORECASE
)

# "보고 N LoC" / "보고 N lines" / "보고 N 줄"
_REPORTED_RE = re.compile(
    r"보고\s*([0-9,]+)\s*(?:LoC|lines?|줄)", re.IGNORECASE
)

# "`path/to/file.py` (N lines)"  /  "`path/to/file.py` (N LoC)"
# path 직후 괄호 안에 숫자+단위만 있는 패턴 (다른 코드 식별자 텍스트와 구분).
_INLINE_LOC_RE = re.compile(
    r"`([A-Za-z0-9_./\-]+\.(?:py|ts|tsx|js|jsx|sql))`\s*"
    r"\(\s*([0-9,]+)\s*(?:LoC|lines?|줄)\s*\)",
    re.IGNORECASE,
)


@dataclass
class LocClaim:
    """단일 파일에 대한 LoC 표기 + 실측."""

    round: str          # "R18" / "R19" / "R20" 등
    file: str           # repo 상대 경로
    reported: Optional[int]   # 보고서가 명시한 LoC. 없으면 None.
    actual: Optional[int]     # 실측 LoC. 파일 없으면 None.
    drift: Optional[int]      # actual - reported. 둘 중 하나라도 None 이면 None.
    drift_pct: Optional[float]  # drift / max(reported, actual) (양수 가능).
    source_lines: List[int]   # 보고서 내 표기 등장 line 번호 (1-based)
    alert: bool               # |drift_pct| > threshold


def _to_int(s: str) -> int:
    return int(s.replace(",", ""))


def _count_lines(path: Path) -> Optional[int]:
    """파일의 line 개수. 파일 없으면 None.

    바이너리 파일은 의도적으로 제외하지 않는다 (확장자 필터로 사전 차단).
    UTF-8 디코드 에러는 errors='replace' 로 무시 — line count 만 필요.
    """
    if not path.is_file():
        return None
    try:
        with path.open("rb") as fh:
            # b'\n' count + (마지막 줄이 \n 으로 끝나지 않으면 +1).
            data = fh.read()
        if not data:
            return 0
        n = data.count(b"\n")
        if not data.endswith(b"\n"):
            n += 1
        return n
    except OSError:
        return None


def _round_from_filename(report: Path) -> str:
    """`R20_STABILIZE_2026-06-05.md` → 'R20'.

    인식 실패 시 파일명 stem 그대로 반환.
    """
    m = re.match(r"(R\d+)[_\-A-Za-z]", report.name)
    if m:
        return m.group(1)
    return report.stem


def _resolve_repo_path(rel: str) -> Path:
    """보고서에서 추출한 상대 경로 → repo 절대 경로.

    'tests/test_foo.py' 처럼 crawler 접두 없이 적힌 경우는 (a) crawler/ 와
    (b) backend/ 양쪽에서 존재 여부 확인 (crawler 우선).
    """
    p = REPO_ROOT / rel
    if p.is_file():
        return p
    # tests/* 로 시작하면 crawler/tests/* 또는 backend/tests/* 추론.
    if rel.startswith("tests/"):
        for prefix in ("crawler", "backend"):
            cand = REPO_ROOT / prefix / rel
            if cand.is_file():
                return cand
    return p  # is_file False 라도 그대로 반환 (count_lines 가 None 처리).


def parse_report(
    report_path: Path,
    *,
    threshold: float = 0.20,
) -> List[LocClaim]:
    """보고서 하나를 파싱하여 LoC claim 리스트 반환.

    중복 (같은 파일 여러 번 등장) 은 *첫 표기 우선* — 같은 파일에 reported 값이
    다르게 있으면 둘 다 별개 claim 으로 추가 (보고서 내부 모순 노출 목적).
    """
    if not report_path.is_file():
        return []
    text = report_path.read_text(encoding="utf-8", errors="replace")
    round_id = _round_from_filename(report_path)
    lines = text.splitlines()

    claims: List[LocClaim] = []
    # 패턴 (c)/(d): 인라인 path (N lines)
    for ln_idx, line in enumerate(lines, start=1):
        for m in _INLINE_LOC_RE.finditer(line):
            file_rel = m.group(1)
            reported = _to_int(m.group(2))
            actual = _count_lines(_resolve_repo_path(file_rel))
            claims.append(
                _build_claim(round_id, file_rel, reported, actual,
                             [ln_idx], threshold)
            )

    # 패턴 (a)+(b): "실측 X LoC, 보고 Y LoC" — 같은 라인에 path 가 있어야
    # 어떤 파일에 대한 표기인지 알 수 있다. 본 패턴은 R20 표 형식이 대상.
    for ln_idx, line in enumerate(lines, start=1):
        paths_in_line = _PATH_RE.findall(line)
        if not paths_in_line:
            continue
        actual_m = _ACTUAL_RE.search(line)
        reported_m = _REPORTED_RE.search(line)
        if not (actual_m or reported_m):
            continue
        # 같은 라인에 여러 path 가 있을 수 있으므로 첫 *코드 파일* 사용.
        first_code = next(
            (p for p in paths_in_line if not p.endswith(".md")), None
        )
        if first_code is None:
            continue
        # 인라인 패턴 (c)/(d) 와 겹치는 경우 중복 방지: 이미 reported 가
        # 같은 파일/같은 라인으로 등록되어 있으면 스킵.
        already = any(
            c.file == first_code and ln_idx in c.source_lines
            for c in claims
        )
        if already:
            continue
        reported = _to_int(reported_m.group(1)) if reported_m else None
        # actual 표기가 명시되어 있어도, 우리는 *실측* 측정값을 우선 사용
        # (보고서가 actual 값을 잘못 적었을 가능성을 검출).
        actual_measured = _count_lines(_resolve_repo_path(first_code))
        claims.append(
            _build_claim(round_id, first_code, reported, actual_measured,
                         [ln_idx], threshold,
                         actual_reported=_to_int(actual_m.group(1))
                         if actual_m else None)
        )

    return claims


def _build_claim(
    round_id: str,
    file_rel: str,
    reported: Optional[int],
    actual: Optional[int],
    source_lines: List[int],
    threshold: float,
    *,
    actual_reported: Optional[int] = None,
) -> LocClaim:
    """LocClaim 생성 + drift 계산.

    actual_reported 가 있으면 (보고서가 "실측 N" 이라고 적은 값) reported 와
    같은 비교 대상으로 함께 사용 가능하나, 본 메서드는 *실제 측정 actual* 만
    drift 계산에 사용한다 (보고서 본문 신뢰성 자체를 검증).
    """
    if reported is None or actual is None:
        return LocClaim(
            round=round_id, file=file_rel,
            reported=reported, actual=actual,
            drift=None, drift_pct=None,
            source_lines=source_lines, alert=False,
        )
    drift = actual - reported
    denom = max(reported, actual)
    pct = (drift / denom) if denom > 0 else 0.0
    alert = abs(pct) > threshold
    return LocClaim(
        round=round_id, file=file_rel,
        reported=reported, actual=actual,
        drift=drift, drift_pct=round(pct, 4),
        source_lines=source_lines, alert=alert,
    )


def validate(
    report_paths: List[Path],
    *,
    threshold: float = 0.20,
) -> Dict[str, Any]:
    """여러 보고서 일괄 검증 → 요약 dict.

    응답::

        {
          "generated_at_utc": "...",
          "threshold": 0.20,
          "reports": [
            {"round":"R20","path":"...","claims":[...]},
            ...
          ],
          "summary": {"total": N, "alerts": M, "files_missing": K}
        }
    """
    from datetime import datetime, timezone

    out_reports: List[Dict[str, Any]] = []
    total = 0
    alerts = 0
    files_missing = 0
    for rp in report_paths:
        claims = parse_report(rp, threshold=threshold)
        total += len(claims)
        alerts += sum(1 for c in claims if c.alert)
        files_missing += sum(1 for c in claims if c.actual is None)
        out_reports.append({
            "round": _round_from_filename(rp),
            "path": str(rp.relative_to(REPO_ROOT))
            if str(rp).startswith(str(REPO_ROOT)) else str(rp),
            "claims": [asdict(c) for c in claims],
        })
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "threshold": threshold,
        "reports": out_reports,
        "summary": {
            "total_claims": total,
            "alerts": alerts,
            "files_missing": files_missing,
        },
    }


def _select_reports(
    reports_dir: Path,
    rounds: Optional[List[str]] = None,
    all_reports: bool = False,
) -> List[Path]:
    """rounds=['R18','R19'] → 해당 라운드 보고서만. all=True 면 R*.md 전체.

    보고서 명은 ``R<N>_<TAG>_<DATE>.md`` 형태로 가정. 매칭은 prefix R<N>_.
    """
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
    """사람 가독 마크다운 표."""
    rows: List[str] = []
    rows.append("| round | file | reported | actual | drift | drift% | alert |")
    rows.append("|---|---|---:|---:|---:|---:|---|")
    for rep in result["reports"]:
        for c in rep["claims"]:
            pct_str = (
                f"{c['drift_pct']*100:+.1f}%"
                if c["drift_pct"] is not None else "—"
            )
            alert_str = "ALERT" if c["alert"] else ""
            rows.append(
                f"| {c['round']} | `{c['file']}` "
                f"| {c['reported'] if c['reported'] is not None else '—'} "
                f"| {c['actual'] if c['actual'] is not None else 'missing'} "
                f"| {c['drift'] if c['drift'] is not None else '—'} "
                f"| {pct_str} | {alert_str} |"
            )
    s = result["summary"]
    rows.append("")
    rows.append(
        f"총 claim {s['total_claims']}건, alert {s['alerts']}건, "
        f"파일 누락 {s['files_missing']}건. threshold={result['threshold']*100:.0f}%."
    )
    return "\n".join(rows)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--report", action="append", default=[],
                   help="단일 보고서 경로 (반복 가능).")
    p.add_argument("--rounds", default="",
                   help="콤마 구분 round 코드 (예: R18,R19,R20).")
    p.add_argument("--all", action="store_true",
                   help="docs/dashboard/R*.md 전체 검증.")
    p.add_argument("--reports-dir", default=str(REPORTS_DIR_DEFAULT),
                   help="기본: docs/dashboard")
    p.add_argument("--threshold", type=float, default=0.20,
                   help="|drift%%| 임계치 (기본 0.20 = 20%%).")
    p.add_argument("--json", action="store_true",
                   help="JSON 출력 (기본은 마크다운 표).")
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
    # 중복 제거 순서 보존.
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

    result = validate(unique, threshold=args.threshold)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(_fmt_table(result))
    # exit code: alert 있으면 1.
    return 1 if result["summary"]["alerts"] > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
