"""Workflow validator hook — 보고서 생성 직후 자동 cross-check (R26 트랙).

목적
====
R25 권고 5: 워크플로우 보고서가 작성되는 즉시 ``workflow_validator`` 를 자동
실행하여 보고된 수치 vs 실측 drift 를 자동 캡처한다. archive 부재 같은
self-report drift 사고 (R25 회고) 를 *작성 시점에* 잡는 것이 목표.

배경 (R25 회고)
-------------
R25 D 트랙은 보고서에 "/reports/archive/R25/*.jsonl 영구 기록" 을 주장했으나
실측은 archive 디렉토리 자체가 부재했다. workflow_validator 가 *수동* 으로만
실행되는 운영 (CLI/HTTP) 에서는 보고서 작성 직후 drift 가 즉시 검증되지
않아 후속 라운드까지 drift 가 누적될 수 있다.

설계
====
1. **폴링 기반** (watchdog 미설치 환경 호환).  ``reports/`` + ``docs/dashboard/``
   디렉토리의 ``R*.md`` 파일을 *mtime* 기준으로 스캔. 마지막 스캔 시점 이후
   변경된 파일만 validator 에 회부.
2. **상태 파일** ``reports/validator_hook_state.json`` — 마지막 스캔 UTC,
   최근 5회 검증 결과 (round / drift_pct / alerts / archive_present_ok 등)
   영구화. 후크 가동 상태 + 히스토리 추적 가능.
3. **Celery beat 매 5 분** — ``tasks.run_validator_hook`` 가 호출하는 단일
   진입점 ``run()``.  watchdog 없이도 안정적으로 동작.
4. **drift > ±10%** 캡처: validator 의 alert 결과를 그대로 상태 파일에 기록.
   별도 alert_events INSERT 까지는 *이번 라운드에선* 하지 않음 (R26 다음
   라운드에서 alert_rules 결선). 본 라운드는 *후크 자체 가동* 보장이 목표.
5. **archive cross-check** — R25 회고 직접 대응. 보고서가 ``archive/R*/``
   경로를 *주장* 하면 디렉토리 존재 여부를 확인하여 결과에 ``archive_drift``
   필드로 기록.  validator 본체에는 없는 *디렉토리 실재성* 검증.

상태 파일 schema
================
.. code-block:: json

   {
     "hook_active": true,
     "last_scan_utc": "2026-06-05T22:13:00+00:00",
     "scan_count": 42,
     "history": [
       {
         "round": "R25",
         "report_path": "docs/dashboard/R25_DEEPEN_2026-06-05.md",
         "scanned_at_utc": "2026-06-05T22:13:00+00:00",
         "report_mtime_utc": "2026-06-05T22:00:00+00:00",
         "claims_total": 23, "alerts": 2,
         "mean_abs_drift_pct": 12.5, "max_abs_drift_pct": 28.4,
         "archive_claims": ["reports/archive/R25/"],
         "archive_drift": ["reports/archive/R25/ (missing)"]
       },
       ...
     ]
   }

``history`` 는 가장 최근 5건만 유지 (FIFO).

CLI
===
.. code-block:: bash

   # 한 번만 스캔 (Celery beat 와 동일 동작)
   python -m insight.workflow_validator_hook

   # 후크 상태만 출력 (검증 미수행)
   python -m insight.workflow_validator_hook --status
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# crawler/ sys.path 보장.
_THIS = Path(__file__).resolve()
_CRAWLER_DIR = _THIS.parent.parent
if str(_CRAWLER_DIR) not in sys.path:
    sys.path.insert(0, str(_CRAWLER_DIR))

from insight.workflow_validator import (  # noqa: E402
    DEFAULT_BACKEND,
    DEFAULT_THRESHOLD,
    REPO_ROOT,
    measure_live,
    parse_report,
    _round_from_filename,
)

# ── 상수 ─────────────────────────────────────────────────────────────────
DEFAULT_STATE_PATH = REPO_ROOT / "reports" / "validator_hook_state.json"
DEFAULT_DASHBOARD_DIR = REPO_ROOT / "docs" / "dashboard"
DEFAULT_REPORTS_DIR = REPO_ROOT / "reports"
HISTORY_MAX = 5

# archive 경로 주장 추출 — 보고서 본문이 "reports/archive/R25/" 같은 경로를
# 언급하면 실재성 확인 대상. 다양한 표기 흡수.
_ARCHIVE_PATH_RE = re.compile(
    r"((?:reports?/)?archive/R\d+(?:/[\w\-./]*)?)",
    re.IGNORECASE,
)


# ── 상태 파일 IO ─────────────────────────────────────────────────────────
def _load_state(state_path: Path) -> Dict[str, Any]:
    """상태 파일 로드. 없거나 손상이면 빈 dict 반환 (graceful)."""
    if not state_path.is_file():
        return {"hook_active": True, "scan_count": 0, "history": []}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"hook_active": True, "scan_count": 0, "history": []}
        data.setdefault("hook_active", True)
        data.setdefault("scan_count", 0)
        if not isinstance(data.get("history"), list):
            data["history"] = []
        return data
    except (OSError, ValueError):
        return {"hook_active": True, "scan_count": 0, "history": []}


def _save_state(state_path: Path, state: Dict[str, Any]) -> None:
    """상태 파일 쓰기. 실패는 graceful (운영 차단 금지)."""
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


# ── 보고서 스캔 ──────────────────────────────────────────────────────────
def _list_reports(scan_dirs: List[Path]) -> List[Path]:
    """``R*.md`` 보고서 후보 전수.  중복 제거."""
    out: List[Path] = []
    seen: set = set()
    for d in scan_dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.glob("R*.md")):
            key = str(p.resolve())
            if key in seen:
                continue
            seen.add(key)
            out.append(p)
    return out


def _select_new(
    reports: List[Path],
    last_scan_iso: Optional[str],
) -> List[Path]:
    """``last_scan_iso`` 이후 변경된 보고서만 선택. 첫 스캔이면 전부."""
    if not last_scan_iso:
        return list(reports)
    try:
        ref = datetime.fromisoformat(last_scan_iso.replace("Z", "+00:00"))
    except ValueError:
        return list(reports)
    out: List[Path] = []
    for p in reports:
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mtime >= ref:
            out.append(p)
    return out


# ── archive cross-check ────────────────────────────────────────────────
def _extract_archive_claims(report_path: Path) -> List[str]:
    """보고서 본문에서 ``archive/R*/`` 경로 주장 추출."""
    try:
        text = report_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    paths: List[str] = []
    seen: set = set()
    for m in _ARCHIVE_PATH_RE.finditer(text):
        raw = m.group(1).strip().rstrip(".,)")
        # 정규화: 선행 "reports/" 없으면 추가 (REPO_ROOT 기준).
        norm = raw if raw.startswith("reports/") else f"reports/{raw.lstrip('/')}"
        if norm in seen:
            continue
        seen.add(norm)
        paths.append(norm)
    return paths


def _check_archive_drift(claims: List[str]) -> List[str]:
    """주장 경로 중 *실제로 존재하지 않는* 항목 목록 반환.

    경로는 디렉토리 (마지막이 / 또는 R\\d+) 또는 파일 양쪽 가능. 어느 쪽이든
    파일시스템에 없으면 drift 로 기록.
    """
    drift: List[str] = []
    for raw in claims:
        # REPO_ROOT 기준 절대 경로.
        p = (REPO_ROOT / raw).resolve()
        # 디렉토리 형식 + 파일 형식 둘 다 부재 시 drift.
        if not p.exists():
            drift.append(f"{raw} (missing)")
    return drift


# ── 메인 진입점 ─────────────────────────────────────────────────────────
def run(
    *,
    backend: str = DEFAULT_BACKEND,
    threshold: float = DEFAULT_THRESHOLD,
    state_path: Optional[Path] = None,
    scan_dirs: Optional[List[Path]] = None,
    force_all: bool = False,
    live_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """후크 1 회 스캔.

    파라미터
    --------
    backend
        validator measure_live 가 사용할 backend URL.
    threshold
        drift alert 임계 (기본 0.10 = 10%).
    state_path
        상태 JSON 경로 (기본 ``reports/validator_hook_state.json``).
    scan_dirs
        스캔할 디렉토리 list (기본 ``[docs/dashboard, reports]``).
    force_all
        ``True`` 면 mtime 무관 *모든* 보고서를 재검증 (테스트/디버깅).
    live_override
        ``measure_live`` 호출 대신 사용할 fake live dict (테스트).

    반환::

        {
          "hook_active": true,
          "scanned_at_utc": "...",
          "scanned_count": 3,
          "alerts_total": 1,
          "archive_drift_total": 1,
          "results": [ {round, alerts, archive_drift, ...}, ... ],
          "state_path": "..."
        }
    """
    state_path = state_path or DEFAULT_STATE_PATH
    scan_dirs = scan_dirs or [DEFAULT_DASHBOARD_DIR, DEFAULT_REPORTS_DIR]

    state = _load_state(state_path)
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # 실측 1 회 (모든 보고서 공유).
    live = live_override if live_override is not None else measure_live(backend=backend)

    # 후보 선택.
    all_reports = _list_reports(scan_dirs)
    last_scan = None if force_all else state.get("last_scan_utc")
    targets = _select_new(all_reports, last_scan)

    results: List[Dict[str, Any]] = []
    alerts_total = 0
    archive_drift_total = 0

    for rp in targets:
        # 보고서 외 파일 (예: workflow_validate_R*_meta.md) 은 스킵 — 본 후크는
        # *작성된* R 보고서 (R{N}_xxx_YYYY-MM-DD.md) 만 대상.
        if "workflow_validate" in rp.name or "_meta" in rp.name:
            continue
        try:
            claims = parse_report(rp, live, threshold=threshold)
        except Exception as e:  # noqa: BLE001 — 한 보고서 실패가 후크 전체 멈추지 않게.
            results.append({
                "round": _round_from_filename(rp),
                "report_path": _rel(rp),
                "error": str(e),
            })
            continue
        rep_alerts = sum(1 for c in claims if c.alert)
        drifts = [abs(c.drift_pct) for c in claims if c.drift_pct is not None]
        mean_abs = (sum(drifts) / len(drifts) * 100.0) if drifts else 0.0
        max_abs = (max(drifts) * 100.0) if drifts else 0.0

        archive_claims = _extract_archive_claims(rp)
        archive_drift = _check_archive_drift(archive_claims)

        alerts_total += rep_alerts
        archive_drift_total += len(archive_drift)

        try:
            mtime = datetime.fromtimestamp(rp.stat().st_mtime, tz=timezone.utc)
            mtime_iso = mtime.isoformat(timespec="seconds")
        except OSError:
            mtime_iso = None

        results.append({
            "round": _round_from_filename(rp),
            "report_path": _rel(rp),
            "scanned_at_utc": now_iso,
            "report_mtime_utc": mtime_iso,
            "claims_total": len(claims),
            "alerts": rep_alerts,
            "mean_abs_drift_pct": round(mean_abs, 2),
            "max_abs_drift_pct": round(max_abs, 2),
            "archive_claims": archive_claims,
            "archive_drift": archive_drift,
        })

    # 상태 갱신 — history FIFO 5건.
    history = list(state.get("history") or [])
    history.extend(results)
    # 최신 5건만 유지 (뒤에서 자름).
    history = history[-HISTORY_MAX:]
    state["hook_active"] = True
    state["last_scan_utc"] = now_iso
    state["scan_count"] = int(state.get("scan_count") or 0) + 1
    state["history"] = history
    state["last_alerts_total"] = alerts_total
    state["last_archive_drift_total"] = archive_drift_total
    _save_state(state_path, state)

    return {
        "hook_active": True,
        "scanned_at_utc": now_iso,
        "scanned_count": len(results),
        "alerts_total": alerts_total,
        "archive_drift_total": archive_drift_total,
        "results": results,
        "state_path": str(state_path),
    }


def status(state_path: Optional[Path] = None) -> Dict[str, Any]:
    """현 후크 가동 상태 + 최근 5회 검증 결과 (검증 미수행)."""
    state_path = state_path or DEFAULT_STATE_PATH
    state = _load_state(state_path)
    return {
        "hook_active": bool(state.get("hook_active", True)),
        "last_scan_utc": state.get("last_scan_utc"),
        "scan_count": int(state.get("scan_count") or 0),
        "last_alerts_total": int(state.get("last_alerts_total") or 0),
        "last_archive_drift_total": int(state.get("last_archive_drift_total") or 0),
        "history": list(state.get("history") or [])[-HISTORY_MAX:],
        "state_path": str(state_path),
    }


def _rel(p: Path) -> str:
    """REPO_ROOT 상대경로 (가능 시) 또는 절대."""
    try:
        return str(p.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(p)


# ── CLI ──────────────────────────────────────────────────────────────────
def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--backend", default=DEFAULT_BACKEND)
    p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    p.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    p.add_argument("--force-all", action="store_true",
                   help="mtime 무관 모든 보고서 재검증 (디버깅).")
    p.add_argument("--status", action="store_true",
                   help="현 상태만 출력 (스캔 미수행).")
    p.add_argument("--json", action="store_true",
                   help="결과를 JSON 으로 출력 (기본 사람가독 요약).")
    args = p.parse_args(argv)

    if args.status:
        out = status(state_path=Path(args.state_path))
    else:
        out = run(
            backend=args.backend,
            threshold=args.threshold,
            state_path=Path(args.state_path),
            force_all=args.force_all,
        )

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        if args.status:
            print(f"hook_active={out['hook_active']}  "
                  f"last_scan={out['last_scan_utc']}  "
                  f"scan_count={out['scan_count']}  "
                  f"last_alerts={out['last_alerts_total']}  "
                  f"last_archive_drift={out['last_archive_drift_total']}")
            for h in out["history"]:
                print(f"  - {h.get('round')} {h.get('report_path')} "
                      f"alerts={h.get('alerts')} "
                      f"archive_drift={len(h.get('archive_drift') or [])}")
        else:
            print(f"scanned={out['scanned_count']}  "
                  f"alerts_total={out['alerts_total']}  "
                  f"archive_drift_total={out['archive_drift_total']}")
            for r in out["results"]:
                print(f"  - {r.get('round')} {r.get('report_path')} "
                      f"alerts={r.get('alerts')} "
                      f"archive_drift={len(r.get('archive_drift') or [])}")
    return 1 if (not args.status and out.get("alerts_total", 0) > 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
