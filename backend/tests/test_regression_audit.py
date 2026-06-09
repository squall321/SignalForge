"""Harvest5 트랙 V4 — regression baseline 변경 감시 wrapper 단위 테스트.

검증 대상: ``crawler/insight/regression_audit.py``

테스트 케이스 (외부 의존 0 — payload 주입 + 임시 상태 파일):
  1. 최초 호출 — is_initial=True, audit JSONL 1줄 생성, state 파일 저장
  2. 2회 호출 (변경 없음) — changed=False, audit JSONL 추가 entry 없음
  3. threshold 변경 — changed=True, diff.thresholds_changed 기록
  4. 신규 check 추가 — diff.checks_added 기록
  5. baseline_* 변경 — diff.baselines_changed 기록
  6. ``current`` 변화는 hash 영향 없음 (정책 변경만 트리거)
  7. alembic_min_head 변경 — diff.alembic_min_changed 기록

실행::

    cd backend && PYTHONPATH=. .venv/bin/pytest tests/test_regression_audit.py -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest


# crawler/ 트리를 sys.path 에 추가 (backend tests 에서 crawler 모듈 사용).
_CRAWLER_ROOT = str(Path(__file__).resolve().parents[2] / "crawler")
if _CRAWLER_ROOT not in sys.path:
    sys.path.insert(0, _CRAWLER_ROOT)

# audit JSONL 도 임시 디렉토리로 격리.
@pytest.fixture(autouse=True)
def _isolated_audit(monkeypatch, tmp_path):
    audit_dir = tmp_path / "reports"
    audit_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("BACKFILL_AUDIT_DIR", str(audit_dir))
    state_path = audit_dir / "regression_baseline_last.json"
    monkeypatch.setenv("REGRESSION_AUDIT_STATE", str(state_path))
    yield {
        "audit_jsonl": audit_dir / "backfill_audit.jsonl",
        "state_path": state_path,
    }


def _sample_payload(threshold=300, baseline_r8=352, extra_check=False,
                    alembic_min="0014", current=529):
    """샘플 endpoint 응답 — 핵심 필드만 (구조 보존)."""
    checks = [
        {
            "name": "note7_voc",
            "label": "Galaxy Note 7",
            "product_code": "GN7",
            "current": current,
            "baseline_r8": baseline_r8,
            "baseline_r12": 366,
            "baseline_r20": 387,
            "threshold": threshold,
            "delta_vs_baseline": current - baseline_r8,
            "ok": current >= threshold,
        },
        {
            "name": "hardware_fr_voc",
            "label": "Hardware.fr 전체 voc",
            "current": 375,
            "baseline_harvest3p": 206,
            "threshold": 150,
            "delta_vs_baseline_harvest3p": 169,
            "ok": True,
        },
    ]
    if extra_check:
        checks.append({
            "name": "xda_voc",
            "label": "XDA tag 전체 voc",
            "current": 88,
            "baseline_harvest5": 50,
            "threshold": 40,
            "ok": True,
        })
    return {
        "generated_at": "2026-06-07T00:00:00+00:00",
        "checks": checks,
        "alembic_head": "0017",
        "alembic_min_head": alembic_min,
        "alembic_ok": True,
        "summary": {"total": len(checks) + 1, "ok": len(checks) + 1, "failed": 0},
    }


def _load_jsonl(path: Path):
    if not path.exists():
        return []
    out = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln:
            out.append(json.loads(ln))
    return out


# ── 1. 최초 호출 ───────────────────────────────────────────────────────────


def test_initial_call_writes_audit_and_state(_isolated_audit):
    from insight.regression_audit import run_once  # type: ignore

    out = run_once(payload=_sample_payload())
    assert out["changed"] is True
    assert out["is_initial"] is True
    assert out["previous_hash"] is None
    assert len(out["hash"]) == 64

    # state file 생성
    assert _isolated_audit["state_path"].exists()
    state = json.loads(_isolated_audit["state_path"].read_text())
    assert state["hash"] == out["hash"]
    assert "checks" in state["signature"]

    # audit JSONL 1줄
    rows = _load_jsonl(_isolated_audit["audit_jsonl"])
    assert len(rows) == 1
    e = rows[0]
    assert e["script"] == "regression_audit"
    assert e["mode"] == "snapshot"
    assert e["env"]["round"] == "harvest5"
    assert e["env"]["track"] == "V4"
    assert e["env"]["previous_hash"] is None
    assert e["env"]["current_hash"] == out["hash"]
    assert e["status"] == "ok"
    # notes 에 initial 표시
    assert any("initial" in n for n in e.get("notes") or [])


# ── 2. 변경 없음 ───────────────────────────────────────────────────────────


def test_no_change_skips_audit(_isolated_audit):
    from insight.regression_audit import run_once  # type: ignore

    p = _sample_payload()
    run_once(payload=p)
    # current 만 변화 (정책 무변) — entry 없어야 함
    p2 = _sample_payload(current=540)
    out = run_once(payload=p2)
    assert out["changed"] is False
    assert out["is_initial"] is False
    rows = _load_jsonl(_isolated_audit["audit_jsonl"])
    assert len(rows) == 1, f"expected 1 (initial only), got {len(rows)}"


# ── 3. threshold 변경 ──────────────────────────────────────────────────────


def test_threshold_change_logged(_isolated_audit):
    from insight.regression_audit import run_once  # type: ignore

    run_once(payload=_sample_payload(threshold=300))
    out = run_once(payload=_sample_payload(threshold=350))

    assert out["changed"] is True
    assert out["is_initial"] is False
    tc = out["diff"]["thresholds_changed"]
    assert len(tc) == 1
    assert tc[0] == {"name": "note7_voc", "from": 300, "to": 350}

    rows = _load_jsonl(_isolated_audit["audit_jsonl"])
    assert len(rows) == 2
    last = rows[-1]
    assert last["counters"].get("thresholds_changed") == 1
    assert any("threshold note7_voc" in n for n in last["notes"])


# ── 4. 신규 check 추가 ─────────────────────────────────────────────────────


def test_check_added_logged(_isolated_audit):
    from insight.regression_audit import run_once  # type: ignore

    run_once(payload=_sample_payload(extra_check=False))
    out = run_once(payload=_sample_payload(extra_check=True))

    assert out["changed"] is True
    assert "xda_voc" in out["diff"]["checks_added"]
    rows = _load_jsonl(_isolated_audit["audit_jsonl"])
    assert rows[-1]["counters"].get("checks_added") == 1
    assert any("+check xda_voc" in n for n in rows[-1]["notes"])


# ── 5. baseline 변경 ───────────────────────────────────────────────────────


def test_baseline_change_logged(_isolated_audit):
    from insight.regression_audit import run_once  # type: ignore

    run_once(payload=_sample_payload(baseline_r8=352))
    out = run_once(payload=_sample_payload(baseline_r8=400))

    assert out["changed"] is True
    bc = out["diff"]["baselines_changed"]
    assert any(it["name"] == "note7_voc" and it["field"] == "baseline_r8"
               and it["from"] == 352 and it["to"] == 400 for it in bc)
    rows = _load_jsonl(_isolated_audit["audit_jsonl"])
    assert rows[-1]["counters"].get("baselines_changed", 0) >= 1


# ── 6. alembic_min 변경 ────────────────────────────────────────────────────


def test_alembic_min_change_logged(_isolated_audit):
    from insight.regression_audit import run_once  # type: ignore

    run_once(payload=_sample_payload(alembic_min="0014"))
    out = run_once(payload=_sample_payload(alembic_min="0018"))

    assert out["changed"] is True
    assert out["diff"]["alembic_min_changed"] == {"from": "0014", "to": "0018"}
    rows = _load_jsonl(_isolated_audit["audit_jsonl"])
    assert rows[-1]["counters"].get("alembic_min_changed") == 1


# ── 7. signature hash 는 current/delta 변화에 영향 없음 ─────────────────────


def test_signature_ignores_current(_isolated_audit):
    from insight.regression_audit import extract_signature, signature_hash  # type: ignore

    a = signature_hash(extract_signature(_sample_payload(current=100)))
    b = signature_hash(extract_signature(_sample_payload(current=999)))
    assert a == b, "current 값 변화는 signature 에 영향 없어야 함"

    # threshold 변화는 영향 있음
    c = signature_hash(extract_signature(_sample_payload(threshold=999)))
    assert a != c


# ── 8. round override ──────────────────────────────────────────────────────


def test_round_override(_isolated_audit, monkeypatch):
    from insight.regression_audit import run_once  # type: ignore

    monkeypatch.setenv("REGRESSION_AUDIT_ROUND", "harvest6")
    run_once(payload=_sample_payload())
    rows = _load_jsonl(_isolated_audit["audit_jsonl"])
    assert rows[-1]["env"]["round"] == "harvest6"
    assert rows[-1]["env"]["track"] == "V4"
