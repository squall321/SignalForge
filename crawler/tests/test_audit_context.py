"""Harvest 3+ 트랙 P2 — `audit_round` 컨텍스트 매니저 단위 테스트.

DB / 네트워크 의존성 0. tmp_path 에 audit JSONL 을 떨어뜨려 검증한다.

3 시나리오
~~~~~~~~~~
1. **정상 종료**: start + end (status=ok) + 중간 event/counter 가 정확히 기록.
2. **예외 발생**: end (status=fail) 이 try/finally 로 보장 + exc_message 기록.
3. **중첩 round**: 서로 다른 라운드 라벨로 start/end 쌍 2 개가 각각 닫힌다.

실행::

    cd crawler && /home/koopark/claude/SignalForge/.venv/bin/python \\
        -m pytest tests/test_audit_context.py -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest


HERE = os.path.dirname(os.path.abspath(__file__))
CRAWLER_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if CRAWLER_ROOT not in sys.path:
    sys.path.insert(0, CRAWLER_ROOT)


from base.audit import audit_round  # noqa: E402


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_audit_round_normal_writes_start_end_and_event(tmp_path: Path, monkeypatch):
    """정상 종료 시 start + 중간 event + end (status=ok) 가 모두 기록되고
    카운터가 end row 의 counters 에 flush 되는지."""
    audit = tmp_path / "audit.jsonl"
    monkeypatch.delenv("ROUND", raising=False)
    monkeypatch.delenv("AUDIT_PATH", raising=False)

    with audit_round(
        "harvest3p",
        track="P1",
        script="korean_pagination_deep",
        path=audit,
        extra={"pages": 50, "sites": ["clien", "fmkorea"], "dry_run": False},
    ) as a:
        a.event("new_collector", track="C", platforms=["resetera"])
        a.update(saved=120, fetched=500)
        a.bump("saved", 5)  # 120 + 5 = 125
        a.bump("fetched", 0)  # no-op

    rows = _read_jsonl(audit)
    assert len(rows) == 3, f"expected 3 rows (start/event/end), got {len(rows)}: {rows}"

    start, mid, end = rows
    # 1) start
    assert start["event"] == "start"
    assert start["round"] == "harvest3p"
    assert start["track"] == "P1"
    assert start["script"] == "korean_pagination_deep"
    assert start["pages"] == 50
    assert start["sites"] == ["clien", "fmkorea"]
    assert start["dry_run"] is False
    # run_id 동일성 — start/end 페어링용
    assert start["run_id"] and len(start["run_id"]) == 12

    # 2) 중간 이벤트
    assert mid["event"] == "new_collector"
    assert mid["run_id"] == start["run_id"]
    assert mid["round"] == "harvest3p"
    # track 키는 호출자가 명시 override 가능 — 이 케이스에선 'C' 로 덮어씀
    assert mid["track"] == "C"
    assert mid["platforms"] == ["resetera"]

    # 3) end
    assert end["event"] == "end"
    assert end["status"] == "ok"
    assert end["run_id"] == start["run_id"]
    assert end["track"] == "P1"
    assert end["counters"]["saved"] == 125  # update(120) + bump(5)
    assert end["counters"]["fetched"] == 500
    assert "exc_message" not in end
    # elapsed_s 는 float 또는 None
    assert end["elapsed_s"] is None or isinstance(end["elapsed_s"], (int, float))


def test_audit_round_exception_still_writes_end_with_fail(tmp_path: Path, monkeypatch):
    """예외 발생 시에도 end (status=fail) + exc_message 가 기록되고
    예외는 그대로 re-raise 되는지."""
    audit = tmp_path / "audit.jsonl"
    monkeypatch.delenv("ROUND", raising=False)

    with pytest.raises(RuntimeError, match="boom"):
        with audit_round(
            "harvest3p",
            track="P1",
            script="korean_pagination_deep",
            path=audit,
        ) as a:
            a.update(saved=42)
            raise RuntimeError("boom")

    rows = _read_jsonl(audit)
    assert len(rows) == 2, f"expected start + end even on exception, got {len(rows)}"

    start, end = rows
    assert start["event"] == "start"
    assert start["track"] == "P1"
    assert end["event"] == "end"
    assert end["status"] == "fail"
    assert end["exc_message"] == "RuntimeError: boom"
    # 예외 발생 직전 update 된 카운터도 end 에 보존돼야 한다.
    assert end["counters"]["saved"] == 42
    # 동일 run_id
    assert start["run_id"] == end["run_id"]


def test_audit_round_nested_two_rounds_each_closed(tmp_path: Path, monkeypatch):
    """중첩(nested) round — 부모/자식 각각의 start/end 페어가 독립적으로 닫힌다.

    실전 케이스: 한 프로세스에서 harvest3p 라운드 안에 보조 정리 라운드
    (예: dedup) 를 잠깐 돌리는 시나리오.  자식 start/end 가 부모 end 보다
    먼저 닫혀야 정상.
    """
    audit = tmp_path / "audit.jsonl"
    monkeypatch.delenv("ROUND", raising=False)

    with audit_round("harvest3p", track="P1", script="parent",
                     path=audit) as parent:
        parent.update(parent_step=1)
        with audit_round("dedup-aux", track="P1.aux", script="child",
                         path=audit) as child:
            child.update(child_step=1)
        parent.update(parent_step=2)

    rows = _read_jsonl(audit)
    # 순서: parent.start → child.start → child.end → parent.end
    assert [r["event"] for r in rows] == ["start", "start", "end", "end"]

    p_start, c_start, c_end, p_end = rows
    assert p_start["round"] == "harvest3p" and p_start["script"] == "parent"
    assert c_start["round"] == "dedup-aux" and c_start["script"] == "child"
    assert c_end["round"] == "dedup-aux" and c_end["status"] == "ok"
    assert p_end["round"] == "harvest3p" and p_end["status"] == "ok"

    # run_id 페어링 정확성
    assert p_start["run_id"] == p_end["run_id"]
    assert c_start["run_id"] == c_end["run_id"]
    assert p_start["run_id"] != c_start["run_id"]

    # 부모 end 의 counters 는 두 번의 update 가 모두 보존돼야 한다.
    assert p_end["counters"]["parent_step"] == 2

    # ★ verify D 가 잡던 결함 (start 수 != end 수) 영구 해결 확인
    starts = [r for r in rows if r["event"] == "start"]
    ends = [r for r in rows if r["event"] == "end"]
    assert len(starts) == len(ends), \
        f"start ({len(starts)}) != end ({len(ends)}) — harvest 결함 재발"
