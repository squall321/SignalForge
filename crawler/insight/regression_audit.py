"""Harvest5 트랙 V4 — regression baseline 변경 감시 wrapper.

배경
====
``/api/v1/_internal/regression-baseline`` 는 11개 check + alembic 의 회귀
점검 지표를 반환한다.  지표 변경 (threshold 조정 / baseline 갱신 / 신규 check
추가) 은 운영 정책 결정 결과로 *반드시* audit 로그에 남아야 한다.  이전까지는
변경 사항이 git diff 에만 존재했으며 timeline 추적이 어려웠다.

본 모듈 동작
=============
1. ``/api/v1/_internal/regression-baseline`` GET.
2. 응답에서 *구조적 핵심* (각 check 의 name/threshold/baseline_* + alembic_min_head)
   만 추출하여 ``signature_hash`` 생성.  ``current`` / ``delta_*`` / ``generated_at``
   같은 매 호출 변동 값은 hash 대상에서 제외 (변경 감지의 거짓 양성 방지).
3. 이전 hash 를 ``reports/regression_baseline_last.json`` 에 비교.
4. **변경 시**: audit JSONL 1줄 append + 이전/현재 signature 의 항목별 diff
   기록 + ``regression_baseline_last.json`` 갱신.
5. **무변경 시**: audit entry 생성하지 않음 (운영 노이즈 최소화).

audit JSONL (``reports/backfill_audit.jsonl``) 라벨:
  - ``script``: ``"regression_audit"``
  - ``mode``:   ``"snapshot"``
  - ``env.round``: ``"harvest5"`` (override 가능 — ``REGRESSION_AUDIT_ROUND``)
  - ``env.track``: ``"V4"``

변경 항목은 ``audit.notes`` 에 사람이 읽을 수 있는 문장 + ``audit.counters``
에 ``checks_added`` / ``checks_removed`` / ``thresholds_changed`` /
``baselines_changed`` / ``alembic_min_changed`` 누적.

환경변수
========
SF_BACKEND_URL           기본 ``http://127.0.0.1:8000``
REGRESSION_AUDIT_STATE   상태 파일 경로 override (테스트용)
REGRESSION_AUDIT_ROUND   audit env.round 라벨 (기본 ``harvest5``)
REGRESSION_AUDIT_TIMEOUT 요청 timeout 초 (기본 20)

CLI
===
``python -m insight.regression_audit`` 1회 호출.  종료 코드:
  - 0 무변경
  - 0 변경 감지 + audit append 성공
  - 2 backend 미가동 / HTTP error
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

# audit 모듈 — crawler/insight/backfill_audit.py 의 record_run 재사용.
# 본 wrapper 가 crawler 트리 내부에 있으므로 상대 import 사용.
try:  # CLI 실행: ``python -m insight.regression_audit``
    from .backfill_audit import record_run, _audit_path  # type: ignore
except ImportError:  # 직접 실행 (PYTHONPATH=crawler)
    from insight.backfill_audit import record_run, _audit_path  # type: ignore


_DEFAULT_STATE_PATH = Path(__file__).resolve().parents[2] / "reports" / "regression_baseline_last.json"


# ── signature 추출 ─────────────────────────────────────────────────────────


def _check_signature(check: Dict[str, Any]) -> Dict[str, Any]:
    """단일 check 에서 *정의* 부분만 추출.

    ``current`` / ``delta_*`` / 측정값은 제외 — *정책* 변경만 hash 대상.
    """
    sig: Dict[str, Any] = {
        "name": check.get("name"),
        "label": check.get("label"),
        "threshold": check.get("threshold"),
    }
    for k, v in check.items():
        if k.startswith("baseline_"):
            sig[k] = v
    return sig


def extract_signature(payload: Dict[str, Any]) -> Dict[str, Any]:
    """endpoint 응답에서 hash 대상 구조만 추출 (정렬된 dict).

    포함:
      - checks: name 정렬, 각 check 의 (name, label, threshold, baseline_*)
      - alembic_min_head: 정책 minimum
    """
    checks_in = list(payload.get("checks") or [])
    checks_sig = sorted(
        (_check_signature(c) for c in checks_in),
        key=lambda d: str(d.get("name") or ""),
    )
    return {
        "checks": checks_sig,
        "alembic_min_head": payload.get("alembic_min_head"),
    }


def signature_hash(sig: Dict[str, Any]) -> str:
    """sha256 hex of canonical JSON (sort_keys, separators)."""
    blob = json.dumps(sig, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ── diff 계산 ──────────────────────────────────────────────────────────────


def _by_name(sig: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {str(c.get("name")): c for c in (sig.get("checks") or [])}


def compute_diff(prev_sig: Optional[Dict[str, Any]], curr_sig: Dict[str, Any]) -> Dict[str, Any]:
    """이전/현재 signature 항목 비교.

    Returns
    -------
    dict with keys::

        {
          "checks_added":      ["name1", ...],
          "checks_removed":    ["name2", ...],
          "thresholds_changed":[{"name":..,"from":..,"to":..}, ...],
          "baselines_changed": [{"name":..,"field":"baseline_r12","from":..,"to":..}, ...],
          "alembic_min_changed": {"from":"0014","to":"0015"} | None,
          "is_initial": bool,
        }
    """
    is_initial = prev_sig is None
    prev_by = _by_name(prev_sig or {"checks": []})
    curr_by = _by_name(curr_sig)

    added = sorted(set(curr_by) - set(prev_by))
    removed = sorted(set(prev_by) - set(curr_by))

    thresholds_changed: List[Dict[str, Any]] = []
    baselines_changed: List[Dict[str, Any]] = []
    for name in sorted(set(prev_by) & set(curr_by)):
        p = prev_by[name]
        c = curr_by[name]
        if p.get("threshold") != c.get("threshold"):
            thresholds_changed.append({
                "name": name,
                "from": p.get("threshold"),
                "to": c.get("threshold"),
            })
        baseline_keys = sorted({k for k in (set(p) | set(c)) if k.startswith("baseline_")})
        for bk in baseline_keys:
            if p.get(bk) != c.get(bk):
                baselines_changed.append({
                    "name": name,
                    "field": bk,
                    "from": p.get(bk),
                    "to": c.get(bk),
                })

    alembic_min_changed: Optional[Dict[str, Any]] = None
    prev_min = (prev_sig or {}).get("alembic_min_head")
    curr_min = curr_sig.get("alembic_min_head")
    if prev_min != curr_min:
        alembic_min_changed = {"from": prev_min, "to": curr_min}

    return {
        "checks_added": added,
        "checks_removed": removed,
        "thresholds_changed": thresholds_changed,
        "baselines_changed": baselines_changed,
        "alembic_min_changed": alembic_min_changed,
        "is_initial": is_initial,
    }


def diff_is_empty(diff: Dict[str, Any]) -> bool:
    """무변경 판정 (is_initial 은 제외 — 최초 1회는 *변경* 으로 취급)."""
    if diff.get("is_initial"):
        return False
    return (
        not diff.get("checks_added")
        and not diff.get("checks_removed")
        and not diff.get("thresholds_changed")
        and not diff.get("baselines_changed")
        and not diff.get("alembic_min_changed")
    )


# ── 상태 파일 IO ───────────────────────────────────────────────────────────


def _state_path() -> Path:
    env = os.getenv("REGRESSION_AUDIT_STATE", "").strip()
    if env:
        p = Path(env)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    _DEFAULT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    return _DEFAULT_STATE_PATH


def load_previous() -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """이전 (signature, hash) 반환.  파일 없거나 손상 시 (None, None)."""
    p = _state_path()
    if not p.exists():
        return None, None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None, None
    return obj.get("signature"), obj.get("hash")


def save_current(sig: Dict[str, Any], h: str) -> Path:
    p = _state_path()
    p.write_text(
        json.dumps({"signature": sig, "hash": h}, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return p


# ── notes 직렬화 ───────────────────────────────────────────────────────────


def diff_to_notes(diff: Dict[str, Any]) -> List[str]:
    """diff 를 audit.notes 용 짧은 문장 list 로."""
    out: List[str] = []
    if diff.get("is_initial"):
        out.append("initial snapshot (no previous state)")
    for name in diff.get("checks_added") or []:
        out.append(f"+check {name}")
    for name in diff.get("checks_removed") or []:
        out.append(f"-check {name}")
    for it in diff.get("thresholds_changed") or []:
        out.append(f"threshold {it['name']}: {it['from']} -> {it['to']}")
    for it in diff.get("baselines_changed") or []:
        out.append(f"baseline {it['name']}.{it['field']}: {it['from']} -> {it['to']}")
    if diff.get("alembic_min_changed"):
        ac = diff["alembic_min_changed"]
        out.append(f"alembic_min: {ac['from']} -> {ac['to']}")
    return out


# ── 실행 ───────────────────────────────────────────────────────────────────


def _fetch(base: str, timeout: float) -> Dict[str, Any]:
    url = base.rstrip("/") + "/api/v1/_internal/regression-baseline"
    r = httpx.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()


def run_once(
    *,
    base_url: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    round_label: Optional[str] = None,
    timeout: float = 20.0,
) -> Dict[str, Any]:
    """1회 실행: fetch → diff → audit append (변경 시).

    Parameters
    ----------
    base_url
        backend base URL.  ``None`` 이면 ``SF_BACKEND_URL`` 환경변수.
    payload
        테스트용 — 응답을 직접 주입.  주어지면 HTTP fetch 생략.
    round_label
        ``env.round`` 라벨.  ``None`` 이면 ``REGRESSION_AUDIT_ROUND``
        환경변수, 그도 없으면 ``"harvest5"``.

    Returns
    -------
    dict::

        {
          "changed": bool,
          "is_initial": bool,
          "hash": "<sha256>",
          "previous_hash": "<sha256>" | None,
          "diff": {...},
          "audit_path": "/.../reports/backfill_audit.jsonl",
          "state_path": "/.../reports/regression_baseline_last.json",
        }
    """
    if payload is None:
        base = (base_url or os.getenv("SF_BACKEND_URL", "http://127.0.0.1:8000")).strip()
        payload = _fetch(base, timeout)

    curr_sig = extract_signature(payload)
    curr_hash = signature_hash(curr_sig)
    prev_sig, prev_hash = load_previous()

    diff = compute_diff(prev_sig, curr_sig)
    changed = not diff_is_empty(diff)

    audit_path = _audit_path()
    state_path = _state_path()

    result: Dict[str, Any] = {
        "changed": changed,
        "is_initial": bool(diff.get("is_initial")),
        "hash": curr_hash,
        "previous_hash": prev_hash,
        "diff": diff,
        "audit_path": str(audit_path),
        "state_path": str(state_path),
    }

    if not changed:
        return result

    round_lbl = (
        round_label
        or os.getenv("REGRESSION_AUDIT_ROUND", "").strip()
        or "harvest5"
    )

    env_block = {
        "round": round_lbl,
        "track": "V4",
        "previous_hash": prev_hash,
        "current_hash": curr_hash,
    }
    with record_run(script="regression_audit", mode="snapshot", env=env_block) as audit:
        for ln in diff_to_notes(diff):
            audit.note(ln)
        audit.bump("checks_added", len(diff.get("checks_added") or []))
        audit.bump("checks_removed", len(diff.get("checks_removed") or []))
        audit.bump("thresholds_changed", len(diff.get("thresholds_changed") or []))
        audit.bump("baselines_changed", len(diff.get("baselines_changed") or []))
        if diff.get("alembic_min_changed"):
            audit.bump("alembic_min_changed", 1)
        audit.note(f"checks_total={len(curr_sig.get('checks') or [])}")

    save_current(curr_sig, curr_hash)
    return result


def main(argv: Optional[List[str]] = None) -> int:
    timeout = float(os.getenv("REGRESSION_AUDIT_TIMEOUT", "20"))
    try:
        out = run_once(timeout=timeout)
    except httpx.HTTPError as e:
        print(f"[regression_audit] HTTP error: {e}", file=sys.stderr)
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
