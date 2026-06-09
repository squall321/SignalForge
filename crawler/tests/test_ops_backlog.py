"""insight.ops_backlog_processor 단위 테스트 (R21 트랙 C).

검증 포인트
----------
1. scan_window  — ops_status JSON 을 ``days`` 일 윈도우로 모은다 (있는 일자만).
2. split_by_severity — violations 를 critical/warning/info 로 분리.
3. process_backlog (dry_run, fake conn)
   * 헤더만 (violations_count > 0, violations=[]) 파일은 alert_events 에서 재구성.
   * critical 은 dedupe 키 매칭 시 skip, 미매칭 시 (dry-run 에서는) would-insert 카운트.
   * warning 은 일별 누적 요약에 기록.
   * info 는 카운트만.
4. audit JSONL 1 line append.
5. warning summary JSON merge (기존 day 보존).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from insight.ops_backlog_processor import (  # noqa: E402
    AUDIT_FILE,
    WARNING_SUMMARY_FILE,
    _dedupe_key,
    append_audit_line,
    append_warning_summary,
    insert_critical_violations,
    process_backlog,
    scan_window,
    split_by_severity,
)


# ── 픽스처 헬퍼 ─────────────────────────────────────────────────────────
def _write_ops_status(
    report_dir: Path,
    target: date,
    *,
    status: str,
    violations_count: int,
    violations: List[Dict[str, Any]],
    extra: Optional[Dict[str, Any]] = None,
) -> Path:
    payload: Dict[str, Any] = {
        "captured_at": f"{target.isoformat()}T00:30:00+00:00",
        "target_date": target.isoformat(),
        "status": status,
        "voc_last": 1000,
        "voc_prev": 1100,
        "sentiment_null_rate": 0.0,
        "topic_rate": 0.9,
        "grounding_last": None,
        "regression_ok_ratio": None,
        "regression_failed": None,
        "violations_count": violations_count,
        "violations": violations,
    }
    if extra:
        payload.update(extra)
    path = report_dir / f"ops_status_{target.isoformat()}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _crit(metric: str = "regression_ok_ratio", value: float = 0.5) -> Dict[str, Any]:
    return {
        "metric": metric,
        "severity": "critical",
        "value": value,
        "threshold": 1.0,
        "reason": f"{metric}={value} < 1.0",
    }


def _warn(metric: str = "voc_daily_drop_pct", value: float = 87.0) -> Dict[str, Any]:
    return {
        "metric": metric,
        "severity": "warning",
        "value": value,
        "threshold": 50.0,
        "reason": f"{metric}={value}",
    }


def _info(metric: str = "minor_blip") -> Dict[str, Any]:
    return {
        "metric": metric,
        "severity": "info",
        "value": 1.0,
        "threshold": 0.0,
        "reason": metric,
    }


# ── scan_window ─────────────────────────────────────────────────────────
def test_scan_window_returns_existing_files_oldest_first(tmp_path: Path):
    """존재하는 일자만 모아 오래된 순으로 정렬해 반환."""
    d0 = date(2026, 6, 1)
    d2 = date(2026, 6, 3)
    d4 = date(2026, 6, 5)
    _write_ops_status(tmp_path, d0, status="ok",
                      violations_count=0, violations=[])
    _write_ops_status(tmp_path, d2, status="warning",
                      violations_count=1, violations=[_warn()])
    _write_ops_status(tmp_path, d4, status="critical",
                      violations_count=2, violations=[_crit(), _warn()])

    out = scan_window(days=7, end_date=d4, report_dir=tmp_path)
    assert [d for d, _ in out] == [d0, d2, d4]
    assert out[0][1]["status"] == "ok"
    assert out[-1][1]["violations_count"] == 2


def test_scan_window_handles_missing_files(tmp_path: Path):
    """파일 없으면 graceful — 빈 리스트."""
    out = scan_window(days=3, end_date=date(2026, 6, 5), report_dir=tmp_path)
    assert out == []


# ── split_by_severity ───────────────────────────────────────────────────
def test_split_by_severity_buckets_correctly():
    """violations 를 critical/warning/info 로 분리."""
    vs = [_crit(), _crit("a"), _warn(), _warn("b"), _warn("c"), _info()]
    buckets = split_by_severity(vs)
    assert len(buckets["critical"]) == 2
    assert len(buckets["warning"]) == 3
    assert len(buckets["info"]) == 1


# ── append_warning_summary ──────────────────────────────────────────────
def test_append_warning_summary_merges_existing(tmp_path: Path):
    """기존 days 는 보존하고 새 days 만 머지."""
    path = tmp_path / WARNING_SUMMARY_FILE
    path.write_text(json.dumps({
        "updated_at": "2026-06-01T00:00:00+00:00",
        "days": {
            "2026-06-01": {"warning_count": 5, "metrics": ["old"]},
        },
    }), encoding="utf-8")

    new = {
        "2026-06-02": {"warning_count": 12, "metrics": ["voc_daily_drop_pct"]},
    }
    out_path = append_warning_summary(
        summary_by_day=new,
        report_dir=tmp_path,
    )
    body = json.loads(out_path.read_text(encoding="utf-8"))
    # 기존 보존 + 새 머지
    assert "2026-06-01" in body["days"]
    assert "2026-06-02" in body["days"]
    assert body["days"]["2026-06-01"]["warning_count"] == 5
    assert body["days"]["2026-06-02"]["warning_count"] == 12
    assert body["updated_at"] is not None


# ── append_audit_line ───────────────────────────────────────────────────
def test_append_audit_line_appends_one_line(tmp_path: Path):
    """1 run = 1 line, 누적 (append)."""
    now = datetime.now(timezone.utc)
    p1 = append_audit_line(
        report_dir=tmp_path,
        run_id="abc",
        started_at=now,
        finished_at=now,
        insert_enabled=False,
        days=7,
        distribution_window={"critical": 0, "warning": 0, "info": 0},
        actions={"critical_inserted": 0, "critical_skipped": 0,
                 "warning_logged": 0, "info_ignored": 0},
        per_day={},
        status="ok",
        exc_message=None,
    )
    p2 = append_audit_line(
        report_dir=tmp_path,
        run_id="def",
        started_at=now,
        finished_at=now,
        insert_enabled=True,
        days=14,
        distribution_window={"critical": 1, "warning": 2, "info": 3},
        actions={"critical_inserted": 1, "critical_skipped": 0,
                 "warning_logged": 2, "info_ignored": 3},
        per_day={"2026-06-05": {}},
        status="ok",
        exc_message=None,
    )
    assert p1 == p2 == (tmp_path / AUDIT_FILE)
    lines = p1.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    rec1 = json.loads(lines[0])
    rec2 = json.loads(lines[1])
    assert rec1["run_id"] == "abc"
    assert rec1["mode"] == "dry_run"
    assert rec1["env"]["DAYS_WINDOW"] == 7
    assert rec2["run_id"] == "def"
    assert rec2["mode"] == "apply"
    assert rec2["counters"]["window_severity"] == {
        "critical": 1, "warning": 2, "info": 3,
    }


# ── insert_critical_violations (fake conn) ──────────────────────────────
class _FakeConn:
    """asyncpg.Connection 의 최소 인터페이스 stub."""

    def __init__(
        self,
        *,
        rule_present: bool = True,
        reconstruct: Optional[Dict[date, List[Dict[str, Any]]]] = None,
        fired_keys_in_db: Optional[Set[str]] = None,
    ):
        self.rule_present = rule_present
        self.reconstruct = reconstruct or {}
        self._fired_keys_in_db = fired_keys_in_db or set()
        self.inserts: List[Dict[str, Any]] = []

    async def fetchrow(self, query: str, *args):
        if "alert_rules" in query and self.rule_present:
            return {"id": 80, "threshold": 0.0, "cooldown_sec": 3600,
                    "severity": "warning"}
        return None

    async def fetch(self, query: str, *args):
        # already_fired_keys: payload->>'metric', payload->>'source_date'
        if "payload->>'metric'" in query:
            out = []
            for k in self._fired_keys_in_db:
                sd, m = k.split("|", 1)
                out.append({"source_date": sd, "metric": m})
            return out
        # reconstruct_violations_from_db
        if "alert_events" in query and "alert_rules" in query:
            target = args[0]
            vs = self.reconstruct.get(target, [])
            out = []
            for i, v in enumerate(vs):
                out.append({
                    "id": 1000 + i,
                    "fired_at": datetime.now(timezone.utc),
                    "severity": v.get("severity") or "info",
                    "value": v.get("value"),
                    "threshold": v.get("threshold"),
                    "payload": json.dumps({
                        "metric": v.get("metric"),
                        "violation": {
                            "value": v.get("value"),
                            "threshold": v.get("threshold"),
                            "reason": v.get("reason"),
                        },
                    }),
                    "rule_name": "ops_status_violation",
                })
            return out
        return []

    async def execute(self, query: str, *args):
        self.inserts.append({
            "rule_id": args[0],
            "severity": args[1],
            "value": args[2],
            "threshold": args[3],
            "payload": json.loads(args[4]),
        })

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_insert_critical_skips_dedupe_keys():
    """fired_keys 에 있는 metric 은 INSERT skip, 미매칭은 INSERT."""
    conn = _FakeConn()
    fired = {"2026-06-05|regression_ok_ratio"}  # 이미 발화
    vs = [
        _crit("regression_ok_ratio"),  # skip
        _crit("voc_zero_today"),       # insert
    ]
    inserted, skipped = await insert_critical_violations(
        conn, vs,
        target_date=date(2026, 6, 5),
        fired_keys=fired,
    )
    assert inserted == 1
    assert skipped == 1
    assert len(conn.inserts) == 1
    assert conn.inserts[0]["payload"]["metric"] == "voc_zero_today"
    assert conn.inserts[0]["payload"]["source_date"] == "2026-06-05"
    assert conn.inserts[0]["payload"]["source_processor"] == "ops_backlog_processor"


@pytest.mark.asyncio
async def test_insert_critical_skips_already_fired_marker():
    """재구성된 (_already_fired=True) violation 은 INSERT 대상이 아니라 skip."""
    conn = _FakeConn()
    vs = [{**_crit("voc_x"), "_already_fired": True}]
    inserted, skipped = await insert_critical_violations(
        conn, vs,
        target_date=date(2026, 6, 5),
        fired_keys=set(),
    )
    assert inserted == 0
    assert skipped == 1
    assert conn.inserts == []


@pytest.mark.asyncio
async def test_insert_critical_graceful_when_rule_missing():
    """룰 없으면 모두 skip 카운트."""
    conn = _FakeConn(rule_present=False)
    vs = [_crit("a"), _crit("b")]
    inserted, skipped = await insert_critical_violations(
        conn, vs,
        target_date=date(2026, 6, 5),
        fired_keys=set(),
    )
    assert inserted == 0
    assert skipped == 2
    assert conn.inserts == []


# ── _dedupe_key ─────────────────────────────────────────────────────────
def test_dedupe_key_format():
    assert _dedupe_key(date(2026, 6, 5), "voc_x") == "2026-06-05|voc_x"


# ── process_backlog 통합 (fake conn 주입) ────────────────────────────────
@pytest.mark.asyncio
async def test_process_backlog_classifies_and_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """주 시나리오: 윈도우 3일.

    * d-2 : 헤더만 (violations_count=3, []) → 재구성 (1 crit + 1 warn + 1 info)
    * d-1 : 정상 채움 (1 crit + 2 warn)
    * d-0 : 빈 파일 (status=ok, violations_count=0)

    fired_keys_in_db 에 d-2 의 critical metric 이 이미 있어 dedupe skip 됨.
    """
    d0 = date(2026, 6, 5)
    d1 = date(2026, 6, 4)
    d2 = date(2026, 6, 3)

    # ops_status JSON 작성
    _write_ops_status(tmp_path, d2, status="critical",
                      violations_count=3, violations=[],  # 헤더만
                      extra={"_source": "backfill_from_db"})
    _write_ops_status(tmp_path, d1, status="critical",
                      violations_count=3, violations=[
                          _crit("d1_crit"),
                          _warn("d1_warn_a"),
                          _warn("d1_warn_b"),
                      ])
    _write_ops_status(tmp_path, d0, status="ok",
                      violations_count=0, violations=[])

    # 재구성 결과 — d2 의 critical 은 이미 발화된 metric 으로 표기
    reconstruct = {
        d2: [
            _crit("d2_crit_already"),
            _warn("d2_warn"),
            _info("d2_info"),
        ],
    }
    # 이미 발화 (dedupe 대상)
    fired_in_db = {"2026-06-03|d2_crit_already"}

    fake_conn = _FakeConn(
        reconstruct=reconstruct,
        fired_keys_in_db=fired_in_db,
    )

    # asyncpg.connect 를 fake 로 교체
    async def _fake_connect(_dsn: str):
        return fake_conn

    import insight.ops_backlog_processor as mod
    monkeypatch.setattr(mod.asyncpg, "connect", _fake_connect)

    result = await process_backlog(
        days=3,
        end_date=d0,
        insert=True,
        report_dir=tmp_path,
    )

    # 1) 윈도우 분포
    assert result["files_scanned"] == 3
    # d2 재구성: 1c + 1w + 1i ; d1: 1c + 2w + 0i ; d0: 0
    assert result["window_severity"] == {
        "critical": 2, "warning": 3, "info": 1,
    }

    actions = result["actions"]
    # d2 critical 은 _already_fired=True 마커 → skip (재구성 경로)
    # d1 critical 은 dedupe 미매칭 + _already_fired 없음 → INSERT
    assert actions["critical_inserted"] == 1
    assert actions["critical_skipped"] == 1
    assert actions["warning_logged"] == 3  # d2 1 + d1 2
    assert actions["info_ignored"] == 1

    # 2) fake conn 에 INSERT 1건 발생 + payload 검증
    assert len(fake_conn.inserts) == 1
    assert fake_conn.inserts[0]["payload"]["metric"] == "d1_crit"
    assert fake_conn.inserts[0]["payload"]["source_date"] == d1.isoformat()

    # 3) warning summary 머지 — 2일 기록
    ws = json.loads((tmp_path / WARNING_SUMMARY_FILE).read_text(encoding="utf-8"))
    assert d2.isoformat() in ws["days"]
    assert d1.isoformat() in ws["days"]
    assert ws["days"][d1.isoformat()]["warning_count"] == 2
    assert sorted(ws["days"][d1.isoformat()]["metrics"]) == [
        "d1_warn_a", "d1_warn_b",
    ]

    # 4) audit JSONL 1 라인 추가
    audit_lines = (tmp_path / AUDIT_FILE).read_text(encoding="utf-8").strip().splitlines()
    assert len(audit_lines) == 1
    rec = json.loads(audit_lines[0])
    assert rec["script"] == "ops_backlog_processor"
    assert rec["mode"] == "apply"
    assert rec["counters"]["files_scanned"] == 3
    assert rec["counters"]["window_severity"] == {
        "critical": 2, "warning": 3, "info": 1,
    }


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
